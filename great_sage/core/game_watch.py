r"""Notice when a game is running, so GAMING mode does not have to be
remembered (spec S38/S40, Phase 10).

Gaming mode already frees about 4GB the moment it is selected. The
problem with that is human: it only helps if you remember to switch
before launching, and again to switch back afterwards. This watches for
the condition instead.

WHAT IT WATCHES, AND WHY THAT SIGNAL

A borderless or exclusive fullscreen window that owns the foreground. It
is the one signal available without elevated permissions or a process
allowlist that would need updating for every game ever released:

  - nvidia-smi reports "[Insufficient Permissions]" for other users'
    processes, so GPU load cannot be attributed reliably
  - a list of known game executables is wrong the moment a new game
    appears, and wrong again for anything not on Steam

Deliberately conservative. It only fires when a window covers the ENTIRE
screen and is not one of the known non-games (Explorer, the desktop, and
Great Sage itself, which goes fullscreen legitimately). A video played
fullscreen in a browser will also match - and that is fine, because the
consequence is only that the model unloads and reloads a few seconds
later on the next question. The cost of a false positive is small; the
cost of missing a real one is a game sharing its card with 4GB it did not
need to.

It never overrides a deliberate choice: the mode in force before the game
started is remembered and restored afterwards, and if Krazaa changes mode
by hand while a game is running, that wins and the watcher stands down
until the next game.
"""

import ctypes
import ctypes.wintypes as wt
import logging
import threading

log = logging.getLogger(__name__)

# Windows that legitimately go fullscreen and are not games.
_IGNORE_TITLES = (
    "great sage", "program manager", "windows input experience",
    "search", "task switching", "start", "",
)


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def fullscreen_app():
    """Title of a window covering the whole screen, or None.

    Returns the title rather than a boolean so the caller can log WHAT it
    reacted to - a mode that changes itself with no explanation is worse
    than one that never changes.
    """
    try:
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return None
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(hwnd, buf, 512)
        title = (buf.value or "").strip()
        if title.lower() in _IGNORE_TITLES:
            return None
        rect = _RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        sw = u.GetSystemMetrics(0)
        sh = u.GetSystemMetrics(1)
        # >= rather than ==: a borderless window can exceed the work area
        # by a pixel or two.
        if w >= sw and h >= sh:
            return title
        return None
    except Exception:
        return None


class GameWatcher:
    """Calls on_change(title_or_None) when a game appears or disappears."""

    def __init__(self, on_change, poll_seconds: float = 5.0,
                 confirm_polls: int = 2):
        self._on_change = on_change
        self._poll = poll_seconds
        # Require the same answer twice before acting. Alt-tabbing through
        # a fullscreen window should not toggle the mode, and neither
        # should a loading screen that briefly loses focus.
        self._confirm = max(1, confirm_polls)
        self._thread = None
        self._stop = threading.Event()
        self.current = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        streak_value, streak = None, 0
        while not self._stop.is_set():
            found = fullscreen_app()
            if found == streak_value:
                streak += 1
            else:
                streak_value, streak = found, 1
            if streak >= self._confirm and streak_value != self.current:
                self.current = streak_value
                try:
                    self._on_change(self.current)
                except Exception:
                    log.exception("Game watcher callback failed")
            self._stop.wait(self._poll)
