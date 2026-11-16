import os
import stat
import textwrap
import time

import pytest
from werkzeug.security import generate_password_hash

from headroom.app import create_app
from headroom.config import ConfigError, load_config
from headroom.monitor import Monitor
from headroom.storage import Store

FAKE_QUOTA = """#!/bin/sh
cat <<'OUT'
Disk quotas for user test (uid 1000):
     Filesystem   space   quota   limit   grace   files   quota   limit   grace
      /dev/sda1    850G   1000G   1000G            10       0       0
OUT
"""


@pytest.fixture
def env(tmp_path):
    script = tmp_path / "fakequota"
    script.write_text(FAKE_QUOTA)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    conf = tmp_path / "config.ini"
    conf.write_text(textwrap.dedent("""
        [server]
        port = 12345
        base_url = headroom/
        [monitor]
        quota_command = %s
        [directories]
        path = %s
        [storage]
        data_dir = %s
    """ % (script, tmp_path, tmp_path / "data")))
    cfg = load_config(str(conf))
    store = Store(cfg.db_path)
    mon = Monitor(cfg, store)
    return cfg, store, mon, conf


def test_config_and_status(env):
    cfg, store, mon, _ = env
    assert cfg.base_url == "/headroom"
    mon.poll_once()
    assert mon.last_error is None
    client = create_app(cfg, store, mon).test_client()

    r = client.get("/headroom/api/status").get_json()
    assert r["level"] == "warning"
    assert r["reading"]["percent"] == 85.0

    # /headroom -> /headroom/ so relative asset and API URLs resolve correctly
    redir = client.get("/headroom")
    assert redir.status_code in (301, 308)
    assert redir.headers["Location"].endswith("/headroom/")
    assert b"Headroom" in client.get("/headroom/").data
    assert client.get("/headroom/static/app.js").status_code == 200
    assert len(client.get("/headroom/api/history?range=24h").get_json()["points"]) == 1


def test_history_downsampling_and_prune(env):
    cfg, store, mon, _ = env
    now = int(time.time())
    for i in range(2000):
        store.add_reading(i, 10000, ts=now - 86400 * 2 + i * 60)
    store.add_reading(1, 10000, ts=now - 86400 * 400)
    assert len(store.history(now - 86400 * 7)) <= 401
    assert store.prune(365) == 1


def test_directory_scan(env):
    cfg, store, mon, _ = env
    os.makedirs(os.path.join(cfg.scan_path, "big"))
    with open(os.path.join(cfg.scan_path, "big", "f"), "wb") as fh:
        fh.write(b"x" * 200000)
    mon.scan_once()
    assert mon.scan_error is None
    scan = store.latest_scan(5)
    assert scan["directories"][0]["name"] == "big"


def test_auth(env):
    cfg, store, mon, _ = env
    cfg.username, cfg.password_hash = "me", generate_password_hash("pw")
    client = create_app(cfg, store, mon).test_client()
    assert client.get("/api/status").status_code == 401
    assert client.get("/healthz").status_code == 200
    ok = client.get("/api/status", headers={"Authorization": "Basic bWU6cHc="})
    assert ok.status_code == 200


def test_bad_thresholds(env):
    *_, conf = env
    conf.write_text(conf.read_text() + "\n[thresholds]\nwarning = 95\nhigh = 90\n")
    with pytest.raises(ConfigError):
        load_config(str(conf))
