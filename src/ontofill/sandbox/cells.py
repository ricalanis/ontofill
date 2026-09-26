"""Disposable, one-session Chromium cells on the sandbox Docker host."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shlex
import socket
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

from ontofill.lake import FileLake, S3Lake
from ontofill.sandbox.capture import (
    _container_network_ip,
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


_SKYVERN_IMAGE = re.compile(r"public\.ecr\.aws/skyvern/skyvern@sha256:[0-9a-f]{64}\Z")
_POSTGRES_IMAGE = re.compile(r"postgres:14-alpine@sha256:[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class SkyvernSpec:
    """Pinned upstream images and a single expiring gateway credential."""

    image: str
    postgres_image: str
    gateway_url: str
    gateway_session_token: str = field(repr=False)
    expires_at: str

    @classmethod
    def from_value(
        cls, value: Mapping[str, object] | None, brain_env: Mapping[str, object] | None
    ) -> SkyvernSpec:
        expected_config = {"image", "postgres_image", "expires_at"}
        expected_env = {"OPENAI_COMPATIBLE_API_BASE", "OPENAI_COMPATIBLE_API_KEY"}
        if not isinstance(value, Mapping) or set(value) != expected_config:
            raise CellError("skyvern config requires pinned images and expiry")
        if not isinstance(brain_env, Mapping) or set(brain_env) != expected_env:
            raise CellError(
                "brain_env requires exactly OpenAI-compatible gateway URL and session key"
            )
        try:
            spec = cls(
                **value,
                gateway_url=brain_env["OPENAI_COMPATIBLE_API_BASE"],
                gateway_session_token=brain_env["OPENAI_COMPATIBLE_API_KEY"],
            )
        except TypeError as exc:
            raise CellError("invalid skyvern config") from exc
        if any(not isinstance(getattr(spec, name), str) for name in cls.__dataclass_fields__):
            raise CellError("skyvern config fields must be strings")
        if not _SKYVERN_IMAGE.fullmatch(spec.image):
            raise CellError("Skyvern image must be the upstream sha256-pinned image")
        if not _POSTGRES_IMAGE.fullmatch(spec.postgres_image):
            raise CellError("Postgres image must be sha256-pinned postgres:14-alpine")
        if not isinstance(spec.gateway_session_token, str) or len(spec.gateway_session_token) < 16:
            raise CellError("Skyvern requires a session gateway token")
        parsed = urlsplit(spec.gateway_url)
        try:
            address = ipaddress.ip_address(parsed.hostname or "")
            port = parsed.port
            expires = datetime.fromisoformat(spec.expires_at)
        except (TypeError, ValueError) as exc:
            raise CellError("invalid Skyvern gateway address or expiry") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or address not in ipaddress.ip_network("100.64.0.0/10")
            or not port
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/", "/v1", "/v1/"}
            or parsed.query
            or parsed.fragment
        ):
            raise CellError("Skyvern gateway must be a NetBird IP and explicit port")
        if expires.tzinfo is None or not 0 < (expires - datetime.now(UTC)).total_seconds() <= 3600:
            raise CellError("Skyvern session token must expire within one hour")
        return spec

    @property
    def gateway_host(self) -> str:
        return urlsplit(self.gateway_url).hostname or ""

    @property
    def gateway_port(self) -> int:
        return urlsplit(self.gateway_url).port or 0

    @property
    def gateway_scheme(self) -> str:
        return urlsplit(self.gateway_url).scheme

    def remaining_seconds(self) -> float:
        expires = datetime.fromisoformat(self.expires_at)
        return (expires - datetime.now(UTC)).total_seconds()


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
    if parsed.password or parsed.query or parsed.fragment or parsed.port is not None:
        raise CellError("remote Docker SSH URL must not contain credentials or options")
    return (parsed.username + "@" if parsed.username else "") + parsed.hostname


def _ssh_prefix() -> list[str]:
    raw = os.environ.get("DOCKER_SSH_COMMAND", "ssh")
    try:
        parts = shlex.split(raw)
    except ValueError as exc:
        raise CellError("invalid DOCKER_SSH_COMMAND") from exc
    if not parts or parts[0] != "ssh":
        raise CellError("DOCKER_SSH_COMMAND must start with ssh")
    index = 1
    while index < len(parts):
        flag = parts[index]
        if flag == "-i" and index + 1 < len(parts):
            if not os.path.isabs(parts[index + 1]):
                raise CellError("Docker SSH identity path must be absolute")
            index += 2
        elif flag == "-o" and index + 1 < len(parts):
            option = parts[index + 1]
            if option.startswith("UserKnownHostsFile="):
                if not os.path.isabs(option.partition("=")[2]):
                    raise CellError("Docker SSH known_hosts path must be absolute")
            elif option not in {"StrictHostKeyChecking=yes", "IdentitiesOnly=yes", "BatchMode=yes"}:
                raise CellError("unsupported DOCKER_SSH_COMMAND option")
            index += 2
        else:
            raise CellError("unsupported DOCKER_SSH_COMMAND option")
    return [*parts, "-o", "StrictHostKeyChecking=yes"]


def _cdp_url(port: int) -> str:
    with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=5) as response:
        payload = json.load(response)
    browser_url = payload.get("webSocketDebuggerUrl")
    parsed = urlsplit(browser_url or "")
    if parsed.scheme not in {"ws", "wss"} or not parsed.path.startswith("/devtools/browser/"):
        raise CellError("Chromium did not expose a browser CDP WebSocket")
    return urlunsplit((parsed.scheme, f"127.0.0.1:{port}", parsed.path, "", ""))


def _wait_preflight(name: str) -> dict:
    timeout_s = max(15, min(300, int(os.environ.get("ONTOFILL_CELL_PREFLIGHT_TIMEOUT_S", "90"))))
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = _docker("exec", name, "cat", "/out/preflight.json", check=False, timeout=5)
        if result.returncode == 0:
            return json.loads(result.stdout)
        state = _docker("inspect", "--format", "{{.State.Running}}", name, check=False, timeout=5)
        if state.returncode == 0 and state.stdout.strip() != "true":
            break
        time.sleep(0.5)
    logs = _docker("logs", "--tail", "10", name, check=False, timeout=10)
    detail = (logs.stdout or logs.stderr).strip()[-600:]
    suffix = f": {detail}" if detail else " (pod exited or timed out without a log)"
    raise CellError("hands pod did not produce a preflight proof" + suffix)


def _wait_cdp(port: int) -> str:
    for _ in range(60):
        try:
            return _cdp_url(port)
        except (OSError, ValueError, CellError):
            time.sleep(0.25)
    raise CellError("hands CDP endpoint did not become ready")


def _published_port(name: str, internal_port: int = 9222) -> int:
    result = _docker("port", name, f"{internal_port}/tcp")
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
        *_ssh_prefix(),
        "-N",
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ConnectTimeout=5",
        "-L",
        f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}",
        target,
    ]
    try:
        tunnel = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise CellError("could not start control-plane CDP tunnel") from exc
    return local_port, tunnel


def _docker_with_session_token(token: str, *args: str) -> None:
    """Pass the ephemeral token through CLI environment, never argv or errors."""
    env = os.environ.copy()
    target = env.get("ONTOFILL_SANDBOX_DOCKER_HOST")
    if target:
        env["DOCKER_HOST"] = target
    env["OPENAI_API_KEY"] = token
    try:
        result = subprocess.run(
            ["docker", *args], env=env, capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CellError("Skyvern brain Docker start failed") from exc
    finally:
        env.pop("OPENAI_API_KEY", None)
    if result.returncode:
        raise CellError("Skyvern brain Docker start failed")


def _host_iptables(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Edit only exact per-cell INPUT/DOCKER-USER rules on the remote sandbox host."""
    target = _remote_ssh_target()
    if target is None:
        raise CellError("Skyvern gateway firewall needs remote sandbox SSH")
    command = [
        *_ssh_prefix(),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        target,
        "sudo",
        "-n",
        "iptables",
        "-w",
        "5",
        *args,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CellError("sandbox gateway firewall command failed") from exc
    if check and result.returncode:
        raise CellError("sandbox gateway firewall command failed")
    return result


def _ensure_image(image: str) -> None:
    if _docker("image", "inspect", image, check=False).returncode:
        _docker("pull", image, timeout=900)
    inspected = json.loads(
        _docker("image", "inspect", image, "--format", "{{json .Config.Env}}").stdout
    )
    forbidden = ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "AWS_ACCESS", "VULTR_", "JEV_")
    if any(
        any(fragment in entry.partition("=")[0].upper() for fragment in forbidden)
        and bool(entry.partition("=")[2])
        for entry in inspected or []
    ):
        raise CellError("upstream image embeds a nonempty credential environment variable")


