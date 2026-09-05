"""
Memory recall: choosing WHICH remembered facts a given turn actually
needs, plus the explicit remember/forget commands.

Kept separate from memory.py, which owns storage and extraction. That
module answers "what do we know"; this one answers "what is worth saying
right now" - different concerns, and the split keeps the retrieval logic
testable without touching the file format.

Why this exists at all: facts used to be loaded once at startup and baked
into the system prompt wholesale, every one of them, on every request.

That was NOT costing latency, contrary to the reasoning this module was
originally written under. Measured on this machine, time-to-first-token
held at ~1.06-1.16s from a 1.8K prompt all the way up to 270K chars - the
~1.1s is a fixed floor from Ollama plus the model, and prompt size barely
registers against it.

What it does buy is quality and freshness. Two hundred unrelated facts
dilute the handful that actually bear on the question, leaving the model
to find the signal in the noise. And because recall now runs per turn
rather than at startup, a fact learned mid-session applies on the very
next message instead of only after a restart.
"""

from typing import List, Optional

from great_sage.models.base import ModelProvider, ModelProviderError

# Common words that carry no retrieval signal. "user" is in here
# deliberately: every stored fact begins with "The user", so keeping it
# would score every fact identically against every query and flatten the
# ranking completely.
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "and", "or", "but", "if", "then", "than", "that", "this", "these",
    "those", "of", "to", "in", "on", "at", "for", "with", "about", "from",
    "by", "as", "it", "its", "user", "users", "do", "does", "did", "has",
    "have", "had", "can", "could", "would", "should", "will", "i", "you",
    "me", "my", "your", "he", "she", "they", "them", "his", "her", "their",
    "what", "when", "where", "who", "why", "how", "not", "no", "yes",
    "get", "got", "just", "like", "want", "need", "know", "there", "here",
}


