import json

import httpx
import pytest

from ontofill.inference import RecordedDecisionClient, VultrDecisionClient
from ontofill.phases.p1_scope.phase import draft_prd


def _prd_response() -> dict:
    return {
        "version": "1",
        "brief_path": "brief.md",
        "personas": [{"id": "reader", "description": "Reads public data"}],
        "jobs_to_be_done": [
            {"id": "find", "persona_id": "reader", "description": "Find reading rooms"}
        ],
        "requirements": [{"id": "evidence", "job_id": "find", "description": "Show evidence"}],
        "constraints": ["Read-only sources"],
        "non_goals": [],
        "definition_of_done": [
            {
                "id": "complete-rooms",
                "metric": "Reading rooms with 80% of required fields",
                "operator": ">=",
                "target": 5,
            }
        ],
        "authority_policy": {
            "jurisdiction": "Example City",
            "trusted_publishers": [],
            "unknown_source_action": "review",
        },
    }


def _tool_response(value: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "tool_calls": [
                            {"function": {"name": "emit", "arguments": json.dumps(value)}}
                        ]
                    },
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30},
        },
    )


def test_prd_threshold_is_inferred_by_separate_small_vultr_call(tmp_path) -> None:
    (tmp_path / "brief.md").write_text(
        "At least 80% of required fields per reading room need public evidence.", encoding="utf-8"
    )
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            criterion_schema = body["tools"][0]["function"]["parameters"]["properties"][
                "definition_of_done"
            ]["items"]
            assert "min_ratio" not in criterion_schema["properties"]
            return _tool_response(_prd_response())
        assert len(calls) == 2
        assert "phase1.dod_thresholds" in body["messages"][1]["content"]
        return _tool_response(
            {
                "ratios": [
                    {
                        "criterion_id": "complete-rooms",
                        "min_ratio": 0.8,
                        "evidence_quote": "80%",
                    }
                ]
            }
        )

    decision = VultrDecisionClient(
        api_key="test-only",
        model="glm-5.3-flash",
        document_model="qwen3.8-flash-next",
        client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    document = draft_prd(tmp_path, decision)
    assert document["definition_of_done"][0]["min_ratio"] == 0.8
    assert document["generated_by"]["model"] == "qwen3.8-flash-next"
    assert [body["model"] for body in calls] == ["qwen3.8-flash-next", "glm-5.3-flash"]
    assert [call["status"] for call in decision.call_log] == ["ok", "ok"]


def test_recorded_prd_without_explicit_ratio_needs_no_second_fixture(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Find public reading rooms.", encoding="utf-8")
    response = _prd_response()
    response["definition_of_done"][0]["metric"] = "Number of reading rooms"
    decision = RecordedDecisionClient({"phase1.prd": [response]})
    document = draft_prd(tmp_path, decision)
    assert "min_ratio" not in document["definition_of_done"][0]
    assert [purpose for purpose, _ in decision.calls] == ["phase1.prd"]


def test_explicit_ratio_requires_second_recorded_fixture(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("80% of fields must have evidence.", encoding="utf-8")
    decision = RecordedDecisionClient({"phase1.prd": [_prd_response()]})
    with pytest.raises(AssertionError, match="phase1.dod_thresholds"):
        draft_prd(tmp_path, decision)


def test_ratio_without_matching_quote_is_rejected(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("80% of fields must have evidence.", encoding="utf-8")
    decision = RecordedDecisionClient(
        {
            "phase1.prd": [_prd_response()],
            "phase1.dod_thresholds": [
                {
                    "ratios": [
                        {
                            "criterion_id": "complete-rooms",
                            "min_ratio": 0.9,
                            "evidence_quote": "90%",
                        }
                    ]
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="threshold evidence"):
        draft_prd(tmp_path, decision)
