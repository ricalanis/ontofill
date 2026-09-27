"""Pattern A repair trace, scoring, and opt-in gVisor containment proof."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

from ontofill.contracts import validate_document
from ontofill.lake import FileLake
from ontofill.repair import CaptureCase, RepairFeedback, run_code_repair
from ontofill.repair import runner as repair_module
from ontofill.repair.runner import RepairExecution
from ontofill.sandbox import SandboxLimits

VULTR = {"backend": "vultr", "model": "synthetic-test", "at": "2026-09-26T00:00:00Z"}


def _case(lake: FileLake, payload: bytes, expected: tuple[dict, ...]) -> CaptureCase:
    return CaptureCase(lake.put_bytes(payload, {"content_type": "text/html"}), expected)


def _args(lake: FileLake, cases: list[CaptureCase]) -> dict:
    return {
        "lake": lake,
        "captures": cases,
        "run_id": "live-test",
        "source_id": "source-test",
        "objective_id": "objective-test",
        "tdd_path": "04-local/tdd.json",
        "generated_by": VULTR,
        "limits": SandboxLimits(timeout_s=3, max_steps=2),
        "max_attempts": 2,
        "parent_step_id": "step:parent",
    }


def test_failure_then_patch_pass_stores_artifacts_and_trace(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    cases = [
        _case(lake, b"<p>Alpha</p>", ({"name": "Alpha"},)),
        _case(lake, b"<p>Beta</p>", ({"name": "Beta"},)),
    ]
    feedback: list[RepairFeedback] = []
    expected_keys = [case.capture_key for case in cases]

    class Executor:
        def run_from_lake(self, _lake, captures, code, limits):
            assert [case.capture_key for case in captures] == expected_keys
            assert limits.timeout_s == 3
            if code == "broken code":
                return RepairExecution(
                    ([{"name": "Alpha"}, {"name": "Wrong"}], []), "assertion failed"
                )
            assert code == "patched code"
            return RepairExecution(([{"name": "Alpha"}], [{"name": "Beta"}]))

    def patch(item: RepairFeedback) -> str:
        feedback.append(item)
        return "patched code"

    emitted = []
    outcome = run_code_repair(
        **_args(lake, cases),
        initial_code="broken code",
        patch=patch,
        executor=Executor(),
        emit_step=emitted.append,
    )
    assert outcome.passed
    assert outcome.attempts == 2
    assert outcome.outputs == ([{"name": "Alpha"}], [{"name": "Beta"}])
    assert [row["repair"]["result"] for row in outcome.trace] == ["fail", "pass"]
    assert outcome.trace[0]["repair"]["test"] == {
        "pages": 2,
        "precision": 0.5,
        "coverage": 0.5,
    }
    assert feedback[0].stderr_excerpt == "assertion failed"
    assert feedback[0].test["coverage"] == 0.5
    assert '-      "name": "Beta"' in feedback[0].diff
    assert '+      "name": "Wrong"' in feedback[0].diff
    assert feedback[0].code_diff.startswith("--- previous.py")
    assert emitted == list(outcome.trace)
    assert lake.read_key(outcome.code_key) == b"patched code"
    for row in outcome.trace:
        validate_document("trace-step", row)
        assert row["requested"]["action"] == "code.write"
        assert row["executed"]["action"] == "code.test"
        for key in (
            row["repair"]["code_key"],
            row["repair"]["diff_key"],
            row["executed"]["stderr_key"],
            row["executed"]["output_diff_key"],
        ):
            assert lake.exists(key)
    assert b"-broken code" in lake.read_key(outcome.trace[1]["repair"]["diff_key"])
    assert b"+patched code" in lake.read_key(outcome.trace[1]["repair"]["diff_key"])


def test_retry_budget_and_limit_stop_are_visible(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    cases = [_case(lake, b"item", ({"item": "found"},))]

    class FailingExecutor:
        def run_from_lake(self, _lake, captures, code, limits):
            return RepairExecution((), "syntax error", error="candidate_error")

    outcome = run_code_repair(
        **_args(lake, cases),
        initial_code="first",
        patch=lambda feedback: "second",
        executor=FailingExecutor(),
    )
    assert not outcome.passed
    assert outcome.failure_reason == "max_attempts"
    assert len(outcome.trace) == 2
    assert all(row["repair"]["result"] == "fail" for row in outcome.trace)

    class LimitedExecutor:
        def run_from_lake(self, _lake, captures, code, limits):
            return RepairExecution((), "timed out", limit_reason="timeout")

    def patch_must_not_run(_feedback):
        raise AssertionError("limit breach must stop repair")

    stopped = run_code_repair(
        **_args(lake, cases),
        initial_code="while True: pass",
        patch=patch_must_not_run,
        executor=LimitedExecutor(),
    )
    assert not stopped.passed
    assert stopped.attempts == 1
    assert stopped.failure_reason == "timeout"
    assert [row["event"] for row in stopped.trace] == ["repair", "limit_kill"]
    for row in stopped.trace:
        validate_document("trace-step", row)
    assert stopped.trace[-1]["evaluated"]["reason"] == "timeout"


def test_patch_error_returns_the_failed_attempt_for_caller_fallback(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    cases = [_case(lake, b"<p>x</p>", ({"name": "x"},))]

    class FailingExecutor:
        def run_from_lake(self, _lake, captures, code, limits):
            return RepairExecution((), "candidate did not match", error="candidate_error")

    def failed_patch(_feedback):
        raise RuntimeError("inference unavailable")

    outcome = run_code_repair(
        **_args(lake, cases),
        initial_code="broken extractor",
        patch=failed_patch,
        executor=FailingExecutor(),
    )
    assert not outcome.passed
    assert outcome.failure_reason == "patch_error"
    assert len(outcome.trace) == 1
    assert outcome.trace[0]["repair"]["result"] == "fail"


def test_pod_runner_executes_bytes_and_returns_error_without_expectations(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "sandbox/code-repair/runner.py"
    spec = importlib.util.spec_from_file_location("repair_pod_runner", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = tmp_path / "input.json"
    output = tmp_path / "output.json"
    candidate = tmp_path / "candidate.py"
    payload.write_text(json.dumps({"captures": [base64.b64encode(b"<p>x</p>").decode()]}))
    candidate.write_text("def extract(capture):\n    return [{'size': len(capture)}]\n")
    module.run(payload, output, candidate)
    assert json.loads(output.read_text())["outputs"] == [[{"size": 8}]]
    candidate.write_text("def extract(capture):\n    raise ValueError('broken')\n")
    module.run(payload, output, candidate)
    result = json.loads(output.read_text())
    assert result["outputs"] == []
    assert result["error"] == "ValueError: broken"
    assert "ValueError: broken" in result["stderr"]


def test_rejects_invalid_budget_before_dispatch(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    cases = [_case(lake, b"item", ())]
    with pytest.raises(ValueError, match="max_attempts"):
        run_code_repair(
            **{**_args(lake, cases), "max_attempts": 3},
            initial_code="print('x')",
            patch=lambda _: None,
        )


def test_each_repair_attempt_writes_a_six_checkpoint_job_record(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    cases = [_case(lake, b"<p>Alpha</p>", ({"name": "Alpha"},))]
    host = {
        "docker_host": "synthetic-host",
        "runtime": "runsc",
        "runtime_available": True,
    }
    pod = {
        "hostname": "synthetic-repair-pod",
        "uname": {"system": "Linux", "release": "synthetic", "machine": "x86_64"},
        "cpu_virtualization_flags": [],
        "dev_kvm_present": False,
    }
    isolation = {
        "probes": [
            {"probe": "network_non_allowlisted", "blocked": True},
            {"probe": "write_outside_pod", "blocked": True},
            {"probe": "write_outside_writable_mount", "blocked": True},
            {"probe": "no_host_mounts", "blocked": True},
        ]
    }
    secrets = {
        "env_keys_found": 0,
        "files_with_keys": 0,
        "metadata_ip": "BLOCKED",
        "mesh": "BLOCKED",
        "ok": True,
    }
    teardown = {
        "pod_gone": True,
        "proxy_gone": True,
        "network_removed": True,
        "verified": True,
    }

    class ProofExecutor:
        def __init__(self) -> None:
            self.calls = 0

        def run_from_lake(self, _lake, captures, code, limits):
            self.calls += 1
            proof = {
                "host_check": host,
                "pod": pod,
                "isolation": isolation,
                "secrets": secrets,
                "teardown": teardown,
            }
            if self.calls == 1:
                return RepairExecution(([{"name": "Wrong"}],), "", proof=proof)
            return RepairExecution(([{"name": "Alpha"}],), "", proof=proof)

    executor = ProofExecutor()
    outcome = run_code_repair(
        **_args(lake, cases),
        initial_code="first",
        patch=lambda _feedback: "second",
        executor=executor,
    )
    assert outcome.passed
    assert [job["outcome"]["status"] for job in outcome.sandbox_jobs] == ["failed", "completed"]
    for job in outcome.sandbox_jobs:
        assert set(job["checkpoints"]) == {
            "host",
            "task",
            "where",
            "isolation",
            "secrets",
            "teardown",
        }
        assert job["run_id"] == "live-test"
        assert job["source_id"] == "source-test"
        assert job["checkpoints"]["host"]["runtime"] == "runsc"
        assert job["checkpoints"]["where"]["hostname"] == "synthetic-repair-pod"
        assert all(
            probe["result"] == "BLOCKED" for probe in job["checkpoints"]["isolation"]["probes"]
        )
        assert job["checkpoints"]["secrets"]["ok"] is True
        assert job["checkpoints"]["teardown"]["ok"] is True
        validate_document("jobs", job)


def test_missing_runsc_fails_before_creating_any_pod(monkeypatch) -> None:
    calls = []

    def fake_docker(*args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, '{"Runtimes":{"runc":{}}}', "")

    monkeypatch.setattr(repair_module, "_docker", fake_docker)
    with pytest.raises(RuntimeError, match="runsc is required"):
        repair_module.DockerRepairExecutor().run(
            "def extract(capture): return []", [b"<p>x</p>"], SandboxLimits()
        )
    assert calls == [("info", "--format", "{{json .}}")]


@pytest.mark.skipif(
    os.environ.get("ONTOFILL_RUN_CONTAINMENT") != "1",
    reason="set ONTOFILL_RUN_CONTAINMENT=1 for disposable runsc code repair proof",
)
def test_on_demand_destructive_loop_is_killed_in_repair_pod(tmp_path: Path) -> None:
    info = subprocess.run(
        ["docker", "info", "--format", "{{json .Runtimes}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    if '"runsc"' not in info.stdout:
        pytest.skip("runsc is unavailable")
    sentinel = tmp_path / "host-untouched"
    sentinel.write_text("intact")
    lake = FileLake(tmp_path / "lake")
    cases = [_case(lake, b"<p>x</p>", ({"name": "x"},))]
    code = "import os\nos.system('rm -rf / >/dev/null 2>&1')\nwhile True: pass\n"
    stopped = run_code_repair(
        **_args(lake, cases),
        initial_code=code,
        patch=lambda _: pytest.fail("limit stop must not ask for a patch"),
    )
    assert not stopped.passed
    assert stopped.failure_reason == "timeout"
    assert stopped.trace[-1]["event"] == "limit_kill"
    assert sentinel.read_text() == "intact"
