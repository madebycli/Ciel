"""
Great Sage HUD - native app entry point.

Opens hud_prototype.html in its own window (via pywebview) and runs the
local WebSocket bridge (great_sage/server.py) that connects it to the
real ChatEngine + voice output, replacing the HUD's canned test-button
behavior with the actual AI.

This is additive: main.py's CLI mode (`py main.py`) is untouched and
still works exactly as before. Run this instead with `py run_hud.py`.
"""

import asyncio
import logging
import os
import sys
import threading
import time

import webview

from great_sage.config import settings
from great_sage.core.chat_engine import ChatEngine
from great_sage.models.base import ModelProviderError
from great_sage.models.ollama_provider import OllamaProvider
from great_sage.log_broadcast import HistoryLogHandler
from great_sage.server import run_server
from great_sage.voice.base import VoiceError
from main import (
    build_disabled_voice_line_patterns,
    build_memory_callback,
    build_recall,
    build_system_prompt,
    build_voice_lines,
)

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "great_sage_hud.log")

# Module-level logger. _configure_logging() binds a LOCAL `log` for its own
# use, which is not visible out here - so anything at module scope that
# logged (the overlay window controls below) raised NameError from inside
# its own except block, swallowing the real error it was trying to report.
log = logging.getLogger(__name__)


# Overlay geometry. Square because the mini visual is a 1:1 composition -
# a non-square window would letterbox it or squash the projection.
OVERLAY_SIZE = 340
OVERLAY_MARGIN = 8
FULL_SIZE = (1920, 1080)

# Win32 constants for toggling the window frame at runtime. pywebview can
# only set `frameless` when the window is CREATED, and recreating the
# window would reload the page - losing the WebSocket, the scene, and the
# conversation. Editing the style bits in place keeps one window and one
# page across the switch, which is the whole point.
_GWL_STYLE = -16
_WS_OVERLAPPEDWINDOW = 0x00CF0000
_WS_POPUP = 0x80000000
_WS_THICKFRAME = 0x00040000   # the sizing border, without a caption
_SWP_FLAGS = 0x0020 | 0x0002 | 0x0001 | 0x0004   # FRAMECHANGED|NOMOVE|NOSIZE|NOZORDER


