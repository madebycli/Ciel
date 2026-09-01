"""
Minimal text chat interface for Great Sage.

Deliberately simple: reads a line, streams a reply, optionally speaks it,
repeats. This is the prototype's UI. It talks to ChatEngine and (now)
an optional VoiceOutput - never to a model provider or TTS engine
directly beyond those interfaces.
"""

from typing import Optional

from great_sage.core.chat_engine import ChatEngine
from great_sage.models.base import ModelProviderError
from great_sage.voice.base import VoiceError, VoiceOutput

EXIT_COMMANDS = {"exit", "quit", ":q"}


def run_cli(engine: ChatEngine, model_name: str, voice: Optional[VoiceOutput] = None) -> None:
    print("=" * 60)
    print(f"  Great Sage (prototype) - model: {model_name}")
    voice_status = "on" if voice else "off"
    print(f"  Voice: {voice_status}. Type 'exit' to quit, 'reset' to clear history.")
    if voice:
        print("  Type 'voice off' to stop speaking for the rest of this session.")
    print("=" * 60)

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not user_input:
            continue
        if user_input.lower() in EXIT_COMMANDS:
            print("Goodbye.")
            break
        if user_input.lower() == "reset":
            engine.reset()
            print("(conversation history cleared)")
            continue
        if user_input.lower() == "voice off":
            voice = None
            print("(voice output disabled for this session)")
            continue

        print("Great Sage: ", end="", flush=True)
        reply_chunks = []
        try:
            for chunk in engine.send_streaming(user_input):
                print(chunk, end="", flush=True)
                reply_chunks.append(chunk)
            print()
        except ModelProviderError as exc:
            print(f"\n[Error] {exc}")
            continue

        if voice:
            try:
                voice.speak("".join(reply_chunks))
            except VoiceError as exc:
                print(f"[Voice error] {exc}")
