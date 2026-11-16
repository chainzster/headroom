"""Read the account's storage allocation from `quota -s`.

`df` is deliberately not used: on Ultra.cc it reports the shared server disk,
not the space allocated to the account.

Typical output::

    Disk quotas for user alice (uid 1234):
         Filesystem   space   quota   limit   grace   files   quota   limit   grace
          /dev/sda1    750G   1000G   1050G           12345       0       0

Long filesystem names may wrap onto their own line, and the space column gets a
trailing '*' when the soft quota is exceeded. Both cases are handled.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import List, Optional

_UNITS = {"": 1024, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4,
          "P": 1024 ** 5, "E": 1024 ** 6}
_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)([KMGTPE]?)$", re.IGNORECASE)


class QuotaError(RuntimeError):
    """Raised when the quota cannot be read or parsed."""


@dataclass
class QuotaEntry:
    filesystem: str
    used: int    # bytes
    soft: int    # bytes, 0 = none
    hard: int    # bytes, 0 = none

    @property
    def total(self) -> int:
        """The allocation: the soft quota if set, otherwise the hard limit."""
        return self.soft or self.hard


@dataclass
class QuotaReading:
    filesystem: str
    used: int
    total: int

    @property
    def free(self) -> int:
        return max(self.total - self.used, 0)

    @property
    def percent(self) -> float:
        return (self.used / self.total * 100.0) if self.total else 0.0


def parse_size(token: str) -> int:
    """Convert a `quota -s` size such as '750G' or '0K' into bytes.

    A bare number is in 1 KiB blocks (quota's native unit).
    """
    token = token.strip().rstrip("*")
    m = _SIZE_RE.match(token)
    if not m:
        raise QuotaError("Unrecognised size %r in quota output" % token)
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()])


def parse_quota_output(text: str) -> List[QuotaEntry]:
    lines = text.splitlines()
    header = next((i for i, l in enumerate(lines) if "Filesystem" in l), None)
    if header is None:
        if re.search(r"\bnone\b", text):
            raise QuotaError("No quota is set for this user")
        raise QuotaError("Unexpected quota output: %r" % text.strip()[:200])

    entries: List[QuotaEntry] = []
    pending_fs: Optional[str] = None
    for line in lines[header + 1:]:
        tokens = line.split()
        if not tokens:
            continue
        if len(tokens) == 1:          # filesystem name wrapped onto its own line
            pending_fs = tokens[0]
            continue
        if pending_fs is not None:
            tokens = [pending_fs] + tokens
            pending_fs = None
        if len(tokens) < 4:
            continue
        try:
            entries.append(QuotaEntry(
                filesystem=tokens[0],
                used=parse_size(tokens[1]),
                soft=parse_size(tokens[2]),
                hard=parse_size(tokens[3]),
            ))
        except QuotaError:
            continue
    if not entries:
        raise QuotaError("No filesystems found in quota output")
    return entries


def select_entry(entries: List[QuotaEntry], filesystem: str = "") -> QuotaReading:
    if filesystem:
        matches = [e for e in entries if e.filesystem == filesystem]
        if not matches:
            raise QuotaError(
                "Filesystem %r not in quota output (found: %s)"
                % (filesystem, ", ".join(e.filesystem for e in entries))
            )
    else:
        matches = [e for e in entries if e.total > 0]
        if not matches:
            raise QuotaError("Quota output contains no filesystem with a limit")
    entry = max(matches, key=lambda e: e.total)
    if entry.total <= 0:
        raise QuotaError("Filesystem %s has no quota limit" % entry.filesystem)
    return QuotaReading(entry.filesystem, entry.used, entry.total)


def read_quota(command: str = "quota -s", filesystem: str = "",
               timeout: int = 30) -> QuotaReading:
    try:
        proc = subprocess.run(
            shlex.split(command), capture_output=True, text=True, timeout=timeout,
            env=dict(os.environ, LC_ALL="C"),
        )
    except FileNotFoundError as exc:
        raise QuotaError("quota command not found: %s" % command) from exc
    except subprocess.TimeoutExpired as exc:
        raise QuotaError("quota command timed out after %ss" % timeout) from exc
    # quota exits non-zero when over quota, so rely on the output, not the code.
    output = proc.stdout or ""
    if "Filesystem" not in output and proc.stderr.strip():
        output = output + "\n" + proc.stderr
    return select_entry(parse_quota_output(output), filesystem)
