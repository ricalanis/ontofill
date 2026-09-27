"""Adopt an engine-captured P3 document into P5 without another network request."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema.exceptions import ValidationError

from ontofill.case.checkpoints import load_json, load_verified_approval
from ontofill.lake import FileLake, S3Lake
from ontofill.sandbox.jobs import validate_job_record

_BRONZE_KEY = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


class BronzeAdoptionRefused(ValueError):
    """A P3 bronze reference cannot establish the required same-run lineage."""


def adopt_p3_document(
    *,
    case_dir: Path,
    lake: FileLake | S3Lake,
    case_id: str,
    run_id: str,
    objective: dict,
    tdd_path: str,
    provenance: dict,
) -> dict:
    """Return a P5 page-shaped receipt only after capture, approval and pod proof checks."""
    source_id = objective.get("source_id")
    key = objective.get("document_key")
    url = objective.get("source_url")
    if (
        not isinstance(source_id, str)
        or not _SOURCE_ID.fullmatch(source_id)
        or not isinstance(key, str)
        or not _BRONZE_KEY.fullmatch(key)
        or objective.get("confirmed_bronze_key") != key
        or not isinstance(url, str)
        or urlsplit(url).scheme not in {"http", "https"}
    ):
        raise BronzeAdoptionRefused("invalid document objective")
    directory = case_dir / "03-fanout/sources" / source_id
    path = directory / (
        "capture.json" if (directory / "capture.json").is_file() else "candidate.json"
    )
    try:
        manifest = load_json(path)
        metadata = lake.read_metadata(key)
    except (OSError, ValueError, KeyError) as exc:
        raise BronzeAdoptionRefused("missing document manifest or sidecar") from exc
    if (
        manifest.get("source_id") != source_id
        or manifest.get("url") != url
        or manifest.get("capture_key") != key
        or manifest.get("document_key") != key
        or not lake.exists(key)
    ):
        raise BronzeAdoptionRefused("document manifest is not bound to objective")
    if (directory / "APPROVED").is_file():
        try:
            approved = load_verified_approval(
                directory / "APPROVED",
                case_dir,
                [f"03-fanout/sources/{source_id}/candidate.json"],
                "source",
            )
            packet = load_json(directory / "candidate.json")
        except (OSError, ValueError, KeyError) as exc:
            raise BronzeAdoptionRefused("source approval is stale") from exc
        if (
            approved.get("decision", "approve") != "approve"
            or approved.get("source_fingerprint") != objective.get("source_fingerprint")
            or packet.get("fingerprint") != approved.get("source_fingerprint")
        ):
            raise BronzeAdoptionRefused("source approval does not authorize document")
    elif manifest.get("authority") != "auto" or manifest.get("fingerprint") != objective.get(
        "source_fingerprint"
    ):
        raise BronzeAdoptionRefused("document source lacks authority")
    landing_url = manifest.get("landing_url") or url
    if (
        metadata.get("source_id") != source_id
        or metadata.get("url") != landing_url
        or not isinstance(metadata.get("captured_at"), str)
        or not isinstance(metadata.get("step_id"), str)
        or not isinstance(metadata.get("content_type"), str)
    ):
        raise BronzeAdoptionRefused("document sidecar is not bound to source")
    trace_key = f"runs/{case_id}/{run_id}/trace.live.jsonl"
    jobs_key = f"runs/{case_id}/{run_id}/jobs.jsonl"
    try:
        trace_lines = lake.read_key(trace_key).splitlines()
        job_lines = lake.read_key(jobs_key).splitlines()
        trace = [json.loads(line) for line in trace_lines]
        jobs = [json.loads(line) for line in job_lines]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise BronzeAdoptionRefused("same-run capture proof is missing") from exc
    capture = next(
        (
            step
            for step in trace
            if step.get("step_id") == metadata["step_id"]
            and step.get("run_id") == run_id
            and step.get("phase") == 3
            and step.get("source_id") == source_id
            and step.get("ts") == metadata["captured_at"]
            and step.get("observed", {}).get("url") == metadata["url"]
            and step.get("evaluated", {}).get("status") == "captured"
            and key in step.get("executed", {}).values()
        ),
        None,
    )
    if capture is None:
        raise BronzeAdoptionRefused("no matching same-run P3 capture step")
    latest_jobs = {job.get("job_id"): job for job in jobs if isinstance(job, dict)}
    matching_jobs = [
        job
        for job in latest_jobs.values()
        if job.get("step_id") == capture["step_id"]
        and job.get("run_id") == run_id
        and job.get("source_id") == source_id
    ]
    if not matching_jobs:
        raise BronzeAdoptionRefused("no matching six-checkpoint capture job")
    try:
        for job in matching_jobs:
            validate_job_record(job)
    except (ValidationError, ValueError, KeyError, TypeError) as exc:
        raise BronzeAdoptionRefused("invalid capture job proof") from exc
    proof = matching_jobs[-1]
    checkpoints = proof["checkpoints"]
    if (
        proof.get("outcome", {}).get("status") != "completed"
        or proof.get("outcome", {}).get("document_key") != key
        or checkpoints["host"].get("runtime") != "runsc"
        or checkpoints["task"].get("requested", {}).get("url") != url
        or checkpoints["task"].get("result", {}).get("url") != landing_url
        or checkpoints["task"].get("result", {}).get("document_key") != key
        or not all(
            checkpoints[name].get("ok") is True
            for name in ("host", "task", "where", "secrets", "teardown")
        )
        or not checkpoints["isolation"].get("probes")
        or any(probe.get("result") != "BLOCKED" for probe in checkpoints["isolation"]["probes"])
    ):
        raise BronzeAdoptionRefused("capture job did not establish containment")
    screenshot_key = manifest.get("screenshot_key")
    screenshot_source_id = source_id
    screenshot_step_id = capture["step_id"]
    link = manifest.get("link_provenance")
    if not isinstance(screenshot_key, str) and isinstance(link, dict):
        screenshot_source_id = link.get("parent_source_id")
        screenshot_step_id = link.get("step_id")
        if (
            not isinstance(screenshot_source_id, str)
            or not _SOURCE_ID.fullmatch(screenshot_source_id)
            or not isinstance(screenshot_step_id, str)
            or not isinstance(link.get("parent_capture_key"), str)
            or not _BRONZE_KEY.fullmatch(link["parent_capture_key"])
        ):
            raise BronzeAdoptionRefused("parent page lineage is invalid")
        parent_path = case_dir / "03-fanout/sources" / str(screenshot_source_id) / "candidate.json"
        try:
            parent = load_json(parent_path)
        except (OSError, ValueError, KeyError) as exc:
            raise BronzeAdoptionRefused("parent page screenshot is missing") from exc
        screenshot_key = parent.get("screenshot_key")
        parent_capture = next(
            (
                step
                for step in trace
                if step.get("step_id") == screenshot_step_id
                and step.get("run_id") == run_id
                and step.get("phase") == 3
                and step.get("source_id") == screenshot_source_id
                and step.get("executed", {}).get("html_key") == link.get("parent_capture_key")
                and step.get("observed", {}).get("url") == link.get("parent_page_url")
            ),
            None,
        )
        if parent_capture is None:
            raise BronzeAdoptionRefused("parent page proof is missing")
    if not isinstance(screenshot_key, str) or not _BRONZE_KEY.fullmatch(screenshot_key):
        raise BronzeAdoptionRefused("document has no evidenced screenshot")
    if not lake.exists(screenshot_key):
        raise BronzeAdoptionRefused("document screenshot bronze is missing")
    try:
        screenshot_metadata = lake.read_metadata(screenshot_key)
    except (OSError, ValueError, KeyError) as exc:
        raise BronzeAdoptionRefused("screenshot sidecar is missing") from exc
    if (
        screenshot_metadata.get("source_id") != screenshot_source_id
        or screenshot_metadata.get("step_id") != screenshot_step_id
        or not isinstance(screenshot_metadata.get("captured_at"), str)
    ):
        raise BronzeAdoptionRefused("screenshot is not bound to source")
    step = {
        "step_id": f"step:{uuid.uuid4().hex}",
        "run_id": run_id,
        "phase": 5,
        "source_id": source_id,
        "objective_id": objective["id"],
        "tdd_path": tdd_path,
        "mode": "D0",
        "observed": {
            "url": landing_url,
            "status": 200,
            "captured_at": metadata["captured_at"],
            "screenshot_source_id": screenshot_source_id,
            "screenshot_step_id": screenshot_step_id,
            "screenshot_captured_at": screenshot_metadata["captured_at"],
        },
        "requested": {"tool": "bronze.adopt", "fetch": "bytes", "network_request": False},
        "executed": {"bronze_key": key, "network_request": False},
        "evaluated": {"status": "captured", "capture_job_id": proof["job_id"]},
        "parent_step_id": capture["step_id"],
        "value_ids": [],
        "ts": datetime.now(UTC).isoformat(),
        "generated_by": provenance,
        "screenshot_key": screenshot_key,
    }
    return {
        "url": landing_url,
        "status": 200,
        "document_key": key,
        "document_content_type": metadata["content_type"],
        "screenshot_key": screenshot_key,
        "trace": [step],
    }
