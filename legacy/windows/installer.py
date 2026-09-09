"""Setup window: shows what is missing and installs what it can.

Run this instead of run_hud.py on a machine that has not been set up:

    py installer.py

Three of Great Sage's dependencies live outside the application and
cannot be shipped inside it - the Ollama service, the chat model it
serves, and Microsoft's WebView2 runtime. Rather than let a tester
discover that through a blank window or a reply that never arrives, this
lists them, says how big each one is, and installs the ones that are just
a command.

Detection lives in great_sage/preflight.py and is shared with the app's
own startup check, so the two can never disagree about what "ready" means.
"""

import dataclasses
import logging
import os
import subprocess
import sys
import threading
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import webview  # noqa: E402

from great_sage import preflight  # noqa: E402

log = logging.getLogger(__name__)
HERE = os.path.dirname(os.path.abspath(__file__))


class SetupApi:
    def __init__(self):
        self._window = None
        self.launch_requested = False

    def attach(self, window):
        self._window = window

    # ---- called from the page -----------------------------------------
    def check(self):
        """Every requirement, as plain dicts the page can render."""
        return [dataclasses.asdict(r) for r in preflight.run_all()]

    def fix(self, key):
        """Act on one requirement. Returns {ok, message}."""
        target = next((r for r in preflight.run_all() if r.key == key), None)
        if target is None:
            return {"ok": False, "message": f"unknown item: {key}"}

        if target.fix_url:
            # Deliberately handed to the browser rather than downloaded and
            # run here: these are signed vendor installers with their own
            # UAC prompts, and silently fetching and executing an installer
            # is exactly the behaviour a tester should not have to trust.
            webbrowser.open(target.fix_url)
            return {"ok": True,
                    "message": "Opened the download in your browser. Run the "
                               "installer, then press RE-CHECK."}

        if target.fix_command:
            return self._run_streaming(target.fix_command)

        return {"ok": False, "message": target.fix_hint or "Nothing to run."}

    def launch(self):
        """Close setup and hand over to the app."""
        self.launch_requested = True
        try:
            self._window.destroy()
        except Exception:
            log.exception("Could not close the setup window")
        return {"ok": True}

    # ---- internals -----------------------------------------------------
    def _emit(self, line):
        """Push one line of command output to the page."""
        if not self._window:
            return
        try:
            safe = line.replace("\\", "\\\\").replace("'", "\\'")
            self._window.evaluate_js(
                f"window.__setupLog && window.__setupLog('{safe}')")
        except Exception:
            pass          # a closed window must not kill the command

    def _run_streaming(self, cmd):
        """Run a command, streaming its output into the page as it goes.

        `ollama pull` is a multi-gigabyte download; without progress the
        window looks frozen for several minutes and gets killed.
        """
        self._emit("$ " + " ".join(cmd))
        try:
            p = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except FileNotFoundError:
            return {"ok": False,
                    "message": f"{cmd[0]} was not found. Install it first."}
        except Exception as exc:
            return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}

        last = ""
        for line in p.stdout:
            line = line.rstrip()
            # Ollama redraws one progress line thousands of times; only
            # emit real changes or the page drowns in duplicates.
            if line and line != last:
                self._emit(line)
                last = line
        rc = p.wait()
        return {"ok": rc == 0,
                "message": "Finished." if rc == 0 else f"Exited with code {rc}."}


def show_setup() -> bool:
    """Open the setup window. True if the user chose to launch the app.

    Returns rather than starting the app itself, so the caller decides how
    to hand over. In a frozen build there is no run_hud.py to exec and
    sys.executable is the bundled exe, so an in-process call is the only
    thing that works in both source and packaged form.
    """
    results = preflight.run_all()
    for r in results:
        log.info("%-32s %s  %s", r.label, "OK" if r.ok else "MISSING", r.detail)

    api = SetupApi()
    window = webview.create_window(
        "Great Sage Setup", os.path.join(HERE, "installer.html"),
        width=620, height=610, resizable=False,
        background_color="#070d14", js_api=api,
    )
    api.attach(window)
    webview.start()
    return api.launch_requested


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    launched = show_setup()
    if not launched:
        return 1
    # Only reached when run directly; app.py is the normal entry point.
    import run_hud
    return run_hud.main()


if __name__ == "__main__":
    raise SystemExit(main())
