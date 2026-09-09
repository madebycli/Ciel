#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from great_sage.characters import list_profiles
from great_sage.runtime import load_runtime


def _diagnostic_payload(character_id: str | None) -> dict:
    runtime = load_runtime(character_id)
    return {
        "character": {
            "id": runtime.character.id,
            "name": runtime.character.display_name,
            "services": [service.id for service in runtime.services],
        },
        "compute": {
            "backend": runtime.accelerator.backend,
            "device": runtime.accelerator.torch_device,
            "name": runtime.accelerator.name,
            "detail": runtime.accelerator.detail,
        },
        "session": {
            "wayland": runtime.session.wayland,
            "compositor": runtime.session.compositor,
            "layer_shell_expected": runtime.session.layer_shell_expected,
        },
        "paths": {
            name: str(getattr(runtime.paths, name))
            for name in ("config", "data", "cache", "runtime")
        },
        "context_graph": {
            "database": str(runtime.context_database),
        },
    }


def _run_chat(character_id: str | None) -> int:
    runtime = load_runtime(character_id)
    enabled = {service.id for service in runtime.services}
    if "chat" not in enabled:
        raise SystemExit(
            f"Character {runtime.character.id!r} does not enable the chat service"
        )

    from great_sage.core.chat_engine import ChatEngine
    from great_sage.models.ollama_provider import OllamaProvider
    from great_sage.ui.cli import run_cli

    host = os.environ.get("CIEL_OLLAMA_HOST", "http://localhost:11434")
    model = os.environ.get("CIEL_OLLAMA_MODEL", "qwen3.5:4b")
    provider = OllamaProvider(host=host, model=model, timeout=60, think=False)
    engine = ChatEngine(provider, runtime.character.read_prompt())
    run_cli(engine, model, voice=None)
    return 0


def _rebuild_context(character_id: str | None) -> int:
    from great_sage.context_graph.runtime import rebuild_project_index

    runtime = load_runtime(character_id)
    count = rebuild_project_index(runtime.context_database)
    print(f"Indexed {count} context nodes -> {runtime.context_database}")
    return 0


def _search_context(character_id: str | None, query: str) -> int:
    from great_sage.context_graph.runtime import open_project_graph

    runtime = load_runtime(character_id)
    with open_project_graph(runtime.context_database) as graph:
        hits = graph.search(query, limit=8, expand_hops=1)
    for hit in hits:
        relation = f" via={hit.via_relation}" if hit.via_relation else ""
        print(
            f"{hit.score:.3f}\td={hit.distance}\t{hit.node.id}"
            f"\t{hit.node.title}{relation}"
        )
    return 0


def _bundle_context(character_id: str | None, query: str) -> int:
    from great_sage.context_graph import render_context_bundle
    from great_sage.context_graph.runtime import open_project_graph

    runtime = load_runtime(character_id)
    with open_project_graph(runtime.context_database) as graph:
        hits = graph.search(query, limit=8, expand_hops=1)
    print(render_context_bundle(hits, max_chars=12000, per_node_chars=4000))
    return 0


def _show_context_node(character_id: str | None, node_id: str) -> int:
    from great_sage.context_graph.runtime import open_project_graph

    runtime = load_runtime(character_id)
    with open_project_graph(runtime.context_database) as graph:
        node = graph.get(node_id)
    if node is None:
        raise SystemExit(f"Unknown context node: {node_id}")
    print(f"# {node.title}\n")
    print(node.body)
    return 0


def _run_overlay_spike(character_id: str | None) -> int:
    runtime = load_runtime(character_id)
    if not runtime.session.wayland:
        raise SystemExit("Overlay spike requires a Wayland session")
    if not runtime.session.layer_shell_expected:
        raise SystemExit(
            f"Layer-shell support is not expected on compositor "
            f"{runtime.session.compositor!r}"
        )
    from great_sage.ui.linux.overlay import run_overlay_spike

    html = Path(__file__).resolve().parent / "hud_prototype.html"
    return run_overlay_spike(html)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ciel Linux-native development entry point"
    )
    parser.add_argument(
        "--character",
        help="Character pack id, defaults to CIEL_CHARACTER or great_sage",
    )
    parser.add_argument("--list-characters", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Run the selected character in the shared CLI ChatEngine",
    )
    parser.add_argument(
        "--context-index",
        action="store_true",
        help="Rebuild the SQLite cache from context/graph.json and Markdown nodes",
    )
    parser.add_argument(
        "--context-search",
        metavar="QUERY",
        help="Search project context and expand one graph hop",
    )
    parser.add_argument(
        "--context-bundle",
        metavar="QUERY",
        help="Render a hard-bounded model context bundle from graph retrieval",
    )
    parser.add_argument(
        "--context-node",
        metavar="ID",
        help="Print one indexed project context node",
    )
    parser.add_argument(
        "--overlay-spike",
        action="store_true",
        help="Run the native GTK/WebKitGTK/gtk-layer-shell mini HUD spike",
    )
    args = parser.parse_args()

    if args.list_characters:
        for profile in list_profiles():
            print(f"{profile.id}\t{profile.display_name}")
        return 0
    if args.diagnose:
        print(json.dumps(_diagnostic_payload(args.character), indent=2))
        return 0
    if args.context_index:
        return _rebuild_context(args.character)
    if args.context_search:
        return _search_context(args.character, args.context_search)
    if args.context_bundle:
        return _bundle_context(args.character, args.context_bundle)
    if args.context_node:
        return _show_context_node(args.character, args.context_node)
    if args.overlay_spike:
        return _run_overlay_spike(args.character)
    if args.chat:
        return _run_chat(args.character)

    runtime = load_runtime(args.character)
    print(f"Ciel Linux runtime ready: {runtime.character.display_name}")
    print(
        "Use --chat, --diagnose, --context-search or --overlay-spike "
        "for the current development paths."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
