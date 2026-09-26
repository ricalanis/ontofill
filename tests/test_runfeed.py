"""The live lake feed exposes execution without promoting recorded runs."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ontofill.lake import FileLake
from ontofill.runfeed import RunFeed

RECORDED = {"backend": "recorded", "model": "synthetic-replay", "at": "2026-01-01T00:00:00Z"}
VULTR = {"backend": "vultr", "model": "synthetic-vultr", "at": "2026-01-01T00:00:00Z"}
JEV = {"backend": "jev", "model": "synthetic-jev", "at": "2026-01-01T00:00:00Z"}


class CountingLake(FileLake):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.written: list[str] = []

    def write_key(self, key: str, data: bytes) -> None:
        super().write_key(key, data)
        self.written.append(key)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def trace(run_id: str, generated_by: dict) -> dict:
    return {
        "step_id": "step-synthetic",
        "run_id": run_id,
        "phase": 5,
        "source_id": "synthetic-source",
        "objective_id": "synthetic-objective",
        "tdd_path": "04-local/synthetic-source__synthetic-objective/tdd.json",
        "mode": "S1",
        "observed": {"url": "https://example.invalid/synthetic"},
        "requested": {"tool": "page.snapshot"},
        "executed": {"tool": "page.snapshot", "status": "ok"},
        "evaluated": {"status": "ok"},
        "parent_step_id": None,
        "value_ids": [],
        "ts": "2026-01-01T00:00:00Z",
        "generated_by": generated_by,
    }


def test_recorded_feed_appends_steps_and_refreshes_status_without_latest(tmp_path: Path) -> None:
    lake = CountingLake(tmp_path / "lake")
    clock = Clock()
    screenshot_key = lake.put_bytes(b"synthetic screenshot")
    feed = RunFeed(
        lake,
        "synthetic-case",
        "mock-synthetic-run",
        RECORDED,
        clock=clock,
        start_heartbeat=False,
    )
    source = {
        "source_id": "synthetic-source",
        "source_type": "synthetic_registry",
        "health": {"ok": 1, "failed": 0, "yield": 1},
    }
    feed.update_status(
        state="running", phase=1, sources=[source], metrics={"entities_total": {"record": 0}}
    )
    status_key = "runs/synthetic-case/mock-synthetic-run/status.json"
    assert lake.written.count(status_key) == 1
    assert not lake.exists("runs/synthetic-case/latest.json")
    feed.append_step(trace("mock-synthetic-run", RECORDED), screenshot_key=screenshot_key)
    lines = lake.read_key("runs/synthetic-case/mock-synthetic-run/trace.live.jsonl").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["screenshot_key"] == screenshot_key
    clock.advance(9)
    feed.heartbeat()
    assert lake.written.count(status_key) == 1
    clock.advance(1)
    feed.heartbeat()
    assert lake.written.count(status_key) == 2
    feed.update_status(state="paused", phase=2, checkpoint_pending="factors")
    assert lake.written.count(status_key) == 3
    status = json.loads(lake.read_key(status_key))
    assert status["state"] == "paused"
    assert status["checkpoint_pending"] == "factors"
    assert status["generated_by"]["backend"] == "recorded"
    assert feed.current_status["sources"] == [source]
    feed.close()
    assert not lake.exists("runs/synthetic-case/latest.json")


def test_live_feed_latest_survives_later_recorded_run(tmp_path: Path) -> None:
    lake = CountingLake(tmp_path / "lake")
    clock = Clock()
    live = RunFeed(
        lake,
        "synthetic-case",
        "synthetic-live",
        VULTR,
        clock=clock,
        start_heartbeat=False,
    )
    live.update_status(state="running", phase=1)
    live.update_status(state="running", phase=2)
    live.append_trace(trace("synthetic-live", VULTR))
    pointer = "runs/synthetic-case/latest.json"
    assert json.loads(lake.read_key(pointer))["run_id"] == "synthetic-live"
    second_live = RunFeed(
        lake,
        "synthetic-case",
        "synthetic-live-2",
        VULTR,
        clock=clock,
        start_heartbeat=False,
    )
    second_live.update_status(state="running", phase=1)
    clock.advance(10)
    live.heartbeat()
    assert json.loads(lake.read_key(pointer))["run_id"] == "synthetic-live-2"
    live.close()
    second_live.close()
    mock = RunFeed(
        lake,
        "synthetic-case",
        "mock-after-live",
        RECORDED,
        clock=clock,
        start_heartbeat=False,
    )
    mock.update_status(state="running", phase=1)
    mock.close()
    assert json.loads(lake.read_key(pointer))["run_id"] == "synthetic-live-2"


def test_live_view_url_follows_cell_lifecycle_and_action_pause(tmp_path: Path) -> None:
    lake = CountingLake(tmp_path / "lake")
    feed = RunFeed(lake, "synthetic-case", "synthetic-live", VULTR, start_heartbeat=False)
    key = "runs/synthetic-case/synthetic-live/status.json"
    feed.update_status(state="running", phase=5, live_view_url="https://cell.example.test/live")
    feed.update_status(state="paused", phase=5, checkpoint_pending="action")
    status = json.loads(lake.read_key(key))
    assert status["checkpoint_pending"] == "action"
    assert status["live_view_url"] == "https://cell.example.test/live"
    feed.update_status(state="running", phase=5, live_view_url=None)
    assert "live_view_url" not in json.loads(lake.read_key(key))
    feed.close()


def test_live_feed_accepts_supporting_jev_quarantine_step(tmp_path: Path) -> None:
    lake = CountingLake(tmp_path / "lake")
    screenshot = lake.put_bytes(b"screened screenshot")
    live = RunFeed(lake, "synthetic-case", "synthetic-live", VULTR, start_heartbeat=False)
    live.update_status(state="running", phase=5)
    step = {
        **trace("synthetic-live", JEV),
        "session_id": "browser-session",
        "event": "quarantine",
        "screenshot_key": screenshot,
        "observed": {"page_withheld": True},
        "screen": {
            "flagged": True,
            "jev_choice": "injection",
            "jev_confidence": 0.95,
            "safety_verdict": "unavailable",
            "reason": "Possible injection",
            "by": "controller",
        },
    }
    live.append_step(step)
    assert (
        json.loads(lake.read_key("runs/synthetic-case/synthetic-live/trace.live.jsonl"))[
            "session_id"
        ]
        == "browser-session"
    )
    with pytest.raises(ValueError, match="cannot ground gold"):
        live.append_step({**step, "value_ids": ["val:unsupported"]})
    live.close()
    mock = RunFeed(lake, "synthetic-case", "mock-synthetic", RECORDED, start_heartbeat=False)
    mock.update_status(state="running", phase=5)
    with pytest.raises(ValueError, match="backend differs"):
        mock.append_step({**step, "run_id": "mock-synthetic"})
    mock.close()


def test_feed_rejects_recorded_run_without_mock_prefix(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mock- run ID"):
        RunFeed(FileLake(tmp_path / "lake"), "synthetic-case", "ordinary-run", RECORDED)
    with pytest.raises(ValueError, match="Jev cannot be the primary run backend"):
        RunFeed(FileLake(tmp_path / "lake"), "synthetic-case", "ordinary-run", JEV)


def test_status_heartbeat_continues_during_a_quiet_run(tmp_path: Path) -> None:
    lake = CountingLake(tmp_path / "lake")
    feed = RunFeed(
        lake,
        "synthetic-case",
        "mock-quiet-run",
        RECORDED,
        interval_seconds=0.05,
    )
    status_key = "runs/synthetic-case/mock-quiet-run/status.json"
    try:
        feed.update_status(state="running", phase=1)
        deadline = time.monotonic() + 1
        while lake.written.count(status_key) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert lake.written.count(status_key) >= 2
    finally:
        feed.close()
