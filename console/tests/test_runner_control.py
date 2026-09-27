"""R18: runner operator actions in the console (start / pause / resume, the kill switch): same identity rules as
approvals, control.json written for the runner, every action logged in decisions.jsonl, and the state line shown."""

import json

from conftest import spec_for
from fastapi.testclient import TestClient
from test_approvals import log_lines

from ontofill_console import runner_state
from ontofill_console.web import create_app, settings_from_env

GROUPS = {"X-NetBird-Groups": "admins, approvers"}
MESH, PROXY = "100.82.17.158", "100.82.93.149"


def client(cases_dir, tmp_path, identity="sso-group", addr=PROXY):
    env = {
        "ONTOFILL_CONSOLE_CASES": spec_for(cases_dir),
        "ONTOFILL_CONSOLE_IDENTITY": identity,
        "ONTOFILL_CONSOLE_DIRECT_DENY": MESH,
        "ONTOFILL_RUNNER_STATE": str(tmp_path / "runner"),
    }
    return TestClient(create_app(settings_from_env(env)), client=(addr, 50000))


def post(c, url, data, headers=None, origin="http://testserver"):
    h = {**(headers or {}), "host": "testserver", **({"origin": origin} if origin else {})}
    return c.post(url, data=data, headers=h, follow_redirects=False)


def control(tmp_path):
    return json.loads((tmp_path / "runner" / "cases" / "libraries" / "control.json").read_text())


def test_actions_need_the_group_the_proxy_and_same_origin(cases_dir, tmp_path):
    url, form = "/cases/libraries/runner", {"action": "pause", "display_name": "Ana"}
    c = client(cases_dir, tmp_path)
    assert post(c, url, form).status_code == 403  # no groups header
    assert post(c, url, form, {"X-NetBird-Groups": "admins"}).status_code == 403  # not in approvers
    assert post(c, url, form, GROUPS, origin="https://evil.example").status_code == 403
    direct = client(cases_dir, tmp_path, addr=MESH)
    assert post(direct, url, form, GROUPS).status_code == 403  # our own peer, forged header
    assert post(c, url, {"action": "pause"}, GROUPS).status_code == 400  # no self-declared name
    assert post(c, url, {"action": "explode", "display_name": "Ana"}, GROUPS).status_code == 400
    assert not (tmp_path / "runner" / "cases" / "libraries" / "control.json").exists()
    assert not [x for x in log_lines(cases_dir) if x.get("checkpoint") == "runner"]


def test_pause_resume_start_write_control_and_the_decision_log(cases_dir, tmp_path):
    c = client(cases_dir, tmp_path)
    url = "/cases/libraries/runner"
    assert post(c, url, {"action": "pause", "display_name": "Ana"}, GROUPS).status_code == 303
    assert control(tmp_path)["paused"] is True
    assert post(c, url, {"action": "resume", "display_name": "Ana"}, GROUPS).status_code == 303
    ctl = control(tmp_path)
    assert ctl["paused"] is False and ctl["retry_at"]
    assert post(c, url, {"action": "start", "to_phase": "2", "display_name": "Ana"}, GROUPS).status_code == 303
    start = control(tmp_path)["start_requested"]
    assert start["to_phase"] == 2 and start["by"] == "group:approvers" and start["at"]
    assert post(c, url, {"action": "start", "to_phase": "9", "display_name": "Ana"}, GROUPS).status_code == 400
    lines = [x for x in log_lines(cases_dir) if x.get("checkpoint") == "runner"]
    assert [x["decision"] for x in lines] == ["pause", "resume", "start"]
    assert all(
        x["approver"] == "group:approvers"
        and x["identity_source"] == "sso-group"
        and x["unverified_name"] == "Ana"
        and x["verified"]["group"] == "approvers"
        for x in lines
    )


def test_kill_switch_toggle_is_logged(cases_dir, tmp_path):
    c = client(cases_dir, tmp_path)
    assert post(c, "/runner/kill", {"state": "on", "display_name": "Ana"}).status_code == 403
    assert post(c, "/runner/kill", {"state": "on", "display_name": "Ana"}, GROUPS).status_code == 303
    assert (tmp_path / "runner" / "KILL").exists()
    assert "Runner: OFF" in c.get("/", headers=GROUPS).text
    assert post(c, "/runner/kill", {"state": "off", "display_name": "Ana"}, GROUPS).status_code == 303
    assert not (tmp_path / "runner" / "KILL").exists()
    log = [json.loads(x) for x in (tmp_path / "runner" / "console-decisions.jsonl").read_text().splitlines()]
    assert [x["decision"] for x in log] == ["on", "off"] and log[0]["unverified_name"] == "Ana"


