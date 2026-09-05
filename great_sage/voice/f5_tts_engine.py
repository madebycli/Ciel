"""
Voice-cloned TTS output using F5-TTS.

Clones a voice from a few seconds of reference audio, entirely locally -
MIT-licensed weights, no cloud account. Replaced Qwen3-TTS, which sounded
comparable but was far too slow to hold a conversation with.

Measured head-to-head on the target machine (RTX 3060 12GB), same
reference clip and lines, both warmed up:

    line      Qwen3-TTS 1.7B     F5-TTS (NFE 8)
    short          7.62s              1.15s
    medium        15.10s              1.49s
    long          40.34s              2.27s
                  ~0.27x realtime     up to 4.4x realtime

The gap is architectural, not tuning. Qwen decodes autoregressively, so
its speed is bounded by memory bandwidth divided by weight bytes - which
is why its 0.6B model measured no faster than its 1.7B one. F5 uses
non-autoregressive flow matching: a fixed number of passes over the
weights regardless of length, so it gets FASTER relative to realtime as
text grows, where Qwen stayed pinned at 0.27x.

That flow-matching step count (NFE) is a direct speed/quality dial with
no equivalent in an autoregressive model - see NFE_STEP below.
"""

import os
import queue
import threading
from typing import Iterable, List, Optional, Set

from great_sage.voice.audio_fx import VoiceFX
from great_sage.voice.base import VoiceError, VoiceOutput
from great_sage.voice.sinks import AudioSink, LocalSpeakerSink
from great_sage.voice.voice_lines import VoiceLine, label_from_pattern, split_voice_lines

# Flow-matching steps. Measured on the same 9.5s line: 8 -> 2.16s
# (4.40x realtime), 16 -> 3.94s (2.41x), 32 -> 8.19s (1.16x). 8 was
# chosen after an A/B where the extra steps weren't audibly different;
# raise it if that ever stops being true.
NFE_STEP = 8

# Chunk sizing. Unlike an autoregressive engine this is NOT fighting a
# large fixed per-call floor - F5 runs faster than realtime, so chunking
# exists only
# to get the first words out sooner. The first chunk is allowed to be
# short for that reason; later ones are larger because longer text is
# MORE efficient here (the per-call overhead amortises), the opposite of
# the autoregressive case.
# Tried at 6 so a one-word declaration ("Notice.", "Answer.") could go
# out on its own - measurably faster to first audio, but wrong in
# practice. Every chunk is a SEPARATE clip through the browser sink, with
# a websocket round trip between them, so a one-word chunk means a word,
# then an audible gap, then the rest. Back at 25 the declaration rides
# along with the sentence it introduces ("Notice. That premise is in
# error.") - one natural utterance, no gap inside it.
FIRST_CHUNK_MIN_CHARS = 25
CHUNK_MIN_CHARS = 80
MAX_CHUNK_CHARS = 240

_SENTENCE_END = ".!?"


def split_into_chunks(text: str) -> List[str]:
    """Split into speakable chunks on sentence boundaries: a short first
    chunk so speech starts quickly, then larger ones.

    Deliberately NOT a split on every punctuation mark - "Yeah, I can"
    sent on its own comes back sounding clipped.
    """
    text = " ".join(text.split())
    if not text:
        return []

    sentences: List[str] = []
    start = 0
    for i, ch in enumerate(text):
        if ch in _SENTENCE_END and (i + 1 == len(text) or text[i + 1].isspace()):
            sentences.append(text[start : i + 1].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)

    chunks: List[str] = []
    pending = ""
    for sentence in sentences:
        pending = f"{pending} {sentence}".strip() if pending else sentence
        threshold = FIRST_CHUNK_MIN_CHARS if not chunks else CHUNK_MIN_CHARS
        if len(pending) >= threshold:
            chunks.append(pending)
            pending = ""
    if pending:
        # Judged against the SMALL threshold: a 45-char sentence is
        # perfectly speakable alone and starts sooner, so only a genuinely
        # tiny fragment gets folded back into the previous chunk.
        if chunks and len(pending) < FIRST_CHUNK_MIN_CHARS:
            chunks[-1] = f"{chunks[-1]} {pending}".strip()
        else:
            chunks.append(pending)

    bounded: List[str] = []
    for chunk in chunks:
        while len(chunk) > MAX_CHUNK_CHARS:
            cut = chunk.rfind(" ", 0, MAX_CHUNK_CHARS)
            if cut <= 0:
                break
            bounded.append(chunk[:cut].strip())
            chunk = chunk[cut:].strip()
        if chunk:
            bounded.append(chunk)
    return bounded


