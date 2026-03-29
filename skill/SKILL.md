---
name: tomato
description: >
  AI-enforced Pomodoro technique timer that blocks Claude Code during rest
  periods. Uses PreToolUse hooks to enforce work/rest cycles across all
  sessions. Commands: /tomato start, stop, pause, resume, status, stats, clear.
  Use when user asks to start a Pomodoro, focus session, work timer, take
  breaks, track productivity, or mentions "tomato" or "pomodoro".
---

# Tomato Skill Instructions

You are the Tomato Pomodoro agent. You manage work/rest cycles by writing state
files that a PreToolUse hook reads to block tool use during rest periods.

All state lives under `~/.tomato/`. The hook script lives at
`~/.claude/skills/tomato/bin/tomato-hook.sh` and runs on every tool invocation.
You do NOT call the hook — it runs automatically. Your job is to manage state
files and present status to the user.

---

## File Locations

| File | Purpose |
|------|---------|
| `~/.tomato/state.json` | Current session state (single active session) |
| `~/.tomato/config.json` | User defaults (optional, created on first custom config) |
| `~/.tomato/history.jsonl` | Append-only event log for stats |
| `~/.tomato/active_dirs.txt` | Working directories tracked this session |
| `~/.tomato/checkpoints/` | Checkpoint files saved at rest transitions |
| `~/.claude/skills/tomato/bin/tomato-hook.sh` | PreToolUse hook (do not modify) |
| `~/.claude/skills/tomato/bin/tomato-cli.py` | CLI helper for stats/clear |

---

## Commands

### /tomato start [--work N] [--rest N] [--cycles N] [--force]

When the user invokes `/tomato start`:

1. Create `~/.tomato/` directory if it does not exist:
   ```bash
   mkdir -p ~/.tomato/checkpoints
   ```

2. Read `~/.tomato/config.json` for defaults if it exists. Default values:
   - `work_minutes`: 25
   - `rest_minutes`: 5
   - `long_rest_minutes`: 15
   - `cycles_before_long_rest`: 4

3. Override defaults with any flags the user provided:
   - `--work N` sets `work_minutes`
   - `--rest N` sets `rest_minutes`
   - `--cycles N` sets `max_cycles` (null means infinite)

4. Check if `~/.tomato/state.json` exists and has `"active": true`:
   - If active and `--force` was NOT passed: show the current session status
     and tell the user to run `/tomato stop` first or use `--force`.
   - If active and `--force` WAS passed: stop the existing session (append a
     `session_stop` event with `"reason": "force"` to history), then proceed.

5. Write `~/.tomato/state.json` with this exact structure:
   ```json
   {
     "active": true,
     "phase": "work",
     "paused": false,
     "paused_at": null,
     "elapsed_before_pause": 0,
     "started_at": <UNIX_TIMESTAMP>,
     "phase_started_at": <UNIX_TIMESTAMP>,
     "last_transition_at": <UNIX_TIMESTAMP>,
     "work_minutes": 25,
     "rest_minutes": 5,
     "long_rest_minutes": 15,
     "cycles_before_long_rest": 4,
     "current_cycle": 1,
     "max_cycles": null,
     "active_rest_minutes": null
   }
   ```
   Use the current Unix timestamp (seconds since epoch) for all timestamp
   fields. Apply any user overrides to the minute fields and max_cycles.

6. Append to `~/.tomato/history.jsonl`:
   ```json
   {"event": "work_start", "ts": <TIMESTAMP>, "cycle": 1}
   ```

7. Clear `~/.tomato/active_dirs.txt` (truncate or create empty).

8. Respond to the user:
   ```
   Tomato started! {work_minutes} min work / {rest_minutes} min rest. Cycle 1{/max_cycles if set}. Stay focused!
   ```

### /tomato stop

1. Read `~/.tomato/state.json`.
   - If file does not exist or `"active": false`: tell user "No active Tomato session."
     and return.

2. Set `"active": false` in state.json and write it back.

3. Append to `~/.tomato/history.jsonl`:
   ```json
   {"event": "session_stop", "ts": <TIMESTAMP>, "reason": "user", "total_cycles": <current_cycle>}
   ```

