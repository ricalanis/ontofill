"""Generic refinement keeps typed values, lineage, and declarative DoD honest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontofill.lake import FileLake
from ontofill.refiner import (
    MemorySilverStore,
    Observation,
    export_run,
    refine_observations,
    stable_value_id,
)
from ontofill.refiner.export import _query_actual

RUN_ID = "mock-books"
SOURCE_ID = "synthetic-catalog"
URL = "https://example.invalid/catalog"
RECORDED = {"backend": "recorded", "model": "synthetic", "at": "2026-01-01T00:00:00Z"}
VULTR = {"backend": "vultr", "model": "synthetic-live", "at": "2026-01-01T00:00:00Z"}


def ontology(generated_by: dict = RECORDED) -> dict:
    def property_record(name: str, domain: str, datatype: str, *, dod: bool = False) -> dict:
        return {
            "id": name,
            "label": name.replace("_", " ").title(),
            "domain": domain,
            "datatype": datatype,
            "dod": dod,
            "order": 1,
            "description": f"Synthetic {name}",
            "aligned_to": None,
        }

    def class_record(name: str, title: str, identifier: str) -> dict:
        return {
            "id": name,
            "label": name,
            "label_plural": f"{name}s",
            "description": f"Synthetic {name}",
            "title_property": title,
            "identifier_property": identifier,
            "aligned_to": None,
        }

    return {
        "version": "v1",
        "prd_path": "01-scope/prd.json",
        "factors": [
            {
                "id": "edition",
                "label": "Edition",
                "description": "Synthetic grouping",
                "kind": "conceptual",
                "evidence": [],
            }
        ],
        "taxonomies": [
            {
                "factor_id": "edition",
                "root_label": "Edition",
                "children": [
                    {
                        "id": "local",
                        "label": "Local",
                        "level": 1,
                        "critic_label": "Good-Exclusive",
                    }
                ],
                "soundness": 1,
                "coverage": 1,
            }
        ],
        "primary_class": "Book",
        "classes": [
            class_record("Book", "title", "book_id"),
            class_record("Library", "name", "library_id"),
        ],
        "properties": [
            property_record("book_id", "Book", "string", dod=True),
            property_record("title", "Book", "string", dod=True),
            property_record("copies", "Book", "integer", dod=True),
            property_record("available", "Book", "boolean"),
            property_record("published", "Book", "date"),
            property_record("library_code", "Book", "string"),
            property_record("library_id", "Library", "string", dod=True),
            property_record("name", "Library", "string", dod=True),
        ],
        "relations": [
            {
                "id": "held_by",
                "label": "Held by",
                "domain": "Book",
                "range": "Library",
                "symmetric": False,
            }
        ],
        "rules": [],
        "source_classes": [{"id": "catalog", "label": "Catalog"}],
        "dod_queries_path": "02-ontology/dod-queries.json",
        "shacl_path": "02-ontology/book-shape.ttl",
        "generated_by": generated_by,
    }


def dod_queries(generated_by: dict = RECORDED) -> dict:
    return {
        "prd_path": "01-scope/prd.json",
        "ontology_version": "v1",
        "queries": [
            {
                "criterion_id": "books_with_core",
                "aggregate": "count_entities_with_properties",
                "class_id": "Book",
                "properties": ["book_id", "title", "copies"],
                "target": 1,
                "operator": ">=",
            },
            {
                "criterion_id": "unavailable",
                "aggregate": "count_entities",
                "class_id": "Book",
                "conditions": [{"property": "available", "operator": "eq", "value": False}],
                "target": 1,
                "operator": ">=",
            },
        ],
        "generated_by": generated_by,
    }


def evidence(lake: FileLake, *, screenshot: bool = True) -> dict:
    bronze = lake.put_bytes(b"<html>synthetic book</html>")
    image = lake.put_bytes(b"synthetic screenshot") if screenshot else "sha256:" + "b" * 64
    return {
        "url": URL,
        "bronze_key": bronze,
        "selector": "tr:first-child",
        "screenshot_key": image,
        "captured_at": "2026-01-01T00:00:00Z",
        "source_id": SOURCE_ID,
        "source_type": "catalog",
    }


def observed(
    lake: FileLake,
    property_id: str,
    value: str | int | bool,
    *,
    entity_id: str = "Book:one",
    entity_class: str = "Book",
    run_id: str = RUN_ID,
    generated_by: dict = RECORDED,
    **kwargs,
) -> Observation:
    return Observation(
        run_id=run_id,
        entity_id=entity_id,
        entity_class=entity_class,
        property_id=property_id,
        value=value,
        evidence=kwargs.pop("evidence", evidence(lake)),
        step_id="step-1",
        generated_by=generated_by,
        **kwargs,
    )


def trace(
    value_ids: list[str], *, run_id: str = RUN_ID, generated_by: dict = RECORDED
) -> list[dict]:
    return [
        {
            "step_id": "step-1",
            "run_id": run_id,
            "phase": 5,
            "source_id": SOURCE_ID,
            "objective_id": "read-catalog",
            "tdd_path": "04-local/synthetic-catalog__read-catalog/tdd.json",
            "mode": "S1",
            "observed": {"url": URL},
            "requested": {"tool": "emit.observation"},
            "executed": {"tool": "emit.observation", "status": "ok"},
            "evaluated": {"status": "ok"},
            "parent_step_id": None,
            "value_ids": value_ids,
            "ts": "2026-01-01T00:00:00Z",
            "generated_by": generated_by,
        }
    ]


def write_lineage(case_dir: Path, model: dict, queries: dict, generated_by: dict) -> None:
    def write(path: str, value: dict) -> None:
        target = case_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding="utf-8")

    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "brief.md").write_text("# Synthetic library brief\n", encoding="utf-8")
    write(
        "01-scope/prd.json",
        {
            "version": "v1",
            "brief_path": "brief.md",
            "personas": [{"id": "p1", "description": "Reader"}],
            "jobs_to_be_done": [{"id": "j1", "persona_id": "p1", "description": "Find a book"}],
            "requirements": [{"id": "r1", "job_id": "j1", "description": "Show availability"}],
            "constraints": [],
            "authority_policy": {
                "jurisdiction": "Synthetic",
                "trusted_publishers": [],
                "unknown_source_action": "review",
            },
            "non_goals": [],
            "definition_of_done": [
                {
                    "id": "books_with_core",
                    "metric": "book coverage",
                    "operator": ">=",
                    "target": 1,
                    "basis": "proposed",
                    "rationale": "One is the test threshold.",
                    "feasibility": "The test budget and run time fit one entity.",
                },
                {
                    "id": "unavailable",
                    "metric": "unavailable books",
                    "operator": ">=",
                    "target": 1,
                    "basis": "proposed",
                    "rationale": "One exercises the unavailable branch.",
                    "feasibility": "The test budget and run time fit one entity.",
                },
            ],
            "generated_by": generated_by,
        },
    )
    write("02-ontology/ontology.json", model)
    write("02-ontology/dod-queries.json", queries)
    target_fields = [prop["id"] for prop in model["properties"]]
    write(
        "03-fanout/objectives.json",
        {
            "ontology_version": "v1",
            "prd_path": "01-scope/prd.json",
            "objectives": [
                {
                    "id": "read-catalog",
                    "source_id": SOURCE_ID,
                    "source_url": URL,
                    "target_fields": target_fields,
                    "priority": 1,
                    "expected_contribution": 1,
                }
            ],
            "generated_by": generated_by,
        },
    )
    prefix = "04-local/synthetic-catalog__read-catalog"
    write(
        f"{prefix}/local-prd.json",
        {
            "source_id": SOURCE_ID,
            "objective_id": "read-catalog",
            "global_prd_path": "01-scope/prd.json",
            "global_requirement_ids": ["r1"],
            "target_fields": target_fields,
            "local_definition_of_done": [
                {"metric": "book coverage", "operator": ">=", "target": 1}
            ],
            "generated_by": generated_by,
        },
    )
    write(
        f"{prefix}/tdd.json",
        {
            "source_id": SOURCE_ID,
            "objective_id": "read-catalog",
            "local_prd_path": f"{prefix}/local-prd.json",
            "ontology_version": "v1",
            "source_url": URL,
            "allowed_domains": ["example.invalid"],
            "target_fields": target_fields,
            "extraction_method": "dom",
            "validation_rules": ["Evidence required"],
            "rate_limit_per_minute": 1,
            "budget_usd": 1,
            "steps": [
                {
                    "id": "step-1",
                    "description": "Read synthetic catalog",
                    "starting_mode": "S1",
                    "allowed_modes": ["S1"],
                    "observation_channel": "text_structure",
                    "risk_tier": "SAFE",
                    "termination_predicate": "Observed row",
                }
            ],
            "generated_by": generated_by,
        },
    )


def test_refinement_preserves_typed_values_missing_and_conflict(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    store = MemorySilverStore()
    entries = [
        observed(lake, "book_id", "B-1"),
        observed(lake, "title", "First", classified_as=("edition:local",), confidence=0.7),
        observed(lake, "title", "Second", confidence=0.8),
        observed(lake, "copies", 0),
        observed(lake, "available", False),
        observed(lake, "published", "not-a-date"),
        observed(lake, "library_id", "L-1", entity_id="Library:one", entity_class="Library"),
        observed(lake, "name", "Main", entity_id="Library:one", entity_class="Library"),
    ]
    for entry in [*entries, entries[0]]:
        store.add(entry)
    assert len(store.list_for_run(RUN_ID)) == len(entries)
    result = refine_observations(
        store.list_for_run(RUN_ID), ontology=ontology(), generated_by=RECORDED
    )
    assert len(result.rejected) == 1
    assert "ISO date" in result.rejected[0]["reason"]
    book, library = result.entities
    assert book["class"] == "Book" and library["class"] == "Library"
    assert book["properties"]["copies"]["value"] == 0
    assert book["properties"]["available"]["value"] is False
    assert book["properties"]["title"]["status"] == "conflict"
    assert book["properties"]["title"]["value"] == "Second"
    assert book["properties"]["published"]["status"] == "missing"
    assert book["classified_as"] == ["edition:local"]
    assert stable_value_id("Book:one", "copies", 0) != stable_value_id("Book:one", "copies", False)
    assert (
        _query_actual(
            [book],
            {
                "aggregate": "count_entities",
                "conditions": [{"property": "copies", "operator": "eq", "value": False}],
            },
        )
        == 0
    )


def test_dod_property_count_requires_all_and_completeness_uses_approved_ratio() -> None:
    def field(value, *, present=True):
        return {
            "value": value,
            "status": "gold" if present else "missing",
            "evidence": [1] if present else [],
        }

    entities = [
        {
            "class": "Room",
            "properties": {"a": field(False), "b": field(0), "c": field(None, present=False)},
        },
        {
            "class": "Room",
            "properties": {
                "a": field("A"),
                "b": field(None, present=False),
                "c": field(None, present=False),
            },
        },
    ]
    assert (
        _query_actual(
            entities,
            {
                "aggregate": "count_entities_with_properties",
                "class_id": "Room",
                "properties": ["a", "b"],
            },
        )
        == 1
    )
    assert (
        _query_actual(
            entities,
            {
                "aggregate": "entities_meeting_completeness",
                "class": "Room",
                "properties": "dod",
                "min_ratio": 2 / 3,
                "_dod_properties": ["a", "b", "c"],
            },
        )
        == 1
    )


def test_export_applies_per_entity_min_ratio_from_approved_query(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    entries = [observed(lake, "book_id", "B-1"), observed(lake, "title", "First")]
    model, queries = ontology(), dod_queries()
    queries["queries"].append(
        {
            "criterion_id": "partial_completeness",
            "aggregate": "entities_meeting_completeness",
            "class": "Book",
            "properties": "dod",
            "min_ratio": 2 / 3,
            "target": 1,
            "operator": ">=",
        }
    )
    case_dir = tmp_path / "case"
    write_lineage(case_dir, model, queries, RECORDED)
    prd_path = case_dir / "01-scope/prd.json"
    prd = json.loads(prd_path.read_text())
    prd["definition_of_done"].append(
        {
            "id": "partial_completeness",
            "metric": "two thirds of required fields",
            "operator": ">=",
            "target": 1,
            "min_ratio": 2 / 3,
            "basis": "proposed",
            "rationale": "The threshold exercises partial completeness.",
            "feasibility": "The synthetic test has enough budget and run time.",
        }
    )
    prd_path.write_text(json.dumps(prd))
    entity = refine_observations(entries, ontology=model, generated_by=RECORDED).entities[0]
    metrics = export_run(
        lake,
        case_dir,
        "books",
        RUN_ID,
        [entity],
        ontology=model,
        dod_queries=queries,
        trace=trace([item.value_id for item in entries]),
        generated_by=RECORDED,
    )
    assert metrics["entities_meeting_dod"]["Book"] == 1
    assert metrics["dod"][-1]["actual"] == 1
    assert metrics["dod"][-1]["met"] is False


def test_generic_export_metrics_lineage_and_recorded_dod(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    entries = [
        observed(lake, "book_id", "B-1"),
        observed(lake, "title", "First", classified_as=("edition:local",)),
        observed(lake, "copies", 0),
        observed(lake, "available", False),
    ]
    model, queries = ontology(), dod_queries()
    book = refine_observations(entries, ontology=model, generated_by=RECORDED).entities[0]
    case_dir = tmp_path / "case"
    write_lineage(case_dir, model, queries, RECORDED)
    metrics = export_run(
        lake,
        case_dir,
        "books",
        RUN_ID,
        [book],
        ontology=model,
        dod_queries=queries,
        trace=trace([item.value_id for item in entries]),
        taxonomy_levels={"edition": [["edition:local", "edition:other"]]},
        generated_by=RECORDED,
    )
    assert metrics["entities_total"] == {"Book": 1, "Library": 0}
    assert metrics["entities_meeting_dod"]["Book"] == 1
    assert metrics["per_property_completeness"]["Book"]["copies"] == 1
    assert metrics["per_property_completeness"]["Book"]["published"] == 0
    assert metrics["distinct_source_classes"] == 1
    assert metrics["values_without_evidence"] == 0
    assert metrics["level_ratio_coverage"]["edition"] == [0.5]
    assert [(row["actual"], row["met"]) for row in metrics["dod"]] == [(1, False), (1, False)]
    assert json.loads(lake.read_key(f"gold/books/{RUN_ID}/entities.jsonl")) == book
    assert json.loads(lake.read_key(f"gold/books/{RUN_ID}/ontology.json")) == model
    assert not lake.exists("gold/books/latest.json")
    assert not lake.exists(f"gold/books/{RUN_ID}/suppliers.jsonl")
    assert (case_dir / "runs" / RUN_ID / "metrics.json").is_file()


def test_export_rejects_missing_bronze_and_untraced_value(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    item = observed(lake, "title", "First")
    model, queries = ontology(), dod_queries()
    entity = refine_observations([item], ontology=model, generated_by=RECORDED).entities[0]
    case_dir = tmp_path / "case"
    write_lineage(case_dir, model, queries, RECORDED)
    with pytest.raises(ValueError, match="untraceable value"):
        export_run(
            lake,
            case_dir,
            "books",
            RUN_ID,
            [entity],
            ontology=model,
            dod_queries=queries,
            trace=trace([]),
            generated_by=RECORDED,
        )
    broken = observed(lake, "title", "Second", evidence=evidence(lake, screenshot=False))
    broken_entity = refine_observations([broken], ontology=model, generated_by=RECORDED).entities[0]
    with pytest.raises(ValueError, match="evidence object absent"):
        export_run(
            lake,
            case_dir,
            "books",
            RUN_ID,
            [broken_entity],
            ontology=model,
            dod_queries=queries,
            trace=trace([broken.value_id]),
            generated_by=RECORDED,
        )
    assert not lake.exists("gold/books/latest.json")


def test_live_export_requires_matching_lineage_and_updates_latest(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    entries = [
        observed(lake, "book_id", "B-1", run_id="live", generated_by=VULTR),
        observed(lake, "title", "First", run_id="live", generated_by=VULTR),
        observed(lake, "copies", 0, run_id="live", generated_by=VULTR),
        observed(lake, "available", False, run_id="live", generated_by=VULTR),
    ]
    model, queries = ontology(VULTR), dod_queries(VULTR)
    entity = refine_observations(entries, ontology=model, generated_by=VULTR).entities[0]
    case_dir = tmp_path / "case"
    write_lineage(case_dir, ontology(), dod_queries(), RECORDED)
    with pytest.raises(ValueError, match="approved case artifact"):
        export_run(
            lake,
            case_dir,
            "books",
            "live",
            [entity],
            ontology=model,
            dod_queries=queries,
            trace=trace([item.value_id for item in entries], run_id="live", generated_by=VULTR),
            generated_by=VULTR,
        )
    write_lineage(case_dir, model, queries, VULTR)
    metrics = export_run(
        lake,
        case_dir,
        "books",
        "live",
        [entity],
        ontology=model,
        dod_queries=queries,
        trace=trace([item.value_id for item in entries], run_id="live", generated_by=VULTR),
        generated_by=VULTR,
    )
    assert all(row["met"] for row in metrics["dod"])
    assert metrics["entities_meeting_dod"]["Book"] == 1
    assert json.loads(lake.read_key("gold/books/latest.json"))["run_id"] == "live"
    assert (case_dir / "runs/latest/metrics.json").is_file()


def test_invalid_property_datatype_and_shacl_are_rejected(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    shape = tmp_path / "shape.ttl"
    shape.write_text(
        """@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix onto: <https://ontofill.dev/ontology/> .
