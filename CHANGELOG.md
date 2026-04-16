# Changelog

All notable changes to Tomato will be documented in this file.

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
