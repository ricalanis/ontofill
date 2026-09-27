"""Bounded feedback for malformed typed model decisions."""

from __future__ import annotations

import json

import httpx
import pytest

from ontofill import workflow
from ontofill.inference import (
    ModelValidationExhausted,
    RecordedDecisionClient,
    VultrDecisionClient,
    complete_validated,
    generated_by,
)
from ontofill.lake import FileLake

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["choice"],
    "properties": {"choice": {"type": "string"}},
}


def _valid_choice(result: dict) -> None:
    if result["choice"] == "repeat":
        raise ValueError("choice must not repeat a prior item")


def test_semantic_rejection_retries_with_exact_feedback_and_attempt_log() -> None:
    decision = RecordedDecisionClient({"choose": [{"choice": "repeat"}, {"choice": "fresh"}]})

    result = complete_validated(decision, "choose", "Choose one item", SCHEMA, _valid_choice)

    assert result == {"choice": "fresh"}
    assert len(decision.calls) == 2
    assert decision.call_log[0]["status"] == "validation_failed"
    assert decision.call_log[0]["reason"] == "choice must not repeat a prior item"
    assert decision.call_log[1]["status"] == "ok"
    assert "choice must not repeat a prior item" in decision.calls[1][1]
    assert "<page_content>" in decision.calls[1][1]


def test_three_invalid_recorded_answers_exhaust_only_model_validation() -> None:
    decision = RecordedDecisionClient({"choose": [{"choice": "repeat"}] * 3})

    with pytest.raises(ModelValidationExhausted, match="after 3 attempts") as raised:
        complete_validated(decision, "choose", "Choose one item", SCHEMA, _valid_choice)

    assert raised.value.reason == "choice must not repeat a prior item"
    assert len(decision.call_log) == 3
    assert all(call["status"] == "validation_failed" for call in decision.call_log)


def test_vultr_json_schema_retries_with_validator_error_in_next_prompt() -> None:
    prompts = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompts.append(body["messages"][1]["content"])
        result = {} if len(prompts) == 1 else {"choice": "fresh"}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {"function": {"name": "emit", "arguments": json.dumps(result)}}
                            ]
                        },
                    }
                ]
            },
        )

    client = VultrDecisionClient(
        api_key="synthetic",
        model="glm-5.3-flash",
        base_url="https://example.invalid/v1",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.complete_json("choose", "Choose one item", SCHEMA) == {"choice": "fresh"}
    assert len(prompts) == 2
    assert "'choice' is a required property" in prompts[1]
    assert "<page_content>" in prompts[1]
    assert [record["status"] for record in client.call_log] == ["invalid_response", "ok"]
    assert client.call_log[0]["reason"] == "'choice' is a required property"


def test_p2_exhaustion_pauses_run_with_three_attempt_steps(tmp_path, monkeypatch) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "brief.md").write_text("Describe public records.\n", encoding="utf-8")
    factor = {
        "id": "region",
        "label": "Region",
        "description": "Where a record applies",
        "kind": "conceptual",
        "evidence": [],
    }
    decision = RecordedDecisionClient({"phase2.factors": [{"factors": [factor, factor]}] * 3})
    provenance = generated_by(decision)
    prd = {
        "requirements": [],
        "definition_of_done": [],
        "jobs_to_be_done": [],
        "generated_by": provenance,
    }
    monkeypatch.setattr(workflow, "draft_prd", lambda *_args, **_kwargs: prd)
    monkeypatch.setattr(workflow, "require_approval", lambda *_args, **_kwargs: True)
    lake = FileLake(tmp_path / "lake")
    run_id = "mock-r22-p2-exhaustion"

    code = workflow.run_case(
        case,
        run_id=run_id,
        decision=decision,
        lake=lake,
        preview_past_checkpoints=True,
        to_phase=2,
    )

    assert code == 3
    status = json.loads(lake.read_key(f"runs/case/{run_id}/status.json"))
    assert status["state"] == "paused"
    assert status["checkpoint_pending"] == "factors"
    trace = [
        json.loads(line)
        for line in lake.read_key(f"runs/case/{run_id}/trace.live.jsonl").splitlines()
    ]
    factor_calls = [step for step in trace if step["observed"].get("artifact") == "phase2.factors"]
    assert len(factor_calls) == 3
    assert all(step["evaluated"]["status"] == "validation_failed" for step in factor_calls)
    assert not (
        workflow._scratch_case(case, run_id)[0] / "02-ontology/factors/factors.json"
    ).exists()