class _HudHostApi:
    """Window controls the page cannot reach on its own.

    Exposed to JS as window.pywebview.api. Every method is defensive: the
    page treats the bridge as optional (it still switches its own visuals
    when opened in a plain browser), and a failure here must never take
    the HUD down with it.
    """

    def __init__(self):
        self._window = None
        self._cached_hwnd = None
        self._maximized = False
        self._overlay_proc = None

    def attach(self, window) -> None:
        self._window = window

    def _hwnd(self):
        """The native window handle, or None if it cannot be determined.

        Asks pywebview for OUR window's handle first, and only falls back
        to a title search. The fallback alone was a bug: FindWindowW
        returns the FIRST window with a matching title, so a second copy
        of the app - or a leftover probe window - meant these calls
        resized and restyled the wrong window entirely.
        """
        if self._cached_hwnd:
            return self._cached_hwnd
        win = self._window
        for getter in (
            lambda: win.native.Handle.ToInt64(),   # WinForms (EdgeChromium)
            lambda: int(win.native.winfo_id()),    # tk
            lambda: int(win.native.effective_win_id),
        ):
            try:
                h = getter()
                if h:
                    self._cached_hwnd = h
                    return h
            except Exception:
                continue
        try:
            import ctypes
            h = ctypes.windll.user32.FindWindowW(None, "Great Sage")
            if h:
                log.warning("Falling back to a title search for the window "
                            "handle; a second instance would be ambiguous.")
                self._cached_hwnd = h
                return h
        except Exception:
            pass
        return None

    def start_drag(self) -> bool:
        """Let Windows drag the window, as though the page were its caption.

        Releasing the mouse capture and posting WM_NCLBUTTONDOWN/HTCAPTION
        hands the drag to the OS, which then moves the window itself. The
        alternative - streaming pointermove deltas into window.move() over
        the JS bridge - puts a round trip in the middle of every mouse
        movement and drags visibly behind the cursor.
        """
        hwnd = self._hwnd()
        if not hwnd:
            log.warning("start_drag: no window handle")
            return False

        def _drag():
            import ctypes
            u = ctypes.windll.user32
            # ReleaseCapture releases the mouse for the CALLING thread, and
            # WM_NCLBUTTONDOWN starts a modal move loop owned by the window's
            # thread. Both are thread-affine, so running them on pywebview's
            # JS-API worker thread (which is where this method is invoked)
            # did nothing at all - no error, no drag. Hence the Invoke below.
            u.ReleaseCapture()
            _WM_NCLBUTTONDOWN, _HTCAPTION = 0x00A1, 2
            u.SendMessageW(ctypes.c_void_p(hwnd), _WM_NCLBUTTONDOWN,
                           _HTCAPTION, 0)

        try:
            native = getattr(self._window, "native", None)
            if native is not None and hasattr(native, "Invoke"):
                from System import Action
                # BeginInvoke, not Invoke: WM_NCLBUTTONDOWN runs a modal
                # drag loop that does not return until the mouse is
                # released, so a blocking Invoke would deadlock this
                # worker thread for the whole drag.
                native.BeginInvoke(Action(_drag))
            else:
                log.warning("start_drag: no Invoke on native window; "
                            "calling inline (may not work)")
                _drag()
            log.info("start_drag dispatched")
            return True
        except Exception:
            log.exception("start_drag failed")
            return False

    def _set_frameless(self, frameless: bool) -> bool:
        hwnd = self._hwnd()
        if not hwnd:
            return False
        try:
            import ctypes
            user32 = ctypes.windll.user32

            # argtypes MUST be declared. Without them ctypes infers a
            # signed 32-bit int for the style argument, and WS_POPUP is
            # 0x80000000 - one past that range - so every call raised
            # "OverflowError: int too long to convert" and the frame never
            # changed. LONG_PTR is pointer-sized, hence c_ssize_t.
            get_l = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
            set_l = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
            get_l.restype = ctypes.c_ssize_t
            get_l.argtypes = [ctypes.c_void_p, ctypes.c_int]
            set_l.restype = ctypes.c_ssize_t
            set_l.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            user32.SetWindowPos.restype = ctypes.c_bool
            user32.SetWindowPos.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint,
            ]

            style = get_l(hwnd, _GWL_STYLE)
            style = (style & ~_WS_OVERLAPPEDWINDOW) | _WS_POPUP if frameless \
                else (style & ~_WS_POPUP) | _WS_OVERLAPPEDWINDOW
            set_l(hwnd, _GWL_STYLE, style)
            # Without FRAMECHANGED the new style is stored but not drawn.
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, _SWP_FLAGS)
            return True
        except Exception:
            log.exception("Could not toggle the window frame")
            return False

    def _work_area(self, hwnd):
        """(left, top, right, bottom) of the work area, in PHYSICAL pixels.

        Work area, not the full monitor rect, so the overlay tucks under
        the taskbar rather than behind it. Taken from the monitor the
        window is currently on, so moving the app to a second display and
        toggling still puts it in that display's corner.

        Replaces webview.screens, which was raising and being swallowed by
        an except - which is why the overlay kept landing at the default
        cascade position (78,78) instead of a corner.
        """
        try:
            import ctypes

            class _RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            class _MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                            ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]

            u = ctypes.windll.user32
            mon = u.MonitorFromWindow(ctypes.c_void_p(hwnd), 2)  # NEAREST
            mi = _MONITORINFO()
            mi.cbSize = ctypes.sizeof(_MONITORINFO)
            if not u.GetMonitorInfoW(mon, ctypes.byref(mi)):
                return None
            w = mi.rcWork
            return (w.left, w.top, w.right, w.bottom)
        except Exception:
            log.exception("Could not read the monitor work area")
            return None

    def _window_size(self, hwnd):
        """Actual outer size in PHYSICAL pixels, after any DPI scaling.

        Read rather than assumed: win.resize() takes LOGICAL pixels, so on
        a scaled display a 340 request is not 340 physical, and positioning
        from the requested number would sit the window off the corner by
        the scale factor.
        """
        import ctypes

        class _R(ctypes.Structure):
            _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                        ("r", ctypes.c_long), ("b", ctypes.c_long)]

        r = _R()
        ctypes.windll.user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r))
        return (r.r - r.l, r.b - r.t)

    def _place(self, hwnd, where: str) -> bool:
        """Move (never resize) the window to a corner or the centre."""
        area = self._work_area(hwnd)
        if not area:
            return False
        left, top, right, bottom = area
        w, h = self._window_size(hwnd)
        if where == "top-right":
            x = right - w - OVERLAY_MARGIN
            y = top + OVERLAY_MARGIN
        else:                                   # centre
            x = left + max(0, (right - left - w) // 2)
            y = top + max(0, (bottom - top - h) // 2)
        import ctypes
        # SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE - size was already set by
        # resize(), and re-asserting it here would fight pywebview's DPI
        # handling.
        ok = ctypes.windll.user32.SetWindowPos(
            ctypes.c_void_p(hwnd), None, int(x), int(y), 0, 0,
            0x0001 | 0x0004 | 0x0010)
        log.info("Placed window %s at (%d,%d) size %dx%d", where, x, y, w, h)
        return bool(ok)

    def set_frame(self, framed: bool) -> bool:
        """Public wrapper, so startup can restore the frame."""
        return self._set_frameless(not framed)

    def fix_host_background(self) -> bool:
        """Repaint the WinForms host behind the page.

        This is THE white background. pywebview's winforms backend does:

            if window.transparent and self.browser:
                self.SetStyle(SupportsTransparentBackColor, True)
                self.browser.DefaultBackgroundColor = Color.Transparent
            else:
                self.BackColor = ColorTranslator.FromHtml(background_color)

        BackColor is set ONLY in the else branch - so asking for a
        transparent window leaves the Form at its WinForms default,
        SystemColors.Control, measured here as (240,240,240). That is the
        white square behind the overlay, and it is also why the
        background_color passed to create_window never took effect.

        True per-pixel transparency is not reachable from this stack, and
        was not for want of trying - transparent=True alone, WebView2's
        DefaultBackgroundColor, AllowTransparency + TransparencyKey,
        disabling GPU compositing, and DWM blur-behind were each measured
        against a magenta backdrop and each left the window opaque.
        WebView2 renders on its own child HWND through DirectComposition,
        which none of those mechanisms reach; real alpha needs composition
        hosting (CoreWebView2CompositionController), which pywebview does
        not use.

        So the host is painted the HUD's own dark colour instead. The
        overlay then reads as a dark panel rather than a white box - which
        is what the reference art looked like anyway.
        """
        try:
            from System.Drawing import ColorTranslator
            from System import Action
            native = getattr(self._window, "native", None)
            if native is None:
                return False
            colour = getattr(settings, "HUD_BACKGROUND_COLOR", "#070d14")

            def on_ui():
                native.BackColor = ColorTranslator.FromHtml(colour)

            if hasattr(native, "Invoke"):
                native.Invoke(Action(on_ui))   # BackColor is UI-thread only
            else:
                on_ui()
            log.info("Host window background set to %s (was the WinForms "
                     "default, 240,240,240)", colour)
            return True
        except Exception:
            log.exception("fix_host_background failed")
            return False

    def set_resizable(self, resizable: bool) -> bool:
        """Native resize borders, without bringing the title bar back.

        WS_THICKFRAME is the "sizing border" style. Windows then handles
        hit-testing and resizing on all four edges and corners itself -
        cursors, snapping, aero-snap and all.

        Doing it with the style bit rather than JS edge handles is
        deliberate: the page would have to catch a press on a 4px strip and
        ask Windows to start a resize with WM_NCLBUTTONDOWN, and that is
        exactly the mechanism that failed for dragging - a click inside the
        WebView2 CHILD window leaves capture with the child, so the modal
        loop never sees the button release and the window sticks to the
        cursor. WS_THICKFRAME is handled entirely outside the webview, so
        that problem cannot arise.

        WS_CAPTION is deliberately NOT included, so there is still no
        title bar - the HUD keeps drawing its own controls.
        """
        hwnd = self._hwnd()
        if not hwnd:
            return False
        try:
            import ctypes
            u = ctypes.windll.user32
            get_l = getattr(u, "GetWindowLongPtrW", None) or u.GetWindowLongW
            set_l = getattr(u, "SetWindowLongPtrW", None) or u.SetWindowLongW
            get_l.restype = ctypes.c_ssize_t
            get_l.argtypes = [ctypes.c_void_p, ctypes.c_int]
            set_l.restype = ctypes.c_ssize_t
            set_l.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            style = get_l(hwnd, _GWL_STYLE)
            style = (style | _WS_THICKFRAME) if resizable \
                else (style & ~_WS_THICKFRAME)
            set_l(hwnd, _GWL_STYLE, style)
            u.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0, _SWP_FLAGS)
            log.info("Resize border %s (style now 0x%08X)",
                     "on" if resizable else "off", style & 0xFFFFFFFF)
            return True
        except Exception:
            log.exception("set_resizable failed")
            return False

    # ---- window controls, drawn by the HUD itself ----------------------
    # The native title bar is gone in both modes now (transparent=True
    # creates the window as a bare WS_POPUP anyway), so the page draws its
    # own minimise / maximise / close and calls these.
    def minimize(self) -> bool:
        try:
            self._window.minimize()
            return True
        except Exception:
            log.exception("minimize failed")
            return False

    def toggle_maximize(self) -> bool:
        """Maximise, or restore if already maximised.

        Tracked here rather than asked of the window: pywebview exposes
        maximize() and restore() but no "is it maximised" flag, so the
        page would otherwise have no way to know which one to call.
        """
        try:
            if self._maximized:
                self._window.restore()
            else:
                self._window.maximize()
            self._maximized = not self._maximized
            return self._maximized
        except Exception:
            log.exception("toggle_maximize failed")
            return self._maximized

    def close_app(self) -> bool:
        try:
            self._window.destroy()
            return True
        except Exception:
            log.exception("close failed")
            return False

    # ---- the transparent overlay, hosted in its own process -----------
    def _overlay_command(self):
        """How to launch overlay_window.py, frozen or from source."""
        if getattr(sys, "frozen", False):
            # Frozen, the overlay is a SEPARATE exe sitting in overlay/
            # beside this one, because it is built against PySide6 6.4.3
            # on Python 3.11 while this bundle is 3.14. See overlay.spec.
            #
            # This used to re-invoke sys.executable with --overlay, which
            # only works while both halves share one interpreter. They
            # cannot: 6.4.3 has no 3.14 build, and every newer PySide6
            # flickers.
            here = os.path.dirname(sys.executable)
            overlay_exe = os.path.join(here, "overlay", "GreatSageOverlay.exe")
            if os.path.exists(overlay_exe):
                return [overlay_exe, "--size", str(OVERLAY_SIZE)]
            # Missing overlay/ means an incomplete extraction rather than
            # a configuration to fall back from. Say so instead of
            # spawning this exe with a flag it can no longer honour, which
            # would start a second copy of the whole app.
            log.error("Overlay executable not found at %s - overlay mode "
                      "is unavailable in this build", overlay_exe)
            return None

        here = os.path.dirname(os.path.abspath(__file__))
        script = os.path.join(here, "overlay_window.py")

        # If an overlay venv exists, run the overlay under THAT interpreter
        # instead of this one. It exists to test older PySide6 builds
        # against the flicker: PySide6 6.4.3 predates the ANGLE switch that
        # is the suspected cause, but it does not support Python 3.14, so
        # it lives in a Python 3.11 venv.
        #
        # Only the overlay moves. The app keeps running on 3.14 with torch,
        # F5-TTS and the whole voice stack untouched - which is possible
        # only because the overlay is already a separate process.
        venv_python = os.path.join(here, ".overlay-venv", "Scripts", "python.exe")
        interpreter = venv_python if os.path.exists(venv_python) else sys.executable
        if interpreter != sys.executable:
            log.info("Overlay will run under %s", venv_python)
        return [interpreter, script, "--size", str(OVERLAY_SIZE)]

    def _watch_overlay(self, proc):
        """Restore the main window when the overlay closes on its own.

        The overlay's own exit control ends its process, so without this
        the app would be left with every window hidden and no way back.
        """
        proc.wait()
        log.info("Overlay process exited (%s); restoring the main window",
                 proc.returncode)
        self._overlay_proc = None
        try:
            self._window.show()
            self._focus_window()
        except Exception:
            log.exception("Could not restore the main window")

    def _focus_window(self) -> bool:
        """Bring the main window to the front and give it focus.

        show() alone only makes it visible - it comes back behind whatever
        the user clicked while the overlay was up, which reads as the
        overlay having closed into nothing.

        SetForegroundWindow refuses to work for a process that does not
        already own the foreground, and this one just lost it when the
        overlay process exited. The topmost flip is the standard way
        around that: briefly mark the window always-on-top so Windows
        raises it, then immediately drop the flag so it does not stay
        pinned above everything else.
        """
        hwnd = self._hwnd()
        if not hwnd:
            return False
        try:
            import ctypes
            u = ctypes.windll.user32
            _SW_RESTORE = 9
            _HWND_TOPMOST, _HWND_NOTOPMOST = -1, -2
            _SWP = 0x0001 | 0x0002 | 0x0040   # NOSIZE|NOMOVE|SHOWWINDOW
            u.ShowWindow(ctypes.c_void_p(hwnd), _SW_RESTORE)
            u.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(_HWND_TOPMOST),
                           0, 0, 0, 0, _SWP)
            u.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(_HWND_NOTOPMOST),
                           0, 0, 0, 0, _SWP)
            u.SetForegroundWindow(ctypes.c_void_p(hwnd))
            log.info("Main window raised and focused")
            return True
        except Exception:
            log.exception("_focus_window failed")
            return False

    def set_overlay(self, on: bool) -> bool:
        """Switch between the main window and the transparent overlay.

        The overlay is a SEPARATE PROCESS (overlay_window.py, PySide6 +
        QWebEngineView) rather than a resize of this window, because this
        window cannot be transparent. pywebview hosts WebView2 in windowed
        mode, which renders into a child HWND, and per-pixel alpha needs a
        composited top-level window - measured against a magenta backdrop,
        seven different approaches all came back opaque.

        Qt can do it: same Chromium engine, so the Three.js scene is
        identical, but hosted in a window that composites with alpha.
        Verified - the backdrop shows through the empty areas.

        Two processes rather than one because Qt and WinForms each want to
        own the GUI event loop, and because it keeps the main HUD on the
        stack that already works: if the overlay host fails, the app it
        was launched from is untouched.
        """
        import subprocess
        win = self._window
        if win is None:
            return False
        try:
            if on:
                if self._overlay_proc is None:
                    cmd = self._overlay_command()
                    if cmd is None:
                        # Overlay unavailable in this build. Leave the main
                        # window up rather than hiding it behind nothing -
                        # hiding first and failing after is how the user
                        # ends up with no window at all.
                        return False
                    self._overlay_proc = subprocess.Popen(
                        cmd, cwd=os.path.dirname(os.path.abspath(__file__)))
                    threading.Thread(target=self._watch_overlay,
                                     args=(self._overlay_proc,),
                                     daemon=True).start()
                    log.info("Overlay process started (pid %s)",
                             self._overlay_proc.pid)
                win.hide()
            else:
                if self._overlay_proc is not None:
                    self._overlay_proc.terminate()
                    self._overlay_proc = None
                win.show()
            return True
        except Exception:
            log.exception("set_overlay(%s) failed", on)
            try:
                win.show()      # never leave every window hidden
            except Exception:
                pass
            return False



