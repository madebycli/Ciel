"""Anthropic's API behind the same ModelProvider seam as Ollama.

Why it exists: Krazaa wanted a way to take the load off this machine.
With a key set, generation happens on Anthropic's hardware and the 3060
is left to F5-TTS and the HUD's WebGL - the two things that actually have
to be local.

Great Sage's identity does not change with the provider (spec S6). The
system prompt, memory, tools and persona are all identical; only where
the tokens come from differs.

Two shape differences from Ollama, both handled here so nothing above
this file has to care:

  - Anthropic takes the system prompt as a separate top-level field, not
    as a message with role "system". Leaving system turns in the list is
    a 400.
  - It has no "tool result" role of Ollama's kind, so a tool turn is
    folded into a user message. Tool calling itself is NOT implemented
    for this provider yet - see supports_tools().
"""

import json
import logging
from typing import Iterator, List

import requests

from great_sage.models.base import Message, ModelProvider, ModelProviderError

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 1024


class AnthropicProvider(ModelProvider):
    def __init__(self, api_key: str, model: str = "", timeout: int = 90,
                 max_tokens: int = DEFAULT_MAX_TOKENS):
        if not api_key:
            raise ModelProviderError("No Anthropic API key is set.")
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout
        self.max_tokens = max_tokens

    @staticmethod
    def supports_tools() -> bool:
        """Tool calling is Ollama-only for now.

        Said explicitly rather than left implicit: the tool layer asks
        before attaching a schema, so with this provider selected Great
        Sage answers from the model alone and does not pretend to have
        run anything (spec S16).
        """
        return False

    def _split(self, messages: List[Message]):
        """(system_text, conversation) in Anthropic's shape."""
        system_parts, convo = [], []
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            if role == "system":
                system_parts.append(content)
            elif role == "tool":
                # No tool role here; fold the result in as context.
                convo.append({"role": "user",
                              "content": "[tool result] " + str(content)})
            else:
                convo.append({"role": "assistant" if role == "assistant"
                              else "user", "content": content})
        # The API rejects an empty conversation, and rejects two turns of
        # the same role in a row.
        merged = []
        for turn in convo:
            if merged and merged[-1]["role"] == turn["role"]:
                merged[-1]["content"] += "\n\n" + turn["content"]
            else:
                merged.append(dict(turn))
        if not merged:
            merged = [{"role": "user", "content": "..."}]
        return "\n\n".join(system_parts), merged

    def _headers(self):
        return {"x-api-key": self.api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json"}

    def _body(self, messages, stream):
        system, convo = self._split(messages)
        body = {"model": self.model, "max_tokens": self.max_tokens,
                "messages": convo, "stream": stream}
        if system:
            body["system"] = system
        return body

    def _fail(self, exc, response=None):
        """Never let a key reach a log line or an error message."""
        if response is not None and response.status_code == 401:
            return ModelProviderError(
                "Anthropic rejected the API key. Check it in AI settings.")
        if response is not None and response.status_code == 429:
            return ModelProviderError(
                "Anthropic rate limit reached. Try again shortly.")
        if response is not None:
            return ModelProviderError(
                "Anthropic returned HTTP %s." % response.status_code)
        return ModelProviderError("Could not reach Anthropic: %s"
                                  % type(exc).__name__)

    def send_message(self, messages: List[Message]) -> str:
        try:
            r = requests.post(API_URL, headers=self._headers(),
                              json=self._body(messages, False),
                              timeout=self.timeout)
        except Exception as exc:
            raise self._fail(exc) from exc
        if r.status_code != 200:
            raise self._fail(None, r)
        try:
            blocks = r.json().get("content") or []
            return "".join(b.get("text", "") for b in blocks
                           if b.get("type") == "text")
        except Exception as exc:
            raise ModelProviderError(
                "Anthropic returned an unexpected response format.") from exc

    def stream_response(self, messages: List[Message]) -> Iterator[str]:
        try:
            r = requests.post(API_URL, headers=self._headers(),
                              json=self._body(messages, True),
                              timeout=self.timeout, stream=True)
        except Exception as exc:
            raise self._fail(exc) from exc
        if r.status_code != 200:
            raise self._fail(None, r)
        for line in r.iter_lines():
            if not line or not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload in (b"", b"[DONE]"):
                continue
            try:
                event = json.loads(payload)
            except ValueError:
                continue
            if event.get("type") == "content_block_delta":
                text = (event.get("delta") or {}).get("text")
                if text:
                    yield text

    def get_available_models(self) -> List[str]:
        # Deliberately a fixed list rather than a live call: there is no
        # cheap models endpoint, and this is only used to populate a
        # picker. Wrong names fail loudly at first use rather than
        # silently.
        return ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"]
