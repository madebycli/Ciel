from __future__ import annotations

from collections.abc import Iterable

from .model import ContextHit


def render_context_bundle(
    hits: Iterable[ContextHit],
    max_chars: int = 12000,
    per_node_chars: int = 4000,
) -> str:
    """Render retrieved graph nodes into a hard-bounded model context block."""
    max_chars = max(256, int(max_chars))
    per_node_chars = max(128, min(int(per_node_chars), max_chars))

    parts: list[str] = []
    used = 0
    for hit in hits:
        relation = hit.via_relation or "seed"
        header = (
            f"[CIEL_CONTEXT id={hit.node.id} kind={hit.node.kind} "
            f"distance={hit.distance} via={relation}]"
        )
        separator_cost = 2 if parts else 0
        available = max_chars - used - separator_cost - len(header) - 1
        if available <= 0:
            break

        body = hit.node.body.strip()
        body_limit = min(per_node_chars, available)
        if len(body) > body_limit:
            if body_limit <= 1:
                break
            body = body[: body_limit - 1].rstrip() + "…"

        part = header + "\n" + body
        if parts:
            used += 2
        parts.append(part)
        used += len(part)

        if used >= max_chars:
            break

    return "\n\n".join(parts)
