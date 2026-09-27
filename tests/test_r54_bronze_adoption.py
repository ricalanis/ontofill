"""P5 adopts only same-run, six-checkpoint P3 document captures."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

from profiler import profile_bytes

from ontofill.inference import RecordedDecisionClient, generated_by
from ontofill.lake import FileLake
from ontofill.phases.p5_execute import execute_objective
from ontofill.refiner import MemorySilverStore
from ontofill.refiner.bronze_replay import _metadata_matches_trace, _screenshot_key
from ontofill.sandbox.jobs import build_job_record
from tests.profiler_helpers import make_pdf
from tests.r17_helpers import SyntheticParseExecutor
from tests.test_jobs import _capture_result

RUN_ID = "mock-adoption"
CASE_ID = "library-case"
SOURCE_ID = "source-library"
URL = "https://records.example.test/catalog.pdf"
CAPTURED_AT = "2026-09-27T10:00:00Z"


class ProfiledExecutor(SyntheticParseExecutor):
    def run(self, payload, *, kind, format, max_rows, base_url, limits):
        execution = super().run(
            payload, kind=kind, format=format, max_rows=max_rows, base_url=base_url, limits=limits
        )
        if kind == "pdf":
            return replace(
                execution, output={**execution.output, "profile": profile_bytes(payload)}
            )
        return execution


def _case(tmp_path):
    case_dir = tmp_path / "case"
    source_dir = case_dir / "03-fanout/sources" / SOURCE_ID
    source_dir.mkdir(parents=True)
    lake = FileLake(tmp_path / "lake")
    pdf = make_pdf([["Entity ID       Public name", "A-1             Alpha"]])
    metadata = {
        "content_type": "application/pdf",
        "url": URL,
        "captured_at": CAPTURED_AT,
        "source_id": SOURCE_ID,
        "step_id": "step:p3-document",
    }
    key = lake.put_bytes(pdf, metadata)
    screenshot = lake.put_bytes(b"synthetic screenshot", {**metadata, "content_type": "image/png"})
    manifest = {
        "source_id": SOURCE_ID,
        "url": URL,
        "landing_url": URL,
        "fingerprint": "fingerprint-library",
        "authority": "auto",
        "capture_key": key,
        "document_key": key,
        "screenshot_key": screenshot,
        "document_content_type": "application/pdf",
    }
    (source_dir / "candidate.json").write_text(json.dumps(manifest))
    provenance = {"backend": "recorded", "model": "synthetic", "at": CAPTURED_AT}
    capture_step = {
        "step_id": "step:p3-document",
        "run_id": RUN_ID,
        "phase": 3,
        "source_id": SOURCE_ID,
        "objective_id": None,
        "tdd_path": "03-fanout/discovery-loop.json",
        "mode": "S1",
        "observed": {"url": URL, "status": 200},
        "requested": {"url": URL},
        "executed": {"document_key": key, "bronze_key": key},
        "evaluated": {"status": "captured"},
        "parent_step_id": None,
        "value_ids": [],
        "ts": CAPTURED_AT,
        "generated_by": provenance,
        "screenshot_key": screenshot,
    }
    lake.write_key(
        f"runs/{CASE_ID}/{RUN_ID}/trace.live.jsonl", (json.dumps(capture_step) + "\n").encode()
    )
    capture_job = _capture_result()
    for step in capture_job["trace"]:
        step.update(step_id="step:p3-document", run_id=RUN_ID, source_id=SOURCE_ID)
        step["requested"]["url"] = URL
        step["generated_by"] = provenance
    capture_job["proof"]["host_check"]["runtime"] = "runsc"
    capture_job["proof"]["dispatch_result"].update(url=URL, document_key=key)
    capture_job["document_key"] = key
    capture_job["document_content_type"] = "application/pdf"
    capture_job["document_size_bytes"] = len(pdf)
    record = build_job_record(capture_job)
    lake.write_key(f"runs/{CASE_ID}/{RUN_ID}/jobs.jsonl", (json.dumps(record) + "\n").encode())
    objective = {
        "source_id": SOURCE_ID,
        "id": "objective-library",
        "source_url": URL,
        "source_type": "directory",
        "source_fingerprint": "fingerprint-library",
        "confirmed_bronze_key": key,
        "document_key": key,
    }
    return case_dir, lake, objective, provenance, key


def _run(case_dir, lake, objective, provenance):
    decision = RecordedDecisionClient(
        {
            "phase5.map_columns": [
                {
                    "class_id": "library",
                    "columns": [
                        {"header": "Entity ID", "property_id": "id"},
                        {"header": "Public name", "property_id": "title"},
                    ],
                }
            ]
        }
    )
    return execute_objective(
        case_dir=case_dir,
        objective=objective,
        ontology={
            "classes": [{"id": "library", "identifier_property": "id", "title_property": "title"}],
            "properties": [
                {"id": "id", "domain": "library", "datatype": "string"},
                {"id": "title", "domain": "library", "datatype": "string"},
            ],
        },
        tdd={"allowed_domains": ["records.example.test"], "target_fields": ["id", "title"]},
        lake=lake,
        run_id=RUN_ID,
        decision=decision,
        store=MemorySilverStore(),
        provenance=generated_by(decision),
        capture=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("browsed")),
        fetch=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fetched")),
        feed=SimpleNamespace(case_id=CASE_ID),
        parse_executor=ProfiledExecutor(),
    )


def test_same_run_document_yields_values_without_browsing(tmp_path) -> None:
    case_dir, lake, objective, provenance, key = _case(tmp_path)

    result = _run(case_dir, lake, objective, provenance)

    assert not result.failed
    assert {item.property_id: item.value for item in result.observations} == {
        "id": "A-1",
        "title": "Alpha",
    }
    assert result.trace[0]["requested"]["tool"] == "bronze.adopt"
    assert result.trace[0]["parent_step_id"] == "step:p3-document"
    assert all(item.evidence["bronze_key"] == key for item in result.observations)


def test_missing_job_refuses_without_browsing_or_values(tmp_path) -> None:
    case_dir, lake, objective, provenance, _key = _case(tmp_path)
    lake.write_key(f"runs/{CASE_ID}/{RUN_ID}/jobs.jsonl", b"")

    result = _run(case_dir, lake, objective, provenance)

    assert result.failed and result.observations == []
    assert result.failure_reason.startswith("invalid_bronze_adoption:")


def test_document_uses_same_run_parent_page_screenshot(tmp_path) -> None:
    case_dir, lake, objective, provenance, key = _case(tmp_path)
    source_dir = case_dir / "03-fanout/sources" / SOURCE_ID
    manifest = json.loads((source_dir / "candidate.json").read_text())
    manifest["screenshot_key"] = None
    parent_id = "source-parent"
    parent_url = "https://records.example.test/index"
    parent_meta = {
        "content_type": "text/html",
        "url": parent_url,
        "captured_at": CAPTURED_AT,
        "source_id": parent_id,
        "step_id": "step:p3-parent",
    }
    parent_key = lake.put_bytes(b"<a>catalog</a>", parent_meta)
    parent_shot = lake.put_bytes(b"parent screenshot", {**parent_meta, "content_type": "image/png"})
    parent_dir = case_dir / "03-fanout/sources" / parent_id
    parent_dir.mkdir(parents=True)
    (parent_dir / "candidate.json").write_text(
        json.dumps({"source_id": parent_id, "screenshot_key": parent_shot})
    )
    manifest["link_provenance"] = {
        "parent_source_id": parent_id,
        "parent_page_url": parent_url,
        "parent_capture_key": parent_key,
        "step_id": "step:p3-parent",
    }
    (source_dir / "candidate.json").write_text(json.dumps(manifest))
    trace_key = f"runs/{CASE_ID}/{RUN_ID}/trace.live.jsonl"
    parent_step = {
        "step_id": "step:p3-parent",
        "run_id": RUN_ID,
        "phase": 3,
        "source_id": parent_id,
        "observed": {"url": parent_url, "status": 200},
        "executed": {"html_key": parent_key},
        "evaluated": {"status": "captured"},
        "ts": CAPTURED_AT,
        "generated_by": provenance,
    }
    lake.write_key(
        trace_key,
        (json.dumps(parent_step) + "\n").encode() + lake.read_key(trace_key),
    )

    result = _run(case_dir, lake, objective, provenance)

    assert not result.failed
    assert {item.property_id for item in result.observations} == {"id", "title"}
    assert all(item.evidence["bronze_key"] == key for item in result.observations)
    assert all(item.evidence["screenshot_key"] == parent_shot for item in result.observations)
    adoption = result.trace[0]
    assert _metadata_matches_trace(lake.read_metadata(key), adoption)
    map_index = next(
        i
        for i, step in enumerate(result.trace)
        if step.get("requested", {}).get("tool") == "column.map"
    )
    assert (
        _screenshot_key(
            result.trace,
            map_index,
            SOURCE_ID,
            "objective-library",
            f"04-local/{SOURCE_ID}__objective-library/tdd.json",
            lake,
        )
        == parent_shot
    )


def test_reviewed_document_requires_digest_bound_approval(tmp_path) -> None:
    case_dir, lake, objective, provenance, _key = _case(tmp_path)
    directory = case_dir / "03-fanout/sources" / SOURCE_ID
    original = directory / "candidate.json"
    manifest = json.loads(original.read_text())
    manifest["authority"] = "review"
    manifest["fingerprint"] = "b" * 64
    (directory / "capture.json").write_text(json.dumps(manifest))
    packet = {"fingerprint": "a" * 64}
    original.write_text(json.dumps(packet))
    objective["source_fingerprint"] = packet["fingerprint"]
    relative = original.relative_to(case_dir).as_posix()
    marker = {
        "approver": "Synthetic reviewer",
        "date": "2026-09-27",
        "checkpoint": "source",
        "identity_source": "local",
        "source_fingerprint": packet["fingerprint"],
        "artifact_sha256": {relative: hashlib.sha256(original.read_bytes()).hexdigest()},
    }
    (directory / "APPROVED").write_text(json.dumps(marker))
    assert not _run(case_dir, lake, objective, provenance).failed

    original.write_text(json.dumps({"fingerprint": "c" * 64}))
    result = _run(case_dir, lake, objective, provenance)
    assert result.failed and result.observations == []
    assert "source approval is stale" in result.failure_reason
