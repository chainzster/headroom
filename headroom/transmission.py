"""Read the torrent list from Transmission's RPC interface.

Only `session-get` and `torrent-get` are used: Headroom never changes
anything in Transmission.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

SETTINGS_CANDIDATES = (
    "~/.config/transmission-daemon/settings.json",
    "~/.config/transmission/settings.json",
)


class TransmissionError(RuntimeError):
    pass


def detect_url(candidates=SETTINGS_CANDIDATES) -> Optional[str]:
    """Build the RPC URL from Transmission's own settings.json, if found."""
    for cand in candidates:
        path = os.path.expanduser(cand)
        try:
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh)
        except (OSError, ValueError):
            continue
        port = settings.get("rpc-port")
        if not port:
            continue
        bind = settings.get("rpc-bind-address") or "127.0.0.1"
        if bind in ("0.0.0.0", "::", ""):
            bind = "127.0.0.1"
        base = "/" + (settings.get("rpc-url") or "/transmission/").strip("/") + "/"
        return "http://%s:%d%srpc" % (bind, int(port), base)
    return None


class Client:
    def __init__(self, url: str, username: str = "", password: str = "",
                 timeout: float = 30):
        self.url = url
        self.timeout = timeout
        self.session_id = ""
        self.auth = None
        if username or password:
            token = base64.b64encode(("%s:%s" % (username, password)).encode()).decode()
            self.auth = "Basic " + token

    def call(self, method: str, arguments: Optional[Dict] = None) -> Dict:
        body = json.dumps({"method": method, "arguments": arguments or {}}).encode()
        # The first request (and any after a daemon restart) is answered with
        # 409 and a session id to repeat the request with.
        for _ in range(2):
            req = urllib.request.Request(self.url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("X-Transmission-Session-Id", self.session_id)
            if self.auth:
                req.add_header("Authorization", self.auth)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.load(resp)
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 409 and exc.headers.get("X-Transmission-Session-Id"):
                    self.session_id = exc.headers["X-Transmission-Session-Id"]
                    continue
                if exc.code == 401:
                    raise TransmissionError("Transmission rejected the username or password")
                raise TransmissionError("Transmission returned HTTP %d" % exc.code)
            except (urllib.error.URLError, OSError) as exc:
                reason = getattr(exc, "reason", exc)
                raise TransmissionError("Could not reach Transmission at %s: %s"
                                        % (self.url, reason))
            except ValueError:
                raise TransmissionError("Transmission sent a reply that is not JSON")
        else:
            raise TransmissionError("Transmission kept rejecting the session id")
        if data.get("result") != "success":
            raise TransmissionError("Transmission: %s" % data.get("result"))
        return data.get("arguments") or {}


def _norm(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


@dataclass
class Snapshot:
    """What Transmission owns, as absolute real paths."""
    torrents: int = 0
    claimed: Set[str] = field(default_factory=set)       # top-level data paths
    download_dirs: Set[str] = field(default_factory=set)  # where to look for leftovers


def snapshot(client: Client, extra_dirs: List[str] = ()) -> Snapshot:
    session = client.call("session-get", {"fields": [
        "download-dir", "incomplete-dir", "incomplete-dir-enabled"]})
    torrents = client.call("torrent-get", {"fields": ["name", "downloadDir"]})
    torrents = torrents.get("torrents") or []

    snap = Snapshot(torrents=len(torrents))
    incomplete = None
    if session.get("incomplete-dir-enabled") and session.get("incomplete-dir"):
        incomplete = _norm(session["incomplete-dir"])
        snap.download_dirs.add(incomplete)
    if session.get("download-dir"):
        snap.download_dirs.add(_norm(session["download-dir"]))
    for d in extra_dirs:
        snap.download_dirs.add(_norm(d))

    for t in torrents:
        name, ddir = t.get("name"), t.get("downloadDir")
        if not name or not ddir:
            continue
        ddir = _norm(ddir)
        snap.download_dirs.add(ddir)
        for base in filter(None, (ddir, incomplete)):
            # Incomplete files carry a .part suffix when rename-partial-files is on.
            snap.claimed.add(os.path.join(base, name))
            snap.claimed.add(os.path.join(base, name + ".part"))
    return snap


def find_leftovers(entries: Dict[str, List], snap: Snapshot,
                   before: float) -> List[Dict]:
    """Entries of the download folders that no torrent owns.

    `entries` maps a download folder to [name, is_dir, bytes, freeable, mtime]
    rows, as produced by the scanner. An entry is kept if it is a torrent's
    data, contains another download folder or torrent, or changed after
    `before` (the moment the torrent list was read), so a torrent added
    during the scan is not reported.
    """
    keep = set(snap.claimed)
    for p in list(snap.claimed) + list(snap.download_dirs):
        parent = os.path.dirname(p)
        while parent and parent not in keep:
            keep.add(parent)
            if parent == os.path.dirname(parent):
                break
            parent = os.path.dirname(parent)
    out = []
    for folder, rows in entries.items():
        base = _norm(folder)
        for name, is_dir, size, freeable, mtime in rows:
            path = os.path.join(base, name)
            if path in keep or path in snap.download_dirs or mtime >= before:
                continue
            out.append({"path": path, "is_dir": is_dir, "bytes": size or 0,
                        "freeable": freeable or 0, "mtime": int(mtime)})
    out.sort(key=lambda x: x["bytes"], reverse=True)
    return out
