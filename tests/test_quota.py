import pytest

from headroom.quota import QuotaError, parse_quota_output, parse_size, select_entry

G = 1024 ** 3

BASIC = """Disk quotas for user alice (uid 1234):
     Filesystem   space   quota   limit   grace   files   quota   limit   grace
      /dev/sda1    750G   1000G   1050G           12345       0       0
"""

WRAPPED = """Disk quotas for user bob (uid 1001):
     Filesystem   space   quota   limit   grace   files   quota   limit   grace
/dev/mapper/very-long-volume-name
                 1010G*  1000G   1050G  6days   12345       0       0
"""

MULTI = """Disk quotas for user carol (uid 1002):
     Filesystem   space   quota   limit   grace   files   quota   limit   grace
      /dev/sda1      4K      0K      0K               1       0       0
      /dev/sdb1   1.5T      2T      2T             500       0       0
"""


def test_parse_size():
    assert parse_size("0K") == 0
    assert parse_size("750G") == 750 * G
    assert parse_size("1010G*") == 1010 * G
    assert parse_size("1.5T") == int(1.5 * 1024 * G)
    assert parse_size("2048") == 2048 * 1024  # bare number = KiB blocks
    with pytest.raises(QuotaError):
        parse_size("lots")


def test_basic():
    r = select_entry(parse_quota_output(BASIC))
    assert r.filesystem == "/dev/sda1"
    assert r.used == 750 * G and r.total == 1000 * G
    assert r.free == 250 * G
    assert round(r.percent, 1) == 75.0


def test_wrapped_and_over_quota():
    r = select_entry(parse_quota_output(WRAPPED))
    assert r.filesystem == "/dev/mapper/very-long-volume-name"
    assert r.used == 1010 * G and r.free == 0
    assert r.percent > 100


def test_multiple_filesystems_picks_largest_and_honours_config():
    entries = parse_quota_output(MULTI)
    assert select_entry(entries).filesystem == "/dev/sdb1"
    with pytest.raises(QuotaError):
        select_entry(entries, "/dev/sda1")  # exists but has no limit
    with pytest.raises(QuotaError):
        select_entry(entries, "/dev/nope")


def test_hard_limit_used_when_no_soft_quota():
    text = BASIC.replace("1000G   1050G", "   0K   1050G")
    assert select_entry(parse_quota_output(text)).total == 1050 * G


def test_no_quota():
    with pytest.raises(QuotaError, match="No quota"):
        parse_quota_output("Disk quotas for user dave (uid 1003): none\n")
