"""Disposable, one-session Chromium cells on the sandbox Docker host."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

from ontofill.lake import FileLake, S3Lake
from ontofill.sandbox.capture import (
    _denied_probe_host,
    _docker,
    _domains,
    _events,
    _images,
    _isolation_proof,
    _mesh_probe_ip,
    _secret_proof,
    _trace,
    _wait_proxy,
)
from ontofill.sandbox.jobs import append_job_record, validate_job_record
from ontofill.sandbox.limits import SandboxLimits


class CellError(RuntimeError):
    """A browser cell could not meet its isolation or lifecycle contract."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _remote_ssh_target() -> str | None:
    target = os.environ.get("ONTOFILL_SANDBOX_DOCKER_HOST", "")
    if not target:
        return None
    parsed = urlsplit(target)
    if parsed.scheme != "ssh" or not parsed.hostname or parsed.path not in {"", "/"}:
        raise CellError("remote Docker must use ssh://[user@]host")
    if parsed.password or parsed.query or parsed.fragment:
        raise CellError("remote Docker SSH URL must not contain credentials or options")
    return (parsed.username + "@" if parsed.username else "") + parsed.hostname


def _cdp_url(port: int) -> str:
    with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=5) as response:
        payload = json.load(response)
    browser_url = payload.get("webSocketDebuggerUrl")
    parsed = urlsplit(browser_url or "")
    if parsed.scheme not in {"ws", "wss"} or not parsed.path.startswith("/devtools/browser/"):
        raise CellError("Chromium did not expose a browser CDP WebSocket")
    return urlunsplit((parsed.scheme, f"127.0.0.1:{port}", parsed.path, "", ""))


def _wait_preflight(name: str) -> dict:
    for _ in range(60):
        result = _docker("exec", name, "cat", "/out/preflight.json", check=False, timeout=5)
        if result.returncode == 0:
            return json.loads(result.stdout)
        time.sleep(0.25)
    raise CellError("hands pod did not produce a preflight proof")


def _wait_cdp(port: int) -> str:
    for _ in range(60):
        try:
            return _cdp_url(port)
        except (OSError, ValueError, CellError):
            time.sleep(0.25)
    raise CellError("hands CDP endpoint did not become ready")


def _published_port(name: str) -> int:
    result = _docker("port", name, "9222/tcp")
    address = result.stdout.strip()
    if not address.startswith("127.0.0.1:") or "\n" in address:
        raise CellError("hands CDP port is not bound exclusively to Docker-host loopback")
    try:
        return int(address.rsplit(":", 1)[1])
    except ValueError as exc:
        raise CellError("invalid Docker CDP port mapping") from exc


def _open_tunnel(remote_port: int) -> tuple[int, subprocess.Popen[bytes] | None]:
    target = _remote_ssh_target()
    if target is None:
        return remote_port, None
    local_port = _loopback_port()
    command = [
        "ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
        "-o", "ConnectTimeout=5", "-L",
        f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}", target,
    ]
    try:
        tunnel = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise CellError("could not start control-plane CDP tunnel") from exc
    return local_port, tunnel


@dataclass
class _Cell:
    cell_id: str
    backend: str
    domains: list[str]
    limits: SandboxLimits
    network: str
    proxy: str
    hands: str
    started_at: str
    started_monotonic: float
    host: dict = field(default_factory=dict)
    pod: dict = field(default_factory=dict)
    isolation: dict = field(default_factory=dict)
    secrets: dict = field(default_factory=dict)
    cdp_url: str | None = None
    tunnel: subprocess.Popen[bytes] | None = None
    timer: threading.Timer | None = None
    steps: int = 0
    peak_memory_mb: float = 0.0
    task_result: dict | None = None
    task_ok: bool = False
    failure_reason: str | None = None
    state: str = "creating"
    job_record: dict | None = None


