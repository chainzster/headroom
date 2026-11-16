"""Measure directory sizes in the home directory using `du`.

A single `du` pass records every directory down to `depth` levels below the
scan root, so the dashboard can drill down without running further scans.

The scan runs at the lowest CPU and I/O priority available and never crosses
filesystem boundaries (`-x`), so rclone/FUSE mounts inside the home directory
are skipped rather than walked.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import List, Tuple

# (path relative to the scan root, bytes). The root itself is "".
Entry = Tuple[str, int]


class ScanError(RuntimeError):
    pass


def build_command(path: str, excludes: List[str], depth: int = 1) -> List[str]:
    cmd: List[str] = []
    if shutil.which("nice"):
        cmd += ["nice", "-n", "19"]
    if shutil.which("ionice"):
        cmd += ["ionice", "-c", "3"]
    # -0: NUL-terminated records, so names containing newlines can't break parsing.
    cmd += ["du", "-0", "-x", "-B1", "--max-depth=%d" % depth]
    for pattern in excludes:
        cmd.append("--exclude=" + pattern)
    cmd.append(path)
    return cmd


def parse_du_output(text: str, root: str) -> Tuple[int, List[Entry]]:
    """Return (total bytes of root, [(relative path, bytes), ...]).

    The list holds every directory du reported below the root, at any depth,
    in no particular order. Accepts NUL- or newline-terminated records.
    """
    root = root.rstrip("/") or "/"
    prefix = root if root == "/" else root + "/"
    total = None
    entries: List[Entry] = []
    for record in text.split("\0" if "\0" in text else "\n"):
        size, sep, path = record.lstrip("\n").partition("\t")
        if not sep or not size.isdigit():
            continue
        path = path.rstrip("/") or "/"
        if path == root:
            total = int(size)
        elif path.startswith(prefix):
            entries.append((path[len(prefix):], int(size)))
    if total is None:
        raise ScanError("du produced no total for %s" % root)
    return total, entries


def scan(path: str, excludes: List[str], timeout: int,
         depth: int = 1) -> Tuple[int, List[Entry]]:
    if not os.path.isdir(path):
        raise ScanError("Not a directory: %s" % path)
    try:
        proc = subprocess.run(
            build_command(path, excludes, depth), capture_output=True, text=True,
            errors="replace", timeout=timeout, env=dict(os.environ, LC_ALL="C"),
        )
    except FileNotFoundError as exc:
        raise ScanError("du not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise ScanError("Directory scan timed out after %ss" % timeout) from exc
    # du exits 1 on unreadable entries but still prints usable totals.
    return parse_du_output(proc.stdout, path)
