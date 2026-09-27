"""Five-phase case runner with honest mock previews and resumable live approvals."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from dotenv import load_dotenv

from ontofill.case.checkpoints import (
    ApprovalArtifactMismatch,
    load_json,
    load_verified_approval,
    require_approval,
    write_json,
)
from ontofill.inference import RecordedDecisionClient, VultrDecisionClient, generated_by
from ontofill.lake import FileLake, lake_for_case
from ontofill.outer_gap import decide_outer_gap, outer_trace_step, prior_reopens
from ontofill.phase_loop import LoopBudget
from ontofill.phases.p1_scope.phase import PrdDraftUnavailable, draft_prd
from ontofill.phases.p2_ontology.phase import draft_factors, draft_ontology
from ontofill.phases.p3_fanout.authority import authority_result, source_fingerprint
from ontofill.phases.p3_fanout.discovery_loop import DiscoveryLoop
from ontofill.phases.p3_fanout.leads import default_lead_providers
from ontofill.phases.p3_fanout.phase import discover_objectives
from ontofill.phases.p3_fanout.search import (
    ProviderSearchClient,
    SandboxSearchClient,
)
from ontofill.phases.p4_local_scoping.phase import draft_local_scope
from ontofill.phases.p5_execute import execute_objectives
from ontofill.refiner import Observation, export_run, refine_observations, silver_store_from_env
from ontofill.refiner.bronze_replay import replay_bronze_observations
from ontofill.runfeed import RunFeed
from ontofill.sandbox import (
    CaptureBlocked,
    SandboxLimitExceeded,
    SandboxParseError,
    append_job_record,
    build_job_record,
    capture_url,
    fetch_url,
    parse_bronze_json,
)


class _SandboxCkanJsonFetcher:
    """Fetch and decode CKAN JSON through the network and parse sandboxes."""

    def __init__(
        self,
        *,
        lake,
        run_id: str,
        provenance: Mapping[str, str],
        fetch: Callable[..., dict] = fetch_url,
        parse: Callable[..., object] = parse_bronze_json,
    ) -> None:
        self.lake = lake
        self.run_id = run_id
        self.provenance = dict(provenance)
        self.fetch = fetch
        self.parse = parse
        self.trace: list[dict] = []
        self.jobs: list[dict] = []

    def __call__(self, url: str, domain: str) -> Mapping:
        allowed_domain = domain.casefold().rstrip(".")
        try:
            parsed_url = urlsplit(url)
            port = parsed_url.port
        except ValueError as exc:
            raise ValueError("invalid CKAN package_search URL") from exc
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname != allowed_domain
            or parsed_url.username
            or parsed_url.password
            or port is not None
            or parsed_url.path != "/api/3/action/package_search"
        ):
            raise ValueError("CKAN URL is outside its approved catalog endpoint")

        source_id = "source:ckan-catalog"
        tdd_path = "03-fanout/ckan/package-search.json"
        try:
            fetched = self.fetch(
                url,
                allowed_domains=[allowed_domain],
                lake=self.lake,
                run_id=self.run_id,
                source_id=source_id,
                objective_id=None,
                tdd_path=tdd_path,
                phase=3,
                generated_by=self.provenance,
                include_bytes=False,
            )
        except Exception as exc:
            self.trace.extend(getattr(exc, "trace", None) or [])
            failure_result = getattr(exc, "result", None)
            if isinstance(failure_result, dict) and "proof" in failure_result:
                self.jobs.append(failure_result)
            raise
        self.trace.extend(fetched.get("trace", []))
        self.jobs.append(fetched)
        try:
            decoded = self.parse(
                self.lake,
                fetched["bronze_key"],
                run_id=self.run_id,
                source_id=source_id,
                objective_id=None,
                tdd_path=tdd_path,
                phase=3,
                generated_by=self.provenance,
            )
        except SandboxParseError as exc:
            self.trace.extend(exc.trace)
            self.jobs.append(exc.job_record)
            raise
        self.trace.extend(decoded.trace)
        self.jobs.append(decoded.job_record)
        document = decoded.document
        if not isinstance(document, Mapping):
            raise TypeError("sandbox CKAN JSON root must be an object")
        return document


def _preview_decision(brief: str) -> RecordedDecisionClient:
    """Shape-based scaffolding for a labeled preview of any brief."""
    subject = (
        " ".join(
            line.strip()
            for line in brief.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )[:160]
        or "Public information question"
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
                            "description": "Trace observed values to public evidence",
                        }
                    ],
                    "constraints": ["Read-only public sources"],
                    "non_goals": ["Unaudited assertions"],
                    "definition_of_done": [
                        {
                            "id": "record_volume",
                            "metric": "records_total",
                            "operator": ">=",
                            "target": 1,
                            "basis": "proposed",
                            "rationale": "One record is a minimal preview target.",
                            "feasibility": "Preview budget and run time are not measured.",
                        },
                        {
                            "id": "evidence_integrity",
                            "metric": "values_without_evidence",
                            "operator": "=",
                            "target": 0,
                            "basis": "proposed",
                            "rationale": "A preview should show only evidenced values.",
                            "feasibility": "Preview budget and run time are not measured.",
                        },
                    ],
                    "authority_policy": {
                        "jurisdiction": "As stated in the brief",
                        "trusted_publishers": [],
                        "unknown_source_action": "review",
                    },
                }
            ],
            "phase2.factors": [
                {
                    "factors": [
                        {
                            "id": "record_identity",
                            "label": "Record identity",
                            "description": "Observed public record labels",
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
                            "factor_id": "record_identity",
                            "root_label": "Record identity",
                            "children": [
                                {
                                    "id": "identified_record",
                                    "label": "Identified record",
                                    "level": 1,
                                    "critic_label": "Good-Exclusive",
                                }
                            ],
                        }
                    ]
                }
            ],
            "phase2.schema": [
                {
                    "primary_class": "record",
                    "classes": [
                        {
                            "id": "record",
                            "label": "Record",
                            "label_plural": "Records",
                            "description": "A public observation relevant to the brief",
                            "title_property": "title",
                            "identifier_property": "title",
                            "aligned_to": None,
                        }
                    ],
                    "properties": [
                        {
                            "id": "title",
                            "label": "Title",
                            "domain": "record",
                            "datatype": "string",
                            "dod": True,
                            "order": 0,
                            "description": "Observed title",
                            "aligned_to": None,
                        }
                    ],
                    "relations": [],
                    "rules": [],
                    "source_classes": [{"id": "public_page", "label": "Public page"}],
                }
            ],
            "phase2.dod_queries": [
                {
                    "queries": [
                        {
                            "criterion_id": "record_volume",
                            "aggregate": "count_entities",
                            "class_id": "record",
                            "operator": ">=",
                            "target": 1,
                        },
                        {
                            "criterion_id": "evidence_integrity",
                            "aggregate": "count_values_without_evidence",
                            "operator": "=",
                            "target": 0,
                        },
                    ]
                }
            ],
            "phase4.local_scope": [
                {
                    "global_requirement_ids": ["evidence"],
                    "local_definition_of_done": [
                        {"metric": "records_total", "operator": ">=", "target": 1}
                    ],
                    "extraction_method": "download",
                    "validation_rules": ["Emit literal observed cells with bronze evidence"],
                    "rate_limit_per_minute": 6,
                    "budget_usd": 0,
                    "target_volume": 1,
                    "steps": [
                        {
                            "id": "read_source",
                            "description": "Read public source and emit evidenced cells",
                            "starting_mode": "S1",
                            "allowed_modes": ["S1"],
                            "observation_channel": "text_structure",
                            "risk_tier": "SAFE",
                            "termination_predicate": "One row observed or no accessible row remains",
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


def _persisted_run_trace(
    lake: object, case_id: str, run_id: str, current: list[dict]
) -> list[dict]:
    key = f"runs/{case_id}/{run_id}/trace.live.jsonl"
    if not lake.exists(key):
        return current
    return [json.loads(line) for line in lake.read_key(key).splitlines()]


def _merge_trace_steps(
    trace: list[dict], replay_steps: list[dict]
) -> tuple[list[dict], list[dict]]:
    merged = list(trace)
    by_id = {step["step_id"]: step for step in trace if isinstance(step.get("step_id"), str)}
    additions = []
    for replay_step in replay_steps:
        step_id = replay_step["step_id"]
        existing = by_id.get(step_id)
        if existing is not None:
            stable_existing = {key: value for key, value in existing.items() if key != "ts"}
            stable_replay = {key: value for key, value in replay_step.items() if key != "ts"}
            if stable_existing != stable_replay:
                raise ValueError(f"conflicting bronze replay trace step: {step_id}")
            continue
        by_id[step_id] = replay_step
        additions.append(replay_step)
        merged.append(replay_step)
    return merged, additions


def _append_trace_bytes(existing: bytes, additions: list[dict]) -> bytes:
    separator = b"\n" if existing and not existing.endswith(b"\n") else b""
    appended = b"".join(
        (json.dumps(step, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for step in additions
    )
    return existing + separator + appended


def _publish_steps(feed: RunFeed, trace: list[dict]) -> None:
    for step in trace:
        feed.append_step(step, screenshot_key=step.get("screenshot_key"))


def _publish_decision_calls(
    feed: RunFeed, trace: list[dict], decision: object, start: int, run_id: str, phase: int
) -> None:
    for call in getattr(decision, "call_log", [])[start:]:
        provenance = {key: call[key] for key in ("backend", "model", "at")}
        step = _trace_step(run_id, phase, provenance, "decision.complete_json", call["purpose"])
        step["mode"] = "D1"
        if call.get("usage") is not None:
            step["usage"] = call["usage"]
        step["evaluated"] = {"status": call.get("status", "ok")}
        _publish_steps(feed, [step])
        trace.append(step)


def _publish_unreported_decisions(
    feed: RunFeed, trace: list[dict], decision: object, run_id: str, phase: int
) -> None:
    published = sum(
        step.get("requested", {}).get("tool") == "decision.complete_json" for step in trace
    )
    _publish_decision_calls(feed, trace, decision, published, run_id, phase)


def _latest_outer_decision(trace: list[dict]) -> dict | None:
    return next(
        (
            step
            for step in reversed(trace)
            if step.get("event") == "loop" and step.get("loop", {}).get("phase") == "outer"
        ),
        None,
    )


def _gap_fields(step: dict | None) -> tuple[str, ...]:
    if step is None:
        return ()
    return tuple(
        dict.fromkeys(
            field
            for gap in step.get("observed", {}).get("gaps", [])
            for field in gap.get("properties", [])
        )
    )


def _gaps_for_objective(gaps: list[dict], objective: dict) -> list[dict]:
    fields = set(objective.get("target_fields", []))
    return [
        {**gap, "properties": [item for item in gap.get("properties", []) if item in fields]}
        for gap in gaps
        if fields.intersection(gap.get("properties", []))
    ]


def _reopen_discovery(case_dir: Path, iteration: int) -> None:
    """Force a new captured search round while retaining its objectives and ledger."""
    path = case_dir / "03-fanout/surface-map/discovery.json"
    if path.exists():
        ledger = load_json(path)
        ledger["request_fingerprint"] = f"reopen-round-{iteration}"
        write_json(path, ledger)


def _reopen_local_scope(case_dir: Path, objective: dict, iteration: int) -> None:
    """Archive the TDD and invalidate only its cache marker for a new draft."""
    directory = case_dir / "04-local" / f"{objective['source_id']}__{objective['id']}"
    if not directory.resolve().is_relative_to(case_dir.resolve()):
        raise ValueError("objective path escapes case directory")
    archive = directory / "revisions" / f"gap-{iteration}"
    archive.mkdir(parents=True, exist_ok=True)
    for name in ("local-prd.json", "tdd.json", "local-prd.md", "tdd.md"):
        source = directory / name
        if source.exists():
            shutil.copy2(source, archive / name)
    marker = directory / "tdd.md"
    if marker.exists():
        marker.unlink()


def _request_ontology_gap_review(
    case_dir: Path, iteration: int, gap_ids: list[str], provenance: dict
) -> None:
    directory = case_dir / "02-ontology"
    marker = directory / "APPROVED"
    if marker.exists():
        load_verified_approval(marker, case_dir, ["02-ontology/ontology.json"], "ontology")
        marker.rename(directory / f"APPROVED.stale.gap-{iteration}")
    require_approval(
        directory,
        phase=2,
        checkpoint="ontology",
        artifact_paths=["02-ontology/ontology.json"],
        generated_by=provenance,
    )
    with (directory / "APPROVAL_PENDING.md").open("a", encoding="utf-8") as pending:
        pending.write(
            "\n## Outer-loop DoD gaps\n\n"
            + "\n".join(f"- `{criterion_id}`" for criterion_id in gap_ids)
            + "\n\nReview the ontology against these approved criteria. Deny with a reason "
            "to regenerate it, or approve the current ontology to continue.\n"
        )


def _source_review(
    case_dir: Path, objective: dict, provenance: dict, authority_policy: dict
) -> tuple[bool, Path]:
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
            authority_policy=authority_policy,
        )
        manifest = {
            "source_id": objective["source_id"],
            "url": objective["source_url"],
            "source_type": objective.get("source_type"),
            "provider": "legacy",
            "capture_key": None,
            "fingerprint": fingerprint,
            "authority": "review",
            "generated_by": provenance,
        }
        write_json(manifest_path, manifest)
    fingerprint = source_fingerprint(
        url=objective["source_url"],
        title=manifest.get("title", ""),
        snippet=manifest.get("snippet", ""),
        provider=manifest["provider"],
        capture_key=manifest.get("capture_key"),
        authority_policy=authority_policy,
    )
    trusted, reason = authority_result(objective["source_url"], policy=authority_policy)
    current_authority = "auto" if trusted else "review"
    if manifest.get("fingerprint") != fingerprint or manifest.get("authority") != current_authority:
        manifest.update(
            fingerprint=fingerprint,
            authority=current_authority,
            authority_reason=reason,
        )
        write_json(manifest_path, manifest)
    if objective.get("source_fingerprint") != fingerprint:
        objective["source_fingerprint"] = fingerprint
        objectives_path = case_dir / "03-fanout/objectives.json"
        if objectives_path.exists():
            objectives = load_json(objectives_path)
            for item in objectives["objectives"]:
                if item["id"] == objective["id"]:
                    item["source_fingerprint"] = fingerprint
            write_json(objectives_path, objectives)
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
    browser_client=None,
    browser_steps_root: Path | None = None,
    browser_captures_root: Path | None = None,
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
        if os.getenv("ONTOFILL_GATEWAY_TOKEN") or os.getenv("VULTR_INFERENCE_API_KEY"):
            decision = VultrDecisionClient.from_env()
        else:
            decision = _preview_decision((original / "brief.md").read_text(encoding="utf-8"))
    if decision.backend not in {"recorded", "vultr"}:
        raise ValueError("primary case decisions require Vultr; Jev is supporting only")
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
    reopen_phase: int | None = None
    outer_paused = False
    historical_trace = _persisted_run_trace(lake, case_id, run_id, [])
    earlier_reopens = prior_reopens(historical_trace)
    latest_outer = _latest_outer_decision(historical_trace)
    discovery_gaps = (
        _gap_fields(latest_outer)
        if latest_outer is not None and latest_outer.get("executed", {}).get("reopen") == 3
        else ()
    )
    local_reopen = (
        latest_outer
        if latest_outer is not None and latest_outer.get("executed", {}).get("reopen") == 4
        else None
    )
    with RunFeed(lake, case_id, run_id, provenance, preview=preview_past_checkpoints) as feed:
        feed.update_status(state="running", phase=from_phase)
        try:

            def emit_prd_loop(step: dict) -> None:
                feed.append_step(step)
                trace.append(step)

            try:
                prd = draft_prd(
                    case_dir,
                    decision,
                    budget_usd=budget_usd,
                    run_id=run_id,
                    emit=emit_prd_loop,
                )
            except PrdDraftUnavailable:
                feed.update_status(state="paused", phase=1, checkpoint_pending="prd")
                print("state=paused checkpoint_pending=prd reason=PRD draft budget exhausted")
                return 3
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
                case_dir=case_dir,
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
            decision_start = len(getattr(decision, "call_log", []))
            factors = draft_factors(case_dir, prd, decision)
            _publish_decision_calls(feed, trace, decision, decision_start, run_id, 2)
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
                case_dir=case_dir,
            ):
                pending = pending or "factors"
                if not preview_past_checkpoints:
                    feed.update_status(state="paused", phase=2, checkpoint_pending="factors")
                    _report_pause("factors", case_dir / "02-ontology/factors", mock)
                    return 3
            decision_start = len(getattr(decision, "call_log", []))
            ontology = draft_ontology(case_dir, prd, factors, decision)
            _publish_decision_calls(feed, trace, decision, decision_start, run_id, 2)
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
                case_dir=case_dir,
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
            if search_client is None:
                providers = []
                if catalog := os.getenv("ONTOFILL_CATALOG_URL"):
                    providers.append(
                        SandboxSearchClient(lake, run_id, provenance, endpoint=catalog)
                    )
                lead_search = ProviderSearchClient(providers) if providers else None
                # Bounded P3 loop: lead-only providers, sandbox confirmation, authority check.
                search_client = DiscoveryLoop(
                    default_lead_providers(
                        decision,
                        search_client=lead_search,
                        fetch_json=_SandboxCkanJsonFetcher(
                            lake=lake,
                            run_id=run_id,
                            provenance=provenance,
                        ),
                    ),
                    capture=capture or capture_url,
                    lake=lake,
                    run_id=run_id,
                    provenance=provenance,
                    budget=LoopBudget(max_iterations=3, max_usd=budget_usd, wall_seconds=900),
                    spider_capture=(capture or capture_url) if not mock else None,
                )
            decision_start = len(getattr(decision, "call_log", []))
            trace_before = len(getattr(search_client, "trace", []))
            jobs_before = len(getattr(search_client, "jobs", []))
            try:
                objectives = discover_objectives(
                    case_dir,
                    ontology,
                    decision,
                    search_client,
                    gaps=discovery_gaps or None,
                    max_sources=4 if earlier_reopens else 1,
                )
            finally:
                _publish_decision_calls(feed, trace, decision, decision_start, run_id, 3)
                fresh_trace = getattr(search_client, "trace", [])[trace_before:]
                _publish_steps(feed, fresh_trace)
                trace.extend(fresh_trace)
                for job in getattr(search_client, "jobs", [])[jobs_before:]:
                    if "checkpoints" in job:
                        append_job_record(lake, case_id, job)
                    elif "proof" in job:
                        append_job_record(lake, case_id, build_job_record(job))
            step = _trace_step(
                run_id, 3, provenance, "source.discover", "03-fanout/objectives.json"
            )
            if from_phase <= 3:
                _publish_steps(feed, [step])
                trace.append(step)
            for discovered in objectives["objectives"]:
                approved, directory = _source_review(
                    case_dir, discovered, provenance, prd["authority_policy"]
                )
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
            selected_objectives = objectives["objectives"]
            tdds: dict[str, dict] = {}
            local_gaps = local_reopen.get("observed", {}).get("gaps", []) if local_reopen else []
            for objective in selected_objectives:
                relevant_gaps = _gaps_for_objective(local_gaps, objective)
                local_objective = (
                    {**objective, "gap_report": relevant_gaps} if relevant_gaps else objective
                )
                decision_start = len(getattr(decision, "call_log", []))
                _, tdd = draft_local_scope(
                    case_dir,
                    prd,
                    ontology,
                    local_objective,
                    decision,
                    budget_usd=budget_usd,
                )
                _publish_decision_calls(feed, trace, decision, decision_start, run_id, 4)
                tdds[objective["id"]] = tdd
                step = _trace_step(
                    run_id,
                    4,
                    provenance,
                    "phase4.local_scope",
                    f"04-local/{objective['source_id']}__{objective['id']}/tdd.json",
                )
                step["source_id"] = objective["source_id"]
                step["objective_id"] = objective["id"]
                step["tdd_path"] = f"04-local/{objective['source_id']}__{objective['id']}/tdd.json"
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
                        "source_type": objective["source_type"],
                        **(
                            {"discovered_by": objective["discovered_by"]}
                            if objective.get("discovered_by") is not None
                            else {}
                        ),
                        "health": {"ok": 0, "failed": 0, "yield": 0},
                    }
                    for objective in selected_objectives
                ],
            )
            kwargs = {}
            if capture is not None:
                kwargs["capture"] = capture
            if fetch is not None:
                kwargs["fetch"] = fetch
            decision_start = len(getattr(decision, "call_log", []))
            execution_order = [
                *[item for item in selected_objectives if not tdds[item["id"]].get("membership")],
                *[item for item in selected_objectives if tdds[item["id"]].get("membership")],
            ]
            executions = execute_objectives(
                case_dir=case_dir,
                objectives=selected_objectives,
                ontology=ontology,
                tdds=tdds,
                lake=lake,
                run_id=run_id,
                decision=decision,
                store=store,
                provenance=provenance,
                feed=feed,
                browser_client=browser_client,
                browser_steps_root=browser_steps_root,
                browser_captures_root=browser_captures_root,
                **kwargs,
            )
            _publish_decision_calls(feed, trace, decision, decision_start, run_id, 5)
            execution_by_id = {
                objective["id"]: execution
                for objective, execution in zip(execution_order, executions, strict=True)
            }
            for execution in executions:
                _publish_steps(feed, execution.trace)
                trace.extend(execution.trace)
            observations = store.list_for_run(run_id)
            _write_silver_cache(case_id, run_id, observations)
            for execution in executions:
                proof_jobs = [job for job in execution.sandbox_jobs if "proof" in job]
                for job in execution.sandbox_jobs:
                    if "checkpoints" in job:
                        append_job_record(lake, case_id, job)
                    elif "proof" in job:
                        ids = (
                            [item.value_id for item in execution.observations]
                            if proof_jobs and job is proof_jobs[-1]
                            else []
                        )
                        append_job_record(lake, case_id, build_job_record(job, value_ids=ids))
            shapes = case_dir / ontology["shacl_path"]
            classification_start = len(getattr(decision, "call_log", []))
            refined = refine_observations(
                observations,
                ontology=ontology,
                generated_by=provenance,
                shapes_ttl=shapes,
                decision=decision if provenance["backend"] == "vultr" else None,
            )
            _publish_decision_calls(feed, trace, decision, classification_start, run_id, 5)
            dod_queries = load_json(case_dir / "02-ontology/dod-queries.json")
            export_trace = _persisted_run_trace(lake, case_id, run_id, trace)
            metrics = export_run(
                lake,
                case_dir,
                case_id,
                run_id,
                refined.entities,
                ontology=ontology,
                dod_queries=dod_queries,
                trace=export_trace,
                generated_by=provenance,
                preview=preview_past_checkpoints,
                decisions_by_backend=getattr(decision, "decisions_by_backend", None),
                taxonomy_classified=refined.classified,
            )
            outer = decide_outer_gap(
                metrics=metrics,
                dod_queries=dod_queries,
                ontology=ontology,
                objectives=objectives,
                decision=decision,
                provenance=provenance,
                trace=export_trace,
                budget_usd=budget_usd,
            )
            outer_step = outer_trace_step(run_id, outer)
            feed.append_step(outer_step)
            trace.append(outer_step)
            metrics = export_run(
                lake,
                case_dir,
                case_id,
                run_id,
                refined.entities,
                ontology=ontology,
                dod_queries=dod_queries,
                trace=[*export_trace, outer_step],
                generated_by=provenance,
                preview=preview_past_checkpoints,
                decisions_by_backend=getattr(decision, "decisions_by_backend", None),
                taxonomy_classified=refined.classified,
            )
            sources = [
                {
                    "source_id": objective["source_id"],
                    "source_type": objective["source_type"],
                    **(
                        {"discovered_by": objective["discovered_by"]}
                        if objective.get("discovered_by") is not None
                        else {}
                    ),
                    "format": execution.format,
                    "health": {
                        "ok": 1,
                        "failed": 0,
                        "yield": len(execution.observations),
                    },
                }
                for objective in selected_objectives
                for execution in [execution_by_id[objective["id"]]]
            ]
            if outer.reopen == 2:
                _request_ontology_gap_review(
                    case_dir,
                    outer.iteration,
                    [gap.criterion_id for gap in outer.gaps],
                    ontology["generated_by"],
                )
                pending = "ontology"
            elif outer.reopen == 3:
                _reopen_discovery(case_dir, outer.iteration)
                if not mock and isinstance(search_client, DiscoveryLoop):
                    missing_property_ids = {
                        property_id for gap in outer.gaps for property_id in gap.properties
                    }
                    search_client.request_site_graph_refresh(missing_property_ids)
                reopen_phase = 3
            elif outer.reopen == 4:
                for objective in selected_objectives:
                    if _gaps_for_objective([gap.public_summary() for gap in outer.gaps], objective):
                        _reopen_local_scope(case_dir, objective, outer.iteration)
                reopen_phase = 4
            outer_paused = outer.stop_reason in {"budget", "human"}
            if reopen_phase is not None:
                feed.update_status(
                    state="running",
                    phase=reopen_phase,
                    checkpoint_pending=pending,
                    metrics=metrics,
                    sources=sources,
                )
            else:
                feed.update_status(
                    state="paused" if pending or outer_paused else "done",
                    phase=5,
                    checkpoint_pending=pending,
                    metrics=metrics,
                    sources=sources,
                )
                if pending:
                    _report_pause(
                        pending,
                        case_dir / ("02-ontology" if pending == "ontology" else "01-scope"),
                        mock,
                    )
                elif outer_paused:
                    print(
                        f"state=paused open_dod_gaps={len(outer.gaps)} reason={outer.stop_reason}"
                    )
                else:
                    print(f"state=done checkpoint_pending=none open_dod_gaps={len(outer.gaps)}")
        except SandboxLimitExceeded as exc:
            fresh = [
                step for step in exc.trace if step["step_id"] not in {s["step_id"] for s in trace}
            ]
            _publish_steps(feed, fresh)
            trace.extend(fresh)
            if exc.result is not None:
                append_job_record(lake, case_id, build_job_record(exc.result))
            phase = feed.current_status["phase"] if feed.current_status else 1
            _publish_unreported_decisions(feed, trace, decision, run_id, phase)
            feed.update_status(state="failed", phase=phase, checkpoint_pending=pending)
            raise
        except SandboxParseError as exc:
            fresh = [
                step for step in exc.trace if step["step_id"] not in {s["step_id"] for s in trace}
            ]
            _publish_steps(feed, fresh)
            trace.extend(fresh)
            jobs = getattr(exc, "sandbox_jobs", [exc.job_record])
            for job in jobs:
                if "checkpoints" in job:
                    append_job_record(lake, case_id, job)
                elif "proof" in job:
                    append_job_record(lake, case_id, build_job_record(job))
            phase = feed.current_status["phase"] if feed.current_status else 5
            _publish_unreported_decisions(feed, trace, decision, run_id, phase)
            feed.update_status(state="failed", phase=phase, checkpoint_pending=pending)
            raise
        except CaptureBlocked as exc:
            if exc.trace:
                dispatch = next(
                    (
                        step
                        for step in exc.trace
                        if step.get("evaluated", {}).get("proof_checkpoint") == "dispatch_result"
                    ),
                    exc.trace[0],
                )
                dispatch["event"] = "hard_stop"
                dispatch["evaluated"] = {
                    **dispatch.get("evaluated", {}),
                    "reason": dispatch.get("evaluated", {}).get("reason", "blocked: dispatch"),
                }
            _publish_steps(feed, exc.trace)
            phase = feed.current_status["phase"] if feed.current_status else 1
            _publish_unreported_decisions(feed, trace, decision, run_id, phase)
            feed.update_status(state="failed", phase=phase, checkpoint_pending=pending)
            raise
        except ApprovalArtifactMismatch as exc:
            phase = feed.current_status["phase"] if feed.current_status else 1
            feed.update_status(
                state="paused",
                phase=phase,
                checkpoint_pending=exc.checkpoint or pending,
                reason=str(exc),
            )
            print(f"state=paused reason={exc}")
            return 3
        except Exception:
            phase = feed.current_status["phase"] if feed.current_status else 1
            _publish_unreported_decisions(feed, trace, decision, run_id, phase)
            feed.update_status(state="failed", phase=phase, checkpoint_pending=pending)
            raise
    if reopen_phase is not None:
        return run_case(
            original,
            from_phase=reopen_phase,
            to_phase=to_phase,
            run_id=run_id,
            budget_usd=budget_usd,
            preview_past_checkpoints=preview_past_checkpoints,
            decision=decision,
            search_client=search_client,
            capture=capture,
            fetch=fetch,
            browser_client=browser_client,
            browser_steps_root=browser_steps_root,
            browser_captures_root=browser_captures_root,
            lake=lake,
            store=store,
        )
    return 3 if pending or outer_paused else 0


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
    if provenance["backend"] == "vultr":
        marker = case_dir / "02-ontology/APPROVED"
        if not marker.is_file():
            raise RuntimeError("current ontology approval required before live refine")
        approval = load_verified_approval(
            marker, case_dir, ["02-ontology/ontology.json"], "ontology"
        )
        if approval.get("decision", "approve") != "approve":
            raise RuntimeError("current ontology is not approved for live refine")
    store = silver_store_from_env()
    ontology = load_json(case_dir / "02-ontology/ontology.json")
    observations = store.list_for_run(run_id) or _read_silver_cache(case_id, run_id)
    trace_key = f"runs/{case_id}/{run_id}/trace.live.jsonl"
    previous_trace_bytes = lake.read_key(trace_key)
    trace = [json.loads(line) for line in previous_trace_bytes.splitlines()]
    replay = replay_bronze_observations(
        case_dir=case_dir,
        lake=lake,
        run_id=run_id,
        trace=trace,
        ontology=ontology,
        provenance=provenance,
    )
    for record in replay.parse_job_records:
        append_job_record(lake, case_id, record)
    trace, parse_additions = _merge_trace_steps(trace, replay.parse_trace_steps)
    if parse_additions:
        lake.write_key(trace_key, _append_trace_bytes(previous_trace_bytes, parse_additions))
        previous_trace_bytes = lake.read_key(trace_key)
    observations.extend(replay.observations)
    if not observations:
        raise RuntimeError(f"no silver observations for {run_id}; refusing to overwrite gold")
    decision = None
    if provenance["backend"] == "vultr":
        if not os.environ.get("ONTOFILL_GATEWAY_TOKEN"):
            raise RuntimeError(
                "live refine requires ONTOFILL_GATEWAY_TOKEN for the screened gateway"
            )
        decision = VultrDecisionClient.from_env()
    decision_start = len(getattr(decision, "call_log", []))
    refined = refine_observations(
        observations,
        ontology=ontology,
        generated_by=provenance,
        shapes_ttl=case_dir / ontology["shacl_path"],
        decision=decision,
    )
    if decision is not None:
        with RunFeed(lake, case_id, run_id, provenance, start_heartbeat=False) as feed:
            _publish_decision_calls(feed, trace, decision, decision_start, run_id, 5)
    # Classification calls remain in the audit trail even if replay export fails.
    # Rollback removes only replay steps that have no exported values.
    previous_trace_bytes = lake.read_key(trace_key)
    export_trace, replay_additions = _merge_trace_steps(trace, replay.trace_steps)
    if replay_additions:
        lake.write_key(trace_key, _append_trace_bytes(previous_trace_bytes, replay_additions))
    try:
        export_run(
            lake,
            case_dir,
            case_id,
            run_id,
            refined.entities,
            ontology=ontology,
            dod_queries=load_json(case_dir / "02-ontology/dod-queries.json"),
            trace=export_trace,
            generated_by=provenance,
            preview=status.get("preview", False),
            taxonomy_classified=refined.classified,
        )
    except Exception:
        if replay_additions:
            try:
                lake.write_key(trace_key, previous_trace_bytes)
            except Exception as restore_error:
                raise RuntimeError(
                    "failed to restore trace after bronze replay export failure"
                ) from restore_error
        raise
    return 0


def export_case(case_dir: Path, *, run_id: str | None = None) -> int:
    return refine_case(case_dir, run_id=run_id)
