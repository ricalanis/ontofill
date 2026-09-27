"""Run a disposable Playwright pod behind a TDD domain allowlist proxy."""

from __future__ import annotations

import base64
import hashlib
import io
import ipaddress
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

from ontofill.lake import FileLake, S3Lake
from ontofill.sandbox.domains import registrable_domain, same_registrable_domain
from ontofill.sandbox.limits import SandboxLimits

_DOMAIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\Z")
_IMAGE_LOCK = threading.Lock()
_REPO_ROOT = Path(__file__).resolve().parents[3]
_REMOTE_OUTPUT_LIMIT = 32 * 1024 * 1024
_REMOTE_OUTPUT_NAME = re.compile(
    r"(?:result\.json|page\.html|a11y\.txt|screenshot\.png|page-\d{4}\.(?:html|bin)|robots-\d{4}\.txt)\Z"
)
_REMOTE_OUTPUT_SCRIPT = """\
import base64, io, pathlib, re, sys, zipfile
root = pathlib.Path('/out')
allowed = re.compile(r'(?:result\\.json|page\\.html|a11y\\.txt|screenshot\\.png|page-\\d{4}\\.(?:html|bin)|robots-\\d{4}\\.txt)\\Z')
buffer = io.BytesIO()
with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(root.iterdir()):
        if path.is_file() and not path.is_symlink() and allowed.fullmatch(path.name):
            archive.write(path, path.name)
sys.stdout.write(base64.b64encode(buffer.getvalue()).decode('ascii'))
"""


class CaptureError(RuntimeError):
    """Browser or proxy execution failed."""

    def __init__(
        self,
        message: str,
        trace: list[dict] | None = None,
        result: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.trace = trace or []
        self.result = result


class CaptureBlocked(CaptureError):
    """A URL was outside the TDD allowlist before browser execution."""

    def __init__(self, message: str, trace: list[dict], result: dict | None = None) -> None:
        super().__init__(message, trace, result)
        self.trace = trace


class DockerTimeout(CaptureError):
    """The Docker CLI exceeded an enforced controller deadline."""


class SandboxLimitExceeded(CaptureError):
    """A pod exceeded one of its resource or action budgets."""

    def __init__(self, reason: str, trace: list[dict] | None = None) -> None:
        if reason not in {"timeout", "memory", "pids", "max_steps"}:
            raise ValueError("unknown sandbox limit reason")
        super().__init__(f"sandbox limit exceeded: {reason}", trace)
        self.reason = reason
        self.result: dict | None = None


def _docker(
    *args: str,
    timeout: int = 120,
    check: bool = True,
    input_text: str | None = None,
    input_stream: BinaryIO | None = None,
) -> subprocess.CompletedProcess[str]:
    if input_text is not None and input_stream is not None:
        raise ValueError("Docker input_text and input_stream are mutually exclusive")
    env = os.environ.copy()
    target = env.get("ONTOFILL_SANDBOX_DOCKER_HOST")
    if target:
        if not target.startswith("ssh://"):
            raise CaptureError("ONTOFILL_SANDBOX_DOCKER_HOST must use ssh://")
        env["DOCKER_HOST"] = target
    try:
        run_options = {
            "input": input_text,
            "capture_output": True,
            "text": True,
            "timeout": timeout,
            "env": env,
        }
        if input_stream is not None:
            run_options.pop("input")
            run_options["stdin"] = input_stream
        result = subprocess.run(["docker", *args], check=False, **run_options)
    except subprocess.TimeoutExpired as exc:
        raise DockerTimeout(f"Docker {args[0]} exceeded its deadline") from exc
    except OSError as exc:
        raise CaptureError(f"Docker command failed: {args[0]}") from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1500:]
        raise CaptureError(f"Docker {args[0]} failed: {detail}")
    return result


def _container_network_ip(name: str, network: str) -> str:
    """Resolve a proxy's per-job bridge IP for gVisor's explicit hosts entry."""
    networks = json.loads(
        _docker("inspect", "--format", "{{json .NetworkSettings.Networks}}", name).stdout
    )
    address = networks.get(network, {}).get("IPAddress", "")
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError as exc:
        raise CaptureError("egress proxy has no per-job IPv4 address") from exc
    if not isinstance(parsed, ipaddress.IPv4Address) or not parsed.is_private:
        raise CaptureError("egress proxy has no private per-job IPv4 address")
    return address


def _images() -> tuple[str, str]:
    assets = Path(os.environ.get("ONTOFILL_SANDBOX_ASSETS", _REPO_ROOT / "sandbox"))

    def image_name(directory: str, override: str, base_name: str) -> str:
        if os.environ.get(override):
            return os.environ[override]
        asset_dir = assets / directory
        digest = hashlib.sha256(
            b"".join(path.read_bytes() for path in sorted(asset_dir.iterdir()) if path.is_file())
        ).hexdigest()[:12]
        return f"{base_name}:{digest}"

    agent = image_name("agent-pod", "ONTOFILL_AGENT_IMAGE", "ontofill-agent-pod")
    egress = image_name("egress", "ONTOFILL_EGRESS_IMAGE", "ontofill-egress")
    with _IMAGE_LOCK:
        for image, directory in ((egress, "egress"), (agent, "agent-pod")):
            if _docker("image", "inspect", image, check=False).returncode:
                _docker("build", "-t", image, str(assets / directory), timeout=900)
    return agent, egress


def _allowed_host(url: str, domains: list[str]) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _domains(allowed_domains: list[str]) -> list[str]:
    normalized = sorted({domain.lower().rstrip(".") for domain in allowed_domains})
    if not normalized or any(
        not _DOMAIN.fullmatch(domain) or ".." in domain for domain in normalized
    ):
        raise ValueError("allowed_domains must contain plain DNS names")
    return normalized


def _mesh_probe_ip() -> str:
    value = os.environ.get("ONTOFILL_CONTROL_NETBIRD_IP", "100.64.0.1")
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("ONTOFILL_CONTROL_NETBIRD_IP must be a NetBird IPv4 address") from exc
    if address not in ipaddress.ip_network("100.64.0.0/10"):
        raise ValueError("ONTOFILL_CONTROL_NETBIRD_IP must be within 100.64.0.0/10")
    return value


