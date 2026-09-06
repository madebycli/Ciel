r"""OpenAI's chat API behind the same ModelProvider seam.

The settings pane offered "openai" as a provider before this existed, and
picking it silently fell back to the local model. A menu entry that
quietly does nothing is worse than a shorter menu - so either this file
had to exist or the option had to go.

Shape differences from Ollama, absorbed here:

  - the system prompt is an ordinary message with role "system", which is
    the same as Ollama, so no splitting is needed (unlike Anthropic)
  - images travel as content PARTS with a data: URL, not as a top-level
    "images" list
  - tool results use role "tool" with a tool_call_id that must match the
    call, so the id is carried through rather than invented
"""

import json
import logging
from typing import Iterator, List

import requests

from great_sage.models.base import Message, ModelProvider, ModelProviderError

log = logging.getLogger(__name__)

API_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIProvider(ModelProvider):
    def __init__(self, api_key: str, model: str = "", timeout: int = 90):
        if not api_key:
            raise ModelProviderError("No OpenAI API key is set.")
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout

    @staticmethod
    def supports_tools() -> bool:
        return True

    def _headers(self):
        return {"Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json"}

    def _convert(self, messages: List[Message]):
        out = []
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            images = m.get("images")
            if role == "tool":
                out.append({"role": "tool",
                            "tool_call_id": m.get("tool_call_id") or "call_1",
                            "content": str(content)})
                continue
            if images:
                parts = [{"type": "text", "text": content or ""}]
                for img in images:
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + img}})
                out.append({"role": role or "user", "content": parts})
                continue
            entry = {"role": role or "user", "content": content}
            if m.get("tool_calls"):
                entry["tool_calls"] = m["tool_calls"]
            out.append(entry)
        return out

    def _fail(self, exc, response=None):
        """Never let the key reach a log line or an error message."""
        if response is not None:
            if response.status_code == 401:
                return ModelProviderError(
                    "OpenAI rejected the API key. Check it in AI settings.")
            if response.status_code == 429:
                return ModelProviderError(
                    "OpenAI rate limit reached. Try again shortly.")
            return ModelProviderError(
                "OpenAI returned HTTP %s." % response.status_code)
        return ModelProviderError("Could not reach OpenAI: %s"
                                  % type(exc).__name__)

    def _post(self, body, stream=False):
        try:
            r = requests.post(API_URL, headers=self._headers(), json=body,
                              timeout=self.timeout, stream=stream)
        except Exception as exc:
            raise self._fail(exc) from exc
        if r.status_code != 200:
            raise self._fail(None, r)
        return r

    def send_message(self, messages: List[Message]) -> str:
        r = self._post({"model": self.model,
                        "messages": self._convert(messages)})
        try:
            return r.json()["choices"][0]["message"].get("content") or ""
        except Exception as exc:
            raise ModelProviderError(
                "OpenAI returned an unexpected response format.") from exc

    def chat_raw(self, messages, tools=None):
        """Ollama-shaped message dict, so the tool loop needs no changes."""
        body = {"model": self.model, "messages": self._convert(messages)}
        if tools:
            body["tools"] = tools
        r = self._post(body)
        try:
            msg = r.json()["choices"][0]["message"]
        except Exception as exc:
            raise ModelProviderError(
                "OpenAI returned an unexpected response format.") from exc
        out = {"role": "assistant", "content": msg.get("content") or ""}
        calls = msg.get("tool_calls") or []
        if calls:
            # Translated into the shape ChatEngine already reads, so one
            # tool loop serves every provider.
            out["tool_calls"] = [{
                "id": c.get("id"),
                "function": {
                    "name": (c.get("function") or {}).get("name"),
                    "arguments": _parse_args(
                        (c.get("function") or {}).get("arguments")),
                }} for c in calls]
        return out

    def stream_response(self, messages: List[Message]) -> Iterator[str]:
        r = self._post({"model": self.model,
                        "messages": self._convert(messages), "stream": True},
                       stream=True)
        for line in r.iter_lines():
            if not line or not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload in (b"", b"[DONE]"):
                continue
            try:
                delta = json.loads(payload)["choices"][0].get("delta") or {}
            except Exception:
                continue
            text = delta.get("content")
            if text:
                yield text

    def get_available_models(self) -> List[str]:
        return ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"]


def _parse_args(raw):
    """OpenAI sends arguments as a JSON STRING; Ollama sends a dict."""
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}
