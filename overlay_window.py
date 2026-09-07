"""Transparent desktop overlay host, run as its own process.

Great Sage's main window is pywebview + WinForms + WebView2, and that
stack CANNOT produce a transparent window: WebView2 renders into a child
HWND, and per-pixel alpha needs a composited TOP-LEVEL window. Measured
against a magenta backdrop, seven approaches all came back opaque -
transparent=True, WebView2's DefaultBackgroundColor, AllowTransparency
plus TransparencyKey, disabling GPU compositing, DWM blur-behind, and
re-parenting the render HWND.

Qt can. QWebEngineView is the same Chromium engine, so the Three.js
scene, the shaders and the WebGL context are all unchanged - only the
window hosting it differs. Verified before this file existed: over a
magenta backdrop, a WebGL canvas rendered correctly AND the backdrop
showed through the empty areas.

Run as a SEPARATE PROCESS on purpose. The main HUD keeps running exactly
as it does today, on the stack that already works; if this host has
trouble, the app it was launched from is untouched. It is also the only
way to have both, since Qt and WinForms each want to own the process's
GUI event loop.

    py overlay_window.py [--size 340] [--url http://...]

VERSION: PySide6 6.4.3, from the .overlay-venv (Python 3.11). This is
what FIXED the flicker, confirmed in use.

Qt 6.5.1 moved QtWebEngine to the ANGLE backend and translucent windows
have flickered on it since; 6.4.3 predates that. It cannot be installed
on Python 3.14 (PySide6 supports 3.14 only from 6.10), which is why the
overlay runs under its own interpreter - possible only because it is
already a separate process. run_hud.py prefers .overlay-venv when present.

Do NOT try to dodge the flicker by selecting a different graphics
backend: ANGLE is also what makes the window transparent here, and every
alternative either lost the transparency or stopped rendering entirely.

Also applied, both from the same investigation: WS_BORDER after show()
(QTBUG-51093) and AA_ShareOpenGLContexts + an alpha buffer in the default
surface format.
"""

import argparse
import os
import sys

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QSurfaceFormat
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView

# Frozen, the overlay is its OWN exe (see overlay.spec) - built on Python
# 3.11 so it can carry PySide6 6.4.3, while the main app stays on 3.14.
# PyInstaller unpacks the bundled hud_prototype.html and vendor/ next to
# _MEIPASS, which is not where __file__ points once the module is inside
# the archive, so ask PyInstaller rather than infer.
FROZEN = getattr(sys, "frozen", False)
HERE = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))

# The page checks for this and starts in overlay mode, so the host does
# not have to inject script or wait for the scene to build.
MINI_QUERY = "?mini=1"

# The page sets document.title to this when its exit control is used.
# titleChanged is the simplest reliable page->host signal that needs no
# QWebChannel plumbing, and the title is invisible on a frameless window.
EXIT_SENTINEL = "GS_EXIT_OVERLAY"

# The page sets this once it has switched to overlay visuals. The
# window stays HIDDEN until then: shown immediately, it displays the
# full-size HUD - test controls, window buttons, chat bar - for the
# second or so it takes the scene to build and mini mode to apply,
# which flashes a completely wrong window every time the overlay opens.
READY_SENTINEL = "GS_OVERLAY_READY"

# The overlay asks for a settings/history window with this prefix followed
# by the section name. Same title channel as the other two signals.
OPEN_PANEL_PREFIX = "GS_OPEN_PANEL:"

# Panel windows are ordinary opaque windows - they show forms and lists,
# not a floating visual, so transparency would only hurt readability.
PANEL_SIZE = (560, 640)

# Tight to the corner. The overlay is meant to tuck out of the way, and
# 24px read as floating loose beside the edge.
MARGIN = 8


# The drag handle's box, in window coordinates. The page draws a matching
# affordance at the same spot (see #mini-drag in hud_prototype.html) that
# fades in on hover; this is the half that decides whether a press starts
# a drag.
#
# Measured from the window's TOP-RIGHT corner, so it sits immediately
# left of the exit control and the two read as one pair of buttons.
# #mini-drag-handle in hud_prototype.html draws the visible affordance at
# the same spot - these must agree, or the handle points somewhere the
# host does not accept a drag.
HANDLE_INSET_RIGHT, HANDLE_TOP, HANDLE_W, HANDLE_H = 48, 2, 26, 26

