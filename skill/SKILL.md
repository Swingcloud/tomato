---
name: tomato
description: >
  AI-enforced Pomodoro technique timer that blocks Claude Code during rest
  periods. Uses PreToolUse hooks to enforce work/rest cycles across all
  sessions. Commands: /tomato start, stop, pause, resume, status, stats, clear.
  Use when user asks to start a Pomodoro, focus session, work timer, take
  breaks, track productivity, or mentions "tomato" or "pomodoro".
---

# Tomato

For any /tomato command, run the CLI and present its output:

    python3 ~/.claude/skills/tomato/bin/tomato-cli.py {command} [flags]

If no subcommand is given (bare `/tomato`), run with no args to show the command menu:

    python3 ~/.claude/skills/tomato/bin/tomato-cli.py

## Commands

| Command | CLI call |
|---------|----------|
| /tomato start | `python3 ~/.claude/skills/tomato/bin/tomato-cli.py start [--work N] [--rest N] [--cycles N] [--force]` |
| /tomato stop | `python3 ~/.claude/skills/tomato/bin/tomato-cli.py stop` |
| /tomato pause | `python3 ~/.claude/skills/tomato/bin/tomato-cli.py pause` |
| /tomato resume | `python3 ~/.claude/skills/tomato/bin/tomato-cli.py resume` |
| /tomato status | `python3 ~/.claude/skills/tomato/bin/tomato-cli.py status` |
| /tomato stats [--week] | `python3 ~/.claude/skills/tomato/bin/tomato-cli.py stats [--week]` |
| /tomato stats --export csv\|json | `python3 ~/.claude/skills/tomato/bin/tomato-cli.py stats --export csv` |
| /tomato clear [--before DATE] | `python3 ~/.claude/skills/tomato/bin/tomato-cli.py clear [--before YYYY-MM-DD]` |

Run the command, then present the CLI output to the user as-is. The CLI handles all formatting.

## Checkpoint Resume

When starting a new conversation while a session is active, check for checkpoint files:

    ls -t ~/.tomato/checkpoints/*.json 2>/dev/null | head -1

If a checkpoint exists, read it and present: "Resuming from checkpoint -- you were working on {branch}. Changed files: {files}. Continue or re-scope?"

## Notes

- The PreToolUse hook (tomato-hook.sh) handles enforcement automatically. Do not call it.
- Break suggestions are shown by the hook during rest. Do not duplicate them.
- If the CLI fails (python3 missing), tell the user to install python3.
- For /tomato clear without --before, ask user for confirmation before running.
