"""Local stand-in for the engine's cell substrate (CONTRACT §13a), over the docker CLI.

A native cell is one "hands" container: headless Chromium with CDP, on the cell's own Docker network, with memory,
CPU and process caps, and its CDP port published on 127.0.0.1 only. It uses the upstream Skyvern image purely as a
Chromium carrier (the image is not modified; `hands.sh` is mounted read-only) because it is already pulled for the
Skyvern cells.

What the stub is NOT: a sandbox. It runs runc (not gVisor) and does not enforce the egress allowlist at the network
layer; only the controller's per-request route abort applies. `isolation` says so, and the proof panel must not
present a stub cell as tier 3. A skyvern cell gets hands only (`brain_url` None): the brain is slice 3's work.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

from controller.cells import Cell, CellError

IMAGE = "public.ecr.aws/skyvern/skyvern:latest"
OWNER_LABEL = "ba.owner=browser-agent-stub"
HANDS = Path(__file__).with_name("hands.sh")
DEFAULTS = {"memory_mb": 1024, "cpus": 1.0, "pids": 512}
ISOLATION = {"runtime": "runc", "tier": 2, "egress": "not enforced by stub; controller route-abort only",
             "provider": "docker-stub"}


def _run(runner, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return runner(args, capture_output=True, text=True, check=check)


class DockerStubProvider:
    def __init__(self, image: str = IMAGE, docker: str = "docker", runner=subprocess.run, ready_timeout_s: float = 45,
                 hands_script: Path = HANDS):
        self.image = image
        self.docker = docker
        self.runner = runner
        self.ready_timeout_s = ready_timeout_s
        self.hands_script = Path(hands_script)

    # --- command construction (unit-tested) ---------------------------------------------------------------
    @staticmethod
    def names(cell_id: str) -> dict:
        return {"network": f"ba-{cell_id}", "hands": f"ba-{cell_id}-hands"}

    def network_cmd(self, cell_id: str) -> list[str]:
        return [self.docker, "network", "create", "--label", f"ba.cell={cell_id}", "--label", OWNER_LABEL,
                self.names(cell_id)["network"]]

    def hands_cmd(self, cell_id: str, limits: dict | None = None) -> list[str]:
        caps = DEFAULTS | {k: v for k, v in (limits or {}).items() if k in DEFAULTS and v}
        n = self.names(cell_id)
        # No -e/--env-file: the hands hold no credentials of any kind.
        return [self.docker, "run", "-d", "--name", n["hands"], "--label", f"ba.cell={cell_id}", "--label",
                OWNER_LABEL, "--label", "ba.role=hands", "--network", n["network"], "--network-alias", "hands",
                "--memory", f"{int(caps['memory_mb'])}m", "--cpus", str(caps["cpus"]),
                "--pids-limit", str(int(caps["pids"])), "--security-opt", "no-new-privileges",
                "-p", "127.0.0.1::9222", "-v", f"{self.hands_script.resolve()}:/hands.sh:ro",
                "--entrypoint", "/bin/bash", self.image, "/hands.sh"]

    # --- CellProvider ---------------------------------------------------------------------------------------
    def create(self, backend: str, allowed_domains: list[str], limits: dict | None = None,
               placement: str = "sandbox_vm") -> Cell:
        if placement != "sandbox_vm":
            raise NotImplementedError("docker stub: only placement=sandbox_vm (throwaway VX1 is the substrate's)")
        cell_id = f"cell-{uuid.uuid4().hex[:10]}"
        n = self.names(cell_id)
        try:
            _run(self.runner, self.network_cmd(cell_id))
            _run(self.runner, self.hands_cmd(cell_id, limits))
            port = self._published_port(n["hands"])
            cdp_url = f"http://127.0.0.1:{port}"
            self._wait_ready(cdp_url, n["hands"])
        except Exception as exc:
            self.destroy(cell_id)
            raise CellError(f"docker stub could not create {cell_id}: {exc}") from exc
        note = {} if backend == "native" else {
            "note": "NotImplemented in the stub: the Skyvern brain (slice 3) is not started; hands only"}
        return Cell(cell_id=cell_id, backend=backend, cdp_url=cdp_url, brain_url=None, live_view_port=None,
                    placement=placement, isolation=dict(ISOLATION), allowed_domains=tuple(allowed_domains or ()),
                    meta={"network": n["network"], "hands": n["hands"], **note})

    def destroy(self, cell_id: str) -> None:
        ids = _run(self.runner, [self.docker, "ps", "-aq", "--filter", f"label=ba.cell={cell_id}"],
                   check=False).stdout.split()
        if ids:
            _run(self.runner, [self.docker, "rm", "-f", "-v", *ids], check=False)
        _run(self.runner, [self.docker, "network", "rm", self.names(cell_id)["network"]], check=False)

    def status(self, cell_id: str) -> dict:
        out = _run(self.runner, [self.docker, "ps", "-a", "--filter", f"label=ba.cell={cell_id}", "--format",
                                 "{{json .}}"], check=False).stdout
        containers = [json.loads(line) for line in out.splitlines() if line.strip()]
        states = {c.get("Names"): c.get("State") for c in containers}
        state = "missing" if not containers else ("running" if all(s == "running" for s in states.values())
                                                   else "degraded")
        return {"cell_id": cell_id, "state": state, "containers": states, "isolation": dict(ISOLATION)}

    def sweep(self) -> int:
        """Remove every container and network this stub ever created (crash recovery, test teardown)."""
        ids = _run(self.runner, [self.docker, "ps", "-aq", "--filter", f"label={OWNER_LABEL}"],
                   check=False).stdout.split()
        if ids:
            _run(self.runner, [self.docker, "rm", "-f", "-v", *ids], check=False)
        nets = _run(self.runner, [self.docker, "network", "ls", "-q", "--filter", f"label={OWNER_LABEL}"],
                    check=False).stdout.split()
        if nets:
            _run(self.runner, [self.docker, "network", "rm", *nets], check=False)
        return len(ids)

    # --- helpers --------------------------------------------------------------------------------------------
    def _published_port(self, container: str) -> int:
        out = _run(self.runner, [self.docker, "port", container, "9222/tcp"]).stdout
        for line in out.splitlines():
            host, _, port = line.strip().rpartition(":")
            if host in ("127.0.0.1", "") and port.isdigit():
                return int(port)
        raise CellError(f"no 127.0.0.1 port published for {container}: {out.strip()!r}")

    def _wait_ready(self, cdp_url: str, container: str) -> None:
        deadline = time.monotonic() + self.ready_timeout_s
        last = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{cdp_url}/json/version", timeout=2) as resp:
                    if resp.status == 200 and "webSocketDebuggerUrl" in json.loads(resp.read()):
                        return
            except (OSError, ValueError) as exc:  # the relay is up before Chrome; keep polling
                last = exc
            time.sleep(0.25)
        raise CellError(f"{container} CDP not ready after {self.ready_timeout_s}s ({last})")