# ---- click-through -------------------------------------------------
# The overlay is a 340px SQUARE, but almost all of it is transparent - the
# visual inside is a core with a wireframe around it. Krazaa could not
# click the browser tabs underneath, because an always-on-top window eats
# every click that lands anywhere in its rectangle, visible or not.
#
# So the window is click-through EXCEPT where there is something to hit.
# WS_EX_TRANSPARENT does that at the OS level: clicks fall through to
# whatever is behind.
#
# It has to be POLLED rather than driven by mouse events, and that is not
# laziness: a click-through window receives no mouse events at all, so
# once it is transparent nothing would ever tell it the cursor had come
# back. The cursor position is global, so a timer can see it regardless.
CLICK_THROUGH_POLL_MS = 50
WS_EX_TRANSPARENT = 0x00000020
GWL_EXSTYLE = -20
# Fraction of the window's width, from the centre, that counts as the
# core. Big enough to click without aiming, small enough that the corners
# - which is where the tabs and buttons underneath are - stay usable.
CORE_HIT_FRACTION = 0.26
# The page says when the whole window must be live: the radial menu is
# open, or the speech bubble is showing. Both extend well past the core.
HIT_ALL_SENTINEL = "GS_HIT_ALL"
HIT_CORE_SENTINEL = "GS_HIT_CORE"


class OverlayView(QWebEngineView):
    """Frameless, always-on-top, transparent, dragged by its handle."""

    def __init__(self, size: int):
        super().__init__()
        self._drag_from = None

        # The three settings that make it genuinely see-through. All are
        # required: dropping any one leaves an opaque window.
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool          # keeps it out of the taskbar and alt-tab
        )
        self.page().setBackgroundColor(QColor(Qt.transparent))
        # Qt must not paint its own background before Chromium draws. On a
        # translucent window that pre-paint is a candidate for the visible
        # flicker, since it briefly shows a frame the web content has not
        # filled yet.
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.resize(size, size)

        self._filtered = None
        self.titleChanged.connect(self._on_title)
        # The render widget does not exist yet at construction time, so the
        # filter is installed once the page has loaded (and again on show).
        self.loadFinished.connect(lambda _ok: self._install_mouse_filter())

        # Click-through state. Starts None so the first poll always
        # applies a style rather than assuming one.
        self._hit_all = False
        self._placed = False
        self._click_through = None
        self._ct_timer = QTimer(self)
        self._ct_timer.timeout.connect(self._update_click_through)
        self._ct_timer.start(CLICK_THROUGH_POLL_MS)

    # ---- click-through --------------------------------------------
    def _interactive_at(self, gpos) -> bool:
        """Is there anything to hit at this screen position?"""
        if self._hit_all:
            return True
        top = self.frameGeometry().topLeft()
        x, y = gpos.x() - top.x(), gpos.y() - top.y()
        w, h = self.width(), self.height()
        if not (0 <= x <= w and 0 <= y <= h):
            return False
        # The drag handle and the exit cross, both top-right.
        hx = w - HANDLE_INSET_RIGHT
        if hx <= x <= hx + HANDLE_W and HANDLE_TOP <= y <= HANDLE_TOP + HANDLE_H:
            return True
        if x >= w - 24 and y <= 24:
            return True
        # The core itself.
        dx, dy = x - w / 2.0, y - h / 2.0
        r = w * CORE_HIT_FRACTION
        return (dx * dx + dy * dy) <= r * r

    def _set_click_through(self, on: bool):
        if on == self._click_through:
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            u = ctypes.windll.user32
            get_l = getattr(u, "GetWindowLongPtrW", None) or u.GetWindowLongW
            set_l = getattr(u, "SetWindowLongPtrW", None) or u.SetWindowLongW
            style = get_l(hwnd, GWL_EXSTYLE)
            style = (style | WS_EX_TRANSPARENT) if on else (style & ~WS_EX_TRANSPARENT)
            set_l(hwnd, GWL_EXSTYLE, style)
            self._click_through = on
        except Exception:
            # Never fatal: worst case the overlay keeps eating clicks,
            # which is how it behaved before this existed.
            pass

    def _update_click_through(self):
        if not self.isVisible():
            return
        self._set_click_through(not self._interactive_at(QCursor.pos()))

    # ---- page -> host ---------------------------------------------
    def _on_title(self, title: str):
        if title.strip() == READY_SENTINEL:
            # Placed and shown only now, so the first frame the user sees
            # is already the overlay.
            #
            # ONCE. This sentinel arrives again every time the page asks
            # for a panel window - requestPanelWindow restores the title to
            # it deliberately, so that asking for the same section twice
            # still registers as a change. Re-placing on those would snap
            # the overlay back to the top right corner every time Master
            # opened settings, throwing away wherever he had dragged it.
            if not self._placed:
                self._placed = True
                place_top_right(self, self.width())
            self.show()
            _apply_ws_border(self)
            self._install_mouse_filter()
            return
        if title.strip().startswith(OPEN_PANEL_PREFIX):
            section = title.strip()[len(OPEN_PANEL_PREFIX):].strip()
            if section:
                _spawn_panel(section)
            return
        if title.strip() == HIT_ALL_SENTINEL:
            self._hit_all = True
            return
        if title.strip() == HIT_CORE_SENTINEL:
            self._hit_all = False
            return
        if title.strip() == EXIT_SENTINEL:
            # Exit code 0 tells the launcher this was a deliberate switch
            # back, not a crash.
            QApplication.instance().exit(0)

    # ---- dragging --------------------------------------------------
    # Done here rather than in the page: Qt owns the frameless window, and
    # moving it from JS would need a bridge for something the host can do
    # in three lines.
    def _in_handle(self, pos) -> bool:
        """Is this press inside the drag handle?

        Measured from the RIGHT edge, so it stays put at any window size.
        """
        x0 = self.width() - HANDLE_INSET_RIGHT
        return (x0 <= pos.x() <= x0 + HANDLE_W
                and HANDLE_TOP <= pos.y() <= HANDLE_TOP + HANDLE_H)

    # QWebEngineView does NOT receive mouse events itself: they go to an
    # internal render widget it owns. Overriding mousePressEvent here was
    # dead code - never called - which is exactly why the handle refused to
    # drag. Watching the focus proxy is the supported way to see them.
    def _install_mouse_filter(self):
        proxy = self.focusProxy()
        if proxy is not None and proxy is not self._filtered:
            proxy.installEventFilter(self)
            self._filtered = proxy

    def eventFilter(self, obj, event):
        et = event.type()
        if et == QEvent.MouseButtonPress:
            if (event.button() == Qt.LeftButton
                    and self._in_handle(event.position())):
                self._drag_from = (event.globalPosition().toPoint()
                                   - self.frameGeometry().topLeft())
                return True      # swallow, or the page reacts to it too
        elif et == QEvent.MouseMove:
            if self._drag_from is not None and (event.buttons() & Qt.LeftButton):
                self.move(self._clamp_to_screen(
                    event.globalPosition().toPoint() - self._drag_from))
                return True
        elif et in (QEvent.MouseButtonRelease, QEvent.Leave):
            self._drag_from = None
        return super().eventFilter(obj, event)

    def _clamp_to_screen(self, point):
        """Keep the whole window on the monitor it is being dragged on.

        Without this the overlay can be pushed past the screen edge, and
        the core menu - which opens centred on the window - gets cut off by
        the monitor rather than fitting inside it. The menu already clamps
        itself to the WINDOW; keeping the window fully visible is what
        makes that clamp sufficient.
        """
        screen = self.screen() or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(point.x(), area.left()), area.right() - self.width() + 1)
        y = min(max(point.y(), area.top()), area.bottom() - self.height() + 1)
        return QPoint(x, y)


