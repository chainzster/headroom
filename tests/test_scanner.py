import os

from headroom.diskusage import build_command, scan
from headroom.scanner import walk

MB = 1024 * 1024


def put(path, size, fill=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(fill * size)


def blocks(path):
    return os.lstat(path).st_blocks * 512


def dirs(result):
    return {p: (b, f) for p, b, f in result["dirs"]}


def test_hardlinks_free_nothing_until_every_link_is_gone(tmp_path):
    root = str(tmp_path)
    put(os.path.join(root, "downloads", "show", "ep1.mkv"), MB)
    os.makedirs(os.path.join(root, "media", "tv"))
    os.link(os.path.join(root, "downloads", "show", "ep1.mkv"),
            os.path.join(root, "media", "tv", "ep1.mkv"))
    put(os.path.join(root, "downloads", "only.bin"), MB)
    size = blocks(os.path.join(root, "downloads", "only.bin"))

    r = walk(root, depth=3)
    d = dirs(r)
    # The linked file is counted once (du-style, wherever the walk meets it
    # first) but deleting either side frees nothing.
    assert size <= d["downloads/show"][0] + d["media"][0] < 2 * size
    assert d["downloads/show"][1] < size
    assert d["media"][1] < size
    assert d["downloads"][1] >= size and d["downloads"][1] < 2 * size
    # Leftover-style size counts the linked file wherever it is.
    show = {e[0]: e for e in walk(root, watch=[os.path.join(root, "downloads")])["watch"][
        os.path.join(root, "downloads")]}["show"]
    assert show[2] >= size and show[3] < size
    # Deleting the whole tree frees everything.
    assert r["freeable"] == r["total"]
    assert r["total"] < 3 * size


def test_links_outside_root_are_never_freeable(tmp_path):
    put(str(tmp_path / "outside.bin"), MB)
    os.makedirs(tmp_path / "home")
    os.link(tmp_path / "outside.bin", tmp_path / "home" / "linked.bin")
    r = walk(str(tmp_path / "home"))
    assert r["shared_outside"] >= MB
    assert r["freeable"] < MB


def test_depth_limit_and_excludes(tmp_path):
    put(str(tmp_path / "a" / "b" / "c" / "d" / "f"), 10)
    put(str(tmp_path / "skip" / "f"), MB)
    r = walk(str(tmp_path), depth=2, excludes=["skip"])
    assert set(dirs(r)) == {"a", "a/b"}
    assert dirs(r)["a"][0] >= blocks(str(tmp_path / "a" / "b" / "c" / "d" / "f"))


def test_junk_watch_largest_and_duplicates(tmp_path):
    root = str(tmp_path)
    put(os.path.join(root, ".cache", "pip", "wheel"), MB, b"a")
    put(os.path.join(root, ".config", "plex", "Plex Media Server", "Cache", "t", "x"), MB, b"b")
    put(os.path.join(root, "dl", "movie", "m.mkv"), 2 * MB)
    put(os.path.join(root, "dl", "single.iso"), MB, b"y")
    put(os.path.join(root, "copy", "single.iso"), MB, b"y")
    r = walk(root, watch=[os.path.join(root, "dl"), "/elsewhere"],
             junk=[(".cache", "Cache"), ("*/Plex Media Server/Cache", "Plex cache"),
                   ("*/pip", "never matched inside a junk folder")],
             top_files=2, dup_min=MB)

    junk = {p: label for p, label, b, f in r["junk"]}
    assert junk == {".cache": "Cache",
                    ".config/plex/Plex Media Server/Cache": "Plex cache"}

    entries = {e[0]: e for e in r["watch"][os.path.join(root, "dl")]}
    assert entries["movie"][1] is True and entries["movie"][2] >= 2 * MB
    assert entries["single.iso"][1] is False and entries["single.iso"][3] >= MB
    assert r["unscanned_watch"] == ["/elsewhere"]

    assert r["files"][0][0] == "dl/movie/m.mkv" and len(r["files"]) == 2
    assert r["duplicates"] == [[MB, ["copy/single.iso", "dl/single.iso"]]]


def test_scan_runs_as_low_priority_subprocess(tmp_path):
    put(str(tmp_path / "x" / "f"), 1000)
    r = scan(str(tmp_path), [], timeout=60, depth=2)
    assert "x" in dirs(r)
    cmd = build_command()
    assert "-I" in cmd and cmd[-1].endswith("scanner.py")
