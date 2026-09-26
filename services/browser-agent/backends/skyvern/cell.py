"""Skyvern half of the cell provider (brief 07 v2 slice 3; CONTRACT §13a): brain + hands for backend="skyvern".

Skyvern is AGPL-3.0. We run the published image **unmodified** as a separate service and talk to it over its HTTP
API only; no Skyvern code is imported or copied here. `BROWSER_TYPE=cdp-connect` is process-wide, so one brain
drives exactly one browser: a cell is the pair, on its own docker network (skyvern-jev.md §3, §9).

`create_skyvern_cell(...)` returns a `SkyvernCell` carrying the §13a fields `{cell_id, cdp_url, brain_url,
live_view_port}` plus the Skyvern API key the brain minted for itself (in memory only). Pooling, leasing and
recycling belong to the controller's CellPool; this module only knows how to build, recycle and destroy one cell.

Secrets: the brain gets a **gateway session token** as `OPENAI_COMPATIBLE_API_KEY` and the gateway as
`OPENAI_COMPATIBLE_API_BASE`; never an upstream key. The token goes through a 0600 `--env-file` (not argv, so it
never shows in `ps`), deleted as soon as the container is created. Because Skyvern reads the token at process start,
**binding a new session token means recreating the brain (~17 s)**; a hands-only recycle takes < 1 s.

Measured on the smoke test (SMOKE.md): hands cold start 0.5-0.9 s; brain boot 16.8-17.7 s on a migrated DB
(22.5 s first boot); warm read-only task 14-16 s; brain 1.7 GiB idle, hands 0.2-0.3 GiB.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx

IMAGE = "public.ecr.aws/skyvern/skyvern:latest"
HANDS_SCRIPT = Path(__file__).parents[2] / "cells" / "hands.sh"
CDP_PORT = 9222
API_PORT = 8000
FORBIDDEN_ENV = re.compile(r"VULTR|JEV", re.IGNORECASE)


class CellError(RuntimeError):
    pass


class Docker:
    """Thin wrapper over the docker CLI (no SDK dependency). Tests replace it with a fake."""

    def __call__(self, args: Sequence[str], *, check: bool = True, input: str | None = None) -> str:
        p = subprocess.run(["docker", *args], capture_output=True, text=True, input=input, check=False)
        if check and p.returncode != 0:
            raise CellError(f"docker {args[0]} failed: {p.stderr.strip()[:300]}")
        return p.stdout


@dataclass
class Limits:
    brain_memory: str = "3g"
    brain_cpus: str = "2"
    brain_pids: int = 1024
    hands_memory: str = "1g"
    hands_cpus: str = "1"
    hands_pids: int = 512


@dataclass
class CellSpec:
    cell_id: str
    gateway_base: str  # as seen from inside the brain, e.g. http://host.docker.internal:8700/v1
    subnet_index: int  # cell network = 172.30.<subnet_index>.0/24; hands at .10 (stable across recycles)
    image: str = IMAGE
    model: str = "qwen3.8-flash-next"
    supports_vision: bool = False
    db_dsn: str = "postgresql+psycopg://skyvern:skyvern@ba-sky-pg:5432/skyvern"
    db_network: str = "ba-sky-db"
    allowed_hosts: list[str] = field(default_factory=list)  # private hosts Skyvern may visit (fixtures only)
    skip_migration_version: str | None = None
    limits: Limits = field(default_factory=Limits)
    hands_script: Path = HANDS_SCRIPT

    @property
    def network(self) -> str:
        return f"ba-sky-{self.cell_id}"

    @property
    def subnet(self) -> str:
        return f"172.30.{self.subnet_index}.0/24"

    @property
    def hands_ip(self) -> str:
        return f"172.30.{self.subnet_index}.10"

    @property
    def brain(self) -> str:
        return f"ba-sky-{self.cell_id}-brain"

    @property
    def hands(self) -> str:
        return f"ba-sky-{self.cell_id}-hands"

    def brain_env(self) -> dict[str, str]:
        """Non-secret brain settings. The token is added separately, only in the env file."""
        env = {
            "ENABLE_OPENAI_COMPATIBLE": "true",
            "OPENAI_COMPATIBLE_MODEL_NAME": self.model,
            "OPENAI_COMPATIBLE_API_BASE": self.gateway_base,
            "OPENAI_COMPATIBLE_SUPPORTS_VISION": "true" if self.supports_vision else "false",
            "LLM_KEY": "OPENAI_COMPATIBLE",
            "BROWSER_TYPE": "cdp-connect",
            "BROWSER_REMOTE_DEBUGGING_URL": f"http://{self.hands_ip}:{CDP_PORT}",
            "DATABASE_STRING": self.db_dsn,
            "SKYVERN_TELEMETRY": "false",
            "LOG_LEVEL": "INFO",
        }
        if self.allowed_hosts:
            env["ALLOWED_HOSTS"] = "[" + ",".join(f'"{h}"' for h in self.allowed_hosts) + "]"
        if self.skip_migration_version:
            env["ALLOWED_SKIP_DB_MIGRATION_VERSION"] = self.skip_migration_version
        return env


def ensure_postgres(docker: Docker, network: str = "ba-sky-db", name: str = "ba-sky-pg",
                    image: str = "postgres:14-alpine", memory: str = "512m", ready_timeout_s: float = 60,
                    sleep: Callable[[float], None] = time.sleep) -> None:
    """One Postgres shared by all skyvern cells (each brain creates its own org). Idempotent; waits until ready,
    because the brain's entrypoint runs `createdb` + migrations under `set -e` and exits if the DB is not up."""
    if name in docker(["ps", "-a", "--format", "{{.Names}}"]).split():
        docker(["start", name], check=False)
    else:
        if network not in docker(["network", "ls", "--format", "{{.Name}}"]).split():
            docker(["network", "create", "--internal", network])
        docker(["run", "-d", "--name", name, "--network", network, "--memory", memory, "-e", "POSTGRES_USER=skyvern",
                "-e", "POSTGRES_PASSWORD=skyvern", "-e", "POSTGRES_DB=skyvern", image])
    t0 = time.monotonic()
    while time.monotonic() - t0 < ready_timeout_s:
        # pg_isready passes during the init-time restart; a real query is the reliable signal
        if docker(["exec", name, "psql", "-U", "skyvern", "-d", "skyvern", "-tAc", "select 1"], check=False).strip() == "1":
            return
        sleep(0.5)
    raise CellError("postgres for skyvern cells did not become ready")


