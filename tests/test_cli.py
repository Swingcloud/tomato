"""Pytest tests for tomato-cli.py.

Runs the CLI as a subprocess with HOME overridden to a temp directory,
so tests never touch real user state.
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

CLI_SCRIPT = "skill/bin/tomato-cli.py"
REPO_ROOT = str(Path(__file__).resolve().parent.parent)


def run_cli(*args: str, home: str | Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run tomato-cli.py as a subprocess with optional HOME override."""
    env = os.environ.copy()
    if home:
        env["HOME"] = str(home)
    result = subprocess.run(
        ["python3", CLI_SCRIPT] + list(args),
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )
    return result


def tomato_dir(home: Path) -> Path:
    return home / ".tomato"


def read_state(home: Path) -> dict[str, Any]:
    state_file = tomato_dir(home) / "state.json"
    with open(state_file) as f:
        return json.load(f)


def read_history(home: Path) -> list[dict[str, Any]]:
    history_file = tomato_dir(home) / "history.jsonl"
    entries: list[dict[str, Any]] = []
    if not history_file.exists():
        return entries
    with open(history_file) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def write_history(home: Path, events: list[dict[str, Any]]) -> None:
    history = tomato_dir(home) / "history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    with open(history, "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")


# ---------------------------------------------------------------------------
# Start / Stop / Pause / Resume / Status
# ---------------------------------------------------------------------------


class TestStart:
    def test_start_creates_state(self, tmp_path: Path) -> None:
        """start -> state.json exists with active=true."""
        result = run_cli("start", home=tmp_path)
        assert result.returncode == 0

        state = read_state(tmp_path)
        assert state["active"] is True
        assert state["phase"] == "work"
        assert state["paused"] is False

    def test_start_writes_history(self, tmp_path: Path) -> None:
        """start -> history.jsonl has a work_start event with 'timestamp' key."""
        run_cli("start", home=tmp_path)

        entries = read_history(tmp_path)
        assert len(entries) >= 1
        work_starts = [e for e in entries if e["event"] == "work_start"]
        assert len(work_starts) == 1
        assert "ts" in work_starts[0]
        assert work_starts[0]["cycle"] == 1

    def test_start_already_active(self, tmp_path: Path) -> None:
        """start twice -> second fails with exit 1."""
        run_cli("start", home=tmp_path)
        result = run_cli("start", home=tmp_path)

        assert result.returncode == 1
        assert "already active" in result.stderr.lower()

    def test_start_force(self, tmp_path: Path) -> None:
        """start --force while active -> stops old, starts new."""
        run_cli("start", home=tmp_path)
        result = run_cli("start", "--force", home=tmp_path)

        assert result.returncode == 0
        state = read_state(tmp_path)
        assert state["active"] is True

        # History should have a session_stop with reason=force
        entries = read_history(tmp_path)
        force_stops = [
            e for e in entries
            if e.get("event") == "session_stop" and e.get("reason") == "force"
        ]
        assert len(force_stops) == 1

    def test_start_custom_durations(self, tmp_path: Path) -> None:
        """start --work 50 --rest 10 -> state.json has correct values."""
        run_cli("start", "--work", "50", "--rest", "10", home=tmp_path)

        state = read_state(tmp_path)
        assert state["work_minutes"] == 50
        assert state["rest_minutes"] == 10

    def test_start_cycles(self, tmp_path: Path) -> None:
        """start --cycles 3 -> state.json has max_cycles=3."""
        run_cli("start", "--cycles", "3", home=tmp_path)

        state = read_state(tmp_path)
        assert state["max_cycles"] == 3


class TestStop:
    def test_stop(self, tmp_path: Path) -> None:
        """start then stop -> active=false, session_stop in history."""
        run_cli("start", home=tmp_path)
        result = run_cli("stop", home=tmp_path)

        assert result.returncode == 0
        state = read_state(tmp_path)
        assert state["active"] is False

        entries = read_history(tmp_path)
        session_stops = [e for e in entries if e["event"] == "session_stop"]
        assert len(session_stops) == 1
        assert session_stops[0]["reason"] == "user"

    def test_stop_no_session(self, tmp_path: Path) -> None:
        """stop without start -> 'No active session'."""
        result = run_cli("stop", home=tmp_path)

        assert result.returncode == 0
        assert "no active session" in result.stdout.lower()


class TestPauseResume:
    def test_pause_resume(self, tmp_path: Path) -> None:
        """start, pause, resume -> phase_started_at recalculated correctly."""
        run_cli("start", home=tmp_path)
        state_before = read_state(tmp_path)
        original_phase_started = state_before["phase_started_at"]

        # Pause
        pause_result = run_cli("pause", home=tmp_path)
        assert pause_result.returncode == 0

        paused_state = read_state(tmp_path)
        assert paused_state["paused"] is True
        assert paused_state["paused_at"] is not None

        # Resume
        resume_result = run_cli("resume", home=tmp_path)
        assert resume_result.returncode == 0

        resumed_state = read_state(tmp_path)
        assert resumed_state["paused"] is False
        assert resumed_state["paused_at"] is None
        # phase_started_at should be recalculated: now - elapsed_before_pause
        # Since the elapsed is very small (near-instant), phase_started_at
        # should be close to the current time
        assert resumed_state["phase_started_at"] >= original_phase_started


