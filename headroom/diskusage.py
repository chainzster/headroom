"""Find the largest directories in the home directory using `du`.

The scan runs at the lowest CPU and I/O priority available and never crosses
filesystem boundaries (`-x`), so rclone/FUSE mounts inside the home directory
are skipped rather than walked.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import List, Tuple

FILES_LABEL = "(files directly in %s)"


class ScanError(RuntimeError):
    pass


def build_command(path: str, excludes: List[str]) -> List[str]:
    cmd: List[str] = []
    if shutil.which("nice"):
        cmd += ["nice", "-n", "19"]
    if shutil.which("ionice"):
        cmd += ["ionice", "-c", "3"]
    cmd += ["du", "-x", "-B1", "--max-depth=1"]
    for pattern in excludes:
        cmd.append("--exclude=" + pattern)
    cmd.append(path)
    return cmd


def parse_du_output(text: str, root: str) -> Tuple[int, List[Tuple[str, int]]]:
    """Return (total bytes of root, [(name, bytes), ...] largest first)."""
    root = root.rstrip("/") or "/"
    total = None
    dirs: List[Tuple[str, int]] = []
    for line in text.splitlines():
        size, sep, path = line.partition("\t")
        if not sep or not size.isdigit():
            continue
        path = path.rstrip("/") or "/"
        if path == root:
            total = int(size)
        else:
            dirs.append((os.path.basename(path), int(size)))
    if total is None:
        raise ScanError("du produced no total for %s" % root)
    loose = total - sum(s for _, s in dirs)
    if loose > 0:
        dirs.append((FILES_LABEL % "~", loose))
    dirs.sort(key=lambda d: d[1], reverse=True)
    return total, dirs


def scan(path: str, excludes: List[str], timeout: int) -> Tuple[int, List[Tuple[str, int]]]:
    if not os.path.isdir(path):
        raise ScanError("Not a directory: %s" % path)
    try:
        proc = subprocess.run(
            build_command(path, excludes), capture_output=True, text=True,
            timeout=timeout, env=dict(os.environ, LC_ALL="C"),
        )
    except FileNotFoundError as exc:
        raise ScanError("du not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise ScanError("Directory scan timed out after %ss" % timeout) from exc
    # du exits 1 on unreadable entries but still prints usable totals.
    return parse_du_output(proc.stdout, path)
