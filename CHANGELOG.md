# Changelog

All notable changes to Tomato will be documented in this file.

## [1.1.1] - 2026-03-29

### Fixed
- Cycle counting now correctly reports completed cycles when stopping mid-work (was overcounting by 1)
- Stopping after an expired work timer now credits the finished cycle instead of undercounting
- Stats, streaks, and `clear --before` use local time instead of UTC for date boundaries
- `pause` with no active session returns exit code 1 (was 0), consistent with `resume`
- Final rest of a finite session (`--cycles N`) shows "Session ends after this break" instead of impossible next-cycle text

### Changed
- All CLI messages use consistent formatting: `🍅 [State] — [details]`
- Smart pluralization everywhere: "1 cycle" / "3 cycles" instead of "cycle(s)"
- Error messages include action hints: "No active session. Start one with /tomato start."
- Hook block messages shortened for less noise on repeated tool calls
- Stats labels tightened: "Focus" instead of "Focused time:", "Streak" instead of "Current streak:"
- Stats date header uses short format: "Mar 29" instead of "2026-03-29"

## [1.1.0] - 2026-03-29

### Added
- Session-aware grace period for long-running tasks: when rest time starts, the session that was mid-task gets time to finish. Other sessions are blocked immediately. Grace expires after inactivity or a hard cap, whichever comes first.
- Configurable grace period: `grace_max_sec` (default 300s/5min) and `grace_timeout_sec` (default 10s) can be set in `~/.tomato/config.json`. Lower `grace_max_sec` for stricter enforcement.

### Changed
- Extracted `GRACE_CLEAR` constant to reduce repeated jq patterns in hook

## [1.0.0] - 2026-03-29

### Added
- AI-enforced Pomodoro timer for Claude Code with work/rest cycle enforcement
- PreToolUse hook blocks all tool calls during rest periods across all sessions
- CLI commands: start, stop, pause, resume, status, stats, clear, export
- Configurable durations (work, rest, long rest, cycles)
- Break suggestions shown during rest (wellness prompts)
- Checkpoint saving of git state across active projects at each break
- Checkpoint resume when starting a new conversation during an active session
- History tracking via append-only JSONL with stats and streaks
- Weekly stats with bar charts and CSV/JSON export
- Long rest every 4th cycle (configurable)
- Auto-hook-register: `/tomato start` detects missing hook and registers it in settings.json
- Input validation for work/rest/cycle durations (must be positive)
- 20 CLI pytest tests

### Security
- Hook whitelist tightened to exact `python3.*tomato-cli.py` pattern (prevents substring bypass)
- Atomic writes for state.json and settings.json (temp file + rename)
- Data directory created with chmod 700
- Stale session auto-detection and cleanup
- CI/automation environment bypass (no enforcement in headless/CI)
