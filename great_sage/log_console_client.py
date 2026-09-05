"""
Standalone script spawned by great_sage/server.py when the HUD's LOGS
button is clicked - a separate window scrolling live activity, the way a
Forge mod's log console pops up alongside the game rather than living
inside it.

It used to be a bare OS console window printing lines to stdout. It's now
its own pywebview window (log_console.html) styled to match the HUD -
black panel, hard white outline, same monospace and grid texture - so it
reads as part of the app instead of a stray terminal.

That also means this file no longer speaks WebSocket at all: the page
connects to the bridge itself (ws://localhost:8765, subscribing as a
"subscribe_logs" viewer - see server.py's protocol docstring) and does
its own reconnecting. All that's left here is opening the window.
"""

import os
import sys

import webview

HTML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "log_console.html"
)


def main() -> int:
    if not os.path.isfile(HTML_PATH):
        print(f"Log console UI not found: {HTML_PATH}", file=sys.stderr)
        return 1
    # frameless, with the page drawing its own chrome. The Windows title
    # bar sat as a grey band above a full-bleed dark panel and broke the
    # illusion that this is part of the app rather than a separate tool.
    #
    # easy_drag=False because it defaults to TRUE for frameless windows,
    # which makes the ENTIRE window a drag surface - the log area would
    # move the window instead of letting text be selected. The page marks
    # its own title bar with .pywebview-drag-region instead.
    window = webview.create_window(
        "Great Sage - Live Logs",
        HTML_PATH,
        width=900,
        height=620,
        background_color="#030b08",
        frameless=True,
        easy_drag=False,
    )

    class _Api:
        """Window controls for the page's own title bar."""

        def minimize(self):
            try:
                window.minimize()
            except Exception:
                pass

        def close(self):
            try:
                window.destroy()
            except Exception:
                pass

    window._js_api = _Api()
    try:
        window.expose(window._js_api.minimize, window._js_api.close)
    except Exception:
        # Older pywebview without expose(): the page falls back to its
        # keyboard shortcut, and the window can still be closed from the
        # taskbar. Not worth failing the whole console over.
        pass
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
