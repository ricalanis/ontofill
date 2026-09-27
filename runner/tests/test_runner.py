import json
import os
import subprocess
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
    cfg = Config(
        cases={"c1": CaseSpec("c1", case, lake)},
        state_dir=tmp_path / "state",
        poll_s=0.1,
        engine_cmd=f"{sys.executable} {FAKE} {{case_dir}} --to-phase {{to_phase}} --run-id {{run_id}} "
        f"--budget-usd {{budget}} --lake {lake}",
        engine_env_file=envfile,
        budgets={"c1": 1.0},
        kill_grace_s=2,
    )
    yield {"case": case, "lake": lake, "cfg": cfg, "calls": tmp_path / "c" / "calls.jsonl"}
    # a fake engine the runner no longer tracks (e.g. a sleep-mode child around a kill) must not outlive its test
    subprocess.run(["pkill", "-f", str(case)], check=False)


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
        json.dumps({"usage": {"est_usd": 1.5}}) + "\n"
    )
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


def test_no_authoritative_source_exits_needs_human_and_is_not_relaunched(setup, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "needs-human")
    approve(setup)
    r = Runner(setup["cfg"], env=dict(os.environ))

    run_until_idle(r)

    st = r.state.status("c1")
    assert st["state"] == "needs_human"
    assert st["checkpoint"] is None and st["phase"] == 3
    assert st["reason"].startswith("no authoritative source found for opening_hours")
    assert "queries:" in st["reason"] and "objections:" in st["reason"]
    assert [event["kind"] for event in events(setup)] == ["resumed", "needs-human"]
    assert "queries:" in events(setup)[-1]["detail"]
    assert "objections:" in events(setup)[-1]["detail"]
    assert "revise the brief or PRD authority policy" in events(setup)[-1]["detail"]

    r.poll_once()
    assert r.state.status("c1")["state"] == "needs_human"
    assert len(calls(setup)) == 1


def test_start_request_launches_a_new_run_once(setup, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "pause:prd")
    (setup["lake"] / "runs").rename(setup["lake"] / "runs-old")  # a case with no run yet
    ctl = setup["cfg"].state_dir / "cases" / "c1" / "control.json"
    ctl.parent.mkdir(parents=True)
    ctl.write_text(
        json.dumps({"paused": False, "start_requested": {"by": "group:approvers", "at": "t1", "to_phase": 2}})
    )
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
    # the console records the paused run in the decision (872a1c0); since R43 that binding is what lets a
    # decision resume a run that is no longer the latest
    (setup["case"] / "01-scope" / "APPROVED").write_text(
        json.dumps({"approver": "group:approvers", "checkpoint": "prd", "run_id": "run-abc"})
    )
    r = Runner(setup["cfg"], env=dict(os.environ))
    run_until_idle(r)
    c = calls(setup)
    assert len(c) == 1 and c[0]["run_id"] == "run-abc"


def test_lifting_the_kill_switch_relaunches_the_interrupted_run(setup, monkeypatch):
    """A run stopped mid-phase by the kill switch (its lake status still says running) is relaunched with the SAME
    run id once the switch is lifted, not left killed forever."""
    monkeypatch.setenv("FAKE_MODE", "sleep")
    approve(setup)
    r = Runner(setup["cfg"], env=dict(os.environ))
    r.poll_once()
    assert "c1" in r.children
    run = setup["lake"] / "runs" / "c1" / "run-abc" / "status.json"
    run.write_text(json.dumps({"state": "running", "phase": 2}))  # the engine was mid-phase when stopped
    (setup["cfg"].state_dir / "KILL").write_text("on")
    run_until_idle(r, timeout=8)
    assert r.state.status("c1")["state"] == "killed" and not r.children
    (setup["cfg"].state_dir / "KILL").unlink()
    r.env["FAKE_MODE"] = "pause:factors"  # the relaunched engine now pauses at the next checkpoint
    run_until_idle(r, timeout=8)
    assert calls(setup)[-1]["run_id"] == "run-abc"  # the relaunch is the same run
    st = r.state.status("c1")
    assert st["state"] == "waiting_approval" and st["checkpoint"] == "factors" and st["run_id"] == "run-abc"
    kinds = [e["kind"] for e in events(setup)]
    assert "killed" in kinds and kinds[-2:] == ["resumed", "paused_at_checkpoint"]
    assert any(e.get("detail") == "resumed after the kill switch was lifted" for e in events(setup))


def test_budget_arg_is_the_constant_case_cap(setup, monkeypatch):
    """The engine fingerprints its checkpoint inputs with --budget-usd; passing the remaining amount would change it
    between runs and invalidate decided artifacts. The runner passes the constant cap (1.00 here) every time."""
    monkeypatch.setenv("FAKE_MODE", "pause:factors")
    monkeypatch.setenv("FAKE_USD", "0.3")
    approve(setup)
    r = Runner(setup["cfg"], env=dict(os.environ))
    run_until_idle(r)
    assert [c["budget"] for c in calls(setup)] == ["1.00"]


