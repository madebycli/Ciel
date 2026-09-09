from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class VoiceConfig:
    engine: str = "inherit"
    reference_audio: str | None = None
    language: str | None = None
    translate: bool | None = None
    speed: float | None = None


@dataclass(frozen=True)
class ThemeConfig:
    accent: str | None = None
    background: str | None = None
    avatar: str | None = None


@dataclass(frozen=True)
class CharacterProfile:
    schema_version: int
    id: str
    display_name: str
    description: str
    pack_dir: Path
    prompt_file: str
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    theme: ThemeConfig = field(default_factory=ThemeConfig)
    services: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def resolve_asset(self, relative_path: str | None) -> Path | None:
        if not relative_path:
            return None
        root = self.pack_dir.resolve()
        candidate = (root / relative_path).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError(f"Asset escapes character pack: {relative_path!r}")
        return candidate

    @property
    def prompt_path(self) -> Path:
        path = self.resolve_asset(self.prompt_file)
        assert path is not None
        return path

    def read_prompt(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8").strip()
