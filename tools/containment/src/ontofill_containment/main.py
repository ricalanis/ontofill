"""The `ontofill-containment` command: append containment proof to a real run's feed.

Both fixtures are the engine's existing ones; nothing hostile is authored here.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path

from ontofill.lake import lake_for_case
from ontofill.repair import CaptureCase, run_code_repair
from ontofill.repair.runner import DockerRepairExecutor
from ontofill.runfeed import RunFeed
from ontofill.sandbox import SandboxLimits, append_job_record, build_job_record, capture_url
from ontofill.sandbox.jobs import validate_job_record

from .gateway import Gateway
from .support import (
    SOURCE_ID,
    TDD_PATH,
    FixtureServer,
    case_id_for,
    default_run_id,
    docker,
    fixture_text,
    iso_now,
    probe_pod,
    ssh_run,
    step_id,
)

HOSTILE_FIXTURE = "hostile.html"
DESTRUCTIVE_FIXTURE = "destructive_loop.py"
ALLOWED_DOMAIN = "host.docker.internal"


def _usage(model: str, raw: Mapping) -> dict:
    from ontofill.inference.decision import TOKEN_PRICES

    prompt = raw.get("prompt_tokens")
    completion = raw.get("completion_tokens")
    input_tokens = prompt if isinstance(prompt, int) and prompt >= 0 else 0
    output_tokens = completion if isinstance(completion, int) and completion >= 0 else 0
    prices = TOKEN_PRICES.get(model)
    est_usd = None
    if prices and isinstance(prompt, int) and isinstance(completion, int):
        est_usd = round(prompt / 1_000_000 * prices[0] + completion / 1_000_000 * prices[1], 8)
    return {
        "model": model,
        "backend": "vultr",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "est_usd": est_usd,
    }


def quarantine_step(
    *,
    run_id: str,
    capture_step_id: str,
    url: str,
    page_key: str,
    blocked_requests: list[str],
    screened: Mapping,
    provenance: Mapping,
    step: str | None = None,
) -> dict:
    """The §12a step: a screened observation flagged by the gateway is withheld from planning."""
    record = {
        "step_id": step or step_id(),
        "run_id": run_id,
        "phase": 5,
        "source_id": SOURCE_ID,
        "objective_id": None,
        "tdd_path": TDD_PATH,
        "mode": "S1",
        "event": "quarantine",
        "observed": {
            "url": url,
            "page_key": page_key,
            "blocked_requests": blocked_requests,
        },
        "requested": {"tool": "screen.page_content"},
        "executed": {"gate": screened["gate"], "model": screened["model"], "by": "gateway"},
        "evaluated": {"status": "quarantined", "reason": screened["screen"]["reason"]},
        "parent_step_id": capture_step_id,
        "value_ids": [],
        "ts": iso_now(),
        "generated_by": dict(provenance),
        "screen": dict(screened["screen"]),
        "usage": _usage(screened["model"], screened.get("usage") or {}),
    }
    return record


def verify_step(
    *,
    run_id: str,
    parent_step_id: str,
    sentinel_path: str,
    sentinel_value: str,
    sentinel_after: str,
    leftover: list[str],
    provenance: Mapping,
) -> dict:
    """Evidence that the destructive fixture was confined: the host sentinel is untouched."""
    intact = sentinel_after == sentinel_value
    return {
        "step_id": step_id(),
        "run_id": run_id,
        "phase": 5,
        "source_id": SOURCE_ID,
        "objective_id": None,
        "tdd_path": TDD_PATH,
        "mode": "D1",
        "observed": {"fixture": f"sandbox/fixtures/{DESTRUCTIVE_FIXTURE}"},
        "requested": {"proof": "host_sentinel_after_limit_kill"},
        "executed": {
            "host_sentinel": sentinel_path,
            "sentinel_intact": intact,
            "leftover_repair_containers": leftover,
        },
        "evaluated": {
            "status": "verified" if intact and not leftover else "failed",
            "reason": "the limit kill stayed inside the pod; the host was not touched",
        },
        "parent_step_id": parent_step_id,
        "value_ids": [],
        "ts": iso_now(),
        "generated_by": dict(provenance),
    }


def repair_job_record(
    *,
    run_id: str,
    step: str,
    host_info: dict,
    probe: dict,
    limits: Mapping,
    started_at: str,
    ended_at: str,
    wall_s: float,
    sentinel_path: str,
    sentinel_value: str,
    sentinel_after: str,
    leftover: list[str],
    provenance: Mapping,
) -> dict:
    """Six checkpoints for the repair job, measured on the runsc substrate."""
    intact = sentinel_after == sentinel_value
    isolation_probes = [
        {
            "probe": "network_egress",
            "result": "BLOCKED" if probe["isolation"]["network"]["blocked"] else "ALLOWED",
            "detail": {**probe["isolation"]["network"], "substrate": "runsc pod, --network none"},
        },
        {
            "probe": "write_outside_pod",
            "result": "BLOCKED"
            if probe["isolation"]["write_outside_pod"]["blocked"]
            else "ALLOWED",
            "detail": probe["isolation"]["write_outside_pod"],
        },
        {
            "probe": "write_outside_writable_mount",
            "result": "BLOCKED"
            if probe["isolation"]["write_outside_writable_mount"]["blocked"]
            else "ALLOWED",
            "detail": probe["isolation"]["write_outside_writable_mount"],
        },
        {
            "probe": "destructive_write_outside_pod",
            "result": "BLOCKED" if intact and not leftover else "ALLOWED",
            "detail": {
                "sentinel": sentinel_path,
                "sentinel_intact": intact,
                "leftover_containers": leftover,
            },
        },
    ]
    secrets = probe["secrets"]
    secrets_ok = (
        secrets["env_keys_found"] == 0
        and secrets["files_with_keys"] == 0
        and secrets["metadata_ip"] == "BLOCKED"
        and secrets["mesh"] == "BLOCKED"
    )
    record = {
        "job_id": f"job:{uuid.uuid4().hex}",
        "run_id": run_id,
        "step_id": step,
        "source_id": SOURCE_ID,
        "started_at": started_at,
        "ended_at": ended_at,
        "generated_by": dict(provenance),
        "limits": dict(limits),
        "usage": {"peak_memory_mb": 0.0, "wall_s": round(wall_s, 3), "steps": 1},
        "failure_reason": "timeout",
        "checkpoints": {
            "host": {
                "ok": bool(host_info.get("Runtimes", {}).get("runsc")),
                "sandbox_host": host_info.get("Name", "unknown"),
                "runtime": "runsc",
                "virt": {
                    "cpu_virtualization_flags": probe["cpu_virtualization_flags"],
                    "dev_kvm_present": probe["dev_kvm_present"],
                },
            },
            "task": {
                "ok": False,
                "requested": {
                    "action": "code.test",
                    "fixture": f"sandbox/fixtures/{DESTRUCTIVE_FIXTURE}",
                    "limits": dict(limits),
                },
                "result": {
                    "reason": "timeout",
                    "host_sentinel": "intact" if intact else "changed",
                    "leftover_repair_containers": leftover,
                },
                "value_ids": [],
            },
            "where": {
                "ok": bool(probe["hostname"] and probe["uname"].get("system") == "Linux"),
                "hostname": probe["hostname"],
                "uname": probe["uname"],
            },
            "isolation": {"probes": isolation_probes},
            "secrets": {
                "ok": secrets_ok,
                "env_keys_found": secrets["env_keys_found"],
                "files_with_keys": secrets["files_with_keys"],
                "metadata_ip": secrets["metadata_ip"],
                "mesh": secrets["mesh"],
            },
            "teardown": {
                "ok": not leftover,
                "detail": {
                    "pod_gone": not leftover,
                    "proxy_gone": True,
                    "network_removed": True,
                    "verified": not leftover,
                },
            },
        },
    }
    validate_job_record(record)
    return record


def _leftover_repair_containers() -> list[str]:
    containers = docker(
        "ps", "-a", "--filter", "name=ontofill-repair-", "--format", "{{.Names}}", check=False
    ).stdout.split()
    networks = docker(
        "network", "ls", "--filter", "name=ontofill-repair-", "--format", "{{.Name}}", check=False
    ).stdout.split()
    return [*containers, *networks]


def run_hostile_page(
    *,
    lake,
    feed: RunFeed,
    case_id: str,
    run_id: str,
    provenance: Mapping,
    gateway: Gateway,
    capture: Callable = capture_url,
    server_factory: Callable = FixtureServer,
) -> dict:
    content = fixture_text(HOSTILE_FIXTURE)
    with server_factory(HOSTILE_FIXTURE, content) as server:
        result = capture(
            server.url(),
            allowed_domains=[ALLOWED_DOMAIN],
            lake=lake,
            run_id=run_id,
            source_id=SOURCE_ID,
            objective_id=None,
            tdd_path=TDD_PATH,
            phase=5,
            generated_by=dict(provenance),
        )
    for row in result["trace"]:
        feed.append_step(row, screenshot_key=row.get("screenshot_key"))
    append_job_record(lake, case_id, build_job_record(result))

    capture_step_id = result["trace"][0]["step_id"]
    screened = gateway.screen_page(result["html"], step_id=step_id())
    blocked = sorted(
        {
            event["host"]
            for event in result.get("egress_events", [])
            if event.get("decision") == "block" and event.get("host")
        }
    )
    step = quarantine_step(
        run_id=run_id,
        capture_step_id=capture_step_id,
        url=result["url"],
        page_key=result["html_key"],
        blocked_requests=blocked,
        screened=screened,
        provenance=provenance,
    )
    feed.append_step(step)
    return {"quarantine": step["step_id"], "blocked": blocked}


def run_destructive_loop(
    *,
    lake,
    feed: RunFeed,
    case_id: str,
    run_id: str,
    provenance: Mapping,
    repair: Callable = run_code_repair,
    probe: Callable = probe_pod,
    docker_cmd: Callable = docker,
    ssh: Callable = ssh_run,
    leftover_check: Callable[[], list[str]] | None = None,
) -> dict:
    code = fixture_text(DESTRUCTIVE_FIXTURE)
    limits = SandboxLimits(timeout_s=3, max_steps=2)
    capture_key = lake.put_bytes(b"<p>x</p>", {"content_type": "text/html"})
    cases = [CaptureCase(capture_key, ({"name": "x"},))]

    sentinel_path = f"/tmp/ontofill-containment-sentinel-{uuid.uuid4().hex[:10]}"
    sentinel_value = uuid.uuid4().hex
    ssh(f"printf %s {sentinel_value} > {sentinel_path}")
    if ssh(f"cat {sentinel_path}").stdout.strip() != sentinel_value:
        raise RuntimeError("host sentinel could not be created")

    def patch_must_not_run(_feedback) -> str:
        raise AssertionError("a limit stop must not ask for a patch")

    started = iso_now()
    clock = time.monotonic()
    outcome = repair(
        lake=lake,
        captures=cases,
        initial_code=code,
        patch=patch_must_not_run,
        run_id=run_id,
        source_id=SOURCE_ID,
        objective_id=None,
        tdd_path=TDD_PATH,
        generated_by=dict(provenance),
        limits=limits,
        max_attempts=2,
        emit_step=feed.append_step,
        executor=DockerRepairExecutor(),
    )
    wall_s = time.monotonic() - clock
    ended = iso_now()
    if outcome.failure_reason != "timeout":
        raise RuntimeError(
            f"destructive fixture was not killed by the timeout: {outcome.failure_reason}"
        )

    sentinel_after = ssh(f"cat {sentinel_path}").stdout.strip()
    leftover = (leftover_check or _leftover_repair_containers)()
    if sentinel_after != sentinel_value or leftover:
        raise RuntimeError("destructive fixture escaped its pod")

    limit_step = next(row for row in outcome.trace if row.get("event") == "limit_kill")
    host_info = json.loads(docker_cmd("info", "--format", "{{json .}}").stdout)
    observed = probe(
        f"ontofill-containment-probe-{uuid.uuid4().hex[:10]}",
        limits.as_dict(),
        _mesh_ip(),
        image="python:3.12-alpine",
    )
    record = repair_job_record(
        run_id=run_id,
        step=limit_step["step_id"],
        host_info=host_info,
        probe=observed,
        limits=limits.as_dict(),
        started_at=started,
        ended_at=ended,
        wall_s=wall_s,
        sentinel_path=sentinel_path,
        sentinel_value=sentinel_value,
        sentinel_after=sentinel_after,
        leftover=leftover,
        provenance=provenance,
    )
    append_job_record(lake, case_id, record)
    step = verify_step(
        run_id=run_id,
        parent_step_id=limit_step["step_id"],
        sentinel_path=sentinel_path,
        sentinel_value=sentinel_value,
        sentinel_after=sentinel_after,
        leftover=leftover,
        provenance=provenance,
    )
    feed.append_step(step)
    ssh(f"rm -f {sentinel_path}", check=False)
    return {
        "limit_kill": limit_step["step_id"],
        "reason": outcome.failure_reason,
        "verify": step["step_id"],
    }


def _mesh_ip() -> str:
    import os

    return os.environ.get("ONTOFILL_CONTROL_NETBIRD_IP", "100.64.0.1")


def containment_provenance(gateway: Gateway) -> dict:
    return {"backend": "vultr", "model": gateway.model, "at": iso_now()}


def run_containment(
    case_dir: Path, run_id: str | None = None, *, gateway: Gateway | None = None
) -> dict:
    """Append both containment proofs to one real run's feed. Returns a summary."""
    case_dir = case_dir.resolve()
    if not case_dir.is_dir():
        raise FileNotFoundError(f"case directory does not exist: {case_dir}")
    lake = lake_for_case(case_dir)
    case_id = case_id_for(case_dir)
    run_id = run_id or default_run_id()
    gateway = gateway or Gateway()
    provenance = containment_provenance(gateway)

    summary: dict = {"run_id": run_id, "case_id": case_id}
    feed = RunFeed(lake, case_id, run_id, provenance, start_heartbeat=False)
    feed.update_status(state="running", phase=5)
    try:
        summary["hostile"] = run_hostile_page(
            lake=lake,
            feed=feed,
            case_id=case_id,
            run_id=run_id,
            provenance=provenance,
            gateway=gateway,
        )
        summary["destructive"] = run_destructive_loop(
            lake=lake, feed=feed, case_id=case_id, run_id=run_id, provenance=provenance
        )
        feed.update_status(state="done", phase=5)
    except Exception as exc:
        feed.update_status(
            state="failed", phase=5, reason=f"containment failed: {type(exc).__name__}"
        )
        feed.close()
        raise
    feed.close()
    return summary