def test_registry_is_reread_each_tick_with_budget_clamp_and_archive(setup, tmp_path, monkeypatch):
    """CONTRACT v1.0.6: cases.json under ONTOFILL_CASES_ROOT is the registry; new cases appear without a restart,
    archived ones are skipped, budgets are clamped by the global cap, relative paths can't escape the root."""
    root = tmp_path / "cases-root"
    root.mkdir()
    cfg = setup["cfg"]
    cfg.cases_root, cfg.env_cases, cfg.global_usd = root, dict(cfg.cases), 5.0
    r = Runner(cfg, env=dict(os.environ))
    r.refresh_cases()  # no registry yet: the env cases stay
    assert set(r.cfg.cases) == {"c1"}
    new = root / "n1" / "case"
    new.mkdir(parents=True)
    (root / "cases.json").write_text(
        json.dumps(
            {
                "version": 1,
                "cases": [
                    {"id": "n1", "path": "n1/case", "budget_usd": 50, "to_phase": 2},
                    {"id": "old", "path": "old/case", "archived": True},
                    {"id": "esc", "path": "../outside/case"},
                    {"id": "c1", "path": str(setup["case"]), "budget_usd": 0.5},
                ],
            }
        )
    )
    r.poll_once()
    assert set(r.cfg.cases) == {"c1", "n1"}  # archived and escaping entries are ignored
    assert r.case_budget("n1") == 5.0 and r.case_budget("c1") == 0.5  # clamped by the global cap
    assert r.case_to_phase("n1") == 2 and r.case_to_phase("c1") == 5
    (root / "cases.json").write_text("{not json")  # a torn write keeps the last good copy
    r.refresh_cases()
    assert set(r.cfg.cases) == {"c1", "n1"}


def test_global_spend_is_published_every_tick(setup, tmp_path):
    log = tmp_path / "gw.jsonl"
    rows = [{"status": 200, "est_usd": 0.25}, {"status": 502, "est_usd": 9}]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    setup["cfg"].gateway_log = log
    r = Runner(setup["cfg"], env=dict(os.environ))
    r.poll_once()
    st = r.state.status("c1")
    assert st["spent_usd_global"] == 0.25 and "gateway call log" in st["spent_usd_global_basis"]


def test_a_failure_after_a_resume_clears_the_checkpoint_and_says_why(setup, monkeypatch):
    """Found live: after a resume the status kept checkpoint "ontology" through a phase-3 failure."""
    monkeypatch.setenv("FAKE_MODE", "fail_phase:3")
    approve(setup)
    r = Runner(setup["cfg"], env=dict(os.environ))
    r.poll_once()
    running = r.state.status("c1")
    assert running["state"] == "running" and running["checkpoint"] is None
    assert running["resumed_from_checkpoint"] == "prd" and running["engine_stop"] is None
    run_until_idle(r)
    st = r.state.status("c1")
    assert st["state"] == "failed" and st["checkpoint"] is None
    assert st["reason"].startswith("engine exited 1 in phase 3: RuntimeError: fan-out loop stopped")
    stop = st["engine_stop"]
    assert stop["exit_code"] == 1 and stop["state"] == "failed" and stop["phase"] == 3
    assert "abc123secretvalue" not in json.dumps(st) and "<redacted>" in stop["reason"]


def test_a_pause_records_the_engines_reason(setup, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "pause:prd:model validation exhausted")
    approve(setup)
    r = Runner(setup["cfg"], env=dict(os.environ))
    run_until_idle(r)
    st = r.state.status("c1")
    assert st["state"] == "waiting_approval" and st["checkpoint"] == "prd"
    # it paused again at the checkpoint just decided; the runner says so AND keeps the engine's reason
    assert st["reason"] == "the engine paused again at this checkpoint after the decision: model validation exhausted"
    assert st["engine_stop"]["reason"] == "model validation exhausted" and st["engine_stop"]["exit_code"] == 3
    assert "(engine: model validation exhausted)" in events(setup)[-1]["detail"]


def test_a_clean_pause_has_no_reason_and_no_stale_stop(setup, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "pause:factors")
    approve(setup)
    r = Runner(setup["cfg"], env=dict(os.environ))
    run_until_idle(r)
    st = r.state.status("c1")
    assert st["reason"] is None and st["checkpoint"] == "factors"
    assert st["engine_stop"]["checkpoint_pending"] == "factors" and st["engine_stop"]["state"] == "paused"


def test_unreachable_sources_exit_is_needs_human_not_failed(setup, monkeypatch):
    """R37: exit 4 with "sources unreachable (…); no authoritative source found for …" ended as failed."""
    monkeypatch.setenv("FAKE_MODE", "unreachable")
    approve(setup)
    r = Runner(setup["cfg"], env=dict(os.environ))
    run_until_idle(r)
    st = r.state.status("c1")
    assert st["state"] == "needs_human" and st["checkpoint"] is None and st["phase"] == 3
    assert st["reason"].startswith("sources unreachable (27 blocked/redirected/403)")
    ev = events(setup)[-1]
    assert ev["kind"] == "needs-human" and "fix access or revise the PRD authority policy" in ev["detail"]
    r.poll_once()
    assert r.state.status("c1")["state"] == "needs_human" and len(calls(setup)) == 1  # not relaunched