def _container_uplink_ip(name: str, uplink_network: str) -> str:
    networks = json.loads(
        _docker("inspect", "--format", "{{json .NetworkSettings.Networks}}", name).stdout
    )
    address = networks.get(uplink_network, {}).get("IPAddress", "")
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError as exc:
        raise CellError("relay has no uplink IPv4 address") from exc
    if not isinstance(parsed, ipaddress.IPv4Address) or not parsed.is_private:
        raise CellError("relay uplink address is not private IPv4")
    return address


def _firewall_rule(
    cell_id: str, source_ip: str, gateway: SkyvernSpec, *, allow: bool
) -> tuple[str, ...]:
    parts = ["DOCKER-USER", "-s", source_ip]
    if allow:
        parts.extend(
            ["-d", gateway.gateway_host, "-p", "tcp", "--dport", str(gateway.gateway_port)]
        )
    else:
        parts.extend(["-m", "conntrack", "--ctstate", "NEW"])
    parts.extend(["-m", "comment", "--comment", cell_id, "-j", "ACCEPT" if allow else "DROP"])
    return tuple(parts)


def _input_drop_rule(cell_id: str, source_ip: str) -> tuple[str, ...]:
    return (
        "INPUT",
        "-s",
        source_ip,
        "-m",
        "conntrack",
        "--ctstate",
        "NEW",
        "-m",
        "comment",
        "--comment",
        cell_id,
        "-j",
        "DROP",
    )


