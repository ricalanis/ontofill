"""A run that fails because a phase loop gave up (the iteration cap, every candidate rejected) says so on /watch, the
failures view and the summary, in its own trace's words, and not only "engine exited 1". Proof jobs carry the phase of
the step that dispatched them and their own failure reason."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from test_viz_live import RUN, run_dir, set_status

from ontofill_console.viz import health_common as hc

OBJECTION = "https://branch.example/hours: opening_hours: the publisher is not authoritative for this jurisdiction"
T0 = datetime(2031, 1, 1, tzinfo=UTC)


def loop_step(i: int, stop: str | None) -> dict:
    return {
        "step_id": f"step:{RUN}:p3loop{i}",
        "run_id": RUN,
        "phase": 3,
        "mode": "D1",
        "observed": "candidates captured",
        "requested": "critique the candidates",
        "executed": "decision.complete_json",
        "evaluated": {"verdict": "rejected"},
        "loop": {
            "phase": 3,
            "role": "critique",
            "iteration": i,
            "objections": [OBJECTION],
            **({"stop_reason": stop} if stop else {}),
        },
        "value_ids": [],
        "ts": (T0 + timedelta(seconds=i)).isoformat(),
    }


@pytest.fixture
def p3_gave_up(cases_dir):
    with (run_dir(cases_dir) / "trace.live.jsonl").open("a") as f:
        for i in (1, 2, 3):
            f.write(json.dumps(loop_step(i, "max_iterations" if i == 3 else None)) + "\n")
    job = {
        "job_id": "job:p3probe",
        "run_id": RUN,
        "step_id": f"step:{RUN}:p3loop2",
        "source_id": "branch-example",
        "checkpoints": {
            "task": {
                "ok": False,
                "requested": {"url": "https://branch.example/"},
                "result": {"status": "http_403"},
                "value_ids": [],
            }
        },
        "failure_reason": "capture refused: http_403",
    }
    with (run_dir(cases_dir) / "jobs.jsonl").open("a") as f:
        f.write(json.dumps(job) + "\n")
    set_status(cases_dir, state="failed", phase=3, reason=None, checkpoint_pending=None)
    return cases_dir


def test_stop_cause_reads_the_last_loop():
    steps = [
        {
            **loop_step(i, "max_iterations" if i == 3 else None),
            "kind": "loop",
            "detail": {
                "phase": 3,
                "iteration": i,
                "role": "critique",
                "objections": [OBJECTION],
                "stop_reason": "max_iterations" if i == 3 else None,
                "usd": None,
            },
        }
        for i in (1, 2, 3)
    ]
    assert hc.stop_cause(steps, "running", "c", "r") is None  # a healthy run has no stop cause
    c = hc.stop_cause(steps, "failed", "c", "r")
    assert c["phase"] == 3 and c["stop_label"] == "iteration cap" and c["iterations"] == 3
    assert c["href"] == "/cases/c/discovery?run=r" and OBJECTION[:40] in c["objection"]


def test_failures_view_leads_with_why_the_run_stopped(client, p3_gave_up):
    m = client.get(f"/cases/libraries/api/viz/failures?run={RUN}").json()
    first = m["strips"][0]
    assert first["kind"] == "run_stop" and first["phase"] == 3 and "iteration cap" in first["title"]
    job = next(s for s in m["strips"] if s["kind"] == "checkpoint_fail" and "p3probe" in s["title"])
    assert job["phase"] == 3  # the dispatching step's phase, not an assumed P5
    assert "capture refused: http_403" in job["detail"]
    page = client.get(f"/cases/libraries/failures?run={RUN}").text
    assert "Run stopped" in page and "failed: capture refused: http_403" in page


def test_summary_and_watch_say_why(client, p3_gave_up):
    s = client.get(f"/cases/libraries/api/viz/summary?run={RUN}").json()
    assert any("iteration cap" in x for x in s["run"]["stop"])
    assert any(x["state"] == "block" and "phase 3" in x["title"] for x in s["next"])  # after pending approvals
    w = client.get("/api/watch").json()
    c = next(x for x in w["cases"] if x["case_id"] == "libraries")
    assert c["stop_cause"]["phase"] == 3
    assert "Why it stopped" in client.get("/watch").text


def test_pages_name_each_source_by_its_hosts(client):
    m = client.get("/cases/libraries/api/viz/pages").json()
    with_hosts = [g for g in m["groups"] if g["hosts"]]
    assert with_hosts, "fixture pages carry URLs"
    for g in with_hosts:
        chip = next(f for f in m["source_filters"] if f["source_id"] == g["source_id"])
        assert chip["label"].startswith(g["hosts"][0])  # the chip reads as a site, the id stays in its title
    page = client.get("/cases/libraries/pages").text
    assert f'title="{with_hosts[0]["source_id"]}"' in page and with_hosts[0]["hosts"][0] in page
