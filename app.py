"""Great Sage - the single entry point, and what the packaged exe runs.

Checks prerequisites first. If anything essential is missing it shows the
setup window; otherwise it goes straight to the HUD, so a machine that is
already set up never sees a splash it has to click through.

Run from source:   py app.py
Force setup:       py app.py --setup

Everything is done in ONE process rather than launching a second one. In a
frozen build sys.executable is the exe itself, so re-launching a .py file
is not an option, and spawning a child would leave the parent alive with
the HUD unable to fully exit when closed.
"""

import logging
import os
import sys

# Frozen builds unpack next to the exe; from source this is the repo root.
BASE = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__)))
if getattr(sys, "frozen", False):
    BASE = getattr(sys, "_MEIPASS", BASE)
sys.path.insert(0, BASE)

# Relative paths in settings.py (the reference clip, voice_lines, the saved
# HUD settings) are resolved against the working directory, which is
# wherever the user launched the exe from - not where the app lives.
os.chdir(BASE)

log = logging.getLogger("great_sage.app")


# Next to the exe, not inside the bundle: _MEIPASS is an implementation
# detail a tester will never find, and for onefile builds it is deleted on
# exit. This is the file to ask for when something goes wrong.
LOG_PATH = os.path.join(
    os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else BASE,
    "great_sage.log")


def _configure_logging() -> None:
    """One logging setup for the whole process, established FIRST.

    force=True matters: logging.basicConfig is a no-op once any handler
    exists, so whichever module called it first used to win. app.py called
    it before run_hud did, which quietly disabled run_hud's file handler -
    and a windowed exe has no console, so every error after that point went
    nowhere at all. That is why the packaged build's voice failure looked
    like silence rather than an error.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
        force=True,
    )


def main() -> int:
    # The transparent overlay runs as its own process, and in a frozen
    # build there is no overlay_window.py to launch - so the exe re-invokes
    # ITSELF with this flag and becomes the Qt host instead. Checked before
    # anything else, since none of the HUD's startup applies to it.
    if "--overlay" in sys.argv:
        import overlay_window
        sys.argv = [a for a in sys.argv if a != "--overlay"]
        return overlay_window.main()

    _configure_logging()
    log.info("Great Sage starting (frozen=%s, base=%s)",
             getattr(sys, "frozen", False), BASE)

    from great_sage import preflight

    force = "--setup" in sys.argv
    blocking = preflight.blocking_failures()
    if blocking:
        log.info("Missing prerequisites: %s",
                 ", ".join(r.label for r in blocking))

    if force or blocking:
        import installer
        if not installer.show_setup():
            # Closed the setup window without launching. Not an error when
            # they only wanted to look, so only a genuine unmet requirement
            # reports failure.
            return 1 if preflight.blocking_failures() else 0

    import run_hud
    return run_hud.main()


if __name__ == "__main__":
    # A windowed build has nowhere to print a traceback, so an unhandled
    # exception would close the window with no explanation at all. Catch
    # it, write it where a tester can find it, and say so on the way out.
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        import traceback
        try:
            _configure_logging()
        except Exception:
            pass
        logging.getLogger("great_sage.app").critical(
            "Great Sage failed to start:\n%s", traceback.format_exc())
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                "Great Sage could not start.\n\nDetails were written to:\n"
                + LOG_PATH,
                "Great Sage", 0x10)
        except Exception:
            pass
        raise SystemExit(2)
