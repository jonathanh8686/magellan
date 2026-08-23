#!/usr/bin/env bash
# Run ON omashu, inside ~/magellan, to apply a code change that's already
# been `git pull`ed: re-syncs prod dependencies and restarts the systemd
# service. Doesn't touch .env or data/ — those persist across deploys.
#
# Deliberately does NOT run `git pull` itself — a script that rewrites its
# own file mid-execution (via git pull) gets bash reading stale in-memory
# content for the rest of the run, which silently skips/garbles later
# lines. Pull first, as a separate command, then run this:
#   cd ~/magellan && git pull && ./deploy/redeploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# Full path, not just `uv` — ~/.local/bin is only on PATH via shell rc
# files, which non-interactive `ssh host 'cmd'` invocations don't source.
UV="$HOME/.local/bin/uv"

"$UV" sync --no-dev
sudo systemctl restart magellan-bot
sleep 2
sudo systemctl status magellan-bot --no-pager -l
