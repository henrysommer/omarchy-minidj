#!/bin/bash
# Development helper: copy this checkout into the Omarchy plugin folder (the
# shell hot-reloads it) and link the launchers into ~/.local/bin.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ID="$(jq -r .id "$ROOT/manifest.json")"
DEST="$HOME/.config/omarchy/plugins/$ID"

omarchy plugin validate "$ROOT"
mkdir -p "$DEST"
rsync -a --delete --exclude .git --exclude __pycache__ --exclude .venv "$ROOT/" "$DEST/"
mkdir -p ~/.local/bin
ln -sf "$DEST/bin/minidj" ~/.local/bin/minidj
ln -sf "$DEST/bin/minidj-launch" ~/.local/bin/minidj-launch
echo "Synced to $DEST"