def take_chunk(buffer: str, is_first: bool, final: bool = False):
    """Streaming counterpart to split_into_chunks.

    Given the text accumulated from the model so far, returns
    (chunk, remainder) - chunk being None when nothing is speakable yet
    and the caller should wait for more text. Same thresholds as the
    batch chunker, so streamed and non-streamed speech sound alike.

    A sentence end is only honoured when the FOLLOWING character is known
    to be whitespace. Mid-stream that character may simply not have
    arrived yet, and splitting on a trailing "." would cut "3.14" or
    "Dr. Reynolds" in half; waiting one more delta costs nothing and
    removes the whole class of error. `final` lifts that requirement,
    since at end-of-reply there is nothing left to disambiguate.
    """
    text = buffer.lstrip()
    if not text:
        return None, buffer

    threshold = FIRST_CHUNK_MIN_CHARS if is_first else CHUNK_MIN_CHARS
    for i, ch in enumerate(text):
        if ch not in _SENTENCE_END:
            continue
        at_end = i + 1 == len(text)
        if at_end and not final:
            break  # ambiguous until the next delta arrives
        if not at_end and not text[i + 1].isspace():
            # Small models routinely drop the space after a full stop
            # ("...effects.Normalization is..."). Without this, the whole
            # passage reads as one unsplittable run and speech waits for
            # the 240-char cap instead of the sentence end that is
            # actually there. A lowercase-then-period-then-uppercase
            # boundary is a sentence; "3.14" and "U.S.A" are not.
            if not (text[i + 1].isupper() and i > 0 and text[i - 1].islower()):
                continue  # decimal point, abbreviation, ellipsis mid-word
        if i + 1 >= threshold:
            return text[: i + 1].strip(), text[i + 1 :]

    # No usable sentence end. Emit anyway once the buffer is long enough
    # that waiting would delay speech more than an imperfect split costs.
    if len(text) >= MAX_CHUNK_CHARS:
        cut = text.rfind(" ", 0, MAX_CHUNK_CHARS)
        if cut > 0:
            return text[:cut].strip(), text[cut:]
    if final:
        return text.strip(), ""
    return None, buffer


def _describe_cause_chain(exc, limit=6):
    """"A: msg <- B: msg <- ..." down to the exception that really failed.

    Follows __cause__ first (explicit `raise ... from`), then __context__
    (an error raised while handling another), which is how a wrapped
    import failure keeps the only useful detail it has.
    """
    parts, seen, cur = [], set(), exc
    while cur is not None and id(cur) not in seen and len(parts) < limit:
        seen.add(id(cur))
        parts.append(f"{type(cur).__name__}: {cur}")
        cur = cur.__cause__ or cur.__context__
    return " <- ".join(parts)


