"""Every engine inference call joins its gateway record to the same run and trace step."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from ontofill import workflow
from ontofill.inference import ModelValidationExhausted, VultrDecisionClient, complete_validated
from ontofill.lake.storage import FileLake
from ontofill.runfeed import RunFeed
from ontofill.workflow import _publish_decision_calls, run_case
from tests.approval_support import bind_approval


@pytest.mark.parametrize("supplied_run_id", [None, "run-r29-explicit"])
def test_run_id_exists_before_gateway_catalog_call(
    tmp_path: Path, monkeypatch, supplied_run_id: str | None
) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "brief.md").write_text("Find public library records.", encoding="utf-8")
    monkeypatch.setenv("ONTOFILL_GATEWAY_TOKEN", "synthetic-token")
    seen: list[str] = []

    def capture_factory(*, run_id: str):
        seen.append(run_id)
        raise RuntimeError("catalog intercepted")

    monkeypatch.setattr(VultrDecisionClient, "from_env", capture_factory)

    with pytest.raises(RuntimeError, match="catalog intercepted"):
        run_case(case, run_id=supplied_run_id)

    assert len(seen) == 1
    if supplied_run_id is None:
        assert seen[0].startswith("run-")
    else:
        assert seen[0] == supplied_run_id


def test_model_call_trace_uses_gateway_step_id() -> None:
    class Feed:
        def __init__(self) -> None:
            self.steps: list[dict] = []

        def append_step(self, step: dict, *, screenshot_key=None) -> None:
            self.steps.append(step)

    class Decision:
        def __init__(self) -> None:
            self.call_log = [
                {
                    "purpose": "phase2.schema",
                    "backend": "vultr",
                    "model": "synthetic-vultr",
                    "at": datetime.now(UTC).isoformat(),
                    "run_id": "run-r29",
                    "step_id": "step:gateway-r29",
                    "status": "ok",
                }
            ]

    feed = Feed()
    trace: list[dict] = []
    _publish_decision_calls(feed, trace, Decision(), 0, "run-r29", 2)

    assert trace[0]["run_id"] == "run-r29"
    assert trace[0]["step_id"] == "step:gateway-r29"
    assert feed.steps == trace


def test_p3_call_ids_and_engine_purposes_are_persisted_to_live_trace(tmp_path: Path) -> None:
    run_id = "run-r29b-p3"
    provenance = {
        "backend": "vultr",
        "model": "synthetic-vultr",
        "at": datetime.now(UTC).isoformat(),
    }
    purposes = (
        "phase3.plan_queries",
        "phase3.propose_publishers",
        "critic.phase3.capability",
        "phase3.redirect_candidate",
    )
    calls = [
        {
            "purpose": purpose,
            "backend": "vultr",
            "model": "synthetic-vultr",
            "at": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "step_id": f"step:r29b-{index}",
            "status": "ok",
        }
        for index, purpose in enumerate(purposes, 1)
    ]
    lake = FileLake(tmp_path / "lake")
    trace: list[dict] = []

    with RunFeed(lake, "case-r29b", run_id, provenance, start_heartbeat=False) as feed:
        _publish_decision_calls(feed, trace, SimpleNamespace(call_log=calls), 0, run_id, phase=3)

    persisted = [
        json.loads(line)
        for line in lake.read_key(f"runs/case-r29b/{run_id}/trace.live.jsonl").splitlines()
    ]
    assert {step["step_id"] for step in persisted} == {call["step_id"] for call in calls}
    assert {step["requested"]["engine_purpose"] for step in persisted} == set(purposes)


@pytest.mark.parametrize(
    ("call_run_id", "call_step_id"),
    [("run-other", "step:r29b-mismatch"), ("run-r29b", None)],
)
def test_live_call_trace_publication_refuses_unjoinable_attribution(
    call_run_id: str, call_step_id: str | None
) -> None:
    class Feed:
        def append_step(self, _step: dict, *, screenshot_key=None) -> None:
            raise AssertionError("unjoinable live call must not be published")

    call = {
        "purpose": "critic.phase3.capability",
        "backend": "vultr",
        "model": "synthetic-vultr",
        "at": datetime.now(UTC).isoformat(),
        "run_id": call_run_id,
        "step_id": call_step_id,
        "status": "ok",
    }

    with pytest.raises(ValueError, match="gateway attribution"):
        _publish_decision_calls(
            Feed(), [], SimpleNamespace(call_log=[call]), 0, "run-r29b", phase=3
        )


def test_standalone_refine_attributes_gateway_catalog_to_existing_run(
    tmp_path: Path, monkeypatch
) -> None:
    case = tmp_path / "case"
    ontology_path = case / "02-ontology/ontology.json"
    ontology_path.parent.mkdir(parents=True)
    ontology_path.write_text("{}\n", encoding="utf-8")
    approval = bind_approval(
        case,
        ["02-ontology/ontology.json"],
        {"approver": "Test Reviewer", "date": "2026-09-27", "checkpoint": "ontology"},
    )
    (ontology_path.parent / "APPROVED").write_text(json.dumps(approval), encoding="utf-8")

    class Lake:
        def read_key(self, _key: str) -> bytes:
            return b""

    class Store:
        def list_for_run(self, _run_id: str) -> list[object]:
            return [object()]

    monkeypatch.setattr(
        workflow,
        "_existing_run",
        lambda *_args, **_kwargs: (
            "test-case",
            Lake(),
            "run-r29-refine",
            {
                "generated_by": {
                    "backend": "vultr",
                    "model": "test-model",
                    "at": "2026-09-27T00:00:00Z",
                }
            },
        ),
    )
    monkeypatch.setattr(workflow, "silver_store_from_env", Store)
    monkeypatch.setattr(
        workflow,
        "replay_bronze_observations",
        lambda **_kwargs: SimpleNamespace(
            parse_job_records=[], parse_trace_steps=[], observations=[]
        ),
    )
    monkeypatch.setenv("ONTOFILL_GATEWAY_TOKEN", "synthetic-token")
    seen: list[str] = []

    def capture_factory(*, run_id: str):
        seen.append(run_id)
        raise RuntimeError("catalog intercepted")

    monkeypatch.setattr(VultrDecisionClient, "from_env", capture_factory)
    with pytest.raises(RuntimeError, match="catalog intercepted"):
        workflow.refine_case(case, run_id="run-r29-refine")
    assert seen == ["run-r29-refine"]


def test_complete_validated_notifies_observer_after_semantic_failure() -> None:
    observed: list[dict] = []

    class Decision:
        def __init__(self) -> None:
            self.call_log: list[dict] = []
            self.call_observer = observed.extend

        def complete_json(self, purpose: str, _prompt: str, _schema: dict) -> dict:
            self.call_log.append(
                {
                    "purpose": purpose,
                    "backend": "vultr",
                    "model": "synthetic-vultr",
                    "at": datetime.now(UTC).isoformat(),
                    "run_id": "run-r29b-live",
                    "step_id": "step:r29b-invalid",
                    "status": "ok",
                }
            )
            return {"ok": True}

    def reject_result(_result: dict) -> None:
        raise ValueError("synthetic result failed local check")

    with pytest.raises(ModelValidationExhausted):
        complete_validated(
            Decision(),
            "phase3.plan_queries",
            "synthetic prompt must not enter the observer payload",
            {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            reject_result,
            max_attempts=1,
        )

    assert len(observed) == 1
    assert observed[0]["step_id"] == "step:r29b-invalid"
    assert observed[0]["status"] == "validation_failed"
    assert observed[0]["reason"] == "synthetic result failed local check"
    assert len(observed[0]["reason"]) <= 300
    assert "prompt" not in observed[0]
    assert "response" not in observed[0]


def test_p3_decision_step_is_live_before_discovery_returns(tmp_path: Path) -> None:
    run_id = "run-r29b-live"
    case_id = "case-r29b-live"
    provenance = {
        "backend": "vultr",
        "model": "synthetic-vultr",
        "at": datetime.now(UTC).isoformat(),
    }
    lake = FileLake(tmp_path / "lake")
    feed = RunFeed(lake, case_id, run_id, provenance, start_heartbeat=False)
    p3_step_published = threading.Event()
    continue_p3 = threading.Event()
    worker_errors: list[BaseException] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "emit",
                                        "arguments": '{"ok": true}',
                                    }
                                }
                            ]
                        },
                    }
                ]
            }

    class Transport:
        def __init__(self) -> None:
            self.step_ids: list[str] = []

        def post(self, _url: str, *, headers: dict, json: dict) -> Response:
            self.step_ids.append(headers["X-BA-Step-Id"])
            assert json["messages"]
            return Response()

    transport = Transport()
    decision = VultrDecisionClient(
        api_key="synthetic-token",
        model="synthetic-vultr",
        base_url="https://gateway.invalid/v1",
        client=transport,
        run_id=run_id,
    )
    prior_observer_calls: list[dict] = []
    prior_observer = prior_observer_calls.extend
    decision.set_call_observer(prior_observer)
    live_scope = getattr(workflow, "_p3_decision_call_trace", None)
    assert callable(live_scope), "workflow must provide its P3 live-call trace scope"

    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }

    def discover_p3() -> None:
        try:
            with live_scope(feed, [], decision, run_id):
                complete_validated(decision, "phase3.plan_queries", "synthetic", schema)
                p3_step_published.set()
                if not continue_p3.wait(timeout=5):
                    raise TimeoutError("test did not release the still-running P3 stage")
                complete_validated(decision, "phase3.select_sources", "synthetic", schema)
        except Exception as exc:  # noqa: BLE001 - surface thread failures in the test thread
            worker_errors.append(exc)
            p3_step_published.set()

    with feed:
        worker = threading.Thread(target=discover_p3)
        worker.start()
        try:
            assert p3_step_published.wait(timeout=5)
            assert worker.is_alive(), "P3 must still be blocked in a later stage"
            first_trace = [
                json.loads(line)
                for line in lake.read_key(f"runs/{case_id}/{run_id}/trace.live.jsonl").splitlines()
            ]
            assert [step["step_id"] for step in first_trace] == [transport.step_ids[0]]
            assert first_trace[0]["requested"]["engine_purpose"] == "phase3.plan_queries"
        finally:
            continue_p3.set()
            worker.join(timeout=5)
        assert not worker.is_alive()

    assert worker_errors == []
    final_trace = [
        json.loads(line)
        for line in lake.read_key(f"runs/{case_id}/{run_id}/trace.live.jsonl").splitlines()
    ]
    trace_ids = [step["step_id"] for step in final_trace]
    assert trace_ids == [call["step_id"] for call in decision.call_log]
    assert len(trace_ids) == len(set(trace_ids))
    assert [call["step_id"] for call in prior_observer_calls] == trace_ids
    assert decision.call_observer is prior_observer
