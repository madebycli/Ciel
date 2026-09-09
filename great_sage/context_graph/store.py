from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import deque
from pathlib import Path

from .model import ContextEdge, ContextHit, ContextNode


def _fts_query(text: str) -> str:
    tokens = re.findall(r"[\w-]+", text.lower(), flags=re.UNICODE)
    return " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens[:16])


class SQLiteContextGraph:
    """Fast local index for a file-backed context graph.

    Markdown files remain the source of truth. SQLite is disposable and may be
    rebuilt at any time. The schema uses namespaces so project context, user
    memory and future character-specific knowledge can coexist without id
    collisions.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self._fts = False
        self._init_schema()

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _init_schema(self) -> None:
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS nodes (
                namespace TEXT NOT NULL,
                node_id TEXT NOT NULL,
                title TEXT NOT NULL,
                kind TEXT NOT NULL,
                scope TEXT NOT NULL,
                body TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                source_path TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (namespace, node_id)
            );

            CREATE TABLE IF NOT EXISTS edges (
                namespace TEXT NOT NULL,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                relation TEXT NOT NULL,
                weight REAL NOT NULL,
                PRIMARY KEY (namespace, source, target, relation)
            );

            CREATE INDEX IF NOT EXISTS idx_edges_source
                ON edges(namespace, source);
            CREATE INDEX IF NOT EXISTS idx_edges_target
                ON edges(namespace, target);
            CREATE INDEX IF NOT EXISTS idx_nodes_kind
                ON nodes(namespace, kind);
            CREATE INDEX IF NOT EXISTS idx_nodes_scope
                ON nodes(namespace, scope);
            """
        )
        try:
            self.db.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
                    namespace UNINDEXED,
                    node_id UNINDEXED,
                    title,
                    body,
                    tags,
                    tokenize='unicode61'
                )
                """
            )
            self._fts = True
        except sqlite3.OperationalError:
            self._fts = False
        self.db.commit()

    def replace_namespace(
        self,
        namespace: str,
        nodes: tuple[ContextNode, ...] | list[ContextNode],
        edges: tuple[ContextEdge, ...] | list[ContextEdge],
    ) -> None:
        now = int(time.time())
        with self.db:
            self.db.execute("DELETE FROM edges WHERE namespace = ?", (namespace,))
            self.db.execute("DELETE FROM nodes WHERE namespace = ?", (namespace,))
            if self._fts:
                self.db.execute("DELETE FROM nodes_fts WHERE namespace = ?", (namespace,))

            self.db.executemany(
                """
                INSERT INTO nodes(
                    namespace, node_id, title, kind, scope, body,
                    tags_json, source_path, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        namespace,
                        node.id,
                        node.title,
                        node.kind,
                        node.scope,
                        node.body,
                        json.dumps(node.tags, ensure_ascii=False),
                        str(node.source_path) if node.source_path else None,
                        now,
                    )
                    for node in nodes
                ],
            )
            self.db.executemany(
                """
                INSERT INTO edges(namespace, source, target, relation, weight)
                VALUES (?, ?, ?, ?, ?)
                """,
                [(namespace, e.source, e.target, e.relation, e.weight) for e in edges],
            )
            if self._fts:
                self.db.executemany(
                    """
                    INSERT INTO nodes_fts(namespace, node_id, title, body, tags)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (namespace, node.id, node.title, node.body, " ".join(node.tags))
                        for node in nodes
                    ],
                )

    def get(self, node_id: str, namespace: str = "project") -> ContextNode | None:
        row = self.db.execute(
            "SELECT * FROM nodes WHERE namespace = ? AND node_id = ?",
            (namespace, node_id),
        ).fetchone()
        return self._row_to_node(row) if row else None

    def search(
        self,
        query: str,
        namespace: str = "project",
        limit: int = 8,
        expand_hops: int = 1,
    ) -> tuple[ContextHit, ...]:
        limit = max(1, min(int(limit), 50))
        expand_hops = max(0, min(int(expand_hops), 3))
        seeds = self._lexical_seed_ids(query, namespace, max(limit, 4))
        if not seeds:
            return ()

        hits: dict[str, ContextHit] = {}
        queue = deque()
        for rank, node_id in enumerate(seeds):
            node = self.get(node_id, namespace)
            if node is None:
                continue
            score = 1.0 / (1.0 + rank)
            hits[node_id] = ContextHit(node=node, score=score)
            queue.append((node_id, score, 0))

        seen_distance = {node_id: 0 for node_id in seeds}
        while queue:
            current, score, distance = queue.popleft()
            if distance >= expand_hops:
                continue
            for neighbor, relation, weight in self._neighbors(current, namespace):
                next_distance = distance + 1
                previous_distance = seen_distance.get(neighbor)
                if previous_distance is not None and previous_distance <= next_distance:
                    continue
                seen_distance[neighbor] = next_distance
                graph_score = score * weight * (0.65 ** next_distance)
                node = self.get(neighbor, namespace)
                if node is None:
                    continue
                previous = hits.get(neighbor)
                if previous is None or graph_score > previous.score:
                    hits[neighbor] = ContextHit(
                        node=node,
                        score=graph_score,
                        distance=next_distance,
                        via_relation=relation,
                    )
                queue.append((neighbor, graph_score, next_distance))

        ordered = sorted(
            hits.values(),
            key=lambda hit: (-hit.score, hit.distance, hit.node.id),
        )
        return tuple(ordered[:limit])

    def _lexical_seed_ids(self, query: str, namespace: str, limit: int) -> list[str]:
        fts = _fts_query(query)
        if self._fts and fts:
            try:
                rows = self.db.execute(
                    """
                    SELECT node_id
                    FROM nodes_fts
                    WHERE nodes_fts MATCH ? AND namespace = ?
                    ORDER BY bm25(nodes_fts)
                    LIMIT ?
                    """,
                    (fts, namespace, limit),
                ).fetchall()
                if rows:
                    return [row["node_id"] for row in rows]
            except sqlite3.OperationalError:
                pass

        terms = re.findall(r"[\w-]+", query.lower(), flags=re.UNICODE)[:8]
        if not terms:
            return []
        clauses = []
        args: list[object] = [namespace]
        for term in terms:
            clauses.append(
                "(lower(title) LIKE ? OR lower(body) LIKE ? OR lower(tags_json) LIKE ?)"
            )
            needle = f"%{term}%"
            args.extend((needle, needle, needle))
        args.append(limit)
        rows = self.db.execute(
            f"""
            SELECT node_id
            FROM nodes
            WHERE namespace = ? AND ({' OR '.join(clauses)})
            ORDER BY title
            LIMIT ?
            """,
            args,
        ).fetchall()
        return [row["node_id"] for row in rows]

    def _neighbors(self, node_id: str, namespace: str):
        rows = self.db.execute(
            """
            SELECT target AS neighbor, relation, weight
            FROM edges
            WHERE namespace = ? AND source = ?
            UNION ALL
            SELECT source AS neighbor, relation, weight
            FROM edges
            WHERE namespace = ? AND target = ?
            """,
            (namespace, node_id, namespace, node_id),
        ).fetchall()
        return [(row["neighbor"], row["relation"], float(row["weight"])) for row in rows]

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> ContextNode:
        return ContextNode(
            id=row["node_id"],
            title=row["title"],
            kind=row["kind"],
            scope=row["scope"],
            body=row["body"],
            tags=tuple(json.loads(row["tags_json"])),
            source_path=Path(row["source_path"]) if row["source_path"] else None,
        )
