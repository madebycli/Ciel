r"""A hotkey that works when Great Sage is not the focused window.

Spec S40: "The user must still be able to manually activate Great Sage...
Possible global hotkey: configurable by user. Example: Ctrl + Alt + S".

Krazaa's actual need is simpler and sharper: talk to it while playing a
game or writing in another application, without alt-tabbing first. A
keybind that only works when the window already has focus is no use for
that - by the time you have focused the window you could have clicked the
microphone.

HOLD TO TALK, WITHOUT A KEYBOARD HOOK
Hold-to-talk needs both edges, and RegisterHotKey only reports the press.
The obvious way to get the release is a low-level hook (WH_KEYBOARD_LL),
and that is still refused here: it means a callback on every keystroke the
machine receives, system-wide, which is both a performance tax and
indistinguishable from a keylogger to any anti-cheat worth the name - and
Krazaa games.

So the two edges come from two different places:

  PRESS    RegisterHotKey. Windows notifies us about ONE combination and
           nothing else, and it also swallows the key, so the "1" in
           Alt+1 does not additionally reach whatever game is focused.

  RELEASE  GetAsyncKeyState, polled - but ONLY while the key is actually
           down, which is the whole point. At rest this thread polls
           nothing and reads no key state; it sits on PeekMessage waiting
           for its one registered combination. The polling starts when the
           press arrives and stops when the key comes up, so there is no
           continuous system-wide key scanning to look suspicious, and it
           queries the ONE key we already know was pressed rather than
           reading the keyboard.

This was briefly a toggle - press to start, press again to send - because
a toggle needs only the press. Krazaa asked for the hold back.

The thread owns its own message loop because RegisterHotKey delivers
WM_HOTKEY to the thread that registered it, and that thread must be
pumping messages to receive them.
"""

import ctypes
import ctypes.wintypes as wt
import logging
import threading
import time

log = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000          # do not fire repeatedly while held
WM_HOTKEY = 0x0312

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL,
         "alt": MOD_ALT, "shift": MOD_SHIFT}


def parse(binding: str):
    """'ctrl+alt+s' -> (modifiers, virtual-key). None if unparseable."""
    parts = [p.strip().lower() for p in (binding or "").split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    key = None
    for part in parts:
        if part in _MODS:
            mods |= _MODS[part]
        elif len(part) == 1:
            key = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit():
            n = int(part[1:])
            if 1 <= n <= 24:
                key = 0x70 + n - 1          # VK_F1 .. VK_F24
    if key is None:
        return None
    return mods | MOD_NOREPEAT, key


class GlobalHotkey:
    """Hold-to-talk: `on_press` when the combination goes down anywhere,
    `on_release` when it comes back up."""

    # A hold that lasts this long is a stuck key, a missed release, or a
    # remote session that swallowed the key-up - not somebody genuinely
    # talking. Releasing on our own is far better than recording for ever.
    MAX_HOLD_S = 120.0

    def __init__(self, binding: str, on_press, on_release=None):
        self.binding = binding
        self._on_press = on_press
        self._on_release = on_release
        self._thread = None
        self._stop = threading.Event()
        self.active = False
        # RegisterHotKey runs on the worker thread, so start() has to wait
        # for its verdict rather than assume the thread starting means the
        # key was claimed. Without this, start() returned True for a
        # combination another application already owned, the UI reported
        # success, and the key was silently dead.
        self._ready = threading.Event()
        self._ok = False

    def start(self) -> bool:
        parsed = parse(self.binding)
        if parsed is None:
            log.warning("Global hotkey %r could not be parsed", self.binding)
            self._ok = False
            self._ready.set()
            return False
        self._stop.clear()
        self._ready.clear()
        self._ok = False
        self._thread = threading.Thread(target=self._run, args=parsed,
                                        daemon=True)
        self._thread.start()
        # The registration itself is immediate; the wait is only to cross
        # the thread boundary. A timeout means something is badly wrong,
        # and reporting failure is the safe answer either way.
        self._ready.wait(2.0)
        return self._ok

    def stop(self):
        self._stop.set()

    def rebind(self, binding: str) -> bool:
        """Point the hotkey at a different combination.

        The old registration is released first: Windows refuses to
        register a combination twice, so rebinding without unregistering
        would silently leave the OLD key working and the new one dead.
        """
        if (binding or "").strip().lower() == (self.binding or "").strip().lower():
            return True
        previous = self.binding
        self.stop()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.binding = binding
        if self.start():
            return True
        # The old registration was released before trying the new one, so
        # a failure here would otherwise leave NO working key at all -
        # the user picks a combination some other app owns and loses the
        # one that was working. Put the previous one back.
        log.warning("Keeping the previous hotkey %r; %r could not be "
                    "registered", previous, binding)
        self.binding = previous
        self.start()
        return False

    def _run(self, mods, key):
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, 1, mods, key):
            # Almost always means another application already owns it.
            log.warning("Could not register the global hotkey %r - another "
                        "application may already be using it", self.binding)
            self._ok = False
            self._ready.set()
            return
        self.active = True
        self._ok = True
        self._ready.set()
        log.info("Global hotkey active: %s (works from any window)",
                 self.binding)
        # SHORT, and the sign bit is the one we want - without an explicit
        # restype ctypes hands back a 32-bit int and the test misreads.
        user32.GetAsyncKeyState.restype = ctypes.c_short

        def key_is_down():
            return bool(user32.GetAsyncKeyState(key) & 0x8000)

        def fire(cb, what):
            if cb is None:
                return
            try:
                cb()
            except Exception:
                log.exception("Global hotkey %s handler failed", what)

        msg = wt.MSG()
        held = False
        held_since = 0.0
        try:
            while not self._stop.is_set():
                # PeekMessage rather than GetMessage: GetMessage blocks
                # forever, and this thread has to notice stop() so the app
                # can shut down instead of hanging on exit.
                got = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)
                if got and msg.message == WM_HOTKEY and not held:
                    held = True
                    held_since = time.monotonic()
                    fire(self._on_press, "press")
                elif held:
                    # Only ever reached while the key is genuinely down.
                    if not key_is_down():
                        held = False
                        fire(self._on_release, "release")
                    elif time.monotonic() - held_since > self.MAX_HOLD_S:
                        log.warning("Voice key held for over %.0fs - "
                                    "releasing it; the key-up was probably "
                                    "missed", self.MAX_HOLD_S)
                        held = False
                        fire(self._on_release, "release")
                if not got:
                    # 20ms while held is well under human release timing and
                    # costs nothing; the same wait when idle just keeps the
                    # loop responsive to stop().
                    self._stop.wait(0.02)
        finally:
            # Never leave the microphone open because the thread went away
            # mid-hold - on rebind, on shutdown, on anything.
            if held:
                fire(self._on_release, "release")
            user32.UnregisterHotKey(None, 1)
            self.active = False
            log.info("Global hotkey released")
