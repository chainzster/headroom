"""Walk the scan root once and measure what could be freed.

This module is run as a separate low-priority process
(`nice -n 19 ionice -c 3 python scanner.py`), reading its parameters as JSON
on stdin and printing the result as JSON on stdout. It uses only the standard
library and no package-relative imports, so it can be run as a plain script.

Sizes are allocated bytes (st_blocks * 512), which is what the quota counts.
Two figures are kept for every directory:

  bytes     du-style usage: each inode counted once, at the first place it
            is found.
  freeable  what deleting the directory would actually give back: files whose
            every hard link lies inside it. A file hardlinked from
            ~/downloads into ~/media counts towards ~/ (their common
            ancestor) but frees nothing if only one of the two is deleted.
  size      everything inside the directory, each inode counted once. Unlike
            `bytes`, a hardlinked file counts in both places. Used for
            leftovers and cache folders, where `bytes` could hide a large
            hardlinked file that happened to be found elsewhere first.

Like `du -x`, the walk never leaves the root's filesystem and never follows
symlinks.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import os
import sys
import time
from fnmatch import fnmatchcase

SAMPLE = 1024 * 1024  # bytes read from each of 3 places for duplicate checks


def _sample_hash(path, size):
    h = hashlib.sha1()
    try:
        with open(path, "rb") as fh:
            for off in sorted({0, max(0, size // 2 - SAMPLE // 2), max(0, size - SAMPLE)}):
                fh.seek(off)
                h.update(fh.read(SAMPLE))
    except OSError:
        return None
    return h.hexdigest()


def _excluded(name, rel, patterns):
    return any(fnmatchcase(name, p) or fnmatchcase(rel, p) for p in patterns)


def _common_dir(dirs):
    parts = dirs[0].split("/") if dirs[0] else []
    for d in dirs[1:]:
        other = d.split("/") if d else []
        n = 0
        while n < len(parts) and n < len(other) and parts[n] == other[n]:
            n += 1
        parts = parts[:n]
    return "/".join(parts)


def _depth(rel):
    return rel.count("/") + 1 if rel else 0


def walk(root, depth=4, excludes=(), watch=(), junk=(), top_files=25,
         dup_min=100 * 1024 * 1024):
    """Scan `root`. See the module docstring for what is measured.

    watch:  directories (absolute) whose immediate entries are reported with
            their sizes, for leftover-download detection.
    junk:   [(pattern, label)] matched against directory paths relative to
            root (fnmatch, `*` also matches `/`). Matching directories are
            reported with their sizes; nothing below a match is matched again.
    """
    root = os.path.realpath(root)
    root_st = os.lstat(root)
    dev = root_st.st_dev
    watch_rel = {}
    for w in watch:
        rp = os.path.realpath(w)
        if rp != root and rp.startswith(root.rstrip("/") + "/"):
            watch_rel[os.path.relpath(rp, root)] = w

    rb = root_st.st_blocks * 512
    own = {"": [rb, rb, rb]}  # rel -> [bytes, freeable, size]
    links = {}        # (dev, ino) -> [size, nlink, [dir rel, ...]]
    largest = []      # min-heap of (size, rel, nlink)
    by_size = {}      # size -> [rel, ...] (first link of each inode only)
    watched = {r: [] for r in watch_rel}
    junk_roots = {}
    errors = 0

    stack = [(root, "", False)]
    while stack:
        path, rel, in_junk = stack.pop()
        acc = own[rel]
        entries_out = watched.get(rel)
        try:
            it = os.scandir(path)
        except OSError:
            errors += 1
            continue
        with it:
            for entry in it:
                name = entry.name
                crel = name if not rel else rel + "/" + name
                if excludes and _excluded(name, crel, excludes):
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    errors += 1
                    continue
                size = st.st_blocks * 512
                if is_dir:
                    if st.st_dev != dev:
                        continue
                    own[crel] = [size, size, size]
                    cj = in_junk
                    if not in_junk:
                        for pat, label in junk:
                            if fnmatchcase(crel, pat):
                                junk_roots[crel] = label
                                cj = True
                                break
                    stack.append((entry.path, crel, cj))
                    if entries_out is not None:
                        entries_out.append([name, True, None, None, st.st_mtime])
                    continue

                nlink = st.st_nlink
                if entries_out is not None:
                    entries_out.append([name, False, size, size if nlink == 1 else 0,
                                        st.st_mtime])
                if nlink > 1:
                    key = (st.st_dev, st.st_ino)
                    rec = links.get(key)
                    if rec is not None:
                        rec[2].append(rel)
                        continue
                    links[key] = [size, nlink, [rel]]
                    acc[0] += size
                else:
                    acc[0] += size
                    acc[1] += size
                    acc[2] += size
                if top_files:
                    item = (size, crel, nlink)
                    if len(largest) < top_files:
                        heapq.heappush(largest, item)
                    elif item > largest[0]:
                        heapq.heapreplace(largest, item)
                if dup_min and size >= dup_min and entry.is_file(follow_symlinks=False):
                    by_size.setdefault(st.st_size, []).append(crel)

    # A hardlinked inode is freed only by deleting every link, so it counts
    # towards the deepest directory holding all of them. Links outside the
    # root mean nothing inside the root can free it.
    shared_outside = 0
    for size, nlink, dirs in links.values():
        if len(dirs) >= nlink:
            own[_common_dir(dirs)][1] += size
        else:
            shared_outside += size

    tot = {k: list(v) for k, v in own.items()}
    for rel in sorted(tot, key=_depth, reverse=True):
        if rel:
            parent = rel.rpartition("/")[0]
            for i in range(3):
                tot[parent][i] += tot[rel][i]

    # A hardlinked inode adds to the size of every directory holding a link,
    # and of their ancestors, once each.
    for size, _nlink, dirs in links.values():
        seen = set()
        for d in dirs:
            while d not in seen:
                seen.add(d)
                tot[d][2] += size
                if not d:
                    break
                d = d.rpartition("/")[0]

    for rel, entries in watched.items():
        for e in entries:
            if e[1]:
                crel = rel + "/" + e[0]
                t = tot.get(crel, (0, 0, 0))
                e[2], e[3] = t[2], t[1]

    duplicates = []
    for size, rels in by_size.items():
        if len(rels) < 2:
            continue
        groups = {}
        for r in rels:
            h = _sample_hash(os.path.join(root, r), size)
            if h:
                groups.setdefault(h, []).append(r)
        for paths in groups.values():
            if len(paths) > 1:
                duplicates.append([size, sorted(paths)])
    duplicates.sort(key=lambda d: d[0] * (len(d[1]) - 1), reverse=True)

    return {
        "root": root,
        "total": tot[""][0],
        "freeable": tot[""][1],
        "shared_outside": shared_outside,
        "errors": errors,
        "dirs": [[r, t[0], t[1]] for r, t in tot.items() if r and _depth(r) <= depth],
        "junk": [[r, label, tot[r][2], tot[r][1]] for r, label in junk_roots.items()],
        "watch": {watch_rel[r]: e for r, e in watched.items()},
        "unscanned_watch": [w for w in watch if w not in watch_rel.values()],
        "files": [[r, s, n] for s, r, n in sorted(largest, reverse=True)],
        "duplicates": duplicates[:50],
    }


def main():
    params = json.load(sys.stdin)
    started = time.time()
    result = walk(params["root"], params.get("depth", 4), params.get("excludes", []),
                  params.get("watch", []), [tuple(j) for j in params.get("junk", [])],
                  params.get("top_files", 25), params.get("dup_min", 0))
    result["duration"] = time.time() - started
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