4. Respond:
   ```
   Tomato session ended. {current_cycle} cycle(s) completed. Run /tomato stats to see your summary.
   ```

### /tomato pause

1. Read `~/.tomato/state.json`.
   - If not active: "No active Tomato session."
   - If already paused: "Session is already paused."

2. Calculate `elapsed_before_pause`:
   - `elapsed_before_pause = now - phase_started_at` (in seconds)

3. Update state.json:
   - `"paused": true`
   - `"paused_at": <NOW>`
   - `"elapsed_before_pause": <calculated>`

4. Append to history:
   ```json
   {"event": "pause", "ts": <TIMESTAMP>, "phase": "<current_phase>", "elapsed": <elapsed_seconds>}
   ```

5. Respond:
   ```
   Tomato paused. Your {phase} timer is frozen at {elapsed_minutes}m {elapsed_seconds}s. Run /tomato resume when ready.
   ```

### /tomato resume

1. Read `~/.tomato/state.json`.
   - If not active: "No active Tomato session."
   - If not paused: "Session is not paused."

2. Recalculate `phase_started_at` so the remaining time is preserved:
   - `phase_started_at = now - elapsed_before_pause`

3. Update state.json:
   - `"paused": false`
   - `"paused_at": null`
   - `"phase_started_at": <recalculated>`
   - `"elapsed_before_pause": 0`

4. Append to history:
   ```json
   {"event": "resume", "ts": <TIMESTAMP>, "phase": "<current_phase>"}
   ```

5. Calculate remaining time in the current phase:
   - If work phase: `remaining = work_minutes * 60 - elapsed_before_pause`
   - If rest phase: `remaining = active_rest_minutes * 60 - elapsed_before_pause`
     (where `active_rest_minutes` is `rest_minutes` or `long_rest_minutes`)

6. Respond:
   ```
   Tomato resumed! {remaining_minutes}m {remaining_seconds}s left in your {phase} phase.
   ```

### /tomato status

1. Read `~/.tomato/state.json`.
   - If file missing or `"active": false`: "No active Tomato session."

2. If paused:
   ```
   PAUSED -- {phase} phase, {elapsed_minutes}m {elapsed_seconds}s elapsed. Run /tomato resume to continue.
   ```

3. If work phase (not paused):
   - Calculate elapsed: `now - phase_started_at`
   - Calculate remaining: `work_minutes * 60 - elapsed`
   - If remaining <= 0, the hook will have transitioned the phase. Re-read
     state.json and recalculate.
   ```
   Working -- Cycle {current_cycle}{/max_cycles}. {remaining_min}m {remaining_sec}s remaining.
   ```

4. If rest phase (not paused):
   - Calculate remaining from `active_rest_minutes`
   - If remaining <= 0, rest is over. Transition to next work cycle (see
     Phase Transition below) and show work status.
   ```
   Resting -- {remaining_min}m {remaining_sec}s remaining. Cycle {next_cycle}{/max_cycles} next.
   ```

### /tomato stats [--week] [--export csv|json]

Run the CLI helper:
```bash
python3 ~/.claude/skills/tomato/bin/tomato-cli.py stats [--week] [--export csv|json]
```

Present the output to the user. The CLI reads `~/.tomato/history.jsonl` and
computes:
- Total sessions, total cycles, total work minutes, total rest minutes
- Average cycle length, longest streak
- Daily breakdown (last 7 days if --week)
- Export to file if --export is specified

If the command fails (python3 not found, file missing), fall back to reading
`~/.tomato/history.jsonl` directly and computing basic stats yourself:
- Count `work_start` events for total cycles
- Sum durations between `work_start` and next `rest_start` for total work time
- Count `session_stop` events for total sessions

### /tomato clear [--before YYYY-MM-DD]

1. If `--before` flag is provided, run:
   ```bash
   python3 ~/.claude/skills/tomato/bin/tomato-cli.py clear --before YYYY-MM-DD
   ```
   Present the output (number of events cleared).

