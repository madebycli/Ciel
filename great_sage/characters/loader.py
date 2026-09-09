from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .model import CharacterProfile, ThemeConfig, VoiceConfig


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class CharacterLoadError(ValueError):
    pass


def default_character_root() -> Path:
    override = os.environ.get("CIEL_CHARACTER_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "characters"


def _manifest_path(character_id: str, root: Path) -> Path:
    if not _ID_RE.fullmatch(character_id or ""):
        raise CharacterLoadError(f"Invalid character id: {character_id!r}")
    return root / character_id / "character.json"


def load_profile(character_id: str, root: Path | None = None) -> CharacterProfile:
    root = (root or default_character_root()).resolve()
    manifest = _manifest_path(character_id, root)
    if not manifest.is_file():
        raise CharacterLoadError(f"Character pack not found: {character_id}")

    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CharacterLoadError(f"Could not read {manifest}: {exc}") from exc

    if raw.get("schema_version") != 1:
        raise CharacterLoadError("Unsupported character schema_version")
    if raw.get("id") != character_id:
        raise CharacterLoadError("Manifest id must match its directory name")

    display_name = str(raw.get("display_name", "")).strip()
    prompt_file = str(raw.get("prompt_file", "")).strip()
    if not display_name or not prompt_file:
        raise CharacterLoadError("display_name and prompt_file are required")

    voice_raw = raw.get("voice") or {}
    theme_raw = raw.get("theme") or {}
    services_raw = raw.get("services") or []
    if not isinstance(services_raw, list) or not all(isinstance(v, str) for v in services_raw):
        raise CharacterLoadError("services must be a list of service ids")

    profile = CharacterProfile(
        schema_version=1,
        id=character_id,
        display_name=display_name,
        description=str(raw.get("description", "")).strip(),
        pack_dir=manifest.parent,
        prompt_file=prompt_file,
        voice=VoiceConfig(
            engine=str(voice_raw.get("engine", "inherit")),
            reference_audio=voice_raw.get("reference_audio"),
            language=voice_raw.get("language"),
            translate=voice_raw.get("translate"),
            speed=voice_raw.get("speed"),
        ),
        theme=ThemeConfig(
            accent=theme_raw.get("accent"),
            background=theme_raw.get("background"),
            avatar=theme_raw.get("avatar"),
        ),
        services=tuple(dict.fromkeys(services_raw)),
        metadata=raw.get("metadata") or {},
    )

    try:
        if not profile.prompt_path.is_file():
            raise CharacterLoadError(f"Prompt file not found: {profile.prompt_file}")
        for optional_asset in (profile.voice.reference_audio, profile.theme.avatar):
            if optional_asset and not profile.resolve_asset(optional_asset).is_file():
                raise CharacterLoadError(f"Character asset not found: {optional_asset}")
    except ValueError as exc:
        raise CharacterLoadError(str(exc)) from exc

    return profile


def list_profiles(root: Path | None = None) -> list[CharacterProfile]:
    root = (root or default_character_root()).resolve()
    if not root.is_dir():
        return []
    profiles: list[CharacterProfile] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        try:
            profiles.append(load_profile(entry.name, root))
        except CharacterLoadError:
            continue
    return profiles