class F5TTSVoiceOutput(VoiceOutput):
    def __init__(
        self,
        reference_audio_path: str,
        reference_text: Optional[str] = None,
        voice_lines: Optional[List[VoiceLine]] = None,
        sink: Optional[AudioSink] = None,
        disabled_voice_line_patterns: Optional[Iterable[str]] = None,
        nfe_step: int = NFE_STEP,
        single_shot: bool = True,
    ):
        if not os.path.isfile(reference_audio_path):
            raise VoiceError(
                f"Reference audio not found at '{reference_audio_path}'. "
                "Record a clean ~5s clip and point F5_REFERENCE_AUDIO_PATH "
                "in config/settings.py at it."
            )
        self._single_shot = single_shot
        self.voice_lines = voice_lines or []
        for _, path in self.voice_lines:
            if not os.path.isfile(path):
                raise VoiceError(
                    f"Voice line audio not found at '{path}'. Check the "
                    "paths in config/settings.py's VOICE_LINES."
                )

        try:
            from f5_tts.api import F5TTS
            from f5_tts.infer.utils_infer import infer_process, preprocess_ref_audio_text
        except ImportError as exc:
            # The original exception is included, not just swallowed behind
            # the install hint. Running from source a missing f5-tts really
            # is the cause - but in a PACKAGED build f5_tts is present and
            # this fires because one of ITS lazily-imported dependencies did
            # not get bundled. The hint alone sent the diagnosis in exactly
            # the wrong direction: "pip install f5-tts" for a package that
            # was already there.
            #
            # Report the whole CAUSE CHAIN, not just this exception.
            # transformers wraps any failure inside its lazy module as
            #   ModuleNotFoundError("Could not import module 'pipeline'.
            #    Are this object's requirements defined correctly?")
            # and chains the real reason with `from e` (see
            # transformers/utils/import_utils.py). Printing only the outer
            # message therefore hides the one fact that identifies the
            # problem, and names 'pipeline' - which is not what is missing.
            #
            # That cost a full diagnosis pass: three different theories
            # about which module was absent, each disproved by reading the
            # bundle, because the message never said what actually failed.
            raise VoiceError(
                "F5-TTS could not be imported (%s). If running from "
                "source, install it with: pip install f5-tts"
                % _describe_cause_chain(exc)
            ) from exc

        self._infer_process = infer_process
        self._preprocess = preprocess_ref_audio_text
        self._nfe_step = nfe_step

        try:
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"
        try:
            self._api = F5TTS(device=device)
        except Exception as exc:
            raise VoiceError(f"Could not load the F5-TTS model: {exc}") from exc

        self._reference_audio_path = reference_audio_path
        self._reference_text = (
            reference_text if reference_text is not None
            else _load_reference_text(reference_audio_path)
        )
        self._ref_cache = None
        self._rebuild_reference()

        try:
            self._sink: AudioSink = sink or LocalSpeakerSink()
        except ImportError as exc:
            raise VoiceError(
                "Voice playback requires the 'sounddevice' and 'soundfile' "
                "packages. Install them with: pip install -r requirements.txt"
            ) from exc

        self._stop_requested = False
        self._disabled_patterns: Set[str] = set(disabled_voice_line_patterns or ())
        self.fx = VoiceFX()  # off by default - see the HUD's AUDIO FX panel

    def set_fx(self, **kwargs) -> None:
        """Update the output effect chain (reverb, flanger, etc.)."""
        self.fx.update(**kwargs)

    # ---------- voice conditioning ----------

    def _rebuild_reference(self) -> None:
        """Preprocess the reference clip once and keep the result.

        F5TTS.infer() runs preprocess_ref_audio_text on every call, which
        re-clips and re-normalises the audio (and transcribes it, when no
        transcript is supplied). Splitting that out and caching it means
        each spoken chunk pays only for the synthesis itself - the same
        win that was worth 1-2s per line on the previous engine.
        """
        try:
            self._ref_cache = self._preprocess(
                self._reference_audio_path,
                self._reference_text or "",
                show_info=lambda *a, **k: None,  # keep it out of the logs
            )
        except Exception as exc:
            raise VoiceError(
                f"Could not process reference audio "
                f"'{self._reference_audio_path}': {exc}"
            ) from exc

    def set_reference_audio(self, reference_audio_path: str) -> None:
        """Switch the cloned voice at runtime (the HUD's voice picker)."""
        if not os.path.isfile(reference_audio_path):
            raise VoiceError(f"Reference audio not found at '{reference_audio_path}'.")
        previous = (self._reference_audio_path, self._reference_text, self._ref_cache)
        self._reference_audio_path = reference_audio_path
        self._reference_text = _load_reference_text(reference_audio_path)
        try:
            self._rebuild_reference()
        except VoiceError:
            # Keep the working voice rather than a half-swapped state that
            # would fail on every subsequent line.
            (self._reference_audio_path, self._reference_text, self._ref_cache) = previous
            raise

    @property
    def current_reference_audio(self) -> str:
        return self._reference_audio_path

    # ---------- sink / voice lines ----------

    def set_sink(self, sink: AudioSink) -> None:
        self._sink = sink

    @property
    def current_sink(self) -> AudioSink:
        return self._sink

    def set_voice_line_enabled(self, pattern_str: str, enabled: bool) -> None:
        if enabled:
            self._disabled_patterns.discard(pattern_str)
        else:
            self._disabled_patterns.add(pattern_str)

    def list_voice_lines(self) -> List[dict]:
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

    # ---------- synthesis ----------

    def generate(self, text: str):
        """Synthesize one chunk. Returns (samples, sample_rate)."""
        ref_audio, ref_text = self._ref_cache
        try:
            wav, sample_rate, _ = self._infer_process(
                ref_audio, ref_text, text,
                self._api.ema_model, self._api.vocoder, self._api.mel_spec_type,
                nfe_step=self._nfe_step,
                show_info=lambda *a, **k: None,
                device=self._api.device,
            )
        except Exception as exc:
            raise VoiceError(f"F5-TTS synthesis failed: {exc}") from exc

        return _pad_tail(wav, sample_rate), sample_rate

    def _speak_tts_segment(self, text: str) -> None:
        """Generate and play in a producer/consumer pair.

        The sink's play() blocks until the audio finishes (the browser
        sink waits on an "audio_ended" ack), so generating and playing on
        one thread would strictly alternate. Running generation on its own
        thread means the next chunk is synthesized while the current one
        is audible.

        Unlike the previous engine this actually keeps up: at ~4x realtime
        the queue fills faster than playback drains it, so speech runs
        continuously instead of gapping between chunks.
        """
        # One clip for the whole segment when single-shot is on. No
        # chunk boundaries means no seams to gap, no effect tails cut at
        # a boundary, and no second audio element to race over.
        if self._single_shot:
            spoken = " ".join(text.split())
            if not spoken:
                return
            samples, rate = self.generate(spoken)
            if not self._stop_requested:
                setattr(self._sink, "pending_text", spoken)
                self._play(samples, rate)
            return

        chunks = split_into_chunks(text)
        if not chunks:
            return
        if len(chunks) == 1:
            samples, rate = self.generate(chunks[0])
            if not self._stop_requested:
                self._play(samples, rate)
            return

        audio_q: "queue.Queue" = queue.Queue(maxsize=2)
        failure: List[BaseException] = []

        def produce():
            try:
                for chunk in chunks:
                    if self._stop_requested:
                        break
                    audio_q.put(self.generate(chunk))
            except BaseException as exc:  # surfaced on the consumer thread
                failure.append(exc)
            finally:
                audio_q.put(None)  # sentinel: nothing more is coming

        worker = threading.Thread(target=produce, daemon=True)
        worker.start()
        try:
            while True:
                item = audio_q.get()
                if item is None or self._stop_requested:
                    break
                samples, rate = item
                self._play(samples, rate)
        finally:
            if failure:
                self._stop_requested = True
            worker.join(timeout=5)
        if failure:
            raise VoiceError(f"F5-TTS synthesis failed: {failure[0]}") from failure[0]

    def _play(self, samples, sample_rate: int) -> None:
        try:
            self._sink.play(self.fx.apply(samples, sample_rate), sample_rate)
        except Exception as exc:
            raise VoiceError(f"Audio playback failed: {exc}") from exc

    def _play_audio_file(self, path: str) -> None:
        if self._stop_requested:
            return
        # With FX off this hands the file straight to the sink. With FX on
        # the clip is decoded here so the same chain runs over it -
        # otherwise the pre-recorded voice lines would sound untreated
        # next to the processed speech around them.
        if not self.fx.settings.enabled:
            try:
                self._sink.play_file(path)
            except Exception as exc:
                raise VoiceError(f"Audio playback failed: {exc}") from exc
            return
        try:
            import soundfile as sf

            data, rate = sf.read(path, dtype="float32", always_2d=False)
        except Exception:
            try:
                self._sink.play_file(path)
            except Exception as exc:
                raise VoiceError(f"Audio playback failed: {exc}") from exc
            return
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        self._play(data, rate)

    def speak(self, text: str) -> None:
        if not text or not text.strip():
            return
        self._stop_requested = False
        self.fx.begin_utterance()
        for kind, payload, spoken in split_voice_lines(
                text, self._active_voice_lines()):
            if self._stop_requested:
                break
            if kind == "audio":
                # Hand the sink the phrase this clip stands in for, so the
                # caption covers it too. Without it the bar shows only the
                # synthesized remainder, and the reply's own text no longer
                # starts with what is on screen - which made the caption
                # clear and retype itself when speech ended.
                setattr(self._sink, "pending_text", spoken)
                self._play_audio_file(payload)
            else:
                self._speak_tts_segment(payload)

    def speak_stream(self, deltas: Iterable[str], timer=None) -> None:
        """Speak text as the model produces it, instead of after it finishes.

        `deltas` is an iterator of text fragments straight off the model's
        stream. Three stages run concurrently, so the model, the GPU and
        the speaker are never waiting on each other:

            caller thread  deltas -> buffer -> take_chunk -> chunk_q
            tts thread     chunk_q -> generate()          -> audio_q
            play thread    audio_q -> _play()             -> speakers

        The win is entirely in when the FIRST chunk starts: speak() could
        not begin until the model had emitted its last token, while this
        begins as soon as one sentence exists. Later chunks were already
        overlapped by the producer/consumer in _speak_tts_segment.

        Voice lines still fire: chunks are cut on sentence boundaries and
        every pattern in VOICE_LINES is a single sentence, so a pattern
        cannot straddle two chunks. The "Good morning, Master." pattern is
        start-anchored and the first chunk starts at the reply's start, so
        that one still matches too.
        """
        self._stop_requested = False
        self.fx.begin_utterance()
        chunk_q: "queue.Queue" = queue.Queue(maxsize=4)
        audio_q: "queue.Queue" = queue.Queue(maxsize=2)
        failure: List[BaseException] = []

        def synthesize():
            try:
                for chunk in iter(chunk_q.get, None):
                    if self._stop_requested:
                        break
                    for kind, payload, spoken in split_voice_lines(
                            chunk, self._active_voice_lines()):
                        if self._stop_requested:
                            break
                        # An audio segment is passed through rather than
                        # synthesized - _play_audio_file handles decoding
                        # and the FX chain on the playback thread. Its
                        # payload is a FILE PATH, so the caption gets
                        # `spoken` (the phrase the clip says) instead;
                        # captioning the payload would print a path.
                        audio_q.put((kind, payload, spoken) if kind == "audio"
                                    else ("samples", self.generate(payload), spoken))
            except BaseException as exc:
                failure.append(exc)
            finally:
                audio_q.put(None)

        def playback():
            try:
                for kind, payload, spoken_text in iter(audio_q.get, None):
                    if self._stop_requested:
                        break
                    # Hand the sink the words about to be heard, so the
                    # caption can appear in step with the audio rather
                    # than ahead of it.
                    setattr(self._sink, "pending_text", spoken_text)
                    # Marked here, not when synthesis finished: this is the
                    # instant sound actually reaches the sink, which is
                    # what the user experiences as "it started talking".
                    if timer is not None:
                        timer.first_audio()
                    if kind == "audio":
                        self._play_audio_file(payload)
                    else:
                        samples, rate = payload
                        self._play(samples, rate)
            except BaseException as exc:
                failure.append(exc)

        tts = threading.Thread(target=synthesize, daemon=True)
        player = threading.Thread(target=playback, daemon=True)
        tts.start()
        player.start()

        buffer = ""
        is_first = True
        try:
            for delta in deltas:
                if self._stop_requested or failure:
                    break
                buffer += delta
                while True:
                    chunk, buffer = take_chunk(buffer, is_first)
                    if chunk is None:
                        break
                    if timer is not None:
                        timer.first_chunk()
                        timer.count_chunk()
                    chunk_q.put(chunk)
                    is_first = False
            if not self._stop_requested and not failure:
                # Flush: `final` lets a trailing sentence end count, and
                # any remainder below the threshold still gets spoken
                # rather than silently dropped.
                while True:
                    chunk, buffer = take_chunk(buffer, is_first, final=True)
                    if chunk is None:
                        break
                    if timer is not None:
                        timer.first_chunk()
                        timer.count_chunk()
                    chunk_q.put(chunk)
                    is_first = False
        finally:
            chunk_q.put(None)
            tts.join(timeout=120)
            player.join(timeout=120)
        if failure:
            raise VoiceError(f"Streaming speech failed: {failure[0]}") from failure[0]

    def stop(self) -> None:
        self._stop_requested = True
        try:
            self._sink.stop()
        except Exception:
            pass  # best-effort


