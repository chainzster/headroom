from headroom.diskusage import build_command, parse_du_output


def test_parse_du_output_newlines():
    out = "100\t/home/u/a\n300\t/home/u/b\n450\t/home/u\n"
    total, entries = parse_du_output(out, "/home/u/")
    assert total == 450
    assert sorted(entries) == [("a", 100), ("b", 300)]


def test_parse_du_output_nul_and_nested():
    out = "\0".join([
        "40\t/home/u/b/x", "60\t/home/u/b/odd\nname", "300\t/home/u/b",
        "100\t/home/u/a", "450\t/home/u", "",
    ])
    total, entries = parse_du_output(out, "/home/u")
    assert total == 450
    assert dict(entries) == {"b/x": 40, "b/odd\nname": 60, "b": 300, "a": 100}


def test_build_command_depth():
    cmd = build_command("/home/u", [".cache"], depth=3)
    assert "--max-depth=3" in cmd and "-0" in cmd
    assert cmd[-2:] == ["--exclude=.cache", "/home/u"]
