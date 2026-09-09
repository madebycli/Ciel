# Ciel

Ciel is being rebuilt as a Linux-native, Wayland-first desktop companion engine with modular character packs and bounded graph-based AI context.

> Development status: the Linux runtime, character system, context graph and first native Layer-Shell overlay spike exist on `linux-port-clean-start`. This is not yet an end-user release.

## Direction

- Linux only
- Wayland first
- Hyprland, Niri and Sway first
- KDE Plasma Wayland where Layer-Shell is available
- AMD ROCm first, CPU fallback
- no Wine and no Windows compatibility layer
- multiple characters without forking the AI core
- local-first model, speech, memory and context

## Character packs

A character is data, not a fork of the application.

```text
characters/<id>/
  character.json
  prompt.md
  voice/        optional
  ui/           optional
```

A pack can select its prompt, voice settings, theme and enabled Ciel services. The actual services stay in the trusted core and are validated against a central allowlist.

Current test packs are `great_sage` and `ciel`.

## Linux development entry point

```bash
python ciel.py --list-characters
python ciel.py --diagnose
python ciel.py --character great_sage --chat
```

Useful environment variables:

```bash
CIEL_CHARACTER=great_sage
CIEL_ACCELERATOR=auto   # auto | rocm | cpu
CIEL_CHARACTER_DIR=/path/to/characters
```

`auto` intentionally means ROCm first and CPU second. NVIDIA CUDA is not a release target for the Linux-native port.

## AI context graph

Architecture context is split into focused Markdown nodes under `context/nodes/` and connected by `context/graph.json`.

```bash
python ciel.py --context-index
python ciel.py --context-search "Wayland overlay"
python ciel.py --context-node linux-native
```

The files are the source of truth. Ciel compiles them into a disposable SQLite cache under the XDG cache directory. Retrieval uses a small lexical seed set plus bounded graph expansion instead of injecting the entire knowledge base into every prompt.

Coding AIs should start at `AI_CONTEXT.md`, `AGENTS.md` or `CLAUDE.md`. All three point to the same graph instead of maintaining separate architecture descriptions.

## Compute

Install PyTorch separately before `requirements.txt` so the build matches the machine:

- AMD Radeon/Ryzen target: ROCm-enabled PyTorch
- CPU target: CPU-only PyTorch

PyTorch exposes ROCm tensors through its `cuda` device API. Ciel detects AMD specifically through `torch.version.hip` and keeps hardware checks inside `great_sage/hardware/accelerator.py`.

## Wayland UI

The existing Three.js HUD remains the first UI to port. A native GTK3 + WebKitGTK + gtk-layer-shell spike now exists at `great_sage/ui/linux/overlay.py`.

Run it on a supported Wayland compositor with the native dependencies installed:

```bash
python ciel.py --overlay-spike
```

The spike currently proves the host path, RGBA transparency setup, WebGL enablement and Layer-Shell placement. Input regions, dragging, panel windows and final character theming are the next stages.

See `docs/linux/DEPENDENCIES.md` for native packages and `GIF-Player` for the established Layer-Shell surface behavior reference.

## Current structure

```text
AI_CONTEXT.md                    shared AI context entry point
context/                         versioned context graph source
ciel.py                          Linux development entry point
characters/                      declarative character packs
great_sage/characters/           pack loader and validation
great_sage/context_graph/        SQLite FTS + bounded graph retrieval
great_sage/services/             trusted service registry
great_sage/hardware/             ROCm/CPU selection
great_sage/platform/linux.py     XDG and Wayland session support
great_sage/ui/linux/             native GTK/Wayland hosts
great_sage/runtime.py            composed runtime context
great_sage/core/                 existing conversation/tool core
great_sage/models/               model providers
great_sage/voice/                speech stack
tests/                           modular-runtime and context tests
legacy/windows/                  old Windows reference implementation
```

## Tests

```bash
python -m pytest -q
```

See `PLAN.md` for the full migration order and `context/graph.json` for the current architecture map.
