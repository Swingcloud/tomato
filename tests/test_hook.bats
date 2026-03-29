#!/usr/bin/env bats
#
# Bats tests for tomato-hook.sh
#
# Each test overrides HOME to a temp directory so real user state is never touched.
# The hook reads ~/.tomato/state.json and exits 0 (allow) or 2 (block).

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
HOOK_SCRIPT="$REPO_ROOT/skill/bin/tomato-hook.sh"

setup() {
    TEST_HOME="$(mktemp -d)"
    export HOME="$TEST_HOME"
    export TOMATO_DIR="$HOME/.tomato"
    mkdir -p "$TOMATO_DIR/checkpoints"

    # Prevent checkpoint spawn and notifications during tests
    export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$(dirname "$(which jq)")"
}

teardown() {
    rm -rf "$TEST_HOME" 2>/dev/null || true
}

# Helper: write a state.json with the given fields merged onto defaults.
# Usage: write_state '{"phase":"rest","active_rest_minutes":5}'
write_state() {
    local now
    now="$(date +%s)"
    local defaults='{
        "active": true,
        "paused": false,
        "paused_at": null,
        "elapsed_before_pause": 0,
        "phase": "work",
        "started_at": '"$now"',
        "phase_started_at": '"$now"',
        "last_transition_at": '"$now"',
        "work_minutes": 25,
        "rest_minutes": 5,
        "long_rest_minutes": 15,
        "cycles_before_long_rest": 4,
        "current_cycle": 1,
        "max_cycles": null,
        "active_rest_minutes": null,
        "grace_session_id": null,
        "grace_last_call_at": null,
        "grace_started_at": null,
        "grace_timeout_sec": null,
        "grace_max_sec": null
    }'
    echo "$defaults" | jq ". + $1" > "$TOMATO_DIR/state.json"
}

# Helper: run the hook with optional JSON input on stdin.
# Call with: run run_hook [json_input]
run_hook() {
    local input="${1:-{}}"
    echo "$input" | bash "$HOOK_SCRIPT"
}

# =========================================================================
# No session / inactive
# =========================================================================

@test "no state file — allows tool call" {
    run run_hook
    [ "$status" -eq 0 ]
}

@test "empty state file — allows tool call" {
    touch "$TOMATO_DIR/state.json"
    run run_hook
    [ "$status" -eq 0 ]
}

@test "malformed JSON state file — allows tool call" {
    echo "not json" > "$TOMATO_DIR/state.json"
    run run_hook
    [ "$status" -eq 0 ]
}

@test "inactive session — allows tool call" {
    write_state '{"active": false}'
    run run_hook
    [ "$status" -eq 0 ]
}

# =========================================================================
# Work phase
# =========================================================================

@test "work phase with time remaining — allows tool call" {
    write_state '{"phase": "work"}'
    run run_hook
    [ "$status" -eq 0 ]
}

@test "work phase — appends PWD to active_dirs" {
    write_state '{"phase": "work"}'
    run run_hook
    [ "$status" -eq 0 ]
    [ -f "$TOMATO_DIR/active_dirs.txt" ]
    grep -q "$PWD" "$TOMATO_DIR/active_dirs.txt"
}

# =========================================================================
# Work → Rest transition
# =========================================================================

@test "work phase expired — blocks and transitions to rest" {
    local past=$(($(date +%s) - 1600))  # 26+ min ago
    write_state '{"phase": "work", "phase_started_at": '"$past"'}'
    run run_hook
    [ "$status" -eq 2 ]

    local phase
    phase="$(jq -r '.phase' "$TOMATO_DIR/state.json")"
    [ "$phase" = "rest" ]
}

@test "work→rest transition logs work_end and rest_start events" {
    local past=$(($(date +%s) - 1600))
    write_state '{"phase": "work", "phase_started_at": '"$past"'}'
    run run_hook

    grep -q '"event":"work_end"' "$TOMATO_DIR/history.jsonl"
    grep -q '"event":"rest_start"' "$TOMATO_DIR/history.jsonl"
}

