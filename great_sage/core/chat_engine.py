"""
Chat engine: owns the conversation and drives whichever ModelProvider
it's given. Contains no provider-specific code, so it works unmodified
no matter which AI backend is plugged in.
"""

from typing import Iterator, List

from great_sage.models.base import Message, ModelProvider, ModelProviderError


class ChatEngine:
    def __init__(self, provider: ModelProvider, system_prompt: str):
        self.provider = provider
        self.history: List[Message] = [{"role": "system", "content": system_prompt}]

    def send(self, user_input: str) -> str:
        """Send a message, get the full reply back, and record both in history."""
        self.history.append({"role": "user", "content": user_input})
        try:
            reply = self.provider.send_message(self.history)
        except ModelProviderError:
            # Don't leave an unanswered user turn in history on failure.
            self.history.pop()
            raise
        self.history.append({"role": "assistant", "content": reply})
        return reply

    def send_streaming(self, user_input: str) -> Iterator[str]:
        """Send a message, yield the reply incrementally, then record it."""
        self.history.append({"role": "user", "content": user_input})
        collected = []
        try:
            for chunk in self.provider.stream_response(self.history):
                collected.append(chunk)
                yield chunk
        except ModelProviderError:
            self.history.pop()
            raise
        self.history.append({"role": "assistant", "content": "".join(collected)})

    def reset(self) -> None:
        """Clear conversation history back to just the system prompt."""
        system = self.history[0]
        self.history = [system]
