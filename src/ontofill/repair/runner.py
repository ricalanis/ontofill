"""Test and repair untrusted extractor code inside one disposable gVisor pod per attempt."""

from __future__ import annotations

import base64
import difflib
import json
import re
import subprocess
import tempfile
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ontofill.lake import FileLake, S3Lake
from ontofill.refiner.provenance import validate_run_provenance
from ontofill.sandbox.capture import DockerTimeout, _docker, _pod_exit_reason
from ontofill.sandbox.limits import SandboxLimits

_BRONZE_KEY = re.compile(r"sha256:[0-9a-f]{64}\Z")
_POD_RUNNER = Path(__file__).resolve().parents[3] / "sandbox/code-repair/runner.py"
_MAX_CODE_BYTES = 256 * 1024
_MAX_CAPTURE_BYTES = 8 * 1024 * 1024
_MAX_RESULT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class CaptureCase:
    """One immutable capture and the records expected from it in a test fixture."""

    capture_key: str
    expected: tuple[dict, ...]


@dataclass(frozen=True)
class RepairExecution:
    outputs: tuple[list[dict], ...]
    stderr: str = ""
    error: str | None = None
    limit_reason: str | None = None
    input_proof: dict | None = None


@dataclass(frozen=True)
class RepairFeedback:
    attempt: int
    code: str
    stderr_excerpt: str
    diff: str
    code_diff: str
    test: dict


@dataclass(frozen=True)
class RepairOutcome:
    passed: bool
    trace: tuple[dict, ...]
    code_key: str
    attempts: int
    failure_reason: str | None = None
    outputs: tuple[list[dict], ...] = ()


class RepairExecutor(Protocol):
    def run_from_lake(
        self,
        lake: FileLake | S3Lake,
        captures: Sequence[CaptureCase],
        code: str,
        limits: SandboxLimits,
    ) -> RepairExecution: ...


