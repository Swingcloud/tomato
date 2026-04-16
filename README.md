# Tomato

AI-enforced Pomodoro for Claude Code. Doesn't remind you to take breaks — makes you take them.

## What it does

Tomato uses Claude Code's hook system to **block all tool calls** during rest periods. When your work sprint ends, Claude Code literally refuses to work until you've taken a break. You can't dismiss it. That's the point.

## Install

The easiest way (via [skills.sh](https://skills.sh)):

```bash
npx skills add Swingcloud/tomato -g -a claude-code
```

Or manually, from source:

```bash
git clone https://github.com/Swingcloud/tomato.git
cd tomato
./install.sh
```

Requires: `jq` (JSON processor). Optional: `python3` (for stats and checkpoints).

The hook auto-registers in `settings.json` on your first `/tomato start`. Restart Claude Code after that for enforcement to take effect.

## Usage

```
/tomato start                     Start a session (25 min work / 5 min rest)
/tomato start --work 50 --rest 10 Custom durations
/tomato start --cycles 5          Auto-stop after 5 sprints
/tomato pause                     Freeze timer (lunch, meeting)
/tomato resume                    Pick up where you left off
/tomato stop                      End session
/tomato status                    Current phase and time remaining
/tomato stats                     Today's focus time, cycles, streaks
/tomato stats --week              Weekly summary
/tomato stats --export csv        Export history as CSV
/tomato clear --before 2026-01-01 Prune old history
```

## How it works

Tomato installs a global `PreToolUse` hook in `~/.claude/settings.json`. On every tool call, the hook checks a state file (`~/.tomato/state.json`) and either allows the call (exit 0) or blocks it (exit 2).

The hook is fast: ~8ms on the normal path (read JSON, check timer, allow). Phase transitions (work to rest, rest to work) take ~35ms and happen once every 25 minutes.

During rest, all tool calls are blocked — but `/tomato stop`, `/tomato resume`, and `/tomato status` still work. The hook whitelists its own CLI so you're never locked out.

When a work sprint ends, Tomato saves a checkpoint of your git state across all active projects. When the break ends, Claude presents what you were working on so you can resume without losing context.

## Configuration

Create `~/.tomato/config.json` to set persistent defaults:

```json
{
  "work_minutes": 25,
  "rest_minutes": 5,
  "long_rest_minutes": 15,
  "cycles_before_long_rest": 4,
  "grace_max_sec": 300,
  "grace_timeout_sec": 10
}
```

Every 4th cycle gets a long rest (15 min by default). When rest starts, the active session gets up to `grace_max_sec` (default 5 min) to finish its current task before being blocked. Lower it for stricter enforcement.

## Uninstall

```bash
cd tomato
./uninstall.sh
```

Removes the hook from `settings.json`, skill files, and `~/.tomato/` history.

## License

MIT
