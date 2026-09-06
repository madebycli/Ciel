"""The tool layer: what Great Sage can actually DO on this machine.

Spec S16's architecture, and the reason it exists: the model must not be
responsible for facts the application can determine for itself. Asked the
time, a language model invents one. Asked what is installed, it guesses.
The model decides INTENT; this module supplies reality.

    user -> model -> tool call -> VALIDATION -> execution -> real result
                                      |
                                      +-- refused if anything is wrong

Spec S24: "Never let hallucinated tool names or arguments directly
execute." Nothing here hands a model-authored string to a shell. The two
tools that touch the outside world are deliberately narrow:

  open_application  resolves a NAME against applications actually
                    installed on this machine and launches the resolved
                    shortcut. It cannot run an arbitrary command because
                    it never receives one - a name matching nothing is
                    refused.
  open_url          http and https only. Not file://, which would open
                    local files, and not any other scheme that might
                    reach a registered handler.

Permission tiers (S24): SAFE runs automatically, CONFIRM needs the user
to agree, BLOCKED is present but not executable.

Adding a tool means adding one Tool() to REGISTRY. The schema handed to
the model, the validation and the dispatch all derive from it.
"""

import glob
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

SAFE, CONFIRM, BLOCKED = "safe", "confirm", "blocked"


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[..., str]
    tier: str = SAFE


class ToolError(Exception):
    """A tool refused to run, or failed. The message reaches the model so
    it can say what happened instead of inventing success."""


def _get_time() -> str:
    return time.strftime("%A %d %B %Y, %H:%M")


def _get_system_status() -> str:
    parts = []
    try:
        import shutil
        total, _used, free = shutil.disk_usage(os.path.expanduser("~"))
        parts.append("disk %.0fGB free of %.0fGB" % (free / 1e9, total / 1e9))
    except Exception:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            free_b, total_b = torch.cuda.mem_get_info()
            parts.append("GPU %s, %.1fGB of %.1fGB VRAM free"
                         % (torch.cuda.get_device_name(0),
                            free_b / 1e9, total_b / 1e9))
    except Exception:
        pass
    return "; ".join(parts) if parts else "No system details available."


def _list_running_apps() -> str:
    """Visible windows, not every process: "what is running" means what
    the user can see, not 200 background services."""
    try:
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.windll.user32
        names: List[str] = []
        buf = ctypes.create_unicode_buffer(512)

        def cb(h, _l):
            if u.IsWindowVisible(h) and u.GetWindowTextLengthW(h) > 0:
                u.GetWindowTextW(h, buf, 512)
                title = buf.value.strip()
                if title and title not in names:
                    names.append(title)
            return True

        u.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
        return ", ".join(names[:25]) if names else "Nothing with a window."
    except Exception as exc:
        raise ToolError("Could not list windows: %s" % exc)


_APP_CACHE: Dict[str, str] = {}


def _installed_apps() -> Dict[str, str]:
    """Lowercased name -> shortcut path, from the Start Menu.

    Start Menu shortcuts rather than a scan of Program Files, because they
    are what the user thinks of as installed applications - and because a
    fixed set is what makes open_application safe. The model supplies a
    name to look up, never a path or a command to run.
    """
    global _APP_CACHE
    if _APP_CACHE:
        return _APP_CACHE
    roots = [
        os.path.join(os.environ.get("APPDATA", ""),
                     "Microsoft", "Windows", "Start Menu", "Programs"),
        os.path.join(os.environ.get("PROGRAMDATA", ""),
                     "Microsoft", "Windows", "Start Menu", "Programs"),
    ]
    found: Dict[str, str] = {}
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for path in glob.glob(os.path.join(root, "**", "*.lnk"), recursive=True):
            name = os.path.splitext(os.path.basename(path))[0]
            found.setdefault(name.lower(), path)
    _APP_CACHE = found
    log.info("Tool layer: %d installed applications indexed", len(found))
    return found


