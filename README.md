# Ciel

Ciel is being rebuilt as a Linux-native, Wayland-first desktop companion engine with modular character packs.

> Development status: the Linux runtime foundation and character system exist on this branch. The Wayland HUD host and Layer-Shell overlay are the next implementation stage, so this branch is not yet a finished end-user release.

## Direction

- Linux only
- Wayland first
- Hyprland, Niri and Sway first
- KDE Plasma Wayland where Layer-Shell is available
- AMD ROCm first, CPU fallback
- no Wine and no Windows compatibility layer
- multiple characters without forking the AI core
- local-first model, speech and memory

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

The first migrated pack is `great_sage`. Additions should use `characters/README.md` as the format reference.

## Linux development entry point

```bash
python ciel.py --list-characters
python ciel.py --diagnose
python ciel.py --character great_sage --diagnose
```

Useful environment variables:

```bash
CIEL_CHARACTER=great_sage
CIEL_ACCELERATOR=auto   # auto | rocm | cpu
CIEL_CHARACTER_DIR=/path/to/characters
```

`auto` intentionally means ROCm first and CPU second. NVIDIA CUDA is not a release target for the Linux-native port.

## Compute

Install PyTorch separately before `requirements.txt` so the build matches the machine:

- AMD Radeon/Ryzen target: ROCm-enabled PyTorch
- CPU target: CPU-only PyTorch

PyTorch exposes ROCm tensors through its `cuda` device API. Ciel detects AMD specifically through `torch.version.hip` and keeps that detail inside `great_sage/hardware/accelerator.py` instead of spreading hardware checks through TTS, STT and model code.

## Wayland UI plan

The existing Three.js HUD remains the first UI to port. The planned host is GTK/WebKitGTK, with GtkLayerShell for the compact transparent overlay. The separate `GIF-Player` project is the reference implementation for Layer-Shell surfaces, positioning, input regions and XDG runtime handling.

The Windows-only host, overlay, installer and packaging scripts were moved to `legacy/windows/`. They are reference code only and new Linux code must not import them.

## Current structure

```text
ciel.py                         Linux development entry point
characters/                     declarative character packs
great_sage/characters/          pack loader and validation
great_sage/services/            trusted service registry
great_sage/hardware/            ROCm/CPU selection
great_sage/platform/linux.py    XDG and Wayland session support
great_sage/runtime.py           composed runtime context
great_sage/core/                existing conversation/tool core
great_sage/models/              model providers
great_sage/voice/               speech stack
tests/                          new modular-runtime tests
legacy/windows/                 old Windows reference implementation
```

## Tests

The new character/runtime foundation uses standard-library unit tests:

```bash
python -m unittest discover -s tests -v
```

See `PLAN.md` for the complete port order, architecture alternatives, open questions and migration checkpoints.
