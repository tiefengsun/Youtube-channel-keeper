#!/usr/bin/env bash
set -euo pipefail

project_dir="$HOME/channel-keeper"
if [[ "$PWD" != "$project_dir" ]]; then
  printf 'Please run this script from %s\n' "$project_dir" >&2
  exit 1
fi
if [[ "$(id -u)" == 0 ]]; then
  printf 'Run as the normal login user, not root.\n' >&2
  exit 1
fi
for program in python3 node ffmpeg ffprobe; do
  if ! command -v "$program" >/dev/null 2>&1; then
    printf 'Missing dependency: %s\n' "$program" >&2
    exit 1
  fi
done
if (( $(node -p 'Number(process.versions.node.split(".")[0])') < 20 )); then
  printf 'Node.js 20 or newer is required for YouTube extraction.\n' >&2
  exit 1
fi

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p data downloads "$HOME/.config/systemd/user"
chmod 700 data downloads
install -m 644 deploy/channel-keeper.service "$HOME/.config/systemd/user/channel-keeper.service"
systemctl --user daemon-reload
systemctl --user enable --now channel-keeper.service
systemctl --user --no-pager status channel-keeper.service
