#!/usr/bin/env bash
#
# tomato-hook.sh — PreToolUse hook for the Tomato Pomodoro timer.
#
# Exit codes:
#   0 = allow tool call
#   2 = block tool call (stderr shown to user)
#
# Designed to be FAST (<50ms on the fast path).

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

TOMATO_DIR="$HOME/.tomato"
STATE_FILE="$TOMATO_DIR/state.json"
ERROR_LOG="$TOMATO_DIR/error.log"
HISTORY_FILE="$TOMATO_DIR/history.jsonl"
ACTIVE_DIRS="$TOMATO_DIR/active_dirs.txt"
LOCK_DIR="$TOMATO_DIR/state.lock"

log_error() {
    mkdir -p "$TOMATO_DIR"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $1" >> "$ERROR_LOG"
}

# Ensure jq is available
if ! command -v jq >/dev/null 2>&1; then
    log_error "WARN: jq not found in PATH, allowing tool call"
    exit 0
fi

# Ensure state dir exists
mkdir -p "$TOMATO_DIR"

# ---------------------------------------------------------------------------
# Step 1: Read state.json
# ---------------------------------------------------------------------------

if [ ! -f "$STATE_FILE" ]; then
    log_error "WARN: state.json not found, allowing tool call"
    exit 0
fi

STATE="$(cat "$STATE_FILE" 2>/dev/null)" || {
    log_error "WARN: failed to read state.json, allowing tool call"
    exit 0
}

# Validate JSON
if ! echo "$STATE" | jq empty 2>/dev/null; then
    log_error "WARN: state.json is not valid JSON, allowing tool call"
    exit 0
fi

# ---------------------------------------------------------------------------
# Step 2: Check if active
# ---------------------------------------------------------------------------

ACTIVE="$(echo "$STATE" | jq -r '.active')"
if [ "$ACTIVE" != "true" ]; then
    exit 0
fi

# ---------------------------------------------------------------------------
# Step 3: Human presence check
# ---------------------------------------------------------------------------

if [ -n "${CI:-}" ] || [ -n "${GITHUB_ACTIONS:-}" ] || [ -n "${GITLAB_CI:-}" ] || \
   [ -n "${JENKINS_URL:-}" ] || [ -n "${CLAUDE_CODE_HEADLESS:-}" ]; then
    exit 0
fi

# NOTE: Do NOT check [ -t 0 ] (TTY) here. Claude Code runs hooks as
# subprocesses without a TTY, so this check would skip enforcement on
# every single invocation. CI env vars above are sufficient for detecting
# automation environments.

# ---------------------------------------------------------------------------
# Step 4: Pause check
# ---------------------------------------------------------------------------

PAUSED="$(echo "$STATE" | jq -r '.paused')"
if [ "$PAUSED" = "true" ]; then
    echo "🍅 Tomato paused. Run /tomato resume to continue." >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Parse common state fields
# ---------------------------------------------------------------------------

NOW="$(date +%s)"

# ---------------------------------------------------------------------------
# Helpers (defined before use)
# ---------------------------------------------------------------------------

acquire_lock() {
    local attempts=0
    while [ "$attempts" -lt 10 ]; do
        if mkdir "$LOCK_DIR" 2>/dev/null; then
            return 0
        fi
        sleep 0.01
        attempts=$((attempts + 1))
    done
    return 1
}

release_lock() {
    rmdir "$LOCK_DIR" 2>/dev/null
}

format_time() {
    local ts="$1"
    # macOS uses -r, Linux uses -d @
    if date -r "$ts" +%H:%M 2>/dev/null; then
        return 0
    elif date -d "@$ts" +%H:%M 2>/dev/null; then
        return 0
    else
        echo "??:??"
        return 0
    fi
}

