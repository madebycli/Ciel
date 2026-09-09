from __future__ import annotations

from pathlib import Path


class OverlayDependencyError(RuntimeError):
    pass


def run_overlay_spike(html_path: Path, size: int = 340, margin: int = 8) -> int:
    """Render the existing mini HUD in a native Wayland layer surface.

    This is intentionally a small spike. It proves WebKitGTK, WebGL,
    transparency and gtk-layer-shell together before the old HUD host is
    ported feature by feature.
    """
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("GtkLayerShell", "0.1")
        try:
            gi.require_version("WebKit2", "4.1")
        except ValueError:
            gi.require_version("WebKit2", "4.0")
        from gi.repository import Gdk, Gtk, GtkLayerShell, WebKit2
    except (ImportError, ValueError) as exc:
        raise OverlayDependencyError(
            "GTK3, PyGObject, WebKitGTK and gtk-layer-shell are required"
        ) from exc

    html_path = Path(html_path).resolve()
    if not html_path.is_file():
        raise FileNotFoundError(html_path)

    window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    window.set_title("Ciel Overlay")
    window.set_decorated(False)
    window.set_default_size(size, size)
    window.set_app_paintable(True)
    window.connect("destroy", Gtk.main_quit)

    screen = window.get_screen()
    visual = screen.get_rgba_visual() if screen else None
    if visual is not None:
        window.set_visual(visual)

    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.TOP, True)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.RIGHT, True)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.BOTTOM, False)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.LEFT, False)
    GtkLayerShell.set_margin(window, GtkLayerShell.Edge.TOP, margin)
    GtkLayerShell.set_margin(window, GtkLayerShell.Edge.RIGHT, margin)
    if hasattr(GtkLayerShell, "KeyboardMode"):
        GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.NONE)

    view = WebKit2.WebView()
    settings = view.get_settings()
    settings.set_enable_javascript(True)
    settings.set_enable_webgl(True)
    if hasattr(settings, "set_enable_webaudio"):
        settings.set_enable_webaudio(True)

    transparent = Gdk.RGBA()
    transparent.red = 0.0
    transparent.green = 0.0
    transparent.blue = 0.0
    transparent.alpha = 0.0
    view.set_background_color(transparent)

    window.add(view)
    view.load_uri(html_path.as_uri() + "?mini=1")
    window.show_all()
    Gtk.main()
    return 0
