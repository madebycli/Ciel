"""
General HUD settings persistence - everything the settings panel controls
(sliders, toggles, color pickers, caption mode, which cloned-voice
reference clip is active) saved to a single JSON file and read back on
startup, so the HUD looks/behaves the same across restarts instead of
resetting to defaults every time.

"active_voice" is called out as its own top-level key because the server
needs it immediately at startup to pick which reference audio to load;
everything else lives under "hud" and is opaque to the server - it's just
handed back to the HUD's JS on connect (see server.py's "hud_settings"
message), which knows what each key means and how to apply it.
"""

import json
import logging
import os
import sys
from typing import Any, Dict


log = logging.getLogger(__name__)


def _shipped_defaults() -> Dict[str, Any]:
    """The tuned settings that ship with the app.

    Everything in the settings panel is per-machine and therefore not in
    version control - which meant a fresh install got raw code defaults
    and looked and sounded nothing like the thing that was built. The
    colours, the scales, the particle counts, the sound levels and the
    voice are all deliberate choices, so they ship as a starting point.

    Read-only, so it lives with the bundled code (_MEIPASS when frozen)
    rather than in the user's data folder.
    """
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        with open(os.path.join(base, "defaults", "hud_settings.json"),
                  "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def load(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        # First run. Seed from the shipped defaults and write them, so
        # from here on this behaves like any other settings file - and so
        # the user's own changes are never fighting a default that keeps
        # coming back.
        seed = _shipped_defaults()
        if seed:
            try:
                save(path, seed)
                log.info("Seeded settings from the shipped defaults")
            except Exception:
                pass          # a read-only folder must not stop startup
        return seed
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def save(path: str, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def save_active_voice(path: str, voice_id: str) -> None:
    data = load(path)
    data["active_voice"] = voice_id
    save(path, data)


def save_hud_settings(path: str, hud_settings: Dict[str, Any]) -> None:
    data = load(path)
    data["hud"] = hud_settings
    save(path, data)
