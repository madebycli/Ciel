# -*- mode: python ; coding: utf-8 -*-
"""The transparent overlay, built as its own executable.

WHY THIS IS A SEPARATE BUILD, and not just another entry point in
great_sage.spec:

The overlay needs PySide6 6.4.3. Anything newer flickers - Qt moved
QtWebEngine onto the ANGLE backend in 6.5.1 and translucent windows have
flickered on it since. 6.4.3 predates that switch and is visibly stable.

But PySide6 only gained Python 3.14 support in 6.10, so 6.4.3 cannot be
installed alongside the main app's interpreter at all. The app itself
wants to stay on 3.14, where its torch/CUDA build already works.

Two interpreters, therefore two bundles. This is exactly how the app runs
from source today - run_hud.py already launches the overlay under
.overlay-venv's Python 3.11 - so the packaged build mirrors the
arrangement that is known to work rather than inventing a new one.

Build this with the 3.11 interpreter, NOT the default one:

    .overlay-venv/Scripts/python.exe -m PyInstaller overlay.spec

build.py does that, and checks the Qt version it picked up, because a
build that silently used the wrong interpreter produces an overlay that
looks fine until it flickers on a tester's machine.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# Refuse to build under the wrong interpreter. Without this the spec runs
# happily on 3.14, picks up PySide6 6.10.1, and ships the flickering
# overlay - the exact bug this split exists to avoid, and one that does
# not show up until someone watches the overlay for a while.
if sys.version_info[:2] != (3, 11):
    raise SystemExit(
        f"overlay.spec must be built with Python 3.11 (got "
        f"{sys.version_info.major}.{sys.version_info.minor}). "
        f"Use .overlay-venv/Scripts/python.exe -m PyInstaller overlay.spec")

import PySide6                                              # noqa: E402
_qt_ver = getattr(PySide6, "__version__", "?")
if not _qt_ver.startswith("6.4"):
    raise SystemExit(
        f"overlay.spec found PySide6 {_qt_ver}, but the overlay needs 6.4.x. "
        f"Newer Qt flickers on translucent windows (ANGLE backend, 6.5.1+).")

# The overlay renders the same page as the main window, and loads it from
# disk by path. It carries its own copy so the overlay exe is
# self-contained: it can be launched, tested, and debugged without the
# main bundle sitting next to it.
datas = [
    ('hud_prototype.html', '.'),
    ('vendor', 'vendor'),
    # The overlay plays the appear sound as its own visuals come up, and
    # its core menu makes the same sounds the main HUD does.
    ('assets', 'assets'),
]

# Qt WebEngine's helper process, Chromium resources and locale data are
# not Python imports - PyInstaller cannot infer them, and without them
# the overlay process starts and dies immediately with no window.
try:
    datas += collect_data_files('PySide6', includes=[
        '**/QtWebEngineProcess*', '**/resources/**', '**/translations/**',
    ])
except Exception:
    pass

hiddenimports = [
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtNetwork',
    'PySide6.QtWebChannel',
    'PySide6.QtPrintSupport',
]

# Everything the AI stack needs lives in the OTHER bundle. The overlay
# imports PySide6 and the standard library, nothing else (verified
# against its import list), so excluding these is not an optimisation
# guess - they genuinely have no path into this process, and leaving them
# includable risks PyInstaller dragging in a multi-gigabyte torch that
# would be dead weight in every copy.
excludes = [
    'torch', 'torchaudio', 'torchvision', 'f5_tts', 'vocos', 'transformers',
    'faster_whisper', 'pedalboard', 'numpy', 'scipy', 'matplotlib',
    'webview', 'pythonnet', 'clr_loader', 'great_sage',
    'PyInstaller', 'pytest', 'IPython', 'tkinter',
]

a = Analysis(
    ['overlay_window.py'],
    pathex=[os.path.abspath('.')],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='GreatSageOverlay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,      # a console here would flash a black box over the desktop
    disable_windowed_traceback=False,
    icon='icon.ico',
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, name='GreatSageOverlay',
)
