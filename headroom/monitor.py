"""Background worker that polls the quota and runs directory scans."""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional

from .config import Config
from .diskusage import ScanError, scan
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

    def scan_once(self) -> None:
        self.scanning = True
        started = time.time()
        try:
            total, entries = scan(self.cfg.scan_path, self.cfg.exclude,
                                  self.cfg.scan_timeout, self.cfg.scan_depth)
            self.store.save_scan(self.cfg.scan_path, total, entries, time.time() - started)
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
