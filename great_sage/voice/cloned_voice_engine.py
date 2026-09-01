"""
Voice-cloned TTS output using Coqui XTTS-v2.

Clones a voice from a short reference audio clip (yours, with your
consent) and speaks arbitrary text in that voice. Runs entirely locally
via the `coqui-tts` package - no cloud service, no account. Uses a CUDA
GPU automatically if available; falls back to CPU (much slower) if not.

This is heavier than voice/tts_engine.py (pyttsx3): it needs a GPU-class
PyTorch install and downloads a multi-GB model on first run. See the
README's "Voice cloning" section before enabling this.
"""

import os
import re
from typing import Callable, List, Optional, Pattern, Tuple

from great_sage.voice.base import VoiceError, VoiceOutput

_XTTS_MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"

# XTTS-v2 truncates/garbles input past a per-language character count, so
# speak() must split a reply into pieces under that limit before handing
# each to inference_stream() - feeding it a whole multi-sentence reply is
# what caused the cutting-in-and-out audio. The limit is much stricter for
# CJK languages (e.g. 71 for Japanese) than for English - this is why a
# language switch to "ja" broke without this table. Values match the
# per-language limits Coqui's own tokenizer enforces internally.
_CHAR_LIMITS = {
    "en": 250, "de": 253, "fr": 273, "es": 239, "it": 213, "pt": 203,
    "pl": 224, "zh-cn": 82, "ar": 166, "cs": 186, "ru": 182, "nl": 251,
    "tr": 226, "ja": 71, "hu": 224, "ko": 95,
}
_DEFAULT_CHAR_LIMIT = 250

VoiceLine = Tuple[Pattern, str]


