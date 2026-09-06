r"""A hotkey that works when Great Sage is not the focused window.

Spec S40: "The user must still be able to manually activate Great Sage...
Possible global hotkey: configurable by user. Example: Ctrl + Alt + S".

Krazaa's actual need is simpler and sharper: talk to it while playing a
game or writing in another application, without alt-tabbing first. A
keybind that only works when the window already has focus is no use for
that - by the time you have focused the window you could have clicked the
microphone.

WHY RegisterHotKey AND NOT A KEYBOARD HOOK
A low-level hook (WH_KEYBOARD_LL) would give key-down AND key-up, which
would allow true hold-to-talk. It also means a callback on every keystroke
the machine receives, system-wide, which is both a performance tax and
indistinguishable from a keylogger to any anti-cheat worth the name -
and Krazaa games. RegisterHotKey asks Windows to notify us about ONE
combination and nothing else.

The cost is that Windows reports the press but not the release, so this is
a TOGGLE: press to start listening, press again to stop and send. That is
also the better fit for gaming, where holding a key down would fight
whatever the game does with it.

The thread owns its own message loop because RegisterHotKey delivers
WM_HOTKEY to the thread that registered it, and that thread must be
pumping messages to receive them.
"""

import ctypes
import ctypes.wintypes as wt
import logging
import threading

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
    """Calls `on_press` whenever the combination is pressed, anywhere."""

    def __init__(self, binding: str, on_press):
        self.binding = binding
        self._on_press = on_press
        self._thread = None
        self._stop = threading.Event()
        self.active = False

    def start(self) -> bool:
        parsed = parse(self.binding)
        if parsed is None:
            log.warning("Global hotkey %r could not be parsed", self.binding)
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=parsed,
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()

    def _run(self, mods, key):
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, 1, mods, key):
            # Almost always means another application already owns it.
            log.warning("Could not register the global hotkey %r - another "
                        "application may already be using it", self.binding)
            return
        self.active = True
        log.info("Global hotkey active: %s (works from any window)",
                 self.binding)
        msg = wt.MSG()
        try:
            while not self._stop.is_set():
                # PeekMessage rather than GetMessage: GetMessage blocks
                # forever, and this thread has to notice stop() so the app
                # can shut down instead of hanging on exit.
                if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    if msg.message == WM_HOTKEY:
                        try:
                            self._on_press()
                        except Exception:
                            log.exception("Global hotkey handler failed")
                else:
                    self._stop.wait(0.03)
        finally:
            user32.UnregisterHotKey(None, 1)
            self.active = False
            log.info("Global hotkey released")
