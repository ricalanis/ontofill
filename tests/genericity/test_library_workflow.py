"""A brief-only second case traverses the same scratch pipeline."""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from ontofill_scrape import SearchResult

from ontofill.phases.p1_scope.phase import draft_prd
from ontofill.phases.p2_ontology.phase import draft_factors, draft_ontology
from ontofill.workflow import _scratch_case, export_case, run_case
from tests.genericity.fixtures.libraries import library_decisions

BRIEF = Path(__file__).parent / "cases/libraries/brief.md"
HTML = """<html><a href="/branches.csv">Download CSV</a><table><tr><th>Branch</th><th>Free internet</th><th>Hours</th></tr>
<tr><td>North Branch</td><td>true</td><td>Mon-Fri 09:00-17:00</td></tr></table></html>"""


class LibrarySearch:
    name = "synthetic_directory"

    def search(self, query: str):
        assert "Example City" in query
        return [
            SearchResult(
                "https://libraries.example.test/branches",
                "City library directory",
                "Official branches and hours",
            )
        ]


def _capture(url: str, **kwargs):
    lake = kwargs["lake"]
    key = lake.put_bytes(HTML.encode())
    screenshot = lake.put_bytes(b"synthetic screenshot")
    return {
        "url": url,
        "html": HTML,
        "html_key": key,
        "screenshot_key": screenshot,
        "trace": [
            {
                "step_id": f"step:{uuid.uuid4().hex}",
                "run_id": kwargs["run_id"],
                "phase": 5,
                "source_id": kwargs["source_id"],
                "objective_id": kwargs["objective_id"],
                "tdd_path": kwargs["tdd_path"],
                "mode": "S1",
                "observed": {"url": url},
                "requested": {"url": url},
                "executed": {"bronze_key": key},
                "evaluated": {"status": "captured"},
                "parent_step_id": None,
                "value_ids": [],
                "ts": datetime.now(UTC).isoformat(),
                "generated_by": kwargs["generated_by"],
            }
        ],
    }


def _fetch(url: str, **kwargs):
    lake = kwargs["lake"]
    content = b"Branch,Free internet,Hours\nNorth Branch,true,Mon-Fri 09:00-17:00\n"
    key = lake.put_bytes(content)
    timestamp = datetime.now(UTC).isoformat()
    return {
        "url": url,
        "bytes": content,
        "bronze_key": key,
        "trace": [
            {
                "step_id": f"step:{uuid.uuid4().hex}",
                "run_id": kwargs["run_id"],
                "phase": 5,
                "source_id": kwargs["source_id"],
                "objective_id": kwargs["objective_id"],
                "tdd_path": kwargs["tdd_path"],
                "mode": "D0",
                "observed": {"url": url},
                "requested": {"url": url},
                "executed": {"bronze_key": key},
                "evaluated": {"status": "captured"},
                "parent_step_id": None,
                "value_ids": [],
                "ts": timestamp,
                "generated_by": kwargs["generated_by"],
            }
        ],
    }


def test_library_brief_runs_all_phases_with_generic_gold(tmp_path) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "brief.md").write_text(BRIEF.read_text(encoding="utf-8"), encoding="utf-8")
    run_id = f"mock-{uuid.uuid4().hex[:16]}"
    result = run_case(
        case,
        run_id=run_id,
        decision=library_decisions(),
        preview_past_checkpoints=True,
        search_client=LibrarySearch(),
        capture=_capture,
        fetch=_fetch,
    )
    assert result == 3
    assert sorted(path.name for path in case.iterdir()) == ["brief.md"]
    scratch, lake = _scratch_case(case, run_id)
    ontology = json.loads((scratch / "02-ontology/ontology.json").read_text())
    assert ontology["primary_class"] == "library"
    assert ontology["properties"][1]["datatype"] == "boolean"
    prefix = f"gold/{case.name}/{run_id}"
    assert lake.exists(f"{prefix}/entities.jsonl")
    assert lake.exists(f"{prefix}/ontology.json")
    entities = [json.loads(line) for line in lake.read_key(f"{prefix}/entities.jsonl").splitlines()]
    assert len(entities) == 1
    assert entities[0]["class"] == "library"
    assert entities[0]["properties"]["free_internet"]["value"] is True
    metrics = json.loads(lake.read_key(f"{prefix}/metrics.json"))
    assert metrics["entities_total"] == {"library": 1}
    assert metrics["entities_meeting_dod"] == {"library": 1}
    assert metrics["values_without_evidence"] == 0
    assert {item["criterion_id"] for item in metrics["dod"]} == {
        "library_count",
        "evidence_integrity",
    }
    assert all(item["met"] is False for item in metrics["dod"])
    assert metrics["inference_backend"] == "recorded"
    assert export_case(case, run_id=run_id) == 0
    assert not lake.exists(f"gold/{case.name}/latest.json")
    assert not lake.exists(f"runs/{case.name}/latest.json")


def test_ontology_repairs_invalid_class_property_reference(tmp_path) -> None:
    (tmp_path / "brief.md").write_text(BRIEF.read_text(encoding="utf-8"), encoding="utf-8")
    decision = library_decisions()
    invalid = deepcopy(decision.responses["phase2.schema"][0])
    invalid["classes"][0]["identifier_property"] = "missing_property"
    decision.responses["phase2.schema"].appendleft(invalid)
    prd = draft_prd(tmp_path, decision)
    factors = draft_factors(tmp_path, prd, decision)
    ontology = draft_ontology(tmp_path, prd, factors, decision)
    assert ontology["classes"][0]["identifier_property"] == "name"
    prompts = [prompt for purpose, prompt in decision.calls if purpose == "phase2.schema"]
    assert len(prompts) == 2
    assert "identifier_property must refer to a property of its class" in prompts[1]
