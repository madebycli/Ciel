from __future__ import annotations

from pathlib import Path

from .files import load_manifest
from .store import SQLiteContextGraph


def project_context_root() -> Path:
    return Path(__file__).resolve().parents[2] / "context"


def rebuild_project_index(database: Path, root: Path | None = None) -> int:
    root = root or project_context_root()
    nodes, edges = load_manifest(root)
    with SQLiteContextGraph(database) as graph:
        graph.replace_namespace("project", nodes, edges)
    return len(nodes)


def open_project_graph(database: Path, root: Path | None = None) -> SQLiteContextGraph:
    root = root or project_context_root()
    graph = SQLiteContextGraph(database)
    count = graph.db.execute(
        "SELECT COUNT(*) FROM nodes WHERE namespace = 'project'"
    ).fetchone()[0]
    if not count:
        nodes, edges = load_manifest(root)
        graph.replace_namespace("project", nodes, edges)
    return graph
