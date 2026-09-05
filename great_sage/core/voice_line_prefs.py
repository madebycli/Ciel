"""
Per-voice-line on/off preference: whether a configured VOICE_LINES phrase
(config/settings.py) plays its pre-recorded clip, or falls through to
live TTS synthesis instead - e.g. hearing Pocket TTS actually say
"Notice." in your cloned voice rather than always playing koku.ogg.

Toggled live from the HUD's settings panel (see great_sage/server.py's
set_voice_line_enabled handling); persisted to a small JSON file, keyed
by the voice line's regex pattern string, so the choice survives restarts.
A pattern with no entry here defaults to enabled (the original behavior).
"""

import json
import os
from typing import Dict


def load_overrides(path: str) -> Dict[str, bool]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): bool(v) for k, v in data.items()}
    except (ValueError, OSError):
        return {}


def save_override(path: str, pattern: str, enabled: bool) -> None:
    overrides = load_overrides(path)
    overrides[pattern] = enabled
    with open(path, "w", encoding="utf-8") as f:
        json.dump(overrides, f, indent=2)