PHASE="$(echo "$STATE" | jq -r '.phase')"
PHASE_STARTED_AT="$(echo "$STATE" | jq -r '.phase_started_at')"
WORK_MINUTES="$(echo "$STATE" | jq -r '.work_minutes')"
REST_MINUTES="$(echo "$STATE" | jq -r '.rest_minutes')"
LONG_REST_MINUTES="$(echo "$STATE" | jq -r '.long_rest_minutes')"
CYCLES_BEFORE_LONG_REST="$(echo "$STATE" | jq -r '.cycles_before_long_rest')"
CURRENT_CYCLE="$(echo "$STATE" | jq -r '.current_cycle')"
MAX_CYCLES="$(echo "$STATE" | jq -r '.max_cycles')"
ACTIVE_REST_MINUTES="$(echo "$STATE" | jq -r '.active_rest_minutes')"

# ---------------------------------------------------------------------------
# Step 5: Stale detection
# ---------------------------------------------------------------------------

TOTAL_CYCLE_SECONDS=$(( (WORK_MINUTES + REST_MINUTES) * 60 * 2 ))
ELAPSED_SINCE_PHASE=$(( NOW - PHASE_STARTED_AT ))

if [ "$ELAPSED_SINCE_PHASE" -gt "$TOTAL_CYCLE_SECONDS" ]; then
    # Auto-stop: session is stale
    if acquire_lock; then
        # Update state: set active=false
        UPDATED="$(echo "$STATE" | jq --arg now "$NOW" '.active = false')"
        TMPFILE="$(mktemp "$TOMATO_DIR/state.tmp.XXXXXX")"
        echo "$UPDATED" > "$TMPFILE"
        mv "$TMPFILE" "$STATE_FILE"

        # Log session_stop
        echo "{\"event\":\"session_stop\",\"ts\":$NOW,\"reason\":\"stale\"}" >> "$HISTORY_FILE"

        release_lock
    fi
    # Whether we got the lock or not, allow the tool call
    exit 0
fi

# ---------------------------------------------------------------------------
# Calculate elapsed time since phase start
# ---------------------------------------------------------------------------

ELAPSED=$(( NOW - PHASE_STARTED_AT ))

# ---------------------------------------------------------------------------
# Step 7: Fast path — work phase, time remaining
# ---------------------------------------------------------------------------

if [ "$PHASE" = "work" ] && [ "$ELAPSED" -lt $((WORK_MINUTES * 60)) ]; then
    # Append pwd to active_dirs (fire and forget)
    echo "$PWD" >> "$ACTIVE_DIRS"
    exit 0
fi

# ---------------------------------------------------------------------------
# Step 8: Work → Rest transition
# ---------------------------------------------------------------------------

if [ "$PHASE" = "work" ] && [ "$ELAPSED" -ge $((WORK_MINUTES * 60)) ]; then
    if ! acquire_lock; then
        exit 2
    fi

    # Determine rest duration
    if [ "$CYCLES_BEFORE_LONG_REST" -gt 0 ] && \
       [ $((CURRENT_CYCLE % CYCLES_BEFORE_LONG_REST)) -eq 0 ]; then
        REST_TYPE="long"
        SELECTED_REST="$LONG_REST_MINUTES"
    else
        REST_TYPE="short"
        SELECTED_REST="$REST_MINUTES"
    fi

    # Update state
    UPDATED="$(echo "$STATE" | jq \
        --arg phase "rest" \
        --argjson arm "$SELECTED_REST" \
        --argjson now "$NOW" \
        '.phase = $phase | .active_rest_minutes = $arm | .phase_started_at = $now | .last_transition_at = $now'
    )"
    TMPFILE="$(mktemp "$TOMATO_DIR/state.tmp.XXXXXX")"
    echo "$UPDATED" > "$TMPFILE"
    mv "$TMPFILE" "$STATE_FILE"

    # Log work_end and rest_start
    echo "{\"event\":\"work_end\",\"ts\":$NOW,\"cycle\":$CURRENT_CYCLE,\"duration_sec\":$ELAPSED}" >> "$HISTORY_FILE"
    echo "{\"event\":\"rest_start\",\"ts\":$NOW,\"cycle\":$CURRENT_CYCLE,\"rest_type\":\"$REST_TYPE\",\"duration_min\":$SELECTED_REST}" >> "$HISTORY_FILE"

    # Try to spawn checkpoint (skip silently if python3 not found)
    if command -v python3 >/dev/null 2>&1; then
        nohup python3 ~/.claude/skills/tomato/bin/tomato-cli.py checkpoint --save >/dev/null 2>&1 &
    fi

    # Try macOS notification
    osascript -e 'display notification "Time for a break!" with title "🍅 Tomato"' 2>/dev/null &

    release_lock

    # Calculate rest end time and format
    REST_END_TS=$(( NOW + SELECTED_REST * 60 ))
    REST_END_TIME="$(format_time "$REST_END_TS")"

    echo "🍅 Break time! Rest until $REST_END_TIME ($SELECTED_REST min remaining). Run /tomato status to check." >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Step 9: Rest in progress
