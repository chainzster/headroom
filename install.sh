#!/usr/bin/env bash
# Headroom installer for Ultra.cc (no root, no Docker).
#
# Usage:  bash install.sh [PORT] [BASE_URL]
#   PORT      a port assigned to your account (only used when creating config.ini)
#   BASE_URL  URL path for the Nginx proxy, default /headroom
#
# Safe to re-run: it updates the virtualenv and service file but never
# overwrites an existing config.ini.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_DIR="$HOME/.config/headroom"
CONF="$CONF_DIR/config.ini"
DATA_DIR="$HOME/.local/share/headroom"
UNIT_DIR="$HOME/.config/systemd/user"
PORT="${1:-}"
BASE_URL="${2:-/headroom}"
PYTHON="${PYTHON:-python3}"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

if [ "$(id -u)" = "0" ]; then
  echo "Do not run this as root." >&2; exit 1
fi

if ! command -v "$PYTHON" >/dev/null; then
  echo "python3 not found. Install Python first (see INSTALL-ULTRACC.md)." >&2; exit 1
fi
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || {
  echo "Python 3.8 or newer is required (found $("$PYTHON" -V 2>&1))." >&2; exit 1; }

if ! command -v quota >/dev/null; then
  echo "Warning: 'quota' command not found in PATH; Headroom will report an error until it is available." >&2
fi

say "Creating virtualenv in $APP_DIR/.venv"
"$PYTHON" -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --quiet --upgrade pip
"$APP_DIR/.venv/bin/python" -m pip install --quiet -r "$APP_DIR/requirements.txt"

mkdir -p "$CONF_DIR" "$DATA_DIR" "$UNIT_DIR"

if [ -f "$CONF" ]; then
  say "Keeping existing config: $CONF"
else
  if [ -z "$PORT" ]; then
    read -r -p "Port assigned to your Ultra.cc account: " PORT
  fi
  case "$PORT" in (''|*[!0-9]*) echo "Invalid port: '$PORT'" >&2; exit 1;; esac
  sed -e "s|^port = .*|port = $PORT|" -e "s|^base_url = .*|base_url = $BASE_URL|" \
      "$APP_DIR/config.example.ini" > "$CONF"
  say "Created config: $CONF (port $PORT, base_url $BASE_URL)"
fi

# The unit assumes the app lives in ~/headroom; adjust if cloned elsewhere.
sed "s|%h/headroom|$APP_DIR|g" "$APP_DIR/deploy/headroom.service" > "$UNIT_DIR/headroom.service"
say "Installed systemd unit: $UNIT_DIR/headroom.service"

say "Checking configuration and quota"
"$APP_DIR/.venv/bin/python" -m headroom --config "$CONF" --check || \
  echo "Warning: the check failed; see the message above." >&2

systemctl --user daemon-reload
systemctl --user enable headroom.service >/dev/null
systemctl --user restart headroom.service
sleep 2
systemctl --user --no-pager status headroom.service | head -n 5 || true

CONF_PORT="$(sed -n 's/^port *= *//p' "$CONF" | head -n1)"
CONF_BASE="$(sed -n 's/^base_url *= *//p' "$CONF" | head -n1)"
cat <<MSG

Headroom is running on 127.0.0.1:$CONF_PORT.

To publish it over HTTPS through Ultra.cc's Nginx:
  sed -e "s|12345|$CONF_PORT|" -e "s|/headroom/|${CONF_BASE:-/headroom}/|" \\
      "$APP_DIR/deploy/headroom.nginx.conf" > ~/.apps/nginx/proxy.d/headroom.conf
  app-nginx restart

Then open https://<username>.<hostname>.usbx.me${CONF_BASE}/
Consider enabling [auth] in $CONF first.
MSG