@test "work→rest transition sets active_rest_minutes" {
    local past=$(($(date +%s) - 1600))
    write_state '{"phase": "work", "phase_started_at": '"$past"', "current_cycle": 1}'
    run run_hook

    local arm
    arm="$(jq -r '.active_rest_minutes' "$TOMATO_DIR/state.json")"
    [ "$arm" = "5" ]
}

@test "work→rest on 4th cycle gives long rest" {
    local past=$(($(date +%s) - 1600))
    write_state '{"phase": "work", "phase_started_at": '"$past"', "current_cycle": 4, "cycles_before_long_rest": 4}'
    run run_hook

    local arm
    arm="$(jq -r '.active_rest_minutes' "$TOMATO_DIR/state.json")"
    [ "$arm" = "15" ]
}

@test "work→rest shows break message on stderr" {
    local past=$(($(date +%s) - 1600))
    write_state '{"phase": "work", "phase_started_at": '"$past"'}'
    run run_hook
    [ "$status" -eq 2 ]
    [[ "$output" == *"Break"* ]]
}

# =========================================================================
# Rest phase
# =========================================================================

@test "rest phase with time remaining — blocks tool call" {
    write_state '{"phase": "rest", "active_rest_minutes": 5}'
    run run_hook
    [ "$status" -eq 2 ]
}

@test "rest phase shows remaining time and suggestion" {
    write_state '{"phase": "rest", "active_rest_minutes": 5}'
    run run_hook
    [ "$status" -eq 2 ]
    [[ "$output" == *"Resting"* ]]
}

# =========================================================================
# Rest → Work resume
# =========================================================================

@test "rest phase expired — allows and transitions to work" {
    local past=$(($(date +%s) - 400))  # 6+ min ago, rest is 5 min
    write_state '{"phase": "rest", "phase_started_at": '"$past"', "active_rest_minutes": 5, "current_cycle": 1}'
    run run_hook
    [ "$status" -eq 0 ]

    local phase
    phase="$(jq -r '.phase' "$TOMATO_DIR/state.json")"
    [ "$phase" = "work" ]
}

@test "rest→work increments cycle count" {
    local past=$(($(date +%s) - 400))
    write_state '{"phase": "rest", "phase_started_at": '"$past"', "active_rest_minutes": 5, "current_cycle": 1}'
    run run_hook
    [ "$status" -eq 0 ]

    local cycle
    cycle="$(jq -r '.current_cycle' "$TOMATO_DIR/state.json")"
    [ "$cycle" = "2" ]
}

@test "rest→work logs rest_end and work_start events" {
    local past=$(($(date +%s) - 400))
    write_state '{"phase": "rest", "phase_started_at": '"$past"', "active_rest_minutes": 5, "current_cycle": 1}'
    run run_hook

    grep -q '"event":"rest_end"' "$TOMATO_DIR/history.jsonl"
    grep -q '"event":"work_start"' "$TOMATO_DIR/history.jsonl"
}

# =========================================================================
# Max cycles (auto-stop)
# =========================================================================

@test "rest expired at max_cycles — auto-stops session" {
    local past=$(($(date +%s) - 400))
    write_state '{"phase": "rest", "phase_started_at": '"$past"', "active_rest_minutes": 5, "current_cycle": 3, "max_cycles": 3}'
    run run_hook
    [ "$status" -eq 0 ]

    local active
    active="$(jq -r '.active' "$TOMATO_DIR/state.json")"
    [ "$active" = "false" ]
}

@test "max_cycles auto-stop logs session_stop event" {
    local past=$(($(date +%s) - 400))
    write_state '{"phase": "rest", "phase_started_at": '"$past"', "active_rest_minutes": 5, "current_cycle": 3, "max_cycles": 3}'
    run run_hook

    grep -q '"reason":"max_cycles"' "$TOMATO_DIR/history.jsonl"
}

# =========================================================================
# Pause
# =========================================================================

