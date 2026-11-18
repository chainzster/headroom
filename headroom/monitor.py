"""Background worker that polls the quota and runs directory scans."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, Optional

from .config import Config
from . import transmission
from .diskusage import DEFAULT_JUNK, ScanError, scan
from .quota import QuotaError, QuotaReading, read_quota
from .storage import Store

log = logging.getLogger("headroom.monitor")


class Monitor:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        self.last_error: Optional[str] = None
        self.last_reading: Optional[QuotaReading] = None
        self.last_poll: Optional[int] = None
        self.scan_error: Optional[str] = None
        self.scanning = False
        self._stop = threading.Event()
        self._scan_lock = threading.Lock()
        self._threads = []

    # -- quota --------------------------------------------------------------

    def poll_once(self) -> None:
        self.last_poll = int(time.time())
        try:
            reading = read_quota(self.cfg.quota_command, self.cfg.filesystem)
        except QuotaError as exc:
            if str(exc) != self.last_error:
                log.error("Quota read failed: %s", exc)
            self.last_error = str(exc)
            return
        except Exception as exc:  # never let the worker die
            log.exception("Unexpected error reading quota")
            self.last_error = "Unexpected error: %s" % exc
            return
        if self.last_error:
            log.info("Quota read recovered")
        self.last_error = None
        self.last_reading = reading
        self.store.add_reading(reading.used, reading.total)

    def _poll_loop(self) -> None:
        last_prune = 0.0
        while not self._stop.is_set():
            self.poll_once()
            if time.time() - last_prune > 86400:
                try:
                    removed = self.store.prune(self.cfg.retention_days)
                    if removed:
                        log.info("Pruned %d old readings", removed)
                except Exception:
                    log.exception("Prune failed")
                last_prune = time.time()
            self._stop.wait(self.cfg.poll_interval)

    # -- directory scan -----------------------------------------------------

    def _transmission(self) -> Dict:
        """Read Transmission's torrent list. Never raises."""
        info: Dict = {"enabled": self.cfg.tr_enabled, "error": None, "snapshot": None,
                      "read_at": time.time()}
        if not self.cfg.tr_enabled:
            return info
        url = self.cfg.tr_url or transmission.detect_url()
        if not url:
            info["error"] = ("Transmission's settings.json was not found; set "
                             "[transmission] url in the config")
            return info
        try:
            client = transmission.Client(url, self.cfg.tr_username, self.cfg.tr_password)
            info["snapshot"] = transmission.snapshot(client, self.cfg.tr_download_dirs)
        except transmission.TransmissionError as exc:
            info["error"] = str(exc)
            log.warning("Transmission: %s", exc)
        except Exception as exc:
            info["error"] = "Unexpected error: %s" % exc
            log.exception("Unexpected error reading Transmission")
        return info

    def _junk_patterns(self):
        junk = list(DEFAULT_JUNK) if self.cfg.junk_defaults else []
        return [(p, "Custom") for p in self.cfg.junk] + junk

    def _claim_scan(self) -> bool:
        with self._scan_lock:
            if self.scanning:
                return False
            self.scanning = True
            return True

    def scan_once(self) -> None:
        """Run a scan now, in this thread, unless one is already running."""
        if self._claim_scan():
            self._scan()

    def _scan(self) -> None:
        started = time.time()
        try:
            tr = self._transmission()
            snap = tr["snapshot"]
            result = scan(self.cfg.scan_path, self.cfg.exclude, self.cfg.scan_timeout,
                          self.cfg.scan_depth,
                          watch=sorted(snap.download_dirs) if snap else [],
                          junk=self._junk_patterns(),
                          top_files=self.cfg.largest_files,
                          dup_min=self.cfg.duplicate_min_mb * 1024 * 1024)
            report = build_report(result, tr)
            self.store.save_scan(self.cfg.scan_path, result["total"], result["dirs"],
                                 time.time() - started, result["freeable"], report)
            self.scan_error = None
            log.info("Directory scan finished in %.1fs", time.time() - started)
        except ScanError as exc:
            self.scan_error = str(exc)
            log.error("Directory scan failed: %s", exc)
        except Exception as exc:
            self.scan_error = "Unexpected error: %s" % exc
            log.exception("Unexpected error during directory scan")
        finally:
            self.scanning = False

    def _scan_loop(self) -> None:
        # Skip the start-up scan if a recent one exists (e.g. after a restart).
        last = self.store.scan_info()
        wait = 60.0
        if last:
            wait = max(wait, last["ts"] + self.cfg.scan_interval - time.time())
        while not self._stop.wait(wait):
            self.scan_once()
            wait = self.cfg.scan_interval

    def rescan(self) -> bool:
        """Start a scan now in the background. False if one is running."""
        if not self._claim_scan():
            return False
        threading.Thread(target=self._scan, name="rescan", daemon=True).start()
        return True

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        targets = [self._poll_loop]
        if self.cfg.scan_enabled:
            targets.append(self._scan_loop)
        for target in targets:
            t = threading.Thread(target=target, name=target.__name__, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> Dict:
        return {
            "last_poll": self.last_poll,
            "error": self.last_error,
            "scan_error": self.scan_error,
            "scanning": self.scanning,
        }


def build_report(result: Dict, tr: Dict) -> Dict:
    """Turn a scan result into the "space you could free" report."""
    root = result["root"]

    def rel(path):
        return os.path.relpath(path, root)

    leftovers: Dict = {"enabled": tr["enabled"], "error": tr["error"],
                       "torrents": None, "folders": [], "unscanned": [], "items": []}
    snap = tr["snapshot"]
    if snap:
        items = transmission.find_leftovers(result["watch"], snap, tr["read_at"])
        for it in items:
            it["path"] = rel(it["path"])
        leftovers.update(
            torrents=snap.torrents,
            folders=sorted(rel(w) for w in result["watch"]),
            unscanned=sorted(result["unscanned_watch"]),
            items=items,
        )
    home = os.path.realpath(os.path.expanduser("~"))
    return {
        "root": root,
        "root_label": "~" if root == home else root,
        "total": result["total"],
        "freeable": result["freeable"],
        "shared_outside": result["shared_outside"],
        "errors": result["errors"],
        "leftovers": leftovers,
        "junk": sorted(({"path": p, "label": label, "bytes": b, "freeable": f}
                        for p, label, b, f in result["junk"] if b > 0),
                       key=lambda x: x["bytes"], reverse=True),
        "files": [{"path": p, "bytes": b, "links": n} for p, b, n in result["files"]],
        "duplicates": [{"bytes": size, "paths": paths, "saving": size * (len(paths) - 1)}
                       for size, paths in result["duplicates"]],
    }