def alembic_version(docker: Docker, pg: str = "ba-sky-pg") -> str | None:
    """Current Skyvern schema version; pass it as `skip_migration_version` to later brains (saves ~2 s each)."""
    out = docker(["exec", pg, "psql", "-U", "skyvern", "-d", "skyvern", "-tAc",
                  "select version_num from alembic_version"], check=False).strip()
    return out or None


class SkyvernCell:
    """One cell: brain + hands. start(token) → recycle_hands()* / rebind(token) → stop()."""

    backend = "skyvern"

    def __init__(self, spec: CellSpec, docker: Docker | None = None, http: httpx.Client | None = None,
                 boot_timeout_s: float = 120, sleep: Callable[[float], None] = time.sleep):
        self.spec = spec
        self.docker = docker or Docker()
        self.http = http or httpx.Client(timeout=30)
        self.boot_timeout_s = boot_timeout_s
        self.sleep = sleep
        self.brain_url: str | None = None
        self._api_key: str | None = None
        self.timings: dict[str, float] = {}

    def __repr__(self) -> str:  # never show the API key
        return f"SkyvernCell({self.spec.cell_id!r}, brain={self.brain_url!r})"

    # -- §13a fields -----------------------------------------------------------------------------------------
    @property
    def cell_id(self) -> str:
        return self.spec.cell_id

    @property
    def cdp_url(self) -> str:  # internal to the cell network
        return f"http://{self.spec.hands_ip}:{CDP_PORT}"

    @property
    def live_view_port(self) -> int | None:
        return None  # slice 5: BROWSER_STREAMING_MODE=cdp or a CDP screencast relay from the hands

    @property
    def api_key(self) -> str:
        if not self._api_key:
            raise CellError("cell not started")
        return self._api_key

    def info(self) -> dict:
        return {"cell_id": self.cell_id, "backend": self.backend, "cdp_url": self.cdp_url,
                "brain_url": self.brain_url, "live_view_port": self.live_view_port}

    def status(self) -> dict:
        out = self.docker(["inspect", "--format", "{{.Name}} {{.State.Status}}", self.spec.brain, self.spec.hands],
                          check=False)
        states = dict(line.strip().lstrip("/").split(" ", 1) for line in out.splitlines() if " " in line)
        return {**self.info(), "brain": states.get(self.spec.brain, "missing"),
                "hands": states.get(self.spec.hands, "missing"), "timings": dict(self.timings)}

    # -- lifecycle -------------------------------------------------------------------------------------------
    def start(self, session_token: str) -> SkyvernCell:
        t0 = time.monotonic()
        d, s = self.docker, self.spec
        if s.network not in d(["network", "ls", "--format", "{{.Name}}"]).split():
            d(["network", "create", "--subnet", s.subnet, s.network])
        self._start_hands()
        self._start_brain(session_token)
        self.timings["start_s"] = round(time.monotonic() - t0, 2)
        return self

    def _start_hands(self) -> None:
        s, lim = self.spec, self.spec.limits
        self.docker(["rm", "-f", s.hands], check=False)
        self.docker(["run", "-d", "--name", s.hands, "--network", s.network, "--ip", s.hands_ip,
                     "--memory", lim.hands_memory, "--cpus", lim.hands_cpus, "--pids-limit", str(lim.hands_pids),
                     "--security-opt", "no-new-privileges",
                     "-v", f"{s.hands_script}:/hands.sh:ro", "--entrypoint", "/bin/bash", s.image, "/hands.sh"])

    def _start_brain(self, session_token: str) -> None:
        s, lim = self.spec, self.spec.limits
        env = s.brain_env()
        bad = [k for k in env if FORBIDDEN_ENV.search(k)]
        if bad:
            raise CellError(f"refusing to pass upstream-credential env names to a cell: {bad}")
        self.docker(["rm", "-f", s.brain], check=False)
        fd, path = tempfile.mkstemp(prefix="ba-sky-", suffix=".env")
        try:
            with os.fdopen(fd, "w") as fh:  # mkstemp → 0600
                for k, v in env.items():
                    fh.write(f"{k}={v}\n")
                fh.write(f"OPENAI_COMPATIBLE_API_KEY={session_token}\n")
            self.docker(["create", "--name", s.brain, "--network", s.network,
                         "--memory", lim.brain_memory, "--cpus", lim.brain_cpus, "--pids-limit", str(lim.brain_pids),
                         "--security-opt", "no-new-privileges",
                         "-p", f"127.0.0.1::{API_PORT}", "--env-file", path, s.image])
        finally:
            os.unlink(path)
        self.docker(["network", "connect", s.db_network, s.brain])
        self.docker(["start", s.brain])
        port = self.docker(["port", s.brain, f"{API_PORT}/tcp"]).strip().splitlines()[0].rsplit(":", 1)[1]
        self.brain_url = f"http://127.0.0.1:{port}/api/v1"
        self._wait_healthy()
        self._api_key = self._read_api_key()

    def _wait_healthy(self) -> None:
        t0 = time.monotonic()
        while time.monotonic() - t0 < self.boot_timeout_s:
            try:
                if self.http.get(f"{self.brain_url}/heartbeat", timeout=2).status_code == 200:
                    self.timings["brain_boot_s"] = round(time.monotonic() - t0, 2)
                    return
            except httpx.HTTPError:
                pass
            if self.docker(["inspect", "--format", "{{.State.Status}}", self.spec.brain], check=False).strip() == "exited":
                raise CellError(f"brain {self.spec.brain} exited during boot (see `docker logs`)")
            self.sleep(0.5)
        raise CellError(f"brain {self.spec.brain} not healthy after {self.boot_timeout_s}s")

    def _read_api_key(self) -> str:
        # Written by the image's entrypoint (scripts/create_organization.py); read, never logged.
        for _ in range(20):
            toml = self.docker(["exec", self.spec.brain, "cat", "/app/.skyvern/credentials.toml"], check=False)
            m = re.search(r'cred="([^"]+)"', toml)
            if m:
                return m.group(1)
            self.sleep(0.5)
        raise CellError("brain has no API credentials file")

    def recycle_hands(self) -> float:
        """Fresh browser (new profile, cookies gone), same brain and token. Brain reconnects per task (< 1 s)."""
        t0 = time.monotonic()
        self._start_hands()
        dt = round(time.monotonic() - t0, 2)
        self.timings["hands_recycle_s"] = dt
        return dt

    def rebind(self, session_token: str) -> float:
        """New session token → recreate the brain (the token is read at process start) and the hands (~17 s)."""
        t0 = time.monotonic()
        self._start_hands()
        self._start_brain(session_token)
        dt = round(time.monotonic() - t0, 2)
        self.timings["rebind_s"] = dt
        return dt

    def stop(self) -> None:
        s = self.spec
        self.docker(["rm", "-f", "-v", s.brain, s.hands], check=False)
        self.docker(["network", "rm", s.network], check=False)
        self._api_key = None

    def fetch_artifact(self, file_url: str) -> bytes:
        """Bytes of a brain artifact (`file:///data/artifacts/...`), read through docker, not a mounted host dir."""
        path = file_url.removeprefix("file://")
        if not path.startswith("/data/artifacts/") or ".." in path:
            raise CellError("not a brain artifact path")
        p = subprocess.run(["docker", "exec", self.spec.brain, "cat", path], capture_output=True, check=False)
        if p.returncode != 0:
            raise CellError("artifact not found")
        return p.stdout


def create_skyvern_cell(cell_id: str, gateway_token: str, gateway_base: str, subnet_index: int,
                        limits: Limits | None = None, docker: Docker | None = None, **spec_kw) -> SkyvernCell:
    """Provider entry for backend="skyvern" (§13a `cell.create`). Needs the session's gateway token up front:
    the brain reads it at start. Shared Postgres is created on first use."""
    d = docker or Docker()
    ensure_postgres(d)
    if "skip_migration_version" not in spec_kw:
        spec_kw["skip_migration_version"] = alembic_version(d)
    spec = CellSpec(cell_id=cell_id, gateway_base=gateway_base, subnet_index=subnet_index,
                    limits=limits or Limits(), **spec_kw)
    return SkyvernCell(spec, docker=d).start(gateway_token)
