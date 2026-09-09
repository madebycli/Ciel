from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LinuxPaths:
    config: Path
    data: Path
    cache: Path
    runtime: Path


@dataclass(frozen=True)
class LinuxSession:
    wayland: bool
    compositor: str
    layer_shell_expected: bool


def xdg_paths(app_id: str = "ciel") -> LinuxPaths:
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / app_id
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / app_id
    cache = Path(os.environ.get("XDG_CACHE_HOME", home / ".cache")) / app_id
    runtime_root = os.environ.get("XDG_RUNTIME_DIR")
    runtime = (Path(runtime_root) / app_id if runtime_root
               else Path(tempfile.gettempdir()) / f"{app_id}-{os.getuid()}")
    return LinuxPaths(config=config, data=data, cache=cache, runtime=runtime)


def ensure_runtime_dirs(paths: LinuxPaths) -> None:
    for path in (paths.config, paths.data, paths.cache, paths.runtime):
        path.mkdir(parents=True, exist_ok=True)
    try:
        paths.runtime.chmod(0o700)
    except OSError:
        pass


def detect_session() -> LinuxSession:
    wayland = bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland")
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        compositor = "hyprland"
    elif os.environ.get("SWAYSOCK"):
        compositor = "sway"
    elif os.environ.get("NIRI_SOCKET"):
        compositor = "niri"
    elif os.environ.get("KDE_FULL_SESSION"):
        compositor = "kde"
    else:
        compositor = "unknown"
    layer_shell_expected = wayland and compositor in {"hyprland", "sway", "niri", "kde"}
    return LinuxSession(wayland, compositor, layer_shell_expected)
