"""Configuration loading.

The configuration file is a plain INI file (read with the standard library's
configparser) so that no extra dependency is needed to parse it.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

DEFAULT_CONFIG_PATH = "~/.config/headroom/config.ini"


class ConfigError(ValueError):
    """Raised when the configuration file is missing or invalid."""


@dataclass
class Config:
    # [server]
    host: str = "127.0.0.1"
    port: int = 8080
    base_url: str = ""
    # [monitor]
    poll_interval: int = 300
    retention_days: int = 365
    quota_command: str = "quota -s"
    filesystem: str = ""
    # [thresholds]
    warning: float = 80.0
    high: float = 90.0
    critical: float = 95.0
    # [directories]
    scan_enabled: bool = True
    scan_path: str = "~"
    scan_interval: int = 21600
    scan_timeout: int = 1800
    top_n: int = 15
    scan_depth: int = 4
    exclude: List[str] = field(default_factory=list)
    # [storage]
    data_dir: str = "~/.local/share/headroom"
    # [auth]
    username: str = ""
    password_hash: str = ""
    # Where the config was loaded from (informational only).
    source: Optional[str] = None

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "headroom.db"

    @property
    def log_path(self) -> Path:
        return Path(self.data_dir) / "headroom.log"

    @property
    def auth_enabled(self) -> bool:
        return bool(self.username and self.password_hash)


def _expand(path: str) -> str:
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def normalise_base_url(value: str) -> str:
    """Return the base URL as '' or '/something' (no trailing slash)."""
    value = (value or "").strip().strip("/")
    return "/" + value if value else ""


def load_config(path: Optional[str] = None) -> Config:
    path = _expand(path or os.environ.get("HEADROOM_CONFIG") or DEFAULT_CONFIG_PATH)
    if not os.path.isfile(path):
        raise ConfigError(
            "Config file not found: %s (copy config.example.ini there and edit it)" % path
        )

    parser = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    try:
        with open(path, encoding="utf-8") as fh:
            parser.read_file(fh)
    except (OSError, configparser.Error) as exc:
        raise ConfigError("Could not read %s: %s" % (path, exc)) from exc

    def get(section, key, fallback, conv=str):
        if not parser.has_option(section, key):
            return fallback
        raw = parser.get(section, key).strip()
        if raw == "":
            return fallback
        try:
            if conv is bool:
                return parser.getboolean(section, key)
            return conv(raw)
        except ValueError as exc:
            raise ConfigError("[%s] %s: invalid value %r" % (section, key, raw)) from exc

    d = Config()
    cfg = Config(
        host=get("server", "host", d.host),
        port=get("server", "port", d.port, int),
        base_url=normalise_base_url(get("server", "base_url", d.base_url)),
        poll_interval=get("monitor", "poll_interval", d.poll_interval, int),
        retention_days=get("monitor", "retention_days", d.retention_days, int),
        quota_command=get("monitor", "quota_command", d.quota_command),
        filesystem=get("monitor", "filesystem", d.filesystem),
        warning=get("thresholds", "warning", d.warning, float),
        high=get("thresholds", "high", d.high, float),
        critical=get("thresholds", "critical", d.critical, float),
        scan_enabled=get("directories", "enabled", d.scan_enabled, bool),
        scan_path=_expand(get("directories", "path", d.scan_path)),
        scan_interval=get("directories", "scan_interval", d.scan_interval, int),
        scan_timeout=get("directories", "timeout", d.scan_timeout, int),
        top_n=get("directories", "top_n", d.top_n, int),
        scan_depth=get("directories", "depth", d.scan_depth, int),
        exclude=[
            e.strip() for e in get("directories", "exclude", "").split(",") if e.strip()
        ],
        data_dir=_expand(get("storage", "data_dir", d.data_dir)),
        username=get("auth", "username", d.username),
        password_hash=get("auth", "password_hash", d.password_hash),
        source=path,
    )
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    if not 1 <= cfg.port <= 65535:
        raise ConfigError("[server] port must be between 1 and 65535")
    if cfg.poll_interval < 60:
        raise ConfigError("[monitor] poll_interval must be at least 60 seconds")
    if cfg.retention_days < 1:
        raise ConfigError("[monitor] retention_days must be at least 1")
    if not 0 < cfg.warning < cfg.high < cfg.critical <= 100:
        raise ConfigError(
            "[thresholds] must satisfy 0 < warning < high < critical <= 100"
        )
    if cfg.scan_interval < 600:
        raise ConfigError("[directories] scan_interval must be at least 600 seconds")
    if cfg.scan_timeout < 30:
        raise ConfigError("[directories] timeout must be at least 30 seconds")
    if not 1 <= cfg.top_n <= 100:
        raise ConfigError("[directories] top_n must be between 1 and 100")
    if not 1 <= cfg.scan_depth <= 8:
        raise ConfigError("[directories] depth must be between 1 and 8")
    if bool(cfg.username) != bool(cfg.password_hash):
        raise ConfigError("[auth] set both username and password_hash, or neither")
