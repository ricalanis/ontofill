"""Recorded retries for locally validated Phase 5 model steps."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ontofill.inference import RecordedDecisionClient, generated_by
from ontofill.lake import FileLake
from ontofill.phases.p5_execute import execute_objectives
from ontofill.refiner import MemorySilverStore
from ontofill.sandbox.capture import CaptureError, CaptureIntegrityError
from tests.r17_helpers import SyntheticParseExecutor

_BAD_MAPPING = {
    "class_id": "record",
    "columns": [
        {"header": "id", "property_id": "record_id"},
        {"header": "label", "property_id": "record_id"},
    ],
}
_GOOD_MAPPING = {
    "class_id": "record",
    "columns": [{"header": "id", "property_id": "record_id"}],
}
_VALIDATION_REASON = "mapping columns and properties must be unique"


def _source(source_id: str) -> dict:
    return {
        "id": f"objective-{source_id}",
        "source_id": source_id,
        "source_url": f"https://{source_id}.example.test/catalog",
        "source_type": "generic_public_directory",
    }


def _run(
    tmp_path,
    mappings: list[dict],
    source_ids: list[str],
    selections: list[dict] | None = None,
    fetch_fail_sources: set[str] | None = None,
    fetch_failure_type: type[CaptureError] = CaptureError,
):
    objectives = [_source(source_id) for source_id in source_ids]
    ontology = {
        "classes": [
            {
                "id": "record",
                "identifier_property": "record_id",
                "title_property": "record_id",
            }
        ],
        "properties": [{"id": "record_id", "domain": "record", "datatype": "string"}],
    }
    tdds = {
        objective["id"]: {
            "allowed_domains": [f"{objective['source_id']}.example.test"],
            "target_fields": ["record_id"],
            "target_volume": 10,
        }
        for objective in objectives
    }
    decision = RecordedDecisionClient(
        {
            "phase5.select_download": selections or [{"index": 0} for _ in objectives],
            "phase5.map_columns": mappings,
        }
    )
    provenance = generated_by(decision)
    lake = FileLake(tmp_path / "lake")
    payload = b"id,label\nrecord-1,Example Record\n"

    def step(source_id: str, objective_id: str, url: str, key: str, step_id: str) -> dict:
        return {
            "step_id": step_id,
            "run_id": "mock-r22-p5",
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
            "generated_by": provenance,
        }

    def capture(url: str, **kwargs) -> dict:
        source_id = kwargs["source_id"]
        objective_id = kwargs["objective_id"]
        host = f"{source_id}.example.test"
        html = f'<html><a href="https://{host}/data.csv">Download CSV</a></html>'
        html_key = lake.put_bytes(html.encode())
        screenshot_key = lake.put_bytes(b"synthetic screenshot")
        return {
            "url": url,
            "html": html,
            "html_key": html_key,
            "screenshot_key": screenshot_key,
            "trace": [step(source_id, objective_id, url, html_key, f"page-{source_id}")],
        }

    def fetch(url: str, **kwargs) -> dict:
        source_id = kwargs["source_id"]
        objective_id = kwargs["objective_id"]
        if source_id in (fetch_fail_sources or set()):
            raise fetch_failure_type("fetch pod failed: temporary name resolution failure")
        key = lake.put_bytes(payload)
        return {
            "url": url,
            "bytes": payload,
            "bronze_key": key,
            "content_type": "text/csv",
            "status": 200,
            "trace": [step(source_id, objective_id, url, key, f"file-{source_id}")],
        }

    results = execute_objectives(
        case_dir=tmp_path,
        objectives=objectives,
        ontology=ontology,
        tdds=tdds,
        lake=lake,
        run_id="mock-r22-p5",
        decision=decision,
        store=MemorySilverStore(),
        provenance=provenance,
        capture=capture,
        fetch=fetch,
        parse_executor=SyntheticParseExecutor(),
    )
    return results, decision


def test_fetch_transport_failure_fails_only_its_source_and_keeps_page_receipt(tmp_path) -> None:
    results, _decision = _run(
        tmp_path,
        [_GOOD_MAPPING],
        ["source-failed", "source-next"],
        fetch_fail_sources={"source-failed"},
    )

    failed, succeeded = results
    assert failed.failed is True
    assert "temporary name resolution failure" in failed.failure_reason
    assert failed.observations == []
    assert any(step["step_id"] == "page-source-failed" for step in failed.trace)
    assert any(step["evaluated"].get("status") == "source_failed" for step in failed.trace)
    assert succeeded.failed is False
    assert [item.value for item in succeeded.observations] == ["record-1"]


def test_fetch_containment_failure_still_stops_the_batch(tmp_path) -> None:
    with pytest.raises(CaptureIntegrityError, match="fetch pod failed"):
        _run(
            tmp_path,
            [],
            ["source-failed", "source-next"],
            fetch_fail_sources={"source-failed"},
            fetch_failure_type=CaptureIntegrityError,
        )


def _mapping_steps(result) -> list[dict]:
    return [
        step
        for step in result.trace
        if step.get("requested", {}).get("purpose") == "phase5.map_columns"
    ]


def test_invalid_mapping_then_valid_answer_records_two_attempts(tmp_path) -> None:
    results, decision = _run(tmp_path, [_BAD_MAPPING, _GOOD_MAPPING], ["source-one"])

    assert len(results) == 1
    result = results[0]
    assert result.failed is False
    assert [item.value for item in result.observations] == ["record-1"]
    attempts = _mapping_steps(result)
    assert len(attempts) == 2
    assert [step["requested"]["attempt"] for step in attempts] == [1, 2]
    assert attempts[0]["evaluated"] == {
        "status": "validation_failed",
        "reason": _VALIDATION_REASON,
    }
    assert attempts[1]["evaluated"]["status"] == "ok"
    assert all(
        (step["source_id"], step["objective_id"]) == ("source-one", "objective-source-one")
        for step in attempts
    )
    mapping_prompts = [
        prompt for purpose, prompt in decision.calls if purpose == "phase5.map_columns"
    ]
    assert len(mapping_prompts) == 2
    assert _VALIDATION_REASON in mapping_prompts[1]
    mapping_calls = [
        call for call in decision.call_log if call.get("purpose") == "phase5.map_columns"
    ]
    assert [call["status"] for call in mapping_calls] == ["validation_failed", "ok"]
    assert mapping_calls[0]["reason"] == _VALIDATION_REASON
    assert all(
        (call["source_id"], call["objective_id"], call["tdd_path"])
        == (
            "source-one",
            "objective-source-one",
            "04-local/source-one__objective-source-one/tdd.json",
        )
        for call in mapping_calls
    )


def test_three_invalid_mappings_fail_only_that_source_and_continue(tmp_path) -> None:
    results, _decision = _run(
        tmp_path,
        [_BAD_MAPPING, _BAD_MAPPING, _BAD_MAPPING, _GOOD_MAPPING],
        ["source-bad", "source-next"],
    )

    assert len(results) == 2
    failed, succeeded = results
    assert failed.failed is True
    assert failed.failure_reason == _VALIDATION_REASON
    assert failed.observations == []
    failed_attempts = _mapping_steps(failed)
    assert len(failed_attempts) == 3
    assert [step["requested"]["attempt"] for step in failed_attempts] == [1, 2, 3]
    assert {step["source_id"] for step in failed_attempts} == {"source-bad"}
    assert {step["objective_id"] for step in failed_attempts} == {"objective-source-bad"}
    assert succeeded.failed is False
    assert [item.value for item in succeeded.observations] == ["record-1"]
    assert {step["source_id"] for step in _mapping_steps(succeeded)} == {"source-next"}
    assert not list((tmp_path / "05-execute/macros").glob("source-bad-*.json"))


def test_invalid_download_selection_retries_with_schema_error(tmp_path) -> None:
    results, decision = _run(
        tmp_path,
        [_GOOD_MAPPING],
        ["source-select"],
        selections=[{"index": 1}, {"index": 0}],
    )

    result = results[0]
    assert result.failed is False
    selection_steps = [
        step
        for step in result.trace
        if step.get("requested", {}).get("purpose") == "phase5.select_download"
    ]
    assert len(selection_steps) == 2
    assert [step["requested"]["attempt"] for step in selection_steps] == [1, 2]
    assert selection_steps[0]["evaluated"]["status"] == "validation_failed"
    assert "greater than the maximum" in selection_steps[0]["evaluated"]["reason"]
    prompts = [prompt for purpose, prompt in decision.calls if purpose == "phase5.select_download"]
    assert len(prompts) == 2
    assert "greater than the maximum" in prompts[1]
    selection_calls = [
        call for call in decision.call_log if call.get("purpose") == "phase5.select_download"
    ]
    assert selection_calls[0]["status"] == "invalid_response"
    assert "greater than the maximum" in selection_calls[0]["reason"]


def test_exhausted_download_selection_fails_one_source_and_continues(tmp_path) -> None:
    results, _decision = _run(
        tmp_path,
        [_GOOD_MAPPING, _GOOD_MAPPING],
        ["source-select-bad", "source-select-next"],
        selections=[{"index": 1}, {"index": 1}, {"index": 1}, {"index": 0}],
    )

    failed, succeeded = results
    assert failed.failed is True
    assert "greater than the maximum" in failed.failure_reason
    selection_steps = [
        step
        for step in failed.trace
        if step.get("requested", {}).get("purpose") == "phase5.select_download"
    ]
    assert len(selection_steps) == 3
    assert {step["source_id"] for step in selection_steps} == {"source-select-bad"}
    assert all(step["evaluated"]["status"] == "validation_failed" for step in selection_steps)
    assert succeeded.failed is False
    assert [item.value for item in succeeded.observations] == ["record-1"]