def _stem(word: str) -> str:
    """Crude suffix stripper, enough to make word VARIANTS match.

    Without this, "bounce" failed to retrieve a fact stored as "bounces"
    and "use" missed "uses" - near-miss forms are extremely common in
    natural questions, and every one of them was a silent retrieval miss.
    Order matters: longer suffixes are tested first, or "ing" would never
    be reached because "s" already matched.

    Deliberately not a real stemmer (Porter and friends). Those bring a
    dependency and a lot of rules to solve a problem that, on a store of
    short plain sentences, is almost entirely plurals and simple verb
    endings.
    """
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    for suffix in ("ings", "ing", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    # Plain trailing "s" only - NOT "es". Stripping "es" turned "bounces"
    # into "bounc" while "bounce" stayed whole, so the two never met and
    # the variant match silently failed. Taking just the "s" maps both to
    # "bounce". Consistency between query and fact matters far more here
    # than linguistic correctness, since both go through this same
    # function.
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def tokens(text: str) -> set:
    """Lowercased, stemmed content words with stopwords removed."""
    out = set()
    for raw in text.lower().split():
        word = "".join(ch for ch in raw if ch.isalnum())
        if len(word) > 2 and word not in _STOPWORDS:
            out.add(_stem(word))
    return out


def relevant_facts(facts: List[str], query: str, limit: int = 6) -> List[str]:
    """The `limit` facts most related to `query`, most relevant first.

    Scored by word overlap rather than embeddings on purpose. Embeddings
    would mean loading a model, maintaining a vector store, and doing a
    lookup on the critical path - to search what is at most a couple of
    hundred short lines. Overlap scoring costs microseconds and adds no
    dependencies. If this store ever grows past a few thousand facts,
    this is the function to revisit.

    Rarer words count for more: a fact sharing "reaper" with the query is
    far stronger evidence than one sharing "project", which might appear
    in half the store. Weighting by inverse document frequency is a cheap
    stand-in for that, and it is what stops a handful of very general
    facts being retrieved for every question asked.

    Facts with no overlap are dropped rather than padded in. An
    irrelevant fact is not free - it is prompt weight that dilutes the
    ones that matter.
    """
    if not facts:
        return []
    query_tokens = tokens(query)
    if not query_tokens:
        return []

    doc_freq = {}
    fact_tokens = []
    for fact in facts:
        toks = tokens(fact)
        fact_tokens.append(toks)
        for tok in toks:
            doc_freq[tok] = doc_freq.get(tok, 0) + 1

    total = len(facts)
    scored = []
    for idx, (fact, toks) in enumerate(zip(facts, fact_tokens)):
        shared = query_tokens & toks
        if not shared:
            continue
        score = sum(total / (1.0 + doc_freq.get(tok, 0)) for tok in shared)
        # Gentle recency tiebreak - when two facts match equally well the
        # newer statement is likelier to still be true.
        score += idx * 0.001
        scored.append((score, idx, fact))

    scored.sort(key=lambda row: (-row[0], -row[1]))
    return [fact for _, _, fact in scored[:limit]]


def format_recall(facts: List[str]) -> str:
    """The block injected into a single request, or "" if nothing matched."""
    if not facts:
        return ""
    lines = "\n".join("- " + fact for fact in facts)
    return (
        "Relevant to this message, from what you remember about Master:\n"
        + lines
        + "\nUse these only where they actually apply, and never invent "
        "anything beyond them."
    )


# --- explicit commands ------------------------------------------------
# Matched at the START of the message, so an incidental "...and remember
# to call mum" mid-sentence does not trigger a store.

_REMEMBER_PREFIXES = (
    "remember this that", "remember this,", "remember this:", "remember this",
    "remember that", "remember,", "remember:", "remember ",
    "make a note that", "make a note", "note that",
    "keep in mind that", "keep in mind",
    "don't forget that", "dont forget that", "don't forget", "dont forget",
)

_FORGET_PREFIXES = (
    "forget that", "forget about", "forget ",
)


def _strip_prefix(text: str, prefixes) -> Optional[str]:
    lowered = text.strip().lower()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return text.strip()[len(prefix):].strip(" ,:-") or None
    return None


def remember_request(text: str) -> Optional[str]:
    """Content of an explicit remember-this instruction, or None.

    The trigger phrase is stripped, so "remember that I use Reaper"
    yields "I use Reaper" - ready to be condensed into a fact.
    """
    return _strip_prefix(text, _REMEMBER_PREFIXES)


def forget_request(text: str) -> Optional[str]:
    """Subject of an explicit forget instruction, or None."""
    return _strip_prefix(text, _FORGET_PREFIXES)


CONDENSE_PROMPT = (
    "Rewrite the user's statement as ONE short third-person fact "
    "starting with 'The user', capturing only the durable point worth "
    "remembering. Strip filler, politeness, and anything transient. "
    "Output that single sentence and nothing else.\n\n"
    "Example input: remember that I always use Reaper when I'm doing sound design\n"
    "Example output: The user uses Reaper for sound design."
)


def condense(provider: ModelProvider, text: str) -> Optional[str]:
    """One compact fact line from a raw statement, or None on failure.

    A one-off, history-free call, so it can never pollute the running
    conversation - the same pattern extract_facts uses.
    """
    try:
        reply = provider.send_message([
            {"role": "system", "content": CONDENSE_PROMPT},
            {"role": "user", "content": text},
        ])
    except ModelProviderError:
        return None
    stripped = reply.strip()
    if not stripped:
        return None
    return stripped.splitlines()[0].strip(" \t-*\"'") or None


# --- superseding ------------------------------------------------------

# How alike two facts must be before the newer is taken to REPLACE the
# older rather than sit alongside it. Tuned so "The user uses Ableton for
# music" / "The user uses Reaper for music" (2 words shared of 3) counts,
# while merely related facts do not.
_SUPERSEDE_RATIO = 0.6
_SUPERSEDE_MIN_SHARED = 2


def supersedes(new_fact: str, old_fact: str) -> bool:
    """Whether new_fact is an updated version of old_fact.

    Compares how much of the SMALLER fact the two share. An earlier
    attempt used a "signature" of each fact's first few sorted words,
    which never worked: the distinguishing word ("Ableton" vs "Reaper")
    sorted into the signature itself, so two facts that differed by
    exactly the thing that mattered produced different signatures and
    never collided.

    Overlap sidesteps that - the words they have in common are the
    subject, and the ones they do not are the changed value.
    """
    a, b = tokens(new_fact), tokens(old_fact)
    if not a or not b:
        return False
    shared = a & b
    if len(shared) < _SUPERSEDE_MIN_SHARED:
        return False
    return len(shared) / min(len(a), len(b)) >= _SUPERSEDE_RATIO


def merge_facts(existing: List[str], new_facts: List[str]) -> List[str]:
    """Existing facts plus new ones, with near-duplicates superseded.

    An exact repeat is dropped. A fact sharing a signature with an older
    one REPLACES it in place, so the store reflects the current state
    instead of accumulating contradictions.
    """
    merged = list(existing)
    lowered = {f.lower() for f in merged}

    for fact in new_facts:
        key = fact.lower()
        if key in lowered:
            continue
        replaced = False
        for i, old in enumerate(merged):
            if supersedes(fact, old):
                merged[i] = fact       # the newer statement wins
                lowered.add(key)
                replaced = True
                break
        if not replaced:
            merged.append(fact)
            lowered.add(key)
    return merged


# Forgetting DESTROYS data, so it demands more evidence than retrieval
# does. A single shared word is far too loose: "forget that I have a cat
# named Biscuit" shares only the stem "nam" with "The user prefers the
# UCS naming convention", and that was enough to delete it too.
_FORGET_MIN_SHARED = 2


def drop_matching(facts: List[str], subject: str) -> List[str]:
    """What survives a "forget about X" instruction.

    A fact is removed only when it shares at least _FORGET_MIN_SHARED
    content words with the subject - or, when the subject is a single
    distinctive word, when that exact word appears. Anything less risks
    taking unrelated facts down with the intended one, and the user has
    no undo.
    """
    wanted = tokens(subject)
    if not wanted:
        return list(facts)
    threshold = _FORGET_MIN_SHARED if len(wanted) >= _FORGET_MIN_SHARED else 1
    return [f for f in facts if len(wanted & tokens(f)) < threshold]
