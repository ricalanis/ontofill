import json
import os
import sys
import time
from pathlib import Path

import pytest

from ontofill_runner.config import CaseSpec, Config
from ontofill_runner.runner import Runner, redact

FAKE = Path(__file__).with_name("fake_engine.py")
PENDING = "---\ncheckpoint: {cp}\nphase: 1\n---\n# Approval pending\n"


@pytest.fixture
def setup(tmp_path):
    case = tmp_path / "c" / "case"
    (case / "01-scope").mkdir(parents=True)
    (case / "01-scope" / "APPROVAL_PENDING.md").write_text(PENDING.format(cp="prd"))
    lake = tmp_path / "c" / "lake"
    run = lake / "runs" / "c1" / "run-abc"
    run.mkdir(parents=True)
    (lake / "runs" / "c1" / "latest.json").write_text(json.dumps({"run_id": "run-abc"}))
    (run / "status.json").write_text(json.dumps({"state": "paused", "checkpoint_pending": "prd"}))
    envfile = tmp_path / "engine.env"
    envfile.write_text("ENGINE_SECRET=do-not-log-me\n")
    cfg = Config(cases={"c1": CaseSpec("c1", case, lake)}, state_dir=tmp_path / "state", poll_s=0.1,
                 engine_cmd=f"{sys.executable} {FAKE} {{case_dir}} --to-phase {{to_phase}} --run-id {{run_id}} "
                            f"--budget-usd {{budget}} --lake {lake}",
                 engine_env_file=envfile, budgets={"c1": 1.0}, kill_grace_s=2)
    return {"case": case, "lake": lake, "cfg": cfg, "calls": tmp_path / "c" / "calls.jsonl"}


def run_until_idle(r: Runner, timeout=10):
    deadline = time.monotonic() + timeout
    r.poll_once()
    while r.children and time.monotonic() < deadline:
        time.sleep(0.1)
        r.poll_once()


def calls(s):
    return [json.loads(x) for x in s["calls"].read_text().splitlines()] if s["calls"].exists() else []


def events(s):
    p = s["cfg"].state_dir / "events.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def approve(s):
    (s["case"] / "01-scope" / "APPROVED").write_text(json.dumps({"approver": "group:approvers", "checkpoint": "prd"}))


def test_no_decision_no_launch(setup, monkeypatch):
    r = Runner(setup["cfg"])
    r.poll_once()
    assert not r.children and not calls(setup)
    assert r.state.status("c1")["state"] == "waiting_approval" and r.state.status("c1")["checkpoint"] == "prd"


def test_decision_resumes_the_same_run_once(setup, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "pause:factors")
    approve(setup)
    r = Runner(setup["cfg"], env=dict(os.environ))
    run_until_idle(r)
    c = calls(setup)
    assert len(c) == 1 and c[0]["run_id"] == "run-abc" and c[0]["to_phase"] == "5" and c[0]["secret_seen"]
    st = r.state.status("c1")
    assert st["state"] == "waiting_approval" and st["checkpoint"] == "factors" and st["last_resumed_at"]
    kinds = [e["kind"] for e in events(setup)]
    assert kinds == ["resumed", "paused_at_checkpoint"]
    run_until_idle(r)  # the same decision never launches twice
    assert len(calls(setup)) == 1
    assert "do-not-log-me" not in (setup["cfg"].state_dir / "events.jsonl").read_text()


def test_same_checkpoint_repause_is_not_relaunched(setup, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "pause:prd")  # e.g. the engine refused a stale digest
    approve(setup)
    r = Runner(setup["cfg"], env=dict(os.environ))
    run_until_idle(r)
    run_until_idle(r)
    assert len(calls(setup)) == 1
    assert "paused again" in (r.state.status("c1").get("reason") or "")


def test_pause_flag_blocks_and_unpause_resumes(setup, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "done")
    approve(setup)
    ctl = setup["cfg"].state_dir / "cases" / "c1" / "control.json"
    ctl.parent.mkdir(parents=True)
    ctl.write_text(json.dumps({"paused": True}))
    r = Runner(setup["cfg"], env=dict(os.environ))
    r.poll_once()
    assert not calls(setup) and r.state.status("c1")["state"] == "paused"
    ctl.write_text(json.dumps({"paused": False}))
    run_until_idle(r)
    assert len(calls(setup)) == 1 and r.state.status("c1")["state"] == "done"
    assert [e["kind"] for e in events(setup)][:2] == ["paused", "unpaused"]