# Silence appended to every synthesized clip, in seconds.
#
# F5 allocates just enough duration for the text and stops essentially on
# the final phoneme - measured at 11 ms of trailing silence on a typical
# reply, which is nothing. The last word is therefore cut off before it
# has finished decaying, which is heard as the voice being clipped right
# at the end of a sentence.
#
# The padding also gives the effect chain somewhere to ring out. Reverb
# and delay tails are generated INTO this buffer, so without it they were
# being truncated at the same edge.
#
# Costs nothing perceptible: it lands after the speech, so it delays
# nothing, and playback simply ends a fraction later.
TAIL_PAD_SECONDS = 0.25


def _pad_tail(samples, sample_rate: int):
    """Append TAIL_PAD_SECONDS of silence to a synthesized clip."""
    try:
        import numpy as np

        pad = int(TAIL_PAD_SECONDS * sample_rate)
        if pad <= 0:
            return samples
        arr = np.asarray(samples)
        if arr.ndim == 1:
            return np.concatenate([arr, np.zeros(pad, dtype=arr.dtype)])
        tail = np.zeros((pad,) + arr.shape[1:], dtype=arr.dtype)
        return np.concatenate([arr, tail], axis=0)
    except Exception:
        # Padding is a polish step; never let it cost the reply.
        return samples


def _load_reference_text(reference_audio_path: str) -> str:
    """Transcript of the reference clip, from a "<name>.txt" sidecar.

    Returns "" when there isn't one, which is meaningful rather than an
    error: F5 transcribes the reference itself in that case. A sidecar is
    still preferred - it's faster (no transcription at startup) and more
    accurate than whisper guessing.
    """
    sidecar = os.path.splitext(reference_audio_path)[0] + ".txt"
    if os.path.isfile(sidecar):
        with open(sidecar, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    return ""
