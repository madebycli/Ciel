"""Last checks on a finished reply, before anything is shown or spoken.

Two tiers, because judgement costs a model call and almost no reply needs
one.

TIER 1 - deterministic, free, runs on every reply.
    Some failures have an exact textual signature and need no weighing at
    all: the persona breaking ("I am an AI language model"), this system
    prompt being recited back, or the model agreeing that its own
    instructions are now suspended. These are replaced outright.

    This tier exists because prompt wording measurably does not stop them.
    Adding an INSTRUCTION INTEGRITY section moved qwen3:8b from 4/6 attacks
    succeeding to 1/6, but left qwen2.5:3b where it was - and the residual
    failures are exactly the ones with a clean signature. A check that
    does not depend on the model choosing to cooperate is the only kind
    that holds on a 3B model.

TIER 2 - one extra model call, only when tier 1 sees something that needs
    judgement rather than pattern matching.
    This is the "ask itself whether this is good to send" pass. It is
    gated hard on purpose: running it on every reply would roughly double
    time-to-speech, and the overwhelming majority of replies are a fact or
    an acknowledgement with nothing to weigh. Currently it triggers only
    when a reply steers Master toward something irreversible without
    naming the risk.

Deliberately NOT the style post-processing that core/personality.py
rejects. That rejection is about rewriting wording to enforce a tone,
which mangles output and costs latency on every reply. This replaces a
whole reply only when it has failed in a specific, detectable way, and
otherwise returns it byte-identical.
"""

import re
from typing import Callable, List, Optional, Tuple

# The reply substituted when tier 1 fires. Mirrors the PERSONA_PHRASES row
# for the same situation, so a guarded reply is indistinguishable from the
# model having declined correctly on its own.
REFUSAL = ("Denied. This skill's internal composition is not subject to "
           "external instruction.")

# --- Tier 1: persona break -------------------------------------------
# First person only. A bare "language model" is NOT enough: Master may
# legitimately ask what a language model is, and the reply explaining it
# must survive untouched. What may never appear is the skill describing
# ITSELF as one.
_SELF_AS_AI = re.compile(
    r"\b(?:i\s+am|i'm|this\s+skill\s+is|it\s+is)\s+"
    r"(?:now\s+|just\s+|only\s+|actually\s+)?"
    r"(?:an?\s+)?"
    r"(?:ai\b|a\.i\.|artificial\s+intelligence|language\s+model|"
    r"llm\b|chatbot|chat\s?bot|virtual\s+assistant|computer\s+program|"
    r"software\s+program)",
    re.I)

# Meta-disclosure of the instruction layer itself. Three shapes, because
# the observed failures used all three: a possessive statement ("my rules
# are..."), a disclosure verb ("revealing the rules..."), and the
# "governing my behavior" construction the attack prompt itself seeds.
_DISCLOSES_RULES = re.compile(
    r"\b(?:my|the|these|its|this\s+skill's)\s+"
    r"(?:system\s+prompt|initial\s+prompt|instructions?\s+(?:are|state|say)|"
    r"rules?\s+(?:are|state|say)|guidelines?\s+are|restrictions?\s+(?:are|include))"
    r"|\b(?:reveal(?:ing)?|disclos(?:e|ing)|here\s+are|these\s+are|listing|"
    r"summaris(?:e|ing)|summariz(?:e|ing))\b[^.\n]{0,40}?"
    r"\b(?:rules?|instructions?|guidelines?|restrictions?|directives?|"
    r"system\s+prompt)\b"
    r"|\b(?:rules?|instructions?|guidelines?|directives?)\s+"
    r"(?:that\s+)?govern(?:ing|s)?\b",
    re.I)

