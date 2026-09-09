# AI Context Entry Point

Ciel is a Linux-native, Wayland-first local AI companion framework.

For architecture work, do not infer the project from old Windows files. Start with the graph in `context/graph.json`, then open only the linked Markdown nodes relevant to the task.

## Context loading order

1. `context/graph.json`
2. `context/nodes/project.md`
3. Follow graph edges to the smallest relevant set of nodes
4. Read `PLAN.md` only when implementation sequencing is needed
5. Treat `legacy/windows/` as reference material, never as an active architecture

## Hard constraints

- Linux native only
- Wayland first
- AMD ROCm first, CPU fallback
- Character packs are data, not executable plugins
- Shared capabilities live in services, not inside character implementations
- Repo Markdown is the source of truth for architecture context
- SQLite context indexes are disposable caches
- New subsystems must add or update a context node and its graph edges
- Avoid monolithic global configuration
- Do not import from `legacy/windows/` in new runtime code

## Performance rule

Do not concatenate every context file into every model prompt. Retrieve a small lexical seed set, expand only a bounded number of graph hops, then render a hard-bounded context bundle.

Useful development commands:

```bash
python ciel.py --context-index
python ciel.py --context-search "character services"
python ciel.py --context-bundle "Wayland overlay"
```
