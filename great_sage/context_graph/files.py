from __future__ import annotations

import json
import re
from pathlib import Path

from .model import ContextEdge, ContextNode

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class ContextManifestError(ValueError):
    pass


def _safe_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    root = root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ContextManifestError(f"context path escapes root: {relative!r}")
    return candidate


def load_manifest(root: Path) -> tuple[tuple[ContextNode, ...], tuple[ContextEdge, ...]]:
    root = root.resolve()
    manifest_path = root / "graph.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContextManifestError(f"missing context manifest: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise ContextManifestError(f"invalid JSON in {manifest_path}: {exc}") from exc

    if data.get("version") != 1:
        raise ContextManifestError("context graph version must be 1")

    nodes: list[ContextNode] = []
    known: set[str] = set()
    for raw in data.get("nodes", []):
        node_id = str(raw.get("id", "")).strip().lower()
        if not _ID_RE.fullmatch(node_id):
            raise ContextManifestError(f"invalid node id: {node_id!r}")
        if node_id in known:
            raise ContextManifestError(f"duplicate node id: {node_id}")
        known.add(node_id)

        rel = str(raw.get("path", "")).strip()
        path = _safe_path(root, rel)
        if path.suffix.lower() != ".md":
            raise ContextManifestError(f"context node must be markdown: {rel!r}")
        try:
            body = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise ContextManifestError(f"missing context node file: {rel}") from exc

        title = str(raw.get("title") or node_id.replace("_", " ").replace("-", " ").title()).strip()
        kind = str(raw.get("kind") or "note").strip().lower()
        scope = str(raw.get("scope") or "project").strip().lower()
        tags = tuple(sorted({str(tag).strip().lower() for tag in raw.get("tags", []) if str(tag).strip()}))
        nodes.append(ContextNode(
            id=node_id,
            title=title,
            kind=kind,
            scope=scope,
            body=body,
            tags=tags,
            source_path=path,
        ))

    edges: list[ContextEdge] = []
    for raw in data.get("edges", []):
        source = str(raw.get("source", "")).strip().lower()
        target = str(raw.get("target", "")).strip().lower()
        relation = str(raw.get("relation", "")).strip().lower()
        if source not in known or target not in known:
            raise ContextManifestError(f"edge references unknown node: {source!r} -> {target!r}")
        if not _ID_RE.fullmatch(relation):
            raise ContextManifestError(f"invalid relation: {relation!r}")
        weight = float(raw.get("weight", 1.0))
        if not 0.0 < weight <= 1.0:
            raise ContextManifestError("edge weight must be > 0 and <= 1")
        edges.append(ContextEdge(source, target, relation, weight))

    return tuple(nodes), tuple(edges)
