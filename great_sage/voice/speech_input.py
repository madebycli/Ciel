"""
Microphone capture for voice input - this is INPUT (the user talking to
Great Sage), separate from voice/pocket_tts_engine.py which handles
OUTPUT (Great Sage talking back). Two ways in, both feeding the same
speech_to_text.transcribe():

- PushToTalkRecorder: starts/stops on demand, bound to a keybind on the
  HUD side (default Alt+1) - hold to talk, release to send.
- WakeWordListener: runs continuously in the background once enabled,
  listening for one of your configured trigger phrases ("Hey Great Sage",
  etc.) at the start of an utterance.

Neither uses a dedicated wake-word model - training one for a custom
phrase is real setup (data collection, no off-the-shelf model already
knows "Hey Raphael"). Instead both just watch mic volume for voice
activity, record until a short silence, and transcribe the whole
utterance locally; the wake-word listener additionally checks whether the
transcript starts with one of your phrases before doing anything with it.
Simpler and works with any custom phrase immediately, at the cost of
transcribing more audio than a lightweight always-on wake-word engine
would (each utterance-shaped sound gets transcribed, not just real
speech) - fine for a local, single-user desktop app.
"""

import queue
import re
import threading

import logging

import numpy as np
import sounddevice as sd

from great_sage.voice.speech_to_text import transcribe

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
MIN_UTTERANCE_S = 0.3


class PushToTalkRecorder:
    """start()/stop() bracket one recording - stop() transcribes and
    calls on_result(text) if anything came through. Safe to call stop()
    without a matching start() (e.g. a stray keyup); it just no-ops.

    If on_level is given, it's called with the RMS of each captured audio
    block for as long as recording is active - purely for the HUD's
    "listening" visual (a live mic-level meter), not used for anything
    functional here. Called directly from sounddevice's audio callback
    thread, so it must stay non-blocking - server.py's implementation
    fires the websocket send without waiting for it to complete.
    """

    def __init__(self, on_result, on_level=None):
        self._on_result = on_result
        self._on_level = on_level
        self._frames = []
        self._stream = None
        self._lock = threading.Lock()
        # Plain public attribute rather than a constructor arg - server.py
        # sets this directly from the settings-panel mic picker, and it
        # needs to be changeable without recreating this whole object.
        # None means sounddevice's own default input device.
        self.device = None

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                return
            self._frames = []

            def callback(indata, frames, time_info, status):
                self._frames.append(indata[:, 0].copy())
                if self._on_level is not None:
                    self._on_level(float(np.sqrt(np.mean(np.square(indata)))))

            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                device=self.device, callback=callback,
            )
            self._stream.start()

    def stop(self) -> None:
        with self._lock:
            if self._stream is None:
                return
            self._stream.stop()
            self._stream.close()
            self._stream = None
            frames = self._frames
            self._frames = []
        audio = np.concatenate(frames) if frames else np.zeros(0, dtype="float32")
        if audio.size < SAMPLE_RATE * MIN_UTTERANCE_S:
            return  # too short to be a real recording - ignore accidental taps
        text = transcribe(audio)
        if text:
            self._on_result(text)


class WakeWordListener:
    """Runs its own background thread once started; stop() ends it.
    get_trigger_phrases is called fresh each utterance (not cached at
    start time) so changing your phrases in settings takes effect on the
    next utterance without needing to restart listening."""

    VOICE_THRESHOLD = 0.02
    SILENCE_HANG_MS = 700
    MAX_UTTERANCE_S = 12
    BLOCK_MS = 30

    def __init__(self, on_result, get_trigger_phrases):
        self._on_result = on_result
        self._get_trigger_phrases = get_trigger_phrases
        self._running = False
        self._thread = None
        # See PushToTalkRecorder.device - same deal, set directly by
        # server.py from the settings-panel mic picker.
        self.device = None

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _run(self) -> None:
        q: "queue.Queue[np.ndarray]" = queue.Queue()

        def callback(indata, frames, time_info, status):
            q.put(indata[:, 0].copy())

        blocksize = int(SAMPLE_RATE * self.BLOCK_MS / 1000)
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                blocksize=blocksize, device=self.device, callback=callback,
            ):
                buffer = []
                silence_ms = 0
                recording = False
                while self._running:
                    try:
                        block = q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    level = float(np.sqrt(np.mean(np.square(block)))) if block.size else 0.0
                    if level > self.VOICE_THRESHOLD:
                        recording = True
                        silence_ms = 0
                        buffer.append(block)
                    elif recording:
                        silence_ms += self.BLOCK_MS
                        buffer.append(block)
                        total_s = sum(len(b) for b in buffer) / SAMPLE_RATE
                        if silence_ms >= self.SILENCE_HANG_MS or total_s >= self.MAX_UTTERANCE_S:
                            self._handle_utterance(np.concatenate(buffer))
                            buffer = []
                            recording = False
                            silence_ms = 0
        except Exception:
            # Log before re-raising. This runs in its own thread, so the
            # traceback would otherwise go to stderr - which a windowed
            # build does not have, making a dead listener indistinguishable
            # from one that simply never hears a trigger phrase.
            self._running = False
            log.exception("Wake-word listener stopped by an error")
            raise

    def _handle_utterance(self, audio: np.ndarray) -> None:
        if audio.size < SAMPLE_RATE * MIN_UTTERANCE_S:
            return
        text = transcribe(audio)
        if not text:
            return
        remainder = self._match_trigger(text)
        if remainder is not None:
            self._on_result(remainder)

    def _match_trigger(self, text: str):
        """Returns the text AFTER a matched trigger phrase (possibly
        empty, if you only said the wake word), or None if no configured
        phrase matched the start of this utterance."""
        norm_text = re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()
        words = text.split()
        for phrase in self._get_trigger_phrases():
            norm_phrase = re.sub(r"[^a-z0-9 ]", "", phrase.lower()).strip()
            if not norm_phrase or not norm_text.startswith(norm_phrase):
                continue
            word_count = len(norm_phrase.split())
            return " ".join(words[word_count:]).strip()
        return None
