#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Ciel Linux-native development entry point")
    parser.add_argument("--character", help="Character pack id, defaults to CIEL_CHARACTER or great_sage")
    parser.add_argument("--list-characters", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    args = parser.parse_args()

    if args.list_characters:
        for profile in list_profiles():
            print(f"{profile.id}\t{profile.display_name}")
        return 0

    if args.diagnose:
        print(json.dumps(_diagnostic_payload(args.character), indent=2))
        return 0

    runtime = load_runtime(args.character)
    print(f"Ciel Linux runtime ready: {runtime.character.display_name}")
    print("HUD host migration is the next implementation stage. Use --diagnose for platform status.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