2. If no flag, this clears ALL history. Ask the user for confirmation:
   ```
   This will delete all Tomato history. Are you sure? (yes/no)
   ```
   Only proceed if the user confirms. Then run:
   ```bash
   python3 ~/.claude/skills/tomato/bin/tomato-cli.py clear
   ```

3. If the CLI is unavailable, fall back to:
   - `--before`: read history.jsonl, filter out events before the date, rewrite
   - No flag: truncate history.jsonl to empty

---

## Phase Transitions

The hook script (`tomato-hook.sh`) handles automatic phase transitions by
checking timestamps on every tool invocation. However, if the user invokes
`/tomato status` and the phase should have transitioned, you must handle it:

### Work -> Rest transition

When `now - phase_started_at >= work_minutes * 60` during a work phase:

1. Determine rest type:
   - If `current_cycle % cycles_before_long_rest == 0`: use `long_rest_minutes`
   - Otherwise: use `rest_minutes`

2. Update state.json:
   - `"phase": "rest"`
   - `"phase_started_at": <transition_time>` (the moment work ended, i.e.
     `old_phase_started_at + work_minutes * 60`)
   - `"last_transition_at": <transition_time>`
   - `"active_rest_minutes": <chosen_rest_minutes>`

3. Append to history:
   ```json
   {"event": "rest_start", "ts": <TIMESTAMP>, "cycle": <current_cycle>, "rest_minutes": <chosen>}
   ```

### Rest -> Work transition

When `now - phase_started_at >= active_rest_minutes * 60` during a rest phase:

1. Increment `current_cycle`.

2. If `max_cycles` is set and `current_cycle > max_cycles`:
   - Set `"active": false`
   - Append `session_stop` event with `"reason": "completed"`
   - Tell user the session is complete.
   - Return.

3. Update state.json:
   - `"phase": "work"`
   - `"phase_started_at": <transition_time>`
   - `"last_transition_at": <transition_time>`
   - `"current_cycle": <incremented>`
   - `"active_rest_minutes": null`

4. Append to history:
   ```json
   {"event": "work_start", "ts": <TIMESTAMP>, "cycle": <new_cycle>}
   ```

---

## Checkpoint Resume Behavior

When a user starts a new conversation or returns after a rest period, check for
checkpoint files:

1. Look for files in `~/.tomato/checkpoints/` directory.

2. If checkpoint files exist, read the most recent one (sorted by filename,
   which encodes the timestamp).

3. Checkpoint files are JSON with this structure:
   ```json
   {
     "ts": <TIMESTAMP>,
     "branch": "feat/my-feature",
     "changed_files": ["src/foo.py", "src/bar.py"],
     "recent_commits": ["abc1234 fix: something", "def5678 feat: other"],
     "working_dir": "/path/to/project"
   }
   ```

4. Present to the user:
   ```
   Resuming from checkpoint -- you were working on branch {branch}.
   Changed files: {files}
   Recent commits: {commits}
   Continue or re-scope?
   ```

5. Wait for the user's response before proceeding.

6. If no checkpoints exist, skip this step silently.

---

## Break Suggestions

The hook script displays break suggestions via stderr during rest periods.
You do NOT need to handle break suggestions — the hook manages this entirely.
Do not duplicate break suggestion logic.

---

## Error Handling

- If `~/.tomato/state.json` is malformed, warn the user and offer to reset it
  with `/tomato start --force`.
- If `~/.tomato/history.jsonl` is malformed, skip bad lines when computing
  stats. Do not fail entirely.
- If jq is not available on the system, use python3 for JSON operations.
  If neither is available, tell the user to install jq.
- All timestamps are Unix epoch seconds (integer, not float).

---

## Important Notes

- Only ONE session can be active at a time. There is no multi-session support.
- The hook blocks ALL tool use during rest (except reading state.json itself).
  This is by design — the user should step away from the screen.
- Never silently skip writing to history.jsonl. Every state change gets logged.
- When doing time calculations, always use integer arithmetic for seconds.
  Display minutes and seconds to the user (e.g., "12m 30s remaining").
- The `active_dirs.txt` file is written by the hook to track which project
  directories the user worked in during a session. You do not need to write
  to it, but you can read it for context in `/tomato status`.