@test "paused session — allows tool call (pause freezes timer, not enforcement)" {
    write_state '{"paused": true}'
    run run_hook
    [ "$status" -eq 0 ]
}

# =========================================================================
# Stale session detection
# =========================================================================

@test "stale session — auto-stops and allows" {
    local long_ago=$(($(date +%s) - 100000))  # way in the past
    write_state '{"phase": "work", "phase_started_at": '"$long_ago"'}'
    run run_hook
    [ "$status" -eq 0 ]

    local active
    active="$(jq -r '.active' "$TOMATO_DIR/state.json")"
    [ "$active" = "false" ]
}

@test "stale session logs session_stop with reason stale" {
    local long_ago=$(($(date +%s) - 100000))
    write_state '{"phase": "work", "phase_started_at": '"$long_ago"'}'
    run run_hook

    grep -q '"reason":"stale"' "$TOMATO_DIR/history.jsonl"
}

# =========================================================================
# CI/automation bypass
# =========================================================================

@test "CI env var — allows during rest" {
    write_state '{"phase": "rest", "active_rest_minutes": 5}'
    CI=true run run_hook
    [ "$status" -eq 0 ]
}

@test "GITHUB_ACTIONS env var — allows during rest" {
    write_state '{"phase": "rest", "active_rest_minutes": 5}'
    GITHUB_ACTIONS=true run run_hook
    [ "$status" -eq 0 ]
}

@test "CLAUDE_CODE_HEADLESS env var — allows during rest" {
    write_state '{"phase": "rest", "active_rest_minutes": 5}'
    CLAUDE_CODE_HEADLESS=1 run run_hook
    [ "$status" -eq 0 ]
}

# =========================================================================
# Whitelist: tomato-cli.py commands pass through
# =========================================================================

@test "tomato-cli.py command — allowed during rest" {
    write_state '{"phase": "rest", "active_rest_minutes": 5}'
    run run_hook '{"tool_input": {"command": "python3 /Users/test/.claude/skills/tomato/bin/tomato-cli.py status"}}'
    [ "$status" -eq 0 ]
}

@test "non-tomato command — blocked during rest" {
    write_state '{"phase": "rest", "active_rest_minutes": 5}'
    run run_hook '{"tool_input": {"command": "ls -la"}}'
    [ "$status" -eq 2 ]
}

# =========================================================================
# Grace period
# =========================================================================

@test "grace period — active session allowed during rest" {
    local now
    now="$(date +%s)"
    write_state '{"phase": "rest", "active_rest_minutes": 5, "grace_session_id": "sess-123", "grace_last_call_at": '"$now"', "grace_started_at": '"$now"'}'
    run run_hook '{"session_id": "sess-123"}'
    [ "$status" -eq 0 ]
}

@test "grace period — different session blocked during rest" {
    local now
    now="$(date +%s)"
    write_state '{"phase": "rest", "active_rest_minutes": 5, "grace_session_id": "sess-123", "grace_last_call_at": '"$now"', "grace_started_at": '"$now"'}'
    run run_hook '{"session_id": "sess-other"}'
    [ "$status" -eq 2 ]
}

@test "grace period — expires after hard cap" {
    local now
    now="$(date +%s)"
    local past=$((now - 400))  # 400s ago, default cap is 300s
    write_state '{"phase": "rest", "active_rest_minutes": 5, "grace_session_id": "sess-123", "grace_last_call_at": '"$now"', "grace_started_at": '"$past"'}'
    run run_hook '{"session_id": "sess-123"}'
    [ "$status" -eq 2 ]
}

@test "grace period — expires after inactivity gap" {
    local now
    now="$(date +%s)"
    local gap=$((now - 30))  # last call 30s ago, default timeout is 10s
    write_state '{"phase": "rest", "active_rest_minutes": 5, "grace_session_id": "sess-123", "grace_last_call_at": '"$gap"', "grace_started_at": '"$gap"'}'
    run run_hook '{"session_id": "sess-123"}'
    [ "$status" -eq 2 ]
}
