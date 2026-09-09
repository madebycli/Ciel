# Ciel Agent Entry Point

Start with `AI_CONTEXT.md` and `context/graph.json`.

Load context by graph relevance, not by concatenating the whole repository. The Markdown nodes under `context/nodes/` are the architecture source of truth. SQLite under the XDG cache path is a disposable runtime index.

Never use `legacy/windows/` as active implementation guidance. Ciel is Linux-native, Wayland-first, AMD ROCm-first with CPU fallback, and supports multiple declarative character packs over one shared runtime.