def _resolve_app(name: str) -> Optional[str]:
    apps = _installed_apps()
    q = (name or "").strip().lower()
    if not q:
        return None
    if q in apps:
        return apps[q]
    starts = [k for k in apps if k.startswith(q)]
    if len(starts) == 1:
        return apps[starts[0]]
    contains = [k for k in apps if q in k]
    if not contains:
        return None
    # Shortest match wins: almost always the application itself rather
    # than "App Uninstaller" or "App Web Help".
    return apps[sorted(contains, key=len)[0]]


def _open_application(name: str) -> str:
    target = _resolve_app(name)
    if not target:
        raise ToolError(
            "No installed application matches %r. It is not in the Start "
            "Menu, so there is nothing to launch." % name)
    try:
        os.startfile(target)
    except Exception as exc:
        raise ToolError("Could not launch %s: %s" % (name, exc))
    return "Launched %s." % os.path.splitext(os.path.basename(target))[0]


def _open_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        raise ToolError("No URL given.")
    low = u.lower()
    if not low.startswith("http://") and not low.startswith("https://"):
        if "://" in u:
            raise ToolError(
                "Refused: only http and https can be opened, not %s."
                % u.split("://")[0])
        # A bare domain, which is what a model usually produces for
        # "open youtube".
        u = "https://" + u
    import webbrowser
    if not webbrowser.open(u):
        raise ToolError("No browser available to open %s." % u)
    return "Opened %s." % u


def _open_folder(path: str) -> str:
    p = os.path.expandvars(os.path.expanduser((path or "").strip()))
    if not p:
        raise ToolError("No folder given.")
    if not os.path.exists(p):
        # Models are bad at real paths here. Asked to open "my Downloads
        # folder" they produce either a bare "Downloads" or an invented
        # POSIX path like "/Users/your_username/Downloads" - both were
        # observed. Refusing those reads as the tool being broken, when
        # the INTENT was perfectly clear.
        #
        # So fall back to the last path segment resolved against this
        # user's real home directory, which turns both forms into the
        # folder actually meant. Still a real check: a segment matching
        # no folder is refused, so this cannot open something arbitrary.
        leaf = os.path.basename(p.rstrip("/" + chr(92))) or p
        candidate = os.path.join(os.path.expanduser("~"), leaf)
        if os.path.isdir(candidate):
            log.info("Resolved %r -> %s", path, candidate)
            p = candidate
        else:
            raise ToolError(
                "No folder called %r was found in your user folder." % leaf)
    try:
        os.startfile(p if os.path.isdir(p) else os.path.dirname(p))
    except Exception as exc:
        raise ToolError("Could not open %s: %s" % (p, exc))
    return "Opened %s." % p


def _search_files(query: str) -> str:
    """Name search over the usual user folders. Deliberately not the whole
    disk: an unbounded walk takes minutes, and the answer would arrive
    long after the conversation moved on."""
    q = (query or "").strip().lower()
    if not q:
        raise ToolError("Nothing to search for.")
    home = os.path.expanduser("~")
    roots = [os.path.join(home, d) for d in
             ("Desktop", "Documents", "Downloads", "Music", "Videos",
              "Pictures")]
    hits: List[str] = []
    deadline = time.time() + 8
    for root in roots:
        if not os.path.isdir(root) or len(hits) >= 15:
            continue
        for dirpath, dirs, files in os.walk(root):
            if time.time() > deadline or len(hits) >= 15:
                break
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if q in f.lower():
                    hits.append(os.path.join(dirpath, f))
                    if len(hits) >= 15:
                        break
    if not hits:
        return "Nothing matching %r in the usual folders." % query
    return chr(10).join(hits)