# Panel processes, kept so a second request for a section already open
# raises that window instead of stacking duplicates.
_panel_procs = {}


def _panel_command(section: str):
    """How to re-invoke ourselves for a panel window.

    Frozen, this module IS the exe's entry point, so the exe re-invoked
    with --panel lands back in main(). Spawning the .py path would look
    right and fail in a build: there is no interpreter to run it with,
    and sys.executable is the overlay exe, not python.
    """
    if FROZEN:
        return [sys.executable, "--panel", section]
    return [sys.executable, os.path.abspath(__file__), "--panel", section]


def _spawn_panel(section: str):
    """Open (or re-focus) a settings/history window for `section`.

    A separate PROCESS rather than another window in this one: Qt wants a
    single GUI thread, and keeping panels out of the overlay's process
    means a panel that misbehaves cannot take the overlay down with it.
    """
    import subprocess
    proc = _panel_procs.get(section)
    if proc is not None and proc.poll() is None:
        return                       # already open
    try:
        proc = subprocess.Popen(
            _panel_command(section), cwd=HERE)
        _panel_procs[section] = proc
        print(f"[overlay] panel window opened: {section} (pid {proc.pid})",
              flush=True)
    except Exception as exc:
        print(f"[overlay] could not open panel {section}: {exc}", flush=True)


# The page draws its own title bar (#panel-window-chrome) because the
# window is frameless. That bar is 46px tall, and its minimise/close
# buttons sit at the right - pressing those must not also start a drag.
PANEL_BAR_H = 46
PANEL_BUTTONS_W = 96


