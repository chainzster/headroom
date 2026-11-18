import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from headroom import transmission as tr


def test_find_leftovers(tmp_path):
    dl = str(tmp_path / "dl")
    tv = os.path.join(dl, "tv")
    snap = tr.Snapshot(torrents=2, download_dirs={dl, tv},
                       claimed={os.path.join(dl, "Movie (2020)"),
                                os.path.join(dl, "Movie (2020).part"),
                                os.path.join(tv, "Show S01")})
    entries = {dl: [
        ["Movie (2020)", True, 100, 100, 1.0],
        ["tv", True, 500, 500, 1.0],            # another download folder
        ["Old Thing", True, 300000, 0, 1.0],       # removed torrent, hardlinked into media
        ["stray.nfo", False, 4096, 4096, 1.0],
        ["New Torrent", True, 50, 50, 999.0],    # appeared after the torrent list was read
    ]}
    out = tr.find_leftovers(entries, snap, before=100.0)
    assert [x["path"] for x in out] == [os.path.join(dl, "Old Thing"),
                                        os.path.join(dl, "stray.nfo")]
    assert out[0]["freeable"] == 0


def test_detect_url(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"rpc-port": 9091, "rpc-bind-address": "0.0.0.0",
                             "rpc-url": "/transmission/"}))
    assert tr.detect_url([str(s)]) == "http://127.0.0.1:9091/transmission/rpc"
    assert tr.detect_url([str(tmp_path / "missing.json")]) is None


class FakeTransmission(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        if self.headers.get("Authorization") != "Basic dTpw":  # u:p
            self.send_response(401); self.end_headers(); return
        if self.headers.get("X-Transmission-Session-Id") != "abc":
            self.send_response(409)
            self.send_header("X-Transmission-Session-Id", "abc")
            self.end_headers(); return
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if req["method"] == "session-get":
            args = {"download-dir": "/data/dl", "incomplete-dir": "/data/inc",
                    "incomplete-dir-enabled": True}
        else:
            args = {"torrents": [{"name": "A", "downloadDir": "/data/dl/movies"}]}
        body = json.dumps({"result": "success", "arguments": args}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), FakeTransmission)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/transmission/rpc" % srv.server_port
    srv.shutdown()


def test_snapshot_over_rpc(server):
    snap = tr.snapshot(tr.Client(server, "u", "p"))
    assert snap.torrents == 1
    assert snap.download_dirs == {"/data/dl", "/data/inc", "/data/dl/movies"}
    assert "/data/dl/movies/A" in snap.claimed and "/data/inc/A.part" in snap.claimed


def test_bad_password(server):
    with pytest.raises(tr.TransmissionError, match="username or password"):
        tr.snapshot(tr.Client(server, "u", "wrong"))
