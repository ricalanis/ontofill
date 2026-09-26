"""Synthetic gold refinement preserves missing values and checks real bronze lineage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontofill.lake import FileLake
from ontofill.refiner import (
    CORE_FIELDS,
    MemorySilverStore,
    Observation,
    export_run,
    refine_observations,
)

RUN_ID = "mock-synthetic-run"
SOURCE_ID = "synthetic-source"
URL = "https://example.invalid/synthetic/supplier"
RECORDED = {"backend": "recorded", "model": "synthetic-replay", "at": "2026-01-01T00:00:00Z"}
VULTR = {"backend": "vultr", "model": "synthetic-vultr", "at": "2026-01-01T00:00:00Z"}


def evidence(lake: FileLake, *, screenshot: bool = True) -> dict:
    bronze_key = lake.put_bytes(
        b"<html><p>Proveedor Ejemplo 01</p></html>",
        {"content_type": "text/html", "url": URL, "source_id": SOURCE_ID, "step_id": "step-1"},
    )
    screenshot_key = lake.put_bytes(b"synthetic screenshot") if screenshot else "sha256:" + "b" * 64
    return {
        "url": URL,
        "bronze_key": bronze_key,
        "selector": "p:first-child",
        "screenshot_key": screenshot_key,
        "captured_at": "2026-01-01T00:00:00Z",
        "source_id": SOURCE_ID,
        "source_type": "synthetic_registry",
    }


def observation(lake: FileLake, field: str, value: str, **kwargs) -> Observation:
    observed_evidence = kwargs.pop("evidence", None) or evidence(lake)
    run_id = kwargs.pop("run_id", RUN_ID)
    generated_by = kwargs.pop("generated_by", RECORDED)
    return Observation(
        run_id=run_id,
        supplier_id="sup:synthetic-01",
        field=field,
        value=value,
        evidence=observed_evidence,
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
            "objective_id": "objective-synthetic",
            "tdd_path": "04-local/synthetic-source__objective-synthetic/tdd.json",
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


def write_lineage(case_dir: Path, *, generated_by: dict = RECORDED) -> None:
    def write(relative_path: str, document: dict) -> None:
        path = case_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document), encoding="utf-8")

    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "brief.md").write_text("# Synthetic public supplier brief\n", encoding="utf-8")
    write(
        "01-scope/prd.json",
        {
            "version": "v1",
            "brief_path": "brief.md",
            "personas": [{"id": "p1", "description": "Synthetic investigator"}],
            "jobs_to_be_done": [
                {"id": "j1", "persona_id": "p1", "description": "Review supplier evidence"}
            ],
            "requirements": [{"id": "r1", "job_id": "j1", "description": "Show observed fields"}],
            "constraints": [],
            "non_goals": [],
            "definition_of_done": [
                {"id": "d1", "metric": "suppliers_total", "operator": ">=", "target": 1}
            ],
            "generated_by": generated_by,
        },
    )
    factor = {
        "id": "profile",
        "label": "Synthetic profile",
        "description": "Example category",
        "kind": "conceptual",
        "evidence": [],
    }
    write(
        "02-ontology/ontology.json",
        {
            "version": "v1",
            "prd_path": "01-scope/prd.json",
            "factors": [factor],
            "taxonomies": [
                {
                    "factor_id": "profile",
                    "root_label": "Profile",
                    "children": [
                        {
                            "id": "profile_local",
                            "label": "Local",
                            "level": 1,
                            "critic_label": "Good-Exclusive",
                        }
                    ],
                    "soundness": 1,
                    "coverage": 1,
                }
            ],
            "classes": [{"id": "Supplier", "aligned_to": "https://schema.org/Organization"}],
            "properties": [{"id": "legal_name", "datatype": "string"}],
            "shacl_path": "02-ontology/supplier-shape.ttl",
            "generated_by": generated_by,
        },
    )
    write(
        "03-fanout/objectives.json",
        {
            "ontology_version": "v1",
            "prd_path": "01-scope/prd.json",
            "objectives": [
                {
                    "id": "objective-synthetic",
                    "source_id": SOURCE_ID,
                    "source_url": URL,
                    "target_fields": list(CORE_FIELDS),
                    "priority": 1,
                    "expected_contribution": 1,
                }
            ],
            "generated_by": generated_by,
        },
    )
    prefix = "04-local/synthetic-source__objective-synthetic"
    write(
        f"{prefix}/local-prd.json",
        {
            "source_id": SOURCE_ID,
            "objective_id": "objective-synthetic",
            "global_prd_path": "01-scope/prd.json",
            "global_requirement_ids": ["r1"],
            "target_fields": list(CORE_FIELDS),
            "local_definition_of_done": [
                {"metric": "legal_name_completeness", "operator": ">=", "target": 1}
            ],
            "generated_by": generated_by,
        },
    )
    write(
        f"{prefix}/tdd.json",
        {
            "source_id": SOURCE_ID,
            "objective_id": "objective-synthetic",
            "local_prd_path": f"{prefix}/local-prd.json",
            "ontology_version": "v1",
            "source_url": URL,
            "allowed_domains": ["example.invalid"],
            "target_fields": list(CORE_FIELDS),
            "extraction_method": "dom",
            "validation_rules": ["Observed public evidence"],
            "rate_limit_per_minute": 1,
            "budget_usd": 1,
            "steps": [
                {
                    "id": "step-1",
                    "description": "Read synthetic supplier",
                    "starting_mode": "S1",
                    "allowed_modes": ["S1"],
                    "observation_channel": "text_structure",
                    "risk_tier": "SAFE",
                    "termination_predicate": "Observed name",
                }
            ],
            "generated_by": generated_by,
        },
    )


def test_partial_observations_export_missing_fields_and_verified_evidence(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    store = MemorySilverStore()
    name = observation(lake, "legal_name", "Proveedor Ejemplo 01", classified_as=("profile:local",))
    tax_id = observation(lake, "tax_id", "FAKE010101AAA")
    invalid_date = observation(lake, "founding_date", "not a date")
    for item in (name, tax_id, invalid_date, name):
        store.add(item)
    assert len(store.list_for_run(RUN_ID)) == 3
    result = refine_observations(store.list_for_run(RUN_ID), generated_by=RECORDED)
    assert len(result.rejected) == 1
    assert "SHACL" in result.rejected[0]["reason"]
    supplier = result.suppliers[0]
    assert supplier["fields"]["founding_date"]["status"] == "missing"
    assert supplier["fields"]["founding_date"]["value"] is None
    assert supplier["fields"]["sanction_status"]["status"] == "missing"
    assert (
        supplier["fields"]["legal_name"]["evidence"][0]["screenshot_key"]
        == name.evidence["screenshot_key"]
    )
    case_dir = tmp_path / "case"
    write_lineage(case_dir)
    metrics = export_run(
        lake,
        case_dir,
        "synthetic-case",
        RUN_ID,
        result.suppliers,
        trace=trace([name.value_id, tax_id.value_id]),
        taxonomy_levels={"profile": [["profile:local", "profile:other"]]},
        generated_by=RECORDED,
    )
    assert metrics["suppliers_total"] == 1
    assert metrics["suppliers_at_80pct_core"] == 0
    assert metrics["per_field_completeness"]["founding_date"] == 0
    assert metrics["per_field_completeness"]["legal_name"] == 1
    assert metrics["gold_values_without_evidence"] == 0
    assert metrics["level_ratio_coverage"]["profile"] == [0.5]
    assert metrics["mode_counts"]["S1"] == 1
    assert metrics["inference_backend"] == "recorded"
    assert not lake.exists("gold/synthetic-case/latest.json")
    assert json.loads(lake.read_key(f"gold/synthetic-case/{RUN_ID}/suppliers.jsonl")) == supplier
    assert lake.read_key(f"gold/synthetic-case/{RUN_ID}/contracts.jsonl") == b""
    assert (case_dir / "runs" / RUN_ID / "metrics.json").read_bytes() == lake.read_key(
        f"gold/synthetic-case/{RUN_ID}/metrics.json"
    )
    assert not (case_dir / "runs/latest/metrics.json").exists()
    assert not (case_dir / "runs" / RUN_ID / "suppliers.jsonl").exists()


def test_export_rejects_untraced_value_and_missing_screenshot(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    name = observation(lake, "legal_name", "Proveedor Ejemplo 01")
    supplier = refine_observations([name], generated_by=RECORDED).suppliers[0]
    write_lineage(tmp_path / "case")
    with pytest.raises(ValueError, match="untraceable value"):
        export_run(
            lake,
            tmp_path / "case",
            "synthetic-case",
            RUN_ID,
            [supplier],
            trace=trace([]),
            generated_by=RECORDED,
        )
    assert not lake.exists("gold/synthetic-case/latest.json")
    broken = observation(
        lake, "legal_name", "Proveedor Ejemplo 02", evidence=evidence(lake, screenshot=False)
    )
    broken_supplier = refine_observations([broken], generated_by=RECORDED).suppliers[0]
    with pytest.raises(ValueError, match="evidence object absent"):
        export_run(
            lake,
            tmp_path / "case",
            "synthetic-case",
            RUN_ID,
            [broken_supplier],
            trace=trace([broken.value_id]),
            generated_by=RECORDED,
        )


def test_export_rejects_broken_case_lineage(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    name = observation(lake, "legal_name", "Proveedor Ejemplo 01")
    case_dir = tmp_path / "case"
    write_lineage(case_dir)
    ontology_path = case_dir / "02-ontology/ontology.json"
    ontology = json.loads(ontology_path.read_text(encoding="utf-8"))
    ontology["version"] = "v2"
    ontology_path.write_text(json.dumps(ontology), encoding="utf-8")
    with pytest.raises(ValueError, match="ontology version"):
        export_run(
            lake,
            case_dir,
            "synthetic-case",
            RUN_ID,
            refine_observations([name], generated_by=RECORDED).suppliers,
            trace=trace([name.value_id]),
            generated_by=RECORDED,
        )
    assert not lake.exists("gold/synthetic-case/latest.json")


def test_recorded_value_cannot_be_labeled_live_or_satisfy_live_latest(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    recorded_name = observation(lake, "legal_name", "Proveedor Ejemplo 01")
    result = refine_observations([recorded_name], generated_by=VULTR)
    assert result.suppliers == []
    assert "differs from run" in result.rejected[0]["reason"]
    with pytest.raises(ValueError, match="mock- run ID"):
        export_run(
            lake,
            tmp_path / "case",
            "synthetic-case",
            "synthetic-live",
            [],
            generated_by=RECORDED,
        )
    assert not lake.exists("gold/synthetic-case/latest.json")


def test_synthetic_live_export_updates_latest_only_when_lineage_is_live(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    run_id = "synthetic-live"
    name = observation(
        lake,
        "legal_name",
        "Proveedor Ejemplo 01",
        run_id=run_id,
        generated_by=VULTR,
    )
    supplier = refine_observations([name], generated_by=VULTR).suppliers[0]
    case_dir = tmp_path / "case"
    write_lineage(case_dir, generated_by=RECORDED)
    with pytest.raises(ValueError, match="artifact inference backend"):
        export_run(
            lake,
            case_dir,
            "synthetic-case",
            run_id,
            [supplier],
            trace=trace([name.value_id], run_id=run_id, generated_by=VULTR),
            generated_by=VULTR,
        )
    assert not lake.exists("gold/synthetic-case/latest.json")
    write_lineage(case_dir, generated_by=VULTR)
    metrics = export_run(
        lake,
        case_dir,
        "synthetic-case",
        run_id,
        [supplier],
        trace=trace([name.value_id], run_id=run_id, generated_by=VULTR),
        generated_by=VULTR,
    )
    assert metrics["inference_backend"] == "vultr"
    assert json.loads(lake.read_key("gold/synthetic-case/latest.json"))["run_id"] == run_id
    assert (case_dir / "runs/latest/metrics.json").exists()


def test_ontology_shacl_and_conflict_do_not_invent_winner(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    shapes = tmp_path / "supplier-shape.ttl"
    shapes.write_text(
        """@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix onto: <https://ontofill.dev/ontology/> .
onto:SupplierShape a sh:NodeShape ; sh:targetClass onto:Supplier ;
  sh:property [ sh:path onto:legal_name ; sh:minCount 1 ; sh:datatype xsd:string ] ;
  sh:property [ sh:path onto:tax_id ; sh:minCount 1 ; sh:pattern "^FAKE" ] .
""",
        encoding="utf-8",
    )
    first = observation(lake, "legal_name", "Proveedor Ejemplo 01", confidence=0.7)
    second = observation(lake, "legal_name", "Proveedor Ejemplo 02", confidence=0.8)
    bad_tax = observation(lake, "tax_id", "INVALID")
    result = refine_observations([first, second, bad_tax], generated_by=RECORDED, shapes_ttl=shapes)
    assert len(result.rejected) == 1
    assert result.rejected[0]["value_id"] == bad_tax.value_id
    field = result.suppliers[0]["fields"]["legal_name"]
    assert field["status"] == "conflict"
    assert field["value"] == second.value
    assert result.suppliers[0]["fields"]["tax_id"]["status"] == "missing"
