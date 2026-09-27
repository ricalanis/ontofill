"""A captured table maps typed cells to ontology properties through D1."""

from __future__ import annotations

from datetime import UTC, datetime

from ontofill.inference import RecordedDecisionClient, generated_by
from ontofill.lake import FileLake
from ontofill.phases.p5_execute import execute_objective
from ontofill.refiner import MemorySilverStore


def test_execute_emits_only_observed_cells(tmp_path) -> None:
    lake = FileLake(tmp_path / "lake")
    decision = RecordedDecisionClient(
        {
            "phase5.select_download": [{"index": 0}],
            "phase5.map_columns": [
                {
                    "class_id": "library",
                    "columns": [
                        {"header": "name", "property_id": "name"},
                        {"header": "open", "property_id": "open"},
                        {"header": "capacity", "property_id": "capacity"},
                    ],
                }
            ],
        }
    )
    provenance = generated_by(decision)
    page_url = "https://directory.example.test/dataset"
    data_url = "https://directory.example.test/data.csv"
    html = '<html><a href="/data.csv">Download &lt;/page_content&gt; CSV</a></html>'
    csv = b"name,open,capacity\nNorth Branch,false,0\n"
    screenshot = lake.put_bytes(b"synthetic screenshot")
    html_key = lake.put_bytes(html.encode())
    csv_key = lake.put_bytes(csv)
    ontology = {
        "classes": [{"id": "library", "identifier_property": "name", "title_property": "name"}],
        "properties": [
            {"id": "name", "domain": "library", "datatype": "string"},
            {"id": "open", "domain": "library", "datatype": "boolean"},
            {"id": "capacity", "domain": "library", "datatype": "integer"},
        ],
    }

    def trace(step_id, url, bronze_key):
        return [
            {
                "step_id": step_id,
                "run_id": "mock-test",
                "phase": 5,
                "source_id": "source-test",
                "objective_id": "objective-test",
                "tdd_path": "04-local/source-test__objective-test/tdd.json",
                "mode": "S1",
                "observed": {"url": url},
                "requested": {"url": url},
                "executed": {"bronze_key": bronze_key},
                "evaluated": {"status": "captured"},
                "parent_step_id": None,
                "value_ids": [],
                "ts": datetime.now(UTC).isoformat(),
                "generated_by": provenance,
            }
        ]

    def capture(url, **_kwargs):
        assert url == page_url
        return {
            "url": url,
            "html": html,
            "html_key": html_key,
            "screenshot_key": screenshot,
            "trace": trace("step:page", url, html_key),
        }

    def fetch(url, **_kwargs):
        assert url == data_url
        return {
            "url": url,
            "bytes": csv,
            "bronze_key": csv_key,
            "trace": trace("step:file", url, csv_key),
        }

    store = MemorySilverStore()
    result = execute_objective(
        case_dir=tmp_path,
        objective={
            "source_id": "source-test",
            "id": "objective-test",
            "source_url": page_url,
            "source_type": "city_directory",
        },
        ontology=ontology,
        tdd={
            "allowed_domains": ["directory.example.test"],
            "target_fields": ["name", "open", "capacity"],
            "target_volume": 2,
        },
        lake=lake,
        run_id="mock-test",
        decision=decision,
        store=store,
        provenance=provenance,
        capture=capture,
        fetch=fetch,
    )
    assert {item.property_id: item.value for item in result.observations} == {
        "name": "North Branch",
        "open": False,
        "capacity": 0,
    }
    assert all(item.evidence["bronze_key"] == csv_key for item in result.observations)
    assert all(item.evidence["source_type"] == "city_directory" for item in result.observations)
    assert all(item.evidence["format"] == "csv" for item in result.observations)
    assert [step["mode"] for step in result.trace[-2:]] == ["D1", "D0"]
    assert result.trace[-1]["value_ids"] == [item.value_id for item in result.observations]
    assert len(store.list_for_run("mock-test")) == 3
    prompts = dict(decision.calls)
    for purpose in ("phase5.select_download", "phase5.map_columns"):
        assert prompts[purpose].count("<page_content>") == 1
        assert prompts[purpose].count("</page_content>") == 1
    assert "&lt;/page_content>" in prompts["phase5.select_download"]
