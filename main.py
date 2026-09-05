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
import threading
from typing import Callable, List, Optional

from great_sage.config import settings
from great_sage.core import memory, memory_recall, personality, voice_line_prefs
from great_sage.core.chat_engine import ChatEngine
from great_sage.models.base import Message, ModelProvider, ModelProviderError
from great_sage.models.ollama_provider import OllamaProvider
from great_sage.ui.cli import run_cli
from great_sage.voice.base import VoiceError


def build_provider():
    if settings.ACTIVE_PROVIDER == "ollama":
        return OllamaProvider(
            host=settings.OLLAMA_HOST,
            model=settings.OLLAMA_DEFAULT_MODEL,
            timeout=settings.REQUEST_TIMEOUT_SECONDS,
            think=settings.OLLAMA_THINK,
        )
    raise ValueError(f"Unknown provider: {settings.ACTIVE_PROVIDER}")


def format_persona_phrases() -> str:
    """Renders settings.PERSONA_PHRASES as a situation -> line table.

    Kept out of SYSTEM_PROMPT's prose so the wording can be tuned by
    editing one obvious list instead of rewriting paragraphs. Appended
    AFTER the prose deliberately: it is the last word on how to decline,
    and a small local model weights the end of its prompt heavily.
    """
    phrases = getattr(settings, "PERSONA_PHRASES", None)
    if not phrases:
        return ""
    table = "\n".join(f'- When {situation}: "{line}"' for situation, line in phrases)
    return (
        "\n\nPREFERRED PHRASING. In these situations OPEN with the "
        "given line near-verbatim - it is how this skill speaks, and it "
        "overrides any habit of explaining yourself in other terms - "
        "then CONTINUE with the actual substance: the reason, the "
        "evidence, or the better course of action. The line is the "
        "opening of a useful reply, never a replacement for one. "
        "(Exception: where the line is itself the complete answer, such "
        "as a one-word fact, stopping there is correct.)\n\n"
        "Two examples of the required shape - note that the "
        "declaration opens the reply and the substance follows it:\n"
        "User: I am going to delete my whole database to fix a typo.\n"
        "You: Warning. Projected outcome is unfavourable. A typo is a "
        "single-field edit; dropping the database destroys every other "
        "record with it. Correct the field in place, and take a backup "
        "before touching it.\n"
        "User: Is the Earth flat?\n"
        "You: Notice. That premise is in error. The curvature is "
        "directly measurable - a ship's hull vanishes below the "
        "horizon before its mast does, and visible distance scales "
        "with observer height.\n\n"
        "The situation table:\n"
        + table
    )


def build_system_prompt() -> str:
    """Assembles the full system prompt, in the order the model reads it:

        base persona  ->  bearing/confidence/speech  ->  phrasing table
        ->  remembered facts

    The personality layer goes before the phrasing table because the
    table is the more concrete instruction and a small model weights what
    comes last most heavily. Facts go last of all so familiarity has
    something immediately preceding it to draw on.
    """
    facts = memory.load_memory(settings.MEMORY_FILE_PATH) if settings.MEMORY_ENABLED else []
    state = personality.current_state(
        fact_count=len(facts),
        overrides=getattr(settings, "PERSONALITY_OVERRIDES", None),
    )
    # The facts themselves are NOT appended here any more. They used to
    # be, all of them, so the entire store rode on every request and
    # buried the few that mattered. build_recall() below selects only
    # the relevant handful per turn. The count is still used, because
    # familiarity is earned from how much is actually remembered.
    # (This was NOT a latency fix - prompt size turned out NOT to drive time-to-first-token here: measured on this machine, TTFT held at ~1.06-1.16s from a 1.8K prompt all the way to 270K chars. The ~1.1s is a fixed floor from Ollama plus the model, not prompt-eval.)
    return settings.SYSTEM_PROMPT + personality.render(state) + format_persona_phrases()


def build_recall(provider: ModelProvider):
    """Returns recall(user_text) -> a block of relevant remembered facts.

    Re-reads the store each turn rather than caching it. The file is a few
    KB at most, so the read is free next to a model call, and it means a
    fact learned mid-session is available on the very next message instead
    of only after a restart.
    """
    if not settings.MEMORY_ENABLED:
        return None

    limit = getattr(settings, "MEMORY_RECALL_LIMIT", 6)

    def recall(user_text: str) -> str:
        facts = memory.load_memory(settings.MEMORY_FILE_PATH)
        if not facts:
            return ""
        picked = memory_recall.relevant_facts(facts, user_text, limit=limit)
        return memory_recall.format_recall(picked)

    return recall


