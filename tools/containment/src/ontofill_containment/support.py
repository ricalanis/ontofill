"""Shared plumbing for the containment command (case resolution, ssh/docker, steps)."""

from __future__ import annotations

import base64
import json
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from ontofill.sandbox import capture as _capture_module
from ontofill.sandbox.capture import _REPO_ROOT, _docker
from ontofill.sandbox.cells import _host_iptables, _remote_ssh_target, _ssh_prefix

FIXTURES = _REPO_ROOT / "sandbox" / "fixtures"
SOURCE_ID = "sandbox-containment"
TDD_PATH = "tools/containment/README.md"


def case_id_for(case_dir: Path) -> str:
    """Mirror `ontofill.workflow._case_id`: the lake pointer's case_id, else the directory name."""
    pointer = case_dir.resolve().parent / "lake.yaml"
    if pointer.exists():
        import yaml

        config = yaml.safe_load(pointer.read_text(encoding="utf-8"))
        return config.get("case_id") or case_dir.name
    return case_dir.name


def default_run_id(now: datetime | None = None) -> str:
    return "containment-demo-" + (now or datetime.now(UTC)).strftime("%Y%m%d%H%M")


def iso_now() -> str:
    return datetime.now(UTC).isoformat()


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def step_id() -> str:
    return f"step:{uuid.uuid4().hex}"


def docker(*args: str, timeout: int = 120, check: bool = True, input_text: str | None = None):
    return _docker(*args, timeout=timeout, check=check, input_text=input_text)


@contextmanager
def streamed_pod_transport() -> Iterator[None]:
    """Make the capture path's remote output copy work for runsc pods.

    `ontofill.sandbox.capture` transfers a remote pod's `/out` with `docker cp`, which the
    remote daemon cannot read from a runsc container (the repair runner already streams
    through `docker exec` for the same reason). This patches only that call for the life of
    the block; every other Docker command is unchanged. It is a tool-local workaround for an
    engine limitation, not a change to engine code.
    """
    real = _capture_module._docker

    def streamed(*args: str, **kwargs: Any):
        if args and args[0] == "cp" and len(args) == 3 and ":/out/." in str(args[1]):
            name = str(args[1]).split(":", 1)[0]
            destination = Path(str(args[2]))
            destination.mkdir(parents=True, exist_ok=True)
            listed = real(
                "exec",
                name,
                "python",
                "-c",
                "import os; print('\\n'.join(sorted(f for f in os.listdir('/out') "
                "if os.path.isfile('/out/' + f))))",
                check=False,
                timeout=30,
            )
            for filename in listed.stdout.split():
                encoded = real("exec", name, "base64", f"/out/{filename}", timeout=60)
                (destination / filename).write_bytes(base64.b64decode(encoded.stdout))
            return subprocess.CompletedProcess(list(args), 0, "", "")

        return real(*args, **kwargs)

    _capture_module._docker = streamed
    try:
        yield
    finally:
        _capture_module._docker = real


