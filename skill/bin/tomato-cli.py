#!/usr/bin/env python3
"""Tomato CLI — AI-enforced Pomodoro timer utilities.

Subcommands:
  start               Start a new Pomodoro session
  stop                Stop the active session
  pause               Pause the active session
  resume              Resume a paused session
  status              Show current session status
  checkpoint --save   Capture git state from all active working directories
  stats               Show focus stats (today or --week)
  clear               Delete history and checkpoints
  export              Export history as CSV or JSON (via stats --export)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

TOMATO_DIR = Path.home() / ".tomato"
STATE_FILE = TOMATO_DIR / "state.json"
ACTIVE_DIRS_FILE = TOMATO_DIR / "active_dirs.txt"
CHECKPOINTS_DIR = TOMATO_DIR / "checkpoints"
HISTORY_FILE = TOMATO_DIR / "history.jsonl"
CONFIG_FILE = TOMATO_DIR / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "work_minutes": 25,
    "rest_minutes": 5,
    "long_rest_minutes": 15,
    "cycles_before_long_rest": 4,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_config() -> Dict[str, Any]:
    """Read ~/.tomato/config.json, falling back to defaults."""
    config = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.is_file():
            with open(CONFIG_FILE, "r") as fh:
                user = json.load(fh)
            config.update(user)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Warning: could not read config — {exc}", file=sys.stderr)
    return config


def run_git(args: List[str], cwd: str) -> Optional[str]:
    """Run a git command, returning stdout or None on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def read_history() -> List[Dict[str, Any]]:
    """Read history.jsonl, skipping corrupted lines."""
    entries: List[Dict[str, Any]] = []
    if not HISTORY_FILE.is_file():
        return entries
    try:
        with open(HISTORY_FILE, "r") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    print(
                        f"Warning: skipping corrupted line {lineno} in history.jsonl",
                        file=sys.stderr,
                    )
    except OSError as exc:
        print(f"Error reading history: {exc}", file=sys.stderr)
    return entries