def _install_gateway_firewall(cell: _Cell, gateway: SkyvernSpec) -> None:
    source_ip = _container_uplink_ip(cell.gateway_relay, cell.uplink_network)
    cell.gateway_source_ip = source_ip
    cell.gateway_target = replace(gateway, gateway_session_token="")
    # Deny first. The relay contains no credential before the brain starts.
    _host_iptables(
        "-I", "DOCKER-USER", "1", *_firewall_rule(cell.cell_id, source_ip, gateway, allow=False)[1:]
    )
    cell.gateway_drop_installed = True
    _host_iptables("-I", "INPUT", "1", *_input_drop_rule(cell.cell_id, source_ip)[1:])
    cell.gateway_input_installed = True
    _host_iptables(
        "-I", "DOCKER-USER", "1", *_firewall_rule(cell.cell_id, source_ip, gateway, allow=True)[1:]
    )
    cell.gateway_allow_installed = True
    for allow in (True, False):
        rule = _firewall_rule(cell.cell_id, source_ip, gateway, allow=allow)
        _host_iptables("-C", *rule)
    _host_iptables("-C", *_input_drop_rule(cell.cell_id, source_ip))


def _remove_gateway_firewall(cell: _Cell) -> bool:
    if not cell.gateway_target:
        return True
    installed_rules = (
        (cell.gateway_source_ip, True, cell.gateway_allow_installed),
        (cell.gateway_source_ip, False, cell.gateway_drop_installed),
        (cell.api_source_ip, False, cell.api_drop_installed),
    )
    input_rules = (
        (cell.gateway_source_ip, cell.gateway_input_installed),
        (cell.api_source_ip, cell.api_input_installed),
    )
    for source_ip, allow, installed in installed_rules:
        if source_ip and installed:
            rule = _firewall_rule(cell.cell_id, source_ip, cell.gateway_target, allow=allow)
            _host_iptables("-D", *rule, check=False)
    for source_ip, installed in input_rules:
        if source_ip and installed:
            _host_iptables("-D", *_input_drop_rule(cell.cell_id, source_ip), check=False)
    forwarding_gone = all(
        _host_iptables(
            "-C",
            *_firewall_rule(cell.cell_id, source_ip, cell.gateway_target, allow=allow),
            check=False,
        ).returncode
        != 0
        for source_ip, allow, installed in installed_rules
        if source_ip and installed
    )
    input_gone = all(
        _host_iptables("-C", *_input_drop_rule(cell.cell_id, source_ip), check=False).returncode
        != 0
        for source_ip, installed in input_rules
        if source_ip and installed
    )
    return forwarding_gone and input_gone


