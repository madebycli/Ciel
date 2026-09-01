"""
Abstract interface that every AI model provider must implement.

This is the seam that keeps Great Sage from being hard-coded to any one
AI backend. core/chat_engine.py only ever talks to this interface, never
to a specific provider's SDK or HTTP API. To add a new provider (OpenAI,
Anthropic, a different local runtime, etc.) later, create a new class in
models/ that implements ModelProvider and register it in main.py - no
other part of the application needs to change.
"""

from abc import ABC, abstractmethod
from typing import Iterator, List, TypedDict


class Message(TypedDict):
    """A single conversation turn, in the shape most chat APIs expect."""
    role: str  # "system" | "user" | "assistant"
    content: str


class ModelProviderError(Exception):
    """Raised when a provider cannot fulfill a request (e.g. unreachable)."""


class ModelProvider(ABC):
    """Common interface for any AI backend Great Sage can talk to."""

    @abstractmethod
    def send_message(self, messages: List[Message]) -> str:
        """Send full conversation history, return the complete response text.

        Blocking call. Raises ModelProviderError on failure (connection
        issues, bad model name, etc.) so callers can handle it cleanly
        instead of crashing.
        """
        raise NotImplementedError

    @abstractmethod
    def stream_response(self, messages: List[Message]) -> Iterator[str]:
        """Send full conversation history, yield response text incrementally.

        Yields chunks of text as they arrive. Raises ModelProviderError on
        failure.
        """
        raise NotImplementedError

    @abstractmethod
    def get_available_models(self) -> List[str]:
        """Return the names of models this provider currently has available.

        Raises ModelProviderError if the provider can't be reached.
        """
        raise NotImplementedError
