r"""Things Great Sage does on its own, later (spec S63, Phase 16).

The examples in the spec are the shape of it:

    "Remind me at 7 PM."
    "Watch my render folder and tell me when it finishes."

Two rules make this safe to have at all.

VERIFY BEFORE ANNOUNCING (S63: "Great Sage should verify results"). A
reminder fires because a clock says so, which is checkable. A folder task
fires because the folder ACTUALLY changed - it re-reads and compares
rather than trusting that something was expected. Great Sage never
announces an event it did not observe.

RESPECT ATTENTION (S62/S81). A task that is due does not interrupt
immediately: if state says now is a bad moment, or the mode is one where
Great Sage should be quiet, it waits. Nothing is lost by waiting - these
are minutes-scale jobs, not alarms - and the spec is explicit that the
goal is "not talk as much as possible".

Tasks persist across restarts. A reminder that evaporates when the app is
closed is worse than one never set, because it was believed.
"""

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, List, Optional

log = logging.getLogger(__name__)

MAX_TASKS = 50


@dataclass
class Task:
    id: str
    kind: str                      # "remind" | "watch_folder"
    message: str = ""
    due: float = 0.0               # epoch seconds, for "remind"
    path: str = ""                 # for "watch_folder"
    baseline: Optional[dict] = None
    created: float = field(default_factory=time.time)
    fired: bool = False


def _folder_signature(path: str):
    """Something comparable that changes when the folder does.

    Names, sizes and modification times - not a content hash. A render
    still being written changes size on every check, and hashing
    gigabytes to notice that would cost far more than it tells us.
    """
    try:
        out = {}
        with os.scandir(path) as it:
            for e in it:
                if e.is_file():
                    st = e.stat()
                    out[e.name] = [st.st_size, int(st.st_mtime)]
        return out
    except Exception:
        return None


class Autonomy:
    """Holds the tasks, checks them on a timer, reports what actually fired."""

    def __init__(self, path: str, on_fire: Callable[[Task], None],
                 may_speak: Callable[[], bool] = None,
                 poll_seconds: float = 20.0):
        self.path = path
        self._on_fire = on_fire
        self._may_speak = may_speak or (lambda: True)
        self._poll = poll_seconds
        self.tasks: List[Task] = []
        self._stop = threading.Event()
        self._thread = None
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            self.tasks = [Task(**t) for t in raw.get("tasks", [])
                          if isinstance(t, dict)][:MAX_TASKS]
            if self.tasks:
                log.info("Restored %d scheduled task(s)", len(self.tasks))
        except FileNotFoundError:
            self.tasks = []
        except Exception:
            log.exception("Task file unreadable; starting with none")
            self.tasks = []

    def save(self):
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"tasks": [asdict(t) for t in self.tasks]}, fh,
                          indent=1)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise

    def remind(self, message: str, in_seconds: float) -> Task:
        t = Task(id="t%d" % int(time.time() * 1000), kind="remind",
                 message=message, due=time.time() + max(1.0, in_seconds))
        self._add(t)
        log.info("Reminder set for %s: %r",
                 time.strftime("%H:%M", time.localtime(t.due)), message[:60])
        return t

    def watch_folder(self, path: str, message: str = "") -> Task:
        real = os.path.expandvars(os.path.expanduser(path))
        if not os.path.isdir(real):
            raise ValueError("No folder at %s" % path)
        t = Task(id="t%d" % int(time.time() * 1000), kind="watch_folder",
                 path=real, message=message,
                 baseline=_folder_signature(real))
        self._add(t)
        log.info("Watching folder %s", real)
        return t

    def _add(self, t: Task):
        self.tasks = (self.tasks + [t])[-MAX_TASKS:]
        self.save()

    def cancel(self, task_id: str) -> bool:
        before = len(self.tasks)
        self.tasks = [t for t in self.tasks if t.id != task_id]
        if len(self.tasks) != before:
            self.save()
            return True
        return False

    def pending(self):
        return [t for t in self.tasks if not t.fired]

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("Autonomy tick failed")
            self._stop.wait(self._poll)

    def _tick(self):
        due = []
        for t in self.tasks:
            if t.fired:
                continue
            if t.kind == "remind" and time.time() >= t.due:
                due.append(t)
            elif t.kind == "watch_folder":
                now = _folder_signature(t.path)
                # None means the folder vanished. Report nothing rather
                # than announce a "change" that was a disappearance.
                if now is not None and now != t.baseline:
                    t.baseline = now
                    due.append(t)
        if not due:
            return
        if not self._may_speak():
            # HELD, not dropped. Attention is the constraint, not the
            # task, so it fires at the next tick that is a better moment.
            log.info("%d task(s) due but holding - not a good moment",
                     len(due))
            return
        for t in due:
            if t.kind == "remind":
                t.fired = True
            try:
                self._on_fire(t)
            except Exception:
                log.exception("Task callback failed")
        self.save()
