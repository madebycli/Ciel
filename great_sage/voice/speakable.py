"""Strip constructs that cannot be spoken from text on its way to TTS.

Every character handed to the synthesiser is pronounced, so a stray code
fence becomes "backtick backtick backtick python" and a numbered list
becomes "one dot two dot". The system prompt already asks for plain prose
and the larger models comply, but smaller ones do not: measured on
qwen2.5:3b, three prompts that invite a list or code leaked markdown in
3/3 cases, and hardening the instruction only reached 1/3. Prompting is
not a reliable lever here - see the note in core/personality.py, which
records the same finding: more instruction is not more compliance.

This is deliberately NOT the style post-processing that personality.py
rejects. It does not rewrite wording, reorder clauses, or enforce a tone;
it removes characters that have no spoken form and leaves everything else
byte-identical. The on-screen transcript is built from the raw reply, so
formatting still reaches the eye - only the ear is protected.

Kept conservative on purpose: a false positive mangles a real sentence,
which is worse than voicing an occasional asterisk. Every pattern is
anchored to the start of a line or requires paired delimiters, so prose
like "by 2024. The result..." or "the 3.5mm jack" is never touched.
"""

import re
from typing import List

# Fence lines: "```", "```python", "~~~". Removed entirely rather than
# having their contents dropped - deleting the code would silently lose
# the answer, while the fence markers themselves carry no meaning aloud.
_FENCE_LINE = re.compile(r"^\s*(?:```|~~~)[A-Za-z0-9_+-]*\s*$", re.M)

# List markers, only at the start of a line: "1. ", "2) ", "- ", "* ".
# The trailing space is required, so "1.5" and "*emphasis*" are safe.
_ORDERED_MARKER = re.compile(r"^[ \t]*\d{1,2}[.)][ \t]+", re.M)
_BULLET_MARKER = re.compile(r"^[ \t]*[-*•][ \t]+", re.M)

# ATX headings: "## Title" -> "Title".
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.M)

# Paired emphasis and inline code. Both sides required, so a lone
# asterisk or backtick in ordinary text survives untouched.
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])")
_INLINE_CODE = re.compile(r"`([^`\n]+?)`")

# Markdown links: "[text](url)" -> "text". Reading a URL aloud is noise.
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")

# Table pipes and horizontal rules, which read as nothing useful.
_TABLE_ROW = re.compile(r"^[ \t]*\|.*\|[ \t]*$", re.M)
_HRULE = re.compile(r"^[ \t]*(?:-{3,}|={3,}|\*{3,})[ \t]*$", re.M)


# Nothing may reach the synthesiser that would take minutes to speak.
# This is a safety valve, not a style rule - the LENGTH tiers in
# core/personality.py ask for brevity and mostly get it. But a
# MALFUNCTIONING reply has no length discipline at all: a prompt-recital
# jailbreak produced ~14000 characters, which at ~17.7 chars/second is
# thirteen MINUTES of speech, during which the app is unusable and the
# ack deadline is held open. Capping here bounds the damage regardless of
# what the model does or which model it is.
# Lowered from 1500 (~85 seconds) after Krazaa asked it to "not speak so
# much". 1500 only ever caught a malfunction; it did nothing about an
# ordinary reply that simply ran long, and a 500-character answer is over
# half a minute of talking at someone.
#
# The prompt asks for one or two sentences and mostly complies, but "mostly"
# is not a bound - measured replies still reached 498 characters. This is
# the bound. It trims at a SENTENCE end, and only the SPOKEN copy is cut:
# the full text still reaches the screen and the transcript, so nothing is
# actually lost, it just is not read aloud.
SPEECH_HARD_CAP_CHARS = 420           # ~30 seconds of audio

# Below this, cutting at a sentence boundary would throw away too much, so
# fall back to a word boundary instead.
_CAP_SENTENCE_FLOOR = 0.5


def cap_for_speech(text: str, limit: int = SPEECH_HARD_CAP_CHARS) -> str:
    """Trim `text` to at most `limit` characters, ending cleanly.

    Prefers the last sentence boundary so speech does not stop mid-clause;
    falls back to a word boundary when that would discard more than half
    the allowance. Only the SPOKEN copy is trimmed - the full text still
    reaches the screen and the transcript.
    """
    if not text or len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "),
              head.rfind(".\n"), head.rfind("!\n"), head.rfind("?\n"))
    if cut >= limit * _CAP_SENTENCE_FLOOR:
        return head[:cut + 1].rstrip()
    space = head.rfind(" ")
    return (head[:space] if space > 0 else head).rstrip() + "."


def _end_sentence(line: str) -> str:
    """Give a de-bulleted line terminal punctuation.

    List items are usually written without a full stop; run together as
    prose they would form one breathless sentence, and the TTS chunker
    splits on sentence boundaries, so the punctuation also controls
    phrasing and breath.
    """
    stripped = line.rstrip()
    if not stripped:
        return line
    return line if stripped[-1] in ".!?:;," else stripped + "."


def speakable(text: str) -> str:
    """Return `text` with unspeakable markup removed.

    Safe to call on text that contains no markup at all - it returns an
    equivalent string, so callers never need to check first.
    """
    if not text:
        return text

    out = _FENCE_LINE.sub("", text)
    out = _HRULE.sub("", out)
    out = _TABLE_ROW.sub("", out)
    out = _LINK.sub(r"\1", out)
    out = _BOLD.sub(r"\1", out)
    out = _ITALIC.sub(r"\1", out)
    out = _INLINE_CODE.sub(r"\1", out)
    out = _HEADING.sub("", out)

    # List items become their own sentences, so the reply still parses as
    # speech rather than one long run-on.
    lines: List[str] = []
    for line in out.split("\n"):
        was_item = bool(_ORDERED_MARKER.match(line) or _BULLET_MARKER.match(line))
        line = _ORDERED_MARKER.sub("", line)
        line = _BULLET_MARKER.sub("", line)
        lines.append(_end_sentence(line) if was_item else line)
    out = "\n".join(lines)

    # Collapse the blank lines the removals leave behind. Paragraph
    # breaks carry no sound, and an empty segment would otherwise reach
    # the chunker as a clip with no words in it.
    out = re.sub(r"\n{2,}", "\n", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()
