"""Runner configuration from the environment (the same case registry as the Ontofill Console)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

CASE_ID = re.compile(r"^[a-z0-9-]{1,40}$")
DEFAULT_ENGINE_CMD = (
    "uv run --no-sync ontofill run {case_dir} --to-phase {to_phase} --run-id {run_id} --budget-usd {budget}"
)


@dataclass(frozen=True)
class CaseSpec:
    id: str
    case_dir: Path
    lake: Path | None = None  # explicit local lake; else <case parent>/lake.yaml


@dataclass
class Config:
    cases: dict[str, CaseSpec] = field(default_factory=dict)
    state_dir: Path = Path("/var/lib/ontofill-runner")
    poll_s: float = 10.0
    engine_cmd: str = DEFAULT_ENGINE_CMD
    engine_dir: Path | None = None
    engine_env_file: Path | None = None
    to_phase: int = 5
    budgets: dict[str, float] = field(default_factory=dict)
    global_usd: float | None = None
    default_case_usd: float = 2.0
    gateway_log: Path | None = None
    kill_grace_s: float = 30.0
    cases_root: Path | None = None  # CONTRACT v1.0.6: <root>/cases.json, the data-driven registry (re-read every tick)
    env_cases: dict[str, CaseSpec] = field(default_factory=dict)  # ONTOFILL_CONSOLE_CASES fallback
    env_budgets: dict[str, float] = field(default_factory=dict)
    to_phases: dict[str, int] = field(default_factory=dict)
    stale_running_s: float = 90.0  # a lake status "running" older than this is not a live engine


def parse_cases(spec: str) -> dict[str, CaseSpec]:
    """`id=/path/to/case[:/path/to/local/lake],…` (the console's ONTOFILL_CONSOLE_CASES format)."""
    cases: dict[str, CaseSpec] = {}
    for part in (p.strip() for p in (spec or "").split(",")):
        if not part:
            continue
        cid, sep, rest = part.partition("=")
        cid = cid.strip()
        if not sep or not CASE_ID.match(cid):
            raise ValueError(f"bad case entry {part!r}: expected id=/path (id: a-z, 0-9, -; at most 40 chars)")
        if cid in cases:
            raise ValueError(f"case id {cid!r} registered twice")
        case_path, _, lake = rest.partition(":")
        cases[cid] = CaseSpec(cid, Path(case_path.strip()), Path(lake.strip()) if lake.strip() else None)
    return cases


def parse_budgets(spec: str | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for part in (p.strip() for p in (spec or "").split(",")):
        if part:
            cid, _, usd = part.partition("=")
            out[cid.strip()] = float(usd)
    return out


def config_from_env(env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ if env is None else env)

    def path(name: str) -> Path | None:
        return Path(env[name]) if env.get(name) else None

    return Config(
        cases=parse_cases(env.get("ONTOFILL_CONSOLE_CASES", "")),
        state_dir=path("ONTOFILL_RUNNER_STATE") or Path("/var/lib/ontofill-runner"),
        poll_s=float(env.get("ONTOFILL_RUNNER_POLL_S") or 10),
        engine_cmd=env.get("ONTOFILL_RUNNER_ENGINE_CMD") or DEFAULT_ENGINE_CMD,
        engine_dir=path("ONTOFILL_RUNNER_ENGINE_DIR"),
        engine_env_file=path("ONTOFILL_RUNNER_ENGINE_ENV"),
        to_phase=int(env.get("ONTOFILL_RUNNER_TO_PHASE") or 5),
        budgets=parse_budgets(env.get("ONTOFILL_RUNNER_BUDGETS")),
        global_usd=float(env["ONTOFILL_RUNNER_GLOBAL_USD"]) if env.get("ONTOFILL_RUNNER_GLOBAL_USD") else None,
        default_case_usd=float(env.get("ONTOFILL_RUNNER_DEFAULT_CASE_USD") or 2.0),
        gateway_log=path("ONTOFILL_RUNNER_GATEWAY_LOG"),
        kill_grace_s=float(env.get("ONTOFILL_RUNNER_KILL_GRACE_S") or 30),
        cases_root=path("ONTOFILL_CASES_ROOT"),
        env_cases=parse_cases(env.get("ONTOFILL_CONSOLE_CASES", "")),
        env_budgets=parse_budgets(env.get("ONTOFILL_RUNNER_BUDGETS")),
    )


ID_RE = __import__("re").compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def load_registry(root: Path) -> tuple[dict[str, CaseSpec], dict[str, float], dict[str, int]]:
    """Read <root>/cases.json (CONTRACT v1.0.6): active (non-archived) cases, their budgets and to_phase defaults.
    Paths are absolute (migrated cases) or relative to the root; a relative path must resolve inside the root.
    Raises ValueError/OSError on a missing or malformed file (the caller keeps its last good copy)."""
    import json

    doc = json.loads((root / "cases.json").read_text(encoding="utf-8"))
    cases: dict[str, CaseSpec] = {}
    budgets: dict[str, float] = {}
    phases: dict[str, int] = {}
    root_real = root.resolve()
    for item in doc.get("cases") or []:
        cid = str(item.get("id") or "")
        if not ID_RE.match(cid) or item.get("archived"):
            continue
        raw = Path(str(item.get("path") or ""))
        case_dir = raw if raw.is_absolute() else (root / raw)
        if not raw.is_absolute() and root_real not in case_dir.resolve().parents:
            continue  # a relative path escaping the root is ignored
        cases[cid] = CaseSpec(cid, case_dir)
        if isinstance(item.get("budget_usd"), (int, float)):
            budgets[cid] = float(item["budget_usd"])
        if isinstance(item.get("to_phase"), int):
            phases[cid] = int(item["to_phase"])
    return cases, budgets, phases
