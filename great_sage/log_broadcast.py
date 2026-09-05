"""
Broadcasts Python logging records to any connected "logs" viewer client
(a second websocket connection from logs_console.html, opened by the
LOGS button in hud_prototype.html) - shows live backend activity the way
the old CLI's console output did, without needing to tail
great_sage_hud.log by hand.
"""

import asyncio
import json
import logging
from collections import deque

# Everything logged this run, oldest first, capped so a long session
# can't grow without bound. Lives at module scope rather than on
# BroadcastLogHandler because it has to start collecting the moment the
# process does - BroadcastLogHandler isn't constructed until the server
# starts, by which point model loading and startup have already been
# logged and lost.
LOG_HISTORY_LIMIT = 1500
_HISTORY = deque(maxlen=LOG_HISTORY_LIMIT)


class HistoryLogHandler(logging.Handler):
    """Records every log line into the shared ring buffer.

    Installed by run_hud.py alongside the file handler, at logging-config
    time, so opening the log console later shows what has ALREADY
    happened instead of starting blank - which is the whole point when
    the thing you want to read is the error that just scrolled past.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _HISTORY.append((record.levelname, self.format(record)))
        except Exception:
            pass  # logging must never take the app down


def history():
    """Snapshot of what's been logged so far, as (level, message)."""
    return list(_HISTORY)


class BroadcastLogHandler(logging.Handler):
    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self._loop = loop
        self._clients = set()

    def add_client(self, websocket) -> None:
        self._clients.add(websocket)
        # Replay first, so the console opens with context rather than
        # waiting for the next thing to happen.
        for level, message in history():
            payload = json.dumps({"type": "log", "level": level, "message": message})
            asyncio.run_coroutine_threadsafe(self._safe_send(websocket, payload), self._loop)

    def remove_client(self, websocket) -> None:
        self._clients.discard(websocket)

    def emit(self, record: logging.LogRecord) -> None:
        if not self._clients:
            return
        try:
            message = self.format(record)
        except Exception:
            return
        payload = json.dumps({"type": "log", "level": record.levelname, "message": message})
        for websocket in list(self._clients):
            asyncio.run_coroutine_threadsafe(self._safe_send(websocket, payload), self._loop)

    @staticmethod
    async def _safe_send(websocket, payload: str) -> None:
        try:
            await websocket.send(payload)
        except Exception:
            pass  # client likely disconnected - server.py's own handler cleans it up