def test_state_line_and_inbox_events(cases_dir, tmp_path):
    root = tmp_path / "runner"
    (root / "cases" / "libraries").mkdir(parents=True)
    (root / "cases" / "libraries" / "status.json").write_text(
        json.dumps(
            {
                "state": "running",
                "run_id": "run-x",
                "running_since": "2026-09-27T03:10:00+00:00",
                "last_resumed_at": "2026-09-27T03:10:00+00:00",
            }
        )
    )
    c = client(cases_dir, tmp_path)
    page = c.get("/cases/libraries", headers=GROUPS).text
    assert "Resumed automatically at 03:10 UTC" in page and "run-x" in page
    assert "Resumed automatically at 03:10 UTC" in c.get("/cases/libraries/approvals", headers=GROUPS).text
    runner_state.append_line(
        root / "events.jsonl",
        {
            "ts": "2026-09-27T03:20:00+00:00",
            "case_id": "libraries",
            "kind": "failed",
            "detail": "engine exited 1\nboom",
            "run_id": "run-x",
        },
    )
    inbox = c.get("/api/viz/inbox", headers=GROUPS).json()
    assert any(s["kind"] == "runner_failed" and "run-x" in s["detail"] for s in inbox["strips"])


def test_runner_line_texts(tmp_path):
    root = tmp_path / "r"
    assert runner_state.line(root, "a")["text"] == "Runner has not seen this case yet"
    (root / "cases" / "a").mkdir(parents=True)
    (root / "cases" / "a" / "status.json").write_text(json.dumps({"state": "waiting_approval", "checkpoint": "prd"}))
    assert runner_state.line(root, "a")["text"].startswith("Waiting for the prd decision")
    (root / "cases" / "a" / "status.json").write_text(json.dumps({"state": "budget_stop"}))
    assert runner_state.line(root, "a")["text"] == "Stopped: budget"
    # a resume left over from an earlier run never stands in for the current run's start
    (root / "cases" / "a" / "status.json").write_text(
        json.dumps(
            {
                "state": "running",
                "running_since": "2026-09-27T17:29:15+00:00",
                "last_resumed_at": "2026-09-27T16:46:02+00:00",
            }
        )
    )
    assert runner_state.line(root, "a")["text"] == "Running since 17:29 UTC"
    (root / "cases" / "a" / "status.json").write_text(
        json.dumps(
            {
                "state": "waiting_approval",
                "checkpoint": "source",
                "running_since": "2026-09-27T17:29:15+00:00",
                "last_resumed_at": "2026-09-27T16:46:02+00:00",
            }
        )
    )
    assert "last resumed" not in runner_state.line(root, "a")["text"]
    (root / "KILL").write_text("on")
    assert runner_state.line(root, "a")["text"] == "Runner off (kill switch)"


def test_needs_human_is_a_needs_you_strip_while_it_lasts(cases_dir, tmp_path):
    """R28: P3 found no authoritative source; the runner's needs_human state is the inbox's 'needs you'."""
    root = tmp_path / "runner"
    (root / "cases" / "libraries").mkdir(parents=True)
    reason = "no authoritative source found for library"
    status_file = root / "cases" / "libraries" / "status.json"
    status_file.write_text(json.dumps({"state": "needs_human", "run_id": "run-y", "phase": 3, "reason": reason}))
    runner_state.append_line(
        root / "events.jsonl",
        {
            "ts": "2026-09-27T07:00:00+00:00",
            "case_id": "libraries",
            "kind": "needs-human",
            "detail": f"{reason} | needs you: revise the brief or PRD authority policy, then start a new run",
            "run_id": "run-y",
        },
    )
    assert runner_state.line(root, "libraries")["text"].startswith("Needs you: the engine found no authoritative")
    c = client(cases_dir, tmp_path)
    inbox = c.get("/api/viz/inbox", headers=GROUPS).json()
    [strip] = [s for s in inbox["strips"] if s["kind"] == "runner_needs-human"]
    assert strip["state"] == "need" and "run-y" in strip["detail"] and reason in strip["detail"]
    assert inbox["needs_you"] >= 1
    status_file.write_text(json.dumps({"state": "running", "run_id": "run-z"}))  # a person started a new run
    inbox = c.get("/api/viz/inbox", headers=GROUPS).json()
    assert not [s for s in inbox["strips"] if s["kind"] == "runner_needs-human"]
