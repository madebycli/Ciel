r"""Voice through an API, for when the local GPU should not do the work.

The settings pane listed "elevenlabs" and "openai" as voice providers
before either existed, and choosing one silently kept using F5. Same
fault as the OpenAI chat entry: a menu offering something it cannot do.

F5 remains the default and the better voice - it is Krazaa's own cloned
voice, and NFE 8 was chosen by listening. These exist for the case he
described: taking load off the machine. An API voice costs no VRAM and no
GPU time, which is exactly what matters while a game is running.

Both return WAV/MP3 bytes rather than samples, so they hand the audio to
the same sink the recorded voice lines already use - that path was built
for a file and needs nothing new.
"""

import logging
from typing import Optional

import requests

from great_sage.voice.base import VoiceError

log = logging.getLogger(__name__)

ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech/%s"
ELEVEN_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"      # "Rachel", their stock voice
OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"


def synthesize(provider: str, api_key: str, text: str,
               voice: Optional[str] = None) -> bytes:
    """Audio bytes for `text`, or raise VoiceError.

    Never puts the key in the exception: a failed request is reported by
    status, because these messages reach the screen and the log.
    """
    text = (text or "").strip()
    if not text:
        raise VoiceError("Nothing to speak.")
    if not api_key:
        raise VoiceError("No API key is set for the %s voice." % provider)

    if provider == "elevenlabs":
        url = ELEVEN_URL % (voice or ELEVEN_DEFAULT_VOICE)
        headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
        body = {"text": text, "model_id": "eleven_turbo_v2_5"}
    elif provider == "openai":
        url = OPENAI_TTS_URL
        headers = {"Authorization": "Bearer " + api_key,
                   "Content-Type": "application/json"}
        body = {"model": "gpt-4o-mini-tts", "voice": voice or "onyx",
                "input": text, "response_format": "wav"}
    else:
        raise VoiceError("Unknown voice provider %r." % provider)

    try:
        r = requests.post(url, headers=headers, json=body, timeout=90)
    except Exception as exc:
        raise VoiceError("Could not reach the %s voice service (%s)."
                         % (provider, type(exc).__name__)) from exc
    if r.status_code == 401:
        raise VoiceError("The %s voice key was rejected. Check it in AI "
                         "settings." % provider)
    if r.status_code == 429:
        raise VoiceError("The %s voice service is rate limiting. Try again "
                         "shortly." % provider)
    if r.status_code != 200:
        raise VoiceError("The %s voice service returned HTTP %s."
                         % (provider, r.status_code))
    if not r.content:
        raise VoiceError("The %s voice service returned no audio." % provider)
    return r.content


def available(cfg) -> bool:
    """Is an API voice both selected AND usable?

    Checked rather than assumed, because falling back to F5 silently is
    what made the menu misleading in the first place. If this returns
    False the caller keeps F5 and says why.
    """
    provider = (cfg or {}).get("tts_provider") or "f5"
    if provider == "f5":
        return False
    key = ((cfg or {}).get("keys") or {}).get("tts", "").strip()
    if not key:
        log.warning("Voice provider %r is selected but no key is set - "
                    "staying on the local F5 voice", provider)
        return False
    return True
