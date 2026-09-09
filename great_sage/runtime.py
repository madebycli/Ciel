from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from great_sage.characters import CharacterProfile, load_profile
from great_sage.hardware import Accelerator, detect_accelerator
from great_sage.platform import LinuxPaths, LinuxSession, detect_session, xdg_paths
from great_sage.services import ServiceSpec, validate_services


@dataclass(frozen=True)
class RuntimeContext:
    character: CharacterProfile
    accelerator: Accelerator
    paths: LinuxPaths
    session: LinuxSession
    services: tuple[ServiceSpec, ...]
    context_database: Path


def load_runtime(character_id: str | None = None) -> RuntimeContext:
    character_id = character_id or os.environ.get("CIEL_CHARACTER", "great_sage")
    character = load_profile(character_id)
    paths = xdg_paths()
    return RuntimeContext(
        character=character,
        accelerator=detect_accelerator(),
        paths=paths,
        session=detect_session(),
        services=validate_services(character.services),
        context_database=paths.cache / "context" / "graph.sqlite3",
    )