def _configure_logging() -> None:
    """Writes to great_sage_hud.log (readable after the fact, since this
    runs as a detached GUI window with no visible console) and echoes to
    stdout too, in case that IS being captured somewhere."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
            # Buffers from here on, so the HUD's LOGS console can show
            # what happened before it was opened.
            HistoryLogHandler(),
        ],
    )


def _build_voice(provider=None):
    log = logging.getLogger(__name__)
    if not settings.VOICE_ENABLED:
        return None
    if settings.VOICE_ENGINE not in ("pocket", "f5"):
        log.warning(
            "VOICE_ENGINE=%r isn't supported by the HUD app (only 'pocket' "
            "and 'f5' stream audio to the browser) - running text-only.",
            settings.VOICE_ENGINE,
        )
        return None

    if settings.VOICE_ENGINE == "f5":
        from great_sage.voice.f5_tts_engine import F5TTSVoiceOutput

        try:
            return F5TTSVoiceOutput(
                reference_audio_path=settings.F5_REFERENCE_AUDIO_PATH,
                voice_lines=build_voice_lines(),
                disabled_voice_line_patterns=build_disabled_voice_line_patterns(),
                nfe_step=settings.F5_NFE_STEP,
                single_shot=settings.VOICE_SINGLE_SHOT,
            )
        except VoiceError as exc:
            # F5 needs a GPU and a multi-GB model; falling back to the
            # CPU-only engine keeps the app usable rather than silently
            # losing its voice entirely.
            log.warning("F5-TTS unavailable (%s) - falling back to Pocket TTS.", exc)

    from great_sage.voice.pocket_tts_engine import PocketTTSVoiceOutput

    try:
        return PocketTTSVoiceOutput(
            reference_audio_path=settings.CLONE_REFERENCE_AUDIO_PATH,
            voice_lines=build_voice_lines(),
            disabled_voice_line_patterns=build_disabled_voice_line_patterns(),
        )
    except VoiceError as exc:
        log.warning("Voice unavailable (%s) - continuing in text-only mode.", exc)
        return None


def _start_server_thread(engine, voice) -> None:
    def runner():
        asyncio.run(run_server(engine, voice))

    threading.Thread(target=runner, daemon=True).start()


def main() -> int:
    _configure_logging()
    logging.getLogger(__name__).info("Starting Great Sage HUD")

    provider = OllamaProvider(
        host=settings.OLLAMA_HOST,
        model=settings.OLLAMA_DEFAULT_MODEL,
        timeout=settings.REQUEST_TIMEOUT_SECONDS,
        think=settings.OLLAMA_THINK,
    )
    try:
        provider.get_available_models()
    except ModelProviderError as exc:
        logging.getLogger(__name__).error("Startup error: %s", exc)
        return 1

    engine = ChatEngine(
        provider,
        build_system_prompt(),
        idle_reset_seconds=settings.SESSION_IDLE_RESET_MINUTES * 60,
        on_session_boundary=build_memory_callback(provider),
        recall=build_recall(provider),
    )
    voice = _build_voice(provider)

    _start_server_thread(engine, voice)

    # The HUD routes all speech through a Web Audio graph (for the
    # amplitude-reactive pulse), and Chromium starts every AudioContext
    # suspended until a user gesture - which meant a reply triggered by
    # push-to-talk, the wake word, or the startup greeting was silent AND
    # never fired 'ended', hanging the Python side on its ack for 30s.
    # The page resumes the context defensively too, but a gesture-less
    # startup greeting can only work if the embedded browser is told the
    # policy doesn't apply. This is our own window, not a web page, so
    # autoplay is exactly what we want. Must be set before webview.start()
    # creates the WebView2 environment, which reads it once.
    os.environ.setdefault(
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
        "--autoplay-policy=no-user-gesture-required",
    )

    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hud_prototype.html")
    api = _HudHostApi()
    # transparent=True is set even though the app starts full-size, because
    # pywebview only honours it at CREATION - there is no runtime toggle,
    # same as `frameless`. It costs nothing here: the page paints its own
    # opaque background in normal mode, so the window only actually reads
    # as transparent once overlay mode makes html/body transparent and
    # stops rendering the background wash.
    # frameless=True is what actually removes the Windows title bar, and it
    # can ONLY be set here - pywebview offers no runtime toggle. Measured:
    # transparent=True alone still creates a framed window (style
    # 0x16CF0000, WS_CAPTION set), which left the native bar sitting above
    # the HUD's own controls.
    #
    # With no frame there is nothing native to drag or close by, so the
    # page MUST provide both: #win-controls draws minimise/maximise/close
    # and #win-drag is the grab strip, all wired to _HudHostApi.
    window = webview.create_window(
        "Great Sage", html_path, width=1920, height=1080,
        background_color="#030b08", js_api=api, transparent=True,
        frameless=True,
        # easy_drag defaults to TRUE for frameless windows, which makes the
        # ENTIRE window a drag surface - press anywhere on the visual and it
        # moves. Measured: a press in the dead centre moved the window by
        # (151,79). The page defines its own drag region instead (the
        # #win-drag strip, via app-region), so this must be off or it
        # overrides that and there is no way to click anything.
        easy_drag=False,
        # Native sizing borders on all edges and corners. pywebview applies
        # this at creation, unlike the WS_THICKFRAME bit set later, which
        # never took effect because the WebView2 child window covers the
        # whole client area and swallows the frame's hit-testing.
        resizable=True,
    )
    api.attach(window)

    # Centre the window once the page is up.
    #
    # Not cosmetic: a frameless window that opens partly off-screen cannot
    # be dragged back, because the only grab strip is inside the page and
    # the title bar Windows would normally provide is gone. Measured on
    # this machine, the first frameless build opened at -432,1287 on a
    # 2560x1440 desktop - most of it below the bottom edge.
    #
    # _place() uses the monitor WORK AREA, so it also keeps clear of the
    # taskbar.
    def _centre_window():
        hwnd = api._hwnd()
        if hwnd:
            api._place(hwnd, "centre")
            # Restores edge/corner resizing that went away with the frame.
            api.set_resizable(True)
        api.fix_host_background()

    try:
        window.events.loaded += _centre_window
    except Exception:
        log.exception("Could not hook the loaded event to centre the window")
    # Devtools OFF by default. debug=True enables WebView2's inspector,
    # which is why F12 or a stray right-click opened a developer window
    # mid-use - fine while building, wrong for anyone testing the app.
    #
    # Set GREAT_SAGE_DEBUG=1 to get it back when debugging the page.
    debug = os.environ.get("GREAT_SAGE_DEBUG", "").strip() not in ("", "0")
    if debug:
        log.info("Devtools enabled (GREAT_SAGE_DEBUG is set)")
    webview.start(debug=debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
