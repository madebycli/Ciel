"""Conversations that survive a restart.

Spec S6: "Chats persist between application launches." Until now the HUD
kept its chat list in a JavaScript array, so closing the window discarded
every conversation - the sidebar, the titles and the transcripts all went
with it.

The HUD stays the owner of the list. It sends the whole thing whenever it
changes and receives it back on connect; this module only writes it down.
That is deliberate: the list is small (titles plus transcripts of one
user's chats), and a whole-list save has no partial-update states to get
out of step. A per-chat API can replace this later without the HUD
noticing, because the transport is one message type either way.

Writes go through a temporary file and then os.replace, which is atomic on
Windows. A crash mid-save otherwise leaves a truncated JSON file, and the
next launch would start with every conversation gone - the exact loss this
module exists to prevent.
"""

import json
import logging
import os
import tempfile
from typing import Any, Dict, List

log = logging.getLogger(__name__)

# Enough for a long history without letting the file grow without bound.
MAX_CHATS = 200
MAX_MESSAGES_PER_CHAT = 500
MAX_IMAGES_PER_MESSAGE = 4
# A 240px JPEG thumbnail at quality 0.7 is roughly 10-20KB of base64, so
# 64KB is generous headroom for one. The first value here was 400KB,
# which multiplied out to a 160GB worst case across the allowed chats and
# messages - a limit that large is not a limit.
MAX_IMAGE_CHARS = 64_000
# A per-chat budget, applied NEWEST FIRST. Bounding by count alone still
# grows without limit as chats accumulate; a byte budget does not, and
# dropping the oldest pictures first is what a person would expect - the
# screenshot from four hundred messages ago is not what they scroll back
# for.
MAX_IMAGE_BYTES_PER_CHAT = 1_500_000


def _sanitise(chats: Any) -> List[Dict[str, Any]]:
    """Keep only well-formed chats, and cap what is stored.

    The payload arrives from the page, so it is validated rather than
    trusted: a malformed entry must not be able to poison the file and
    take the whole history down with it on the next load.
    """
    out: List[Dict[str, Any]] = []
    if not isinstance(chats, list):
        return out
    for c in chats[-MAX_CHATS:]:
        if not isinstance(c, dict) or "id" not in c:
            continue
        msgs = c.get("messages")
        msgs = msgs if isinstance(msgs, list) else []
        clean_msgs = []
        for m in msgs[-MAX_MESSAGES_PER_CHAT:]:
            if not isinstance(m, dict):
                continue
            entry = {"role": str(m.get("role", ""))[:32],
                     "text": str(m.get("text", ""))}
            # Action entries carry what was run and what it returned. Kept
            # so the trail survives a restart - the point of it is being
            # able to look back and check what Great Sage actually did.
            if m.get("role") == "action":
                entry["tool"] = str(m.get("tool", ""))[:64]
                entry["result"] = str(m.get("result", ""))[:600]
            # Thumbnails only, and capped. The page stores downscaled
            # copies rather than what was sent to the model - a full
            # screenshot is hundreds of KB of base64, and a few of those
            # per chat would dominate this file.
            imgs = m.get("images")
            if isinstance(imgs, list):
                kept = [str(i) for i in imgs[:MAX_IMAGES_PER_MESSAGE]
                        if isinstance(i, str) and len(i) <= MAX_IMAGE_CHARS]
                if kept:
                    entry["images"] = kept
            clean_msgs.append(entry)

        # Spend the image budget newest-first, then strip the rest.
        budget = MAX_IMAGE_BYTES_PER_CHAT
        for entry in reversed(clean_msgs):
            imgs = entry.get("images")
            if not imgs:
                continue
            keep = []
            for img in imgs:
                if len(img) <= budget:
                    keep.append(img)
                    budget -= len(img)
            if keep:
                entry["images"] = keep
            else:
                entry.pop("images", None)
        out.append({
            "id": c["id"],
            "title": str(c.get("title") or "New chat")[:120],
            "created": c.get("created"),
            "updated": c.get("updated"),
            "summary": str(c.get("summary") or "")[:2000],
            # Whether a title was already generated, and whether the user
            # renamed it by hand. Persisted so a restart does not re-request
            # a title for every stored chat, or overwrite a manual name.
            "titled": bool(c.get("titled")),
            "renamed": bool(c.get("renamed")),
            "messages": clean_msgs,
        })
    return out


def load(path: str) -> List[Dict[str, Any]]:
    """Every stored chat, oldest first. Never raises."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except Exception:
        # A corrupt file must not stop the app from starting. Keep the
        # damaged copy so it can be looked at rather than silently binned.
        log.exception("Chat store unreadable (%s); starting empty", path)
        try:
            os.replace(path, path + ".corrupt")
            log.warning("Kept the unreadable file as %s.corrupt", path)
        except Exception:
            pass
        return []
    return _sanitise(data.get("chats") if isinstance(data, dict) else data)


def save(path: str, chats: Any) -> int:
    """Write the list atomically. Returns how many chats were stored."""
    clean = _sanitise(chats)
    payload = {"version": 1, "chats": clean}
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)       # atomic on Windows and POSIX alike
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise
    return len(clean)