onto:BookShape a sh:NodeShape ; sh:targetClass onto:Book ;
  sh:property [ sh:path onto:title ; sh:minCount 1 ; sh:pattern "^Good" ] .
""",
        encoding="utf-8",
    )
    entries = [
        observed(lake, "copies", True),
        observed(lake, "title", "Bad title"),
        observed(lake, "published", "2026-02-31"),
        observed(lake, "name", "Wrong domain"),
        observed(lake, "title", "Good title"),
    ]
    result = refine_observations(
        entries, ontology=ontology(), generated_by=RECORDED, shapes_ttl=shape
    )
    assert len(result.rejected) == 4
    assert result.entities[0]["properties"]["title"]["value"] == "Good title"
    assert result.entities[0]["properties"]["copies"]["status"] == "missing"


def test_links_require_declared_relation_target_and_value(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    entries = [
        observed(lake, "library_code", "L-1"),
        observed(lake, "library_id", "L-1", entity_id="Library:one", entity_class="Library"),
    ]
    model, queries = ontology(), dod_queries()
    entities = refine_observations(entries, ontology=model, generated_by=RECORDED).entities
    entities[0]["links"] = [
        {"property": "held_by", "target": "Library:one", "via_value_id": entries[0].value_id}
    ]
    case_dir = tmp_path / "case"
    write_lineage(case_dir, model, queries, RECORDED)
    export_run(
        lake,
        case_dir,
        "books",
        RUN_ID,
        entities,
        ontology=model,
        dod_queries=queries,
        trace=trace([item.value_id for item in entries]),
        generated_by=RECORDED,
    )
    entities[0]["links"][0]["target"] = "Library:absent"
    with pytest.raises(ValueError, match="entity link"):
        export_run(
            lake,
            case_dir,
            "books",
            RUN_ID,
            entities,
            ontology=model,
            dod_queries=queries,
            trace=trace([item.value_id for item in entries]),
            generated_by=RECORDED,
        )


def test_backend_mismatch_and_recorded_run_id(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    item = observed(lake, "title", "First")
    result = refine_observations([item], ontology=ontology(), generated_by=VULTR)
    assert result.entities == []
    assert "differs from run" in result.rejected[0]["reason"]
    with pytest.raises(ValueError, match="mock- run ID"):
        export_run(
            lake,
            tmp_path / "case",
            "books",
            "live",
            [],
            ontology=ontology(),
            dod_queries=dod_queries(),
            generated_by=RECORDED,
        )


def test_export_rejects_duplicate_entities_and_tampered_typed_value(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    item = observed(lake, "copies", 0)
    model, queries = ontology(), dod_queries()
    entity = refine_observations([item], ontology=model, generated_by=RECORDED).entities[0]
    case_dir = tmp_path / "case"
    write_lineage(case_dir, model, queries, RECORDED)
    with pytest.raises(ValueError, match="duplicate entity ID"):
        export_run(
            lake,
            case_dir,
            "books",
            RUN_ID,
            [entity, entity],
            ontology=model,
            dod_queries=queries,
            trace=trace([item.value_id]),
            generated_by=RECORDED,
        )
    entity["properties"]["copies"]["value"] = 1
    with pytest.raises(ValueError, match="value_id does not match"):
        export_run(
            lake,
            case_dir,
            "books",
            RUN_ID,
            [entity],
            ontology=model,
            dod_queries=queries,
            trace=trace([item.value_id]),
            generated_by=RECORDED,
        )


def test_export_rejects_changed_approved_dod_target(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    item = observed(lake, "title", "First")
    model, queries = ontology(), dod_queries()
    entity = refine_observations([item], ontology=model, generated_by=RECORDED).entities[0]
    case_dir = tmp_path / "case"
    write_lineage(case_dir, model, queries, RECORDED)
    queries["queries"][0]["target"] = 2
    (case_dir / "02-ontology/dod-queries.json").write_text(json.dumps(queries), encoding="utf-8")
    with pytest.raises(ValueError, match="approved PRD criterion"):
        export_run(
            lake,
            case_dir,
            "books",
            RUN_ID,
            [entity],
            ontology=model,
            dod_queries=queries,
            trace=trace([item.value_id]),
            generated_by=RECORDED,
        )


def test_valid_ontology_date_survives_without_string_coercion(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    entries = [observed(lake, "published", "2024-02-29"), observed(lake, "copies", 0)]
    result = refine_observations(entries, ontology=ontology(), generated_by=RECORDED)
    assert result.rejected == []
    assert result.entities[0]["properties"]["published"]["value"] == "2024-02-29"
    assert type(result.entities[0]["properties"]["copies"]["value"]) is int
