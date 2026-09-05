"""
Ollama implementation of ModelProvider.

Talks to a locally running Ollama server over its REST API. This is the
only concrete provider in the first prototype; nothing outside this file
knows or cares that it's Ollama specifically.
"""

import json
from typing import Iterator, List

import requests

from great_sage.models.base import Message, ModelProvider, ModelProviderError


class OllamaProvider(ModelProvider):
    def __init__(self, host: str, model: str, timeout: int = 60,
                 think: bool = False):
        """think=False disables a reasoning model's private deliberation.

        This matters enormously for a spoken assistant. Ollama reports a
        reasoning model's thinking in a SEPARATE "thinking" field, not as
        content - so with it on, nothing arrives on the content stream
        until the model has finished deliberating. Measured on qwen3:8b
        with an identical question:

            thinking on  -> first content at 14.13s (857 chars of it)
            thinking off -> first content at  1.15s

        Same model, same prompt, 12x difference in how long the user
        waits before anything can be spoken. Off is the right default for
        conversation; turn it on deliberately for a hard problem where
        the extra time buys something.

        Harmless on models without a thinking mode - Ollama ignores the
        field, so llama3 and friends behave exactly as before.
        """
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.think = think

    def _payload(self, messages: List[Message], stream: bool) -> dict:
        body = {"model": self.model, "messages": messages, "stream": stream}
        if self.think is not None:
            body["think"] = self.think
        return body

    def send_message(self, messages: List[Message]) -> str:
        try:
            response = requests.post(
                f"{self.host}/api/chat",
                json=self._payload(messages, stream=False),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.exceptions.ConnectionError as exc:
            raise ModelProviderError(
                f"Could not connect to Ollama at {self.host}. "
                "Is Ollama running? (try `ollama serve`)"
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise ModelProviderError(
                f"Ollama did not respond within {self.timeout}s."
            ) from exc
        except requests.exceptions.HTTPError as exc:
            raise ModelProviderError(self._describe_http_error(exc)) from exc

        try:
            data = response.json()
            return data["message"]["content"]
        except (ValueError, KeyError) as exc:
            raise ModelProviderError(
                "Ollama returned an unexpected response format."
            ) from exc

    def stream_response(self, messages: List[Message]) -> Iterator[str]:
        try:
            response = requests.post(
                f"{self.host}/api/chat",
                json=self._payload(messages, stream=True),
                timeout=self.timeout,
                stream=True,
            )
            response.raise_for_status()
        except requests.exceptions.ConnectionError as exc:
            raise ModelProviderError(
                f"Could not connect to Ollama at {self.host}. "
                "Is Ollama running? (try `ollama serve`)"
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise ModelProviderError(
                f"Ollama did not respond within {self.timeout}s."
            ) from exc
        except requests.exceptions.HTTPError as exc:
            raise ModelProviderError(self._describe_http_error(exc)) from exc

        try:
            for line in response.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get("done"):
                    break
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    yield piece
        except requests.exceptions.ConnectionError as exc:
            raise ModelProviderError(
                "Lost connection to Ollama mid-response."
            ) from exc
        except (ValueError, KeyError) as exc:
            raise ModelProviderError(
                "Ollama returned an unexpected response format."
            ) from exc

    def get_available_models(self) -> List[str]:
        try:
            response = requests.get(f"{self.host}/api/tags", timeout=self.timeout)
            response.raise_for_status()
        except requests.exceptions.ConnectionError as exc:
            raise ModelProviderError(
                f"Could not connect to Ollama at {self.host}. "
                "Is Ollama running? (try `ollama serve`)"
            ) from exc
        except requests.exceptions.HTTPError as exc:
            raise ModelProviderError(self._describe_http_error(exc)) from exc

        try:
            data = response.json()
            return [m["name"] for m in data.get("models", [])]
        except (ValueError, KeyError) as exc:
            raise ModelProviderError(
                "Ollama returned an unexpected response format."
            ) from exc

    @staticmethod
    def _describe_http_error(exc: requests.exceptions.HTTPError) -> str:
        status = exc.response.status_code if exc.response is not None else "?"
        if status == 404:
            return (
                "Ollama returned 404 - the model may not be pulled yet. "
                "Try `ollama pull <model-name>`."
            )
        return f"Ollama returned an error (HTTP {status})."