# ---------------------------------------------------------------------------

if [ "$PHASE" = "rest" ] && [ "$ELAPSED" -lt $((ACTIVE_REST_MINUTES * 60)) ]; then
    REMAINING_SEC=$(( ACTIVE_REST_MINUTES * 60 - ELAPSED ))
    REMAINING_MIN=$(( REMAINING_SEC / 60 ))
    REMAINING_SEC_PART=$(( REMAINING_SEC % 60 ))

    # Break suggestions
    SUGGESTIONS=(
        "Look at something 20 feet away for 20 seconds (20-20-20 rule)"
        "Stand up and stretch your wrists"
        "Walk to the kitchen. Get water."
        "Close your eyes for 30 seconds"
        "Roll your shoulders 5 times each direction"
        "Take 3 deep breaths"
        "Step outside for fresh air"
        "Do 10 squats or calf raises"
    )

    # Pick a random suggestion using RANDOM
    INDEX=$(( RANDOM % ${#SUGGESTIONS[@]} ))
    SUGGESTION="${SUGGESTIONS[$INDEX]}"

    echo "🍅 Resting... ${REMAINING_MIN}m ${REMAINING_SEC_PART}s remaining." >&2
    echo "💡 $SUGGESTION" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Step 10: Rest → Work resume
# ---------------------------------------------------------------------------

if [ "$PHASE" = "rest" ] && [ "$ELAPSED" -ge $((ACTIVE_REST_MINUTES * 60)) ]; then
    if ! acquire_lock; then
        exit 0
    fi

    # Check max_cycles
    if [ "$MAX_CYCLES" != "null" ] && [ "$CURRENT_CYCLE" -ge "$MAX_CYCLES" ]; then
        # Session complete — stop
        UPDATED="$(echo "$STATE" | jq --argjson now "$NOW" '.active = false')"
        TMPFILE="$(mktemp "$TOMATO_DIR/state.tmp.XXXXXX")"
        echo "$UPDATED" > "$TMPFILE"
        mv "$TMPFILE" "$STATE_FILE"

        echo "{\"event\":\"session_stop\",\"ts\":$NOW,\"reason\":\"max_cycles\"}" >> "$HISTORY_FILE"

        release_lock
        exit 0
    fi

    # Resume work: increment cycle, reset phase
    NEW_CYCLE=$(( CURRENT_CYCLE + 1 ))

    UPDATED="$(echo "$STATE" | jq \
        --arg phase "work" \
        --argjson cycle "$NEW_CYCLE" \
        --argjson now "$NOW" \
        '.phase = $phase | .current_cycle = $cycle | .phase_started_at = $now | .last_transition_at = $now | .active_rest_minutes = null'
    )"
    TMPFILE="$(mktemp "$TOMATO_DIR/state.tmp.XXXXXX")"
    echo "$UPDATED" > "$TMPFILE"
    mv "$TMPFILE" "$STATE_FILE"

    # Clear active_dirs
    : > "$ACTIVE_DIRS"

    # Log rest_end and work_start
    echo "{\"event\":\"rest_end\",\"ts\":$NOW,\"cycle\":$CURRENT_CYCLE}" >> "$HISTORY_FILE"
    echo "{\"event\":\"work_start\",\"ts\":$NOW,\"cycle\":$NEW_CYCLE}" >> "$HISTORY_FILE"

    release_lock
    exit 0
fi

# Fallback: allow
exit 0