class DockerRepairExecutor:
    """Run Python code with no network, no host mounts, no secrets, and enforced gVisor caps."""

    image = "python:3.12-alpine"

    def run(self, code: str, captures: Sequence[bytes], limits: SandboxLimits) -> RepairExecution:
        """Execute in-memory synthetic inputs; bronze callers use run_from_lake."""
        input_document = {"captures": [base64.b64encode(item).decode("ascii") for item in captures]}
        return self._execute(code, input_document, (), limits)

    def run_from_lake(
        self,
        lake: FileLake | S3Lake,
        captures: Sequence[CaptureCase],
        code: str,
        limits: SandboxLimits,
    ) -> RepairExecution:
        """Stage bronze paths into the runsc pod; application code never reads bytes."""
        references = []
        staged_paths = []
        total_bytes = 0
        with ExitStack() as stack:
            for index, case in enumerate(captures):
                if not _BRONZE_KEY.fullmatch(case.capture_key):
                    raise ValueError("capture_key must be a bronze sha256 key")
                if isinstance(lake, FileLake):
                    path = lake.bronze_path(case.capture_key)
                    size = path.stat().st_size
                else:
                    size = lake.bronze_size(case.capture_key)
                    if size > _MAX_CAPTURE_BYTES:
                        raise ValueError("capture exceeds 8 MiB")
                    directory = Path(
                        stack.enter_context(tempfile.TemporaryDirectory(prefix="ontofill-repair-"))
                    )
                    path = directory / f"capture-{index}"
                    lake.download_bronze(case.capture_key, path, max_bytes=_MAX_CAPTURE_BYTES)
                total_bytes += size
                if size > _MAX_CAPTURE_BYTES:
                    raise ValueError("capture exceeds 8 MiB")
                if total_bytes > _MAX_CAPTURE_BYTES:
                    raise ValueError("combined captures exceed 8 MiB")
                container_path = f"/work/capture-{index}"
                references.append(
                    {
                        "path": container_path,
                        "expected_sha256": case.capture_key.removeprefix("sha256:"),
                        "max_bytes": _MAX_CAPTURE_BYTES,
                    }
                )
                staged_paths.append((path, container_path))
            return self._execute(
                code,
                {"captures": references},
                tuple(staged_paths),
                limits,
            )

    def _execute(
        self,
        code: str,
        input_document: dict,
        capture_paths: Sequence[tuple[Path, str]],
        limits: SandboxLimits,
    ) -> RepairExecution:
        info = json.loads(_docker("info", "--format", "{{json .}}").stdout)
        if "runsc" not in info.get("Runtimes", {}):
            raise RuntimeError("gVisor runsc is required for code repair")
        name = f"ontofill-repair-{uuid.uuid4().hex[:12]}"
        with tempfile.TemporaryDirectory(prefix="ontofill-repair-") as directory:
            stage = Path(directory)
            (stage / "candidate.py").write_text(code, encoding="utf-8")
            (stage / "input.json").write_text(
                json.dumps(input_document),
                encoding="utf-8",
            )
            try:
                _docker(
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--runtime",
                    "runsc",
                    "--network",
                    "none",
                    "--read-only",
                    "--tmpfs",
                    "/work:rw,nosuid,nodev,mode=1777,size=64m",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--user",
                    "1000:1000",
                    *limits.docker_args(),
                    self.image,
                    "python",
                    "-c",
                    "import time; time.sleep(3600)",
                    timeout=90,
                )
                for filename, target in (
                    (_POD_RUNNER, "runner.py"),
                    (stage / "candidate.py", "candidate.py"),
                    (stage / "input.json", "input.json"),
                ):
                    # Docker cp cannot see gVisor's tmpfs /work through the
                    # remote daemon. Stream the file through the pod's stdin.
                    _docker(
                        "exec",
                        "-i",
                        name,
                        "sh",
                        "-c",
                        f"cat > /work/{target}",
                        input_text=filename.read_text(encoding="utf-8"),
                        timeout=30,
                    )
                for source_path, container_path in capture_paths:
                    self._stage_local_file(source_path, name, container_path)
                try:
                    result = _docker(
                        "exec",
                        name,
                        "python",
                        "/work/runner.py",
                        timeout=limits.timeout_s,
                        check=False,
                    )
                except DockerTimeout:
                    return RepairExecution(
                        (), "code.test exceeded pod wall-clock limit", limit_reason="timeout"
                    )
                reason = _pod_exit_reason(name, result)
                if reason:
                    return RepairExecution(
                        (), "code.test exceeded pod resource limit", limit_reason=reason
                    )
                if result.returncode:
                    detail = (result.stderr or result.stdout)[-8192:]
                    return RepairExecution((), detail, error=f"pod exited {result.returncode}")
                size = _docker(
                    "exec", name, "wc", "-c", "/work/output.json", timeout=10
                ).stdout.split()[0]
                if int(size) > _MAX_RESULT_BYTES:
                    return RepairExecution(
                        (), "runner result exceeded 8 MiB", error="result_too_large"
                    )
                document = json.loads(
                    _docker("exec", name, "cat", "/work/output.json", timeout=30).stdout
                )
                return RepairExecution(
                    tuple(document["outputs"]),
                    document["stderr"],
                    document.get("error"),
                    input_proof=document.get("input_proof"),
                )
            finally:
                _docker("rm", "-f", name, check=False, timeout=30)
                absence = _docker("inspect", name, check=False, timeout=10)
                detail = (absence.stderr + absence.stdout).lower()
                if absence.returncode == 0 or not any(
                    marker in detail for marker in ("no such object", "no such container")
                ):
                    raise RuntimeError("repair pod teardown could not be verified")

    @staticmethod
    def _stage_local_file(path: Path, name: str, container_path: str) -> None:
        source_path = str(path.resolve(strict=True))
        copied = _docker(
            "cp",
            source_path,
            f"{name}:{container_path}",
            timeout=30,
            check=False,
        )
        if copied.returncode == 0:
            return
        source = subprocess.Popen(
            ["cat", "--", source_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert source.stdout is not None
        try:
            staged = _docker(
                "exec",
                "-i",
                name,
                "sh",
                "-c",
                f"cat > {container_path}",
                timeout=30,
                check=False,
                input_stream=source.stdout,
            )
        finally:
            source.stdout.close()
        try:
            source.wait(timeout=5)
        except subprocess.TimeoutExpired:
            source.kill()
            source.wait(timeout=5)
            raise RuntimeError("repair bronze staging process timed out") from None
        if staged.returncode != 0 or source.returncode != 0:
            raise RuntimeError("repair bronze staging failed through Docker CLI")


def _score(outputs: Sequence[list[dict]], cases: Sequence[CaptureCase]) -> dict:
    true_positive = false_positive = false_negative = 0
    for index, case in enumerate(cases):
        actual = outputs[index] if index < len(outputs) else []
        predicted = Counter(json.dumps(row, sort_keys=True) for row in actual)
        expected = Counter(json.dumps(row, sort_keys=True) for row in case.expected)
        true_positive += sum((predicted & expected).values())
        false_positive += sum((predicted - expected).values())
        false_negative += sum((expected - predicted).values())
    if len(outputs) > len(cases):
        false_positive += sum(len(rows) for rows in outputs[len(cases) :])
    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else float(false_negative == 0)
    )
    coverage = (
        true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    )
    return {"pages": len(cases), "precision": precision, "coverage": coverage}


def _artifact(lake: FileLake | S3Lake, data: bytes, content_type: str, source_id: str) -> str:
    return lake.put_bytes(data, {"content_type": content_type, "source_id": source_id})


def _output_diff(outputs: Sequence[list[dict]], cases: Sequence[CaptureCase]) -> str:
    expected = [list(case.expected) for case in cases]
    before = json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2, default=str)
    after = json.dumps(outputs, ensure_ascii=False, sort_keys=True, indent=2, default=str)
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="expected.json",
            tofile="actual.json",
        )
    )[:32768]


