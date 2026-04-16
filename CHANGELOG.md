# Changelog

All notable changes to Tomato will be documented in this file.

## [0.1.1] - 2026-04-17

### Fixed
- Grace-period arithmetic no longer bypasses rest indefinitely when the system clock jumps backward; backward skew now falls through to block (#11)
- `uninstall.sh` preserves `~/.tomato/` focus history by default; pass `--purge` to also delete it (#11)
- `load_config()` rejects invalid user values (negative ints, wrong types, bools) and falls back to per-key defaults with a warning (#11)
- Stats tests no longer flake around local midnight (#11)

### Changed
- Softened the README hook-latency claim; we'll add a CI benchmark before quoting specific numbers again (#11)

## [0.1.0] - 2026-04-17

Initial public release.

### Features
- AI-enforced Pomodoro timer for Claude Code — a PreToolUse hook blocks tool calls during rest periods so breaks are actually taken
- Work/rest cycles with configurable durations; auto long-rest every 4 cycles
- Session-aware grace period so mid-task transitions into rest can finish cleanly; different sessions are blocked immediately
- Pause/resume that freezes the timer without blocking tools
- Auto-bypass in CI and headless environments (`CI`, `GITHUB_ACTIONS`, `CLAUDE_CODE_HEADLESS`)
- Auto-registering PreToolUse hook on first `/tomato start`
- CLI: `start`, `stop`, `pause`, `resume`, `status`, `stats`, `clear`
- Local history and stats with daily focus time, cycle counts, and streaks
- Tests: 21 pytest cases for the CLI, 30 bats cases for the hook
- GitHub Actions CI on Ubuntu and macOS
