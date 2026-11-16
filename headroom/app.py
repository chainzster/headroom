"""Flask application: one HTML page plus a small JSON API."""

from __future__ import annotations

import hmac
import time
from typing import Optional

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.security import check_password_hash

from . import __version__
from .config import Config
from .monitor import Monitor
from .storage import Store

RANGES = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "1y": 365 * 86400}


class PrefixMiddleware:
    """Serve the app under a base URL (e.g. /headroom) behind a reverse proxy.

    Ultra.cc's Nginx passes the full path through (`proxy_pass` without a URI),
    so the prefix is moved from PATH_INFO into SCRIPT_NAME. Requests that
    arrive without the prefix (e.g. direct access on the port) still work.
    """

    def __init__(self, app, prefix: str):
        self.app = app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if self.prefix and (path == self.prefix or path.startswith(self.prefix + "/")):
            environ["SCRIPT_NAME"] = self.prefix
            environ["PATH_INFO"] = path[len(self.prefix):]
        return self.app(environ, start_response)


def level_for(percent: Optional[float], cfg: Config) -> str:
    if percent is None:
        return "unknown"
    if percent >= cfg.critical:
        return "critical"
    if percent >= cfg.high:
        return "high"
    if percent >= cfg.warning:
        return "warning"
    return "ok"


def create_app(cfg: Config, store: Store, monitor: Monitor) -> Flask:
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False

    @app.before_request
    def require_auth():
        if not cfg.auth_enabled or request.endpoint == "healthz":
            return None
        auth = request.authorization
        if (auth and auth.username is not None and auth.password is not None
                and hmac.compare_digest(auth.username, cfg.username)
                and check_password_hash(cfg.password_hash, auth.password)):
            return None
        return Response("Authentication required", 401,
                        {"WWW-Authenticate": 'Basic realm="Headroom"'})

    @app.after_request
    def no_cache(resp):
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        return resp

    @app.route("/")
    def index():
        return render_template("index.html", version=__version__)

    @app.route("/api/status")
    def status():
        latest = store.latest_reading()
        payload = {
            "thresholds": {"warning": cfg.warning, "high": cfg.high,
                           "critical": cfg.critical},
            "poll_interval": cfg.poll_interval,
            "monitor": monitor.status(),
            "reading": None,
            "level": "unknown",
            "change": {},
        }
        if latest:
            used, total = latest["used"], latest["total"]
            percent = used / total * 100 if total else None
            payload["reading"] = {
                "ts": latest["ts"], "used": used, "total": total,
                "free": max(total - used, 0),
                "percent": round(percent, 2) if percent is not None else None,
            }
            payload["level"] = level_for(percent, cfg)
            now = int(time.time())
            for key, span in (("24h", 86400), ("7d", 7 * 86400)):
                past = store.reading_near(now - span)
                # Only report a change if the history actually covers most of the span.
                if past and past["ts"] < latest["ts"] and \
                        latest["ts"] - past["ts"] >= span * 0.9:
                    payload["change"][key] = used - past["used"]
        return jsonify(payload)

    @app.route("/api/history")
    def history():
        rng = request.args.get("range", "7d")
        if rng not in RANGES:
            rng = "7d"
        rows = store.history(int(time.time()) - RANGES[rng])
        return jsonify({"range": rng, "points": [list(r) for r in rows]})

    @app.route("/api/directories")
    def directories():
        return jsonify({
            "scan": store.latest_scan(cfg.top_n),
            "enabled": cfg.scan_enabled,
            "scanning": monitor.scanning,
            "error": monitor.scan_error,
            "interval": cfg.scan_interval,
        })

    @app.route("/healthz")
    def healthz():
        return jsonify({"ok": monitor.last_error is None, "version": __version__})

    app.wsgi_app = PrefixMiddleware(app.wsgi_app, cfg.base_url)
    return app
