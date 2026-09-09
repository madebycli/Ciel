#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os

from great_sage.characters import list_profiles
from great_sage.runtime import load_runtime


def _diagnostic_payload(character_id: str | None) -> dict:
    runtime = load_runtime(character_id)
    return {
        "character": {
            "id": runtime.character.id,
            "name": runtime.character.display_name,
            "services": [service.id for service in runtime.services],
        },
        "compute": {
            "backend": runtime.accelerator.backend,
            "device": runtime.accelerator.torch_device,
            "name": runtime.accelerator.name,
            "detail": runtime.accelerator.detail,
        },
        "session": {
            "wayland": runtime.session.wayland,
            "compositor": runtime.session.compositor,
            "layer_shell_expected": runtime.session.layer_shell_expected,
        },
        "paths": {name: str(getattr(runtime.paths, name)) for name in ("config", "data", "cache", "runtime")},
    }


def _run_chat(character_id: str | None) -> int:
    runtime = load_runtime(character_id)
    enabled = {service.id for service in runtime.services}
    if "chat" not in enabled:
        raise SystemExit(f"Character {runtime.character.id!r} does not enable the chat service")

    from great_sage.core.chat_engine import ChatEngine
    from great_sage.models.ollama_provider import OllamaProvider
    from great_sage.ui.cli import run_cli

    host = os.environ.get("CIEL_OLLAMA_HOST", "http://localhost:11434")
    model = os.environ.get("CIEL_OLLAMA_MODEL", "qwen3.5:4b")
    provider = OllamaProvider(host=host, model=model, timeout=60, think=False)
    engine = ChatEngine(provider, runtime.character.read_prompt())
    run_cli(engine, model, voice=None)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ciel Linux-native development entry point")
    parser.add_argument("--character", help="Character pack id, defaults to CIEL_CHARACTER or great_sage")
    parser.add_argument("--list-characters", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--chat", action="store_true", help="Run the selected character in the shared CLI ChatEngine")
    args = parser.parse_args()

    if args.list_characters:
        for profile in list_profiles():
            print(f"{profile.id}\t{profile.display_name}")
        return 0

    if args.diagnose:
        print(json.dumps(_diagnostic_payload(args.character), indent=2))
        return 0

    if args.chat:
        return _run_chat(args.character)

    runtime = load_runtime(args.character)
    print(f"Ciel Linux runtime ready: {runtime.character.display_name}")
    print("Use --chat for the shared ChatEngine or --diagnose for platform status.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
