"""
Local speech-to-text via faster-whisper - CPU-only, fully offline, no
cloud API. Used by both push-to-talk and the wake-word listener (see
speech_input.py) to turn recorded microphone audio into text, which then
feeds into the same ChatEngine path a typed message would.

The model loads lazily (first call only) and stays cached for the life of
the process - loading takes a couple seconds, so it happens once, not per
utterance. First run also downloads the model (~145MB for "base.en") to
the Hugging Face cache.
"""

import logging

import numpy as np

log = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        # base.en: English-only, ~145MB, good accuracy/speed balance on
        # CPU for short utterances. int8 compute keeps it fast without a GPU.
        _model = WhisperModel("base.en", device="cpu", compute_type="int8")
    return _model


def transcribe(audio: np.ndarray) -> str:
    """audio must be mono float32 samples at 16000 Hz (speech_input.py's
    recorders capture at that rate directly, so no resampling needed)."""
    if audio.size == 0:
        log.warning("Transcription skipped: empty recording")
        return ""
    model = _get_model()
    segments, _ = model.transcribe(audio, language="en", vad_filter=True)
    text = "".join(seg.text for seg in segments).strip()

    # An empty result used to be completely silent: faster_whisper logged
    # that it processed the audio, this returned "", and the caller simply
    # did nothing. Push-to-talk looked like it worked while Sage never
    # heard a word, with nothing anywhere saying why.
    #
    # That is exactly how a packaged build failed - vad_filter needs
    # faster_whisper's assets/silero_vad_v6.onnx, which was not bundled,
    # so nothing was ever detected as speech. Worth a line in the log
    # whichever way it goes.
    seconds = audio.size / 16000.0
    if text:
        log.info("Transcribed %.1fs of audio: %r", seconds, text)
    else:
        log.warning(
            "Transcribed %.1fs of audio but got NO text. Either nothing was "
            "said, or the VAD filter rejected it - check that "
            "faster_whisper/assets/silero_vad_v6.onnx is present.", seconds)
    return text