class PanelView(QWebEngineView):
    """A settings/history window: opaque, frameless, its own chrome."""

    def __init__(self, section: str):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.resize(*PANEL_SIZE)
        self.setWindowTitle(f"Great Sage - {section}")
        self._drag_from = None
        self._filtered = None
        # Same reasoning as the overlay: QWebEngineView never receives
        # mouse events itself - they go to an internal render widget - so
        # the page's .pywebview-drag-region did nothing here, and there is
        # no pywebview under Qt to honour it anyway. That is why the title
        # bar could not be dragged.
        self.loadFinished.connect(lambda _ok: self._install_mouse_filter())

    def showEvent(self, event):
        # The render widget may not exist yet when the page finishes
        # loading, and installing on a proxy that is not there yet is a
        # silent no-op - which leaves the title bar dead with nothing to
        # show why. The overlay installs twice for the same reason.
        super().showEvent(event)
        self._install_mouse_filter()
        QTimer.singleShot(0, self._install_mouse_filter)

    def _install_mouse_filter(self):
        proxy = self.focusProxy()
        if proxy is not None and proxy is not self._filtered:
            proxy.installEventFilter(self)
            self._filtered = proxy

    def _in_bar(self, pos) -> bool:
        return (pos.y() <= PANEL_BAR_H
                and pos.x() <= self.width() - PANEL_BUTTONS_W)

    def eventFilter(self, obj, event):
        et = event.type()
        if et == QEvent.MouseButtonPress:
            if (event.button() == Qt.LeftButton
                    and self._in_bar(event.position())):
                self._drag_from = (event.globalPosition().toPoint()
                                   - self.frameGeometry().topLeft())
                return True      # swallow, or the page reacts to it too
        elif et == QEvent.MouseMove:
            if self._drag_from is not None and (event.buttons() & Qt.LeftButton):
                self.move(event.globalPosition().toPoint() - self._drag_from)
                return True
        elif et in (QEvent.MouseButtonRelease, QEvent.Leave):
            self._drag_from = None
        return super().eventFilter(obj, event)


def _apply_ws_border(win) -> bool:
    """Add WS_BORDER to the window style, after it is shown.

    Workaround for QTBUG-51093. Windows gives OpenGL-based windows special
    treatment when they have no border style, and Qt's own Windows notes
    flag it: the window ends up on a different compositing path, which
    shows up as flicker and as popups drawing behind the window.

    Reported to fix exactly this in a PyQtGraph thread with the same
    symptom. WS_BORDER must be applied AFTER show(), because Qt sets the
    style itself when the window is created and would overwrite it.

    Nothing is drawn by this on a frameless translucent window - the style
    bit changes how Windows composites it, not what it paints. Failure is
    non-fatal: worst case the flicker stays.
    """
    try:
        import ctypes
        hwnd = int(win.winId())
        u = ctypes.windll.user32
        get_l = getattr(u, "GetWindowLongPtrW", None) or u.GetWindowLongW
        set_l = getattr(u, "SetWindowLongPtrW", None) or u.SetWindowLongW
        get_l.restype = ctypes.c_ssize_t
        get_l.argtypes = [ctypes.c_void_p, ctypes.c_int]
        set_l.restype = ctypes.c_ssize_t
        set_l.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        _GWL_STYLE, _WS_BORDER = -16, 0x00800000
        style = get_l(ctypes.c_void_p(hwnd), _GWL_STYLE)
        set_l(ctypes.c_void_p(hwnd), _GWL_STYLE, style | _WS_BORDER)
        # SWP_FRAMECHANGED, or the new style is stored but never applied.
        u.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0,
                       0x0020 | 0x0002 | 0x0001 | 0x0004)
        print(f"[overlay] WS_BORDER applied (style 0x{style & 0xFFFFFFFF:08X}"
              f" -> 0x{(style | _WS_BORDER) & 0xFFFFFFFF:08X})", flush=True)
        return True
    except Exception as exc:
        print(f"[overlay] WS_BORDER failed: {type(exc).__name__}: {exc}",
              flush=True)
        return False


