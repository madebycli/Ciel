from __future__ import annotations

import json
from pathlib import Path

import pytest

from great_sage.context_graph import ContextManifestError, SQLiteContextGraph, load_manifest


def _write_graph(root: Path) -> None:
    (root / "nodes").mkdir()
    (root / "nodes" / "architecture.md").write_text(
        "# Architecture\nWayland overlay uses GTK and layer shell.",
        encoding="utf-8",
    )
    (root / "nodes" / "characters.md").write_text(
        "# Characters\nCharacter packs are data only.",
        encoding="utf-8",
    )
    (root / "graph.json").write_text(
        json.dumps(
            {
                "version": 1,
                "nodes": [
                    {
                        "id": "architecture",
                        "path": "nodes/architecture.md",
                        "kind": "architecture",
                        "scope": "project",
                        "tags": ["wayland", "gtk"],
                    },
                    {
                        "id": "characters",
                        "path": "nodes/characters.md",
                        "kind": "subsystem",
                        "scope": "project",
                        "tags": ["persona"],
                    },
                ],
                "edges": [
                    {
                        "source": "architecture",
                        "target": "characters",
                        "relation": "contains",
                        "weight": 0.8,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_manifest_and_bounded_graph_retrieval(tmp_path: Path):
    root = tmp_path / "context"
    root.mkdir()
    _write_graph(root)
    nodes, edges = load_manifest(root)

    with SQLiteContextGraph(tmp_path / "graph.sqlite3") as graph:
        graph.replace_namespace("project", nodes, edges)
        hits = graph.search("Wayland GTK", expand_hops=1)

    assert hits[0].node.id == "architecture"
    assert any(hit.node.id == "characters" and hit.distance == 1 for hit in hits)


def test_context_manifest_blocks_path_escape(tmp_path: Path):
    root = tmp_path / "context"
    root.mkdir()
    (tmp_path / "secret.md").write_text("nope", encoding="utf-8")
    (root / "graph.json").write_text(
        json.dumps(
            {
                "version": 1,
                "nodes": [{"id": "bad", "path": "../secret.md"}],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ContextManifestError):
        load_manifest(root)