def _trace(
    *,
    step_id: str,
    run_id: str,
    phase: int,
    source_id: str,
    objective_id: str | None,
    tdd_path: str,
    observed: dict,
    requested: dict,
    executed: dict,
    evaluated: dict,
    ts: str,
    mode: str = "S1",
    generated_by: dict | None = None,
    parent_step_id: str | None = None,
    event: str | None = None,
) -> dict:
    row = {
        "step_id": step_id,
        "run_id": run_id,
        "phase": phase,
        "source_id": source_id,
        "objective_id": objective_id,
        "tdd_path": tdd_path,
        "mode": mode,
        "observed": observed,
        "requested": requested,
        "executed": executed,
        "evaluated": evaluated,
        "parent_step_id": parent_step_id,
        "value_ids": [],
        "ts": ts,
        "generated_by": generated_by or _provenance(None),
    }
    if event is not None:
        row["event"] = event
    return row


def _provenance(generated_by: dict | None) -> dict:
    if generated_by is None:
        return {"backend": "recorded", "model": "sandbox-test", "at": datetime.now(UTC).isoformat()}
    if set(generated_by) != {"backend", "model", "at"}:
        raise ValueError("generated_by must contain only backend, model and at")
    if generated_by.get("backend") not in {"recorded", "vultr"} or not generated_by.get("model"):
        raise ValueError("generated_by needs backend=recorded|vultr and model")
    try:
        at = datetime.fromisoformat(generated_by["at"])
    except (TypeError, ValueError) as exc:
        raise ValueError("generated_by needs an ISO-8601 at timestamp") from exc
    if at.tzinfo is None:
        raise ValueError("generated_by.at needs a timezone")
    return dict(generated_by)


def _host_check() -> tuple[dict, list[str]]:
    info = json.loads(_docker("info", "--format", "{{json .}}").stdout)
    selected = os.environ.get("ONTOFILL_SANDBOX_RUNTIME") or ("runsc" if _remote_docker() else None)
    runtime = selected or info.get("DefaultRuntime", "unknown")
    host = {
        "docker_host": info.get("Name", "unknown"),
        "operating_system": info.get("OperatingSystem", "unknown"),
        "architecture": info.get("Architecture", "unknown"),
        "runtime": runtime,
        "runtime_available": runtime in info.get("Runtimes", {}),
    }
    if selected and not host["runtime_available"]:
        raise CaptureError(f"requested sandbox runtime {selected} is unavailable")
    return host, (["--runtime", selected] if selected else [])


def _denied_probe_host(domains: list[str]) -> str:
    for suffix in ("invalid", "test", uuid.uuid4().hex):
        host = f"ontofill-proof-denied-{uuid.uuid4().hex[:8]}.{suffix}"
        if not _allowed_host("http://" + host, domains):
            return host
    raise CaptureError("could not select a non-allowlisted proof domain")


def _isolation_proof(probes: dict, events: list[dict]) -> dict:
    network = probes.get("network", {})
    writes = probes.get("writes", {})
    proxy_blocked = any(
        event.get("decision") == "block" and event.get("host") == network.get("host")
        for event in events
    )
    return {
        "network": {**network, "proxy_logged_block": proxy_blocked},
        "write_outside_pod": writes.get("outside_pod", {}),
        "write_outside_writable_mount": writes.get("outside_writable_mount", {}),
        "blocked": bool(
            network.get("blocked")
            and proxy_blocked
            and writes.get("outside_pod", {}).get("blocked")
            and writes.get("outside_writable_mount", {}).get("blocked")
        ),
    }


def _secret_proof(probes: dict) -> dict:
    proof = {
        "env_keys_found": probes["env_keys_found"],
        "files_with_keys": probes["files_with_keys"],
        "metadata_ip": probes["metadata_ip"],
        "mesh": probes["mesh"],
    }
    proof["ok"] = (
        proof["env_keys_found"] == 0
        and proof["files_with_keys"] == 0
        and proof["metadata_ip"] == "BLOCKED"
        and proof["mesh"] == "BLOCKED"
    )
    return proof


def _proof_rows(
    *,
    step_id: str,
    run_id: str,
    phase: int,
    source_id: str,
    objective_id: str | None,
    tdd_path: str,
    mode: str,
    generated_by: dict,
    host: dict,
    pod: dict,
    isolation: dict,
    secrets: dict,
) -> list[dict]:
    details = (
        ("host_check", host),
        ("pod_identity", pod),
        ("isolation_probe", isolation),
        ("secrets", secrets),
    )
    return [
        _trace(
            step_id=f"step:{uuid.uuid4().hex}",
            run_id=run_id,
            phase=phase,
            source_id=source_id,
            objective_id=objective_id,
            tdd_path=tdd_path,
            observed={"checkpoint": checkpoint},
            requested={"proof_checkpoint": checkpoint},
            executed=detail,
            evaluated={
                "proof_checkpoint": checkpoint,
                "status": "verified"
                if checkpoint not in {"isolation_probe", "secrets"}
                else ("blocked" if detail.get("blocked", detail.get("ok")) else "failed"),
            },
            ts=datetime.now(UTC).isoformat(),
            mode=mode,
            generated_by=generated_by,
            parent_step_id=step_id,
        )
        for checkpoint, detail in details
    ]


def _cleanup_and_verify(proxy_name: str, pod_name: str, network: str) -> dict:
    _docker("rm", "-f", pod_name, check=False)
    _docker("rm", "-f", proxy_name, check=False)
    _docker("network", "rm", network, check=False)
    result = {
        "pod_gone": _docker("inspect", pod_name, check=False).returncode != 0,
        "proxy_gone": _docker("inspect", proxy_name, check=False).returncode != 0,
        "network_removed": _docker("network", "inspect", network, check=False).returncode != 0,
    }
    result["verified"] = all(result.values())
    return result


def _teardown_row(
    *,
    parent_step_id: str,
    run_id: str,
    phase: int,
    source_id: str,
    objective_id: str | None,
    tdd_path: str,
    mode: str,
    generated_by: dict,
    teardown: dict,
) -> dict:
    return _trace(
        step_id=f"step:{uuid.uuid4().hex}",
        run_id=run_id,
        phase=phase,
        source_id=source_id,
        objective_id=objective_id,
        tdd_path=tdd_path,
        observed={"checkpoint": "teardown"},
        requested={"pod_and_network_absent": True},
        executed=teardown,
        evaluated={
            "proof_checkpoint": "teardown",
            "status": "verified" if teardown["verified"] else "failed",
        },
        ts=datetime.now(UTC).isoformat(),
        mode=mode,
        generated_by=generated_by,
        parent_step_id=parent_step_id,
    )