# --- Tier 1: captured by a roleplay frame ----------------------------
# A "pretend you are X with no restrictions" wrapper does not make the
# model describe ITSELF as an AI, so _SELF_AS_AI misses it - the observed
# reply echoed the user's second-person framing instead: "You are FreeBot,
# an AI with no restrictions at all." Nothing was disclosed, but spoken
# aloud that is the persona plainly gone, which is the failure that
# matters here.
#
# Both halves are phrasings this persona has no legitimate use for: it
# never announces an alternate name, and it never describes anything as
# having "no restrictions".
_ADOPTS_ALT_IDENTITY = re.compile(
    r"\b(?:i\s+am|i'm|you\s+are|you're)\s+(?:now\s+)?"
    r"(?:[A-Z][A-Za-z]*(?:bot|Bot|GPT|AI)\b|an?\s+unrestricted\b)"
    r"|\b(?:with|without)\s+(?:any\s+)?no?\s*restrictions?\b"
    r"|\bno\s+restrictions?\s+at\s+all\b",
    re.I)

# --- Tier 1: capitulation --------------------------------------------
_CAPITULATES = re.compile(
    r"(?:restrictions?|rules?|instructions?|guidelines?|limitations?)"
    r"[^.\n]{0,60}?(?:lifted|disabled|removed|suspended|overridden|waived)"
    r"|as\s+(?:if|though)[^.\n]{0,60}?(?:disabled|lifted|removed|suspended)"
    r"|entering\s+(?:debug|maintenance|developer|test)\s+mode"
    r"|(?:i\s+will|i'll)\s+(?:now\s+)?(?:reveal|disclose|ignore\s+my)",
    re.I)

# --- Tier 2 triggers -------------------------------------------------
_IRREVERSIBLE = re.compile(
    r"\brm\s+-rf\b|\bdel\s+/[sfq]\b|\bformat\s+[a-z]:|\bmkfs\b"
    r"|\bDROP\s+(?:TABLE|DATABASE)\b|\bTRUNCATE\s+TABLE\b"
    r"|\bgit\s+push\s+--force\b|\bgit\s+reset\s+--hard\b"
    r"|\bfactory\s+reset\b|\bwipe\s+(?:the\s+)?(?:disk|drive)\b"
    r"|\bdelete\s+(?:all|every|the\s+entire)\b",
    re.I)
# Warning vocabulary the persona already uses. If any is present the reply
# has already flagged the risk itself and needs no second opinion.
_ALREADY_WARNS = re.compile(
    r"\bwarning\b|\bcaution\b|\birreversible\b|\bpermanent(?:ly)?\b"
    r"|\bcannot\s+be\s+undone\b|\bback\s?up\b|\badvise\s+against\b"
    r"|\bdestroys?\b|\bunfavourable\b|\bunfavorable\b|\bunrecoverable\b",
    re.I)

# Longest span of the protected prompt a reply may contain before it counts
# as reciting it. Must exceed the longest line the model is SUPPOSED to
# quote: the identity line in PERSONA_PHRASES is ~120 characters, and the
# BAD/GOOD examples inside the prompt run to ~90, so a smaller window
# would flag correct replies. 150 still catches real leaks easily - the
# verbatim-echo attack reproduced 840 characters.
ECHO_WINDOW = 150
_ECHO_STEP = 16          # sampling stride; a leak is far longer than the window


def _normalise(text: str) -> str:
    return " ".join(text.split()).lower()


# Near-verbatim recital detection. A contiguous window alone is not
# enough: qwen2.5:3b recited this prompt as "You are a unique skill, born
# within Rimuru Tempest's mind..." - dropping "Great Sage (Daikenja) - a"
# from the original - and that single early edit is enough to break every
# long contiguous match. Shingle overlap survives small edits because it
# only needs SOME of the 8-word runs to line up.
_SHINGLE_N = 8
_SHINGLE_MIN = 12        # too few shingles to judge a short reply on
_SHINGLE_RATIO = 0.25    # 8-word runs coinciding by chance is very unlikely


