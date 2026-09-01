"""
Great Sage - entry point.

This is the only file that knows how to translate config into a concrete
ModelProvider. Every other module works against the abstract ModelProvider
interface. To add a new provider later, add a branch here that builds it
(and set config.settings.ACTIVE_PROVIDER accordingly) - core/, ui/, and
models/base.py don't need to change.
"""

import re
import sys

from great_sage.config import settings
from great_sage.core.chat_engine import ChatEngine
from great_sage.models.base import ModelProvider, ModelProviderError
from great_sage.models.ollama_provider import OllamaProvider
from great_sage.ui.cli import run_cli
from great_sage.voice.base import VoiceError


def build_provider():
    if settings.ACTIVE_PROVIDER == "ollama":
        return OllamaProvider(
            host=settings.OLLAMA_HOST,
            model=settings.OLLAMA_DEFAULT_MODEL,
            timeout=settings.REQUEST_TIMEOUT_SECONDS,
        )
    raise ValueError(f"Unknown provider: {settings.ACTIVE_PROVIDER}")


def build_translator(provider: ModelProvider):
    """Return a str->str callable that translates text into CLONE_LANGUAGE.

    Reuses the same ModelProvider the chat itself talks to, via a
    one-off, history-free call - so it works with whatever provider is
    configured (never hard-coded to Ollama specifically) and never
    pollutes the actual conversation history.
    """

    def translate(text: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "Translate the user's message into natural, spoken "
                    f"{settings.CLONE_LANGUAGE}. Output only the "
                    "translation - no notes, quotes, or romanization."
                ),
            },
            {"role": "user", "content": text},
        ]
        try:
            return provider.send_message(messages)
        except ModelProviderError:
            return text  # Speak the original text rather than fail silently.

    return translate


def build_voice(provider: ModelProvider):
    """Return a VoiceOutput, or None if voice is disabled/unavailable.

    Voice is treated as optional: if it's off in config, or it fails to
    initialize, Great Sage falls back to text-only rather than refusing
    to start.
    """
    if not settings.VOICE_ENABLED:
        return None

    if settings.VOICE_ENGINE == "sapi5":
        # Imported lazily so a missing pyttsx3 install doesn't break
        # text-only runs for anyone who set VOICE_ENABLED = False.
        from great_sage.voice.tts_engine import Pyttsx3VoiceOutput

        try:
            return Pyttsx3VoiceOutput(
                rate=settings.VOICE_RATE,
                volume=settings.VOICE_VOLUME,
                voice_id=settings.VOICE_ID,
            )
        except VoiceError as exc:
            print(f"[Voice unavailable] {exc}")
            print("Continuing in text-only mode.\n")
            return None

    if settings.VOICE_ENGINE == "clone":
        from great_sage.voice.cloned_voice_engine import XTTSClonedVoiceOutput

        voice_lines = [
            (re.compile(pattern, re.IGNORECASE), path)
            for pattern, path in settings.VOICE_LINES
        ]
        translator = build_translator(provider) if settings.CLONE_TRANSLATE else None
        try:
            return XTTSClonedVoiceOutput(
                reference_audio_path=settings.CLONE_REFERENCE_AUDIO_PATH,
                language=settings.CLONE_LANGUAGE,
                device=settings.CLONE_DEVICE,
                speed=settings.CLONE_SPEED,
                voice_lines=voice_lines,
                translator=translator,
            )
        except VoiceError as exc:
            print(f"[Voice unavailable] {exc}")
            print("Continuing in text-only mode.\n")
            return None

    print(f"[Voice unavailable] Unknown VOICE_ENGINE: {settings.VOICE_ENGINE!r}")
    return None


def main() -> int:
    provider = build_provider()

    # Fail fast and clearly if Ollama isn't reachable, instead of letting
    # the user discover it on their first chat message.
    try:
        available = provider.get_available_models()
    except ModelProviderError as exc:
        print(f"[Startup error] {exc}")
        return 1

    if available and settings.OLLAMA_DEFAULT_MODEL not in available:
        print(
            f"[Warning] '{settings.OLLAMA_DEFAULT_MODEL}' was not found in "
            f"Ollama's pulled models: {', '.join(available)}"
        )
        print(f"Try: ollama pull {settings.OLLAMA_DEFAULT_MODEL}\n")

    voice = build_voice(provider)
    engine = ChatEngine(provider, settings.SYSTEM_PROMPT)
    run_cli(engine, settings.OLLAMA_DEFAULT_MODEL, voice=voice)
    return 0


if __name__ == "__main__":
    sys.exit(main())
