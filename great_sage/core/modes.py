r"""Modes (spec S39/S40): what Great Sage is allowed to spend right now.

A mode is not a label. Each one changes real behaviour, because the point
is the RTX 3060 that Great Sage, the voice model and whatever Krazaa is
actually doing all share:

  COMPANION  the default. Everything on.
  WORK       everything on; the assistant is expected to stay quiet.
  GAMING     unload the model, drop the HUD frame rate, stop listening.
             This is the mode that matters - it hands back the ~4GB the
             model holds and the GPU time the HUD was spending.
  CODING     everything on, longer answers tolerated.
  SLEEP      unload the model, minimum frame rate, no listening.
  PRIVATE    local model only, no web, whatever else is on.
  ONLINE     an API provider may be used.

PRIVATE and ONLINE are about WHERE data goes; the rest are about what is
spent. They are kept in one list anyway, because a mode the user has to
combine by hand is a mode they will get wrong.

Nothing here talks to the HUD or to Ollama directly - a mode only
describes limits, and the server applies them. That keeps the policy
readable in one place instead of scattered through the code that enforces
it.
"""

import logging
from dataclasses import dataclass
from typing import Dict

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Mode:
    name: str
    label: str
    description: str
    # Keep the model resident between replies? False frees ~4GB of VRAM
    # at the cost of a reload (~4s) on the next message.
    keep_model_loaded: bool = True
    # Frame cap the HUD should use. None means "leave it alone".
    hud_fps: int = 0
    # Listen for the wake word in the background?
    wake_word: bool = True
    # May tools reach the network, whatever the permission says?
    allow_web: bool = True
    # May an API provider be used, whatever the settings say?
    allow_online: bool = True


MODES: Dict[str, Mode] = {
    "companion": Mode(
        "companion", "COMPANION",
        "Normal conversation. Everything available."),
    "work": Mode(
        "work", "WORK",
        "Everything available; Great Sage stays out of the way."),
    "gaming": Mode(
        "gaming", "GAMING",
        "Frees the GPU: the model is unloaded between replies, the HUD "
        "drops to a low frame rate, and background listening stops.",
        keep_model_loaded=False, hud_fps=15, wake_word=False),
    "coding": Mode(
        "coding", "CODING",
        "Everything available; longer answers are fine."),
    "sleep": Mode(
        "sleep", "SLEEP",
        "Minimal. The model is unloaded and nothing runs in the "
        "background until you speak first.",
        keep_model_loaded=False, hud_fps=10, wake_word=False),
    "private": Mode(
        "private", "PRIVATE",
        "Local only. No web access and no online model, regardless of "
        "what the settings say.",
        allow_web=False, allow_online=False),
    "online": Mode(
        "online", "ONLINE",
        "An online model may be used if one is configured."),
}

DEFAULT = "companion"


def get(name: str) -> Mode:
    return MODES.get((name or "").strip().lower(), MODES[DEFAULT])


def public_list():
    return [{"name": m.name, "label": m.label, "description": m.description}
            for m in MODES.values()]