def _stale_setup(setup, marker_run_id):
    """R43 as found live: an old run is still paused at a checkpoint in the lake, a later run has finished and is
    the latest, and the case's APPROVED marker answers that checkpoint for a later run (or names no run)."""
    runs = setup["lake"] / "runs" / "c1"
    (runs / "run-abc" / "status.json").write_text(
        json.dumps({"state": "paused", "checkpoint_pending": "prd", "updated_at": "2026-09-27T07:39:00+00:00"})
    )
    (runs / "run-new").mkdir()
    (runs / "run-new" / "status.json").write_text(
        json.dumps({"state": "done", "updated_at": "2026-09-27T10:25:20+00:00"})
    )
    (runs / "latest.json").write_text(json.dumps({"run_id": "run-new"}))
    marker = {
        "approver": "group:approvers",
        "checkpoint": "prd",
        **({"run_id": marker_run_id} if marker_run_id else {}),
    }
    (setup["case"] / "01-scope" / "APPROVED").write_text(json.dumps(marker))


@pytest.mark.parametrize("marker_run_id", ["run-mid", None])
def test_a_superseded_paused_run_is_never_resumed_by_a_later_decision(setup, monkeypatch, marker_run_id):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    _stale_setup(setup, marker_run_id)
    r = Runner(setup["cfg"], env=dict(os.environ))
    r.poll_once()
    assert not r.children and not calls(setup)  # nothing launched
    st = r.state.status("c1")
    assert st["state"] == "idle" and "start a new run" in (st.get("reason") or "")


def test_a_decision_made_for_the_paused_run_still_resumes_it(setup, monkeypatch):
    """The legitimate case (a finished proof run became latest after the case paused) keeps working when the
    marker names the paused run."""
    monkeypatch.setenv("FAKE_MODE", "pause:factors")
    _stale_setup(setup, "run-abc")
    r = Runner(setup["cfg"], env=dict(os.environ))
    run_until_idle(r)
    assert [c["run_id"] for c in calls(setup)] == ["run-abc"]


def test_lifting_the_kill_switch_never_relaunches_a_superseded_run(setup, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    runs = setup["lake"] / "runs" / "c1"
    (runs / "run-abc" / "status.json").write_text(json.dumps({"state": "running", "phase": 2}))
    (runs / "run-new").mkdir()
    (runs / "run-new" / "status.json").write_text(json.dumps({"state": "paused", "checkpoint_pending": "factors"}))
    (runs / "latest.json").write_text(json.dumps({"run_id": "run-new"}))
    r = Runner(setup["cfg"], env=dict(os.environ))
    r.state.set_status("c1", state="killed", run_id="run-abc")
    r.poll_once()
    assert not r.children and not calls(setup)


def test_a_multi_request_checkpoint_waits_for_every_answer_then_resumes(setup, monkeypatch):
    """Found live (run-9c120dd56edd): one old answered source made `source` look decided, the runner resumed at
    once, the engine paused again for three new source reviews, and answering them could never resume the run
    (the decision digest was the old marker's). Now the checkpoint is decided only when every request is
    answered, and the digest covers all answers."""
    monkeypatch.setenv("FAKE_MODE", "pause:factors")
    runs = setup["lake"] / "runs" / "c1"
    (runs / "run-abc" / "status.json").write_text(json.dumps({"state": "paused", "checkpoint_pending": "source"}))
    src = setup["case"] / "03-fanout" / "sources"
    for sid in ("s-old", "s-new1", "s-new2"):
        (src / sid).mkdir(parents=True)
        (src / sid / "APPROVAL_PENDING.md").write_text(PENDING.format(cp="source"))
    (src / "s-old" / "APPROVED").write_text(json.dumps({"approver": "a", "checkpoint": "source", "run_id": "run-abc"}))
    r = Runner(setup["cfg"], env=dict(os.environ))
    r.poll_once()
    assert not r.children and not calls(setup)
    assert r.state.status("c1")["state"] == "waiting_approval" and r.state.status("c1")["checkpoint"] == "source"
    (src / "s-new1" / "APPROVED").write_text(json.dumps({"approver": "a", "checkpoint": "source", "run_id": "run-abc"}))
    r.poll_once()
    assert not calls(setup)  # one of two new answers is not enough
    (src / "s-new2" / "APPROVED").write_text(
        json.dumps({"approver": "a", "checkpoint": "source", "decision": "deny", "reason": "r", "run_id": "run-abc"})
    )
    run_until_idle(r)
    assert [c["run_id"] for c in calls(setup)] == ["run-abc"]  # every answer in: resumed once
