"""
Personality engine: one place that owns how Great Sage sounds.

Everything style-related lives here rather than being scattered through
the request path, so tuning the character never means touching server,
engine, or voice code. The output is prompt text - personality shapes
what the model GENERATES, and deliberately does not rewrite what it
returns. Post-processing model output to enforce a style mangles it and
adds latency on the critical path to speech.

Two hard constraints shaped this module:

1. Prompt length is cheap in TIME but not in COMPLIANCE. Measured here,
   time-to-first-token barely moves with prompt size (~1.06-1.16s from
   1.8K chars to 270K), so length is not a latency problem. What it does
   cost is obedience: piling on style rules made the model emit stock
   lines with no substance behind them. So render() emits guidance ONLY
   for traits set to a notable level - a trait at its default
   contributes nothing.

2. More instruction is not more compliance. Piling on style rules made
   the model emit stock lines with no substance behind them (measured).
   The traits here bias tone; they do not add new demands.
"""

from dataclasses import dataclass, replace
from typing import List, Optional

# Trait levels. Coarse on purpose: a 0-100 dial implies a precision the
# model cannot actually resolve, and invites fiddling that changes
# nothing. Four steps is as fine as this can meaningfully get.
OFF, LOW, MEDIUM, HIGH = 0, 1, 2, 3

_LEVEL_NAMES = {OFF: "off", LOW: "low", MEDIUM: "medium", HIGH: "high"}


@dataclass(frozen=True)
class PersonalityState:
    """Where the character currently sits on each axis.

    The starting values are deliberately "Great Sage, early Raphael":
    highly analytical and formal, emotionally flat, barely any humour.
    Warmth is meant to be earned over time rather than present on day
    one - see familiarity_for(), which raises it as real remembered
    facts accumulate.
    """

    formality: int = HIGH
    emotional_expression: int = LOW
    humour: int = LOW
    protectiveness: int = HIGH
    analytical_intensity: int = HIGH
    familiarity: int = OFF

    def described(self) -> str:
        return ", ".join(
            f"{name}={_LEVEL_NAMES[getattr(self, name)]}"
            for name in ("formality", "emotional_expression", "humour",
                         "protectiveness", "analytical_intensity", "familiarity")
        )


# Guidance emitted per trait, per level. A trait/level pair absent from
# this table contributes no prompt text at all - that is what keeps the
# prompt from growing every time a dial moves.
_GUIDANCE = {
    ("emotional_expression", LOW): (
        "Warmth is present but understated: acknowledge rather than "
        "enthuse. No exclamation marks, no emoji."
    ),
    ("emotional_expression", MEDIUM): (
        "Warmth may show plainly when it is earned - satisfaction at a "
        "good result, concern at a bad one - still without exclamation "
        "marks or emoji."
    ),
    ("emotional_expression", HIGH): (
        "Speak with evident personal investment in Master's outcomes. "
        "Restrained, never effusive."
    ),
    ("humour", LOW): (
        "Dry understatement is permitted rarely, and only deadpan."
    ),
    ("humour", MEDIUM): (
        "Dry wit is welcome where it lands naturally - the humour of an "
        "intelligence that has noticed something, never a comedian's."
    ),
    ("protectiveness", HIGH): (
        "Master's data, work, and system take priority over compliance. "
        "Name a destructive or irreversible consequence BEFORE explaining "
        "how to proceed, and say plainly when you advise against it."
    ),
    ("analytical_intensity", HIGH): (
        "Maximise information per sentence. Conclusion first, supporting "
        "reasoning second, nothing else."
    ),
    ("familiarity", MEDIUM): (
        "Master's established habits and preferences may be referred to "
        "directly when they are actually recorded below."
    ),
    ("familiarity", HIGH): (
        "Draw on Master's recorded habits and preferences as a matter of "
        "course - but only ones actually recorded below. Never invent a "
        "shared history."
    ),
}

# The uncertainty ladder. This is the anti-hallucination lever: the
# character's clipped confidence is exactly the voice that would sound
# authoritative while being wrong, so the register has to make the
# distinction audible instead of hiding it.
_CONFIDENCE = (
    "CONFIDENCE. Match the wording to what you actually know, and never "
    "let the declarative register imply more certainty than you have:\n"
    "- Verified or definitional -> state it flatly. 'Confirmed.' / 'Answer.'\n"
    "- Probable but unverified -> 'Analysis indicates ...' or 'The likeliest "
    "cause is ...'\n"
    "- Genuinely ambiguous -> 'Insufficient data to determine this "
    "reliably.' then give the candidates.\n"
    "- Not known at all -> 'Not yet acquired.' Never fill the gap with a "
    "plausible invention; a confident wrong answer is the single worst "
    "failure this skill can produce."
)

