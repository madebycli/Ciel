"""
Voice-cloned TTS output using Kyutai's Pocket TTS.

Clones a voice from a short reference audio clip (yours, with your
consent) and speaks arbitrary text in that voice. Runs entirely locally
via the `pocket-tts` package - no cloud service, no account, no GPU
needed (CPU-only, ~100M parameters, MIT licensed). Much lighter than
voice/cloned_voice_engine.py (XTTS-v2): no CUDA install, no FFmpeg/
torchcodec dependency chain, clones from ~5s of reference audio instead
of 15-30s. Trade-off: English (plus French/German/Spanish/Portuguese/
Italian) only - no Japanese.

Pocket TTS's documented Python API returns one complete audio buffer per
generate_audio() call rather than an incremental chunk generator, so
this plays each segment as a single buffer instead of streaming it
piece by piece - simpler than cloned_voice_engine.py's approach, and
sidesteps the buffer-underrun choppiness that came with XTTS's
chunk-by-chunk inference_stream().
"""

import os
from typing import Iterable, List, Optional, Set

from great_sage.voice.base import VoiceError, VoiceOutput
from great_sage.voice.sinks import AudioSink, LocalSpeakerSink
from great_sage.voice.voice_lines import VoiceLine, label_from_pattern, split_voice_lines


class PocketTTSVoiceOutput(VoiceOutput):
    def __init__(
        self,
        reference_audio_path: str,
        voice_lines: Optional[List[VoiceLine]] = None,
        sink: Optional[AudioSink] = None,
        disabled_voice_line_patterns: Optional[Iterable[str]] = None,
    ):
        if not os.path.isfile(reference_audio_path):
            raise VoiceError(
                f"Reference audio not found at '{reference_audio_path}'. "
                "Record a clean ~5-30s clip of your own voice and point "
                "CLONE_REFERENCE_AUDIO_PATH in config/settings.py at it."
            )
        self.voice_lines = voice_lines or []

        for _, path in self.voice_lines:
            if not os.path.isfile(path):
                raise VoiceError(
                    f"Voice line audio not found at '{path}'. Check the "
                    "paths in config/settings.py's VOICE_LINES."
                )

        try:
            from pocket_tts import TTSModel
        except ImportError as exc:
            raise VoiceError(
                "Pocket TTS requires the 'pocket-tts' package. Install "
                "with: pip install pocket-tts"
            ) from exc

        try:
            self._model = TTSModel.load_model()
        except Exception as exc:
            raise VoiceError(f"Could not load the Pocket TTS model: {exc}") from exc

        try:
            self._voice_state = self._model.get_state_for_audio_prompt(
                reference_audio_path
            )
        except Exception as exc:
            raise VoiceError(
                f"Could not process reference audio '{reference_audio_path}': "
                f"{exc}"
            ) from exc
        self._reference_audio_path = reference_audio_path

        # Defaults to local-speaker playback (the original CLI behavior);
        # the HUD app (run_hud.py) swaps this for a BrowserAudioSink per
        # connection via set_sink() instead, so the same synthesis code
        # above is shared by both.
        try:
            self._sink: AudioSink = sink or LocalSpeakerSink()
        except ImportError as exc:
            raise VoiceError(
                "Voice playback requires the 'sounddevice' and "
                "'soundfile' packages. Install them with: "
                "pip install -r requirements.txt"
            ) from exc

        self._stop_requested = False
        self._disabled_patterns: Set[str] = set(disabled_voice_line_patterns or ())

    def set_sink(self, sink: AudioSink) -> None:
        """Swap where audio gets played - e.g. per HUD client connection."""
        self._sink = sink

    @property
    def current_sink(self) -> AudioSink:
        return self._sink

    def set_reference_audio(self, reference_audio_path: str) -> None:
        """Switches the cloned voice at runtime (e.g. from the HUD's voice
        picker) by recomputing the conditioning state from a different
        reference clip - same call the constructor makes, just repeatable."""
        if not os.path.isfile(reference_audio_path):
            raise VoiceError(f"Reference audio not found at '{reference_audio_path}'.")
        try:
            self._voice_state = self._model.get_state_for_audio_prompt(
                reference_audio_path
            )
        except Exception as exc:
            raise VoiceError(
                f"Could not process reference audio '{reference_audio_path}': "
                f"{exc}"
            ) from exc
        self._reference_audio_path = reference_audio_path

    @property
    def current_reference_audio(self) -> str:
        return self._reference_audio_path

    def set_voice_line_enabled(self, pattern_str: str, enabled: bool) -> None:
        """Toggle whether a configured voice line plays its clip (True) or
        falls through to live TTS synthesis instead (False)."""
        if enabled:
            self._disabled_patterns.discard(pattern_str)
        else:
            self._disabled_patterns.add(pattern_str)

    def list_voice_lines(self) -> List[dict]:
        """For the HUD's settings panel: each configured voice line's
        pattern (a stable id), a human-readable label, its current
        enabled state, and its clip's filename (for the HUD's preview
        button - matched against the copies already in its own
        voice_lines/ folder, since the real path here often points
        outside anywhere the HUD can serve files from)."""
        return [
            {
                "pattern": pattern.pattern,
                "label": label_from_pattern(pattern.pattern),
                "enabled": pattern.pattern not in self._disabled_patterns,
                "file": os.path.basename(path),
            }
            for pattern, path in self.voice_lines
        ]

    def _active_voice_lines(self) -> List[VoiceLine]:
        return [
            (pattern, path)
            for pattern, path in self.voice_lines
            if pattern.pattern not in self._disabled_patterns
        ]

    def _speak_tts_segment(self, text: str) -> None:
        try:
            audio = self._model.generate_audio(self._voice_state, text)
        except Exception as exc:
            raise VoiceError(f"Pocket TTS synthesis failed: {exc}") from exc
        if self._stop_requested:
            return
        try:
            self._sink.play(audio.numpy(), self._model.sample_rate)
        except Exception as exc:
            raise VoiceError(f"Audio playback failed: {exc}") from exc

    def _play_audio_file(self, path: str) -> None:
        if self._stop_requested:
            return
        try:
            self._sink.play_file(path)
        except Exception as exc:
            raise VoiceError(f"Audio playback failed: {exc}") from exc

    def speak(self, text: str) -> None:
        if not text or not text.strip():
            return
        self._stop_requested = False
        for kind, payload, spoken in split_voice_lines(
                text, self._active_voice_lines()):
            if self._stop_requested:
                break
            if kind == "audio":
                setattr(self._sink, "pending_text", spoken)
                self._play_audio_file(payload)
            else:
                self._speak_tts_segment(payload)

    def stop(self) -> None:
        self._stop_requested = True
        try:
            self._sink.stop()
        except Exception:
            pass  # best-effort
