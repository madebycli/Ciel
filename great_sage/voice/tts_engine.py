"""
pyttsx3-based voice output.

pyttsx3 wraps the operating system's built-in speech engine - SAPI5 on
Windows. This keeps text-to-speech fully local and offline (no cloud
service, no API key), consistent with Great Sage's local-first principle.
"""

from typing import Optional

import pyttsx3

from great_sage.voice.base import VoiceError, VoiceOutput


class Pyttsx3VoiceOutput(VoiceOutput):
    def __init__(self, rate: int = 175, volume: float = 1.0, voice_id: Optional[str] = None):
        try:
            self._engine = pyttsx3.init()
        except Exception as exc:
            raise VoiceError(
                "Could not start the system speech engine. On Windows, "
                "make sure at least one SAPI5 voice is installed "
                "(Settings > Time & Language > Speech)."
            ) from exc

        self._engine.setProperty("rate", rate)
        self._engine.setProperty("volume", volume)
        if voice_id:
            self._engine.setProperty("voice", voice_id)

    def speak(self, text: str) -> None:
        if not text or not text.strip():
            return
        try:
            self._engine.say(text)
            self._engine.runAndWait()
        except Exception as exc:
            raise VoiceError(f"Speech playback failed: {exc}") from exc

    def stop(self) -> None:
        try:
            self._engine.stop()
        except Exception:
            pass  # best-effort - nothing meaningful to do if stop() itself fails

    @staticmethod
    def list_voice_ids() -> list:
        """Return (id, name) pairs for voices installed on this system.

        Handy for picking a VOICE_ID in config/settings.py.
        """
        engine = pyttsx3.init()
        voices = engine.getProperty("voices")
        return [(v.id, v.name) for v in voices]
