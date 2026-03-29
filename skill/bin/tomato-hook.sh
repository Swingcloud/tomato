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
GRACE_TIMEOUT_SEC=10   # gap between tool calls before grace expires
GRACE_MAX_SEC=120      # hard cap on total grace duration
GRACE_CLEAR='.grace_session_id = null | .grace_last_call_at = null | .grace_started_at = null'

log_error() {
    mkdir -p "$TOMATO_DIR"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $1" >> "$ERROR_LOG"
}

# Ensure jq is available
if ! command -v jq >/dev/null 2>&1; then
    log_error "WARN: jq not found in PATH, allowing tool call"
    exit 0
fi

# ---------------------------------------------------------------------------
# Step 0: Read hook input and whitelist tomato-cli.py commands
# ---------------------------------------------------------------------------
# Claude Code passes tool info via stdin JSON. Allow tomato-cli.py calls
# even during rest/pause — otherwise the user can't stop/resume.

HOOK_INPUT="$(cat 2>/dev/null)" || HOOK_INPUT=""
SESSION_ID=""
if [ -n "$HOOK_INPUT" ]; then
    TOOL_CMD="$(echo "$HOOK_INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)"
    if echo "$TOOL_CMD" | grep -qE '(^|/)python3? .+tomato-cli\.py( |$)' 2>/dev/null; then
        exit 0
    fi
    SESSION_ID="$(echo "$HOOK_INPUT" | jq -r '.session_id // empty' 2>/dev/null)"
fi

# ---------------------------------------------------------------------------
# Step 1: Read state.json — single jq call extracts all fields
# ---------------------------------------------------------------------------

# Single jq call to extract all fields (saves ~15-30ms vs multiple spawns)
ALL_FIELDS="$(jq -r '[.active, .paused, .phase, .phase_started_at, .work_minutes, .rest_minutes, .long_rest_minutes, .cycles_before_long_rest, .current_cycle, (.max_cycles // "null"), (.active_rest_minutes // "null"), (.grace_session_id // "null"), (.grace_last_call_at // "null"), (.grace_started_at // "null")] | @tsv' "$STATE_FILE" 2>/dev/null)" || {
    log_error "WARN: failed to parse state.json, allowing tool call"
    exit 0
}

# If jq returned empty (invalid JSON), allow
[ -z "$ALL_FIELDS" ] && exit 0

read -r ACTIVE PAUSED PHASE PHASE_STARTED_AT WORK_MINUTES REST_MINUTES LONG_REST_MINUTES CYCLES_BEFORE_LONG_REST CURRENT_CYCLE MAX_CYCLES ACTIVE_REST_MINUTES GRACE_SESSION_ID GRACE_LAST_CALL_AT GRACE_STARTED_AT <<< "$ALL_FIELDS"

# ---------------------------------------------------------------------------
# Step 2: Check if active
# ---------------------------------------------------------------------------

[ "$ACTIVE" != "true" ] && exit 0

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

write_state() {
    local content="$1"
    local tmp
    tmp="$(mktemp "$TOMATO_DIR/state.tmp.XXXXXX")"
    echo "$content" > "$tmp"
    mv "$tmp" "$STATE_FILE"
}

log_event() {
    echo "$1" >> "$HISTORY_FILE"
}

# We need STATE for jq updates — read the file once into a variable.
STATE="$(cat "$STATE_FILE" 2>/dev/null)" || STATE=""

# ---------------------------------------------------------------------------
# Step 5: Stale detection
# ---------------------------------------------------------------------------

TOTAL_CYCLE_SECONDS=$(( (WORK_MINUTES + REST_MINUTES) * 60 * 2 ))
ELAPSED_SINCE_PHASE=$(( NOW - PHASE_STARTED_AT ))