class CellManager:
    """Create one gVisor hands pod and egress proxy per native browser session.

    The optional lake/run context writes a jobs.jsonl proof when the cell closes.
    The controller must call record_step for every browser action and report_task_result
    before destroy; otherwise the task checkpoint is honestly marked failed.
    """

    def __init__(
        self,
        *,
        lake: FileLake | S3Lake | None = None,
        case_id: str | None = None,
        run_id: str | None = None,
        source_id: str = "source:browser-cell",
        tdd_path: str | None = None,
        generated_by: dict | None = None,
        on_trace: Callable[[dict], None] | None = None,
    ) -> None:
        if lake is not None and (not case_id or not run_id):
            raise ValueError("lake requires case_id and run_id")
        self.lake = lake
        self.case_id = case_id
        self.run_id = run_id
        self.source_id = source_id
        self.tdd_path = tdd_path
        self.generated_by = generated_by or {
            "backend": "recorded", "model": "cell-substrate", "at": _now(),
        }
        self.on_trace = on_trace
        self._cells: dict[str, _Cell] = {}
        self._lock = threading.RLock()

    def create(
        self,
        backend: str,
        allowed_domains: list[str],
        limits: SandboxLimits | dict | None = None,
        placement: str = "sandbox_vm",
    ) -> dict:
        if backend not in {"native", "skyvern"}:
            raise ValueError("backend must be native or skyvern")
        if placement not in {"sandbox_vm", "throwaway_vx1"}:
            raise ValueError("placement must be sandbox_vm or throwaway_vx1")
        if placement == "throwaway_vx1":
            raise CellError("throwaway_vx1 is unavailable until Vultr admission is cleared")
        if backend == "skyvern":
            raise CellError("Skyvern cells require a pinned upstream image and session gateway wiring")
        domains = _domains(allowed_domains)
        budget = SandboxLimits.from_value(limits)
        _remote_ssh_target()  # Validate remote target before creating any resource.
        info = json.loads(_docker("info", "--format", "{{json .}}").stdout)
        if "runsc" not in info.get("Runtimes", {}):
            raise CellError("gVisor runsc is required for browser cells")
        agent_image, egress_image = _images()
        suffix = uuid.uuid4().hex[:12]
        cell = _Cell(
            cell_id=f"cell:{suffix}", backend=backend, domains=domains, limits=budget,
            network=f"ontofill-cell-{suffix}", proxy=f"ontofill-cell-egress-{suffix}",
            hands=f"ontofill-cell-hands-{suffix}", started_at=_now(),
            started_monotonic=time.monotonic(),
            host={"docker_host": info.get("Name", "unknown"), "runtime": "runsc", "runtime_available": True},
        )
        with self._lock:
            self._cells[cell.cell_id] = cell
        try:
            _docker("network", "create", "--internal", cell.network)
            _docker(
                "run", "-d", "--name", cell.proxy, "--network", cell.network,
                "--network-alias", "egress", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "64",
                "--memory", "128m", "--memory-swap", "128m", "--cpus", "0.25",
                "-e", "ALLOWED_DOMAINS=" + ",".join(domains), egress_image,
            )
            _docker("network", "connect", "bridge", cell.proxy)
            _wait_proxy(cell.proxy)
            _docker(
                "run", "-d", "--name", cell.hands, "--runtime", "runsc",
                "--network", cell.network, "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=512m",
                "--tmpfs", "/out:rw,nosuid,size=4m,mode=1777", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", *budget.docker_args(),
                "--shm-size", "256m", "-p", "127.0.0.1::9222",
                "-e", "PROBE_DENIED_HOST=" + _denied_probe_host(domains),
                "-e", "PROBE_MESH_IP=" + _mesh_probe_ip(),
                agent_image, "python", "-u", "/app/cdp.py",
            )
            preflight = _wait_preflight(cell.hands)
            cell.pod = preflight["pod_identity"]
            cell.host["cpu_virtualization_flags"] = cell.pod["cpu_virtualization_flags"]
            cell.host["dev_kvm_present"] = cell.pod["dev_kvm_present"]
            cell.isolation = _isolation_proof(preflight["isolation_probes"], _events(cell.proxy))
            cell.secrets = _secret_proof(preflight["secret_probes"])
            cell.peak_memory_mb = float(preflight["peak_memory_mb"])
            if not cell.isolation["blocked"] or not cell.secrets["ok"]:
                raise CellError("hands failed isolation or secret-hygiene preflight")
            for checkpoint, detail in (
                ("host_check", cell.host),
                ("pod_identity", cell.pod),
                ("isolation_probe", cell.isolation),
                ("secrets", cell.secrets),
            ):
                self._emit(cell, checkpoint, detail, ok=True)
            port, cell.tunnel = _open_tunnel(_published_port(cell.hands))
            cell.cdp_url = _wait_cdp(port)
            cell.state = "ready"
            cell.timer = threading.Timer(budget.timeout_s, self._expire, args=(cell.cell_id,))
            cell.timer.daemon = True
            cell.timer.start()
            return {
                "cell_id": cell.cell_id, "cdp_url": cell.cdp_url,
                "brain_url": None, "live_view_port": None,
            }
        except Exception:
            self._destroy(cell, mark_failed=True)
            raise

    def _require(self, cell_id: str) -> _Cell:
        try:
            return self._cells[cell_id]
        except KeyError as exc:
            raise CellError("unknown cell_id") from exc

    def status(self, cell_id: str) -> dict:
        with self._lock:
            cell = self._require(cell_id)
            if cell.state == "ready":
                state_result = _docker("inspect", "--format", "{{json .State}}", cell.hands, check=False)
                if state_result.returncode:
                    cell.failure_reason = "pids"
                    self._destroy(cell, mark_failed=True)
                else:
                    state = json.loads(state_result.stdout)
                    if not state.get("Running"):
                        cell.failure_reason = "memory" if state.get("OOMKilled") else "pids"
                        self._destroy(cell, mark_failed=True)
            return {
                "cell_id": cell.cell_id, "backend": cell.backend, "state": cell.state,
                "steps": cell.steps, "limits": cell.limits.as_dict(),
                "failure_reason": cell.failure_reason, "cdp_url": cell.cdp_url if cell.state == "ready" else None,
                "brain_url": None, "live_view_port": None,
            }

    def record_step(self, cell_id: str) -> int:
        with self._lock:
            cell = self._require(cell_id)
            if cell.state != "ready":
                raise CellError("cell is not ready")
            if cell.steps >= cell.limits.max_steps:
                cell.failure_reason = "max_steps"
                self._destroy(cell, mark_failed=True)
                raise CellError("cell max_steps limit reached")
            cell.steps += 1
            return cell.steps

    def report_task_result(self, cell_id: str, result: dict, *, ok: bool) -> None:
        if not isinstance(result, dict) or not isinstance(ok, bool):
            raise TypeError("result must be an object and ok must be boolean")
        with self._lock:
            cell = self._require(cell_id)
            if cell.state != "ready":
                raise CellError("cell is not ready")
            cell.task_result = result
            cell.task_ok = ok
            self._emit(cell, "dispatch_result", result, ok=ok)

    def destroy(self, cell_id: str) -> dict:
        with self._lock:
            cell = self._require(cell_id)
            if cell.state != "destroyed":
                self._destroy(cell)
            return {"cell_id": cell_id, "state": cell.state, "job_record": cell.job_record}

    def _expire(self, cell_id: str) -> None:
        with self._lock:
            cell = self._cells.get(cell_id)
            if cell and cell.state == "ready":
                cell.failure_reason = "timeout"
                self._destroy(cell, mark_failed=True)

    def _destroy(self, cell: _Cell, *, mark_failed: bool = False) -> None:
        if cell.timer:
            cell.timer.cancel()
        if cell.tunnel:
            cell.tunnel.terminate()
            try:
                cell.tunnel.wait(timeout=2)
            except subprocess.TimeoutExpired:
                cell.tunnel.kill()
        for name in (cell.hands, cell.proxy):
            _docker("rm", "-f", name, check=False)
        _docker("network", "rm", cell.network, check=False)
        teardown = {
            "pod_gone": _docker("inspect", cell.hands, check=False).returncode != 0,
            "proxy_gone": _docker("inspect", cell.proxy, check=False).returncode != 0,
            "network_removed": _docker("network", "inspect", cell.network, check=False).returncode != 0,
        }
        teardown["verified"] = all(teardown.values())
        cell.state = "destroyed" if teardown["verified"] else "teardown_failed"
        cell.cdp_url = None
        if cell.failure_reason:
            self._emit(cell, "limit_kill", {"reason": cell.failure_reason}, ok=False)
        if cell.task_result is None:
            self._emit(cell, "dispatch_result", {"reason": cell.failure_reason or "no_task_result"}, ok=False)
        self._emit(cell, "teardown", teardown, ok=teardown["verified"])
        cell.job_record = self._job_record(cell, teardown, mark_failed=mark_failed)
        if self.lake is not None and self.case_id is not None:
            append_job_record(self.lake, self.case_id, cell.job_record)

    def _emit(self, cell: _Cell, checkpoint: str, detail: dict, *, ok: bool) -> None:
        if self.on_trace is None:
            return
        evaluated = {"proof_checkpoint": checkpoint, "status": "verified" if ok else "failed"}
        event = None
        if checkpoint == "limit_kill":
            evaluated = {"status": "hard_stop", "reason": cell.failure_reason}
            event = "hard_stop"
        row = _trace(
            step_id=f"step:{uuid.uuid4().hex}", run_id=self.run_id or "run:unattached",
            phase=3, source_id=self.source_id, objective_id=None,
            tdd_path=self.tdd_path, observed={"cell_id": cell.cell_id},
            requested={"proof_checkpoint": checkpoint}, executed=detail,
            evaluated=evaluated, ts=_now(), generated_by=self.generated_by,
            parent_step_id="step:" + cell.cell_id.partition(":")[2], event=event,
        )
        self.on_trace(row)

    def _job_record(self, cell: _Cell, teardown: dict, *, mark_failed: bool) -> dict:
        host = (
            {
                "ok": cell.host["runtime_available"], "sandbox_host": cell.host["docker_host"],
                "runtime": cell.host["runtime"],
                "virt": {
                    "cpu_virtualization_flags": cell.host["cpu_virtualization_flags"],
                    "dev_kvm_present": cell.host["dev_kvm_present"],
                },
            }
            if "cpu_virtualization_flags" in cell.host else {"ok": False, "not_run": True}
        )
        where = (
            {"ok": bool(cell.pod.get("hostname")), "hostname": cell.pod["hostname"], "uname": cell.pod["uname"]}
            if cell.pod else {"ok": False, "not_run": True}
        )
        isolation = (
            {
                "probes": [
                    {"probe": name, "result": "BLOCKED" if blocked else "ALLOWED"}
                    for name, blocked in (
                        ("network_non_allowlisted", cell.isolation["network"]["blocked"]
                         and cell.isolation["network"]["proxy_logged_block"]),
                        ("write_outside_pod", cell.isolation["write_outside_pod"]["blocked"]),
                        ("write_outside_writable_mount", cell.isolation["write_outside_writable_mount"]["blocked"]),
                    )
                ]
            }
            if cell.isolation else {"probes": [], "not_run": True}
        )
        wall_s = round(max(0.0, time.monotonic() - cell.started_monotonic), 3)
        record = {
            "job_id": "job:" + cell.cell_id.partition(":")[2],
            "run_id": self.run_id or "run:unattached",
            "step_id": "step:" + cell.cell_id.partition(":")[2],
            "source_id": self.source_id,
            "started_at": cell.started_at, "ended_at": _now(),
            "generated_by": self.generated_by,
            "limits": cell.limits.as_dict(),
            "usage": {"peak_memory_mb": cell.peak_memory_mb, "wall_s": wall_s, "steps": cell.steps},
            "checkpoints": {
                "host": host,
                "task": {
                    "ok": bool(cell.task_ok and not mark_failed),
                    "requested": {"backend": cell.backend, "allowed_domains": cell.domains},
                    "result": cell.task_result or {"reason": cell.failure_reason or "no_task_result"},
                    "value_ids": [],
                },
                "where": where, "isolation": isolation,
                "secrets": cell.secrets or {"ok": False, "not_run": True},
                "teardown": {"ok": teardown["verified"], "detail": teardown},
            },
        }
        if cell.failure_reason:
            record["failure_reason"] = cell.failure_reason
        validate_job_record(record)
        return record
