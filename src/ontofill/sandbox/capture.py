"""Run a disposable Playwright pod behind a TDD domain allowlist proxy."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from ontofill.lake import FileLake, S3Lake
from ontofill.sandbox.limits import SandboxLimits

_DOMAIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\Z")
_IMAGE_LOCK = threading.Lock()
_REPO_ROOT = Path(__file__).resolve().parents[3]


class CaptureError(RuntimeError):
    """Browser or proxy execution failed."""

    def __init__(self, message: str, trace: list[dict] | None = None) -> None:
        super().__init__(message)
        self.trace = trace or []


class CaptureBlocked(CaptureError):
    """A URL was outside the TDD allowlist before browser execution."""

    def __init__(self, message: str, trace: list[dict]) -> None:
        super().__init__(message)
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
    *args: str, timeout: int = 120, check: bool = True, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    target = env.get("ONTOFILL_SANDBOX_DOCKER_HOST")
    if target:
        if not target.startswith("ssh://"):
            raise CaptureError("ONTOFILL_SANDBOX_DOCKER_HOST must use ssh://")
        env["DOCKER_HOST"] = target
    try:
        result = subprocess.run(
            ["docker", *args],
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
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
        _docker("cp", f"{name}:/out/.", str(output), timeout=budget.timeout_s)
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
) -> dict:
    """Capture a public page; only the proxy container can leave the internal network."""
    domains = _domains(allowed_domains)
    step_id = f"step:{uuid.uuid4().hex}"
    timestamp = datetime.now(UTC).isoformat()
    provenance = _provenance(generated_by)
    budget = SandboxLimits.from_value(limits)
    started_monotonic = time.monotonic()
    request = {
        "url": url,
        "allowed_domains": domains,
        "capture": ["html", "a11y", "screenshot"],
        "limits": budget.as_dict(),
    }
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

    agent_image, egress_image = _images()
    host, runtime_args = _host_check()
    denied_host = _denied_probe_host(domains)
    suffix = uuid.uuid4().hex[:12]
    network = f"ontofill-sandbox-{suffix}"
    proxy_name = f"ontofill-egress-{suffix}"
    browser_name = f"ontofill-browser-{suffix}"
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
            final_url = result["url"]
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
            keys = {"html_key": html_key, "a11y_key": a11y_key, "screenshot_key": screenshot_key}
            row = _trace(
                step_id=step_id,
                run_id=run_id,
                phase=phase,
                source_id=source_id,
                objective_id=objective_id,
                tdd_path=tdd_path,
                observed={"url": final_url, "status": result["status"]},
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
                "dispatch_result": {"url": final_url, "status": result["status"], **keys},
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
                "bytes": content,
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