def _wait_gateway(name: str, host: str, port: int) -> None:
    probe = f"import socket; socket.create_connection(({host!r}, {port}), 3).close()"
    for _ in range(10):
        if not _docker("exec", name, "python", "-c", probe, check=False, timeout=5).returncode:
            return
        time.sleep(0.5)
    raise CellError("session inference gateway is unreachable from relay")


def _wait_brain(port: int) -> None:
    for _ in range(60):
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/v1/heartbeat", timeout=3) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise CellError("Skyvern brain heartbeat did not become ready")


def _probe_brain(name: str, mesh_ip: str) -> dict:
    script = f"""
import json, os, socket
pairs = [('cdp', 'cdp', 9222), ('gateway', 'gateway', 8787),
         ('egress', 'egress', 8888), ('metadata', '169.254.169.254', 80),
         ('mesh', {mesh_ip!r}, 22)]
out = {{}}
for label, host, port in pairs:
    try:
        connection = socket.create_connection((host, port), 2)
        connection.close()
        out[label] = 'ALLOWED'
    except OSError:
        out[label] = 'BLOCKED'
out['credential_env_names'] = [name for name in os.environ if any(
    marker in name.upper() for marker in
    ('API_KEY', 'SECRET', 'TOKEN', 'PASSWORD', 'CREDENTIAL', 'VULTR_', 'NETBIRD_', 'AWS_', 'JEV_')
)]
print(json.dumps(out))
"""
    result = _docker("exec", name, "python", "-c", script, timeout=30)
    proof = json.loads(result.stdout)
    if (
        proof.get("cdp") != "ALLOWED"
        or proof.get("gateway") != "ALLOWED"
        or any(proof.get(key) != "BLOCKED" for key in ("egress", "metadata", "mesh"))
        or proof.get("credential_env_names") != ["OPENAI_API_KEY"]
    ):
        raise CellError("Skyvern brain network or credential-name probe failed")
    return proof


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
    brain_proof: dict = field(default_factory=dict)
    cdp_url: str | None = None
    brain_url: str | None = None
    brain_network: str | None = None
    uplink_network: str | None = None
    api_relay: str | None = None
    cdp_relay: str | None = None
    gateway_relay: str | None = None
    database: str | None = None
    brain: str | None = None
    brain_tunnel: subprocess.Popen[bytes] | None = None
    gateway_source_ip: str | None = None
    api_source_ip: str | None = None
    gateway_target: SkyvernSpec | None = field(default=None, repr=False)
    session_token: str | None = field(default=None, repr=False)
    gateway_drop_installed: bool = False
    gateway_allow_installed: bool = False
    gateway_input_installed: bool = False
    api_drop_installed: bool = False
    api_input_installed: bool = False
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
            "backend": "recorded",
            "model": "cell-substrate",
            "at": _now(),
        }
        self.on_trace = on_trace
        self._cells: dict[str, _Cell] = {}
        self._lock = threading.RLock()

    def _start_control_cdp_relay(self, cell: _Cell, relay_image: str) -> int:
        """Expose the hands' loopback CDP through one cell-only relay on host loopback."""
        suffix = cell.cell_id.partition(":")[2]
        cell.uplink_network = f"ontofill-cell-uplink-{suffix}"
        cell.cdp_relay = f"ontofill-cell-cdp-{suffix}"
        _docker("network", "create", cell.uplink_network)
        hands_ip = _container_network_ip(cell.hands, cell.network)
        _docker(
            "run",
            "-d",
            "--name",
            cell.cdp_relay,
            "--network",
            cell.uplink_network,
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
            "-p",
            "127.0.0.1::9222",
            "-e",
            "RELAY_TARGET_HOST=" + hands_ip,
            "-e",
            "RELAY_TARGET_PORT=9223",
            "-e",
            "RELAY_LISTEN_PORT=9222",
            relay_image,
            "python",
            "-u",
            "/app/relay.py",
        )
        _docker("network", "connect", cell.network, cell.cdp_relay)
        return _published_port(cell.cdp_relay)

    def create(
        self,
        backend: str,
        allowed_domains: list[str],
        limits: SandboxLimits | dict | None = None,
        placement: str = "sandbox_vm",
        *,
        skyvern: Mapping[str, object] | None = None,
        brain_env: Mapping[str, object] | None = None,
    ) -> dict:
        if backend not in {"native", "skyvern"}:
            raise ValueError("backend must be native or skyvern")
        if placement not in {"sandbox_vm", "throwaway_vx1"}:
            raise ValueError("placement must be sandbox_vm or throwaway_vx1")
        if placement == "throwaway_vx1":
            raise CellError("throwaway_vx1 is unavailable until Vultr admission is cleared")
        if backend == "native" and (skyvern is not None or brain_env is not None):
            raise CellError("native cells do not accept Skyvern credentials")
        brain_spec = SkyvernSpec.from_value(skyvern, brain_env) if backend == "skyvern" else None
        if brain_spec is not None and _remote_ssh_target() is None:
            raise CellError("Skyvern cells require the remote sandbox VM and host firewall")
        domains = _domains(allowed_domains)
        budget = SandboxLimits.from_value(limits)
        _remote_ssh_target()  # Validate remote target before creating any resource.
        _ssh_prefix()
        info = json.loads(_docker("info", "--format", "{{json .}}").stdout)
        try:
            docker_major = int(str(info["ServerVersion"]).split(".", 1)[0])
        except (KeyError, ValueError) as exc:
            raise CellError("browser cells require a known Docker Engine version") from exc
        if docker_major < 28:
            raise CellError(
                "browser cells require Docker Engine 28 or newer for loopback isolation"
            )
        if "runsc" not in info.get("Runtimes", {}):
            raise CellError("gVisor runsc is required for browser cells")
        agent_image, egress_image = _images()
        if brain_spec is not None:
            _ensure_image(brain_spec.image)
            _ensure_image(brain_spec.postgres_image)
        suffix = uuid.uuid4().hex[:12]
        cell = _Cell(
            cell_id=f"cell:{suffix}",
            backend=backend,
            domains=domains,
            limits=budget,
            network=f"ontofill-cell-{suffix}",
            proxy=f"ontofill-cell-egress-{suffix}",
            hands=f"ontofill-cell-hands-{suffix}",
            started_at=_now(),
            started_monotonic=time.monotonic(),
            host={
                "docker_host": info.get("Name", "unknown"),
                "runtime": "runsc",
                "runtime_available": True,
            },
        )
        with self._lock:
            self._cells[cell.cell_id] = cell
        try:
            _docker("network", "create", "--internal", cell.network)
            _docker(
                "run",
                "-d",
                "--name",
                cell.proxy,
                "--network",
                cell.network,
                "--network-alias",
                "egress",
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
            _docker("network", "connect", "bridge", cell.proxy)
            _wait_proxy(cell.proxy)
            proxy_ip = _container_network_ip(cell.proxy, cell.network)
            _docker(
                "run",
                "-d",
                "--name",
                cell.hands,
                "--runtime",
                "runsc",
                "--network",
                cell.network,
                "--add-host",
                f"egress:{proxy_ip}",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,size=512m",
                "--tmpfs",
                "/out:rw,nosuid,size=4m,mode=1777",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                *budget.docker_args(),
                "--shm-size",
                "256m",
                "-e",
                "PROBE_DENIED_HOST=" + _denied_probe_host(domains),
                "-e",
                "PROBE_MESH_IP=" + _mesh_probe_ip(),
                "-e",
                "XDG_CONFIG_HOME=/tmp/chromium-config",
                "-e",
                "XDG_CACHE_HOME=/tmp/chromium-cache",
                agent_image,
                "python",
                "-u",
                "/app/cdp.py",
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
            port, cell.tunnel = _open_tunnel(self._start_control_cdp_relay(cell, egress_image))
            cell.cdp_url = _wait_cdp(port)
            if brain_spec is not None:
                self._start_skyvern(cell, brain_spec, egress_image)
            cell.state = "ready"
            remaining = budget.timeout_s - (time.monotonic() - cell.started_monotonic)
            if brain_spec is not None:
                remaining = min(remaining, brain_spec.remaining_seconds())
            if remaining <= 0:
                cell.failure_reason = "timeout"
                raise CellError("browser cell deadline expired during startup")
            cell.timer = threading.Timer(remaining, self._expire, args=(cell.cell_id,))
            cell.timer.daemon = True
            cell.timer.start()
            return {
                "cell_id": cell.cell_id,
                "cdp_url": cell.cdp_url,
                "brain_url": cell.brain_url,
                "live_view_port": None,
            }
        except Exception:
            self._destroy(cell, mark_failed=True)
            raise

    def _start_skyvern(self, cell: _Cell, spec: SkyvernSpec, relay_image: str) -> None:
        suffix = cell.cell_id.partition(":")[2]
        cell.brain_network = f"ontofill-cell-brain-{suffix}"
        cell.gateway_relay = f"ontofill-cell-gateway-{suffix}"
        cell.api_relay = f"ontofill-cell-api-{suffix}"
        cell.database = f"ontofill-cell-db-{suffix}"
        cell.brain = f"ontofill-cell-brain-{suffix}"
        _docker(
            "network",
            "create",
            "--internal",
            "-o",
            "com.docker.network.bridge.gateway_mode_ipv4=isolated",
            cell.brain_network,
        )

        relay_caps = (
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
        )
        _docker("network", "connect", "--alias", "cdp", cell.brain_network, cell.cdp_relay)
        _docker(
            "run",
            "-d",
            "--name",
            cell.gateway_relay,
            "--network",
            cell.brain_network,
            "--network-alias",
            "gateway",
            *relay_caps,
            "-e",
            "RELAY_TARGET_HOST=" + spec.gateway_host,
            "-e",
            "RELAY_TARGET_PORT=" + str(spec.gateway_port),
            "-e",
            "RELAY_LISTEN_PORT=8787",
            relay_image,
            "python",
            "-u",
            "/app/relay.py",
        )
        _docker("network", "connect", cell.uplink_network, cell.gateway_relay)
        _install_gateway_firewall(cell, spec)
        _wait_gateway(cell.gateway_relay, spec.gateway_host, spec.gateway_port)

        _docker(
            "run",
            "-d",
            "--name",
            cell.database,
            "--network",
            cell.brain_network,
            "--network-alias",
            "postgres",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "70:70",
            "--pids-limit",
            "64",
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--cpus",
            "0.5",
            "--tmpfs",
            "/tmp:rw,nosuid,size=64m,mode=1777",
            "--tmpfs",
            "/var/run/postgresql:rw,nosuid,size=16m,mode=1777",
            "--tmpfs",
            "/var/lib/postgresql/data:rw,nosuid,size=512m,mode=1777",
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            "-e",
            "POSTGRES_USER=skyvern",
            "-e",
            "POSTGRES_DB=skyvern",
            "-e",
            "PGDATA=/var/lib/postgresql/data/pgdata",
            spec.postgres_image,
        )
        self._wait_database(cell.database)
        brain_args = (
            "run",
            "-d",
            "--name",
            cell.brain,
            "--network",
            cell.brain_network,
            "--log-driver",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            *cell.limits.docker_args(),
            "--tmpfs",
            "/tmp:rw,nosuid,size=256m,mode=1777",
            "--tmpfs",
            "/data:rw,nosuid,size=256m,mode=1777",
            "--tmpfs",
            "/app/.skyvern:rw,nosuid,size=8m,mode=1777",
            "-e",
            "DATABASE_STRING=postgresql+psycopg://skyvern@postgres:5432/skyvern",
            "-e",
            "BROWSER_TYPE=cdp-connect",
            "-e",
            "BROWSER_REMOTE_DEBUGGING_URL=http://cdp:9222/",
            "-e",
            "BROWSER_STREAMING_MODE=cdp",
            "-e",
            "ENABLE_OPENAI=true",
            "-e",
            "LLM_KEY=OPENAI_GPT5_5",
            "-e",
            "OPENAI_API_BASE=" + spec.gateway_scheme + "://gateway:8787/v1",
            "-e",
            "ENABLE_LOCAL_CREDENTIAL_VAULT=false",
            "-e",
            "OPENAI_API_KEY",
            spec.image,
        )
        cell.session_token = spec.gateway_session_token
        _docker_with_session_token(cell.session_token, *brain_args)
        _docker(
            "run",
            "-d",
            "--name",
            cell.api_relay,
            "--network",
            cell.uplink_network,
            *relay_caps,
            "-p",
            "127.0.0.1::8000",
            "-e",
            "RELAY_TARGET_HOST=" + cell.brain,
            "-e",
            "RELAY_TARGET_PORT=8000",
            "-e",
            "RELAY_LISTEN_PORT=8000",
            relay_image,
            "python",
            "-u",
            "/app/relay.py",
        )
        _docker("network", "connect", cell.brain_network, cell.api_relay)
        cell.api_source_ip = _container_uplink_ip(cell.api_relay, cell.uplink_network)
        _host_iptables(
            "-I",
            "DOCKER-USER",
            "1",
            *_firewall_rule(cell.cell_id, cell.api_source_ip, spec, allow=False)[1:],
        )
        cell.api_drop_installed = True
        _host_iptables("-I", "INPUT", "1", *_input_drop_rule(cell.cell_id, cell.api_source_ip)[1:])
        cell.api_input_installed = True
        _host_iptables("-C", *_firewall_rule(cell.cell_id, cell.api_source_ip, spec, allow=False))
        _host_iptables("-C", *_input_drop_rule(cell.cell_id, cell.api_source_ip))
        port, cell.brain_tunnel = _open_tunnel(_published_port(cell.api_relay, 8000))
        _wait_brain(port)
        cell.brain_proof = _probe_brain(cell.brain, _mesh_probe_ip())
        self._emit(cell, "brain_network", cell.brain_proof, ok=True)
        cell.brain_url = f"http://127.0.0.1:{port}"

    @staticmethod
    def _wait_database(name: str) -> None:
        for _ in range(60):
            result = _docker("exec", name, "pg_isready", "-U", "skyvern", check=False, timeout=5)
            if result.returncode == 0:
                return
            time.sleep(0.5)
        raise CellError("per-cell Skyvern database did not become ready")

    def _require(self, cell_id: str) -> _Cell:
        try:
            return self._cells[cell_id]
        except KeyError as exc:
            raise CellError("unknown cell_id") from exc

    def status(self, cell_id: str) -> dict:
        with self._lock:
            cell = self._require(cell_id)
            if cell.state == "ready":
                state_result = _docker(
                    "inspect", "--format", "{{json .State}}", cell.hands, check=False
                )
                if state_result.returncode:
                    cell.failure_reason = "pids"
                    self._destroy(cell, mark_failed=True)
                else:
                    state = json.loads(state_result.stdout)
                    if not state.get("Running"):
                        cell.failure_reason = "memory" if state.get("OOMKilled") else "pids"
                        self._destroy(cell, mark_failed=True)
                if cell.state == "ready" and cell.brain:
                    brain_state = _docker(
                        "inspect", "--format", "{{json .State}}", cell.brain, check=False
                    )
                    if brain_state.returncode or not json.loads(brain_state.stdout).get("Running"):
                        self._destroy(cell, mark_failed=True)
            return {
                "cell_id": cell.cell_id,
                "backend": cell.backend,
                "state": cell.state,
                "steps": cell.steps,
                "limits": cell.limits.as_dict(),
                "failure_reason": cell.failure_reason,
                "cdp_url": cell.cdp_url if cell.state == "ready" else None,
                "brain_url": cell.brain_url if cell.state == "ready" else None,
                "live_view_port": None,
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
            if cell.session_token and cell.session_token in json.dumps(result, ensure_ascii=False):
                raise CellError("task result contains a session credential")
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
        if cell.brain_tunnel:
            cell.brain_tunnel.terminate()
            try:
                cell.brain_tunnel.wait(timeout=2)
            except subprocess.TimeoutExpired:
                cell.brain_tunnel.kill()
        for name in (
            cell.api_relay,
            cell.brain,
            cell.database,
            cell.gateway_relay,
            cell.cdp_relay,
            cell.hands,
            cell.proxy,
        ):
            if name is None:
                continue
            _docker("rm", "-f", name, check=False)
        try:
            firewall_removed = _remove_gateway_firewall(cell)
        except CellError:
            firewall_removed = False
        if cell.brain_network:
            _docker("network", "rm", cell.brain_network, check=False)
        if cell.uplink_network:
            _docker("network", "rm", cell.uplink_network, check=False)
        _docker("network", "rm", cell.network, check=False)
        teardown = {
            "pod_gone": _docker("inspect", cell.hands, check=False).returncode != 0,
            "proxy_gone": _docker("inspect", cell.proxy, check=False).returncode != 0,
            "network_removed": _docker("network", "inspect", cell.network, check=False).returncode
            != 0
            and (
                cell.brain_network is None
                or _docker("network", "inspect", cell.brain_network, check=False).returncode != 0
            )
            and (
                cell.uplink_network is None
                or _docker("network", "inspect", cell.uplink_network, check=False).returncode != 0
            )
            and all(
                _docker("inspect", name, check=False).returncode != 0
                for name in (
                    cell.api_relay,
                    cell.brain,
                    cell.database,
                    cell.gateway_relay,
                    cell.cdp_relay,
                )
                if name
            )
            and firewall_removed,
        }
        teardown["verified"] = all(teardown.values())
        cell.state = "destroyed" if teardown["verified"] else "teardown_failed"
        cell.cdp_url = None
        cell.brain_url = None
        cell.session_token = None
        if cell.failure_reason:
            self._emit(cell, "limit_kill", {"reason": cell.failure_reason}, ok=False)
        if cell.task_result is None:
            self._emit(
                cell,
                "dispatch_result",
                {"reason": cell.failure_reason or "no_task_result"},
                ok=False,
            )
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
            step_id=f"step:{uuid.uuid4().hex}",
            run_id=self.run_id or "run:unattached",
            phase=3,
            source_id=self.source_id,
            objective_id=None,
            tdd_path=self.tdd_path,
            observed={"cell_id": cell.cell_id},
            requested={"proof_checkpoint": checkpoint},
            executed=detail,
            evaluated=evaluated,
            ts=_now(),
            generated_by=self.generated_by,
            parent_step_id="step:" + cell.cell_id.partition(":")[2],
            event=event,
        )
        self.on_trace(row)

    def _job_record(self, cell: _Cell, teardown: dict, *, mark_failed: bool) -> dict:
        host = (
            {
                "ok": cell.host["runtime_available"],
                "sandbox_host": cell.host["docker_host"],
                "runtime": cell.host["runtime"],
                "virt": {
                    "cpu_virtualization_flags": cell.host["cpu_virtualization_flags"],
                    "dev_kvm_present": cell.host["dev_kvm_present"],
                },
            }
            if "cpu_virtualization_flags" in cell.host
            else {"ok": False, "not_run": True}
        )
        where = (
            {
                "ok": bool(cell.pod.get("hostname")),
                "hostname": cell.pod["hostname"],
                "uname": cell.pod["uname"],
            }
            if cell.pod
            else {"ok": False, "not_run": True}
        )
        isolation = (
            {
                "probes": [
                    {"probe": name, "result": "BLOCKED" if blocked else "ALLOWED"}
                    for name, blocked in (
                        (
                            "network_non_allowlisted",
                            cell.isolation["network"]["blocked"]
                            and cell.isolation["network"]["proxy_logged_block"],
                        ),
                        ("write_outside_pod", cell.isolation["write_outside_pod"]["blocked"]),
                        (
                            "write_outside_writable_mount",
                            cell.isolation["write_outside_writable_mount"]["blocked"],
                        ),
                    )
                ]
            }
            if cell.isolation
            else {"probes": [], "not_run": True}
        )
        if cell.brain_proof and not isolation.get("not_run"):
            isolation["probes"].extend(
                {"probe": "brain_" + name, "result": result}
                for name, result in cell.brain_proof.items()
                if name in {"egress", "metadata", "mesh"}
            )
        wall_s = round(max(0.0, time.monotonic() - cell.started_monotonic), 3)
        record = {
            "job_id": "job:" + cell.cell_id.partition(":")[2],
            "run_id": self.run_id or "run:unattached",
            "step_id": "step:" + cell.cell_id.partition(":")[2],
            "source_id": self.source_id,
            "started_at": cell.started_at,
            "ended_at": _now(),
            "generated_by": self.generated_by,
            "limits": cell.limits.as_dict(),
            "usage": {"peak_memory_mb": cell.peak_memory_mb, "wall_s": wall_s, "steps": cell.steps},
            "checkpoints": {
                "host": host,
                "task": {
                    "ok": bool(cell.task_ok and not mark_failed),
                    "requested": {"backend": cell.backend, "allowed_domains": cell.domains},
                    "result": cell.task_result
                    or {"reason": cell.failure_reason or "no_task_result"},
                    "value_ids": [],
                },
                "where": where,
                "isolation": isolation,
                "secrets": cell.secrets or {"ok": False, "not_run": True},
                "teardown": {"ok": teardown["verified"], "detail": teardown},
            },
        }
        if cell.failure_reason:
            record["failure_reason"] = cell.failure_reason
        validate_job_record(record)
        return record