def place_top_right(win, size: int):
    """Corner of the WORK area, so it tucks beside the taskbar."""
    screen = QGuiApplication.primaryScreen()
    area = screen.availableGeometry()
    win.move(area.right() - size - MARGIN + 1, area.top() + MARGIN)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=340)
    ap.add_argument("--url", default=None,
                    help="page to load; defaults to the local hud file")
    ap.add_argument("--panel", default=None,
                    help="open as a settings/history window instead of the "
                         "overlay (values: settings, history)")
    args = ap.parse_args()

    # Chromium needs this before the QApplication exists, or the WebGL
    # surface comes back opaque even with the Qt attributes set.
    # VSYNC STAYS ON, and the frame rate STAYS CAPPED.
    #
    # --disable-gpu-vsync and --disable-frame-rate-limit were tried here as
    # flicker mitigations and did active harm:
    #   * uncapped frames ran the GPU flat out and spun the fans up, for a
    #     visual that only ever needs 60fps;
    #   * the HUD's animations advance PER FRAME, not per second
    #     (rotation.y += 0.0022), so more frames literally meant faster
    #     spinning - the wireframe visibly sped up;
    #   * disabling vsync CAUSES tearing, which on a translucent window
    #     looks like exactly the flicker it was meant to fix.
    # Removing them is not a compromise; keeping them was the mistake.
    #
    # NOT --disable-gpu-compositing. It was tried here as a flicker fix, on
    # the theory that a 340x340 window is cheap to composite in software.
    # Measured in use: about 3fps. Software compositing of a continuously
    # animating WebGL scene is far more expensive than the small window
    # size suggests, and an unusable frame rate is worse than the flicker
    # it was meant to cure.
    #
    # THE FLICKER: ANGLE. Qt 6.5.1 switched QtWebEngine to the ANGLE
    # backend (OpenGL translated to Direct3D), and translucent windows have
    # flickered since - reported against other Qt WebEngine apps, not just
    # this one. The usual advice is to pin Qt 6.4.3, which is not possible
    # here: PySide6 only supports Python 3.14 from 6.10 onward, so pip
    # offers 6.10.1 at the oldest.
    #
    # Selecting a different backend was tried and cannot work HERE, because
    # ANGLE is also what makes the transparency work. Measured, each
    # against a magenta backdrop:
    #
    #   --use-angle=gl        GPU context lost, nothing renders at all
    #   --use-angle=d3d9      failed to come up
    #   QT_OPENGL=desktop     renders, but the window is OPAQUE - the
    #                         desktop no longer shows through
    #
    # So the default ANGLE/D3D11 path stays. Escaping the flicker by
    # avoiding ANGLE would mean giving up the transparency the overlay
    # exists for.
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
                          "--enable-transparent-visuals "
                          "--autoplay-policy=no-user-gesture-required")

    # Both of these MUST happen before the QApplication is constructed -
    # afterwards the GL context and surface format are already fixed.
    #
    # AA_ShareOpenGLContexts: Qt WebEngine renders in its own process and
    # shares GL resources with the GUI thread. Without a shared context the
    # two ends can disagree about which surface is current, which is a
    # known cause of flicker in Qt apps that mix OpenGL widgets with other
    # rendering - Qt itself warns about this attribute for WebEngine.
    #
    # The explicit alpha buffer matters for a TRANSLUCENT window
    # specifically: the default surface format has no alpha channel, so the
    # compositor can end up reading undefined alpha for the frame, which
    # shows up as the window flashing rather than blending steadily.
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    fmt.setSwapBehavior(QSurfaceFormat.DoubleBuffer)
    QSurfaceFormat.setDefaultFormat(fmt)

    app = QApplication(sys.argv)

    # --panel: a settings/history window instead of the overlay. Opaque and
    # ordinary - it shows forms and lists, so transparency would only make
    # them harder to read - but still frameless, with the page drawing its
    # own title bar to match the log console.
    if args.panel:
        panel = PanelView(args.panel)

        # Commands from the page arrive as title changes - there is no
        # window.pywebview.api under Qt, so a title channel is the simplest
        # bridge that needs no extra plumbing.
        def _on_panel_title(t):
            cmd = t.strip()
            if cmd == "GS_PANEL_CLOSE":
                app.quit()
            elif cmd == "GS_PANEL_MIN":
                panel.showMinimized()

        panel.titleChanged.connect(_on_panel_title)
        url = args.url or QUrl.fromLocalFile(
            os.path.join(HERE, "hud_prototype.html")).toString()
        panel.load(QUrl(f"{url}?panel={args.panel}"))
        panel.show()
        _apply_ws_border(panel)
        return app.exec()

    view = OverlayView(args.size)

    url = args.url or QUrl.fromLocalFile(
        os.path.join(HERE, "hud_prototype.html")).toString()
    view.load(QUrl(url + MINI_QUERY))

    # Deliberately NOT shown here - see READY_SENTINEL. The page asks to
    # be shown once it is actually in overlay mode.
    #
    # Failsafe: if the page never reports ready (an older copy of the HTML,
    # or a JS error before it gets there), show it anyway rather than
    # leaving an invisible process running with no window.
    def _failsafe():
        if not view.isVisible():
            place_top_right(view, args.size)
            view.show()

    QTimer.singleShot(12000, _failsafe)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
