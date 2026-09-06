"""
Long-term memory: durable facts about Master, extracted from finished
conversations and persisted to a small text file - unlike ChatEngine's
in-memory `history`, which is gone the moment the process exits, this
carries over between runs.

Extraction reuses the same ModelProvider the chat itself talks to, via a
one-off, history-free call (same pattern as main.py's build_translator),
so it works with whatever provider is configured and never pollutes the
real conversation history. It's wired to ChatEngine's existing idle-reset
boundary (see chat_engine.py's on_session_boundary) rather than running
on every message, so it doesn't add latency to every reply - just to the
first message after a real gap, and it runs in a background thread even
then (see main.py's build_memory_callback).
"""

import os
from typing import List

from great_sage.models.base import Message, ModelProvider, ModelProviderError

EXTRACTION_PROMPT = (
    "Below is a conversation between a user and an AI companion. Pull out "
    "any concrete facts the user stated about themselves worth "
    "remembering in future conversations - their name, likes/dislikes, "
    "hobbies, ongoing projects, people or places they mentioned, "
    "preferences, or recurring context. Extract facts even from a short "
    "or casual conversation - a single stated name or hobby still counts "
    "and should not be skipped. If the user stated more than one fact, "
    "list every one of them separately - do not stop after the first or "
    "merge them into one line.\n\n"
    "Output ONLY the facts, one short factual sentence per line, in plain "
    "English, third person, always starting with \"The user\" (e.g. "
    "\"The user's name is Sarah.\"). No quotes, no bullet points, no "
    "extra commentary. Output exactly NONE only if the conversation truly "
    "contains no personal information at all (pure small talk with zero "
    "details about the user).\n\n"
    "Example input:\n"
    "User: hey, i'm Sarah, I've been learning guitar for 3 months\n"
    "AI: Wonderful, Sarah.\n"
    "Example output:\n"
    "The user's name is Sarah.\n"
    "The user has been learning guitar for about 3 months."
)


# Extraction is told to output NONE when a conversation contains nothing
# worth keeping. Small models often narrate that instead - "The user did
# not provide any concrete facts about themselves..." - which then gets
# stored as though it were a fact about the user, kept forever, and
# competes for retrieval slots against real ones. These are the shapes
# that turn up in practice; a line containing any of them is discarded.
_NON_FACT_MARKERS = (
    "did not provide",
    "didn't provide",
    "no concrete facts",
    "nothing worth remembering",
    "worth remembering in future",
    "no personal information",
    "did not share",
    "didn't share",
    "did not mention anything",
    "tried again to initiate",
    "initiate a conversation",
    "the conversation contains",
    "this conversation",
)


def is_real_fact(line: str) -> bool:
    """Whether an extracted line is an actual fact about the user.

    Filters the model's meta-commentary about the extraction itself. A
    stored non-fact is worse than a missed one: it is permanent, it is
    retrieved as though true, and it displaces a real fact under the
    recall limit.
    """
    lowered = line.lower()
    return not any(marker in lowered for marker in _NON_FACT_MARKERS)


def load_memory(path: str) -> List[str]:
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def format_memory_for_prompt(facts: List[str]) -> str:
    if not facts:
        return ""
    bullets = "\n".join(f"- {fact}" for fact in facts)
    return (
        "\n\nWhat this Great Sage remembers about Master from past "
        f"sessions:\n{bullets}"
    )


def _transcript_text(messages: List[Message]) -> str:
    # Labels match EXTRACTION_PROMPT's own example (User:/AI:) so the
    # few-shot pattern lines up exactly with what the model is shown here.
    lines = []
    for msg in messages:
        speaker = "User" if msg["role"] == "user" else "AI"
        lines.append(f"{speaker}: {msg['content']}")
    return "\n".join(lines)


def extract_facts(provider: ModelProvider, messages: List[Message]) -> List[str]:
    """Pulls memorable facts out of a finished conversation slice.

    Never raises - extraction is a nice-to-have, not something that
    should break anything if the provider call fails.
    """
    if not messages:
        return []
    request = [
        {"role": "system", "content": EXTRACTION_PROMPT},
        {"role": "user", "content": _transcript_text(messages)},
    ]
    try:
        reply = provider.send_message(request)
    except ModelProviderError:
        return []

    facts = []
    for line in reply.splitlines():
        line = line.strip(" \t-*\"'“”")
        if not line or line.upper() == "NONE":
            continue
        if not is_real_fact(line):
            continue
        facts.append(line)
    return facts


def save_facts(path: str, new_facts: List[str], max_facts: int) -> List[str]:
    """Merges new_facts into the file (case-insensitive exact-match dedup
    against what's already there), capped to the most recent max_facts.
    Returns the resulting full list."""
    if not new_facts:
        return load_memory(path)

    existing = load_memory(path)
    seen_lower = {fact.lower() for fact in existing}
    merged = list(existing)
    for fact in new_facts:
        if fact.lower() not in seen_lower:
            merged.append(fact)
            seen_lower.add(fact.lower())
    merged = merged[-max_facts:]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(merged) + ("\n" if merged else ""))
    return merged

def write_facts(path: str, facts: List[str]) -> int:
    """Replace the whole file. Returns how many facts were written.

    Needed by the memory manager (spec S46): deleting or editing a fact
    is a rewrite, not an append, and save_facts only ever adds.

    Written through a temp file and os.replace so an interrupted write
    cannot leave a half-file - this is the user's long-term memory, and
    a truncated one would silently lose facts with nothing to notice it.
    """
    import os
    import tempfile
    clean = [f.strip() for f in facts if f and f.strip()]
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for fact in clean:
                fh.write(fact + chr(10))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise
    return len(clean)