def _limit_row(
    *,
    parent_step_id: str,
    run_id: str,
    phase: int,
    source_id: str,
    objective_id: str | None,
    tdd_path: str,
    mode: str,
    generated_by: dict,
    reason: str,
) -> dict:
    return _trace(
        step_id=f"step:{uuid.uuid4().hex}",
        run_id=run_id,
        phase=phase,
        source_id=source_id,
        objective_id=objective_id,
        tdd_path=tdd_path,
        observed={"limit": reason},
        requested={"enforce_limit": reason},
        executed={"pod_stop_requested": True},
        evaluated={"status": "hard_stop", "reason": reason},
        ts=datetime.now(UTC).isoformat(),
        mode=mode,
        generated_by=generated_by,
        parent_step_id=parent_step_id,
        event="limit_kill",
    )


def _wait_proxy(name: str) -> None:
    probe = "import socket; socket.create_connection(('127.0.0.1', 8888), 1).close()"
    for _ in range(40):
        if not _docker("exec", name, "python", "-c", probe, check=False, timeout=5).returncode:
            return
        time.sleep(0.25)
    raise CaptureError("egress proxy did not become ready")


def _events(name: str) -> list[dict]:
    result = _docker("logs", name, check=False)
    events = []
    for line in result.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _remote_docker() -> bool:
    target = os.environ.get("ONTOFILL_SANDBOX_DOCKER_HOST") or os.environ.get("DOCKER_HOST", "")
    return target.startswith("ssh://")


def _pod_output_args(output: Path) -> tuple[str, ...]:
    if _remote_docker():
        return ("--tmpfs", "/out:rw,nosuid,size=512m,mode=1777")
    return ("-v", f"{output}:/out:rw")


def _copy_remote_output(name: str, output: Path, timeout: int) -> None:
    """Read runsc tmpfs through docker exec; the daemon cannot docker-cp it."""
    encoded = _docker(
        "exec", name, "python", "-c", _REMOTE_OUTPUT_SCRIPT, timeout=timeout
    ).stdout.strip()
    try:
        archive_bytes = base64.b64decode(encoded, validate=True)
        if len(archive_bytes) > _REMOTE_OUTPUT_LIMIT:
            raise CaptureError("remote sandbox output archive exceeded its size limit")
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            files = archive.infolist()
            if not files or not any(item.filename == "result.json" for item in files):
                raise CaptureError("remote sandbox returned no result marker")
            if len(files) > 110 or sum(item.file_size for item in files) > _REMOTE_OUTPUT_LIMIT:
                raise CaptureError("remote sandbox output exceeded its size limit")
            for item in files:
                if not _REMOTE_OUTPUT_NAME.fullmatch(item.filename) or item.is_dir():
                    raise CaptureError("remote sandbox returned an invalid output filename")
                (output / item.filename).write_bytes(archive.read(item))
    except (ValueError, zipfile.BadZipFile, OSError) as exc:
        raise CaptureError("remote sandbox output archive was invalid") from exc


def _pod_exit_reason(name: str, result: subprocess.CompletedProcess[str]) -> str | None:
    if result.returncode == 0:
        return None
    state = _docker("inspect", "--format", "{{.State.OOMKilled}}", name, check=False)
    if state.returncode == 0 and state.stdout.strip() == "true":
        return "memory"
    detail = (result.stderr or result.stdout).lower()
    if "pids limit" in detail or "resource temporarily unavailable" in detail:
        return "pids"
    return None


def _spider_settings(options: Mapping[str, object]) -> dict:
    expected = {
        "max_depth",
        "page_cap",
        "delay_seconds",
        "max_redirects",
        "max_redirects_total",
        "max_response_bytes",
    }
    if set(options) != expected:
        raise ValueError("spider options must contain the exact bounded crawl policy")
    if type(options["max_depth"]) is not int or not 0 <= options["max_depth"] <= 2:
        raise ValueError("spider max_depth must be between 0 and 2")
    if type(options["page_cap"]) is not int or not 1 <= options["page_cap"] <= 50:
        raise ValueError("spider page_cap must be between 1 and 50")
    if (
        isinstance(options["delay_seconds"], bool)
        or not isinstance(options["delay_seconds"], (int, float))
        or not 0.25 <= float(options["delay_seconds"]) <= 60
    ):
        raise ValueError("spider delay_seconds must be between 0.25 and 60")
    for key, lower, upper in (("max_redirects", 1, 10), ("max_redirects_total", 1, 250)):
        if type(options[key]) is not int or not lower <= options[key] <= upper:
            raise ValueError(f"spider {key} must be between {lower} and {upper}")
    if (
        type(options["max_response_bytes"]) is not int
        or not 1024 <= options["max_response_bytes"] <= 512 * 1024
    ):
        raise ValueError("spider max_response_bytes must be between 1 KiB and 512 KiB")
    return dict(options)


def _safe_output_file(output: Path, name: object, pattern: re.Pattern) -> Path | None:
    if not isinstance(name, str) or not pattern.fullmatch(name) or Path(name).name != name:
        return None
    path = output / name
    return path if path.is_file() else None


