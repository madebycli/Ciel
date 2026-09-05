"""Build Great Sage into dist/GreatSage/ - both halves, in the right order.

There are TWO bundles, built by TWO different Pythons:

  great_sage.spec   the app: HUD, Ollama client, F5-TTS voice stack.
                    Python 3.14, where the working CUDA torch lives.

  overlay.spec      the transparent desktop overlay.
                    Python 3.11 (.overlay-venv), the newest Python that
                    PySide6 6.4.3 supports.

The overlay is pinned to Qt 6.4.3 because every later version flickers:
Qt moved QtWebEngine to the ANGLE backend in 6.5.1, and translucent
windows have flickered on it since. 6.4.3 has no Python 3.14 build, and
the app will not leave 3.14, so the two cannot share an interpreter.

That is not a packaging compromise - it is what already runs here. From
source, run_hud.py launches the overlay under .overlay-venv's Python
while the app stays on 3.14. This script packages that same arrangement.

The overlay bundle is dropped into dist/GreatSage/overlay/, where
run_hud._overlay_command looks for it.

    py build.py                 both halves
    py build.py --overlay-only  just the overlay
    py build.py --app-only      just the app
"""

import argparse
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
APP_DIST = os.path.join(DIST, "GreatSage")
OVERLAY_DIST = os.path.join(DIST, "GreatSageOverlay")
OVERLAY_VENV_PY = os.path.join(HERE, ".overlay-venv", "Scripts", "python.exe")


def _run(cmd, label):
    print(f"\n=== {label} ===\n    {' '.join(cmd[:3])} ...", flush=True)
    t0 = time.time()
    p = subprocess.run(cmd, cwd=HERE)
    if p.returncode != 0:
        raise SystemExit(f"\n{label} FAILED (exit {p.returncode})")
    print(f"    done in {time.time() - t0:.0f}s", flush=True)


def _check_locked(path):
    """Refuse to build while a previous copy is still running.

    Ask the OS for the process; do NOT probe the file. Renaming a running
    .exe to its own name SUCCEEDS on Windows, so the obvious check passes
    and the build then dies partway through with PermissionError on some
    unrelated file inside _internal - which reads as a corrupt build
    rather than "the app is still open". That cost one failed build.
    """
    name = os.path.basename(path)
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}.exe", "/FO", "CSV"],
            capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return                      # cannot tell; let PyInstaller try
    if f"{name}.exe" in out:
        raise SystemExit(
            os.linesep
            + f"{name}.exe is still running. Close it, then build again."
            + os.linesep
            + f'  powershell -Command "Stop-Process -Name {name} -Force"')


def build_overlay():
    if not os.path.exists(OVERLAY_VENV_PY):
        raise SystemExit(
            f"Overlay interpreter not found:\n  {OVERLAY_VENV_PY}\n"
            f"Create it with Python 3.11, then:\n"
            f"  .overlay-venv/Scripts/python.exe -m pip install "
            f"PySide6==6.4.3 pyinstaller")
    _check_locked(OVERLAY_DIST)
    _run([OVERLAY_VENV_PY, "-m", "PyInstaller", "--noconfirm", "overlay.spec"],
         "Overlay bundle (Python 3.11 / PySide6 6.4.3)")


def build_app():
    # The HUD page is one big inline script, so a single broken string
    # literal is a SyntaxError that kills ALL of it: the window opens
    # black, never connects, and sits on "connecting..." - with a
    # perfectly healthy Python log, because the failure is in the page.
    # That shipped once. It is checked BEFORE the build now.
    for _script in ("check_js.py", "check_shaders.py"):
        if subprocess.run([sys.executable, _script], cwd=HERE).returncode:
            raise SystemExit(
                chr(10) + _script + " failed - refusing to build a bundle "
                "whose HUD cannot render.")
    _check_locked(APP_DIST)
    _run([sys.executable, "-m", "PyInstaller", "--noconfirm", "great_sage.spec"],
         "App bundle (Python 3.14 / torch + F5-TTS)")


def merge():
    """Move the overlay bundle inside the app bundle."""
    target = os.path.join(APP_DIST, "overlay")
    if not os.path.isdir(OVERLAY_DIST):
        raise SystemExit(f"No overlay build to merge at {OVERLAY_DIST}")
    if not os.path.isdir(APP_DIST):
        raise SystemExit(f"No app build to merge into at {APP_DIST}")
    if os.path.isdir(target):
        shutil.rmtree(target)
    shutil.copytree(OVERLAY_DIST, target)
    print(f"\n=== merged ===\n    overlay -> {target}")


def _find(root, needle):
    """Is a file named `needle` anywhere under root?"""
    needle = needle.lower()
    for _d, _dirs, files in os.walk(root):
        if any(f.lower() == needle for f in files):
            return True
    return False