def fmt_duration(minutes: int) -> str:
    """Format minutes as 'Xh Ym'."""
    h, m = divmod(minutes, 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def date_from_ts(ts: float) -> datetime:
    """Convert a unix timestamp to a UTC datetime."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def load_state() -> Optional[Dict[str, Any]]:
    """Read state.json, return None if missing or corrupt."""
    try:
        if STATE_FILE.is_file():
            with open(STATE_FILE, "r") as fh:
                return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Warning: could not read state.json — {exc}", file=sys.stderr)
    return None


def save_state(state: Dict[str, Any]) -> None:
    """Write state.json atomically (temp file + rename)."""
    TOMATO_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=2)
    os.rename(str(tmp), str(STATE_FILE))


def append_history(event: Dict[str, Any]) -> None:
    """Append one JSON line to history.jsonl."""
    TOMATO_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "a") as fh:
        fh.write(json.dumps(event) + "\n")


def fmt_remaining(seconds: int) -> str:
    """Format seconds as 'Xm Ys'."""
    if seconds < 0:
        seconds = 0
    m, s = divmod(seconds, 60)
    return f"{m}m {s}s"


def _plural(n: int, word: str) -> str:
    """Smart pluralization: _plural(1, 'cycle') -> '1 cycle', _plural(3, 'cycle') -> '3 cycles'."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _completed_cycles(state: Dict[str, Any], now: Optional[int] = None) -> int:
    """Count completed work cycles based on current phase.

    During work phase, the current cycle is in-progress (not yet completed),
    unless the work timer has already expired (overdue transition).
    During rest phase, the current cycle's work is done.
    """
    cycle = state.get("current_cycle", 1)
    phase = state.get("phase", "work")
    if phase == "rest":
        return cycle
    # Work phase: check if the timer already expired (hook hasn't transitioned yet)
    if now is not None:
        work_min = state.get("work_minutes", 25)
        phase_started = state.get("phase_started_at", now)
        if now - phase_started >= work_min * 60:
            return cycle  # work finished, just not transitioned yet
    return max(0, cycle - 1)


# ---------------------------------------------------------------------------
# Hook auto-registration
# ---------------------------------------------------------------------------

SETTINGS_FILE = Path.home() / ".claude" / "settings.json"
HOOK_CMD = str(Path.home() / ".claude" / "skills" / "tomato" / "bin" / "tomato-hook.sh")


def _hook_is_registered() -> bool:
    """Check if the Tomato PreToolUse hook is in settings.json."""
    try:
        if not SETTINGS_FILE.is_file():
            return False
        with open(SETTINGS_FILE, "r") as fh:
            settings = json.load(fh)
        hooks = settings.get("hooks", {}).get("PreToolUse", [])
        for entry in hooks:
            for h in entry.get("hooks", []):
                if h.get("command", "") == HOOK_CMD:
                    return True
        return False
    except (json.JSONDecodeError, OSError):
        return False


def _register_hook() -> bool:
    """Register the Tomato PreToolUse hook in settings.json. Returns True on success."""
    try:
        settings_dir = SETTINGS_FILE.parent
        settings_dir.mkdir(parents=True, exist_ok=True)

        hook_entry = {
            "matcher": "*",
            "hooks": [{"type": "command", "command": HOOK_CMD}],
        }

        if SETTINGS_FILE.is_file():
            with open(SETTINGS_FILE, "r") as fh:
                settings = json.load(fh)
        else:
            settings = {}

        settings.setdefault("hooks", {})
        settings["hooks"].setdefault("PreToolUse", [])
        settings["hooks"]["PreToolUse"].append(hook_entry)

        tmp = SETTINGS_FILE.with_suffix(".tmp")
        with open(tmp, "w") as fh:
            json.dump(settings, fh, indent=2)
        os.rename(str(tmp), str(SETTINGS_FILE))
        return True
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: could not register hook — {exc}", file=sys.stderr)
        return False


def ensure_hook_registered() -> None:
    """Check for hook and auto-register if missing."""
    if _hook_is_registered():
        return

    # Check that the hook script actually exists
    if not Path(HOOK_CMD).is_file():
        print(
            "Warning: hook script not found at expected path. "
            "Run install.sh to set up Tomato properly.",
            file=sys.stderr,
        )
        return

    if _register_hook():
        print(
            "\U0001f345 Hook registered in ~/.claude/settings.json. "
            "Restart Claude Code for enforcement to take effect."
        )
    else:
        print(
            "Warning: could not auto-register hook. "
            "Run install.sh to set up enforcement.",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


def cmd_start(args: argparse.Namespace) -> int:
    """Start a new Pomodoro session."""
    ensure_hook_registered()
    config = load_config()

    work = args.work if args.work is not None else config.get("work_minutes", 25)
    rest = args.rest if args.rest is not None else config.get("rest_minutes", 5)
    long_rest = config.get("long_rest_minutes", 15)
    cycles_before_long = config.get("cycles_before_long_rest", 4)

    # Validate durations
    if work <= 0:
        print("\U0001f345 Error: work minutes must be positive.", file=sys.stderr)
        return 1
    if rest <= 0:
        print("\U0001f345 Error: rest minutes must be positive.", file=sys.stderr)
        return 1
    if args.cycles is not None and args.cycles <= 0:
        print("\U0001f345 Error: cycles must be positive.", file=sys.stderr)
        return 1

    state = load_state()
    if state is not None and state.get("active"):
        if not args.force:
            phase = state.get("phase", "work")
            cycle = state.get("current_cycle", 1)
            print(
                f"\U0001f345 Session already active \u2014 cycle {cycle}, {phase} phase. "
                "Stop first or use --force.",
                file=sys.stderr,
            )
            return 1
        # Force-stop the existing session
        now = int(time.time())
        append_history({
            "event": "session_stop",
            "ts": now,
            "reason": "force",
            "total_cycles": _completed_cycles(state, now=now),
        })

    now = int(time.time())
    TOMATO_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)

    new_state: Dict[str, Any] = {
        "active": True,
        "phase": "work",
        "paused": False,
        "paused_at": None,
        "elapsed_before_pause": 0,
        "started_at": now,
        "phase_started_at": now,
        "last_transition_at": now,
        "work_minutes": work,
        "rest_minutes": rest,
        "long_rest_minutes": long_rest,
        "cycles_before_long_rest": cycles_before_long,
        "current_cycle": 1,
        "max_cycles": args.cycles,
        "active_rest_minutes": None,
    }
    save_state(new_state)

    append_history({
        "event": "work_start",
        "ts": now,
        "cycle": 1,
    })

    # Clear active_dirs.txt
    with open(ACTIVE_DIRS_FILE, "w") as fh:
        pass

    max_str = f"/{args.cycles}" if args.cycles else ""
    print(
        f"\U0001f345 Started \u2014 {work}m work / {rest}m rest, cycle 1{max_str}. Go."
    )
    return 0


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def cmd_stop(_args: argparse.Namespace) -> int:
    """Stop the active Pomodoro session."""
    state = load_state()
    if state is None or not state.get("active"):
        print("\U0001f345 No active session.")
        return 0

    now = int(time.time())
    completed = _completed_cycles(state, now=now)

    state["active"] = False
    save_state(state)

    append_history({
        "event": "session_stop",
        "ts": now,
        "reason": "user",
        "total_cycles": completed,
    })

    print(f"\U0001f345 Stopped \u2014 {_plural(completed, 'cycle')} completed.")
    return 0


# ---------------------------------------------------------------------------
# pause
# ---------------------------------------------------------------------------


def cmd_pause(_args: argparse.Namespace) -> int:
    """Pause the active Pomodoro session."""
    state = load_state()
    if state is None or not state.get("active"):
        print("\U0001f345 No active session. Start one with /tomato start.", file=sys.stderr)
        return 1

    if state.get("paused"):
        print("\U0001f345 Already paused.")
        return 0

    now = int(time.time())
    phase = state.get("phase", "work")
    elapsed = now - state.get("phase_started_at", now)

    state["paused"] = True
    state["paused_at"] = now
    state["elapsed_before_pause"] = elapsed
    save_state(state)

    append_history({
        "event": "pause",
        "ts": now,
        "phase": phase,
        "elapsed": elapsed,
    })

    print(
        f"\U0001f345 Paused \u2014 {phase} phase, {fmt_remaining(elapsed)} elapsed. "
        "/tomato resume when ready."
    )
    return 0


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


def cmd_resume(_args: argparse.Namespace) -> int:
    """Resume a paused Pomodoro session."""
    state = load_state()
    if state is None or not state.get("active"):
        print("\U0001f345 No active session. Start one with /tomato start.", file=sys.stderr)
        return 1

    if not state.get("paused"):
        print("\U0001f345 Not paused \u2014 session is running.", file=sys.stderr)
        return 1

    now = int(time.time())
    phase = state.get("phase", "work")
    elapsed_before = state.get("elapsed_before_pause", 0)

    state["phase_started_at"] = now - elapsed_before
    state["paused"] = False
    state["paused_at"] = None
    state["elapsed_before_pause"] = 0
    save_state(state)

    append_history({
        "event": "resume",
        "ts": now,
        "phase": phase,
    })

    # Calculate remaining time
    if phase == "work":
        total_sec = state.get("work_minutes", 25) * 60
    else:
        active_rest = state.get("active_rest_minutes") or state.get("rest_minutes", 5)
        total_sec = active_rest * 60
    remaining = max(0, total_sec - elapsed_before)

    print(
        f"\U0001f345 Resumed \u2014 {fmt_remaining(remaining)} left in {phase} phase."
    )
    return 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def _transition_work_to_rest(state: Dict[str, Any]) -> Dict[str, Any]:
    """Handle work -> rest transition, update state and history."""
    cycle = state.get("current_cycle", 1)
    cycles_before_long = state.get("cycles_before_long_rest", 4)
    work_minutes = state.get("work_minutes", 25)

    # Transition time is when work actually ended
    transition_at = state.get("phase_started_at", 0) + work_minutes * 60

    if cycles_before_long > 0 and cycle % cycles_before_long == 0:
        rest_min = state.get("long_rest_minutes", 15)
    else:
        rest_min = state.get("rest_minutes", 5)

    state["phase"] = "rest"
    state["phase_started_at"] = transition_at
    state["last_transition_at"] = transition_at
    state["active_rest_minutes"] = rest_min

    append_history({
        "event": "work_end",
        "ts": transition_at,
        "cycle": cycle,
        "duration_sec": work_minutes * 60,
    })
    append_history({
        "event": "rest_start",
        "ts": transition_at,
        "cycle": cycle,
        "rest_minutes": rest_min,
    })

    return state


def _transition_rest_to_work(state: Dict[str, Any]) -> Dict[str, Any]:
    """Handle rest -> work transition, update state and history."""
    active_rest = state.get("active_rest_minutes") or state.get("rest_minutes", 5)
    transition_at = state.get("phase_started_at", 0) + active_rest * 60
    cycle = state.get("current_cycle", 1) + 1

    max_cycles = state.get("max_cycles")
    if max_cycles is not None and cycle > max_cycles:
        state["active"] = False
        save_state(state)
        append_history({
            "event": "rest_end",
            "ts": transition_at,
            "cycle": cycle - 1,
        })
        append_history({
            "event": "session_stop",
            "ts": transition_at,
            "reason": "completed",
            "total_cycles": max_cycles,
        })
        return state

    state["phase"] = "work"
    state["phase_started_at"] = transition_at
    state["last_transition_at"] = transition_at
    state["current_cycle"] = cycle
    state["active_rest_minutes"] = None

    append_history({
        "event": "rest_end",
        "ts": transition_at,
        "cycle": cycle - 1,
    })
    append_history({
        "event": "work_start",
        "ts": transition_at,
        "cycle": cycle,
    })

    return state


def cmd_status(_args: argparse.Namespace) -> int:
    """Show current Pomodoro session status."""
    state = load_state()
    if state is None or not state.get("active"):
        print("\U0001f345 No active session. Start one with /tomato start.")
        return 0

    now = int(time.time())
    phase = state.get("phase", "work")
    cycle = state.get("current_cycle", 1)
    max_cycles = state.get("max_cycles")
    max_str = f"/{max_cycles}" if max_cycles else ""

    # Handle paused state
    if state.get("paused"):
        elapsed = state.get("elapsed_before_pause", 0)
        print(
            f"\U0001f345 Paused \u2014 {phase} phase, {fmt_remaining(elapsed)} elapsed."
        )
        return 0

    phase_started = state.get("phase_started_at", now)

    if phase == "work":
        work_min = state.get("work_minutes", 25)
        elapsed = now - phase_started
        remaining = work_min * 60 - elapsed

        if remaining <= 0:
            # Work phase expired — transition to rest
            state = _transition_work_to_rest(state)
            save_state(state)
            # Now show rest status
            return _show_rest_status(state, now, cycle, max_str)

        print(
            f"\U0001f345 Working \u2014 cycle {cycle}{max_str}, "
            f"{fmt_remaining(remaining)} left."
        )
        return 0
    else:
        return _show_rest_status(state, now, cycle, max_str)


def _show_rest_status(
    state: Dict[str, Any], now: int, cycle: int, max_str: str
) -> int:
    """Show rest phase status, handling expired rest transitions."""
    active_rest = state.get("active_rest_minutes") or state.get("rest_minutes", 5)
    phase_started = state.get("phase_started_at", now)
    elapsed = now - phase_started
    remaining = active_rest * 60 - elapsed

    if remaining <= 0:
        # Rest expired — transition to next work cycle
        state = _transition_rest_to_work(state)

        if not state.get("active"):
            completed = state.get("max_cycles", cycle)
            print(
                f"\U0001f345 Done \u2014 {_plural(completed, 'cycle')} finished."
            )
            return 0

        # Show new work status
        new_cycle = state.get("current_cycle", cycle + 1)
        max_cycles = state.get("max_cycles")
        new_max_str = f"/{max_cycles}" if max_cycles else ""
        work_min = state.get("work_minutes", 25)
        new_elapsed = now - state.get("phase_started_at", now)
        new_remaining = work_min * 60 - new_elapsed
        save_state(state)
        print(
            f"\U0001f345 Working \u2014 cycle {new_cycle}{new_max_str}, "
            f"{fmt_remaining(new_remaining)} left."
        )
        return 0

    # Check if this is the last rest before session ends
    max_cycles = state.get("max_cycles")
    if max_cycles is not None and cycle >= max_cycles:
        print(
            f"\U0001f345 Resting \u2014 {fmt_remaining(remaining)} left. "
            f"Session ends after this break."
        )
    else:
        next_cycle = cycle + 1
        next_str = f"/{max_cycles}" if max_cycles else ""
        print(
            f"\U0001f345 Resting \u2014 {fmt_remaining(remaining)} left, "
            f"cycle {next_cycle}{next_str} next."
        )
    return 0


# ---------------------------------------------------------------------------
# checkpoint --save
# ---------------------------------------------------------------------------


def cmd_checkpoint(args: argparse.Namespace) -> int:
    """Capture git state from all active working directories."""
    if not args.save:
        print("Usage: tomato-cli.py checkpoint --save", file=sys.stderr)
        return 1

    # Read active dirs
    dirs: List[str] = []
    try:
        if ACTIVE_DIRS_FILE.is_file():
            with open(ACTIVE_DIRS_FILE, "r") as fh:
                dirs = list(dict.fromkeys(
                    line.strip() for line in fh if line.strip()
                ))
    except OSError as exc:
        print(f"Warning: could not read active_dirs.txt — {exc}", file=sys.stderr)

    projects: List[Dict[str, Any]] = []
    for d in dirs:
        if not os.path.isdir(d):
            continue

        repo_root = run_git(["-C", d, "rev-parse", "--show-toplevel"], cwd=d)
        if repo_root is None:
            continue  # not a git repo

        branch = run_git(["-C", d, "branch", "--show-current"], cwd=d) or ""
        status = run_git(["-C", d, "status", "--porcelain"], cwd=d) or ""
        diff_stat = run_git(["-C", d, "diff", "--stat"], cwd=d) or ""
        log_out = run_git(["-C", d, "log", "--oneline", "-3"], cwd=d) or ""

        recent_commits = [line for line in log_out.splitlines() if line.strip()]

        projects.append(
            {
                "path": repo_root,
                "branch": branch,
                "status": status,
                "diff_stat": diff_stat,
                "recent_commits": recent_commits,
            }
        )

    ts = int(time.time())
    checkpoint = {
        "timestamp": ts,
        "projects": projects,
    }

    try:
        CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
        outfile = CHECKPOINTS_DIR / f"{ts}.json"
        with open(outfile, "w") as fh:
            json.dump(checkpoint, fh, indent=2)
        print(f"Checkpoint saved: {outfile}")
    except OSError as exc:
        print(f"Error saving checkpoint: {exc}", file=sys.stderr)
        return 1

    return 0


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def _now_local() -> datetime:
    """Current local time for date-based comparisons (not UTC)."""
    return datetime.now().astimezone()


def _entries_for_date(entries: List[Dict[str, Any]], target: datetime) -> List[Dict[str, Any]]:
    """Filter entries whose timestamp falls on *target* date (local time)."""
    target_date = target.date()
    # Support both "ts" (v2+) and "timestamp" (v1) for backwards compat
    # Use fromtimestamp() without tz arg to get local time
    return [
        e
        for e in entries
        if datetime.fromtimestamp(e.get("ts", e.get("timestamp", 0))).date()
        == target_date
    ]


def _compute_stats(entries: List[Dict[str, Any]], config: Dict[str, Any]) -> Dict[str, Any]:
    """Compute focused minutes, completed cycles, and breaks honored."""
    work_minutes = config.get("work_minutes", 25)

    # Count completed work cycles (work_end events)
    completed_cycles = sum(1 for e in entries if e.get("event") == "work_end")

    # Focused time: sum of duration_sec from work_end events, or fall back to
    # cycles * work_minutes
    total_sec = 0
    has_duration = False
    for e in entries:
        if e.get("event") == "work_end":
            dur = e.get("duration_sec")
            if dur is not None:
                total_sec += dur
                has_duration = True
    if has_duration:
        focused_minutes = total_sec // 60
    else:
        focused_minutes = completed_cycles * work_minutes

    # Breaks: rest_end means break was honored (completed naturally)
    breaks_honored = sum(1 for e in entries if e.get("event") == "rest_end")
    # Total breaks offered = completed cycles (each cycle ends with a rest)
    total_breaks = max(breaks_honored, completed_cycles)

    return {
        "focused_minutes": focused_minutes,
        "completed_cycles": completed_cycles,
        "breaks_honored": breaks_honored,
        "total_breaks": total_breaks,
    }


def _compute_streak(all_entries: List[Dict[str, Any]]) -> int:
    """Consecutive days (ending today or yesterday) with >=1 completed cycle."""
    cycle_dates = set()
    for e in all_entries:
        if e.get("event") == "work_end":
            # Support both "ts" (v2+) and "timestamp" (v1) for backwards compat
            # Use local time for streak calculation
            d = datetime.fromtimestamp(
                e.get("ts", e.get("timestamp", 0))
            ).date()
            cycle_dates.add(d)

    if not cycle_dates:
        return 0

    today = _now_local().date()
    streak = 0
    check = today
    while check in cycle_dates:
        streak += 1
        check -= timedelta(days=1)

    # If today has no cycles yet, start checking from yesterday
    if streak == 0:
        check = today - timedelta(days=1)
        while check in cycle_dates:
            streak += 1
            check -= timedelta(days=1)

    return streak


def _bar(cycles: int, max_cycles: int) -> str:
    """Render a simple bar chart with filled/empty blocks."""
    bar_width = 10
    if max_cycles == 0:
        filled = 0
    else:
        filled = round(cycles / max_cycles * bar_width)
    filled = min(filled, bar_width)
    return "\u2588" * filled + "\u2591" * (bar_width - filled)


def cmd_stats(args: argparse.Namespace) -> int:
    """Show focus statistics."""
    entries = read_history()
    config = load_config()

    # Handle --export first (always operates on full history)
    if args.export:
        return _export(entries, args.export)

    if not entries:
        print("\U0001f345 No sessions yet. Start one with /tomato start.")
        return 0

    now = _now_local()

    if args.week:
        return _stats_week(entries, config, now)
    else:
        return _stats_today(entries, config, now)


def _stats_today(
    entries: List[Dict[str, Any]], config: Dict[str, Any], now: datetime
) -> int:
    today_entries = _entries_for_date(entries, now)
    stats = _compute_stats(today_entries, config)
    streak = _compute_streak(entries)

    date_str = now.strftime("%b %d")
    focused = fmt_duration(stats["focused_minutes"])
    cycles = stats["completed_cycles"]
    breaks_honored = stats["breaks_honored"]
    total_breaks = stats["total_breaks"]

    print(f"\U0001f345 Today \u2014 {date_str}")
    print("\u2501" * 30)
    print(f"  Focus      {focused}  ({_plural(cycles, 'cycle')})")
    print(f"  Breaks     {breaks_honored} of {total_breaks}")
    print(f"  Streak     {_plural(streak, 'day')}")
    return 0


def _stats_week(
    entries: List[Dict[str, Any]], config: Dict[str, Any], now: datetime
) -> int:
    week_start = now - timedelta(days=6)
    start_str = week_start.strftime("%b %d")
    end_str = now.strftime("%d")

    # Collect per-day stats
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    daily: List[Dict[str, Any]] = []
    total_focused = 0
    total_cycles = 0
    total_breaks_honored = 0
    total_breaks = 0

    for offset in range(7):
        day = week_start + timedelta(days=offset)
        day_entries = _entries_for_date(entries, day)
        day_stats = _compute_stats(day_entries, config)
        wday = day.weekday()  # 0=Mon
        daily.append(
            {
                "name": day_names[wday],
                "focused_minutes": day_stats["focused_minutes"],
                "cycles": day_stats["completed_cycles"],
            }
        )
        total_focused += day_stats["focused_minutes"]
        total_cycles += day_stats["completed_cycles"]
        total_breaks_honored += day_stats["breaks_honored"]
        total_breaks += day_stats["total_breaks"]

    max_cycles = max((d["cycles"] for d in daily), default=1) or 1

    print(
        f"\U0001f345 Week \u2014 {start_str}\u2013{end_str}"
    )
    print("\u2501" * 38)
    print(
        f"  Focus      {fmt_duration(total_focused)}  ({_plural(total_cycles, 'cycle')})"
    )
    print(f"  Breaks     {total_breaks_honored} of {total_breaks}")
    print()
    for d in daily:
        bar = _bar(d["cycles"], max_cycles)
        print(
            f"  {d['name']}  {fmt_duration(d['focused_minutes']):>7s}  {bar}  {d['cycles']}"
        )
    return 0


def _export(entries: List[Dict[str, Any]], fmt: str) -> int:
    if not entries:
        print("\U0001f345 No sessions yet. Start one with /tomato start.")
        return 0

    if fmt == "json":
        json.dump(entries, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if fmt == "csv":
        # Collect all keys across entries for CSV header
        all_keys: List[str] = []
        seen_keys: set[str] = set()
        for e in entries:
            for k in e:
                if k not in seen_keys:
                    all_keys.append(k)
                    seen_keys.add(k)

        writer = csv.DictWriter(sys.stdout, fieldnames=all_keys, extrasaction="ignore")
        writer.writeheader()
        for e in entries:
            writer.writerow(e)
        return 0

    print(f"Unknown export format: {fmt}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


def cmd_clear(args: argparse.Namespace) -> int:
    """Delete history and/or checkpoints."""
    before_date: Optional[datetime] = None
    if args.before:
        try:
            before_date = datetime.strptime(args.before, "%Y-%m-%d").astimezone()
        except ValueError:
            print(
                f"Invalid date format: {args.before} (expected YYYY-MM-DD)",
                file=sys.stderr,
            )
            return 1

    events_cleared = 0
    checkpoints_cleared = 0

    # --- History ---
    if before_date is None:
        # Delete all history
        try:
            if HISTORY_FILE.is_file():
                with open(HISTORY_FILE) as fh:
                    events_cleared = sum(
                        1 for line in fh if line.strip()
                    )
                HISTORY_FILE.unlink()
        except OSError as exc:
            print(f"Error clearing history: {exc}", file=sys.stderr)
            return 1
    else:
        # Keep only entries on or after the cutoff date
        entries = read_history()
        events_cleared = 0
        kept: List[str] = []
        for e in entries:
            # Support both "ts" (v2+) and "timestamp" (v1) for backwards compat
            ts = e.get("ts", e.get("timestamp", 0))
            entry_date = datetime.fromtimestamp(ts).astimezone()
            if entry_date < before_date:
                events_cleared += 1
            else:
                kept.append(json.dumps(e))
        try:
            with open(HISTORY_FILE, "w") as fh:
                for line in kept:
                    fh.write(line + "\n")
        except OSError as exc:
            print(f"Error rewriting history: {exc}", file=sys.stderr)
            return 1

    # --- Checkpoints ---
    try:
        if CHECKPOINTS_DIR.is_dir():
            for cp in sorted(CHECKPOINTS_DIR.iterdir()):
                if not cp.name.endswith(".json"):
                    continue
                if before_date is None:
                    cp.unlink()
                    checkpoints_cleared += 1
                else:
                    # Parse timestamp from filename
                    try:
                        cp_ts = int(cp.stem)
                        cp_dt = datetime.fromtimestamp(cp_ts).astimezone()
                        if cp_dt < before_date:
                            cp.unlink()
                            checkpoints_cleared += 1
                    except (ValueError, OSError):
                        pass
    except OSError as exc:
        print(f"Error clearing checkpoints: {exc}", file=sys.stderr)
        return 1

    print(
        f"\U0001f345 Cleared {events_cleared:,} events and {checkpoints_cleared:,} checkpoints."
    )
    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tomato-cli",
        description="Tomato — AI-enforced Pomodoro timer utilities",
    )
    sub = parser.add_subparsers(dest="command")

    # start
    sp = sub.add_parser("start", help="Start a new Pomodoro session")
    sp.add_argument("--work", type=int, default=None, help="Work minutes")
    sp.add_argument("--rest", type=int, default=None, help="Rest minutes")
    sp.add_argument("--cycles", type=int, default=None, help="Max cycles")
    sp.add_argument(
        "--force", action="store_true", help="Force-stop existing session"
    )

    # stop
    sub.add_parser("stop", help="Stop the active session")

    # pause
    sub.add_parser("pause", help="Pause the active session")

    # resume
    sub.add_parser("resume", help="Resume a paused session")

    # status
    sub.add_parser("status", help="Show current session status")

    # checkpoint
    cp = sub.add_parser("checkpoint", help="Capture git state snapshot")
    cp.add_argument(
        "--save", action="store_true", help="Save checkpoint now"
    )

    # stats
    st = sub.add_parser("stats", help="Show focus statistics")
    st.add_argument(
        "--week", action="store_true", help="Show last 7 days instead of today"
    )
    st.add_argument(
        "--export",
        choices=["csv", "json"],
        default=None,
        help="Export raw history to stdout",
    )

    # clear
    cl = sub.add_parser("clear", help="Delete history and checkpoints")
    cl.add_argument(
        "--before",
        metavar="YYYY-MM-DD",
        default=None,
        help="Only delete entries before this date",
    )

    # export (convenience alias for stats --export)
    ex = sub.add_parser("export", help="Export raw history (csv or json)")
    ex.add_argument(
        "format",
        choices=["csv", "json"],
        help="Output format",
    )

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        print("🍅 Tomato — AI-enforced Pomodoro for Claude Code")
        print()
        print("Commands:")
        print("  /tomato start                  Start a session (25m work / 5m rest)")
        print("  /tomato start --work 50        Custom work duration")
        print("  /tomato start --cycles 5       Auto-stop after 5 sprints")
        print("  /tomato stop                   End session")
        print("  /tomato pause                  Freeze timer (lunch, meeting)")
        print("  /tomato resume                 Pick up where you left off")
        print("  /tomato status                 Current phase and time remaining")
        print("  /tomato stats                  Today's focus time and streaks")
        print("  /tomato stats --week           Weekly summary")
        print("  /tomato stats --export csv     Export history")
        print("  /tomato clear                  Delete all history")
        print("  /tomato clear --before DATE    Prune old history")
        return 0

    try:
        if args.command == "start":
            return cmd_start(args)
        elif args.command == "stop":
            return cmd_stop(args)
        elif args.command == "pause":
            return cmd_pause(args)
        elif args.command == "resume":
            return cmd_resume(args)
        elif args.command == "status":
            return cmd_status(args)
        elif args.command == "checkpoint":
            return cmd_checkpoint(args)
        elif args.command == "stats":
            return cmd_stats(args)
        elif args.command == "clear":
            return cmd_clear(args)
        elif args.command == "export":
            # Translate to stats --export path
            entries = read_history()
            return _export(entries, args.format)
        else:
            parser.print_help()
            return 1
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
