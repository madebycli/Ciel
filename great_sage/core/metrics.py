"""
Per-reply latency instrumentation.

One ResponseTimer is created per user turn and logs a single summary
line when the turn ends, rather than scattering timing logs through the
request path. The number that actually matters is time_to_speech: how
long the user waits, from their request landing to hearing the first
sound. Everything else is there to explain that number when it regresses.

Deliberately dependency-free and cheap (a few time.time() calls), so it
can stay on in normal use instead of being a debug-only mode.
"""

import logging
import time
from typing import Optional

log = logging.getLogger(__name__)


class ResponseTimer:
    """Marks the stages of one request->speech round trip.

    Every mark is optional: a text-only reply never calls first_audio(),
    a voice-line-only reply never calls first_token(). Whatever was
    marked is what gets reported.
    """

    def __init__(self, label: str = "") -> None:
        self.label = label
        self._start = time.time()
        self._first_token: Optional[float] = None
        self._first_chunk: Optional[float] = None
        self._first_audio: Optional[float] = None
        self._text_done: Optional[float] = None
        self._end: Optional[float] = None
        self._chunks = 0

    def first_token(self) -> None:
        """The model's first text delta - i.e. it has started thinking out loud."""
        if self._first_token is None:
            self._first_token = time.time()

    def first_chunk(self) -> None:
        """The first speakable chunk exists; synthesis can begin."""
        if self._first_chunk is None:
            self._first_chunk = time.time()

    def first_audio(self) -> None:
        """Audio is being handed to the sink - the user is about to hear it."""
        if self._first_audio is None:
            self._first_audio = time.time()

    def text_done(self) -> None:
        """The model finished generating."""
        if self._text_done is None:
            self._text_done = time.time()

    def count_chunk(self) -> None:
        self._chunks += 1

    def _since(self, mark: Optional[float]) -> Optional[float]:
        return None if mark is None else mark - self._start

    def finish(self) -> None:
        self._end = time.time()
        parts = []
        for name, mark in (
            ("first_token", self._first_token),
            ("first_chunk", self._first_chunk),
            ("SPEECH_START", self._first_audio),
            ("text_done", self._text_done),
            ("total", self._end),
        ):
            elapsed = self._since(mark)
            if elapsed is not None:
                parts.append(f"{name}={elapsed:.2f}s")
        if self._chunks:
            parts.append(f"chunks={self._chunks}")
        log.info("timing %s | %s", self.label or "reply", "  ".join(parts))

    @property
    def time_to_speech(self) -> Optional[float]:
        """The headline number: request in, first sound out."""
        return self._since(self._first_audio)
