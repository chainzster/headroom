"""Run the directory scan in a separate, low-priority process.

The walk itself lives in scanner.py. It runs as a child process under
`nice -n 19 ionice -c 3`, so on a shared server it only uses CPU and disk time
nobody else wants, and it can be killed cleanly if it times out.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Dict, List, Sequence, Tuple

from . import scanner

# Folders that are safe to empty: apps rebuild them as needed. Patterns are
# relative to the scan root; `*` also matches `/`. The first match wins and
# nothing below a match is matched again.
DEFAULT_JUNK: List[Tuple[str, str]] = [
    (".cache", "Cache"),
    (".local/share/Trash", "Trash"),
    ("*/.Trash-*", "Trash"),
    ("*/Plex Media Server/Cache", "Plex cache"),
    ("*/Plex Media Server/Crash Reports", "Plex crash reports"),
    ("*/Plex Media Server/Logs", "Plex logs"),
    ("*/Plex Media Server/Diagnostics", "Plex diagnostics"),
    (".config/*/logs", "App logs"),
    (".config/*/Logs", "App logs"),
    (".config/*/Backups", "App backups"),
    (".config/*/backups", "App backups"),
]


class ScanError(RuntimeError):
    pass


def build_command() -> List[str]:
    cmd: List[str] = []
    if shutil.which("nice"):
        cmd += ["nice", "-n", "19"]
    if shutil.which("ionice"):
        cmd += ["ionice", "-c", "3"]
    # -I: isolated mode, so nothing in the scanned tree can shadow a module.
    return cmd + [sys.executable, "-I", os.path.abspath(scanner.__file__)]


def scan(path: str, excludes: Sequence[str], timeout: int, depth: int = 4,
         watch: Sequence[str] = (), junk: Sequence[Tuple[str, str]] = (),
         top_files: int = 25, dup_min: int = 0) -> Dict:
    if not os.path.isdir(path):
        raise ScanError("Not a directory: %s" % path)
    params = {"root": path, "depth": depth, "excludes": list(excludes),
              "watch": list(watch), "junk": [list(j) for j in junk],
              "top_files": top_files, "dup_min": dup_min}
    try:
        proc = subprocess.run(build_command(), input=json.dumps(params),
                              capture_output=True, text=True, timeout=timeout,
                              cwd="/", env=dict(os.environ, LC_ALL="C"))
    except subprocess.TimeoutExpired as exc:
        raise ScanError("Directory scan timed out after %ss" % timeout) from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["no output"]
        raise ScanError("Directory scan failed: %s" % tail[0])
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise ScanError("Directory scan produced unreadable output") from exc
