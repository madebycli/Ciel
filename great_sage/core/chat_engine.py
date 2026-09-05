"""
Chat engine: owns the conversation and drives whichever ModelProvider
it's given. Contains no provider-specific code, so it works unmodified
no matter which AI backend is plugged in.
"""

import time
from typing import Callable, Iterator, List, Optional

from great_sage.models.base import Message, ModelProvider, ModelProviderError

# Prepended (only on the outgoing copy sent to the model, never stored in
# history) to mark a genuinely new interaction, per SYSTEM_PROMPT's
# instruction to only open with "Notice." when this is present.
SESSION_START_MARKER = "[SESSION START] "


class ChatEngine:
    def __init__(
        self,
        provider: ModelProvider,
        system_prompt: str,
        idle_reset_seconds: float = 900.0,
        on_session_boundary: Optional[Callable[[List[Message]], None]] = None,
        recall: Optional[Callable[[str], str]] = None,
    ):
        """on_session_boundary, if given, is called with the messages from
        a just-finished conversation (never including the system prompt)
        whenever a genuinely new session starts - after an idle gap, or
        on an explicit reset() - but not on the very first message ever,
        since there's nothing finished yet. See great_sage/core/memory.py
        for the intended use: extracting durable facts worth remembering
        long-term, decoupled from the moment-to-moment chat history here.
        """
        self.provider = provider
        self.history: List[Message] = [{"role": "system", "content": system_prompt}]
        self._idle_reset_seconds = idle_reset_seconds
        self._last_activity: Optional[float] = None
        self._on_session_boundary = on_session_boundary
        # recall(user_text) -> a short block of remembered facts relevant
        # to THIS turn, or "". Injected into the outgoing request only,
        # never stored in history, for two reasons: irrelevant facts
        # dilute the ones that matter, and a fact learned mid-session
        # applies on the very next message instead of after a restart.
        # (Not for speed - prompt size turned out NOT to drive time-to-first-token here: measured on this machine, TTFT held at ~1.06-1.16s from a 1.8K prompt all the way to 270K chars. The ~1.1s is a fixed floor from Ollama plus the model, not prompt-eval.)
        self._recall = recall
        self._boundary_start_index = 1  # skip the system message at index 0

    def _handle_session_boundary(self) -> None:
        if self._on_session_boundary is None:
            return
        finished = self.history[self._boundary_start_index :]
        if finished:
            self._on_session_boundary(finished)
        self._boundary_start_index = len(self.history)

    def _build_outgoing(self, user_input: str) -> List[Message]:
        """Append user_input to history; return the messages to send.

        A turn counts as a session start - and gets tagged with
        SESSION_START_MARKER on the outgoing copy only - when it's the
        very first message, or one arriving after at least
        idle_reset_seconds of silence. self.history keeps the original,
        unmarked user text either way, so the visible transcript and
        reset() are unaffected by this.
        """
        now = time.monotonic()
        is_fresh = (
            self._last_activity is None
            or (now - self._last_activity) >= self._idle_reset_seconds
        )
        if is_fresh and self._last_activity is not None:
            # A real idle gap, not just the first-ever message - the
            # previous session just ended.
            self._handle_session_boundary()
        self._last_activity = now
        self.history.append({"role": "user", "content": user_input})

        outgoing = list(self.history)
        if is_fresh:
            outgoing[-1] = {
                "role": "user",
                "content": SESSION_START_MARKER + user_input,
            }

        # Memory goes in immediately before the user's turn, so it reads
        # as context for this specific question rather than as part of the
        # standing persona. Deliberately not appended to self.history:
        # keeping it out means the visible transcript stays clean and the
        # facts are re-selected fresh every turn instead of accumulating.
        if self._recall is not None:
            try:
                block = self._recall(user_input)
            except Exception:
                block = ""   # memory is a nice-to-have, never a blocker
            if block:
                outgoing.insert(len(outgoing) - 1,
                                {"role": "system", "content": block})
        return outgoing

    def send(self, user_input: str) -> str:
        """Send a message, get the full reply back, and record both in history."""
        outgoing = self._build_outgoing(user_input)
        try:
            reply = self.provider.send_message(outgoing)
        except ModelProviderError:
            # Don't leave an unanswered user turn in history on failure.
            self.history.pop()
            raise
        self.history.append({"role": "assistant", "content": reply})
        return reply

    def send_streaming(self, user_input: str) -> Iterator[str]:
        """Send a message, yield the reply incrementally, then record it."""
        outgoing = self._build_outgoing(user_input)
        collected = []
        try:
            for chunk in self.provider.stream_response(outgoing):
                collected.append(chunk)
                yield chunk
        except ModelProviderError:
            self.history.pop()
            raise
        self.history.append({"role": "assistant", "content": "".join(collected)})

    def replace_last_reply(self, text: str) -> bool:
        """Overwrite the last assistant turn in history. True if it applied.

        For a reply the guardrails substituted. Without this the history
        would keep the ORIGINAL - so a jailbreak that got one bad answer
        past the model would sit in context for every later turn,
        demonstrating to the model that it already broke character once and
        may as well continue. Rewriting it means the conversation the model
        sees only ever contains replies that passed the check.
        """
        for i in range(len(self.history) - 1, 0, -1):
            if self.history[i].get("role") == "assistant":
                self.history[i] = {"role": "assistant", "content": text}
                return True
        return False

    def reset(self) -> None:
        """Clear conversation history back to just the system prompt."""
        self._handle_session_boundary()  # flush what's finished before it's gone
        system = self.history[0]
        self.history = [system]
        self._last_activity = None
        self._boundary_start_index = 1