def verify():
    """Check the shipped layout, not the build log.

    Every item here is something that has actually gone wrong, or that
    would fail silently on a tester's machine rather than on this one.
    """
    exe = os.path.join(APP_DIST, "GreatSage.exe")
    ov = os.path.join(APP_DIST, "overlay", "GreatSageOverlay.exe")
    internal = os.path.join(APP_DIST, "_internal")
    ov_internal = os.path.join(APP_DIST, "overlay", "_internal")

    checks = [
        ("app executable", os.path.exists(exe)),
        ("overlay executable", os.path.exists(ov)),
        ("HUD page", os.path.exists(os.path.join(internal, "hud_prototype.html"))),
        ("vendored three.js",
         os.path.exists(os.path.join(internal, "vendor", "three.min.js"))),
        ("voice lines", os.path.isdir(os.path.join(internal, "voice_lines"))),
        ("reference voice",
         os.path.isdir(os.path.join(internal, "voice_samples"))),
        ("overlay has its own HUD page",
         os.path.exists(os.path.join(ov_internal, "hud_prototype.html"))),
        # PyInstaller places these under _internal/PySide6/, not at the
        # top of _internal - so walk, do not list. Without the helper exe
        # and the .pak resources the overlay process starts and dies with
        # no window and no error the user ever sees.
        ("overlay has Qt WebEngine helper",
         _find(ov_internal, "qtwebengineprocess.exe")),
        ("overlay has Chromium resources",
         _find(ov_internal, "qtwebengine_resources.pak")),
        ("overlay has icu data", _find(ov_internal, "icudtl.dat")),
    ]
    # The whole point of the split: the app bundle must NOT carry Qt, and
    # the overlay must NOT carry torch. Either one leaking in means the
    # specs' excludes stopped working and the download doubles in size.
    def _tree_has(root, needle):
        for dirpath, _dirs, files in os.walk(root):
            if any(needle in f.lower() for f in files):
                return True
        return False

    # Metadata, not modules. A build that has torchcodec's module but not
    # its dist-info imports fine and then has NO VOICE - transformers
    # probes the version, PackageNotFoundError propagates up as a bogus
    # "Could not import module 'pipeline'", F5-TTS is skipped, and the app
    # runs mute in text-only mode without saying so. Checked here because
    # it is invisible at every other stage.
    if os.path.isdir(internal):
        for _m in ("torchcodec", "transformers", "torch"):
            checks.append((f"{_m} package metadata (dist-info)",
                           any(d.lower().startswith(_m.replace("-", "_"))
                               and d.endswith(".dist-info")
                               for d in os.listdir(internal))))

    # The two silent-failure assets. Both shipped missing once, and
    # neither produces an error the user ever sees: without the torchcodec
    # DLLs a reply is generated with NO SOUND, and without the VAD model
    # push-to-talk records, transcribes to "", and Sage never responds.
    if os.path.isdir(internal):
        # Name the exact files. "some libtorchcodec_* exists" passed while
        # the .pyd was missing, and the build shipped mute a second time.
        _tc = os.path.join(internal, "torchcodec")
        _tc_files = set(os.listdir(_tc)) if os.path.isdir(_tc) else set()
        for _lib in ("libtorchcodec_core7.dll", "libtorchcodec_image.dll",
                     "libtorchcodec_pybind_ops.pyd"):
            checks.append((f"torchcodec/{_lib} (else: replies have no audio)",
                           _lib in _tc_files))
        checks.append((
            "whisper VAD model (else: push-to-talk hears nothing)",
            _find(internal, "silero_vad_v6.onnx")))

    if os.path.isdir(internal):
        checks.append(("app bundle carries no Qt (expected)",
                       not _tree_has(internal, "pyside6")))
    if os.path.isdir(ov_internal):
        checks.append(("overlay carries no torch (expected)",
                       not _tree_has(ov_internal, "torch_cpu")))

    print("\n=== verify ===")
    bad = 0
    for label, ok in checks:
        print(f"    {'OK  ' if ok else 'FAIL'}  {label}")
        bad += (not ok)

    def _size(p):
        return sum(os.path.getsize(os.path.join(d, f))
                   for d, _s, fs in os.walk(p) for f in fs) / 1e9
    if os.path.isdir(APP_DIST):
        print(f"\n    total {_size(APP_DIST):.2f} GB"
              f"   (overlay {_size(os.path.join(APP_DIST, 'overlay')):.2f} GB)")
    if bad:
        raise SystemExit(f"\n{bad} check(s) FAILED - do not ship this build.")
    print("\n    Build is complete and self-consistent.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--overlay-only", action="store_true")
    ap.add_argument("--app-only", action="store_true")
    a = ap.parse_args()

    if not a.app_only:
        build_overlay()
    if not a.overlay_only:
        build_app()
    if not (a.app_only or a.overlay_only):
        merge()
        verify()
    elif a.overlay_only and os.path.isdir(APP_DIST):
        merge()
        verify()
