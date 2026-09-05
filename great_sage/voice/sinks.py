"""
Where synthesized/pre-recorded audio actually gets played.

Separating "how to speak text" (a TTS engine, e.g. pocket_tts_engine.py)
from "where the resulting audio goes" (a sink) lets the same engine
either play through local speakers (the CLI) or stream to a connected
HUD browser client (great_sage/voice/browser_sink.py), without
duplicating any synthesis logic.
"""

from abc import ABC, abstractmethod


class AudioSink(ABC):
    @abstractmethod
    def play(self, samples, samplerate: int) -> None:
        """Play a float32 numpy array. Blocks until playback finishes."""
        raise NotImplementedError

    @abstractmethod
    def play_file(self, path: str) -> None:
        """Play a pre-recorded audio file. Blocks until playback finishes."""
        raise NotImplementedError

    def stop(self) -> None:
        """Stop any playback in progress, if possible. Optional to override."""


class LocalSpeakerSink(AudioSink):
    """Plays through this machine's own speakers via sounddevice - the
    original, CLI-only behavior, unchanged."""

    def __init__(self):
        import sounddevice as sd
        import soundfile as sf

        self._sd = sd
        self._sf = sf

    def play(self, samples, samplerate: int) -> None:
        self._sd.play(samples, samplerate=samplerate)
        self._sd.wait()

    def play_file(self, path: str) -> None:
        data, samplerate = self._sf.read(path, dtype="float32")
        self.play(data, samplerate)

    def stop(self) -> None:
        self._sd.stop()
