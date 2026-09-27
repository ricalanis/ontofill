"""Recorded multi-source coverage for generic P5 execution requirements."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from ontofill import workflow
from ontofill.case.checkpoints import write_json
from ontofill.inference import RecordedDecisionClient, generated_by
from ontofill.lake import FileLake
from ontofill.outer_gap import OuterDecision
from ontofill.phases.p5_execute import (
    ExecutionResult,
    execute_objective,
    execute_objectives,
    normalize_identifier,
)
from ontofill.refiner import MemorySilverStore, Observation
from ontofill.workflow import _gaps_for_objective
from tests.genericity.fixtures.multisource import SOURCE_CLASSES, multisource_objectives


def _trace(
    run_id: str, source_id: str, objective_id: str, url: str, key: str, step: str
) -> list[dict]:
    return [
        {
            "step_id": step,
            "run_id": run_id,
            "phase": 5,
            "source_id": source_id,
            "objective_id": objective_id,
            "tdd_path": f"04-local/{source_id}__{objective_id}/tdd.json",
            "mode": "S1",
            "observed": {"url": url},
            "requested": {"url": url},
            "executed": {"bronze_key": key},
            "evaluated": {"status": "captured"},
            "parent_step_id": None,
            "value_ids": [],
            "ts": datetime.now(UTC).isoformat(),
            "generated_by": {
                "backend": "recorded",
                "model": "recorded-test",
                "at": "2026-01-01T00:00:00Z",
            },
        }
    ]


def test_recorded_multisource_batch_flattens_json_and_derives_membership(tmp_path) -> None:
    objectives = [
        {
            **item,
            "discovered_by": {
                "provider": "recorded-multisource-fixture",
                "at": "2026-01-01T00:00:00Z",
            },
        }
        for item in multisource_objectives()
    ]
    assert len({item["source_type"] for item in objectives}) == 4
    assert {item["id"] for item in SOURCE_CLASSES} == {item["source_type"] for item in objectives}
    payloads = {
        "source-index": ("csv", b"id,title\nR-1,First\nR-2,Second\nR-3,Third\n"),
        "source-json": ("json", b'{"records":[{"id":"J-1","summary":"Open data item"}]}'),
        "source-ocds": ("json", b'{"releases":[{"id":"O-1","category":"goods"}]}'),
        "source-membership": ("csv", "id\nＲ－1\nR-1\nＲ－3\n".encode()),
    }
    ontology = {
        "classes": [
            {
                "id": "record",
                "identifier_property": "record_id",
                "title_property": "title",
            }
        ],
        "properties": [
            {"id": "record_id", "domain": "record", "datatype": "string"},
            {"id": "title", "domain": "record", "datatype": "string"},
            {"id": "summary", "domain": "record", "datatype": "string"},
            {"id": "category", "domain": "record", "datatype": "string"},
            {"id": "is_listed", "domain": "record", "datatype": "boolean"},
        ],
    }
    target_fields = {
        "objective-index": ["record_id", "title"],
        "objective-json": ["record_id", "summary"],
        "objective-ocds": ["record_id", "category"],
        "objective-membership": ["is_listed"],
    }
    tdds = {
        item["id"]: {
            "allowed_domains": [item["source_url"].split("/")[2]],
            "target_fields": target_fields[item["id"]],
            "target_volume": 300,
            **(
                {
                    "membership": {
                        "property_id": "is_listed",
                        "identifier_property_id": "record_id",
                        "complete": True,
                    }
                }
                if item["id"] == "objective-membership"
                else {}
            ),
        }
        for item in objectives
    }
    mappings = [
        {
            "class_id": "record",
            "columns": [
                {"header": "id", "property_id": "record_id"},
                {"header": "title", "property_id": "title"},
            ],
        },
        {
            "class_id": "record",
            "columns": [
                {"header": "records.id", "property_id": "record_id"},
                {"header": "records.summary", "property_id": "summary"},
            ],
        },
        {
            "class_id": "record",
            "columns": [
                {"header": "releases.id", "property_id": "record_id"},
                {"header": "releases.category", "property_id": "category"},
            ],
        },
        {
            "class_id": "record",
            "columns": [{"header": "id", "property_id": "record_id"}],
        },
    ]
    decision = RecordedDecisionClient(
        {
            "phase5.select_download": [{"index": 0} for _ in objectives],
            "phase5.map_columns": mappings,
        }
    )
    provenance = generated_by(decision)
    lake = FileLake(tmp_path / "lake")
    extensions = {source_id: extension for source_id, (extension, _) in payloads.items()}

    def capture(url: str, **kwargs) -> dict:
        source_id = kwargs["source_id"]
        extension = extensions[source_id]
        host = url.split("/")[2]
        data_url = f"https://{host}/data.{extension}"
        html = f'<html><a href="{data_url}">Captured data</a></html>'
        key = lake.put_bytes(html.encode())
        screenshot = lake.put_bytes(b"synthetic screenshot")
        return {
            "url": url,
            "html": html,
            "html_key": key,
            "screenshot_key": screenshot,
            "trace": _trace(
                kwargs["run_id"], source_id, kwargs["objective_id"], url, key, f"page-{source_id}"
            ),
        }

    list_keys: list[str] = []

    def fetch(url: str, **kwargs) -> dict:
        source_id = kwargs["source_id"]
        _extension, content = payloads[source_id]
        key = lake.put_bytes(content)
        if source_id == "source-membership":
            list_keys.append(key)
        return {
            "url": url,
            "bytes": content,
            "content_type": "application/octet-stream",
            "bronze_key": key,
            "status": 200,
            "trace": _trace(
                kwargs["run_id"], source_id, kwargs["objective_id"], url, key, f"file-{source_id}"
            ),
        }

    store = MemorySilverStore()
    run_id = f"mock-{uuid.uuid4().hex[:12]}"
    results = execute_objectives(
        case_dir=tmp_path,
        objectives=objectives,
        ontology=ontology,
        tdds=tdds,
        lake=lake,
        run_id=run_id,
        decision=decision,
        store=store,
        provenance=provenance,
        capture=capture,
        fetch=fetch,
    )

    assert [result.format for result in results] == ["csv", "json", "json", "csv"]
    assert [
        step["objective_id"]
        for step in results[0].trace
        if step["requested"].get("tool") == "column.map"
    ] == ["objective-index"]
    assert [
        step["objective_id"]
        for step in results[-1].trace
        if step["requested"].get("tool") == "membership.derive"
    ] == ["objective-membership"]
    assert len(list_keys) == 1
    membership_values = {
        item.entity_id: item.value
        for item in results[-1].observations
        if item.property_id == "is_listed"
    }
    entity_ids = {
        item.value: item.entity_id
        for result in results[:3]
        for item in result.observations
        if item.property_id == "record_id"
    }
    assert membership_values == {
        entity_id: identifier == "R-3"
        for identifier, entity_id in entity_ids.items()
        if identifier != "R-1"
    }
    assert all(
        item.evidence["bronze_key"] == list_keys[0]
        for item in results[-1].observations
        if item.property_id == "is_listed"
    )
    assert any(
        item.property_id == "summary" and item.value == "Open data item"
        for item in results[1].observations
    )
    assert any(
        item.property_id == "category" and item.value == "goods" for item in results[2].observations
    )
    membership_step = next(
        step
        for step in results[-1].trace
        if step.get("requested", {}).get("tool") == "membership.derive"
    )
    assert membership_step["observed"]["ambiguous_identifiers"] == 1
    assert normalize_identifier(" Ｒ－1 ") == "r-1"


def test_membership_refuses_partial_file_even_when_tdd_claims_complete(tmp_path) -> None:
    objective = {
        "id": "objective-membership",
        "source_id": "source-membership",
        "source_url": "https://reference.example.test/catalog",
        "source_type": "complete_reference_list",
    }
    ontology = {
        "classes": [
            {"id": "record", "identifier_property": "record_id", "title_property": "record_id"}
        ],
        "properties": [
            {"id": "record_id", "domain": "record", "datatype": "string"},
            {"id": "is_listed", "domain": "record", "datatype": "boolean"},
        ],
    }
    decision = RecordedDecisionClient(
        {
            "phase5.select_download": [{"index": 0}],
            "phase5.map_columns": [
                {"class_id": "record", "columns": [{"header": "id", "property_id": "record_id"}]}
            ],
        }
    )
    provenance = generated_by(decision)
    store = MemorySilverStore()
    store.add(
        Observation(
            run_id="mock-partial",
            entity_id="record:existing",
            entity_class="record",
            property_id="record_id",
            value="R-1",
            evidence={"bronze_key": "sha256:existing"},
            step_id="step:existing",
            generated_by=provenance,
        )
    )
    page = {
        "url": objective["source_url"],
        "html": '<a href="/list.csv">List</a>',
        "html_key": "sha256:page",
        "screenshot_key": None,
        "trace": [{"step_id": "step:page"}],
    }

    result = execute_objective(
        case_dir=tmp_path,
        objective=objective,
        ontology=ontology,
        tdd={
            "allowed_domains": ["reference.example.test"],
            "target_fields": ["is_listed"],
            "membership": {
                "property_id": "is_listed",
                "identifier_property_id": "record_id",
                "complete": True,
            },
        },
        lake=FileLake(tmp_path / "lake"),
        run_id="mock-partial",
        decision=decision,
        store=store,
        provenance=provenance,
        capture=lambda _url, **_kwargs: page,
        fetch=lambda url, **_kwargs: {
            "url": url,
            "bytes": b"id\nR-1\n",
            "bronze_key": "sha256:partial-list",
            "status": 206,
            "trace": [{"step_id": "step:file"}],
        },
    )
    assert not any(item.property_id == "is_listed" for item in result.observations)
    refusal = next(
        step
        for step in result.trace
        if step.get("requested", {}).get("tool") == "membership.derive"
    )
    assert refusal["evaluated"] == {"status": "refused", "reason": "non_complete_http_response"}


def test_membership_without_download_refuses_instead_of_using_browser(tmp_path) -> None:
    objective = {
        "id": "objective-list",
        "source_id": "source-list",
        "source_url": "https://reference.example.test/catalog",
        "source_type": "complete_reference_list",
    }
    decision = RecordedDecisionClient({})
    result = execute_objective(
        case_dir=tmp_path,
        objective=objective,
        ontology={"classes": [], "properties": []},
        tdd={
            "allowed_domains": ["reference.example.test"],
            "target_fields": ["is_listed"],
            "membership": {
                "property_id": "is_listed",
                "identifier_property_id": "record_id",
                "complete": True,
            },
        },
        lake=FileLake(tmp_path / "lake"),
        run_id="mock-no-download",
        decision=decision,
        store=MemorySilverStore(),
        provenance=generated_by(decision),
        capture=lambda url, **_kwargs: {
            "url": url,
            "html": "<main>No downloadable list</main>",
            "trace": [{"step_id": "step:page"}],
        },
    )
    assert result.observations == []
    assert result.trace[-1]["evaluated"] == {
        "status": "refused",
        "reason": "membership_requires_downloaded_file",
    }


def test_gap_report_routes_by_ontology_property_overlap() -> None:
    gaps = [
        {"criterion_id": "hours-gap", "properties": ["hours", "name"], "iteration": 2},
        {"criterion_id": "category-gap", "properties": ["category"], "iteration": 1},
    ]
    routed = _gaps_for_objective(gaps, {"target_fields": ["name", "record_id"]})
    assert routed == [{"criterion_id": "hours-gap", "properties": ["name"], "iteration": 2}]


def test_workflow_drafts_and_submits_all_selected_objectives_in_one_pass(
    tmp_path, monkeypatch
) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "brief.md").write_text("Synthetic generic-source case\n", encoding="utf-8")
    objectives = [
        {
            **item,
            "discovered_by": {
                "provider": "recorded-multisource-fixture",
                "at": "2026-01-01T00:00:00Z",
            },
        }
        for item in multisource_objectives()
    ]
    decision = RecordedDecisionClient({})
    provenance = generated_by(decision)
    ontology = {
        "version": "v1",
        "classes": [],
        "properties": [],
        "shacl_path": "02-ontology/shape.ttl",
        "generated_by": provenance,
    }
    draft_calls: list[str] = []
    batch_calls: list[list[str]] = []

    monkeypatch.setattr(
        workflow,
        "draft_prd",
        lambda *_args, **_kwargs: {
            "authority_policy": {},
            "requirements": [],
            "generated_by": provenance,
        },
    )
    monkeypatch.setattr(
        workflow,
        "draft_factors",
        lambda *_args, **_kwargs: {"generated_by": provenance},
    )

    def draft_ontology(case_dir, *_args, **_kwargs):
        write_json(case_dir / "02-ontology/dod-queries.json", {"queries": []})
        return ontology

    monkeypatch.setattr(workflow, "draft_ontology", draft_ontology)
    monkeypatch.setattr(
        workflow,
        "discover_objectives",
        lambda *_args, **_kwargs: {"objectives": objectives},
    )
    monkeypatch.setattr(workflow, "_source_review", lambda *_args, **_kwargs: (True, tmp_path))
    monkeypatch.setattr(workflow, "require_approval", lambda *_args, **_kwargs: True)

    def draft_local_scope(_case, _prd, _ontology, objective, *_args, **_kwargs):
        draft_calls.append(objective["id"])
        tdd = {"target_fields": objective["target_fields"]}
        if objective["id"] == "objective-membership":
            tdd["membership"] = {
                "property_id": "is_listed",
                "identifier_property_id": "record_id",
                "complete": True,
            }
        return {}, tdd

    def execute_batch(**kwargs):
        batch_calls.append([item["id"] for item in kwargs["objectives"]])
        return [ExecutionResult([], [], [], "csv") for _ in kwargs["objectives"]]

    monkeypatch.setattr(workflow, "draft_local_scope", draft_local_scope)
    monkeypatch.setattr(workflow, "execute_objectives", execute_batch)
    monkeypatch.setattr(workflow, "_write_silver_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        workflow,
        "refine_observations",
        lambda *_args, **_kwargs: SimpleNamespace(entities=[]),
    )
    monkeypatch.setattr(workflow, "export_run", lambda *_args, **_kwargs: {"dod": []})
    monkeypatch.setattr(
        workflow,
        "decide_outer_gap",
        lambda **_kwargs: OuterDecision(
            iteration=1,
            gaps=(),
            reopen=None,
            reason="all checks passed",
            stop_reason="checks_passed",
            usage={
                "model": provenance["model"],
                "backend": "recorded",
                "input_tokens": 0,
                "output_tokens": 0,
                "est_usd": 0,
            },
            usd=0,
        ),
    )

    selected_ids = [item["id"] for item in objectives]
    result = workflow.run_case(
        case,
        run_id=f"mock-{uuid.uuid4().hex[:12]}",
        decision=decision,
        preview_past_checkpoints=True,
        search_client=SimpleNamespace(trace=[], jobs=[]),
        store=MemorySilverStore(),
        lake=FileLake(tmp_path / "lake"),
    )
    assert result == 0
    assert draft_calls == selected_ids
    assert batch_calls == [selected_ids]