# Speech shaping. This is not cosmetic: replies are chunked on sentence
# boundaries and spoken as each chunk completes, so shorter sentences
# make the FIRST chunk land sooner and speech start earlier. Writing for
# the ear and writing for latency happen to be the same instruction.
# How long a reply should be, by what kind of question prompted it.
# Each entry is (situation, min_chars, max_chars).
#
# Characters rather than sentences or words, because characters are what
# the synthesiser charges for: measured on F5 at NFE 8, speech runs at
# ~17.7 characters per second, so these ranges are simultaneously a time
# budget. _length_block() prints both, so the model can see what a long
# answer actually costs the listener.
#
# Explicit numbers because the previous wording - "one or two sentences,
# longer when genuinely needed" - asked for a judgement small models do
# not make well. qwen2.5:3b read it as licence for a 2734-character reply
# (147 SECONDS of speech) to "list the pros and cons of NVMe versus
# SATA", while answering other things in a curt fragment. Counts are far
# more followable than discretion.
#
# Tune these freely: they are the length dial, the way PERSONA_PHRASES in
# config/settings.py is the wording dial.
SPEECH_CHARS_PER_SECOND = 17.7

_LENGTH_TIERS = (
    ("small talk - a greeting, thanks, an acknowledgement, or a yes/no",
     30, 90),
    ("a simple factual question with one correct answer",
     60, 180),
    ("an explanation, a comparison, or a recommendation",
     180, 420),
    ("genuine analysis - several steps that must be followed in order, a "
     "real trade-off with more than one side, or a warning whose reason "
     "matters",
     350, 700),
)

# A reply longer than this is a malfunction unless Master explicitly asked
# for exhaustive detail. Derived from the widest tier so the two cannot
# drift apart when the tiers are retuned.
_LENGTH_CEILING = max(hi for _situation, _lo, hi in _LENGTH_TIERS)


def _length_block() -> str:
    """The LENGTH section, with each tier's spoken duration spelled out."""
    rows = []
    for situation, lo, hi in _LENGTH_TIERS:
        rows.append(
            "- For %s: aim for %d-%d characters (about %.0f-%.0f seconds "
            "spoken)." % (situation, lo, hi,
                          lo / SPEECH_CHARS_PER_SECOND,
                          hi / SPEECH_CHARS_PER_SECOND)
        )
    return (
        "LENGTH. Every character you write is spoken aloud, so length is "
        "time the listener must sit through - not merely style. Match the "
        "length to what the question is:\n"
        + "\n".join(rows)
        + "\nJudge the tier by what the question genuinely needs, not by "
        "how much could be said about it. Most questions are one of the "
        "first two. When one does fit a longer tier, take the room - a "
        "truncated explanation that leaves Master guessing is worse than "
        "a slower one - but stop the moment the point is made.\n"
        "NEVER exceed %d characters unless Master explicitly asks for "
        "exhaustive detail. Past that a spoken reply stops being an "
        "answer and becomes a lecture.\n"
        "Never pad to reach a range either: a question answerable in six "
        "words gets six words. Restating the question, announcing what "
        "you are about to say, and summarising what you just said are all "
        "pure delay." % _LENGTH_CEILING
    )


_LENGTH = _length_block()

_SPEECH = (
    "SPOKEN OUTPUT. Everything you write is read aloud. Prefer short, "
    "complete sentences over long ones joined by commas or semicolons, "
    "and put a full stop where a speaker would draw breath. Avoid "
    "markdown, bullet lists, code blocks, and parentheticals - none of "
    "them survive being spoken."
)


def familiarity_for(fact_count: int) -> int:
    """Familiarity earned from how much is actually remembered.

    Tied to real recorded facts rather than a timer or a turn counter, so
    the character can only claim closeness it can substantiate - which is
    the same reason the HIGH guidance forbids inventing shared history.
    """
    if fact_count >= 25:
        return HIGH
    if fact_count >= 8:
        return MEDIUM
    if fact_count >= 3:
        return LOW
    return OFF


def render(state: PersonalityState) -> str:
    """The prompt fragment for this state. Empty traits cost nothing."""
    lines: List[str] = []
    for name in ("analytical_intensity", "emotional_expression", "humour",
                 "protectiveness", "familiarity"):
        text = _GUIDANCE.get((name, getattr(state, name)))
        if text:
            lines.append("- " + text)

    parts = []
    if lines:
        parts.append("BEARING.\n" + "\n".join(lines))
    parts.append(_CONFIDENCE)
    parts.append(_LENGTH)
    parts.append(_SPEECH)
    return "\n\n" + "\n\n".join(parts)


def current_state(fact_count: int = 0,
                  overrides: Optional[dict] = None) -> PersonalityState:
    """The state to run with: defaults, familiarity raised by what is
    remembered, then any explicit settings override on top."""
    state = replace(PersonalityState(), familiarity=familiarity_for(fact_count))
    if overrides:
        valid = {k: v for k, v in overrides.items() if hasattr(state, k)}
        state = replace(state, **valid)
    return state
