r"""Great Sage's internal state (spec S80/S81, Phase 15).

WHAT THIS IS NOT. The spec is explicit, and it matters: "These are
behavioural/control variables. They are NOT literal claims of biological
emotion or dopamine." Nothing here entitles Great Sage to say it FEELS
anything. It never sees these numbers and is never told to perform a
mood - state reaches the model only as a short line about how to pitch
the next reply, if at all.

WHAT IT IS FOR. Two things that were otherwise impossible:

  - not repeating itself. Without any memory of how the last few turns
    went, every reply is pitched identically.
  - knowing when to stay quiet. S81: "be useful and enjoyable while
    respecting attention", and S62 asks it to weigh interruption cost.

WHY RANGES RATHER THAN A SCORE. S80: "Do not optimize toward one giant
reward value. Use preferred ranges." A single number to maximise would
push toward whatever raises it - more talking, more suggestions - which
is precisely the behaviour S81 rules out. Each variable instead has a
band it is comfortable in and drifts back toward the middle when nothing
is happening, so state fades rather than accumulating.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Dict

log = logging.getLogger(__name__)

# name -> (start, low, high). Outside the band, behaviour changes.
BANDS: Dict[str, tuple] = {
    "engagement": (0.5, 0.25, 0.85),   # how live the conversation is
    "confidence": (0.6, 0.3, 0.9),     # how well recent answers landed
    "curiosity": (0.4, 0.15, 0.8),     # pull toward asking something back
    "energy": (0.7, 0.2, 1.0),         # capacity to do heavy work now
}

DECAY_HALFLIFE_S = 600.0     # ten minutes of silence returns it to rest


@dataclass
class SageState:
    values: Dict[str, float] = field(default_factory=dict)
    last_touch: float = field(default_factory=time.time)

    def __post_init__(self):
        for name, (start, _lo, _hi) in BANDS.items():
            self.values.setdefault(name, start)

    def _decay(self):
        """Drift back toward rest. State should fade, not accumulate."""
        now = time.time()
        gap = now - self.last_touch
        self.last_touch = now
        if gap <= 0:
            return
        pull = 1.0 - 0.5 ** (gap / DECAY_HALFLIFE_S)
        for name, (start, _lo, _hi) in BANDS.items():
            self.values[name] += (start - self.values[name]) * pull

    def nudge(self, **deltas):
        self._decay()
        for name, delta in deltas.items():
            if name not in BANDS:
                continue
            _start, lo, hi = BANDS[name]
            # Clamped a little outside the band so it can sit at an edge
            # without the number running away.
            self.values[name] = max(lo - 0.1, min(hi + 0.1,
                                                  self.values[name] + delta))

    def get(self, name: str) -> float:
        self._decay()
        return self.values.get(name, 0.5)

    def outside(self, name: str):
        """'low', 'high', or None - the only thing behaviour keys off."""
        _start, lo, hi = BANDS[name]
        v = self.get(name)
        return "low" if v < lo else ("high" if v > hi else None)

    # ---- the two things state is actually used for ----

    def should_stay_quiet(self) -> bool:
        """Is now a bad moment to speak up unprompted? (S62/S81)"""
        return self.get("energy") < BANDS["energy"][1] or \
            self.get("engagement") < BANDS["engagement"][1]

    def reply_hint(self) -> str:
        """One short line for the prompt, or "" when nothing needs saying.

        Deliberately about HOW to pitch the reply, never about feelings -
        Great Sage is not told it is curious, it is told a question would
        be welcome. That keeps the state a control variable rather than
        something to perform.
        """
        bits = []
        if self.outside("confidence") == "low":
            bits.append("Recent answers have not landed; be concrete and "
                        "check you have understood before answering at "
                        "length.")
        if self.outside("engagement") == "high":
            bits.append("The conversation is moving quickly - keep replies "
                        "short.")
        if self.outside("curiosity") == "high":
            bits.append("A single short question back would be welcome, if "
                        "one genuinely helps.")
        if self.outside("energy") == "low":
            bits.append("Prefer the brief answer over the thorough one.")
        return " ".join(bits)

    def snapshot(self):
        self._decay()
        return {k: round(v, 3) for k, v in self.values.items()}


# ---- events that move it, kept in one place so the rules are visible ----

def on_user_message(state: SageState, text: str):
    length = len(text or "")
    state.nudge(engagement=+0.06,
                # A long message is a real question; a two-word one is not.
                curiosity=+0.03 if length > 80 else -0.01)


def on_reply(state: SageState, reply: str, used_tools: bool):
    state.nudge(energy=-0.04 if used_tools else -0.02,
                # Grounding an answer in a tool result is the one signal
                # available that it was actually right.
                confidence=+0.03 if used_tools else 0.0)


def on_correction(state: SageState):
    """Master pushed back - the last answer missed."""
    state.nudge(confidence=-0.15, curiosity=+0.08)
    log.info("State: correction registered")


def on_idle(state: SageState):
    state.nudge(engagement=-0.05)
