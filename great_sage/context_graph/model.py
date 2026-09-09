from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ContextNode:
    id: str
    title: str
    kind: str
    scope: str
    body: str
    tags: tuple[str, ...] = ()
    source_path: Path | None = None


@dataclass(frozen=True)
class ContextEdge:
    source: str
    target: str
    relation: str
    weight: float = 1.0


@dataclass(frozen=True)
class ContextHit:
    node: ContextNode
    score: float
    distance: int = 0
    via_relation: str | None = None
