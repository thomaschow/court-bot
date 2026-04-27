#!/usr/bin/env bash
set -euo pipefail
AGENTS="$HOME/Library/LaunchAgents"
for plist in "$AGENTS"/ai.zipline.courtbot.*.plist; do
  [ -f "$plist" ] || continue
  label=$(basename "$plist" .plist)
  launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
  rm -f "$plist"
  echo "removed: $label"
done
