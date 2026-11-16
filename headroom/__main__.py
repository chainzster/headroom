"""Entry point: `python -m headroom [--config PATH]`."""

from __future__ import annotations

import argparse
import getpass
import logging
import logging.handlers
import signal
import sys

from . import __version__
from .config import ConfigError, load_config


def _setup_logging(log_path) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    # Under systemd stderr goes to service.log, which is not rotated: keep it to
    # warnings and let the rotating headroom.log hold the full log.
    stream.setLevel(logging.INFO if sys.stderr.isatty() else logging.WARNING)
    root.addHandler(stream)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            str(log_path), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as exc:
        logging.warning("File logging disabled: %s", exc)
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)


def _hash_password() -> int:
    from werkzeug.security import generate_password_hash
    pw = getpass.getpass("Password: ")
    if not pw or pw != getpass.getpass("Repeat password: "):
        print("Passwords empty or do not match.", file=sys.stderr)
        return 1
    print(generate_password_hash(pw))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="headroom", description="Storage quota monitor")
    parser.add_argument("-c", "--config", help="path to config.ini "
                        "(default: $HEADROOM_CONFIG or ~/.config/headroom/config.ini)")
    parser.add_argument("--check", action="store_true",
                        help="validate config, read the quota once and exit")
    parser.add_argument("--hash-password", action="store_true",
                        help="print a password hash for the [auth] section and exit")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    if args.hash_password:
        return _hash_password()

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print("Configuration error: %s" % exc, file=sys.stderr)
        return 2

    if args.check:
        from .quota import QuotaError, read_quota
        print("Config OK: %s" % cfg.source)
        try:
            r = read_quota(cfg.quota_command, cfg.filesystem)
        except QuotaError as exc:
            print("Quota read FAILED: %s" % exc, file=sys.stderr)
            return 1
        gib = 1024 ** 3
        print("Filesystem %s: %.1f GiB used of %.1f GiB (%.1f%%), %.1f GiB free"
              % (r.filesystem, r.used / gib, r.total / gib, r.percent, r.free / gib))
        return 0

    _setup_logging(cfg.log_path)
    log = logging.getLogger("headroom")

    from waitress import serve

    from .app import create_app
    from .monitor import Monitor
    from .storage import Store

    store = Store(cfg.db_path)
    monitor = Monitor(cfg, store)
    app = create_app(cfg, store, monitor)

    def _shutdown(signum, frame):
        log.info("Received signal %s, shutting down", signum)
        monitor.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    monitor.start()
    log.info("Headroom %s listening on http://%s:%d%s/", __version__,
             cfg.host, cfg.port, cfg.base_url)
    serve(app, host=cfg.host, port=cfg.port, threads=4, ident="Headroom",
          clear_untrusted_proxy_headers=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
