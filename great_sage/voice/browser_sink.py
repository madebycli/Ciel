"""
Streams synthesized/pre-recorded audio to a connected HUD browser client
instead of playing it through local speakers, so the HUD's own Web Audio
analyser (already tuned for real audio-reactive pulsing in
hud_prototype.html) drives the visuals from genuine playback - Python
doesn't need to separately compute or forward amplitude data.

speak() on the voice engine is documented to block until playback
finishes, so play()/play_file() here send the audio over the websocket
and then block on an ack ("audio_ended") that the HUD sends once its
<audio> element actually finishes playing - see great_sage/server.py's
protocol docstring for the full message shapes.
"""

import asyncio
import base64
import json
import threading

from great_sage.voice.base import VoiceError
from great_sage.voice.sinks import AudioSink

# How long to wait for the client's "finished playing" ack BEYOND the
# clip's own length. This is a dead-client detector - a browser that
# closed, crashed, or never decoded the audio - so it only has to exceed
# normal network and decode jitter, not the audio itself.
#
# It used to be a flat 30s wall-clock cap, which silently doubled as a
# maximum spoken length: with VOICE_SINGLE_SHOT the whole reply is ONE
# clip, so any answer longer than 30s of speech timed out and was lost.
# That went unnoticed while the model's median reply was ~110 chars and
# became reachable as soon as a model that writes longer lists was used.
ACK_TIMEOUT_MARGIN_SECONDS = 30.0

# Floor for clips whose duration cannot be determined.
ACK_TIMEOUT_SECONDS = 30.0


class BrowserAudioSink(AudioSink):
    def __init__(self, websocket, loop: asyncio.AbstractEventLoop):
        self._websocket = websocket
        self._loop = loop
        self._ack_event = threading.Event()
        # Text belonging to the clip about to be sent. The HUD shows it
        # at the moment that clip starts playing, so the caption tracks
        # the voice instead of racing ahead of it. Set by the engine
        # immediately before each play(); cleared once consumed so a
        # stale line can never ride along with the next clip.
        self.pending_text = None

    def notify_audio_ended(self) -> None:
        """Called by the server's receive loop when the client reports its
        <audio> element finished - unblocks the matching play()/play_file()."""
        self._ack_event.set()

    def _send_and_wait(self, data: bytes, mime: str,
                       duration: float = None) -> None:
        """Send one clip and block until the client says it finished.

        `duration` is the clip's length in seconds when the caller knows
        it, so the ack deadline can scale with the audio instead of
        capping it. Without it the flat floor applies.
        """
        text = self.pending_text
        self.pending_text = None
        payload = json.dumps({
            "type": "audio",
            "mime": mime,
            "data": base64.b64encode(data).decode("ascii"),
            "text": text or "",
        })
        self._ack_event.clear()
        future = asyncio.run_coroutine_threadsafe(self._websocket.send(payload), self._loop)
        future.result()
        # The clip has to finish PLAYING before the ack can arrive, so the
        # deadline is its length plus room for jitter - never less than the
        # floor, so a very short clip keeps a sane grace period.
        deadline = ACK_TIMEOUT_SECONDS
        if duration and duration > 0:
            deadline = max(deadline, duration + ACK_TIMEOUT_MARGIN_SECONDS)
        if not self._ack_event.wait(timeout=deadline):
            raise VoiceError(
                "Timed out waiting for the HUD to finish playing audio "
                "(waited %.0fs for a %s clip)." % (
                    deadline,
                    "%.1fs" % duration if duration else "clip of unknown length"))

    def play(self, samples, samplerate: int) -> None:
        import io

        import soundfile as sf

        buf = io.BytesIO()
        sf.write(buf, samples, samplerate, format="WAV")
        duration = None
        try:
            duration = len(samples) / float(samplerate)
        except Exception:
            pass          # a length we cannot compute just falls back to the floor
        self._send_and_wait(buf.getvalue(), "audio/wav", duration)

    def play_file(self, path: str) -> None:
        with open(path, "rb") as f:
            data = f.read()
        mime = "audio/ogg" if path.lower().endswith(".ogg") else "audio/wav"
        duration = None
        try:
            import soundfile as sf
            duration = sf.info(path).duration
        except Exception:
            pass          # header we cannot read just falls back to the floor
        self._send_and_wait(data, mime, duration)

    def stop(self) -> None:
        self._ack_event.set()  # unblock any pending wait so speak() can return
