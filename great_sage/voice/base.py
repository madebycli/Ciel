"""
Abstract interface for voice output (text-to-speech).

Mirrors models/base.py: core and ui code call only this interface, never
a specific TTS engine directly. That keeps the door open to swap in a
different local engine, or later an online one, without touching
anything outside this module.
"""

from abc import ABC, abstractmethod


class VoiceError(Exception):
    """Raised when the voice engine can't initialize or can't speak."""


class VoiceOutput(ABC):
    @abstractmethod
    def speak(self, text: str) -> None:
        """Speak the given text aloud. Blocks until finished speaking.

        Raises VoiceError on failure.
        """
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        """Stop any speech currently in progress, if possible."""
        raise NotImplementedError
