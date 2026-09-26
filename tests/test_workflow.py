"""Recorded end-to-end preview stays in scratch and cannot advance latest pointers."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from ontofill_scrape import SearchResult

from ontofill.workflow import _preview_decision, export_case, run_case


class SyntheticSearch:
    def search(self, query):
        assert "Example City" in query
        return [SearchResult("https://registry.example.test/data", "Example City public records")]


def test_recorded_workflow_uses_scratch_and_never_claims_approval(tmp_path) -> None:
    case = tmp_path / "tracked-case"
    case.mkdir()
    (case / "brief.md").write_text("Suppliers in Example City", encoding="utf-8")
    html = '<html><a href="/suppliers.csv">Download</a></html>'
    csv = (
        b"legal_name,tax_id,address,founding_date,tax_list_status,sanction_status\n"
        b"Proveedor Ejemplo 01,FAKE010101AAA,1 Test Street,2020-01-01,clear,clear\n"
    )

    def trace(run_id, source_id, objective_id, tdd_path, provenance, url, key):
        return [
            {
                "step_id": f"step:{key[-12:]}",
                "run_id": run_id,
                "phase": 5,
                "source_id": source_id,
                "objective_id": objective_id,
                "tdd_path": tdd_path,
                "mode": "S1",
                "observed": {"url": url},
                "requested": {"url": url},
                "executed": {"bronze_key": key},
                "evaluated": {"status": "captured"},
                "parent_step_id": None,
                "value_ids": [],
                "ts": datetime.now(UTC).isoformat(),
                "generated_by": provenance,
            }
        ]

    def capture(url, **kwargs):
        lake = kwargs["lake"]
        key = lake.put_bytes(html.encode())
        screenshot = lake.put_bytes(b"synthetic screenshot")
        return {
            "url": url,
            "html": html,
            "html_key": key,
            "screenshot_key": screenshot,
            "trace": trace(
                kwargs["run_id"],
                kwargs["source_id"],
                kwargs["objective_id"],
                kwargs["tdd_path"],
                kwargs["generated_by"],
                url,
                key,
            ),
        }

    def fetch(url, **kwargs):
        lake = kwargs["lake"]
        key = lake.put_bytes(csv)
        return {
            "url": url,
            "bytes": csv,
            "bronze_key": key,
            "trace": trace(
                kwargs["run_id"],
                kwargs["source_id"],
                kwargs["objective_id"],
                kwargs["tdd_path"],
                kwargs["generated_by"],
                url,
                key,
            ),
        }

    run_id = "mock-" + uuid.uuid4().hex
    assert (
        run_case(
            case,
            run_id=run_id,
            preview_past_checkpoints=True,
            decision=_preview_decision("Suppliers in Example City"),
            search_client=SyntheticSearch(),
            capture=capture,
            fetch=fetch,
        )
        == 3
    )
    assert list(case.iterdir()) == [case / "brief.md"]
    from ontofill.workflow import _scratch_case

    scratch, lake = _scratch_case(case, run_id)
    assert (scratch / "01-scope/APPROVAL_PENDING.md").exists()
    assert (scratch / "02-ontology/factors/APPROVAL_PENDING.md").exists()
    assert (scratch / "02-ontology/APPROVAL_PENDING.md").exists()
    assert not lake.exists(f"gold/{case.name}/latest.json")
    assert not lake.exists(f"runs/{case.name}/latest.json")
    metrics = json.loads(lake.read_key(f"gold/{case.name}/{run_id}/metrics.json"))
    assert metrics["suppliers_total"] == 1
    assert metrics["inference_backend"] == "recorded"
    assert metrics["preview"] is True
    assert metrics["suppliers_at_80pct_core"] == 1
    assert export_case(case, run_id=run_id) == 0
    assert not lake.exists(f"gold/{case.name}/latest.json")
    status = json.loads(lake.read_key(f"runs/{case.name}/{run_id}/status.json"))
    assert status["state"] == "paused"
    assert status["checkpoint_pending"] == "prd"
    assert status["preview"] is True
    assert status["sources"][0]["source_type"] == "supplier website"
    assert status["sources"][0]["format"] == "csv"


def test_recorded_default_pauses_at_first_checkpoint(tmp_path, capsys) -> None:
    case = tmp_path / "tracked-case"
    case.mkdir()
    (case / "brief.md").write_text("Suppliers in Example City", encoding="utf-8")
    run_id = "mock-" + uuid.uuid4().hex
    assert (
        run_case(case, run_id=run_id, decision=_preview_decision("Suppliers in Example City")) == 3
    )
    from ontofill.workflow import _scratch_case

    scratch, lake = _scratch_case(case, run_id)
    assert (scratch / "01-scope/prd.json").exists()
    prd = json.loads((scratch / "01-scope/prd.json").read_text())
    assert not prd["jobs_to_be_done"][0]["description"].startswith("#")
    assert {item["metric"] for item in prd["definition_of_done"]} == {
        "suppliers_total",
        "suppliers_at_80pct_core",
        "distinct_source_types",
        "gold_values_without_evidence",
    }
    assert not (scratch / "02-ontology/factors/factors.json").exists()
    assert not lake.exists(f"gold/{case.name}/{run_id}/metrics.json")
    output = capsys.readouterr().out
    assert f"run_id={run_id}" in output
    assert "checkpoint_pending=prd" in output
    assert "recorded artifacts cannot satisfy" in output
    (scratch / "01-scope/APPROVED").write_text(
        '{"approver":"Example Reviewer","date":"2026-09-26","checkpoint":"prd"}',
        encoding="utf-8",
    )
    assert (
        run_case(case, run_id=run_id, decision=_preview_decision("Suppliers in Example City")) == 3
    )
    assert (
        "APPROVED exists but the artifact was produced by the recorded backend"
        in capsys.readouterr().out
    )
