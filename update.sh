#!/usr/bin/env bash
# Update Headroom to the latest code and restart it.
#
# Usage:  bash update.sh                 update now
#         bash update.sh --auto          used by the timer: quiet when there is
#                                        nothing new, skips a version that
#                                        already failed
#         bash update.sh --enable-auto   check for updates every hour
#         bash update.sh --disable-auto  stop checking automatically
#
# An update pulls the code, reinstalls packages if requirements.txt changed,
# checks the new version, restarts the service and waits for it to answer.
# If any step after the pull fails, it goes back to the previous version.
# Your config and history live outside this directory and are never touched.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="${HEADROOM_CONFIG:-$HOME/.config/headroom/config.ini}"
DATA_DIR="$HOME/.local/share/headroom"
UNIT_DIR="$HOME/.config/systemd/user"
VENV_PY="$APP_DIR/.venv/bin/python"
FAILED_MARK="$DATA_DIR/update-failed"
MODE="${1:-now}"

say() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die() { say "ERROR: $*" >&2; exit 1; }

if [ "$(id -u)" = "0" ]; then die "Do not run this as root."; fi

# Render a unit from deploy/ for this checkout's location.
install_unit() {
  sed "s|%h/headroom|$APP_DIR|g" "$APP_DIR/deploy/$1" > "$UNIT_DIR/$1"
}

case "$MODE" in
  --enable-auto)
    mkdir -p "$UNIT_DIR"
    install_unit headroom-update.service
    install_unit headroom-update.timer
    systemctl --user daemon-reload
    systemctl --user enable --now headroom-update.timer
    say "Automatic updates on. Next check:"
    systemctl --user list-timers headroom-update.timer --no-pager | sed -n 2p
    say "Log: $DATA_DIR/update.log"
    exit 0 ;;
  --disable-auto)
    systemctl --user disable --now headroom-update.timer 2>/dev/null || true
    rm -f "$UNIT_DIR/headroom-update.timer" "$UNIT_DIR/headroom-update.service"
    systemctl --user daemon-reload
    say "Automatic updates off."
    exit 0 ;;
  now|--auto) ;;
  *) echo "Unknown option: $MODE (see the top of update.sh)" >&2; exit 2 ;;
esac

mkdir -p "$DATA_DIR"
cd "$APP_DIR"

# Never run two updates at once (timer and a manual run).
if command -v flock >/dev/null; then
  exec 9>"$DATA_DIR/update.lock"
  flock -n 9 || die "Another update is already running."
fi

[ -x "$VENV_PY" ] || die "No virtualenv in $APP_DIR/.venv. Run: bash install.sh"
git rev-parse --abbrev-ref '@{upstream}' >/dev/null 2>&1 \
  || die "This checkout does not track a remote branch. Was it cloned with git?"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  die "The code in $APP_DIR has local edits; not updating. See 'Updating' in docs/INSTALL-ULTRACC.md."
fi

git fetch --quiet
OLD="$(git rev-parse HEAD)"
NEW="$(git rev-parse '@{upstream}')"

if [ "$OLD" = "$NEW" ]; then
  [ "$MODE" = "--auto" ] || say "Already up to date ($(git rev-parse --short HEAD))."
  exit 0
fi
if ! git merge-base --is-ancestor "$OLD" "$NEW"; then
  die "The local code has commits that are not on GitHub; not updating."
fi
if [ "$MODE" = "--auto" ] && [ "$(cat "$FAILED_MARK" 2>/dev/null)" = "$NEW" ]; then
  exit 0   # this version already failed; wait for a newer one
fi

say "Updating $(git rev-parse --short "$OLD") -> $(git rev-parse --short "$NEW")"
git log --oneline --no-decorate "$OLD..$NEW" | sed 's/^/    /'

# Switch the code, packages and unit file to commit $1. The tree was checked
# clean above, so reset --hard only moves between the two commits. Errors are
# returned explicitly because set -e is off inside "cmd || rollback".
switch_to() {
  git reset --quiet --hard "$1" || return 1
  if ! git diff --quiet "$OLD" "$NEW" -- requirements.txt; then
    say "requirements.txt changed; updating packages"
    "$VENV_PY" -m pip install --quiet --disable-pip-version-check \
      -r requirements.txt || return 1
  fi
  if ! git diff --quiet "$OLD" "$NEW" -- deploy/headroom.service; then
    install_unit headroom.service || return 1
    systemctl --user daemon-reload || return 1
  fi
}

# Exit 0 once /healthz answers, or fail after about 30 seconds.
wait_healthy() {
  "$VENV_PY" - "$CONF" <<'PY'
import sys, time, urllib.request
from headroom.config import load_config
cfg = load_config(sys.argv[1])
host = "127.0.0.1" if cfg.host in ("0.0.0.0", "", "::") else cfg.host
url = "http://%s:%d%s/healthz" % (host, cfg.port, cfg.base_url)
for _ in range(30):
    time.sleep(1)
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            if r.status == 200:
                sys.exit(0)
    except Exception:
        pass
print("No answer from %s" % url, file=sys.stderr)
sys.exit(1)
PY
}

rollback() {
  say "Update failed: $1. Going back to $(git rev-parse --short "$OLD")."
  echo "$NEW" > "$FAILED_MARK"
  switch_to "$OLD"
  systemctl --user restart headroom.service
  if wait_healthy; then
    say "Rolled back; Headroom is running the previous version."
  else
    say "Rolled back, but Headroom is not answering. Check $DATA_DIR/service.log"
  fi
  exit 1
}

switch_to "$NEW" || rollback "could not install the new version"

# Catch broken code before stopping the running version.
"$VENV_PY" -c 'import headroom.app, headroom.monitor, headroom.storage, waitress' \
  || rollback "the new code does not load"
"$VENV_PY" -m headroom --config "$CONF" --check >/dev/null \
  || rollback "'python -m headroom --check' failed"

systemctl --user restart headroom.service
wait_healthy || rollback "the service did not start"

rm -f "$FAILED_MARK"
say "Updated to $(git rev-parse --short HEAD); Headroom is running."