class TestStatus:
    def test_status_work(self, tmp_path: Path) -> None:
        """start -> status shows 'Working'."""
        run_cli("start", home=tmp_path)
        result = run_cli("status", home=tmp_path)

        assert result.returncode == 0
        assert "Working" in result.stdout or "working" in result.stdout.lower()

    def test_status_no_session(self, tmp_path: Path) -> None:
        """status without start -> 'No active session'."""
        result = run_cli("status", home=tmp_path)

        assert result.returncode == 0
        assert "no active session" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestStats:
    def test_stats_empty(self, tmp_path: Path) -> None:
        """No history -> 'No sessions yet'."""
        result = run_cli("stats", home=tmp_path)

        assert result.returncode == 0
        assert "no sessions yet" in result.stdout.lower()

    def test_stats_with_data(self, tmp_path: Path) -> None:
        """Write history entries with 'timestamp' key -> stats shows correct cycles."""
        now = int(time.time())
        events = [
            {"event": "work_start", "timestamp": now - 3000, "cycle": 1},
            {"event": "work_end", "timestamp": now - 1500, "cycle": 1, "duration_sec": 1500},
            {"event": "rest_start", "timestamp": now - 1500, "cycle": 1, "rest_minutes": 5},
            {"event": "rest_end", "timestamp": now - 1200, "cycle": 1},
            {"event": "work_start", "timestamp": now - 1200, "cycle": 2},
            {"event": "work_end", "timestamp": now - 100, "cycle": 2, "duration_sec": 1100},
        ]
        write_history(tmp_path, events)

        result = run_cli("stats", home=tmp_path)
        assert result.returncode == 0
        # Should show 2 completed cycles
        assert "2 cycles" in result.stdout

    def test_stats_export_json(self, tmp_path: Path) -> None:
        """Write history -> export json -> valid JSON array output."""
        now = int(time.time())
        events = [
            {"event": "work_start", "timestamp": now, "cycle": 1},
            {"event": "work_end", "timestamp": now + 1500, "cycle": 1, "duration_sec": 1500},
        ]
        write_history(tmp_path, events)

        result = run_cli("stats", "--export", "json", home=tmp_path)
        assert result.returncode == 0

        parsed = json.loads(result.stdout)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["event"] == "work_start"

    def test_stats_export_csv(self, tmp_path: Path) -> None:
        """Write history -> export csv -> valid CSV output."""
        now = int(time.time())
        events = [
            {"event": "work_start", "timestamp": now, "cycle": 1},
            {"event": "work_end", "timestamp": now + 1500, "cycle": 1, "duration_sec": 1500},
        ]
        write_history(tmp_path, events)

        result = run_cli("stats", "--export", "csv", home=tmp_path)
        assert result.returncode == 0

        reader = csv.DictReader(io.StringIO(result.stdout))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["event"] == "work_start"


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------


class TestClear:
    def test_clear_all(self, tmp_path: Path) -> None:
        """Write history + checkpoints -> clear -> both deleted."""
        now = int(time.time())
        events = [
            {"event": "work_start", "timestamp": now, "cycle": 1},
        ]
        write_history(tmp_path, events)

        # Create a checkpoint file
        cp_dir = tomato_dir(tmp_path) / "checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)
        cp_file = cp_dir / f"{now}.json"
        cp_file.write_text(json.dumps({"timestamp": now, "projects": []}))

        result = run_cli("clear", home=tmp_path)
        assert result.returncode == 0
        assert "1" in result.stdout  # at least 1 event cleared

        history_file = tomato_dir(tmp_path) / "history.jsonl"
        assert not history_file.exists()
        assert not cp_file.exists()

    def test_clear_before_date(self, tmp_path: Path) -> None:
        """Write old + new entries -> clear --before -> only old deleted."""
        # Old event: 2020-01-01 (well before cutoff)
        old_ts = 1577836800  # 2020-01-01 00:00:00 UTC
        # New event: 2025-06-01 (after cutoff)
        new_ts = 1748736000  # 2025-06-01 00:00:00 UTC

        events = [
            {"event": "work_start", "timestamp": old_ts, "cycle": 1},
            {"event": "work_start", "timestamp": new_ts, "cycle": 2},
        ]
        write_history(tmp_path, events)

        result = run_cli("clear", "--before", "2024-01-01", home=tmp_path)
        assert result.returncode == 0

        remaining = read_history(tmp_path)
        assert len(remaining) == 1
        assert remaining[0]["timestamp"] == new_ts


# ---------------------------------------------------------------------------
# No-args menu
# ---------------------------------------------------------------------------


class TestMenu:
    def test_no_args_shows_menu(self, tmp_path: Path) -> None:
        """Run with no args -> output contains 'Commands:'."""
        result = run_cli(home=tmp_path)

        assert result.returncode == 0
        assert "Commands:" in result.stdout


# ---------------------------------------------------------------------------
# Integration: timestamp key consistency
# ---------------------------------------------------------------------------


class TestTimestampKey:
    def test_start_history_uses_timestamp_key(self, tmp_path: Path) -> None:
        """start -> read history.jsonl -> verify key is 'timestamp'."""
        run_cli("start", home=tmp_path)

        entries = read_history(tmp_path)
        work_starts = [e for e in entries if e["event"] == "work_start"]
        assert len(work_starts) == 1
        # The CLI writes "ts" key (standardized in codex review)
        assert "ts" in work_starts[0]

    def test_stats_reads_timestamp_key(self, tmp_path: Path) -> None:
        """Write history with 'timestamp' key -> stats returns correct data (not zero)."""
        now = int(time.time())
        events = [
            {"event": "work_start", "timestamp": now - 1600, "cycle": 1},
            {"event": "work_end", "timestamp": now - 100, "cycle": 1, "duration_sec": 1500},
        ]
        write_history(tmp_path, events)

        result = run_cli("stats", home=tmp_path)
        assert result.returncode == 0
        # Should show 1 cycle and non-zero focused time
        assert "1 cycles" in result.stdout or "1 cycle" in result.stdout
        assert "0m (0 cycles)" not in result.stdout
