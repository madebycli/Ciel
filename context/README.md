# Ciel Context Graph

This directory is the versioned architecture and product context for humans, coding agents and the Ciel runtime.

`graph.json` contains lightweight metadata and relationships. The actual knowledge lives in Markdown under `nodes/`.

At runtime the graph can be compiled into an indexed SQLite database under the XDG cache directory. The database is not source of truth and is safe to delete.

## Rules for adding context

1. Add one focused Markdown node.
2. Register it in `graph.json`.
3. Connect it to existing nodes with explicit relations.
4. Keep nodes small enough to retrieve independently.
5. Put stable architecture facts here, not chat transcripts or secrets.

This graph is intentionally storage-agnostic. A future vector index may sit next to SQLite without changing the Markdown nodes or graph relations.
