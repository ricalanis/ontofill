"""Five-phase case runner with honest mock previews and resumable live approvals."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from ontofill.case.checkpoints import load_json, require_approval, write_json
from ontofill.inference import RecordedDecisionClient, VultrDecisionClient, generated_by
from ontofill.lake import FileLake, lake_for_case
from ontofill.phases.p1_scope.phase import draft_prd
from ontofill.phases.p2_ontology.phase import draft_factors, draft_ontology
from ontofill.phases.p3_fanout.authority import authority_result, source_fingerprint
from ontofill.phases.p3_fanout.phase import discover_objectives
from ontofill.phases.p3_fanout.search import (
    ProviderSearchClient,
    SandboxSearchClient,
    SandboxWebSearchProvider,
)
from ontofill.phases.p4_local_scoping.phase import draft_local_scope
from ontofill.phases.p5_execute import execute_objective
from ontofill.refiner import Observation, export_run, refine_observations, silver_store_from_env
from ontofill.runfeed import RunFeed
from ontofill.sandbox import CaptureBlocked, append_job_record, build_job_record


def _preview_decision(brief: str) -> RecordedDecisionClient:
    """Deterministic scaffolding for a labeled preview; it contains no source or supplier data."""
    subject = (
        " ".join(
            line.strip()
            for line in brief.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )[:160]
        or "Public supplier investigation"
    )
    return RecordedDecisionClient(
        {
            "phase1.prd": [
                {
                    "version": "1",
                    "brief_path": "brief.md",
                    "personas": [{"id": "investigator", "description": "Public data investigator"}],
                    "jobs_to_be_done": [
                        {"id": "review", "persona_id": "investigator", "description": subject}
                    ],
                    "requirements": [
                        {
                            "id": "evidence",
                            "job_id": "review",
                            "description": "Trace supplier fields to public evidence",
                        }
                    ],
                    "constraints": ["Read-only public sources"],
                    "non_goals": ["Unaudited assertions"],
                    "definition_of_done": [
                        {
                            "id": "supplier_volume",
                            "metric": "suppliers_total",
                            "operator": ">=",
                            "target": 50,
                        },
                        {
                            "id": "core_coverage",
                            "metric": "suppliers_at_80pct_core",
                            "operator": ">=",
                            "target": 50,
                        },
                        {
                            "id": "source_diversity",
                            "metric": "distinct_source_types",
                            "operator": ">=",
                            "target": 4,
                        },
                        {
                            "id": "evidence_integrity",
                            "metric": "gold_values_without_evidence",
                            "operator": "=",
                            "target": 0,
                        },
                    ],
                }
            ],
            "phase2.factors": [
                {
                    "factors": [
                        {
                            "id": "supplier_identity",
                            "label": "Supplier identity",
                            "description": "Observed public supplier identity fields",
                            "kind": "conceptual",
                            "evidence": [],
                        }
                    ]
                }
            ],
            "phase2.taxonomies": [
                {
                    "taxonomies": [
                        {
                            "factor_id": "supplier_identity",
                            "root_label": "Supplier identity",
                            "children": [
                                {
                                    "id": "identified_supplier",
                                    "label": "Identified supplier",
                                    "level": 1,
                                    "critic_label": "Good-Exclusive",
                                }
                            ],
                        }
                    ]
                }
            ],
            "phase4.local_scope": [
                {
                    "global_requirement_ids": ["evidence"],
                    "local_definition_of_done": [
                        {"metric": "suppliers_total", "operator": ">=", "target": 1}
                    ],
                    "extraction_method": "download",
                    "validation_rules": ["Emit literal observed cells with bronze evidence"],
                    "rate_limit_per_minute": 6,
                    "budget_usd": 0,
                    "steps": [
                        {
                            "id": "read_source",
                            "description": "Read public source and emit evidenced supplier cells",
                            "starting_mode": "S1",
                            "allowed_modes": ["S1"],
                            "observation_channel": "text_structure",
                            "risk_tier": "SAFE",
                            "termination_predicate": "One supplier row observed or no accessible row remains",
                        }
                    ],
                }
            ],
            "phase5.select_download": [{"index": 0}],
        }
    )


def _load_local_env() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


def _case_id(case_dir: Path) -> str:
    pointer = case_dir.resolve().parent / "lake.yaml"
    if pointer.exists():
        config = yaml.safe_load(pointer.read_text(encoding="utf-8"))
        return config.get("case_id") or case_dir.name
    return case_dir.name


def _trace_step(run_id: str, phase: int, provenance: dict, tool: str, artifact: str) -> dict:
    return {
        "step_id": f"step:{uuid.uuid4().hex}",
        "run_id": run_id,
        "phase": phase,
        "source_id": None,
        "objective_id": None,
        "tdd_path": None,
        "mode": "D0",
        "observed": {"artifact": artifact},
        "requested": {"tool": tool},
        "executed": {"artifact": artifact},
        "evaluated": {"status": "ok"},
        "parent_step_id": None,
        "value_ids": [],
        "ts": datetime.now(UTC).isoformat(),
        "generated_by": provenance,
    }


def _scratch_case(case_dir: Path, run_id: str) -> tuple[Path, FileLake]:
    root = Path(__file__).resolve().parents[2] / ".cache" / "case-mock" / run_id
    scratch = root / "case"
    scratch.mkdir(parents=True, exist_ok=True)
    shutil.copy2(case_dir / "brief.md", scratch / "brief.md")
    return scratch, FileLake(root / "lake")


def _silver_cache(case_id: str, run_id: str) -> Path:
    return Path(__file__).resolve().parents[2] / ".cache" / "silver" / case_id / f"{run_id}.jsonl"


def _write_silver_cache(case_id: str, run_id: str, observations: list[Observation]) -> None:
    path = _silver_cache(case_id, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(asdict(item), ensure_ascii=False, sort_keys=True) + "\n"
            for item in observations
        ),
        encoding="utf-8",
    )


def _read_silver_cache(case_id: str, run_id: str) -> list[Observation]:
    path = _silver_cache(case_id, run_id)
    if not path.exists():
        return []
    return [
        Observation(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()
    ]


def _publish_steps(feed: RunFeed, trace: list[dict]) -> None:
    for step in trace:
        feed.append_step(step, screenshot_key=step.get("screenshot_key"))


def _source_review(case_dir: Path, objective: dict, provenance: dict) -> tuple[bool, Path]:
    directory = case_dir / "03-fanout/sources" / objective["source_id"]
    manifest_path = directory / "candidate.json"
    if manifest_path.exists():
        manifest = load_json(manifest_path)
    else:
        # Existing objectives from an earlier engine version still need authority review.
        fingerprint = source_fingerprint(
            url=objective["source_url"],
            title="",
            snippet="",
            provider="legacy",
            capture_key=None,
        )
        manifest = {
            "source_id": objective["source_id"],
            "url": objective["source_url"],
            "source_type": objective.get("source_type", "supplier website"),
            "provider": "legacy",
            "capture_key": None,
            "fingerprint": fingerprint,
            "authority": "review",
            "generated_by": provenance,
        }
        write_json(manifest_path, manifest)
    fingerprint = objective.get("source_fingerprint", manifest["fingerprint"])
    trusted, _ = authority_result(objective["source_url"])
    if manifest.get("authority") == "auto" and manifest["fingerprint"] == fingerprint:
        trusted = True
    if trusted:
        return True, directory
    approved = require_approval(
        directory,
        phase=3,
        checkpoint="source",
        artifact_paths=[f"03-fanout/sources/{objective['source_id']}/candidate.json"],
        generated_by=provenance,
        source_fingerprint=fingerprint,
    )
    return approved, directory


def _report_pause(checkpoint: str, directory: Path, recorded: bool) -> None:
    if recorded and (directory / "APPROVED").exists():
        reason = "APPROVED exists but the artifact was produced by the recorded backend"
    elif recorded:
        reason = "recorded artifacts cannot satisfy a checkpoint"
    else:
        reason = "waiting for human approval"
    print(f"state=paused checkpoint_pending={checkpoint} reason={reason}")


def run_case(
    case_dir: Path,
    *,
    from_phase: int = 1,
    to_phase: int = 5,
    run_id: str | None = None,
    budget_usd: float | None = None,
    preview_past_checkpoints: bool = False,
    decision=None,
    search_client=None,
    capture=None,
    fetch=None,
    lake=None,
    store=None,
) -> int:
    original = case_dir.resolve()
    _load_local_env()
    if from_phase not in range(1, 6) or to_phase not in range(from_phase, 6):
        raise ValueError("phase range must satisfy 1 <= from_phase <= to_phase <= 5")
    if budget_usd is not None and budget_usd < 0:
        raise ValueError("budget_usd must be nonnegative")
    if decision is None:
        if os.getenv("VULTR_INFERENCE_API_KEY") and os.getenv("VULTR_INFERENCE_MODEL"):
            decision = VultrDecisionClient.from_env()
        else:
            decision = _preview_decision((original / "brief.md").read_text(encoding="utf-8"))
    mock = decision.backend == "recorded"
    if preview_past_checkpoints and not mock:
        raise ValueError("checkpoint preview is reserved for recorded development runs")
    run_id = run_id or (("mock-" if mock else "run-") + uuid.uuid4().hex[:12])
    if mock != run_id.startswith("mock-"):
        raise ValueError("recorded runs require mock- IDs; live runs reserve them")
    case_id = _case_id(original)
    if mock:
        case_dir, default_lake = _scratch_case(original, run_id)
        lake = lake or default_lake
    else:
        case_dir = original
        lake = lake or lake_for_case(case_dir)
    location = "scratch" if mock else "case"
    print(f"run_id={run_id} {location}_path={case_dir} inference_backend={decision.backend}")
    provenance = generated_by(decision)
    store = store or silver_store_from_env()
    trace: list[dict] = []
    pending: str | None = None
    with RunFeed(lake, case_id, run_id, provenance, preview=preview_past_checkpoints) as feed:
        feed.update_status(state="running", phase=from_phase)
        try:
            prd = draft_prd(case_dir, decision)
            step = _trace_step(run_id, 1, provenance, "phase1.prd", "01-scope/prd.json")
            if from_phase <= 1:
                _publish_steps(feed, [step])
                trace.append(step)
            if not require_approval(
                case_dir / "01-scope",
                phase=1,
                checkpoint="prd",
                artifact_paths=["01-scope/prd.json"],
                generated_by=prd["generated_by"],
            ):
                pending = "prd"
                if not preview_past_checkpoints:
                    feed.update_status(state="paused", phase=1, checkpoint_pending="prd")
                    _report_pause("prd", case_dir / "01-scope", mock)
                    return 3
            if to_phase == 1:
                feed.update_status(
                    state="paused" if pending else "done", phase=1, checkpoint_pending=pending
                )
                if pending:
                    _report_pause(pending, case_dir / "01-scope", mock)
                return 3 if pending else 0

            if from_phase <= 2:
                feed.update_status(state="running", phase=2)
            factors = draft_factors(case_dir, prd, decision)
            step = _trace_step(
                run_id, 2, provenance, "phase2.factors", "02-ontology/factors/factors.json"
            )
            if from_phase <= 2:
                _publish_steps(feed, [step])
                trace.append(step)
            if not require_approval(
                case_dir / "02-ontology/factors",
                phase=2,
                checkpoint="factors",
                artifact_paths=["02-ontology/factors/factors.json"],
                generated_by=factors["generated_by"],
            ):
                pending = pending or "factors"
                if not preview_past_checkpoints:
                    feed.update_status(state="paused", phase=2, checkpoint_pending="factors")
                    _report_pause("factors", case_dir / "02-ontology/factors", mock)
                    return 3
            ontology = draft_ontology(case_dir, prd, factors, decision)
            step = _trace_step(
                run_id, 2, provenance, "phase2.ontology", "02-ontology/ontology.json"
            )
            if from_phase <= 2:
                _publish_steps(feed, [step])
                trace.append(step)
            if not require_approval(
                case_dir / "02-ontology",
                phase=2,
                checkpoint="ontology",
                artifact_paths=["02-ontology/ontology.json"],
                generated_by=ontology["generated_by"],
            ):
                pending = pending or "ontology"
                if not preview_past_checkpoints:
                    feed.update_status(state="paused", phase=2, checkpoint_pending="ontology")
                    _report_pause("ontology", case_dir / "02-ontology", mock)
                    return 3
            if to_phase == 2:
                feed.update_status(
                    state="paused" if pending else "done", phase=2, checkpoint_pending=pending
                )
                if pending:
                    _report_pause(pending, case_dir / "01-scope", mock)
                return 3 if pending else 0

            if from_phase <= 3:
                feed.update_status(state="running", phase=3)
            search_client = search_client or ProviderSearchClient(
                [
                    SandboxSearchClient(lake, run_id, provenance),
                    SandboxWebSearchProvider("bing_html", lake, run_id, provenance),
                    SandboxWebSearchProvider("duckduckgo_html", lake, run_id, provenance),
                ]
            )
            trace_before = len(getattr(search_client, "trace", []))
            jobs_before = len(getattr(search_client, "jobs", []))
            try:
                objectives = discover_objectives(
                    case_dir, ontology, decision, search_client, max_sources=1
                )
            finally:
                fresh_trace = getattr(search_client, "trace", [])[trace_before:]
                _publish_steps(feed, fresh_trace)
                trace.extend(fresh_trace)
                for job in getattr(search_client, "jobs", [])[jobs_before:]:
                    if "proof" in job:
                        append_job_record(lake, case_id, build_job_record(job))
            step = _trace_step(
                run_id, 3, provenance, "source.discover", "03-fanout/objectives.json"
            )
            if from_phase <= 3:
                _publish_steps(feed, [step])
                trace.append(step)
            for discovered in objectives["objectives"]:
                approved, directory = _source_review(case_dir, discovered, provenance)
                if not approved:
                    pending = pending or "source"
                    if not preview_past_checkpoints:
                        feed.update_status(state="paused", phase=3, checkpoint_pending="source")
                        _report_pause("source", directory, mock)
                        return 3
            if to_phase == 3:
                feed.update_status(
                    state="paused" if pending else "done", phase=3, checkpoint_pending=pending
                )
                if pending:
                    _report_pause(pending, case_dir / "01-scope", mock)
                return 3 if pending else 0

            if from_phase <= 4:
                feed.update_status(state="running", phase=4)
            objective = objectives["objectives"][0]
            _, tdd = draft_local_scope(
                case_dir, prd, ontology, objective, decision, budget_usd=budget_usd
            )
            step = _trace_step(
                run_id,
                4,
                provenance,
                "phase4.local_scope",
                f"04-local/{objective['source_id']}__{objective['id']}/tdd.json",
            )
            if from_phase <= 4:
                _publish_steps(feed, [step])
                trace.append(step)
            if to_phase == 4:
                feed.update_status(
                    state="paused" if pending else "done", phase=4, checkpoint_pending=pending
                )
                if pending:
                    _report_pause(pending, case_dir / "01-scope", mock)
                return 3 if pending else 0

            feed.update_status(
                state="running",
                phase=5,
                sources=[
                    {
                        "source_id": objective["source_id"],
                        "source_type": objective.get("source_type", "supplier website"),
                        "health": {"ok": 0, "failed": 0, "yield": 0},
                    }
                ],
            )
            kwargs = {}
            if capture is not None:
                kwargs["capture"] = capture
            if fetch is not None:
                kwargs["fetch"] = fetch
            execution = execute_objective(
                objective=objective,
                tdd=tdd,
                lake=lake,
                run_id=run_id,
                decision=decision,
                store=store,
                provenance=provenance,
                **kwargs,
            )
            _publish_steps(feed, execution.trace)
            trace.extend(execution.trace)
            observations = store.list_for_run(run_id)
            _write_silver_cache(case_id, run_id, observations)
            for index, job in enumerate(execution.sandbox_jobs):
                if "proof" in job:
                    ids = (
                        [item.value_id for item in execution.observations]
                        if index == len(execution.sandbox_jobs) - 1
                        else []
                    )
                    append_job_record(lake, case_id, build_job_record(job, value_ids=ids))
            shapes = case_dir / ontology["shacl_path"]
            refined = refine_observations(observations, generated_by=provenance, shapes_ttl=shapes)
            metrics = export_run(
                lake,
                case_dir,
                case_id,
                run_id,
                refined.suppliers,
                trace=trace,
                generated_by=provenance,
                preview=preview_past_checkpoints,
            )
            feed.update_status(
                state="paused" if pending else "done",
                phase=5,
                checkpoint_pending=pending,
                metrics=metrics,
                sources=[
                    {
                        "source_id": objective["source_id"],
                        "source_type": objective.get("source_type", "supplier website"),
                        "format": execution.format,
                        "health": {"ok": 1, "failed": 0, "yield": len(refined.suppliers)},
                    }
                ],
            )
            if pending:
                _report_pause(pending, case_dir / "01-scope", mock)
            else:
                print("state=done checkpoint_pending=none")
            return 3 if pending else 0
        except CaptureBlocked as exc:
            for step in exc.trace:
                step["event"] = "hard_stop"
                feed.append_step(step)
            phase = feed.current_status["phase"] if feed.current_status else 1
            feed.update_status(state="failed", phase=phase, checkpoint_pending=pending)
            raise
        except Exception:
            phase = feed.current_status["phase"] if feed.current_status else 1
            feed.update_status(state="failed", phase=phase, checkpoint_pending=pending)
            raise


def _existing_run(case_dir: Path, run_id: str | None) -> tuple[str, object, str, dict]:
    _load_local_env()
    case_id = _case_id(case_dir)
    if run_id and run_id.startswith("mock-"):
        case_dir, lake = _scratch_case(case_dir, run_id)
    else:
        lake = lake_for_case(case_dir)
    if not run_id:
        run_id = json.loads(lake.read_key(f"runs/{case_id}/latest.json"))["run_id"]
    status = json.loads(lake.read_key(f"runs/{case_id}/{run_id}/status.json"))
    return case_id, lake, run_id, status


def refine_case(case_dir: Path, *, run_id: str | None = None) -> int:
    case_id, lake, run_id, status = _existing_run(case_dir, run_id)
    provenance = status["generated_by"]
    if run_id.startswith("mock-"):
        case_dir, _ = _scratch_case(case_dir, run_id)
    store = silver_store_from_env()
    ontology = load_json(case_dir / "02-ontology/ontology.json")
    observations = store.list_for_run(run_id) or _read_silver_cache(case_id, run_id)
    if not observations:
        raise RuntimeError(f"no silver observations for {run_id}; refusing to overwrite gold")
    refined = refine_observations(
        observations,
        generated_by=provenance,
        shapes_ttl=case_dir / ontology["shacl_path"],
    )
    key = f"runs/{case_id}/{run_id}/trace.live.jsonl"
    trace = [json.loads(line) for line in lake.read_key(key).splitlines()]
    export_run(
        lake,
        case_dir,
        case_id,
        run_id,
        refined.suppliers,
        trace=trace,
        generated_by=provenance,
        preview=status.get("preview", False),
    )
    return 0


def export_case(case_dir: Path, *, run_id: str | None = None) -> int:
    return refine_case(case_dir, run_id=run_id)