def handle_memory_command(provider: ModelProvider, user_text: str) -> Optional[str]:
    """Acts on an explicit "remember this" / "forget that" instruction.

    Returns a short note describing what happened (for logging), or None
    if the message wasn't a memory command. The reply itself is still
    produced by the model, so the acknowledgement stays in character
    rather than being a canned string from here.
    """
    if not settings.MEMORY_ENABLED:
        return None

    subject = memory_recall.forget_request(user_text)
    if subject:
        facts = memory.load_memory(settings.MEMORY_FILE_PATH)
        kept = memory_recall.drop_matching(facts, subject)
        removed = len(facts) - len(kept)
        if removed:
            with open(settings.MEMORY_FILE_PATH, "w", encoding="utf-8") as fh:
                fh.write("\n".join(kept) + ("\n" if kept else ""))
        return "forgot %d fact(s) matching %r" % (removed, subject)

    content = memory_recall.remember_request(user_text)
    if content:
        fact = memory_recall.condense(provider, content)
        if not fact:
            return None
        facts = memory.load_memory(settings.MEMORY_FILE_PATH)
        merged = memory_recall.merge_facts(facts, [fact])[-settings.MEMORY_MAX_FACTS:]
        with open(settings.MEMORY_FILE_PATH, "w", encoding="utf-8") as fh:
            fh.write("\n".join(merged) + ("\n" if merged else ""))
        return "remembered: %s" % fact
    return None


def build_memory_callback(provider: ModelProvider) -> Optional[Callable[[List[Message]], None]]:
    """Returns the on_session_boundary callback ChatEngine calls with a
    just-finished conversation, or None if memory is disabled.

    Runs extraction in a background thread rather than inline, so the
    (already idle-triggered, so infrequent) extra model call never adds
    latency to the reply that triggered it.
    """
    if not settings.MEMORY_ENABLED:
        return None

    def on_session_boundary(messages: List[Message]) -> None:
        def worker():
            facts = memory.extract_facts(provider, messages)
            memory.save_facts(settings.MEMORY_FILE_PATH, facts, settings.MEMORY_MAX_FACTS)

        threading.Thread(target=worker, daemon=True).start()

    return on_session_boundary


def build_disabled_voice_line_patterns() -> List[str]:
    """Patterns the user has toggled off (settings.VOICE_LINE_PREFS_PATH),
    meaning that phrase should fall through to live TTS instead of
    playing its pre-recorded clip. Shared by main() below and run_hud.py
    so the same persisted choice applies in both."""
    overrides = voice_line_prefs.load_overrides(settings.VOICE_LINE_PREFS_PATH)
    return [pattern for pattern, enabled in overrides.items() if not enabled]


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


def active_voice_line_set() -> str:
    """Which clip set is selected, falling back to the settings default.

    Read from the persisted HUD settings rather than a module constant,
    so switching sets in the panel survives a restart.
    """
    from great_sage.core import hud_settings
    saved = hud_settings.load(settings.HUD_SETTINGS_PATH).get("voice_line_set")
    sets = getattr(settings, "VOICE_LINE_SETS", {})
    if saved in sets:
        return saved
    return getattr(settings, "VOICE_LINE_SET", "japanese")


def build_voice_lines(set_name=None):
    """Compiled (pattern, clip) pairs for the chosen set.

    A set need not cover every phrase. Anything without a clip falls
    through to live TTS, exactly as a disabled line does, so a partial
    set is a valid choice rather than a broken one.
    """
    sets = getattr(settings, "VOICE_LINE_SETS", None)
    if sets:
        name = set_name or active_voice_line_set()
        lines = sets.get(name, [])
    else:
        lines = settings.VOICE_LINES        # older config, no sets defined
    return [
        (re.compile(pattern, re.IGNORECASE), path)
        for pattern, path in lines
    ]


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

    if settings.VOICE_ENGINE == "pocket":
        from great_sage.voice.pocket_tts_engine import PocketTTSVoiceOutput

        try:
            return PocketTTSVoiceOutput(
                reference_audio_path=settings.CLONE_REFERENCE_AUDIO_PATH,
                voice_lines=build_voice_lines(),
                disabled_voice_line_patterns=build_disabled_voice_line_patterns(),
            )
        except VoiceError as exc:
            print(f"[Voice unavailable] {exc}")
            print("Continuing in text-only mode.\n")
            return None

    if settings.VOICE_ENGINE == "clone":
        from great_sage.voice.cloned_voice_engine import XTTSClonedVoiceOutput

        translator = build_translator(provider) if settings.CLONE_TRANSLATE else None
        try:
            return XTTSClonedVoiceOutput(
                reference_audio_path=settings.CLONE_REFERENCE_AUDIO_PATH,
                language=settings.CLONE_LANGUAGE,
                device=settings.CLONE_DEVICE,
                speed=settings.CLONE_SPEED,
                voice_lines=build_voice_lines(),
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
    engine = ChatEngine(
        provider,
        build_system_prompt(),
        idle_reset_seconds=settings.SESSION_IDLE_RESET_MINUTES * 60,
        on_session_boundary=build_memory_callback(provider),
    )
    run_cli(engine, settings.OLLAMA_DEFAULT_MODEL, voice=voice)
    return 0


if __name__ == "__main__":
    sys.exit(main())
