"""Parse immutable bronze objects inside a networkless, resource-capped runsc pod."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

from ontofill.lake import FileLake, S3Lake
from ontofill.lake.storage import validate_key
from ontofill.sandbox.capture import CaptureError, DockerTimeout, _docker
from ontofill.sandbox.jobs import validate_job_record
from ontofill.sandbox.limits import SandboxLimits

ParseFormat = Literal["csv", "xlsx", "xlsm", "html", "json", "pdf"]
_BRONZE_KEY = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SUPPORTED_FORMATS = frozenset({"csv", "xlsx", "xlsm", "html", "json", "pdf"})
_MAX_INPUT_BYTES = 8 * 1024 * 1024
_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
_MAX_ROWS = 10_000
_MAX_DOCUMENT_ITEMS = 100_000
_POD_DIRECTORY = Path(__file__).resolve().parents[3] / "sandbox/parse-pod"
_IMAGE_LOCK = threading.Lock()


@dataclass(frozen=True)
class ParseResult:
    """Bounded rows from one bronze object, with an auditable sandbox job proof."""

    bronze_key: str
    format: str
    rows: tuple[dict[str, Any], ...]
    truncated: bool
    text: str
    page_text: str
    links: tuple[dict[str, str], ...]
    dom_skeleton_hash: str | None
    trace: tuple[dict[str, Any], ...]
    job_record: dict[str, Any]

    def as_parsed_file(self) -> Any:
        """Rebuild the safe parsed-file value object without reopening bronze bytes."""
        from ontofill_scrape.models import ParsedFile, ParsedRow

        rows = tuple(
            ParsedRow(
                row.get("sheet"),
                row["row_number"],
                tuple(row["values"]),
            )
            for row in self.rows
            if "row_number" in row and "values" in row
        )
        return ParsedFile(self.format, rows, text=self.text, truncated=self.truncated)


@dataclass(frozen=True)
class JsonDocumentResult:
    """A bounded JSON object decoded in the pod, never in the control process."""

    bronze_key: str
    document: Mapping[str, Any]
    trace: tuple[dict[str, Any], ...]
    job_record: dict[str, Any]


class SandboxParseError(RuntimeError):
    """A parse failure with a six-checkpoint record; no partial rows are returned."""

    def __init__(
        self,
        reason: str,
        *,
        job_record: dict[str, Any],
        trace: tuple[dict[str, Any], ...],
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.job_record = job_record
        self.trace = trace


@dataclass(frozen=True)
class ParseExecution:
    """Transfer envelope returned by a pod executor, containing no lake client."""

    output: dict[str, Any] | None
    host: dict[str, Any] | None
    pod: dict[str, Any] | None
    isolation: dict[str, Any] | None
    secrets: dict[str, Any] | None
    teardown: dict[str, Any]
    peak_memory_mb: float = 0.0
    wall_s: float = 0.0
    steps: int = 0
    error: str | None = None
    limit_reason: str | None = None


class ParseExecutor(Protocol):
    def run(
        self,
        payload: bytes,
        *,
        kind: str,
        format: str,
        max_rows: int,
        base_url: str,
        limits: SandboxLimits,
    ) -> ParseExecution: ...


class DockerParseExecutor:
    """Run a parser worker in a disposable runsc container without network or secrets."""

    image_prefix = "ontofill-parse-pod"

    def run(
        self,
        payload: bytes,
        *,
        kind: str,
        format: str,
        max_rows: int,
        base_url: str,
        limits: SandboxLimits,
    ) -> ParseExecution:
        name = f"ontofill-parse-{uuid.uuid4().hex[:12]}"
        host: dict[str, Any] | None = None
        pod: dict[str, Any] | None = None
        isolation: dict[str, Any] | None = None
        secrets: dict[str, Any] | None = None
        output: dict[str, Any] | None = None
        failure: str | None = None
        limit_reason: str | None = None
        steps = 0
        peak_memory_mb = 0.0
        created = False
        deadline: float | None = None
        wall_started: float | None = None
        teardown = _empty_teardown()

        try:
            info = json.loads(_docker("info", "--format", "{{json .}}").stdout)
            if "runsc" not in info.get("Runtimes", {}):
                failure = "gVisor runsc is required for bronze parsing"
                limit_reason = "runtime_unavailable"
            else:
                host = {
                    "docker_host": info.get("Name", "unknown"),
                    "operating_system": info.get("OperatingSystem", "unknown"),
                    "architecture": info.get("Architecture", "unknown"),
                    "runtime": "runsc",
                    "runtime_available": True,
                }
                image = self._image()
                wall_started = time.monotonic()
                deadline = wall_started + limits.timeout_s
                # Mark cleanup necessary before asking Docker to create the pod:
                # a timed out CLI can still have created the named container.
                created = True
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
                    "/work:rw,nosuid,nodev,noexec,mode=1777,size=64m",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--user",
                    "1000:1000",
                    *limits.docker_args(),
                    image,
                    "python",
                    "-c",
                    "import time; time.sleep(3600)",
                    timeout=min(90.0, _remaining(deadline)),
                )

                envelope = json.dumps(
                    {
                        "kind": kind,
                        "format": format,
                        "max_rows": max_rows,
                        "base_url": base_url,
                        "payload": base64.b64encode(payload).decode("ascii"),
                    },
                    separators=(",", ":"),
                )
                _docker(
                    "exec",
                    "-i",
                    name,
                    "sh",
                    "-c",
                    "cat > /work/input.json",
                    input_text=envelope,
                    timeout=_remaining(deadline),
                )
                result = None
                try:
                    result = _docker(
                        "exec",
                        name,
                        "python",
                        "/app/runner.py",
                        "/work/input.json",
                        "/work/output.json",
                        timeout=_remaining(deadline),
                        check=False,
                    )
                    steps = 1
                except DockerTimeout:
                    failure = "pod exceeded wall-clock limit"
                    limit_reason = "timeout"

                if result is not None:
                    reason = _pod_failure_reason(name, result, deadline)
                    if reason:
                        failure = f"pod exceeded {reason} limit"
                        limit_reason = reason
                    elif result.returncode:
                        failure = "parse pod runner failed"
                    else:
                        size_result = _docker(
                            "exec",
                            name,
                            "wc",
                            "-c",
                            "/work/output.json",
                            timeout=_remaining(deadline),
                        )
                        size = int(size_result.stdout.split()[0])
                        if size > _MAX_OUTPUT_BYTES:
                            failure = "parse pod output exceeded 4 MiB"
                            limit_reason = "output_too_large"
                        else:
                            document = json.loads(
                                _docker(
                                    "exec",
                                    name,
                                    "cat",
                                    "/work/output.json",
                                    timeout=_remaining(deadline),
                                ).stdout
                            )
                            if not isinstance(document, dict):
                                failure = "parse pod returned incomplete proof"
                            else:
                                output = document
                                proof = document.get("proof")
                                proof = proof if isinstance(proof, dict) else {}
                                pod = proof.get("pod")
                                isolation = proof.get("isolation")
                                secrets = proof.get("secrets")
                                usage = document.get("usage")
                                usage = usage if isinstance(usage, dict) else {}
                                peak_memory_mb = float(usage.get("peak_memory_mb", 0))
                                error = document.get("error")
                                if error:
                                    failure = (
                                        str(error.get("code", "parse_error"))
                                        if isinstance(error, dict)
                                        else "parse_error"
                                    )
                                if (
                                    not isinstance(pod, dict)
                                    or not isinstance(isolation, dict)
                                    or not isinstance(secrets, dict)
                                ):
                                    failure = "parse pod returned incomplete proof"
        except (CaptureError, OSError, ValueError, subprocess.SubprocessError) as exc:
            if isinstance(exc, DockerTimeout) and deadline is not None:
                failure = "pod exceeded wall-clock limit"
                limit_reason = "timeout"
            else:
                failure = f"sandbox execution failed: {type(exc).__name__}"
        finally:
            if created:
                try:
                    _docker("rm", "-f", name, check=False, timeout=30)
                    absent = _docker("inspect", name, check=False, timeout=10)
                    detail = (absent.stderr + absent.stdout).lower()
                    pod_gone = absent.returncode != 0 and any(
                        marker in detail for marker in ("no such object", "no such container")
                    )
                    teardown = {
                        "pod_gone": pod_gone,
                        "proxy_gone": True,
                        "network_removed": True,
                        "verified": pod_gone,
                    }
                except (CaptureError, OSError, subprocess.SubprocessError):
                    teardown = {
                        "pod_gone": False,
                        "proxy_gone": True,
                        "network_removed": True,
                        "verified": False,
                    }

        return ParseExecution(
            output,
            host,
            pod,
            isolation,
            secrets,
            teardown,
            peak_memory_mb=peak_memory_mb,
            wall_s=round(time.monotonic() - wall_started, 3) if wall_started else 0.0,
            steps=steps,
            error=failure,
            limit_reason=limit_reason,
        )

    @classmethod
    def _image(cls) -> str:
        assets = Path(os.environ.get("ONTOFILL_SANDBOX_ASSETS", _POD_DIRECTORY.parent))
        pod_directory = assets / "parse-pod"
        if not pod_directory.is_dir():
            pod_directory = _POD_DIRECTORY
        digest = hashlib.sha256(
            b"".join(
                path.read_bytes() for path in sorted(pod_directory.iterdir()) if path.is_file()
            )
        ).hexdigest()[:12]
        image = f"{cls.image_prefix}:{digest}"
        with _IMAGE_LOCK:
            if _docker("image", "inspect", image, check=False).returncode:
                _docker("build", "-t", image, str(pod_directory), timeout=900)
        return image


def parse_bronze(
    lake: FileLake | S3Lake,
    bronze_key: str,
    *,
    format: ParseFormat,
    max_rows: int = 300,
    max_bytes: int = _MAX_INPUT_BYTES,
    limits: SandboxLimits | Mapping[str, object] | None = None,
    run_id: str | None = None,
    source_id: str = "source:bronze-parse",
    step_id: str | None = None,
    objective_id: str | None = None,
    tdd_path: str = "sandbox/parse",
    generated_by: Mapping[str, str] | None = None,
    executor: ParseExecutor | None = None,
) -> ParseResult:
    """Parse a bronze CSV, XLSX, HTML, JSON table, or PDF without opening bytes on host.

    The control process reads and digest-checks the object, then transfers opaque
    bytes to the parser pod. All format decoding happens in `sandbox/parse-pod`.
    A size, row, output, parser, isolation, or teardown failure raises
    `SandboxParseError`; that exception carries the failed job and trace proof.
    """
    kind = _format(format)
    _validate_limits(max_rows=max_rows, max_bytes=max_bytes)
    budget = _parse_limits(limits)
    context = _job_context(
        run_id=run_id,
        source_id=source_id,
        step_id=step_id,
        objective_id=objective_id,
        tdd_path=tdd_path,
        generated_by=generated_by,
    )
    data = _read_verified(lake, bronze_key)
    base_url = _bronze_base_url(lake, bronze_key)
    if len(data) > max_bytes:
        execution = ParseExecution(
            None,
            None,
            None,
            None,
            None,
            _empty_teardown(),
            error="input_too_large",
            limit_reason="input_too_large",
        )
        return _result(
            execution,
            bronze_key=bronze_key,
            format=kind,
            kind=kind,
            max_rows=max_rows,
            max_bytes=max_bytes,
            limits=budget,
            context=context,
        )
    runner = executor or DockerParseExecutor()
    execution = runner.run(
        data,
        kind=kind,
        format=kind,
        max_rows=max_rows,
        base_url=base_url,
        limits=budget,
    )
    return _result(
        execution,
        bronze_key=bronze_key,
        format=kind,
        kind=kind,
        max_rows=max_rows,
        max_bytes=max_bytes,
        limits=budget,
        context=context,
    )


def parse_bronze_json(
    lake: FileLake | S3Lake,
    bronze_key: str,
    *,
    max_bytes: int = _MAX_INPUT_BYTES,
    limits: SandboxLimits | Mapping[str, object] | None = None,
    run_id: str | None = None,
    source_id: str = "source:bronze-json",
    step_id: str | None = None,
    objective_id: str | None = None,
    tdd_path: str = "sandbox/parse-json",
    generated_by: Mapping[str, str] | None = None,
    executor: ParseExecutor | None = None,
) -> JsonDocumentResult:
    """Decode a CKAN-style JSON document in the parse pod and return its root object."""
    _validate_limits(max_rows=1, max_bytes=max_bytes)
    budget = _parse_limits(limits)
    context = _job_context(
        run_id=run_id,
        source_id=source_id,
        step_id=step_id,
        objective_id=objective_id,
        tdd_path=tdd_path,
        generated_by=generated_by,
    )
    data = _read_verified(lake, bronze_key)
    base_url = _bronze_base_url(lake, bronze_key)
    if len(data) > max_bytes:
        execution = ParseExecution(
            None,
            None,
            None,
            None,
            None,
            _empty_teardown(),
            error="input_too_large",
            limit_reason="input_too_large",
        )
        return _result(
            execution,
            bronze_key=bronze_key,
            format="json",
            kind="json_document",
            max_rows=1,
            max_bytes=max_bytes,
            limits=budget,
            context=context,
        )
    runner = executor or DockerParseExecutor()
    execution = runner.run(
        data,
        kind="json_document",
        format="json",
        max_rows=1,
        base_url=base_url,
        limits=budget,
    )
    result = _result(
        execution,
        bronze_key=bronze_key,
        format="json",
        kind="json_document",
        max_rows=1,
        max_bytes=max_bytes,
        limits=budget,
        context=context,
    )
    document = result.rows[0].get("document") if result.rows else None
    if not isinstance(document, Mapping):
        failed = _failed_after_result(
            result,
            reason="json_document_root_must_be_object",
            context=context,
            bronze_key=bronze_key,
            kind="json_document",
            format="json",
            max_rows=1,
            max_bytes=max_bytes,
            limits=budget,
        )
        raise failed
    return JsonDocumentResult(
        bronze_key=bronze_key,
        document=document,
        trace=result.trace,
        job_record=result.job_record,
    )


def _result(
    execution: ParseExecution,
    *,
    bronze_key: str,
    format: str,
    kind: str,
    max_rows: int,
    max_bytes: int,
    limits: SandboxLimits,
    context: dict[str, Any],
) -> ParseResult:
    output = execution.output or {}
    task_ok = output.get("ok") is True and execution.error is None
    reason = execution.limit_reason or execution.error
    if reason is None and not task_ok:
        error = output.get("error")
        reason = (
            str(error.get("code", "parse_error")) if isinstance(error, Mapping) else "parse_error"
        )
    rows = output.get("rows", []) if task_ok else []
    text = output.get("text", "") if task_ok else ""
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        rows = []
        task_ok = False
        reason = "parse_pod_returned_invalid_rows"
    elif len(rows) > max_rows + (1 if kind == "json" else 0):
        rows = []
        task_ok = False
        reason = "parse_pod_returned_too_many_rows"
    if not isinstance(text, str):
        text = ""
        task_ok = False
        reason = "parse_pod_returned_invalid_text"
    page_text = output.get("page_text", "")
    links = output.get("links", [])
    skeleton_hash = output.get("dom_skeleton_hash")
    if (
        not isinstance(page_text, str)
        or len(page_text) > 200_000
        or not isinstance(links, list)
        or len(links) > max_rows
        or any(
            not isinstance(link, dict)
            or set(link) != {"url", "text", "rel"}
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in link.items()
            )
            for link in links
        )
    ):
        task_ok = False
        reason = "parse_pod_returned_invalid_html_metadata"
        page_text = ""
        links = []
    if skeleton_hash is not None and (
        not isinstance(skeleton_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", skeleton_hash)
    ):
        task_ok = False
        reason = "parse_pod_returned_invalid_skeleton_hash"
        skeleton_hash = None
    truncated = output.get("truncated", False)
    if not isinstance(truncated, bool):
        task_ok = False
        reason = "parse_pod_returned_invalid_truncation_flag"
        truncated = False
    if not task_ok:
        rows = []
        text = ""
        page_text = ""
        links = []
        skeleton_hash = None
    result = ParseResult(
        bronze_key=bronze_key,
        format=format,
        rows=tuple(rows),
        truncated=truncated if task_ok else False,
        text=text,
        page_text=page_text if task_ok else "",
        links=tuple(links) if task_ok else (),
        dom_skeleton_hash=skeleton_hash if task_ok else None,
        trace=(),
        job_record={},
    )
    record, trace = _job_and_trace(
        execution,
        context=context,
        task_ok=task_ok,
        reason=reason,
        request={
            "tool": "file.parse",
            "bronze_key": bronze_key,
            "format": format,
            "kind": kind,
            "max_rows": max_rows,
            "max_bytes": max_bytes,
        },
        result={
            "bronze_key": bronze_key,
            "format": format,
            "row_count": len(result.rows),
            "truncated": result.truncated,
            "text_chars": len(result.text),
            "page_text_chars": len(result.page_text),
            "link_count": len(result.links),
            "dom_skeleton_hash": result.dom_skeleton_hash,
        },
        limits=limits,
    )
    if not task_ok:
        raise SandboxParseError(reason or "parse failed", job_record=record, trace=trace)
    if not _job_proof_passes(record):
        raise SandboxParseError("parse pod proof did not pass", job_record=record, trace=trace)
    return ParseResult(
        bronze_key=result.bronze_key,
        format=result.format,
        rows=result.rows,
        truncated=result.truncated,
        text=result.text,
        page_text=result.page_text,
        links=result.links,
        dom_skeleton_hash=result.dom_skeleton_hash,
        trace=trace,
        job_record=record,
    )


def _job_and_trace(
    execution: ParseExecution,
    *,
    context: dict[str, Any],
    task_ok: bool,
    reason: str | None,
    request: dict[str, Any],
    result: dict[str, Any],
    limits: SandboxLimits,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    host = execution.host
    pod = execution.pod
    isolation = execution.isolation
    secrets = execution.secrets
    teardown = execution.teardown
    started_at = context["started_at"]
    ended_at = datetime.now(UTC).isoformat()
    where = (
        {
            "ok": bool(pod.get("hostname") and pod.get("uname", {}).get("system") == "Linux"),
            "hostname": pod.get("hostname", "unknown"),
            "uname": pod.get("uname", {}),
        }
        if pod
        else {"ok": False, "not_run": True}
    )
    host_checkpoint = (
        {
            "ok": bool(host.get("runtime_available") and host.get("runtime") == "runsc"),
            "sandbox_host": host.get("docker_host", "unknown"),
            "runtime": host.get("runtime", "unknown"),
            "virt": {
                "cpu_virtualization_flags": pod.get("cpu_virtualization_flags", []) if pod else [],
                "dev_kvm_present": bool(pod and pod.get("dev_kvm_present")),
            },
        }
        if host
        else {"ok": False, "not_run": True}
    )
    task_result = {**result, "status": "parsed" if task_ok else "failed"}
    if reason:
        task_result["reason"] = reason
    isolation_checkpoint = _isolation_checkpoint(isolation)
    secret_checkpoint = _secrets_checkpoint(secrets)
    teardown_detail = {
        "pod_gone": bool(teardown.get("pod_gone")),
        "proxy_gone": bool(teardown.get("proxy_gone")),
        "network_removed": bool(teardown.get("network_removed")),
        "verified": bool(teardown.get("verified")),
    }
    teardown_checkpoint = {
        "ok": bool(teardown_detail["verified"]),
        "detail": teardown_detail,
    }
    record: dict[str, Any] = {
        "job_id": context["job_id"],
        "run_id": context["run_id"],
        "step_id": context["step_id"],
        "source_id": context["source_id"],
        "started_at": started_at,
        "ended_at": ended_at,
        "generated_by": context["generated_by"],
        "limits": limits.as_dict(),
        "usage": {
            "peak_memory_mb": max(0.0, float(execution.peak_memory_mb)),
            "wall_s": max(0.0, float(execution.wall_s)),
            "steps": max(0, int(execution.steps)),
        },
        "checkpoints": {
            "host": host_checkpoint,
            "task": {
                "ok": task_ok,
                "requested": request,
                "result": task_result,
                "value_ids": [],
            },
            "where": where,
            "isolation": isolation_checkpoint,
            "secrets": secret_checkpoint,
            "teardown": teardown_checkpoint,
        },
    }
    if reason in {"timeout", "memory", "pids", "max_steps", "browser_closed", "cdp_unreachable"}:
        record["failure_reason"] = reason
    validate_job_record(record)

    trace = _trace_rows(
        context=context,
        request=request,
        result=task_result,
        task_ok=task_ok,
        host=host_checkpoint,
        where=where,
        isolation=isolation_checkpoint,
        secrets=secret_checkpoint,
        teardown=teardown_checkpoint,
    )
    return record, trace


def _trace_rows(
    *,
    context: dict[str, Any],
    request: dict[str, Any],
    result: dict[str, Any],
    task_ok: bool,
    host: dict[str, Any],
    where: dict[str, Any],
    isolation: dict[str, Any],
    secrets: dict[str, Any],
    teardown: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    checkpoints = (
        ("task", request, result, "parsed" if task_ok else "failed"),
        ("host", {"proof_checkpoint": "host"}, host, "verified" if host["ok"] else "failed"),
        ("where", {"proof_checkpoint": "where"}, where, "verified" if where["ok"] else "failed"),
        (
            "isolation",
            {"proof_checkpoint": "isolation"},
            isolation,
            "blocked" if _all_blocked(isolation) else "failed",
        ),
        (
            "secrets",
            {"proof_checkpoint": "secrets"},
            secrets,
            "verified" if secrets.get("ok") else "failed",
        ),
        (
            "teardown",
            {"proof_checkpoint": "teardown"},
            teardown,
            "verified" if teardown["ok"] else "failed",
        ),
    )
    rows = []
    for index, (checkpoint, requested, executed, status) in enumerate(checkpoints):
        rows.append(
            {
                "step_id": context["step_id"] if index == 0 else f"step:{uuid.uuid4().hex}",
                "run_id": context["run_id"],
                "phase": 5,
                "source_id": context["source_id"],
                "objective_id": context["objective_id"],
                "tdd_path": context["tdd_path"],
                "mode": "D0",
                "observed": {"proof_checkpoint": checkpoint},
                "requested": requested,
                "executed": executed,
                "evaluated": {"status": status, "proof_checkpoint": checkpoint},
                "parent_step_id": None if index == 0 else context["step_id"],
                "value_ids": [],
                "ts": context["started_at"],
                "generated_by": context["generated_by"],
            }
        )
    return tuple(rows)


def _isolation_checkpoint(proof: dict[str, Any] | None) -> dict[str, Any]:
    if not proof:
        return {"probes": [], "not_run": True}
    probes = proof.get("probes", [])
    if not probes:
        return {"probes": [], "not_run": True}
    return {
        "probes": [
            {
                "probe": str(probe.get("probe", "unnamed")),
                "result": "BLOCKED" if probe.get("blocked") else "ALLOWED",
                "detail": {
                    str(key): value
                    for key, value in probe.items()
                    if key not in {"probe", "blocked"}
                    and isinstance(value, (str, int, float, bool))
                },
            }
            for probe in probes
        ]
    }


def _secrets_checkpoint(proof: dict[str, Any] | None) -> dict[str, Any]:
    if not proof:
        return {"ok": False, "not_run": True}
    result = {
        "env_keys_found": int(proof.get("env_keys_found", 0)),
        "files_with_keys": int(proof.get("files_with_keys", 0)),
        "metadata_ip": proof.get("metadata_ip", "ALLOWED"),
        "mesh": proof.get("mesh", "ALLOWED"),
    }
    result["ok"] = (
        result["env_keys_found"] == 0
        and result["files_with_keys"] == 0
        and result["metadata_ip"] == "BLOCKED"
        and result["mesh"] == "BLOCKED"
    )
    return result


def _all_blocked(isolation: dict[str, Any]) -> bool:
    probes = isolation.get("probes", [])
    return bool(probes) and all(item.get("result") == "BLOCKED" for item in probes)


def _job_proof_passes(record: dict[str, Any]) -> bool:
    checks = record["checkpoints"]
    return bool(
        checks["host"]["ok"]
        and checks["task"]["ok"]
        and checks["where"]["ok"]
        and checks["isolation"].get("probes")
        and all(item["result"] == "BLOCKED" for item in checks["isolation"]["probes"])
        and checks["secrets"].get("ok")
        and checks["teardown"]["ok"]
    )


def _failed_after_result(
    result: ParseResult,
    *,
    reason: str,
    context: dict[str, Any],
    bronze_key: str,
    kind: str,
    format: str,
    max_rows: int,
    max_bytes: int,
    limits: SandboxLimits,
) -> SandboxParseError:
    record, trace = _job_and_trace(
        ParseExecution(
            output=None,
            host=_host_from_job(result.job_record),
            pod=None,
            isolation=None,
            secrets=None,
            teardown=result.job_record["checkpoints"]["teardown"]["detail"],
            error=reason,
        ),
        context=context,
        task_ok=False,
        reason=reason,
        request={
            "tool": "file.parse",
            "bronze_key": bronze_key,
            "format": format,
            "kind": kind,
            "max_rows": max_rows,
            "max_bytes": max_bytes,
        },
        result={"bronze_key": bronze_key, "format": format, "row_count": 0},
        limits=limits,
    )
    return SandboxParseError(reason, job_record=record, trace=trace)


def _host_from_job(record: dict[str, Any]) -> dict[str, Any] | None:
    host = record["checkpoints"]["host"]
    if host.get("not_run"):
        return None
    return {
        "runtime": host["runtime"],
        "runtime_available": host["ok"],
        "docker_host": host["sandbox_host"],
    }


def _empty_teardown() -> dict[str, Any]:
    return {"pod_gone": True, "proxy_gone": True, "network_removed": True, "verified": True}


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DockerTimeout("parse pod exceeded its wall-clock deadline")
    return remaining


def _pod_failure_reason(
    name: str, result: subprocess.CompletedProcess[str], deadline: float
) -> str | None:
    if result.returncode == 0:
        return None
    state = _docker(
        "inspect",
        "--format",
        "{{.State.OOMKilled}}",
        name,
        check=False,
        timeout=_remaining(deadline),
    )
    if state.returncode == 0 and state.stdout.strip() == "true":
        return "memory"
    detail = (result.stderr or result.stdout).lower()
    if "pids limit" in detail or "resource temporarily unavailable" in detail:
        return "pids"
    return None


def _read_verified(lake: FileLake | S3Lake, key: str) -> bytes:
    if not _BRONZE_KEY.fullmatch(key):
        raise ValueError("bronze_key must be a sha256 bronze object key")
    validate_key(key)
    data = lake.read_key(key)
    if not isinstance(data, bytes):
        raise TypeError("lake.read_key must return bytes")
    if "sha256:" + hashlib.sha256(data).hexdigest() != key:
        raise ValueError("bronze bytes do not match the requested key digest")
    return data


def _bronze_base_url(lake: FileLake | S3Lake, key: str) -> str:
    """Resolve relative HTML links using sanitized bronze metadata, never the raw page."""
    try:
        raw_url = lake.read_metadata(key).get("url", "")
    except Exception:  # noqa: BLE001 - metadata is optional for non-HTML bronze objects
        return ""
    if not isinstance(raw_url, str) or not raw_url or len(raw_url) > 4096:
        return ""
    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _format(value: str) -> str:
    kind = value.lower() if isinstance(value, str) else ""
    if kind not in _SUPPORTED_FORMATS:
        raise ValueError(f"unsupported parse format: {value!r}")
    return kind


def _validate_limits(*, max_rows: int, max_bytes: int) -> None:
    if type(max_rows) is not int or not 1 <= max_rows <= _MAX_ROWS:
        raise ValueError(f"max_rows must be between 1 and {_MAX_ROWS}")
    if type(max_bytes) is not int or not 1 <= max_bytes <= _MAX_INPUT_BYTES:
        raise ValueError(f"max_bytes must be between 1 and {_MAX_INPUT_BYTES}")


def _parse_limits(value: SandboxLimits | Mapping[str, object] | None) -> SandboxLimits:
    if value is None:
        return SandboxLimits(memory_mb=512, cpus=1, pids=64, timeout_s=30, max_steps=1)
    budget = SandboxLimits.from_value(value)
    if budget.max_steps < 1:
        raise ValueError("parse pod requires one step")
    return budget


def _job_context(
    *,
    run_id: str | None,
    source_id: str,
    step_id: str | None,
    objective_id: str | None,
    tdd_path: str,
    generated_by: Mapping[str, str] | None,
) -> dict[str, Any]:
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("source_id must be nonempty")
    if not isinstance(tdd_path, str) or not tdd_path.strip():
        raise ValueError("tdd_path must be nonempty")
    timestamp = datetime.now(UTC).isoformat()
    provenance = dict(
        generated_by or {"backend": "recorded", "model": "deterministic-parse-pod", "at": timestamp}
    )
    if set(provenance) != {"backend", "model", "at"}:
        raise ValueError("generated_by must contain backend, model and at")
    if provenance.get("backend") not in {"recorded", "vultr", "jev"} or not provenance.get("model"):
        raise ValueError("generated_by needs backend=recorded|vultr|jev and a model")
    try:
        at = datetime.fromisoformat(provenance["at"])
    except (TypeError, ValueError) as exc:
        raise ValueError("generated_by.at must be ISO-8601") from exc
    if at.tzinfo is None:
        raise ValueError("generated_by.at must include a timezone")
    job_suffix = uuid.uuid4().hex
    return {
        "job_id": f"job:{job_suffix}",
        "run_id": run_id or f"mock-parse-{job_suffix}",
        "step_id": step_id or f"step:{job_suffix}",
        "source_id": source_id,
        "objective_id": objective_id,
        "tdd_path": tdd_path,
        "started_at": timestamp,
        "generated_by": provenance,
    }