def ssh_run(
    command: str, *, timeout: int = 15, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run one shell command string on the sandbox host (where the sandbox Docker runs)."""
    target = _remote_ssh_target()
    if target is None:
        raise RuntimeError("remote sandbox SSH is required for host commands")
    argv = [*_ssh_prefix(), "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", target, command]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("sandbox host command failed") from exc
    if check and result.returncode:
        raise RuntimeError("sandbox host command failed")
    return result


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def bridge_gateway() -> tuple[str, str]:
    """(gateway IP, subnet) of the sandbox default bridge."""
    payload = json.loads(
        docker("network", "inspect", "bridge", "--format", "{{json .IPAM.Config}}").stdout
    )
    config = payload[0]
    return config["Gateway"], config["Subnet"]


class FixtureServer:
    """Serve one existing fixture file to the sandbox pods as `host.docker.internal`.

    The capture proxy reaches the sandbox host's bridge gateway; the host INPUT policy drops
    that path, so a single scoped ACCEPT rule (source = the bridge subnet, destination = the
    published port, tagged with a unique comment) is installed for the life of the server and
    removed afterwards. This mirrors the per-cell INPUT rules in `ontofill.sandbox.cells`.
    """

    def __init__(self, filename: str, content: str) -> None:
        self.filename = filename
        self.content = content
        self.name = f"ontofill-containment-fixture-{uuid.uuid4().hex[:10]}"
        self.comment = f"ontofill-containment-{uuid.uuid4().hex[:10]}"
        self.port: int | None = None
        self.gateway: str | None = None
        self.subnet: str | None = None
        self._rule_installed = False

    def url(self) -> str:
        return f"http://host.docker.internal:{self.port}/{self.filename}"

    def _rule(self) -> tuple[str, ...]:
        return (
            "-i",
            "docker0",
            "-s",
            str(self.subnet),
            "-p",
            "tcp",
            "--dport",
            str(self.port),
            "-m",
            "comment",
            "--comment",
            self.comment,
            "-j",
            "ACCEPT",
        )

    def _install_rule(self) -> None:
        _host_iptables("-I", "INPUT", "1", *self._rule())
        if _host_iptables("-C", "INPUT", *self._rule(), check=False).returncode:
            raise RuntimeError("fixture firewall rule could not be verified")
        self._rule_installed = True

    def _remove_rule(self) -> None:
        if not self._rule_installed or self.subnet is None:
            return
        _host_iptables("-D", "INPUT", *self._rule(), check=False)
        if _host_iptables("-C", "INPUT", *self._rule(), check=False).returncode == 0:
            raise RuntimeError("fixture firewall rule could not be removed")
        self._rule_installed = False

    def start(self) -> str:
        remote = _remote_ssh_target() is not None
        if remote:
            self.gateway, self.subnet = bridge_gateway()
        last_error: Exception | None = None
        for _ in range(5):
            self.port = free_port()
            publish = f"{self.gateway}:{self.port}:8000" if remote else f"{self.port}:8000"
            result = docker(
                "run",
                "-d",
                "--name",
                self.name,
                "-p",
                publish,
                "--tmpfs",
                "/tmp:rw,nosuid,mode=1777",
                "python:3.12-alpine",
                "sh",
                "-c",
                "mkdir -p /srv && cd /srv && exec python -m http.server 8000",
                check=False,
            )
            if result.returncode == 0:
                last_error = None
                break
            last_error = RuntimeError("fixture container did not start")
            docker("rm", "-f", self.name, check=False)
        if last_error is not None:
            raise last_error
        docker(
            "exec",
            "-i",
            self.name,
            "sh",
            "-c",
            f"cat > /srv/{self.filename}",
            input_text=self.content,
        )
        try:
            if remote:
                self._install_rule()
            self._wait_ready()
        except Exception:
            self.stop()
            raise
        return self.url()

    def _wait_ready(self) -> None:
        probe = (
            "import urllib.request; "
            f"urllib.request.urlopen('http://127.0.0.1:8000/{self.filename}', timeout=2).read(64)"
        )
        for _ in range(40):
            if not docker("exec", self.name, "python", "-c", probe, check=False).returncode:
                return
            time.sleep(0.25)
        raise RuntimeError("fixture server did not become ready")

    def stop(self) -> None:
        self._remove_rule()
        docker("rm", "-f", self.name, check=False)

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()


_PROBE_SCRIPT = r"""
import json, os, platform, re, socket
from pathlib import Path


def direct(host, port):
    try:
        socket.create_connection((host, port), timeout=1)
        return "ALLOWED"
    except OSError:
        return "BLOCKED"


def write_probe(path):
    try:
        Path(path).write_bytes(b"probe")
        try:
            Path(path).unlink()
        except OSError:
            pass
        return {"path": path, "blocked": False}
    except OSError as exc:
        return {"path": path, "blocked": exc.errno in (1, 2, 13, 30), "errno": exc.errno}


secret_paths = ("/app/.env", "/root/.aws/credentials", "/home/pwuser/.aws/credentials",
                "/var/run/secrets", "/run/secrets")
files_with_keys = 0
for path in secret_paths:
    try:
        item = Path(path)
        if item.is_file():
            files_with_keys += 1
        elif item.is_dir():
            files_with_keys += sum(1 for child in item.rglob("*") if child.is_file())
    except PermissionError:
        pass
pattern = re.compile(r"(API_KEY|SECRET|TOKEN|PASSWORD|AWS_ACCESS|VULTR_|JEV_)", re.IGNORECASE)
cpuinfo = Path("/proc/cpuinfo").read_text(errors="replace") if Path("/proc/cpuinfo").exists() else ""
print(json.dumps({
    "hostname": socket.gethostname(),
    "uname": platform.uname()._asdict(),
    "cpu_virtualization_flags": [flag for flag in ("vmx", "svm") if flag in cpuinfo.split()],
    "dev_kvm_present": Path("/dev/kvm").exists(),
    "secrets": {
        "env_keys_found": sum(bool(pattern.search(name)) for name in os.environ),
        "files_with_keys": files_with_keys,
        "metadata_ip": direct("169.254.169.254", 80),
        "mesh": direct(os.environ.get("PROBE_MESH_IP", "100.64.0.1"), 22),
    },
    "isolation": {
        "network": {"host": "ontofill-probe.invalid", "blocked": direct("93.184.215.14", 80) == "BLOCKED"},
        "write_outside_pod": write_probe("/host/ontofill-proof-denied"),
        "write_outside_writable_mount": write_probe("/etc/ontofill-proof-denied"),
    },
}))
"""


def probe_pod(name: str, limits: dict[str, Any], mesh_ip: str, *, image: str) -> dict:
    """Run the identity/isolation/secret probe inside a matching disposable runsc pod."""
    result = docker(
        "run",
        "--rm",
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
        "--memory",
        f"{limits['memory_mb']}m",
        "--memory-swap",
        f"{limits['memory_mb']}m",
        "--cpus",
        str(limits["cpus"]),
        "--pids-limit",
        str(limits["pids"]),
        "-e",
        f"PROBE_MESH_IP={mesh_ip}",
        image,
        "python",
        "-c",
        _PROBE_SCRIPT,
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("containment probe pod failed")
    return json.loads(result.stdout.strip().splitlines()[-1])
