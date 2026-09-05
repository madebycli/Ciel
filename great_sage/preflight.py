"""What is missing before Great Sage can run, and how to get it.

Detection only - nothing here installs, downloads, or changes anything, so
it is safe to call on every startup and from the installer UI alike. The
installer decides what to act on; this module only reports.

Three of Great Sage's dependencies cannot travel inside the application:

  Ollama          a separate background service, installed system-wide
  the chat model  ~1.9GB, managed by Ollama in its own store
  WebView2        the Microsoft runtime the HUD window renders through

A fourth, the voice model weights, CAN be bundled but usually should not
be: ~3.6GB that F5-TTS and Whisper fetch on first use anyway.

Each check returns a Requirement, and every one of them is expected to
fail on a clean machine - that is the normal first-run state, not an
error. `blocking` marks the ones without which the app genuinely cannot
work, as opposed to those that only cost speed or polish.
"""

import os
import shutil
import sys
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

from great_sage.config import settings

OLLAMA_DOWNLOAD = "https://ollama.com/download/OllamaSetup.exe"
WEBVIEW2_DOWNLOAD = ("https://go.microsoft.com/fwlink/p/?LinkId=2124703")


@dataclass
class Requirement:
    key: str
    label: str
    ok: bool
    detail: str = ""
    blocking: bool = True
    # A shell command the installer can run to fix it, when fixing is just
    # a command. None means the fix needs a download or a human.
    fix_command: Optional[List[str]] = None
    fix_url: Optional[str] = None
    fix_hint: str = ""
    size_hint: str = ""


