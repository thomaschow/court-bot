#!/usr/bin/env bash
# Render launchd plists and load them as user agents.
# Run from the repo root: scripts/install_launchd.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COURTBOT_BIN="${COURTBOT_BIN:-$ROOT/.venv/bin/courtbot}"
CONFIG_PATH="${COURTBOT_CONFIG:-$ROOT/config/config.yaml}"
LOG_DIR="$ROOT/state/logs"
AGENTS="$HOME/Library/LaunchAgents"

mkdir -p "$LOG_DIR" "$AGENTS"

if [ ! -x "$COURTBOT_BIN" ]; then
  echo "ERROR: $COURTBOT_BIN not executable. Run scripts/setup.sh first." >&2
  exit 1
fi

render() {
  local tmpl="$1" out="$2"
  shift 2
  local content
  content="$(cat "$tmpl")"
  while [ $# -gt 0 ]; do
    local key="$1" val="$2"
    shift 2
    # Escape '/' for sed.
    local val_esc
    val_esc=$(printf '%s\n' "$val" | sed 's/[\/&]/\\&/g')
    content=$(printf '%s\n' "$content" | sed "s/{{$key}}/$val_esc/g")
  done
  printf '%s\n' "$content" > "$out"
}

# Watcher
render "$ROOT/launchd/ai.zipline.courtbot.watcher.plist.tmpl" \
       "$AGENTS/ai.zipline.courtbot.watcher.plist" \
       COURTBOT_BIN "$COURTBOT_BIN" CONFIG_PATH "$CONFIG_PATH" LOG_DIR "$LOG_DIR"

# Wake scheduler
render "$ROOT/launchd/ai.zipline.courtbot.wake.plist.tmpl" \
       "$AGENTS/ai.zipline.courtbot.wake.plist" \
       COURTBOT_BIN "$COURTBOT_BIN" CONFIG_PATH "$CONFIG_PATH" LOG_DIR "$LOG_DIR"

# Web dashboard
render "$ROOT/launchd/ai.zipline.courtbot.web.plist.tmpl" \
       "$AGENTS/ai.zipline.courtbot.web.plist" \
       COURTBOT_BIN "$COURTBOT_BIN" CONFIG_PATH "$CONFIG_PATH" LOG_DIR "$LOG_DIR"

# Per-facility racer plists are rendered by `courtbot schedule-wake`
# (it has access to parsed config).

for label in ai.zipline.courtbot.watcher ai.zipline.courtbot.wake ai.zipline.courtbot.web; do
  launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$AGENTS/$label.plist"
  echo "loaded: $label"
done

echo
echo "Run \`launchctl list | grep courtbot\` to verify."
echo "Per-facility racer plists are managed by \`courtbot schedule-wake\`."
