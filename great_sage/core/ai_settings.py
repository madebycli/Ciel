r"""API keys and provider choice for Chat Mode's AI settings.

Separate from hud_settings.py on purpose. That file holds display
preferences and is harmless; this one holds SECRETS, and the two should
never end up in the same place by accident.

Where it lives (spec S67, "safe API-key storage/config"):

  - in DATA_DIR, so %LOCALAPPDATA%\GreatSage in a build and the repo
    folder from source - never inside the application bundle, which is
    overwritten on every update
  - gitignored, so a key cannot be committed by reflex
  - written with the file permissions tightened to the current user on
    Windows, so another account on the machine cannot read it

Keys are never logged, never echoed back to the page in full, and never
included in an error message. The page receives only a MASK (whether a
key is set, and its last four characters) so the user can tell that a key
is saved without the value being recoverable from the UI.
"""

import json
import logging
import os
import subprocess
import tempfile
from typing import Any, Dict

log = logging.getLogger(__name__)

# Providers Great Sage knows how to talk to. "local" needs no key.
PROVIDERS = ("local", "anthropic", "openai")
TTS_PROVIDERS = ("f5", "elevenlabs", "openai")

DEFAULTS: Dict[str, Any] = {
    "chat_provider": "local",
    "chat_model": "",
    "tts_provider": "f5",
    "keys": {},
    # Tool permissions (spec S24). Off by default for anything that
    # reaches outside this machine.
    "allow_web": False,
    "allow_desktop": True,
}


def _tighten(path: str) -> None:
    """Restrict the file to the current user. Best effort, never fatal."""
    try:
        user = os.environ.get("USERNAME")
        if not user:
            return
        subprocess.run(["icacls", path, "/inheritance:r",
                        "/grant:r", "%s:F" % user],
                       capture_output=True, timeout=10,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        log.debug("Could not tighten permissions on the key file")


def load(path: str) -> Dict[str, Any]:
    data = dict(DEFAULTS)
    data["keys"] = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            for k, v in stored.items():
                if k in DEFAULTS:
                    data[k] = v
            if not isinstance(data.get("keys"), dict):
                data["keys"] = {}
    except FileNotFoundError:
        pass
    except Exception:
        # Never mention the file's CONTENTS in a log line - it holds keys.
        log.exception("AI settings unreadable; using defaults")
    return data


def save(path: str, data: Dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, path)
        _tighten(path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def mask(value: str) -> str:
    """What the UI is allowed to see: set-or-not, and the last 4 chars."""
    v = (value or "").strip()
    if not v:
        return ""
    return ("*" * max(4, len(v) - 4)) + v[-4:] if len(v) > 4 else "****"


def public_view(data: Dict[str, Any]) -> Dict[str, Any]:
    """The settings, with every key replaced by a mask.

    This is what goes over the WebSocket. A key that has been saved must
    not be readable back out of the interface - only replaceable.
    """
    out = {k: v for k, v in data.items() if k != "keys"}
    out["keys"] = {name: mask(val) for name, val in
                   (data.get("keys") or {}).items()}
    out["providers"] = list(PROVIDERS)
    out["tts_providers"] = list(TTS_PROVIDERS)
    return out


def apply_update(data: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a change from the page, ignoring anything unrecognised.

    A blank key means "leave it alone", not "erase it": the page only ever
    sends a mask for an existing key, and treating that as the new value
    would overwrite a real key with asterisks. Clearing is explicit, via
    clear_keys.
    """
    for field in ("chat_provider", "chat_model", "tts_provider"):
        if field in update and isinstance(update[field], str):
            data[field] = update[field]
    for field in ("allow_web", "allow_desktop"):
        if field in update:
            data[field] = bool(update[field])
    incoming = update.get("keys")
    if isinstance(incoming, dict):
        keys = dict(data.get("keys") or {})
        for name, val in incoming.items():
            val = (val or "").strip()
            if not val or set(val) <= {"*"} or val.startswith("*"):
                continue          # a mask came back; the key is unchanged
            keys[name] = val
        data["keys"] = keys
    for name in (update.get("clear_keys") or []):
        data.get("keys", {}).pop(name, None)
    return data
