"""Trace-bounded bronze replay preserves typed values and export lineage."""

from __future__ import annotations

import json

from ontofill.lake import FileLake
from ontofill.refiner import export_run, refine_observations
from ontofill.refiner.bronze_replay import replay_bronze_observations
from tests.test_refiner import RECORDED, SOURCE_ID, URL, dod_queries, ontology, write_lineage

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
    model = ontology()
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
    write_lineage(case_dir, model, queries, RECORDED)
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


def test_replay_fills_new_ontology_property_and_exports_trace_lineage(tmp_path) -> None:
    case_dir, lake, model, queries, bronze_key, screenshot_key, trace = _recorded_case(tmp_path)

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
