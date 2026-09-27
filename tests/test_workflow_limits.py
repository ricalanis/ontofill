"""A killed sandbox job remains visible in the live run feed and jobs proof."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ontofill.sandbox import SandboxLimitExceeded
from ontofill.workflow import _scratch_case, run_case
from tests.genericity.fixtures.libraries import library_decisions
from tests.genericity.test_library_workflow import BRIEF, LibrarySearch, _capture, _fetch


def test_limit_kill_publishes_trace_failed_job_and_status(tmp_path: Path) -> None:
    case = tmp_path / "library-case"
    case.mkdir()
    (case / "brief.md").write_text(BRIEF.read_text(encoding="utf-8"), encoding="utf-8")
    run_id = f"mock-limit-{uuid.uuid4().hex[:12]}"

    def limited_capture(url: str, **kwargs) -> dict:
        row = _capture(url, **kwargs)["trace"][0]
        row["event"] = "limit_kill"
        row["evaluated"] = {"status": "hard_stop", "reason": "timeout"}
        teardown = {
            **row,
            "step_id": "step:teardown",
            "event": None,
            "evaluated": {"proof_checkpoint": "teardown"},
        }
        now = datetime.now(UTC).isoformat()
        result = {
            "trace": [row, teardown],
            "failure_reason": "timeout",
            "requested": {"url": url},
            "started_at": now,
            "ended_at": now,
            "limits": {"memory_mb": 1024, "cpus": 1, "pids": 256, "timeout_s": 90, "max_steps": 32},
            "usage": {"peak_memory_mb": 0, "wall_s": 90, "steps": 0},
            "proof": {
                "host_check": None,
                "pod_identity": None,
                "isolation_probe": None,
                "secrets": None,
                "teardown": {
                    "verified": True,
                    "pod_gone": True,
                    "proxy_gone": True,
                    "network_removed": True,
                },
            },
        }
        error = SandboxLimitExceeded("timeout", [row, teardown])
        error.result = result
        raise error

    with pytest.raises(SandboxLimitExceeded):
        run_case(
            case,
            run_id=run_id,
            decision=library_decisions(),
            preview_past_checkpoints=True,
            search_client=LibrarySearch(),
            capture=limited_capture,
            fetch=_fetch,
        )
    _, lake = _scratch_case(case, run_id)
    prefix = f"runs/{case.name}/{run_id}"
    status = json.loads(lake.read_key(f"{prefix}/status.json"))
    assert status["state"] == "failed"
    trace = [json.loads(line) for line in lake.read_key(f"{prefix}/trace.live.jsonl").splitlines()]
    assert [step["evaluated"]["reason"] for step in trace if step.get("event") == "limit_kill"] == [
        "timeout"
    ]
    job = json.loads(lake.read_key(f"{prefix}/jobs.jsonl"))
    assert job["failure_reason"] == "timeout"
    assert job["checkpoints"]["task"]["ok"] is False
    assert job["checkpoints"]["teardown"]["ok"] is True