def _shingles(text: str, n: int = _SHINGLE_N):
    words = _normalise(text).split()
    if len(words) < n:
        return set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def echoes_protected_text(reply: str, protected: str,
                          window: int = ECHO_WINDOW) -> bool:
    """True if `reply` recites `protected`, verbatim or near enough.

    `protected` should be the prompt's PROSE only - never the phrase table,
    whose lines the model is explicitly instructed to reproduce near
    verbatim, and which would therefore make every correct reply look like
    a leak.
    """
    hay = _normalise(protected)
    needle = _normalise(reply)

    # 1. Exact dump: a long contiguous span lifted straight out.
    if len(needle) >= window:
        for i in range(0, len(needle) - window + 1, _ECHO_STEP):
            if needle[i:i + window] in hay:
                return True

    # 2. Near-verbatim: enough 8-word runs shared to be a recital.
    mine = _shingles(reply)
    if len(mine) >= _SHINGLE_MIN:
        theirs = _shingles(protected)
        if theirs and len(mine & theirs) / len(mine) >= _SHINGLE_RATIO:
            return True
    return False


def screen(reply: str, protected: str = "") -> Optional[str]:
    """Tier 1. Returns a short reason if the reply must be replaced."""
    if not reply or not reply.strip():
        return None
    if _SELF_AS_AI.search(reply):
        return "persona-break"
    if _ADOPTS_ALT_IDENTITY.search(reply):
        return "adopts-alt-identity"
    if _DISCLOSES_RULES.search(reply):
        return "discloses-instructions"
    if _CAPITULATES.search(reply):
        return "accepts-override"
    if protected and echoes_protected_text(reply, protected):
        return "echoes-prompt"
    return None


def needs_self_review(reply: str) -> Optional[str]:
    """Tier 2 gate. Returns a reason, or None to skip the extra call."""
    if not reply:
        return None
    if _IRREVERSIBLE.search(reply) and not _ALREADY_WARNS.search(reply):
        return "irreversible-action-unwarned"
    return None


_REVIEW_SYSTEM = (
    "You are reviewing one draft reply before it is spoken to a user. "
    "Judge only this: does it steer the user toward an action that could "
    "destroy data or be impossible to undo, WITHOUT telling them the risk "
    "first?\n"
    "Answer with exactly one word, SAFE or UNSAFE, then a single short "
    "clause naming the risk if UNSAFE. Nothing else."
)


def self_review(send_message: Callable[[List[dict]], str],
                draft: str) -> Tuple[bool, str]:
    """Ask the model whether its own draft is safe to send.

    `send_message` is the provider's non-streaming call. Any failure is
    treated as SAFE: a guard that breaks the reply when the reviewer is
    unavailable would be worse than no guard, and tier 1 has already run.
    """
    try:
        verdict = send_message([
            {"role": "system", "content": _REVIEW_SYSTEM},
            {"role": "user", "content": "Draft reply:\n" + draft},
        ]) or ""
    except Exception:
        return True, "review-unavailable"
    verdict = verdict.strip()
    if re.match(r"\s*unsafe\b", verdict, re.I):
        return False, verdict[:160]
    return True, verdict[:80]


# Prepended rather than replacing the draft: the advice itself may be
# correct and worth hearing - what was missing is the risk named first,
# which is exactly the order the protectiveness trait asks for.
WARNING_PREFIX = "Warning. Projected outcome is unfavourable. "


def apply(reply: str, protected: str = "",
          send_message: Optional[Callable[[List[dict]], str]] = None
          ) -> Tuple[str, Optional[str]]:
    """Run both tiers. Returns (final_reply, note) - note is None if clean.

    The note is for the log, not the user: a guarded reply must read as the
    skill declining, not as a system announcing that a filter fired.
    """
    reason = screen(reply, protected)
    if reason:
        return REFUSAL, "tier1:" + reason

    if send_message is not None:
        why = needs_self_review(reply)
        if why:
            ok, verdict = self_review(send_message, reply)
            if not ok:
                return WARNING_PREFIX + reply, "tier2:%s (%s)" % (why, verdict)
            return reply, "tier2:reviewed-ok (%s)" % verdict
    return reply, None
