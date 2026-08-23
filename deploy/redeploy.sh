#!/usr/bin/env bash
# Run ON omashu, inside ~/magellan, after `git pull`, to apply a code
# change: re-syncs prod dependencies and restarts the systemd service.
# Doesn't touch .env or data/ — those persist across deploys.
set -euo pipefail
cd "$(dirname "$0")/.."

# Full path, not just `uv` — ~/.local/bin is only on PATH via shell rc
# files, which non-interactive `ssh host 'cmd'` invocations don't source.
UV="$HOME/.local/bin/uv"

git pull
"$UV" sync --no-dev
sudo systemctl restart magellan-bot
sleep 2
sudo systemctl status magellan-bot --no-pager -l