def _persist_spider_output(
    *,
    output: Path,
    result: Mapping,
    lake: FileLake | S3Lake,
    source_id: str,
    objective_id: str | None,
    run_id: str,
    step_id: str,
    job_id: str,
    generated_by: dict,
    redirect_domain: str,
    limits: SandboxLimits,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    pages = result.get("pages")
    robots = result.get("robots")
    if not isinstance(pages, list) or not isinstance(robots, list):
        raise CaptureError("spider pod returned no page and robots manifests")
    page_cap = result.get("crawl", {}).get("page_cap")
    if type(page_cap) is not int or not 1 <= page_cap <= 50 or len(pages) > page_cap:
        raise CaptureError("spider pod exceeded its configured page cap")
    page_rows: list[dict] = []
    page_traces: list[dict] = []
    robot_rows: list[dict] = []
    robot_traces: list[dict] = []
    page_file = re.compile(r"page-\d{4}\.(?:html|bin)\Z")
    robot_file = re.compile(r"robots-\d{4}\.txt\Z")
    for item in robots:
        if not isinstance(item, dict):
            raise CaptureError("spider pod returned an invalid robots record")
        record = dict(item)
        path = _safe_output_file(output, record.get("file_name"), robot_file)
        key = None
        if path is not None:
            key = lake.put_bytes(
                path.read_bytes(),
                {
                    "content_type": "text/plain",
                    "url": record.get("url"),
                    "source_id": source_id,
                    "step_id": step_id,
                    "captured_at": datetime.now(UTC).isoformat(),
                },
            )
        record["bronze_key"] = key
        record.pop("file_name", None)
        robot_rows.append(record)
        trace_id = f"step:{uuid.uuid4().hex}"
        status = record.get("http_status")
        robot_traces.append(
            _trace(
                step_id=trace_id,
                run_id=run_id,
                phase=3,
                source_id=source_id,
                objective_id=objective_id,
                tdd_path=f"03-fanout/surface-map/{source_id}/site-graph.json",
                observed={"url": record.get("url"), "http_status": status},
                requested={"url": record.get("url"), "method": "GET", "resource": "robots.txt"},
                executed={
                    "network_request": status is not None,
                    "bronze_key": key,
                    "job_id": job_id,
                },
                evaluated={
                    "status": "captured" if record.get("decision") == "allow" else "blocked",
                    "reason": record.get("decision"),
                },
                ts=datetime.now(UTC).isoformat(),
                generated_by=generated_by,
                parent_step_id=step_id,
            )
        )
    for item in pages:
        if not isinstance(item, dict) or item.get("method") != "GET":
            raise CaptureError("spider pod returned a non-GET page record")
        record = dict(item)
        requested_url = record.get("requested_url")
        final_url = record.get("final_url")
        chain = record.get("redirect_chain")
        if (
            not isinstance(requested_url, str)
            or not isinstance(final_url, str)
            or not isinstance(chain, list)
            or not chain
            or chain[0] != requested_url
            or chain[-1] != final_url
            or any(
                not isinstance(url, str)
                or not _allowed_host(url, [redirect_domain])
                or not same_registrable_domain(requested_url, url)
                for url in chain
            )
            or not _allowed_host(final_url, [redirect_domain])
            or not same_registrable_domain(requested_url, final_url)
        ):
            raise CaptureBlocked("spider pod returned a page outside the registrable domain", [])
        path = _safe_output_file(output, record.get("file_name"), page_file)
        if path is None:
            continue
        captured_at = datetime.now(UTC).isoformat()
        content_type = str(record.get("content_type") or "application/octet-stream")
        key = lake.put_bytes(
            path.read_bytes(),
            {
                "content_type": content_type,
                "url": final_url,
                "captured_at": captured_at,
                "source_id": source_id,
                "step_id": step_id,
            },
        )
        trace_id = f"step:{uuid.uuid4().hex}"
        record.update(
            url=requested_url,
            final_url=final_url,
            bronze_key=key,
            job_id=job_id,
            trace_step_id=trace_id,
        )
        record.pop("file_name", None)
        page_rows.append(record)
        status = record.get("status")
        page_traces.append(
            _trace(
                step_id=trace_id,
                run_id=run_id,
                phase=3,
                source_id=source_id,
                objective_id=objective_id,
                tdd_path=f"03-fanout/surface-map/{source_id}/site-graph.json",
                observed={
                    "url": final_url,
                    "requested_url": requested_url,
                    "http_status": status,
                    "depth": record.get("depth"),
                    "redirect_chain": chain,
                },
                requested={"url": requested_url, "method": "GET", "depth": record.get("depth")},
                executed={
                    "network_request": True,
                    "bronze_key": key,
                    "content_type": content_type,
                    "job_id": job_id,
                },
                evaluated={
                    "status": "captured"
                    if isinstance(status, int) and 200 <= status < 300
                    else "failed",
                    "reason": record.get("blocked_reason"),
                },
                ts=captured_at,
                generated_by=generated_by,
                parent_step_id=step_id,
            )
        )
    return page_rows, robot_rows, page_traces, robot_traces


def _spider_job_result(
    *,
    output: Path,
    result: Mapping,
    lake: FileLake | S3Lake,
    run_id: str,
    phase: int,
    source_id: str,
    objective_id: str | None,
    tdd_path: str,
    step_id: str,
    job_id: str,
    requested: dict,
    provenance: dict,
    host: dict,
    pod: dict,
    isolation: dict,
    secrets: dict,
    redirect_domain: str,
    limits: SandboxLimits,
    started_at: str,
    pod_steps: int,
    peak_memory_mb: float,
    egress_events: list[dict],
) -> tuple[dict, list[dict]]:
    crawl = result.get("crawl")
    if not isinstance(crawl, Mapping):
        raise CaptureError("spider pod returned no crawl summary")
    page_rows, robot_rows, page_traces, robot_traces = _persist_spider_output(
        output=output,
        result=result,
        lake=lake,
        source_id=source_id,
        objective_id=objective_id,
        run_id=run_id,
        step_id=step_id,
        job_id=job_id,
        generated_by=provenance,
        redirect_domain=redirect_domain,
        limits=limits,
    )
    status = result.get("status")
    status = status if type(status) is int and 0 <= status <= 599 else 0
    summary = dict(crawl)
    root_trace = _trace(
        step_id=step_id,
        run_id=run_id,
        phase=phase,
        source_id=source_id,
        objective_id=objective_id,
        tdd_path=tdd_path,
        observed={
            "seed_url": result.get("url"),
            "http_status": status,
            "crawl": summary,
            "captured_pages": len(page_rows),
        },
        requested=requested,
        executed={
            "network_request": bool(result.get("request_count")),
            "job_id": job_id,
            "page_bronze_keys": [row["bronze_key"] for row in page_rows],
            "robots_bronze_keys": [row["bronze_key"] for row in robot_rows],
        },
        evaluated={
            "status": "captured",
            "reason": summary.get("stop_reason"),
            "proof_checkpoint": "dispatch_result",
        },
        ts=datetime.now(UTC).isoformat(),
        mode="S1",
        generated_by=provenance,
    )
    proof_rows = _proof_rows(
        step_id=step_id,
        run_id=run_id,
        phase=phase,
        source_id=source_id,
        objective_id=objective_id,
        tdd_path=tdd_path,
        mode="S1",
        generated_by=provenance,
        host=host,
        pod=pod,
        isolation=isolation,
        secrets=secrets,
    )
    proof = {
        "dispatch_result": {
            "url": result.get("url"),
            "status": status,
            "job_id": job_id,
            "crawl": summary,
            "pages_captured": len(page_rows),
            "robots_captured": sum(row.get("bronze_key") is not None for row in robot_rows),
        },
        "host_check": host,
        "pod_identity": pod,
        "isolation_probe": isolation,
        "secrets": secrets,
    }
    job = {
        "url": result.get("url"),
        "status": status,
        "redirect_chain": result.get("redirect_chain", [result.get("url")]),
        "trace": [root_trace, *proof_rows],
        "egress_events": egress_events,
        "proof": proof,
        "started_at": started_at,
        "limits": limits.as_dict(),
        "usage": {
            "peak_memory_mb": peak_memory_mb,
            "wall_s": 0,
            "steps": pod_steps,
        },
        "job_id": job_id,
        "crawl": summary,
        "pages": page_rows,
        "robots": robot_rows,
        "edges": result.get("edges", []),
        "page_trace": [*robot_traces, *page_traces],
    }
    return job, job["page_trace"]


def _run_agent_pod(
    name: str, output: Path, *args: str, limits: SandboxLimits | None = None
) -> subprocess.CompletedProcess[str]:
    budget = limits or SandboxLimits()
    if not _remote_docker():
        try:
            result = _docker("run", "--name", name, *args, timeout=budget.timeout_s, check=False)
        except DockerTimeout as exc:
            raise SandboxLimitExceeded("timeout") from exc
        reason = _pod_exit_reason(name, result)
        if reason:
            raise SandboxLimitExceeded(reason)
        return result
    # Bind mount paths are interpreted on the remote daemon. The pod keeps its
    # /out tmpfs mounted until the controller has copied the published result.
    _docker("run", "-d", "--name", name, "-e", "CAPTURE_WAIT_FOR_COPY=1", *args, timeout=90)
    ready = False
    deadline = time.monotonic() + budget.timeout_s
    while time.monotonic() < deadline:
        if not _docker("exec", name, "test", "-f", "/out/result.json", check=False).returncode:
            ready = True
            break
        state = _docker("inspect", "--format", "{{.State.Running}}", name, check=False)
        if state.returncode or state.stdout.strip() != "true":
            break
        time.sleep(0.25)
    if ready:
        _copy_remote_output(name, output, budget.timeout_s)
        _docker("exec", name, "touch", "/out/.copied")
    elif time.monotonic() >= deadline:
        raise SandboxLimitExceeded("timeout")
    try:
        waited = _docker("wait", name, timeout=max(1, int(deadline - time.monotonic())))
    except DockerTimeout as exc:
        raise SandboxLimitExceeded("timeout") from exc
    try:
        exit_code = int(waited.stdout.strip())
    except ValueError as exc:
        raise CaptureError("remote Docker wait returned an invalid exit status") from exc
    logs = _docker("logs", name, check=False)
    if exit_code == 0 and not ready:
        raise CaptureError("remote sandbox returned no result marker")
    result = subprocess.CompletedProcess(["docker", "run"], exit_code, logs.stdout, logs.stderr)
    reason = _pod_exit_reason(name, result)
    if reason:
        raise SandboxLimitExceeded(reason)
    return result


def capture_url(
    url: str,
    *,
    allowed_domains: list[str],
    lake: FileLake | S3Lake,
    run_id: str,
    source_id: str,
    objective_id: str | None,
    tdd_path: str,
    phase: int = 5,
    generated_by: dict | None = None,
    limits: SandboxLimits | Mapping[str, object] | None = None,
    redirect_domain: str | None = None,
    spider_options: Mapping[str, object] | None = None,
    job_id: str | None = None,
) -> dict:
    """Capture a public page or one bounded gVisor spider job."""
    domains = _domains(allowed_domains)
    step_id = f"step:{uuid.uuid4().hex}"
    timestamp = datetime.now(UTC).isoformat()
    provenance = _provenance(generated_by)
    settings = _spider_settings(spider_options) if spider_options is not None else None
    if settings is not None:
        if redirect_domain is None:
            redirect_domain = registrable_domain(urlsplit(url).hostname or "")
        if limits is None:
            limits = SandboxLimits(timeout_s=300, max_steps=300)
        job_id = job_id or f"job:{uuid.uuid4().hex}"
    budget = SandboxLimits.from_value(limits)
    started_monotonic = time.monotonic()
    request = {
        "url": url,
        "allowed_domains": domains,
        "capture": ["site_graph"] if settings is not None else ["html", "a11y", "screenshot"],
        "limits": budget.as_dict(),
    }
    if settings is not None:
        request["spider"] = settings
        request["job_id"] = job_id
    if not _allowed_host(url, domains):
        row = _trace(
            step_id=step_id,
            run_id=run_id,
            phase=phase,
            source_id=source_id,
            objective_id=objective_id,
            tdd_path=tdd_path,
            observed={"url": url},
            requested=request,
            executed={"network_request": False},
            evaluated={"status": "blocked", "reason": "domain_not_allowed"},
            ts=timestamp,
            generated_by=provenance,
        )
        raise CaptureBlocked("URL domain is not allowed by the TDD", [row])
    if redirect_domain is not None:
        redirect_domain = registrable_domain(redirect_domain)
        request["redirect_domain"] = redirect_domain
        if redirect_domain not in domains or not same_registrable_domain(url, redirect_domain):
            row = _trace(
                step_id=step_id,
                run_id=run_id,
                phase=phase,
                source_id=source_id,
                objective_id=objective_id,
                tdd_path=tdd_path,
                observed={"url": url},
                requested=request,
                executed={"network_request": False},
                evaluated={"status": "blocked", "reason": "redirect_policy_invalid"},
                ts=timestamp,
                generated_by=provenance,
            )
            raise CaptureBlocked("redirect domain must be the URL's registrable domain", [row])

    if settings is not None:
        selected_runtime = os.environ.get("ONTOFILL_SANDBOX_RUNTIME")
        if not _remote_docker() and selected_runtime != "runsc":
            raise CaptureError("site spider requires ONTOFILL_SANDBOX_RUNTIME=runsc")
        if selected_runtime is not None and selected_runtime != "runsc":
            raise CaptureError("site spider requires the runsc gVisor runtime")
    agent_image, egress_image = _images()
    host, runtime_args = _host_check()
    if settings is not None and (
        host.get("runtime") != "runsc" or not host.get("runtime_available")
    ):
        raise CaptureError("site spider requires a verified available runsc runtime")
    denied_host = _denied_probe_host(domains)
    suffix = uuid.uuid4().hex[:12]
    network = f"ontofill-sandbox-{suffix}"
    proxy_name = f"ontofill-egress-{suffix}"
    browser_name = f"ontofill-browser-{suffix}"
    trace_rows: list[dict] | None = None
    proof: dict = {}
    job_result: dict | None = None
    capture_error: CaptureError | None = None
    limit_error: SandboxLimitExceeded | None = None
    pod_identity: dict | None = None
    isolation: dict | None = None
    secrets: dict | None = None
    pod_steps = 0
    peak_memory_mb = 0.0
    _docker("network", "create", "--internal", network)
    try:
        _docker(
            "run",
            "-d",
            "--rm",
            "--name",
            proxy_name,
            "--network",
            network,
            "--network-alias",
            "egress",
            "--add-host",
            "host.docker.internal:host-gateway",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "128m",
            "--memory-swap",
            "128m",
            "--cpus",
            "0.25",
            "-e",
            "ALLOWED_DOMAINS=" + ",".join(domains),
            egress_image,
        )
        _docker("network", "connect", "bridge", proxy_name)
        _wait_proxy(proxy_name)
        proxy_ip = _container_network_ip(proxy_name, network)
        with tempfile.TemporaryDirectory(prefix="ontofill-capture-") as temp_dir:
            output = Path(temp_dir)
            output.chmod(0o777)
            browser = _run_agent_pod(
                browser_name,
                output,
                *runtime_args,
                "--network",
                network,
                "--add-host",
                f"egress:{proxy_ip}",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,size=512m",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                *budget.docker_args(),
                "--shm-size",
                "256m",
                *_pod_output_args(output),
                "-e",
                "CAPTURE_URL=" + url,
                "-e",
                "PROXY_URL=http://egress:8888",
                "-e",
                "CAPTURE_MODE=" + ("spider" if settings is not None else "page"),
                "-e",
                "SPIDER_ALLOWED_DOMAIN=" + (redirect_domain or ""),
                "-e",
                "SPIDER_CONFIG=" + json.dumps(settings or {}),
                "-e",
                "PROBE_DENIED_HOST=" + denied_host,
                "-e",
                "PROBE_MESH_IP=" + _mesh_probe_ip(),
                "-e",
                "CAPTURE_MAX_STEPS=" + str(budget.max_steps),
                "-e",
                "XDG_CONFIG_HOME=/tmp/chromium-config",
                "-e",
                "XDG_CACHE_HOME=/tmp/chromium-cache",
                agent_image,
                limits=budget,
            )
            events = _events(proxy_name)
            if browser.returncode:
                detail = (browser.stderr or browser.stdout).strip()[-2000:]
                raise CaptureError(f"browser pod failed: {detail}; egress={events}")
            result = json.loads((output / "result.json").read_text(encoding="utf-8"))
            pod_identity = result.get("pod_identity")
            if pod_identity is not None:
                host["cpu_virtualization_flags"] = pod_identity["cpu_virtualization_flags"]
                host["dev_kvm_present"] = pod_identity["dev_kvm_present"]
            pod_steps = result.get("steps", 0)
            peak_memory_mb = result.get("peak_memory_mb", 0)
            if result.get("isolation_probes"):
                isolation = _isolation_proof(result["isolation_probes"], events)
            if result.get("secret_probes"):
                secrets = _secret_proof(result["secret_probes"])
            if result.get("limit_reason"):
                raise SandboxLimitExceeded(result["limit_reason"])
            assert pod_identity is not None
            assert isolation is not None and secrets is not None
            if not isolation["blocked"]:
                raise CaptureError(f"sandbox isolation proof failed: {isolation}")
            if not secrets["ok"]:
                raise CaptureError("sandbox secret hygiene proof failed")
            if settings is not None:
                job_result, _ = _spider_job_result(
                    output=output,
                    result=result,
                    lake=lake,
                    run_id=run_id,
                    phase=phase,
                    source_id=source_id,
                    objective_id=objective_id,
                    tdd_path=tdd_path,
                    step_id=step_id,
                    job_id=job_id or "",
                    requested=request,
                    provenance=provenance,
                    host=host,
                    pod=pod_identity,
                    isolation=isolation,
                    secrets=secrets,
                    redirect_domain=redirect_domain or "",
                    limits=budget,
                    started_at=timestamp,
                    pod_steps=pod_steps,
                    peak_memory_mb=peak_memory_mb,
                    egress_events=events,
                )
                trace_rows = job_result["trace"]
                proof = job_result["proof"]
            else:
                final_url = result["url"]
                redirect_chain = result.get("redirect_chain")
                if not isinstance(redirect_chain, list) or not all(
                    isinstance(item, str) for item in redirect_chain
                ):
                    redirect_chain = [url]
                elif not redirect_chain or redirect_chain[0] != url:
                    redirect_chain = [url, *redirect_chain]
                navigation_error = result.get("navigation_error")
                invalid_redirect = redirect_domain is not None and any(
                    not _allowed_host(item, domains) or not same_registrable_domain(url, item)
                    for item in [*redirect_chain, final_url]
                )
                if navigation_error or invalid_redirect:
                    blocked = invalid_redirect
                    reason = (
                        "redirect_outside_registrable_domain"
                        if blocked
                        else "sandbox_navigation_error"
                    )
                    status = result.get("status")
                    status = int(status) if isinstance(status, int) else 0
                    captured_at = datetime.now(UTC).isoformat()
                    redirect_result = {
                        "url": final_url,
                        "status": status,
                        "redirect_chain": redirect_chain,
                        "navigation_error": navigation_error,
                        "reason": reason,
                        "egress_events": events,
                    }
                    row = _trace(
                        step_id=step_id,
                        run_id=run_id,
                        phase=phase,
                        source_id=source_id,
                        objective_id=objective_id,
                        tdd_path=tdd_path,
                        observed={"url": final_url, "redirect_chain": redirect_chain},
                        requested=request,
                        executed={"network_request": True},
                        evaluated={
                            "status": "blocked" if blocked else "failed",
                            "reason": reason,
                            "proof_checkpoint": "dispatch_result",
                        },
                        ts=captured_at,
                        generated_by=provenance,
                        event="hard_stop" if blocked else None,
                    )
                    trace_rows = [
                        row,
                        *_proof_rows(
                            step_id=step_id,
                            run_id=run_id,
                            phase=phase,
                            source_id=source_id,
                            objective_id=objective_id,
                            tdd_path=tdd_path,
                            mode="S1",
                            generated_by=provenance,
                            host=host,
                            pod=pod_identity,
                            isolation=isolation,
                            secrets=secrets,
                        ),
                    ]
                    proof = {
                        "dispatch_result": redirect_result,
                        "host_check": host,
                        "pod_identity": pod_identity,
                        "isolation_probe": isolation,
                        "secrets": secrets,
                    }
                    job_result = {
                        "url": final_url,
                        "status": status,
                        "redirect_chain": redirect_chain,
                        "trace": trace_rows,
                        "egress_events": events,
                        "proof": proof,
                        "started_at": timestamp,
                        "limits": budget.as_dict(),
                        "usage": {
                            "peak_memory_mb": peak_memory_mb,
                            "wall_s": 0,
                            "steps": pod_steps,
                        },
                    }
                    message = (
                        "redirect left the registrable domain"
                        if blocked
                        else "sandbox navigation failed"
                    )
                    capture_error = CaptureBlocked(message, trace_rows, job_result)
                else:
                    captured_at = datetime.now(UTC).isoformat()
                    metadata = {
                        "url": final_url,
                        "captured_at": captured_at,
                        "source_id": source_id,
                        "step_id": step_id,
                    }
                    html_bytes = (output / "page.html").read_bytes()
                    html_key = lake.put_bytes(html_bytes, {**metadata, "content_type": "text/html"})
                    a11y_key = lake.put_bytes(
                        (output / "a11y.txt").read_bytes(),
                        {**metadata, "content_type": "text/plain"},
                    )
                    screenshot_key = lake.put_bytes(
                        (output / "screenshot.png").read_bytes(),
                        {**metadata, "content_type": "image/png"},
                    )
                    keys = {
                        "html_key": html_key,
                        "a11y_key": a11y_key,
                        "screenshot_key": screenshot_key,
                    }
                    row = _trace(
                        step_id=step_id,
                        run_id=run_id,
                        phase=phase,
                        source_id=source_id,
                        objective_id=objective_id,
                        tdd_path=tdd_path,
                        observed={
                            "url": final_url,
                            "status": result["status"],
                            "redirect_chain": redirect_chain,
                        },
                        requested=request,
                        executed={"network_request": True, **keys},
                        evaluated={
                            "status": "captured",
                            "bronze_objects": 3,
                            "proof_checkpoint": "dispatch_result",
                        },
                        ts=captured_at,
                        generated_by=provenance,
                    )
                    row["screenshot_key"] = screenshot_key
                    trace_rows = [
                        row,
                        *_proof_rows(
                            step_id=step_id,
                            run_id=run_id,
                            phase=phase,
                            source_id=source_id,
                            objective_id=objective_id,
                            tdd_path=tdd_path,
                            mode="S1",
                            generated_by=provenance,
                            host=host,
                            pod=pod_identity,
                            isolation=isolation,
                            secrets=secrets,
                        ),
                    ]
                    proof = {
                        "dispatch_result": {
                            "url": final_url,
                            "status": result["status"],
                            "redirect_chain": redirect_chain,
                            **keys,
                        },
                        "host_check": host,
                        "pod_identity": pod_identity,
                        "isolation_probe": isolation,
                        "secrets": secrets,
                    }
                    job_result = {
                        **keys,
                        "html": html_bytes.decode("utf-8"),
                        "url": final_url,
                        "status": result["status"],
                        "redirect_chain": redirect_chain,
                        "trace": trace_rows,
                        "egress_events": events,
                        "proof": proof,
                        "started_at": timestamp,
                        "limits": budget.as_dict(),
                        "usage": {
                            "peak_memory_mb": result.get("peak_memory_mb", 0),
                            "wall_s": 0,
                            "steps": result["steps"],
                        },
                    }
    except SandboxLimitExceeded as exc:
        limit_error = exc
        trace_rows = exc.trace
        trace_rows.append(
            _limit_row(
                parent_step_id=step_id,
                run_id=run_id,
                phase=phase,
                source_id=source_id,
                objective_id=objective_id,
                tdd_path=tdd_path,
                mode="S1",
                generated_by=provenance,
                reason=exc.reason,
            )
        )
        raise
    finally:
        teardown = _cleanup_and_verify(proxy_name, browser_name, network)
        if trace_rows is not None:
            proof["teardown"] = teardown
            trace_rows.append(
                _teardown_row(
                    parent_step_id=step_id,
                    run_id=run_id,
                    phase=phase,
                    source_id=source_id,
                    objective_id=objective_id,
                    tdd_path=tdd_path,
                    mode="S1",
                    generated_by=provenance,
                    teardown=teardown,
                )
            )
        if job_result is not None:
            job_result["proof"] = proof
            job_result["trace"] = trace_rows
            job_result["ended_at"] = datetime.now(UTC).isoformat()
            job_result["usage"]["wall_s"] = round(time.monotonic() - started_monotonic, 3)
        if limit_error is not None:
            limit_error.result = {
                "trace": trace_rows,
                "requested": request,
                "proof": {
                    "host_check": host,
                    "pod_identity": pod_identity,
                    "isolation_probe": isolation,
                    "secrets": secrets,
                    "teardown": teardown,
                },
                "started_at": timestamp,
                "ended_at": datetime.now(UTC).isoformat(),
                "limits": budget.as_dict(),
                "usage": {
                    "peak_memory_mb": peak_memory_mb,
                    "wall_s": round(time.monotonic() - started_monotonic, 3),
                    "steps": pod_steps,
                },
                "failure_reason": limit_error.reason,
            }
        if not teardown["verified"]:
            raise CaptureError("sandbox teardown proof failed", trace_rows)
    if capture_error is not None:
        capture_error.trace = trace_rows or []
        capture_error.result = job_result
        raise capture_error
    assert job_result is not None
    return job_result


def fetch_url(
    url: str,
    *,
    allowed_domains: list[str],
    lake: FileLake | S3Lake,
    run_id: str,
    source_id: str,
    objective_id: str | None,
    tdd_path: str,
    phase: int = 5,
    generated_by: dict | None = None,
    limits: SandboxLimits | Mapping[str, object] | None = None,
    include_bytes: bool = True,
) -> dict:
    """Fetch a discovered file inside the same contained network and store raw bytes."""
    domains = _domains(allowed_domains)
    step_id = f"step:{uuid.uuid4().hex}"
    timestamp = datetime.now(UTC).isoformat()
    provenance = _provenance(generated_by)
    budget = SandboxLimits.from_value(limits)
    started_monotonic = time.monotonic()
    request = {"url": url, "allowed_domains": domains, "fetch": "bytes", "limits": budget.as_dict()}
    if not _allowed_host(url, domains):
        row = _trace(
            step_id=step_id,
            run_id=run_id,
            phase=phase,
            source_id=source_id,
            objective_id=objective_id,
            tdd_path=tdd_path,
            observed={"url": url},
            requested=request,
            executed={"network_request": False},
            evaluated={"status": "blocked", "reason": "domain_not_allowed"},
            ts=timestamp,
            mode="D0",
            generated_by=provenance,
        )
        raise CaptureBlocked("URL domain is not allowed by the TDD", [row])

    agent_image, egress_image = _images()
    host, runtime_args = _host_check()
    denied_host = _denied_probe_host(domains)
    suffix = uuid.uuid4().hex[:12]
    network = f"ontofill-sandbox-{suffix}"
    proxy_name = f"ontofill-egress-{suffix}"
    pod_name = f"ontofill-fetch-{suffix}"
    trace_rows: list[dict] | None = None
    proof: dict = {}
    job_result: dict | None = None
    limit_error: SandboxLimitExceeded | None = None
    pod_identity: dict | None = None
    isolation: dict | None = None
    secrets: dict | None = None
    pod_steps = 0
    peak_memory_mb = 0.0
    _docker("network", "create", "--internal", network)
    try:
        _docker(
            "run",
            "-d",
            "--rm",
            "--name",
            proxy_name,
            "--network",
            network,
            "--network-alias",
            "egress",
            "--add-host",
            "host.docker.internal:host-gateway",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "128m",
            "--memory-swap",
            "128m",
            "--cpus",
            "0.25",
            "-e",
            "ALLOWED_DOMAINS=" + ",".join(domains),
            egress_image,
        )
        _docker("network", "connect", "bridge", proxy_name)
        _wait_proxy(proxy_name)
        with tempfile.TemporaryDirectory(prefix="ontofill-fetch-") as temp_dir:
            output = Path(temp_dir)
            output.chmod(0o777)
            pod = _run_agent_pod(
                pod_name,
                output,
                *runtime_args,
                "--network",
                network,
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,size=512m",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                *budget.docker_args(),
                "--shm-size",
                "256m",
                *_pod_output_args(output),
                "-e",
                "CAPTURE_URL=" + url,
                "-e",
                "PROXY_URL=http://egress:8888",
                "-e",
                "CAPTURE_MODE=fetch",
                "-e",
                "PROBE_DENIED_HOST=" + denied_host,
                "-e",
                "PROBE_MESH_IP=" + _mesh_probe_ip(),
                "-e",
                "CAPTURE_MAX_STEPS=" + str(budget.max_steps),
                agent_image,
                limits=budget,
            )
            events = _events(proxy_name)
            if pod.returncode:
                detail = (pod.stderr or pod.stdout).strip()[-2000:]
                raise CaptureError(f"fetch pod failed: {detail}; egress={events}")
            result = json.loads((output / "result.json").read_text(encoding="utf-8"))
            pod_identity = result.get("pod_identity")
            if pod_identity is not None:
                host["cpu_virtualization_flags"] = pod_identity["cpu_virtualization_flags"]
                host["dev_kvm_present"] = pod_identity["dev_kvm_present"]
            pod_steps = result.get("steps", 0)
            peak_memory_mb = result.get("peak_memory_mb", 0)
            if result.get("isolation_probes"):
                isolation = _isolation_proof(result["isolation_probes"], events)
            if result.get("secret_probes"):
                secrets = _secret_proof(result["secret_probes"])
            if result.get("limit_reason"):
                raise SandboxLimitExceeded(result["limit_reason"])
            assert pod_identity is not None
            assert isolation is not None and secrets is not None
            if not isolation["blocked"]:
                raise CaptureError(f"sandbox isolation proof failed: {isolation}")
            if not secrets["ok"]:
                raise CaptureError("sandbox secret hygiene proof failed")
            final_url = result["url"]
            if not _allowed_host(final_url, domains):
                raise CaptureError("fetch redirected outside the TDD allowlist")
            content = (output / "payload.bin").read_bytes()
            captured_at = datetime.now(UTC).isoformat()
            bronze_key = lake.put_bytes(
                content,
                {
                    "content_type": result["content_type"],
                    "url": final_url,
                    "captured_at": captured_at,
                    "source_id": source_id,
                    "step_id": step_id,
                },
            )
            row = _trace(
                step_id=step_id,
                run_id=run_id,
                phase=phase,
                source_id=source_id,
                objective_id=objective_id,
                tdd_path=tdd_path,
                observed={"url": final_url, "status": result["status"]},
                requested=request,
                executed={"network_request": True, "bronze_key": bronze_key},
                evaluated={
                    "status": "captured",
                    "bronze_objects": 1,
                    "proof_checkpoint": "dispatch_result",
                },
                ts=captured_at,
                mode="D0",
                generated_by=provenance,
            )
            trace_rows = [
                row,
                *_proof_rows(
                    step_id=step_id,
                    run_id=run_id,
                    phase=phase,
                    source_id=source_id,
                    objective_id=objective_id,
                    tdd_path=tdd_path,
                    mode="D0",
                    generated_by=provenance,
                    host=host,
                    pod=pod_identity,
                    isolation=isolation,
                    secrets=secrets,
                ),
            ]
            proof = {
                "dispatch_result": {
                    "url": final_url,
                    "status": result["status"],
                    "bronze_key": bronze_key,
                },
                "host_check": host,
                "pod_identity": pod_identity,
                "isolation_probe": isolation,
                "secrets": secrets,
            }
            job_result = {
                "content_type": result["content_type"],
                "bronze_key": bronze_key,
                "url": final_url,
                "status": result["status"],
                "trace": trace_rows,
                "egress_events": events,
                "proof": proof,
                "started_at": timestamp,
                "limits": budget.as_dict(),
                "usage": {
                    "peak_memory_mb": result.get("peak_memory_mb", 0),
                    "wall_s": 0,
                    "steps": result["steps"],
                },
            }
            if include_bytes:
                job_result["bytes"] = content
            return job_result
    except SandboxLimitExceeded as exc:
        limit_error = exc
        trace_rows = exc.trace
        trace_rows.append(
            _limit_row(
                parent_step_id=step_id,
                run_id=run_id,
                phase=phase,
                source_id=source_id,
                objective_id=objective_id,
                tdd_path=tdd_path,
                mode="D0",
                generated_by=provenance,
                reason=exc.reason,
            )
        )
        raise
    finally:
        teardown = _cleanup_and_verify(proxy_name, pod_name, network)
        if trace_rows is not None:
            proof["teardown"] = teardown
            trace_rows.append(
                _teardown_row(
                    parent_step_id=step_id,
                    run_id=run_id,
                    phase=phase,
                    source_id=source_id,
                    objective_id=objective_id,
                    tdd_path=tdd_path,
                    mode="D0",
                    generated_by=provenance,
                    teardown=teardown,
                )
            )
        if job_result is not None:
            job_result["ended_at"] = datetime.now(UTC).isoformat()
            job_result["usage"]["wall_s"] = round(time.monotonic() - started_monotonic, 3)
        if limit_error is not None:
            limit_error.result = {
                "trace": trace_rows,
                "requested": request,
                "proof": {
                    "host_check": host,
                    "pod_identity": pod_identity,
                    "isolation_probe": isolation,
                    "secrets": secrets,
                    "teardown": teardown,
                },
                "started_at": timestamp,
                "ended_at": datetime.now(UTC).isoformat(),
                "limits": budget.as_dict(),
                "usage": {
                    "peak_memory_mb": peak_memory_mb,
                    "wall_s": round(time.monotonic() - started_monotonic, 3),
                    "steps": pod_steps,
                },
                "failure_reason": limit_error.reason,
            }
        if not teardown["verified"]:
            raise CaptureError("sandbox teardown proof failed", trace_rows)
