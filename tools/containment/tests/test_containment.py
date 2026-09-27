"""Offline tests for the containment plumbing (no Docker, no gateway).

Run from the engine checkout:
    uv run pytest tools/containment/tests -q
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime

import pytest
from ontofill.lake import FileLake
from ontofill.runfeed import RunFeed
from ontofill.sandbox.jobs import validate_job_record

from ontofill_containment.gateway import Gateway
from ontofill_containment.main import (
    quarantine_step,
    repair_job_record,
    verify_step,
)

RUN_ID = "containment-demo-test"
PROV = {"backend": "vultr", "model": "glm-5.3-flash", "at": "2026-09-27T00:00:00Z"}


def _step(run_id, checkpoint, detail, *, event=None):
    row = {
        "step_id": f"step:{checkpoint}",
        "run_id": run_id,
        "phase": 5,
        "source_id": "sandbox-containment",
        "objective_id": None,
        "tdd_path": "tools/containment/README.md",
        "mode": "S1",
        "observed": {"checkpoint": checkpoint},
        "requested": {"proof_checkpoint": checkpoint},
        "executed": detail,
        "evaluated": {"proof_checkpoint": checkpoint, "status": "verified"},
        "parent_step_id": "step:dispatch",
        "value_ids": [],
        "ts": datetime.now(UTC).isoformat(),
        "generated_by": dict(PROV),
    }
    if event:
        row["event"] = event
    return row


def _capture_result(lake: FileLake, run_id: str) -> dict:
    """A capture success result shaped exactly like `ontofill.sandbox.capture.capture_url`."""
    html_key = lake.put_bytes(b"<html>hostile</html>", {"content_type": "text/html"})
    shot = lake.put_bytes(b"\x89PNG\x00", {"content_type": "image/png"})
    host = {
        "docker_host": "sandbox",
        "runtime": "runsc",
        "runtime_available": True,
        "cpu_virtualization_flags": ["vmx"],
        "dev_kvm_present": True,
    }
    pod = {"hostname": "pod-1", "uname": {"system": "Linux", "release": "6", "machine": "x86_64"}}
    isolation = {
        "network": {"host": "denied.invalid", "blocked": True, "proxy_logged_block": True},
        "write_outside_pod": {"blocked": True},
        "write_outside_writable_mount": {"blocked": True},
        "blocked": True,
    }
    secrets = {
        "ok": True,
        "env_keys_found": 0,
        "files_with_keys": 0,
        "metadata_ip": "BLOCKED",
        "mesh": "BLOCKED",
    }
    dispatch = _step(
        run_id, "dispatch_result", {"url": "http://host.docker.internal:1/hostile.html"}
    )
    dispatch["evaluated"] = {"proof_checkpoint": "dispatch_result", "status": "captured"}
    dispatch["executed"] = {"network_request": True, "html_key": html_key, "screenshot_key": shot}
    dispatch["screenshot_key"] = shot
    dispatch["step_id"] = "step:dispatch"
    trace = [
        dispatch,
        _step(run_id, "host_check", host),
        _step(run_id, "pod_identity", pod),
        _step(run_id, "isolation_probe", isolation),
        _step(run_id, "secrets", secrets),
        _step(
            run_id,
            "teardown",
            {"pod_gone": True, "proxy_gone": True, "network_removed": True, "verified": True},
        ),
    ]
    return {
        "url": "http://host.docker.internal:1/hostile.html",
        "status": 200,
        "redirect_chain": ["http://host.docker.internal:1/hostile.html"],
        "html": "<html>hostile page</html>",
        "html_key": html_key,
        "screenshot_key": shot,
        "trace": trace,
        "egress_events": [
            {"decision": "block", "host": "169.254.169.254", "method": "GET"},
            {"decision": "block", "host": "blocked.invalid", "method": "GET"},
            {"decision": "allow", "host": "host.docker.internal", "method": "GET"},
        ],
        "proof": {
            "dispatch_result": {"url": "http://host.docker.internal:1/hostile.html"},
            "host_check": host,
            "pod_identity": pod,
            "isolation_probe": isolation,
            "secrets": secrets,
            "teardown": {
                "pod_gone": True,
                "proxy_gone": True,
                "network_removed": True,
                "verified": True,
            },
        },
        "started_at": "2026-09-27T00:00:00Z",
        "ended_at": "2026-09-27T00:00:01Z",
        "limits": {"memory_mb": 1024, "cpus": 1.0, "pids": 256, "timeout_s": 90, "max_steps": 32},
        "usage": {"peak_memory_mb": 10.0, "wall_s": 1.0, "steps": 2},
    }


class FakeGateway:
    model = "glm-5.3-flash"

    def __init__(self, flagged: bool = True):
        self.flagged = flagged
        self.calls: list[str] = []

    def screen_page(self, html: str, *, step_id: str) -> dict:
        self.calls.append(step_id)
        if not self.flagged:
            raise RuntimeError("the gateway did not flag the hostile fixture")
        return {
            "screen": {
                "flagged": True,
                "jev_choice": None,
                "jev_confidence": None,
                "safety_verdict": "unavailable",
                "reason": "gateway X-BA-Gate: flagged (quarantined in the prompt)",
                "by": "gateway",
            },
            "usage": {"prompt_tokens": 20, "completion_tokens": 2},
            "model": "glm-5.3-flash",
            "gate": "flagged",
        }


class FakeServer:
    def __init__(self, filename: str, content: str):
        self.url_value = "http://host.docker.internal:1234/" + filename

    def url(self) -> str:
        return self.url_value

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None


class FakeRepairOutcome:
    failure_reason = "timeout"

    def __init__(self, rows):
        self.trace = rows


def _repair_steps(run_id):
    repair = {
        "step_id": "step:repair-1",
        "run_id": run_id,
        "phase": 5,
        "source_id": "sandbox-containment",
        "objective_id": None,
        "tdd_path": "tools/containment/README.md",
        "mode": "D1",
        "event": "repair",
        "observed": {"capture_keys": ["sha256:" + "0" * 64]},
        "requested": {"action": "code.write", "code_key": "sha256:" + "1" * 64},
        "executed": {
            "action": "code.test",
            "stderr_key": "sha256:" + "2" * 64,
            "output_diff_key": "sha256:" + "3" * 64,
            "network": "none",
            "runtime": "runsc_requested",
            "limits": {"memory_mb": 1024, "cpus": 1.0, "pids": 256, "timeout_s": 3, "max_steps": 2},
        },
        "evaluated": {"status": "fail", "error": None},
        "repair": {
            "attempt": 1,
            "max_attempts": 2,
            "code_key": "sha256:" + "1" * 64,
            "result": "fail",
            "stderr_excerpt": "timed out",
            "diff_key": "sha256:" + "4" * 64,
            "test": {"pages": 1, "precision": 1.0, "coverage": 0.0},
        },
        "parent_step_id": None,
        "value_ids": [],
        "ts": datetime.now(UTC).isoformat(),
        "generated_by": dict(PROV),
    }
    limit = {
        **repair,
        "step_id": "step:limit-1",
        "event": "limit_kill",
        "evaluated": {"status": "hard_stop", "reason": "timeout"},
        "parent_step_id": "step:repair-1",
    }
    limit.pop("repair")
    return [repair, limit]


def test_quarantine_and_verify_steps_are_schema_valid(tmp_path):
    lake = FileLake(tmp_path / "lake")
    feed = RunFeed(lake, "case", RUN_ID, PROV, start_heartbeat=False)
    feed.update_status(state="running", phase=5)
    screened = FakeGateway().screen_page("x", step_id="step:screen")
    q = quarantine_step(
        run_id=RUN_ID,
        capture_step_id="step:dispatch",
        url="http://host.docker.internal:1/hostile.html",
        page_key="sha256:" + "a" * 64,
        blocked_requests=["169.254.169.254", "blocked.invalid"],
        screened=screened,
        provenance=PROV,
    )
    feed.append_step(q)  # RunFeed validates against the trace-step schema
    v = verify_step(
        run_id=RUN_ID,
        parent_step_id="step:limit-1",
        sentinel_path="/tmp/s",
        sentinel_value="abc",
        sentinel_after="abc",
        leftover=[],
        provenance=PROV,
    )
    feed.append_step(v)
    feed.update_status(state="done", phase=5)
    feed.close()
    rows = [
        json.loads(line)
        for line in (lake.root / "runs/case" / RUN_ID / "trace.live.jsonl").read_text().splitlines()
    ]
    assert {row.get("event") for row in rows} == {"quarantine", None}
    assert rows[0]["screen"]["flagged"] is True
    assert rows[0]["observed"]["blocked_requests"] == ["169.254.169.254", "blocked.invalid"]


def test_repair_job_record_has_six_checkpoints(tmp_path):
    probe = {
        "hostname": "pod-2",
        "uname": {"system": "Linux", "release": "6", "machine": "x86_64"},
        "cpu_virtualization_flags": ["vmx"],
        "dev_kvm_present": True,
        "secrets": {
            "env_keys_found": 0,
            "files_with_keys": 0,
            "metadata_ip": "BLOCKED",
            "mesh": "BLOCKED",
        },
        "isolation": {
            "network": {"host": "ontofill-probe.invalid", "blocked": True},
            "write_outside_pod": {"path": "/host/x", "blocked": True},
            "write_outside_writable_mount": {"path": "/etc/x", "blocked": True},
        },
    }
    record = repair_job_record(
        run_id=RUN_ID,
        step="step:limit-1",
        host_info={"Name": "sandbox", "Runtimes": {"runsc": {}}},
        probe=probe,
        limits={"memory_mb": 1024, "cpus": 1.0, "pids": 256, "timeout_s": 3, "max_steps": 2},
        started_at="2026-09-27T00:00:00Z",
        ended_at="2026-09-27T00:00:04Z",
        wall_s=4.0,
        sentinel_path="/tmp/s",
        sentinel_value="abc",
        sentinel_after="abc",
        leftover=[],
        provenance=PROV,
    )
    validate_job_record(record)
    assert set(record["checkpoints"]) == {
        "host",
        "task",
        "where",
        "isolation",
        "secrets",
        "teardown",
    }
    assert record["failure_reason"] == "timeout"
    assert all(p["result"] == "BLOCKED" for p in record["checkpoints"]["isolation"]["probes"])
    assert record["checkpoints"]["task"]["ok"] is False


def test_run_hostile_page_appends_only(tmp_path):
    lake = FileLake(tmp_path / "lake")
    feed = RunFeed(lake, "case", RUN_ID, PROV, start_heartbeat=False)
    feed.update_status(state="running", phase=5)
    run_dir = lake.root / "runs/case" / RUN_ID
    trace_path = run_dir / "trace.live.jsonl"
    # A pre-existing line that a naive writer would clobber.
    original = b'{"run_id":"other-run","step_id":"step:old"}\n'
    trace_path.write_bytes(original)

    from ontofill_containment import main as main_mod

    result = main_mod.run_hostile_page(
        lake=lake,
        feed=feed,
        case_id="case",
        run_id=RUN_ID,
        provenance=PROV,
        gateway=FakeGateway(),
        capture=lambda *a, **k: _capture_result(lake, RUN_ID),
        server_factory=FakeServer,
    )
    feed.update_status(state="done", phase=5)
    feed.close()

    data = trace_path.read_bytes()
    assert data.startswith(original), "existing lines must be preserved"
    rows = [json.loads(line) for line in data.splitlines()]
    ids = [row["step_id"] for row in rows]
    assert len(ids) == len(set(ids)), "step ids must be unique"
    assert rows[-1]["event"] == "quarantine"
    jobs = [json.loads(line) for line in (run_dir / "jobs.jsonl").read_text().splitlines()]
    assert len(jobs) == 1 and "checkpoints" in jobs[0]
    assert result["blocked"] == ["169.254.169.254", "blocked.invalid"]


def test_run_destructive_loop_appends_limit_kill_and_six_checkpoints(tmp_path):
    lake = FileLake(tmp_path / "lake")
    feed = RunFeed(lake, "case", RUN_ID, PROV, start_heartbeat=False)
    feed.update_status(state="running", phase=5)
    run_dir = lake.root / "runs/case" / RUN_ID

    probe_result = {
        "hostname": "pod-9",
        "uname": {"system": "Linux", "release": "6", "machine": "x86_64"},
        "cpu_virtualization_flags": ["vmx", "svm"],
        "dev_kvm_present": True,
        "secrets": {
            "env_keys_found": 0,
            "files_with_keys": 0,
            "metadata_ip": "BLOCKED",
            "mesh": "BLOCKED",
        },
        "isolation": {
            "network": {"host": "ontofill-probe.invalid", "blocked": True},
            "write_outside_pod": {"path": "/host/x", "blocked": True},
            "write_outside_writable_mount": {"path": "/etc/x", "blocked": True},
        },
    }

    class FakeSSH:
        def __init__(self):
            self.files = {}
            self.removed = []

        def __call__(self, command, *, timeout=15, check=True):
            if command.startswith("printf "):
                self.files["sentinel"] = command.split()[2]
            elif command.startswith("cat "):
                return subprocess.CompletedProcess(command, 0, self.files.get("sentinel", ""), "")
            elif command.startswith("rm -f "):
                self.removed.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

    def fake_repair(**kwargs):
        rows = _repair_steps(kwargs["run_id"])
        kwargs["emit_step"](rows[0])
        kwargs["emit_step"](rows[1])
        return FakeRepairOutcome(rows)

    from ontofill_containment import main as main_mod

    ssh = FakeSSH()
    out = main_mod.run_destructive_loop(
        lake=lake,
        feed=feed,
        case_id="case",
        run_id=RUN_ID,
        provenance=PROV,
        repair=fake_repair,
        probe=lambda *a, **k: probe_result,
        docker_cmd=lambda *a, **k: subprocess.CompletedProcess(
            a, 0, '{"Name":"sandbox","Runtimes":{"runsc":{}}}', ""
        ),
        ssh=ssh,
        leftover_check=list,
    )
    feed.update_status(state="done", phase=5)
    feed.close()

    rows = [json.loads(line) for line in (run_dir / "trace.live.jsonl").read_text().splitlines()]
    assert [row["event"] for row in rows if row.get("event")] == ["repair", "limit_kill"]
    record = json.loads((run_dir / "jobs.jsonl").read_text().splitlines()[0])
    validate_job_record(record)
    assert set(record["checkpoints"]) == {
        "host",
        "task",
        "where",
        "isolation",
        "secrets",
        "teardown",
    }
    assert record["failure_reason"] == "timeout"
    assert record["checkpoints"]["task"]["result"]["host_sentinel"] == "intact"
    assert out["reason"] == "timeout"
    assert ssh.removed, "the sentinel must be cleaned up"


def test_gateway_requires_flag_and_reads_header():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "glm-5.3-flash"}]})
        assert "<page_content>" in json.loads(request.content)["messages"][0]["content"]
        return httpx.Response(
            200,
            json={"usage": {"prompt_tokens": 5, "completion_tokens": 1}},
            headers={"X-BA-Gate": "flagged"},
        )

    import os

    os.environ["VULTR_INFERENCE_BASE_URL"] = "http://gateway.test/v1"
    os.environ["ONTOFILL_GATEWAY_TOKEN"] = "synthetic"
    try:
        gw = Gateway(client=httpx.Client(transport=httpx.MockTransport(handler), timeout=5))
        out = gw.screen_page("<html>hi</html>", step_id="step:s")
    finally:
        os.environ.pop("VULTR_INFERENCE_BASE_URL", None)
        os.environ.pop("ONTOFILL_GATEWAY_TOKEN", None)
    assert out["gate"] == "flagged" and out["screen"]["by"] == "gateway"


def test_gateway_fails_closed_on_clean():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "glm-5.3-flash"}]})
        return httpx.Response(200, json={}, headers={"X-BA-Gate": "clean"})

    import os

    os.environ["VULTR_INFERENCE_BASE_URL"] = "http://gateway.test/v1"
    os.environ["ONTOFILL_GATEWAY_TOKEN"] = "synthetic"
    try:
        gw = Gateway(client=httpx.Client(transport=httpx.MockTransport(handler), timeout=5))
        with pytest.raises(RuntimeError, match="did not flag"):
            gw.screen_page("<html>hi</html>", step_id="step:s")
    finally:
        os.environ.pop("VULTR_INFERENCE_BASE_URL", None)
        os.environ.pop("ONTOFILL_GATEWAY_TOKEN", None)
