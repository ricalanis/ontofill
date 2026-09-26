"""A synthetic page and CSV exercise literal cell evidence through the S1 channel."""

from __future__ import annotations

from datetime import UTC, datetime

from ontofill.inference import RecordedDecisionClient, generated_by
from ontofill.lake import FileLake
from ontofill.phases.p5_execute import execute_objective
from ontofill.refiner import MemorySilverStore


def test_execute_emits_only_observed_cells(tmp_path) -> None:
    lake = FileLake(tmp_path / "lake")
    decision = RecordedDecisionClient({"phase5.select_download": [{"index": 0}]})
    provenance = generated_by(decision)
    page_url = "https://registry.example.test/dataset"
    data_url = "https://registry.example.test/data.csv"
    html = '<html><a href="/data.csv">Download CSV</a></html>'
    csv = b"name,tax_id,address\nProveedor Ejemplo 01,FAKE010101AAA,1 Test Street\n"
    screenshot = lake.put_bytes(b"synthetic screenshot")
    html_key = lake.put_bytes(html.encode())
    csv_key = lake.put_bytes(csv)

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
        objective={"source_id": "source-test", "id": "objective-test", "source_url": page_url},
        tdd={"allowed_domains": ["registry.example.test"]},
        lake=lake,
        run_id="mock-test",
        decision=decision,
        store=store,
        provenance=provenance,
        capture=capture,
        fetch=fetch,
    )
    assert {item.field for item in result.observations} == {"legal_name", "tax_id", "address"}
    assert {item.value for item in result.observations} == {
        "Proveedor Ejemplo 01",
        "FAKE010101AAA",
        "1 Test Street",
    }
    assert all(item.evidence["bronze_key"] == csv_key for item in result.observations)
    assert result.trace[-1]["value_ids"] == [item.value_id for item in result.observations]
    assert len(store.list_for_run("mock-test")) == 3
