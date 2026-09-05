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
import os
from typing import Any, Dict


def load(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {}
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
