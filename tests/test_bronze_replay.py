"""Trace-bounded bronze replay preserves typed values and export lineage."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from ontofill import workflow
from ontofill.case.checkpoints import ApprovalArtifactMismatch
from ontofill.lake import FileLake
from ontofill.refiner import MemorySilverStore, export_run, refine_observations
from ontofill.refiner.bronze_replay import replay_bronze_observations
from ontofill.sandbox import parse as parse_module
from tests.approval_support import bind_approval
from tests.r17_helpers import SyntheticParseExecutor
from tests.test_refiner import RECORDED, SOURCE_ID, URL, dod_queries, ontology, write_lineage


@pytest.fixture(autouse=True)
def synthetic_parse_pod(monkeypatch):
    monkeypatch.setattr(parse_module, "DockerParseExecutor", SyntheticParseExecutor)


RUN_ID = "mock-bronze-replay"
OBJECTIVE_ID = "read-catalog"
TDD_PATH = f"04-local/{SOURCE_ID}__{OBJECTIVE_ID}/tdd.json"


def _step(
    *,
    step_id: str,
    source_id: str,
    objective_id: str,
    tdd_path: str,
    requested: dict,
    executed: dict,
    observed: dict | None = None,
    parent_step_id: str | None = None,
    screenshot_key: str | None = None,
) -> dict:
    step = {
        "step_id": step_id,
        "run_id": RUN_ID,
        "phase": 5,
        "source_id": source_id,
        "objective_id": objective_id,
        "tdd_path": tdd_path,
        "mode": "D0",
        "observed": observed or {},
        "requested": requested,
        "executed": executed,
        "evaluated": {"status": "captured"},
        "parent_step_id": parent_step_id,
        "value_ids": [],
        "ts": "2026-01-01T00:00:00Z",
        "generated_by": RECORDED,
    }
    if screenshot_key is not None:
        step["screenshot_key"] = screenshot_key
    return step


def _recorded_case(tmp_path, *, duplicate_property_label: bool = False):
    case_dir = tmp_path / "case"
    historical_model = ontology()
    model = deepcopy(historical_model)
    model["version"] = "v2"
    model["properties"].append(
        {
            "id": "service_zone",
            "label": "Service Zone",
            "domain": "Book",
            "datatype": "string",
            "dod": False,
            "order": 10,
            "description": "The service zone listed for this book.",
            "aligned_to": None,
        }
    )
    if duplicate_property_label:
        model["properties"].append(
            {
                "id": "zone_alias",
                "label": "Service Zone",
                "domain": "Book",
                "datatype": "string",
                "dod": False,
                "order": 11,
                "description": "A deliberately ambiguous duplicate label.",
                "aligned_to": None,
            }
        )
    queries = dod_queries()
    write_lineage(case_dir, historical_model, queries, RECORDED)
    current_ontology_path = case_dir / "02-ontology/ontology.json"
    current_ontology_path.write_text(json.dumps(model), encoding="utf-8")
    shape_path = case_dir / model["shacl_path"]
    shape_path.parent.mkdir(parents=True, exist_ok=True)
    shape_path.write_text("", encoding="utf-8")
    objectives_path = case_dir / "03-fanout/objectives.json"
    objectives = json.loads(objectives_path.read_text(encoding="utf-8"))
    objectives["objectives"][0]["source_type"] = "catalog"
    objectives_path.write_text(json.dumps(objectives), encoding="utf-8")

    macro_path = "05-execute/macros/catalog-recorded.json"
    macro_file = case_dir / macro_path
    macro_file.parent.mkdir(parents=True, exist_ok=True)
    # This historical mapping predates service_zone and has no column for it.
    macro_file.write_text(
        json.dumps(
            {
                "source_id": SOURCE_ID,
                "class_id": "Book",
                "columns": [
                    {"header": "book_id", "property_id": "book_id"},
                    {"header": "title", "property_id": "title"},
                ],
            }
        ),
        encoding="utf-8",
    )

    lake = FileLake(tmp_path / "lake")
    screenshot_key = lake.put_bytes(
        b"synthetic browser screenshot",
        {
            "content_type": "image/png",
            "url": URL,
            "captured_at": "2026-01-01T00:00:00Z",
            "source_id": SOURCE_ID,
            "step_id": "step:page",
        },
    )
    file_url = "https://example.invalid/catalog.csv"
    bronze_key = lake.put_bytes(
        b"book_id,title,Service Zone\nB-1,The First,North\n",
        {
            "content_type": "text/csv",
            "url": file_url,
            "captured_at": "2026-01-01T00:00:00Z",
            "source_id": SOURCE_ID,
            "step_id": "step:file",
        },
    )
    trace = [
        _step(
            step_id="step:page",
            source_id=SOURCE_ID,
            objective_id=OBJECTIVE_ID,
            tdd_path=TDD_PATH,
            requested={"url": URL},
            executed={
                "html_key": lake.put_bytes(b"<html>catalog</html>"),
                "screenshot_key": screenshot_key,
            },
            observed={"url": URL, "status": 200},
            screenshot_key=screenshot_key,
        ),
        _step(
            step_id="step:file",
            source_id=SOURCE_ID,
            objective_id=OBJECTIVE_ID,
            tdd_path=TDD_PATH,
            requested={"url": file_url, "fetch": "bytes"},
            executed={"bronze_key": bronze_key},
            observed={"url": file_url, "status": 200},
        ),
        _step(
            step_id="step:map",
            source_id=SOURCE_ID,
            objective_id=OBJECTIVE_ID,
            tdd_path=TDD_PATH,
            requested={"tool": "column.map"},
            executed={"macro_path": macro_path},
            parent_step_id="step:file",
        ),
    ]
    return case_dir, lake, model, queries, bronze_key, screenshot_key, trace


def _trace_payload(trace: list[dict]) -> bytes:
    return b"".join(
        (json.dumps(step, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for step in trace
    )


def _install_refine_case(monkeypatch, case_dir, lake, trace: list[dict]) -> tuple[str, bytes]:
    case_id = "library-case"
    trace_key = f"runs/{case_id}/{RUN_ID}/trace.live.jsonl"
    original_trace = _trace_payload(trace)
    lake.write_key(trace_key, original_trace)
    monkeypatch.setattr(
        workflow,
        "_existing_run",
        lambda _case, _run: (
            case_id,
            lake,
            RUN_ID,
            {"generated_by": RECORDED, "preview": True},
        ),
    )
    monkeypatch.setattr(workflow, "_scratch_case", lambda _case, _run: (case_dir, lake))
    monkeypatch.setattr(workflow, "silver_store_from_env", MemorySilverStore)
    return trace_key, original_trace


def test_replay_fills_new_ontology_property_and_exports_trace_lineage(tmp_path) -> None:
    case_dir, lake, model, queries, bronze_key, screenshot_key, trace = _recorded_case(tmp_path)
    historical_objectives = json.loads(
        (case_dir / "03-fanout/objectives.json").read_text(encoding="utf-8")
    )
    historical_objectives["objectives"][0].update(
        {
            "authority_tier": "primary",
            "publisher_of_record": {
                "kind": "Example Library Board",
                "domain": "library.example.test",
                "tier": "primary",
                "basis": "approved_policy",
            },
        }
    )
    (case_dir / "03-fanout/objectives.json").write_text(
        json.dumps(historical_objectives), encoding="utf-8"
    )
    historical_tdd = json.loads((case_dir / TDD_PATH).read_text(encoding="utf-8"))
    historical_local_prd = json.loads(
        (case_dir / f"04-local/{SOURCE_ID}__{OBJECTIVE_ID}/local-prd.json").read_text(
            encoding="utf-8"
        )
    )
    assert "service_zone" not in historical_objectives["objectives"][0]["target_fields"]
    assert "service_zone" not in historical_tdd["target_fields"]
    assert "service_zone" not in historical_local_prd["target_fields"]
    assert historical_objectives["ontology_version"] == "v1"
    assert model["version"] == "v2"

    replay = replay_bronze_observations(
        case_dir=case_dir,
        lake=lake,
        run_id=RUN_ID,
        trace=trace,
        ontology=model,
        provenance=RECORDED,
    )

    added = [item for item in replay.observations if item.property_id == "service_zone"]
    assert [(item.entity_id, item.value) for item in added] == [
        ("Book:3e499e752620c6a90ed59a20", "North")
    ]
    assert all(
        (item.authority_tier, item.publisher_id) == ("primary", "name:example library board")
        for item in replay.observations
    )
    assert added[0].evidence == {
        "url": "https://example.invalid/catalog.csv",
        "bronze_key": bronze_key,
        "selector": "table:2:Service Zone",
        "screenshot_key": screenshot_key,
        "step_id": replay.trace_steps[0]["step_id"],
        "captured_at": "2026-01-01T00:00:00Z",
        "source_id": SOURCE_ID,
        "source_type": "catalog",
        "format": "csv",
    }
    assert replay.trace_steps[0]["parent_step_id"] == "step:file"
    assert replay.trace_steps[0]["requested"]["ontology_version"] == "v2"
    assert replay.trace_steps[0]["executed"]["capture_ontology_version"] == "v1"
    assert replay.trace_steps[0]["observed"]["ontology_only_properties"] == ["service_zone"]
    assert set(replay.trace_steps[0]["value_ids"]) == {
        item.value_id for item in replay.observations
    }
    assert all(item.step_id == replay.trace_steps[0]["step_id"] for item in replay.observations)
    assert replay.trace_steps[0]["executed"]["bronze_key"] == bronze_key

    refined = refine_observations(
        replay.observations,
        ontology=model,
        generated_by=RECORDED,
    )
    export_run(
        lake,
        case_dir,
        "library-case",
        RUN_ID,
        refined.entities,
        ontology=model,
        dod_queries=queries,
        trace=[*trace, *replay.trace_steps],
        generated_by=RECORDED,
        preview=True,
    )
    exported = json.loads(lake.read_key(f"gold/library-case/{RUN_ID}/entities.jsonl"))
    assert exported["properties"]["service_zone"]["value"] == "North"
    assert exported["properties"]["service_zone"]["evidence"][0]["bronze_key"] == bronze_key


def test_replay_ignores_unreferenced_files_incomplete_fetches_and_ambiguous_headers(
    tmp_path,
) -> None:
    case_dir, lake, model, _queries, _bronze_key, _screenshot_key, trace = _recorded_case(
        tmp_path, duplicate_property_label=True
    )
    unreferenced_key = lake.put_bytes(b"book_id,Service Zone\nB-2,South\n")
    assert lake.exists(unreferenced_key)

    incomplete_trace = [dict(step) for step in trace]
    incomplete_trace[1] = {
        **incomplete_trace[1],
        "observed": {**incomplete_trace[1]["observed"], "status": 206},
    }
    replay = replay_bronze_observations(
        case_dir=case_dir,
        lake=lake,
        run_id=RUN_ID,
        trace=incomplete_trace,
        ontology=model,
        provenance=RECORDED,
    )
    assert replay.observations == []
    assert replay.trace_steps == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("url", "https://example.invalid/other.csv"),
        ("source_id", "another-source"),
        ("step_id", "step:other"),
        ("captured_at", "2026-01-01T00:00:01Z"),
    ],
)
def test_replay_rejects_file_sidecar_metadata_not_bound_to_capture_trace(
    tmp_path, monkeypatch, field: str, value: str
) -> None:
    case_dir, lake, model, _queries, bronze_key, _screenshot_key, trace = _recorded_case(tmp_path)
    read_metadata = lake.read_metadata

    def read_tampered_metadata(key: str) -> dict[str, str]:
        metadata = read_metadata(key)
        if key == bronze_key:
            metadata[field] = value
        return metadata

    monkeypatch.setattr(lake, "read_metadata", read_tampered_metadata)
    replay = replay_bronze_observations(
        case_dir=case_dir,
        lake=lake,
        run_id=RUN_ID,
        trace=trace,
        ontology=model,
        provenance=RECORDED,
    )
    assert replay.observations == []
    assert replay.trace_steps == []


def test_replay_rejects_screenshot_sidecar_not_bound_to_capture_trace(
    tmp_path, monkeypatch
) -> None:
    case_dir, lake, model, _queries, _bronze_key, screenshot_key, trace = _recorded_case(tmp_path)
    read_metadata = lake.read_metadata

    def read_tampered_metadata(key: str) -> dict[str, str]:
        metadata = read_metadata(key)
        if key == screenshot_key:
            metadata["source_id"] = "another-source"
        return metadata

    monkeypatch.setattr(lake, "read_metadata", read_tampered_metadata)
    replay = replay_bronze_observations(
        case_dir=case_dir,
        lake=lake,
        run_id=RUN_ID,
        trace=trace,
        ontology=model,
        provenance=RECORDED,
    )
    assert replay.observations == []
    assert replay.trace_steps == []


def test_export_rejects_ontology_only_value_without_valid_replay_parent(tmp_path) -> None:
    case_dir, lake, model, queries, _bronze_key, _screenshot_key, trace = _recorded_case(tmp_path)
    replay = replay_bronze_observations(
        case_dir=case_dir,
        lake=lake,
        run_id=RUN_ID,
        trace=trace,
        ontology=model,
        provenance=RECORDED,
    )
    refined = refine_observations(replay.observations, ontology=model, generated_by=RECORDED)
    malformed_replay = {
        **replay.trace_steps[0],
        "parent_step_id": "step:unknown-capture",
    }
    with pytest.raises(ValueError, match="preceding capture parent"):
        export_run(
            lake,
            case_dir,
            "library-case",
            RUN_ID,
            refined.entities,
            ontology=model,
            dod_queries=queries,
            trace=[*trace, malformed_replay],
            generated_by=RECORDED,
            preview=True,
        )


def test_export_rejects_stale_lineage_without_ontology_only_replay(tmp_path) -> None:
    case_dir, lake, model, queries, _bronze_key, _screenshot_key, trace = _recorded_case(tmp_path)
    with pytest.raises(ValueError, match="objectives ontology version"):
        export_run(
            lake,
            case_dir,
            "library-case",
            RUN_ID,
            [],
            ontology=model,
            dod_queries=queries,
            trace=trace,
            generated_by=RECORDED,
            preview=True,
        )


def test_replay_step_is_deterministic_for_the_same_capture_and_ontology(tmp_path) -> None:
    case_dir, lake, model, _queries, _bronze_key, _screenshot_key, trace = _recorded_case(tmp_path)
    first = replay_bronze_observations(
        case_dir=case_dir,
        lake=lake,
        run_id=RUN_ID,
        trace=trace,
        ontology=model,
        provenance=RECORDED,
    )
    second = replay_bronze_observations(
        case_dir=case_dir,
        lake=lake,
        run_id=RUN_ID,
        trace=trace,
        ontology=model,
        provenance=RECORDED,
    )
    assert [step["step_id"] for step in first.trace_steps] == [
        step["step_id"] for step in second.trace_steps
    ]
    assert [item.value_id for item in first.observations] == [
        item.value_id for item in second.observations
    ]


def test_refine_case_exports_replayed_values_once_and_persists_lineage_idempotently(
    tmp_path, monkeypatch
) -> None:
    case_dir, lake, _model, _queries, _bronze_key, _screenshot_key, trace = _recorded_case(tmp_path)
    trace_key, _original_trace = _install_refine_case(monkeypatch, case_dir, lake, trace)
    original_export = workflow.export_run
    exported_traces = []

    def export_spy(*args, **kwargs):
        persisted = [
            json.loads(line) for line in lake.read_key(trace_key).decode("utf-8").splitlines()
        ]
        replay_steps = [
            step for step in persisted if step.get("requested", {}).get("tool") == "bronze.replay"
        ]
        assert len(replay_steps) == 1
        assert replay_steps[0]["parent_step_id"] == "step:file"
        exported_traces.append(kwargs["trace"])
        return original_export(*args, **kwargs)

    monkeypatch.setattr(workflow, "export_run", export_spy)

    assert workflow.refine_case(case_dir, run_id=RUN_ID) == 0
    first_live_trace = lake.read_key(trace_key)
    first_steps = [json.loads(line) for line in first_live_trace.decode("utf-8").splitlines()]
    replay_steps = [
        step for step in first_steps if step.get("requested", {}).get("tool") == "bronze.replay"
    ]
    assert len(replay_steps) == 1
    assert replay_steps[0]["parent_step_id"] == "step:file"
    assert len(exported_traces) == 1
    gold = json.loads(lake.read_key(f"gold/library-case/{RUN_ID}/entities.jsonl"))
    assert gold["properties"]["service_zone"]["value"] == "North"

    assert workflow.refine_case(case_dir, run_id=RUN_ID) == 0
    second_live_trace = lake.read_key(trace_key)
    assert second_live_trace.startswith(first_live_trace)
    second_steps = [json.loads(line) for line in second_live_trace.decode("utf-8").splitlines()]
    # Each actual parser pod gets six new proof steps. The replay value lineage
    # remains unique for the same capture and ontology.
    assert (
        sum(step.get("evaluated", {}).get("proof_checkpoint") is not None for step in first_steps)
        == 6
    )
    assert (
        sum(step.get("evaluated", {}).get("proof_checkpoint") is not None for step in second_steps)
        == 12
    )
    assert (
        sum(step.get("requested", {}).get("tool") == "bronze.replay" for step in second_steps) == 1
    )
    assert len(exported_traces) == 2
    assert all(
        sum(step.get("requested", {}).get("tool") == "bronze.replay" for step in trace_rows) == 1
        for trace_rows in exported_traces
    )


def test_refine_case_restores_live_trace_when_export_fails(tmp_path, monkeypatch) -> None:
    case_dir, lake, _model, _queries, _bronze_key, _screenshot_key, trace = _recorded_case(tmp_path)
    trace_key, original_trace = _install_refine_case(monkeypatch, case_dir, lake, trace)
    export_calls = 0

    def fail_export(*_args, **_kwargs):
        nonlocal export_calls
        export_calls += 1
        persisted = [
            json.loads(line) for line in lake.read_key(trace_key).decode("utf-8").splitlines()
        ]
        assert any(step.get("requested", {}).get("tool") == "bronze.replay" for step in persisted)
        raise RuntimeError("synthetic export failure")

    monkeypatch.setattr(workflow, "export_run", fail_export)

    with pytest.raises(RuntimeError, match="synthetic export failure"):
        workflow.refine_case(case_dir, run_id=RUN_ID)

    assert export_calls == 1
    persisted_trace = lake.read_key(trace_key)
    assert persisted_trace.startswith(original_trace)
    persisted_steps = [json.loads(line) for line in persisted_trace.decode("utf-8").splitlines()]
    # The sandbox job happened and stays auditable; only replay value steps that
    # were never exported are rolled back.
    assert (
        sum(
            step.get("evaluated", {}).get("proof_checkpoint") is not None
            for step in persisted_steps
        )
        == 6
    )
    assert not any(
        step.get("requested", {}).get("tool") == "bronze.replay" for step in persisted_steps
    )


@pytest.mark.parametrize("marker_state", ["missing", "stale", "denied"])
def test_live_refine_refuses_unapproved_current_ontology_before_writes(
    tmp_path, monkeypatch, marker_state: str
) -> None:
    case_dir = tmp_path / "case"
    ontology_path = case_dir / "02-ontology/ontology.json"
    ontology_path.parent.mkdir(parents=True)
    ontology_path.write_text('{"version":"v2"}\n', encoding="utf-8")
    marker = ontology_path.parent / "APPROVED"
    if marker_state != "missing":
        approval = bind_approval(
            case_dir,
            ["02-ontology/ontology.json"],
            {"approver": "Test Reviewer", "date": "2026-09-27", "checkpoint": "ontology"},
        )
        if marker_state == "denied":
            approval.update({"decision": "deny", "reason": "Add the missing property"})
        marker.write_text(json.dumps(approval), encoding="utf-8")
    if marker_state == "stale":
        ontology_path.write_text('{"version":"v3"}\n', encoding="utf-8")
    before = {
        path.relative_to(case_dir): path.read_bytes()
        for path in case_dir.rglob("*")
        if path.is_file()
    }
    provenance = {"backend": "vultr", "model": "test-model", "at": "2026-09-27T00:00:00Z"}
    monkeypatch.setattr(
        workflow,
        "_existing_run",
        lambda *_args, **_kwargs: ("test-case", object(), "run-live", {"generated_by": provenance}),
    )
    monkeypatch.setattr(
        workflow,
        "silver_store_from_env",
        lambda: pytest.fail("refine must verify ontology approval before opening silver"),
    )

    error = ApprovalArtifactMismatch if marker_state == "stale" else RuntimeError
    with pytest.raises(error):
        workflow.refine_case(case_dir, run_id="run-live")
    assert before == {
        path.relative_to(case_dir): path.read_bytes()
        for path in case_dir.rglob("*")
        if path.is_file()
    }
