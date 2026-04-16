#!/usr/bin/env bash
set -euo pipefail

SKILL_NAME="tomato"
SKILL_DIR="$HOME/.claude/skills/$SKILL_NAME"
TOMATO_DIR="$HOME/.tomato"
SETTINGS_FILE="$HOME/.claude/settings.json"
HOOK_CMD="$HOME/.claude/skills/tomato/bin/tomato-hook.sh"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------- Dependency checks ----------

if ! command -v jq &>/dev/null; then
  echo "Error: jq is required but not installed."
  echo ""
  echo "Install it with one of:"
  echo "  macOS:   brew install jq"
  echo "  Ubuntu:  sudo apt-get install jq"
  echo "  Fedora:  sudo dnf install jq"
  exit 1
fi

if ! command -v python3 &>/dev/null; then
  echo "Warning: python3 not found. Checkpoint saving and /tomato stats will not work."
  echo "Continuing installation anyway..."
  echo ""
fi

# ---------- Create data directories ----------

echo "Creating $TOMATO_DIR/ ..."
mkdir -p "$TOMATO_DIR/checkpoints"

# ---------- Copy skill files ----------

echo "Installing skill to $SKILL_DIR/ ..."
mkdir -p "$SKILL_DIR"
cp -R "$SCRIPT_DIR/skills/tomato/" "$SKILL_DIR/"

# ---------- Make scripts executable ----------

if [ -d "$SKILL_DIR/bin" ]; then
  chmod +x "$SKILL_DIR/bin/"* 2>/dev/null || true
fi

# ---------- Register PreToolUse hook in settings.json ----------

echo "Configuring Claude Code hook..."

# The hook entry we want to add (using correct nested format with matcher)
HOOK_ENTRY=$(cat <<HOOKJSON
{
  "matcher": "*",
  "hooks": [{
    "type": "command",
    "command": "$HOOK_CMD"
  }]
}
HOOKJSON
)

if [ ! -f "$SETTINGS_FILE" ]; then
  # settings.json does not exist — create it with our hook
  mkdir -p "$(dirname "$SETTINGS_FILE")"
  TEMP_FILE=$(mktemp)
  jq -n --argjson hook "$HOOK_ENTRY" '{hooks: {PreToolUse: [$hook]}}' > "$TEMP_FILE" && mv "$TEMP_FILE" "$SETTINGS_FILE"
  echo "  Created $SETTINGS_FILE with Tomato hook."
else
  # settings.json exists — check if our hook is already registered
  EXISTING=$(jq -r --arg cmd "$HOOK_CMD" '
    .hooks.PreToolUse // [] | map(select(.hooks[]?.command == $cmd)) | length
  ' "$SETTINGS_FILE" 2>/dev/null || echo "0")

  if [ "$EXISTING" != "0" ] && [ "$EXISTING" != "" ]; then
    echo "  Tomato hook already registered in $SETTINGS_FILE. Skipping."
  else
    TEMP_FILE=$(mktemp)
    jq --argjson hook "$HOOK_ENTRY" '
      .hooks //= {} |
      .hooks.PreToolUse //= [] |
      .hooks.PreToolUse += [$hook]
    ' "$SETTINGS_FILE" > "$TEMP_FILE" && mv "$TEMP_FILE" "$SETTINGS_FILE"
    echo "  Added Tomato hook to $SETTINGS_FILE."
  fi
fi

# ---------- Done ----------

echo ""
echo "Tomato installed! Type /tomato start in any Claude Code session."
