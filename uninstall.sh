#!/usr/bin/env bash
set -euo pipefail

SKILL_NAME="tomato"
SKILL_DIR="$HOME/.claude/skills/$SKILL_NAME"
TOMATO_DIR="$HOME/.tomato"
SETTINGS_FILE="$HOME/.claude/settings.json"

# ---------- Remove hook from settings.json ----------

echo "Removing Tomato hook from Claude Code settings..."

if [ ! -f "$SETTINGS_FILE" ]; then
  echo "  $SETTINGS_FILE not found. Nothing to remove."
else
  # Try jq first, fall back to python3
  if command -v jq &>/dev/null; then
    HOOK_COUNT=$(jq -r --arg cmd "$HOME/.claude/skills/tomato/bin/tomato-hook.sh" '
      .hooks.PreToolUse // [] | map(select(.hooks[]?.command == $cmd)) | length
    ' "$SETTINGS_FILE" 2>/dev/null || echo "0")

    if [ "$HOOK_COUNT" = "0" ] || [ -z "$HOOK_COUNT" ]; then
      echo "  Tomato hook not found in settings. Skipping."
    else
      TEMP_FILE=$(mktemp)
      jq --arg cmd "$HOME/.claude/skills/tomato/bin/tomato-hook.sh" '
        .hooks.PreToolUse = [.hooks.PreToolUse[] | select((.hooks[]?.command == $cmd) | not)]
      ' "$SETTINGS_FILE" > "$TEMP_FILE" && mv "$TEMP_FILE" "$SETTINGS_FILE"
      echo "  Removed Tomato hook from $SETTINGS_FILE."
    fi
  elif command -v python3 &>/dev/null; then
    python3 -c "
import json, sys

path = '$SETTINGS_FILE'
try:
    with open(path) as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    print('  Could not parse settings. Skipping hook removal.')
    sys.exit(0)

hooks = data.get('hooks', {}).get('PreToolUse', [])
original_len = len(hooks)
hooks = [h for h in hooks if not any(sub.get('command') == '$HOME/.claude/skills/tomato/bin/tomato-hook.sh' for sub in h.get('hooks', []))]

if len(hooks) == original_len:
    print('  Tomato hook not found in settings. Skipping.')
else:
    data['hooks']['PreToolUse'] = hooks
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    print('  Removed Tomato hook from $SETTINGS_FILE.')
"
  else
    echo "  Warning: neither jq nor python3 available. Cannot remove hook from settings."
    echo "  Please manually remove the tomato hook entry from $SETTINGS_FILE"
  fi
fi

# ---------- Remove skill directory ----------

echo "Removing skill files..."

if [ -d "$SKILL_DIR" ]; then
  rm -rf "$SKILL_DIR"
  echo "  Removed $SKILL_DIR/"
else
  echo "  $SKILL_DIR/ not found. Skipping."
fi

# ---------- Optionally remove history ----------

if [ -d "$TOMATO_DIR" ]; then
  echo ""
  read -rp "Keep your Tomato history? (y/N) " KEEP_HISTORY
  case "$KEEP_HISTORY" in
    [yY]|[yY][eE][sS])
      echo "  History preserved at $TOMATO_DIR/"
      ;;
    *)
      rm -rf "$TOMATO_DIR"
      echo "  Removed $TOMATO_DIR/"
      ;;
  esac
fi

# ---------- Done ----------

echo ""
echo "Tomato uninstalled."