def test_kill_switch_blocks_launch_and_stops_a_running_child(setup, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    approve(setup)
    r = Runner(setup["cfg"], env=dict(os.environ))
    (setup["cfg"].state_dir).mkdir(parents=True, exist_ok=True)
    (setup["cfg"].state_dir / "KILL").write_text("on")
    r.poll_once()
    assert not r.children and r.state.status("c1")["state"] == "killed"
    (setup["cfg"].state_dir / "KILL").unlink()
    r.poll_once()
    assert "c1" in r.children
    time.sleep(0.5)
    (setup["cfg"].state_dir / "KILL").write_text("on")
    run_until_idle(r, timeout=8)
    assert not r.children and r.state.status("c1")["state"] == "killed"


def test_budget_cap_stops_before_launch(setup, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "done")
    approve(setup)
    (setup["lake"] / "runs" / "c1" / "run-abc" / "trace.live.jsonl").write_text(
        json.dumps({"usage": {"est_usd": 1.5}}) + "\n")
    r = Runner(setup["cfg"], env=dict(os.environ))
    r.poll_once()
    assert not calls(setup)
    st = r.state.status("c1")
    assert st["state"] == "budget_stop" and "1.50" in st["reason"]
    assert [e["kind"] for e in events(setup)] == ["budget_stop"]


def test_global_cap_from_gateway_log(setup, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_MODE", "done")
    approve(setup)
    gw = tmp_path / "gw.jsonl"
    gw.write_text(json.dumps({"status": 200, "est_usd": 5.0}) + "\n" + json.dumps({"status": 502, "est_usd": 9}) + "\n")
    setup["cfg"].gateway_log, setup["cfg"].global_usd = gw, 4.0
    r = Runner(setup["cfg"], env=dict(os.environ))
    r.poll_once()
    assert not calls(setup) and r.state.status("c1")["state"] == "budget_stop"


def test_running_child_crossing_the_budget_is_stopped(setup, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    monkeypatch.setenv("FAKE_USD", "2.0")
    approve(setup)
    r = Runner(setup["cfg"], env=dict(os.environ))
    r.poll_once()
    time.sleep(0.5)
    run_until_idle(r, timeout=8)
    st = r.state.status("c1")
    assert st["state"] == "budget_stop" and st["spent_usd_case"] >= 1.0  # the final spend, not the launch-time value


def test_lock_prevents_a_second_child(setup, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    approve(setup)
    r1 = Runner(setup["cfg"], env=dict(os.environ))
    r2 = Runner(setup["cfg"], env=dict(os.environ))
    r1.poll_once()
    r2.poll_once()
    assert "c1" in r1.children and "c1" not in r2.children
    (setup["cfg"].state_dir / "KILL").write_text("on")
    run_until_idle(r1, timeout=8)


def test_failure_records_a_redacted_tail(setup, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "fail")
    approve(setup)
    r = Runner(setup["cfg"], env=dict(os.environ))
    run_until_idle(r)
    ev = events(setup)[-1]
    assert ev["kind"] == "failed" and "Traceback" in ev["detail"]
    assert "THISISASECRETVALUE" not in ev["detail"] and "<redacted>" in ev["detail"]
    assert r.state.status("c1")["state"] == "failed"


def test_start_request_launches_a_new_run_once(setup, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "pause:prd")
    (setup["lake"] / "runs").rename(setup["lake"] / "runs-old")  # a case with no run yet
    ctl = setup["cfg"].state_dir / "cases" / "c1" / "control.json"
    ctl.parent.mkdir(parents=True)
    ctl.write_text(json.dumps({"paused": False, "start_requested": {"by": "group:approvers", "at": "t1",
                                                                     "to_phase": 2}}))
    r = Runner(setup["cfg"], env=dict(os.environ))
    run_until_idle(r)
    c = calls(setup)
    assert len(c) == 1 and c[0]["run_id"].startswith("run-") and c[0]["to_phase"] == "2"
    run_until_idle(r)
    assert len(calls(setup)) == 1  # the same request is handled once
    assert [e["kind"] for e in events(setup)][:2] == ["start_requested", "started"]


def test_redact():
    out = redact("token=abc123 Authorization: Bearer xyz and a long one sk-" + "A" * 40)
    assert "abc123" not in out and "xyz" not in out and "A" * 40 not in out


def test_failed_stays_failed_until_console_resume(setup, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "fail")
    approve(setup)
    r = Runner(setup["cfg"], env=dict(os.environ))
    run_until_idle(r)
    run_until_idle(r)
    assert len(calls(setup)) == 1 and r.state.status("c1")["state"] == "failed"
    ctl = setup["cfg"].state_dir / "cases" / "c1" / "control.json"
    ctl.write_text(json.dumps({"paused": False, "retry_at": "t2"}))
    monkeypatch.setenv("FAKE_MODE", "done")
    r.env["FAKE_MODE"] = "done"
    run_until_idle(r)
    assert len(calls(setup)) == 2 and r.state.status("c1")["state"] == "done"


def test_a_finished_proof_run_does_not_hide_the_paused_run(setup, monkeypatch):
    """A later, finished run in the same lake (e.g. a proof written into a scratch lake) must not make the runner
    lose the case's paused run: it follows the most recent paused run instead of latest.json."""
    lake = setup["lake"]
    proof = lake / "runs" / "c1" / "proof-run"
    proof.mkdir(parents=True)
    (proof / "status.json").write_text(json.dumps({"state": "done", "phase": 5}))
    (lake / "runs" / "c1" / "latest.json").write_text(json.dumps({"run_id": "proof-run"}))
    monkeypatch.setenv("FAKE_MODE", "pause:factors")
    approve(setup)
    r = Runner(setup["cfg"], env=dict(os.environ))
    run_until_idle(r)
    c = calls(setup)
    assert len(c) == 1 and c[0]["run_id"] == "run-abc"
