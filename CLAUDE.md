# Ciel AI Instructions

Use `AI_CONTEXT.md` as the stable entry point for this repository.

Do not infer the active architecture from `legacy/windows/`, old Great Sage constants or historical notes. Read `context/graph.json`, then open only the connected context nodes needed for the task.

Hard constraints:

- Linux native only
- Wayland first
- AMD ROCm first, CPU fallback
- Character packs are declarative data
- Shared capabilities live in trusted services
- New code must not import from `legacy/windows/`
- Update the context graph when architecture boundaries change

For implementation order and unresolved decisions, read `PLAN.md` after the relevant graph nodes.
