"""
Shared voice-line trigger matching, used by any VoiceOutput implementation
that supports pre-recorded audio clips standing in for fixed phrases
(see VOICE_LINES in config/settings.py).
"""

from typing import List, Pattern, Tuple

VoiceLine = Tuple[Pattern, str]


def label_from_pattern(pattern_str: str) -> str:
    """A human-readable label for a voice-line's regex, for display in the
    HUD's settings panel (e.g. r"^\\s*Notice\\." -> "Notice."). Only needs
    to handle the simple literal-text-plus-escaped-punctuation patterns
    actually used in config/settings.py's VOICE_LINES, not arbitrary regex."""
    label = pattern_str.lstrip("^").replace(r"\s*", " ").strip()
    for escaped, plain in ((r"\.", "."), (r"\,", ","), (r"\!", "!"), (r"\?", "?")):
        label = label.replace(escaped, plain)
    return label.strip()


def split_voice_lines(
    text: str, voice_lines: List[VoiceLine]
) -> List[Tuple[str, str, str]]:
    """Split text into ("tts", text, spoken) / ("audio", path, spoken) segments.

    The third element is always the words a listener will actually HEAR.
    For a TTS segment that is the segment itself; for a pre-recorded clip
    it is the phrase that matched the trigger. Callers need it because the
    payload of an audio segment is a file path, which must never reach the
    caption, while the phrase it stands in for must - otherwise the
    on-screen text silently omits words that were spoken aloud.

    Scans for the earliest-matching configured voice-line trigger (e.g.
    an opening "Notice." or a "Good morning, Master" greeting)
    repeatedly, so a pre-recorded clip plays for that phrase instead of
    it being synthesized, while the rest of the text still goes through
    the TTS pipeline as normal.
    """
    segments: List[Tuple[str, str, str]] = []
    remaining = text
    while remaining and voice_lines:
        best_match = None
        best_path = None
        for pattern, path in voice_lines:
            match = pattern.search(remaining)
            if match and (best_match is None or match.start() < best_match.start()):
                best_match = match
                best_path = path
        if best_match is None:
            break
        before = remaining[: best_match.start()].strip()
        if before:
            segments.append(("tts", before, before))
        segments.append(("audio", best_path, best_match.group(0).strip()))
        remaining = remaining[best_match.end():].strip()
    if remaining:
        segments.append(("tts", remaining, remaining))
    return segments
