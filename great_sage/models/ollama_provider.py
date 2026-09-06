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
        # Deliberately above Ollama's 4096 default; see _needed_ctx. The
        # KV cache grows with this, so it is raised where it is needed
        # rather than pinned high for every request.
        # How long Ollama keeps the model in VRAM after a reply.
        # Ollama's own default is 5 minutes. GAMING and SLEEP set this to
        # "0", which unloads it the moment the answer is finished and
        # hands ~4GB straight back to whatever Krazaa is actually doing.
        self.keep_alive = None
        self.base_num_ctx = 8192
        self.image_num_ctx = 16384

    def _payload(self, messages: List[Message], stream: bool) -> dict:
        body = {"model": self.model, "messages": messages, "stream": stream}
        if self.think is not None:
            body["think"] = self.think
        body["options"] = {"num_ctx": self._needed_ctx(messages)}
        if self.keep_alive is not None:
            body["keep_alive"] = self.keep_alive
        return body

    def _needed_ctx(self, messages) -> int:
        """How much context this request actually needs.

        Ollama defaults to 4096, which this app overruns easily and
        silently: the persona prompt, the recalled memory, the tool
        schema and the conversation already fill most of it, and ONE
        image pushes it over. The failure is an HTTP 400 that says
        nothing unless the body is read - it surfaced as "Ollama returned
        an error" and looked like the screen tool was broken.
        """
        if any(m.get("images") for m in messages if isinstance(m, dict)):
            # An image is worth on the order of a thousand tokens, and
            # look_at_screen sends one on top of a full conversation.
            return self.image_num_ctx
        return self.base_num_ctx

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
        # Include what Ollama actually said. Without this a 400 reads as
        # "Ollama returned an error (HTTP 400)" and nothing else, which is
        # a dead end - the cause is always in the body, and this is a
        # LOCAL server, so there is nothing sensitive in it to leak.
        detail = ""
        try:
            if exc.response is not None:
                payload = exc.response.json()
                detail = str(payload.get("error") or payload)[:300]
        except Exception:
            try:
                detail = (exc.response.text or "")[:300]
            except Exception:
                detail = ""
        return ("Ollama returned an error (HTTP %s)%s"
                % (status, ": " + detail if detail else "."))

    def unload(self) -> bool:
        """Drop the model from VRAM now, without waiting for a timeout.

        A zero-token request with keep_alive 0 is Ollama's documented way
        to do this; there is no explicit unload endpoint.
        """
        try:
            requests.post(f"{self.host}/api/generate", timeout=30,
                          json={"model": self.model, "keep_alive": 0})
            return True
        except Exception:
            log.warning("Could not unload %s from VRAM", self.model)
            return False

    def chat_raw(self, messages, tools=None):
        """One /api/chat round trip, returning Ollama's whole `message`.

        send_message() returns only the text, which is enough for
        conversation but throws away tool_calls - the field the tool layer
        exists to read. This returns the message dict untouched so the
        caller can see both.
        """
        body = self._payload(messages, stream=False)
        if tools:
            body["tools"] = tools
        try:
            response = requests.post(f"{self.host}/api/chat", json=body,
                                     timeout=self.timeout)
            response.raise_for_status()
        except requests.exceptions.ConnectionError as exc:
            raise ModelProviderError(
                f"Could not connect to Ollama at {self.host}. "
                "Is Ollama running? (try `ollama serve`)"
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise ModelProviderError(
                f"Ollama did not respond within {self.timeout}s.") from exc
        except requests.exceptions.HTTPError as exc:
            raise ModelProviderError(self._describe_http_error(exc)) from exc
        try:
            return response.json()["message"]
        except (ValueError, KeyError) as exc:
            raise ModelProviderError(
                "Ollama returned an unexpected response format.") from exc