if [ "$ELAPSED_SINCE_PHASE" -gt "$TOTAL_CYCLE_SECONDS" ]; then
    # Auto-stop: session is stale
    if acquire_lock; then
        # Re-read state inside lock to avoid stale snapshot
        STATE="$(cat "$STATE_FILE" 2>/dev/null)" || STATE=""
        # Update state: set active=false
        UPDATED="$(echo "$STATE" | jq --arg now "$NOW" '.active = false')"
        write_state "$UPDATED"

        # Log session_stop
        log_event "{\"event\":\"session_stop\",\"ts\":$NOW,\"reason\":\"stale\"}"

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

    # Re-read state inside lock to avoid stale snapshot
    STATE="$(cat "$STATE_FILE" 2>/dev/null)" || STATE=""

    # Determine rest duration
    if [ "$CYCLES_BEFORE_LONG_REST" -gt 0 ] && \
       [ $((CURRENT_CYCLE % CYCLES_BEFORE_LONG_REST)) -eq 0 ]; then
        REST_TYPE="long"
        SELECTED_REST="$LONG_REST_MINUTES"
    else
        REST_TYPE="short"
        SELECTED_REST="$REST_MINUTES"
    fi

    # Update state (including grace for the active session to finish its task)
    GRACE_SID="${SESSION_ID:-null}"
    if [ "$GRACE_SID" = "" ] || [ "$GRACE_SID" = "null" ]; then
        GRACE_SID="null"
        GRACE_JQ="$GRACE_CLEAR"
    else
        GRACE_JQ='.grace_session_id = $gsid | .grace_last_call_at = $now | .grace_started_at = $now'
    fi
    UPDATED="$(echo "$STATE" | jq \
        --arg phase "rest" \
        --argjson arm "$SELECTED_REST" \
        --argjson now "$NOW" \
        --arg gsid "$GRACE_SID" \
        ".phase = \$phase | .active_rest_minutes = \$arm | .phase_started_at = \$now | .last_transition_at = \$now | $GRACE_JQ"
    )"
    write_state "$UPDATED"

    # Log work_end and rest_start
    log_event "{\"event\":\"work_end\",\"ts\":$NOW,\"cycle\":$CURRENT_CYCLE,\"duration_sec\":$ELAPSED}"
    log_event "{\"event\":\"rest_start\",\"ts\":$NOW,\"cycle\":$CURRENT_CYCLE,\"rest_type\":\"$REST_TYPE\",\"duration_min\":$SELECTED_REST}"

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
    # Grace period: let the active session finish its current task
    if [ "$GRACE_SESSION_ID" != "null" ] && [ -n "$SESSION_ID" ] && \
       [ "$SESSION_ID" = "$GRACE_SESSION_ID" ]; then
        # Check hard cap first
        if [ "$GRACE_STARTED_AT" != "null" ] && \
           [ $((NOW - GRACE_STARTED_AT)) -ge "$GRACE_MAX_SEC" ]; then
            # Hard cap reached — clear grace, fall through to block
            UPDATED="$(echo "$(cat "$STATE_FILE")" | jq "$GRACE_CLEAR")"
            write_state "$UPDATED"
        elif [ "$GRACE_LAST_CALL_AT" != "null" ] && \
             [ $((NOW - GRACE_LAST_CALL_AT)) -lt "$GRACE_TIMEOUT_SEC" ]; then
            # Within grace window — allow and update timestamp
            UPDATED="$(echo "$(cat "$STATE_FILE")" | jq --argjson now "$NOW" \
                '.grace_last_call_at = $now')"
            write_state "$UPDATED"
            exit 0
        else
            # Grace expired (inactivity) — clear grace, fall through to block
            UPDATED="$(echo "$(cat "$STATE_FILE")" | jq "$GRACE_CLEAR")"
            write_state "$UPDATED"
        fi
    fi

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

    # Re-read state inside lock to avoid stale snapshot
    STATE="$(cat "$STATE_FILE" 2>/dev/null)" || STATE=""

    # Check max_cycles
    if [ "$MAX_CYCLES" != "null" ] && [ "$CURRENT_CYCLE" -ge "$MAX_CYCLES" ]; then
        # Session complete — stop
        UPDATED="$(echo "$STATE" | jq --argjson now "$NOW" '.active = false')"
        write_state "$UPDATED"

        log_event "{\"event\":\"session_stop\",\"ts\":$NOW,\"reason\":\"max_cycles\"}"

        release_lock
        exit 0
    fi

    # Resume work: increment cycle, reset phase
    NEW_CYCLE=$(( CURRENT_CYCLE + 1 ))

    UPDATED="$(echo "$STATE" | jq \
        --arg phase "work" \
        --argjson cycle "$NEW_CYCLE" \
        --argjson now "$NOW" \
        ".phase = \$phase | .current_cycle = \$cycle | .phase_started_at = \$now | .last_transition_at = \$now | .active_rest_minutes = null | $GRACE_CLEAR"
    )"
    write_state "$UPDATED"

    # Clear active_dirs
    : > "$ACTIVE_DIRS"

    # Log rest_end and work_start
    log_event "{\"event\":\"rest_end\",\"ts\":$NOW,\"cycle\":$CURRENT_CYCLE}"
    log_event "{\"event\":\"work_start\",\"ts\":$NOW,\"cycle\":$NEW_CYCLE}"

    release_lock
    exit 0
fi

# Fallback: allow
exit 0