def run_code_repair(
    *,
    lake: FileLake | S3Lake,
    captures: Sequence[CaptureCase],
    initial_code: str,
    patch: Callable[[RepairFeedback], str | None],
    run_id: str,
    source_id: str,
    objective_id: str | None,
    tdd_path: str,
    generated_by: dict[str, str],
    limits: SandboxLimits | None = None,
    max_attempts: int = 3,
    parent_step_id: str | None = None,
    executor: RepairExecutor | None = None,
    emit_step: Callable[[dict], None] | None = None,
) -> RepairOutcome:
    """Test stored captures, ask the control plane for patches, and log every bounded attempt.

    The patch callback receives only feedback and may call Vultr on the control plane.
    The pod receives neither expected records nor any inference credential.
    """
    provenance = validate_run_provenance(run_id, generated_by)
    budget = limits or SandboxLimits()
    if type(max_attempts) is not int or not 1 <= max_attempts <= min(10, budget.max_steps):
        raise ValueError("max_attempts must be 1..10 and within limits.max_steps")
    if not captures:
        raise ValueError("at least one stored capture is required")
    if not source_id or not tdd_path:
        raise ValueError("source_id and tdd_path are required")
    total_bytes = 0
    for case in captures:
        if not _BRONZE_KEY.fullmatch(case.capture_key):
            raise ValueError("capture_key must be a bronze sha256 key")
        if isinstance(lake, FileLake):
            size = lake.bronze_path(case.capture_key).stat().st_size
        else:
            size = lake.bronze_size(case.capture_key)
        if size > _MAX_CAPTURE_BYTES:
            raise ValueError("capture exceeds 8 MiB")
        total_bytes += size
        if any(not isinstance(row, dict) for row in case.expected):
            raise ValueError("expected records must be dictionaries")
    if total_bytes > _MAX_CAPTURE_BYTES:
        raise ValueError("combined captures exceed 8 MiB")
    runner = executor or DockerRepairExecutor()
    code = initial_code
    previous_code = ""
    trace: list[dict] = []
    last_key = ""
    for attempt in range(1, max_attempts + 1):
        code_bytes = code.encode("utf-8")
        if not code_bytes or len(code_bytes) > _MAX_CODE_BYTES:
            raise ValueError("candidate code must be 1..262144 bytes")
        code_key = _artifact(lake, code_bytes, "text/x-python", source_id)
        last_key = code_key
        diff = "".join(
            difflib.unified_diff(
                previous_code.splitlines(keepends=True),
                code.splitlines(keepends=True),
                fromfile="previous.py",
                tofile="candidate.py",
            )
        )
        diff_key = _artifact(lake, diff.encode("utf-8"), "text/x-diff", source_id)
        try:
            execution = runner.run_from_lake(lake, captures, code, budget)
        except (OSError, RuntimeError, ValueError) as exc:
            execution = RepairExecution((), f"{type(exc).__name__}: {exc}", error="executor_error")
        stderr = execution.stderr[-8192:]
        try:
            test = _score(execution.outputs, captures)
        except (TypeError, ValueError) as exc:
            test = {"pages": len(captures), "precision": 0.0, "coverage": 0.0}
            stderr = f"{stderr}\ninvalid extractor output: {exc}"[-8192:]
        stderr_key = _artifact(lake, stderr.encode("utf-8"), "text/plain", source_id)
        output_diff = _output_diff(execution.outputs, captures)
        output_diff_key = _artifact(lake, output_diff.encode("utf-8"), "text/x-diff", source_id)
        passed = (
            execution.error is None
            and execution.limit_reason is None
            and len(execution.outputs) == len(captures)
            and test["precision"] == 1.0
            and test["coverage"] == 1.0
        )
        repair_step = {
            "step_id": f"step:{uuid.uuid4().hex}",
            "run_id": run_id,
            "phase": 5,
            "source_id": source_id,
            "objective_id": objective_id,
            "tdd_path": tdd_path,
            "mode": "D1",
            "event": "repair",
            "observed": {"capture_keys": [case.capture_key for case in captures]},
            "requested": {"action": "code.write", "code_key": code_key},
            "executed": {
                "action": "code.test",
                "stderr_key": stderr_key,
                "output_diff_key": output_diff_key,
                "network": "none",
                "runtime": "runsc_requested"
                if isinstance(runner, DockerRepairExecutor)
                else "test-double",
                "limits": budget.as_dict(),
                "input_proof": execution.input_proof,
            },
            "evaluated": {"status": "pass" if passed else "fail", "error": execution.error},
            "repair": {
                "attempt": attempt,
                "max_attempts": max_attempts,
                "code_key": code_key,
                "result": "pass" if passed else "fail",
                "stderr_excerpt": stderr[-500:],
                "diff_key": diff_key,
                "test": test,
            },
            "parent_step_id": parent_step_id,
            "value_ids": [],
            "ts": datetime.now(UTC).isoformat(),
            "generated_by": provenance.copy(),
        }
        trace.append(repair_step)
        if emit_step is not None:
            emit_step(repair_step.copy())
        if passed:
            return RepairOutcome(
                True,
                tuple(trace),
                code_key,
                attempt,
                outputs=tuple(execution.outputs),
            )
        if execution.limit_reason:
            limit_step = {
                **trace[-1],
                "step_id": f"step:{uuid.uuid4().hex}",
                "event": "limit_kill",
                "evaluated": {"status": "hard_stop", "reason": execution.limit_reason},
                "parent_step_id": trace[-1]["step_id"],
                "ts": datetime.now(UTC).isoformat(),
            }
            limit_step.pop("repair")
            trace.append(limit_step)
            if emit_step is not None:
                emit_step(limit_step.copy())
            return RepairOutcome(False, tuple(trace), code_key, attempt, execution.limit_reason)
        if execution.error == "executor_error":
            return RepairOutcome(False, tuple(trace), code_key, attempt, "executor_error")
        if attempt == max_attempts:
            return RepairOutcome(False, tuple(trace), code_key, attempt, "max_attempts")
        feedback = RepairFeedback(attempt, code, stderr[-500:], output_diff[:8192], diff, test)
        try:
            candidate = patch(feedback)
        except Exception:  # noqa: BLE001 - inference failures trigger the caller's safe fallback
            return RepairOutcome(False, tuple(trace), code_key, attempt, "patch_error")
        if candidate is None or candidate == code:
            return RepairOutcome(False, tuple(trace), code_key, attempt, "no_patch")
        previous_code, code = code, candidate
    raise AssertionError(f"unreachable after code key {last_key}")