def _run(cmd, timeout=10):
    """Run a command, returning (rc, stdout). Never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:
        return 1, str(exc)


def check_ollama_installed() -> Requirement:
    exe = shutil.which("ollama")
    if not exe:
        # Installed-but-not-on-PATH is common enough to be worth catching,
        # otherwise a user who has it gets told to install it again.
        guess = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
        if os.path.exists(guess):
            exe = guess
    return Requirement(
        key="ollama",
        label="Ollama runtime",
        ok=bool(exe),
        detail=exe or "not found on PATH or in the default install location",
        fix_url=OLLAMA_DOWNLOAD,
        fix_hint="Download and run the installer, then come back and re-check.",
        size_hint="~700 MB",
    )


def check_ollama_running() -> Requirement:
    """Is the service actually answering? Installed but stopped is a real
    state - the app would otherwise fail only on the first message."""
    reachable, detail = False, "no response on " + settings.OLLAMA_HOST
    try:
        import requests
        r = requests.get(settings.OLLAMA_HOST + "/api/tags", timeout=3)
        reachable = r.status_code == 200
        detail = f"responding ({settings.OLLAMA_HOST})" if reachable \
            else f"HTTP {r.status_code}"
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"[:90]
    return Requirement(
        key="ollama_running", label="Ollama service", ok=reachable, detail=detail,
        fix_command=["ollama", "serve"],
        fix_hint="Ollama normally starts with Windows. Launch it once by hand "
                 "if this stays red.",
    )


def check_model() -> Requirement:
    """Is the configured chat model pulled?"""
    want = settings.OLLAMA_DEFAULT_MODEL
    found, detail = False, "could not query Ollama"
    try:
        import requests
        r = requests.get(settings.OLLAMA_HOST + "/api/tags", timeout=5)
        if r.status_code == 200:
            names = [m.get("name", "") for m in r.json().get("models", [])]
            # Ollama reports "qwen2.5:3b"; a bare "qwen2.5" should still match.
            found = any(n == want or n.split(":")[0] == want.split(":")[0]
                        for n in names)
            detail = ("installed" if found
                      else ("installed models: " + (", ".join(names) or "none")))
    except Exception as exc:
        detail = f"{type(exc).__name__}"
    return Requirement(
        key="model", label=f"Chat model ({want})", ok=found, detail=detail,
        fix_command=["ollama", "pull", want],
        fix_hint="Downloads once, then works offline.",
        size_hint="~1.9 GB",
    )


def check_webview2() -> Requirement:
    """The runtime the HUD window renders through.

    Present on essentially every up-to-date Windows 11, but a fresh or
    LTSC install can lack it, and without it the window never appears -
    which looks like the app silently doing nothing.
    """
    present, detail = False, "not detected"
    keys = [
        r"HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
        r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        r"HKCU\SOFTWARE\Microsoft\EdgeUpdate\Clients"
        r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    ]
    for k in keys:
        rc, out = _run(["reg", "query", k, "/v", "pv"])
        if rc == 0 and "pv" in out:
            present = True
            detail = "version " + out.split()[-1]
            break
    return Requirement(
        key="webview2", label="Microsoft WebView2 runtime", ok=present,
        detail=detail, fix_url=WEBVIEW2_DOWNLOAD,
        fix_hint="Ships with Windows 11; only needed on older or stripped installs.",
        size_hint="~150 MB",
    )


_gpu_cache = None


def check_gpu() -> Requirement:
    """CUDA availability. NOT blocking - the app runs on CPU, just slowly.

    Cached after the first call. Importing torch costs several seconds,
    and the installer re-runs every check after each fix - without the
    cache the window sat on "checking..." long enough to look hung, which
    is exactly what the screenshot of the first build showed.

    Safe to cache: a GPU does not appear or vanish mid-session.
    """
    global _gpu_cache
    if _gpu_cache is not None:
        return _gpu_cache
    ok, detail = False, "no CUDA GPU detected - voice will be very slow"
    try:
        import torch
        ok = bool(torch.cuda.is_available())
        detail = (torch.cuda.get_device_name(0) if ok
                  else "torch present, but no CUDA device")
    except Exception as exc:
        detail = f"torch unavailable ({type(exc).__name__})"
    _gpu_cache = Requirement(
        key="gpu", label="CUDA GPU (for voice)", ok=ok, detail=detail,
        blocking=False,
        fix_hint="Without a GPU the app still runs, but speech takes far "
                 "longer than it does to listen to.",
    )
    return _gpu_cache


def check_voice_weights() -> Requirement:
    """F5-TTS / Whisper weights in the HuggingFace cache.

    Not blocking: they download themselves on first use. Reported so a
    tester on a slow connection knows the first reply will be delayed
    rather than broken.
    """
    hf = os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface")
    hub = os.path.join(hf, "hub")
    have = os.path.isdir(hub) and any(
        "F5-TTS" in d or "vocos" in d.lower() for d in os.listdir(hub))
    return Requirement(
        key="weights", label="Voice model weights", ok=have,
        detail=("cached" if have else "will download on first use"),
        blocking=False, size_hint="~3.6 GB",
        fix_hint="Downloads automatically the first time Great Sage speaks.",
    )


def check_overlay_host() -> Requirement:
    """PySide6, which the transparent overlay runs on.

    NOT blocking: the main window is pywebview and works without it. Only
    overlay mode is lost - and without this check that loss is silent,
    because the launcher starts the overlay process, the import fails, the
    process exits, and the watcher quietly restores the main window. It
    looks exactly like the button doing nothing.
    """
    ok, detail = False, "not installed - overlay mode unavailable"

    if getattr(sys, "frozen", False):
        # In a build there is no PySide6 to import HERE - it lives in the
        # overlay's own bundle, built on a different Python (see
        # overlay.spec). Importing it in this process would always fail
        # and report overlay mode as broken on a build where it works.
        exe = os.path.join(os.path.dirname(sys.executable),
                           "overlay", "GreatSageOverlay.exe")
        ok = os.path.exists(exe)
        detail = exe if ok else f"missing: {exe}"
        return Requirement(
            key="overlay_host", label="Overlay host", ok=ok, detail=detail,
            blocking=False,
            fix_hint="Part of the application files - a missing overlay "
                     "folder means the extraction was incomplete.")

    # From source, the overlay runs under .overlay-venv when that exists -
    # run_hud._overlay_command prefers it - so ask THAT interpreter. This
    # process has a different, newer PySide6, and reporting its version
    # would name a Qt the overlay never uses: 6.10.1 here, 6.4.3 in the
    # venv that actually runs, and only 6.4.x is free of the flicker.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    venv_py = os.path.join(here, ".overlay-venv", "Scripts", "python.exe")
    if os.path.exists(venv_py):
        rc, out = _run([venv_py, "-c",
                        "from PySide6 import QtCore;print(QtCore.qVersion())"],
                       timeout=20)
        ok = rc == 0 and out.strip()[:1].isdigit()
        ver = out.strip().splitlines()[-1] if ok else ""
        detail = (f"Qt {ver} (.overlay-venv)" if ok
                  else "overlay venv present but PySide6 did not import")
        if ok and not ver.startswith("6.4"):
            detail += " - 6.4.x recommended; newer Qt flickers"
        return Requirement(
            key="overlay_host", label="Overlay host (PySide6)", ok=ok,
            detail=detail, blocking=False,
            fix_command=[venv_py, "-m", "pip", "install", "PySide6==6.4.3"],
            fix_hint="Only needed for the transparent desktop overlay.",
            size_hint="~150 MB")

    try:
        import PySide6                                     # noqa: F401
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        from PySide6 import QtCore
        ok = True
        detail = f"PySide6 / Qt {QtCore.qVersion()}"
    except Exception as exc:
        detail = f"{type(exc).__name__}: overlay mode unavailable"
    return Requirement(
        key="overlay_host", label="Overlay host (PySide6)", ok=ok,
        detail=detail, blocking=False,
        fix_command=[sys.executable, "-m", "pip", "install", "PySide6", "qtpy"],
        fix_hint="Only needed for the transparent desktop overlay; the main "
                 "window does not use it.",
        size_hint="~150 MB",
    )


def check_reference_voice() -> Requirement:
    """The clip F5 clones. Ships with the app, so this failing means a
    broken or incomplete copy rather than a missing prerequisite."""
    p = settings.F5_REFERENCE_AUDIO_PATH
    if not os.path.isabs(p):
        p = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), p)
    ok = os.path.exists(p)
    return Requirement(
        key="voice_ref", label="Reference voice clip", ok=ok,
        detail=p if ok else f"missing: {p}",
        fix_hint="Part of the application files - a missing one means the "
                 "download or extraction was incomplete.",
    )


ALL_CHECKS = (check_ollama_installed, check_ollama_running, check_model,
              check_webview2, check_gpu, check_voice_weights,
              check_overlay_host, check_reference_voice)


def run_all() -> List[Requirement]:
    results = []
    for fn in ALL_CHECKS:
        try:
            results.append(fn())
        except Exception as exc:
            results.append(Requirement(
                key=getattr(fn, "__name__", "?"), label=fn.__name__,
                ok=False, detail=f"check itself failed: {exc}"[:120]))
    return results


def blocking_failures(results=None) -> List[Requirement]:
    """The ones that actually stop the app from working."""
    return [r for r in (results or run_all()) if r.blocking and not r.ok]


if __name__ == "__main__":
    print(f"{'':2} {'requirement':30} {'':4} detail")
    print("-" * 78)
    for r in run_all():
        mark = "OK" if r.ok else ("!!" if r.blocking else "..")
        size = f" [{r.size_hint}]" if (not r.ok and r.size_hint) else ""
        print(f"{mark:2} {r.label:30} {'':4} {r.detail}{size}")
    bad = blocking_failures()
    print("\n" + ("Ready to run." if not bad else
                  f"{len(bad)} blocking item(s): "
                  + ", ".join(r.label for r in bad)))
