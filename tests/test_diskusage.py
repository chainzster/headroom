from headroom.diskusage import parse_du_output


def test_parse_du_output():
    out = "100\t/home/u/a\n300\t/home/u/b\n450\t/home/u\n"
    total, dirs = parse_du_output(out, "/home/u/")
    assert total == 450
    assert dirs[0] == ("b", 300)
    assert ("a", 100) in dirs
    assert dirs[-1] == ("(files directly in ~)", 50)