REGISTRY: List[Tool] = [
    Tool("get_time", "Get the current local date and time.",
         {"type": "object", "properties": {}}, _get_time, SAFE),
    Tool("get_system_status",
         "Report free disk space and free VRAM on this machine.",
         {"type": "object", "properties": {}}, _get_system_status, SAFE),
    Tool("list_running_apps",
         "List the applications currently open on screen.",
         {"type": "object", "properties": {}}, _list_running_apps, SAFE),
    Tool("open_url",
         "Open a web page or video in the browser. Use for YouTube links, "
         "websites, and anything the user asks to open online.",
         {"type": "object",
          "properties": {"url": {"type": "string",
                                 "description": "Full URL or domain"}},
          "required": ["url"]},
         _open_url, SAFE),
    Tool("open_application",
         "Launch an installed application by name, for example Discord, "
         "Reaper, Chrome.",
         {"type": "object",
          "properties": {"name": {"type": "string",
                                  "description": "Application name"}},
          "required": ["name"]},
         _open_application, SAFE),
    Tool("open_folder",
         "Open a folder, or a file's location, in File Explorer.",
         {"type": "object",
          "properties": {"path": {"type": "string"}},
          "required": ["path"]},
         _open_folder, SAFE),
    Tool("search_files",
         "Search Desktop, Documents, Downloads, Music, Videos and Pictures "
         "for files whose name contains the query.",
         {"type": "object",
          "properties": {"query": {"type": "string"}},
          "required": ["query"]},
         _search_files, SAFE),
]

BY_NAME: Dict[str, Tool] = {t.name: t for t in REGISTRY}


def ollama_schema() -> List[Dict[str, Any]]:
    """The registry in the shape Ollama's /api/chat expects."""
    return [{"type": "function",
             "function": {"name": t.name,
                          "description": t.description,
                          "parameters": t.parameters}}
            for t in REGISTRY if t.tier != BLOCKED]


def execute(name: str, arguments: Any) -> str:
    """Validate, then run. Raises ToolError with a message fit to show.

    Everything arriving here was produced by a language model, so nothing
    is assumed: not that the tool exists, not that the arguments are a
    dict, not that the required ones are present, and not that no extra
    ones were invented along the way.
    """
    tool = BY_NAME.get(name)
    if tool is None:
        raise ToolError("No such tool: %r." % name)
    if tool.tier == BLOCKED:
        raise ToolError("%s exists but is not enabled." % name)
    args = arguments if isinstance(arguments, dict) else {}
    props = (tool.parameters or {}).get("properties", {}) or {}
    required = (tool.parameters or {}).get("required", []) or []
    missing = [r for r in required if not str(args.get(r, "")).strip()]
    if missing:
        raise ToolError("%s needs %s." % (name, ", ".join(missing)))
    unknown = [k for k in args if k not in props]
    if unknown:
        # Dropped rather than passed on: an invented argument would be a
        # TypeError deep inside the handler, surfacing as a crash rather
        # than as the model having made something up.
        log.warning("Tool %s: ignoring unknown argument(s) %s", name, unknown)
        args = {k: v for k, v in args.items() if k in props}
    log.info("Tool call: %s(%s)", name, args)
    return tool.handler(**args)


# Words that suggest the user wants something DONE or LOOKED UP, rather
# than talked about. Deliberately generous: a false positive costs about
# a second, a false negative means the model invents an answer it should
# have fetched.
_TRIGGERS = (
    "open", "launch", "start", "run ", "play ", "show me", "pull up",
    "find", "search", "look for", "locate", "where is", "where's",
    "time", "date", "clock", "what day",
    "vram", "ram ", "memory", "disk", "space", "storage", "gpu",
    "running", "what's open", "whats open", "apps", "programs", "windows",
    "folder", "directory", "file", "downloads", "desktop", "documents",
    "youtube", "google", "browser", "website", "url", "link", ".com",
)


def might_need_tools(text: str) -> bool:
    """Should the tool schema be attached to this turn?

    Attaching it to EVERY message cost 1.9s each, measured: first visible
    token went from 1.65s to 3.50s. That is the model reading 1,674
    characters of schema, not the network and not streaming - streaming
    with tools attached was just as slow.

    So ordinary conversation skips it entirely and stays fast, and only
    turns that look like a request for an action or a fact about the
    machine pay the cost.

    A miss is not silent: without tools the model answers from memory, so
    the failure mode is a made-up time rather than a crash. Hence the
    generous list.
    """
    low = (text or "").lower()
    return any(t in low for t in _TRIGGERS)
