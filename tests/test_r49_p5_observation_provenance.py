"""R49 keeps confirmed source authority on every P5 observation path."""

from __future__ import annotations

from ontofill.lake import FileLake
from ontofill.phases.p5_execute.controller import _emit_rows
from ontofill.refiner import MemorySilverStore
from ontofill.refiner.provenance import observation_source_metadata

RECORDED = {"backend": "recorded", "model": "synthetic", "at": "2026-01-01T00:00:00Z"}


def test_observation_metadata_uses_structured_publisher_fields_and_defaults_unknown() -> None:
    objective = {
        "authority_tier": "unknown",
        "publisher_of_record": {
            "kind": "  Example   Public Data Office ",
            "domain": "portal.example.test",
            "tier": "primary",
            "basis": "approved_policy",
            "evidence_quote": "Example Public Data Office publishes this registry.",
        },
        "page_text": "A different authority claims it is the publisher.",
    }

    assert observation_source_metadata(objective) == {
        "authority_tier": "primary",
        "publisher_id": "name:example public data office",
    }
    assert observation_source_metadata(
        {"source_url": "https://official.example.test", "page_text": "official agency"}
    ) == {"authority_tier": "unknown", "publisher_id": None}


def test_controller_observations_inherit_objective_source_metadata(tmp_path) -> None:
    lake = FileLake(tmp_path / "lake")
    screenshot_key = lake.put_bytes(b"synthetic controller screenshot")
    captured_at = "2026-01-01T00:00:00Z"
    url = "https://agency.example.test/record/1"
    objective = {
        "authority_tier": "secondary",
        "publisher_of_record": {
            "kind": "Example Public Data Office",
            "domain": "agency.example.test",
            "tier": "secondary",
            "basis": "captured_page",
            "evidence_quote": "Published by Example Public Data Office.",
        },
    }
    values = {
        "record_id": {"value": "R-1", "selector": "row 1 / id"},
        "name": {"value": "Synthetic Record", "selector": "row 1 / name"},
    }
    controller_steps = [
        {
            "step_id": "extract-1",
            "requested": {"tool": "extract", "args": {"fields": {"record_id": {}, "name": {}}}},
            "executed": {"values": values},
            "observed": {"url": url},
            "screenshot_key": screenshot_key,
            "ts": captured_at,
            "parent_step_id": None,
        }
    ]
    rows = [
        {
            property_id: {
                **cell,
                "url": url,
                "screenshot_key": screenshot_key,
                "step_id": "extract-1",
                "captured_at": captured_at,
            }
            for property_id, cell in values.items()
        }
    ]
    ontology = {
        "classes": [{"id": "Record", "identifier_property": "record_id", "title_property": "name"}],
        "properties": [
            {"id": "record_id", "domain": "Record", "datatype": "string"},
            {"id": "name", "domain": "Record", "datatype": "string"},
        ],
    }
    store = MemorySilverStore()

    def coerce(value: object, _datatype: str) -> str | None:
        return value if isinstance(value, str) else None

    observations, _trace = _emit_rows(
        rows=rows,
        target_fields=["record_id", "name"],
        ontology=ontology,
        objective=objective,
        allowed_domains=["agency.example.test"],
        lake=lake,
        store=store,
        run_id="mock-r49-p5",
        source_id="source-agency",
        source_type="registry",
        objective_id="objective-agency",
        tdd_path="04-local/source-agency__objective-agency/tdd.json",
        provenance=RECORDED,
        coerce=coerce,
        safe_screenshots={screenshot_key},
        controller_steps=controller_steps,
        target_volume=10,
    )

    assert len(observations) == 2
    assert all(
        (item.authority_tier, item.publisher_id) == ("secondary", "name:example public data office")
        for item in observations
    )
    assert {item.value_id for item in store.list_for_run("mock-r49-p5")} == {
        item.value_id for item in observations
    }