class XTTSClonedVoiceOutput(VoiceOutput):
    def __init__(
        self,
        reference_audio_path: str,
        language: str = "en",
        device: Optional[str] = None,
        speed: float = 1.15,
        voice_lines: Optional[List[VoiceLine]] = None,
        translator: Optional[Callable[[str], str]] = None,
    ):
        if not os.path.isfile(reference_audio_path):
            raise VoiceError(
                f"Reference audio not found at '{reference_audio_path}'. "
                "Record a clean 15-30s clip of your own voice and point "
                "CLONE_REFERENCE_AUDIO_PATH in config/settings.py at it."
            )
        self.reference_audio_path = reference_audio_path
        self.language = language
        self.speed = speed
        self.voice_lines = voice_lines or []
        self._translator = translator
        self._char_limit = _CHAR_LIMITS.get(language, _DEFAULT_CHAR_LIMIT)

        for _, path in self.voice_lines:
            if not os.path.isfile(path):
                raise VoiceError(
                    f"Voice line audio not found at '{path}'. Check the "
                    "paths in config/settings.py's VOICE_LINES."
                )

        try:
            import torch
            from TTS.api import TTS  # provided by the 'coqui-tts' package
        except ImportError as exc:
            raise VoiceError(
                "Voice cloning requires the 'coqui-tts' and 'torch' "
                "packages. See the README's 'Voice cloning' section for "
                "the correct install order (torch with CUDA first)."
            ) from exc

        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        if self._device == "cpu":
            print(
                "[Voice] No CUDA GPU detected by torch - running XTTS-v2 "
                "on CPU. This will be noticeably slow. If you have an "
                "NVIDIA GPU, double check your torch install (see README)."
            )

        try:
            self._tts = TTS(_XTTS_MODEL_NAME).to(self._device)
        except Exception as exc:
            raise VoiceError(
                f"Could not load the XTTS-v2 model: {exc}. "
                "First run downloads several GB - check your internet "
                "connection and disk space."
            ) from exc

        # Dropping to the underlying Xtts model (instead of the high-level
        # TTS.api.TTS.tts() wrapper) lets us: (1) compute the voice-cloning
        # conditioning from the reference clip once here, instead of on
        # every single speak() call, and (2) use streaming inference below
        # so playback starts after the first audio chunk instead of after
        # the entire reply has been synthesized. Both cut a lot of the
        # per-message delay - see get_conditioning_latents()/
        # inference_stream() in Coqui's XTTS docs.
        self._model = self._tts.synthesizer.tts_model
        try:
            self._gpt_cond_latent, self._speaker_embedding = (
                self._model.get_conditioning_latents(
                    audio_path=[reference_audio_path]
                )
            )
        except Exception as exc:
            raise VoiceError(
                f"Could not process reference audio '{reference_audio_path}': "
                f"{exc}"
            ) from exc

        try:
            import sounddevice as sd
            import soundfile as sf
            self._sd = sd
            self._sf = sf
        except ImportError as exc:
            raise VoiceError(
                "Voice playback requires the 'sounddevice' and "
                "'soundfile' packages. Install them with: "
                "pip install -r requirements.txt"
            ) from exc

        self._stop_requested = False
        self._stream = None

    def _split_into_chunks(self, text: str) -> List[str]:
        """Break text into pieces XTTS-v2 won't truncate for self.language.

        Splits on sentence boundaries first, then hard-wraps any single
        sentence that's still too long on its own.
        """
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        chunks: List[str] = []
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            while len(sentence) > self._char_limit:
                cut = sentence.rfind(" ", 0, self._char_limit)
                if cut <= 0:
                    cut = self._char_limit
                chunks.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            chunks.append(sentence)
        return chunks

    def _split_voice_lines(self, text: str) -> List[Tuple[str, str]]:
        """Split text into ("tts", text) / ("audio", file_path) segments.

        Scans for the earliest-matching configured voice-line trigger
        (e.g. an opening "Notice." or a "Good morning, Master" greeting)
        repeatedly, so a pre-recorded clip plays for that phrase instead
        of it being synthesized, while the rest of the reply still goes
        through the TTS pipeline as normal.
        """
        segments: List[Tuple[str, str]] = []
        remaining = text
        while remaining and self.voice_lines:
            best_match = None
            best_path = None
            for pattern, path in self.voice_lines:
                match = pattern.search(remaining)
                if match and (
                    best_match is None or match.start() < best_match.start()
                ):
                    best_match = match
                    best_path = path
            if best_match is None:
                break
            before = remaining[: best_match.start()].strip()
            if before:
                segments.append(("tts", before))
            segments.append(("audio", best_path))
            remaining = remaining[best_match.end():].strip()
        if remaining:
            segments.append(("tts", remaining))
        return segments

    def _speak_tts_segment(self, text: str) -> None:
        if self._translator is not None:
            try:
                text = self._translator(text)
            except Exception as exc:
                raise VoiceError(f"Translation failed: {exc}") from exc
            if self._stop_requested or not text.strip():
                return
        for piece in self._split_into_chunks(text):
            if self._stop_requested:
                return
            try:
                audio_chunks = self._model.inference_stream(
                    piece,
                    self.language,
                    self._gpt_cond_latent,
                    self._speaker_embedding,
                    speed=self.speed,
                )
            except Exception as exc:
                raise VoiceError(f"Voice cloning synthesis failed: {exc}") from exc
            for chunk in audio_chunks:
                if self._stop_requested:
                    return
                audio = chunk.detach().cpu().numpy().reshape(-1).astype("float32")
                self._stream.write(audio)

    def _play_audio_file(self, path: str) -> None:
        try:
            data, samplerate = self._sf.read(path, dtype="float32")
        except Exception as exc:
            raise VoiceError(f"Could not read voice line '{path}': {exc}") from exc
        if self._stop_requested:
            return
        try:
            self._sd.play(data, samplerate=samplerate)
            self._sd.wait()
        except Exception as exc:
            raise VoiceError(f"Audio playback failed: {exc}") from exc

    def speak(self, text: str) -> None:
        if not text or not text.strip():
            return
        self._stop_requested = False
        segments = self._split_voice_lines(text)

        try:
            self._stream = self._sd.OutputStream(
                samplerate=24000, channels=1, dtype="float32"
            )
            self._stream.start()
            for kind, payload in segments:
                if self._stop_requested:
                    break
                if kind == "audio":
                    self._play_audio_file(payload)
                else:
                    self._speak_tts_segment(payload)
        except VoiceError:
            raise
        except Exception as exc:
            raise VoiceError(f"Audio playback failed: {exc}") from exc
        finally:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None

    def stop(self) -> None:
        self._stop_requested = True
        try:
            self._sd.stop()
            if self._stream is not None:
                self._stream.abort()
        except Exception:
            pass  # best-effort
