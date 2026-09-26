import json

import httpx
import pytest

from ontofill.inference import RecordedDecisionClient, VultrDecisionClient, generated_by

SCHEMA = {
    "type": "object",
    "required": ["choice"],
    "properties": {"choice": {"enum": ["first", "second"]}},
    "additionalProperties": False,
}


def test_recorded_decisions_validate_and_are_consumed_once() -> None:
    client = RecordedDecisionClient({"choose": [{"choice": "first"}]})
    assert client.complete_json("choose", "pick", SCHEMA) == {"choice": "first"}
    with pytest.raises(AssertionError):
        client.complete_json("choose", "pick again", SCHEMA)


def test_vultr_client_uses_only_inference_endpoint_and_validates_response() -> None:
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "emit",
                                        "arguments": '{"choice":"second"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handle)
    client = VultrDecisionClient(
        api_key="test-only", model="test-model", client=httpx.Client(transport=transport)
    )
    assert client.complete_json("choose", "pick", SCHEMA) == {"choice": "second"}
    assert seen[0].url.path == "/v1/chat/completions"
    assert seen[0].headers["Authorization"] == "Bearer test-only"
    body = json.loads(seen[0].content)
    assert body["tool_choice"] == {"type": "function", "function": {"name": "emit"}}
    assert body["tools"][0]["function"]["parameters"] == SCHEMA
    assert body["tools"][0]["function"]["strict"] is True
    assert client.call_log[0]["status"] == "ok"
    assert client.call_log[0]["usage"]["est_usd"] is None  # unknown model price
    assert client.call_log[0]["usage"]["input_tokens"] == 1000


def test_vultr_discovers_models_and_routes_by_decision_role(monkeypatch) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": model}
                        for model in (
                            "glm-5.3-flash",
                            "qwen3.8-flash-next",
                            "minimax-m3",
                            "glm-5.3",
                        )
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "tool_calls": [
                                {"function": {"name": "emit", "arguments": '{"choice":"first"}'}}
                            ]
                        },
                    }
                ],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
            },
        )

    monkeypatch.setenv("VULTR_INFERENCE_API_KEY", "test-only")
    monkeypatch.delenv("VULTR_INFERENCE_MODEL", raising=False)
    monkeypatch.delenv("VULTR_INFERENCE_CRITIC_MODEL", raising=False)
    monkeypatch.delenv("VULTR_INFERENCE_EXTRACTION_MODEL", raising=False)
    client = VultrDecisionClient.from_env(
        client=httpx.Client(transport=httpx.MockTransport(handle))
    )
    assert client.model == "glm-5.3-flash"
    assert client.extraction_model == "qwen3.8-flash-next"
    assert client.critic_model == "minimax-m3"
    assert client.fallback_model == "glm-5.3"
    client.complete_json("phase1.prd", "generate", SCHEMA)
    client.complete_json("phase5.map_columns", "extract", SCHEMA)
    client.complete_json("critic.prd", "review", SCHEMA)
    bodies = [json.loads(request.content) for request in calls[1:]]
    assert [body["model"] for body in bodies] == [
        "glm-5.3-flash",
        "qwen3.8-flash-next",
        "minimax-m3",
    ]
    assert bodies[0]["reasoning_effort"] == "minimal"
    assert "reasoning_effort" not in bodies[1]
    assert "reasoning_effort" not in bodies[2]
    assert client.call_log[0]["usage"]["est_usd"] == pytest.approx(0.00017)
    assert client.call_log[1]["usage"]["est_usd"] == pytest.approx(0.00014)
    assert client.call_log[2]["usage"]["est_usd"] == pytest.approx(0.00038)


def test_truncated_extraction_is_logged_and_escalates_to_stronger_model() -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [{"finish_reason": "length", "message": {"tool_calls": []}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 1024},
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "tool_calls": [
                                {"function": {"name": "emit", "arguments": '{"choice":"first"}'}}
                            ]
                        },
                    }
                ],
                "usage": {"prompt_tokens": 200, "completion_tokens": 100},
            },
        )

    client = VultrDecisionClient(
        api_key="test-only",
        model="glm-5.3-flash",
        extraction_model="qwen3.8-flash-next",
        fallback_model="glm-5.3",
        client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    assert client.complete_json("phase5.map_columns", "map", SCHEMA) == {"choice": "first"}
    assert [body["model"] for body in calls] == ["qwen3.8-flash-next", "glm-5.3"]
    assert calls[1]["max_completion_tokens"] >= 1024
    assert [record["status"] for record in client.call_log] == ["length", "ok"]
    assert client.call_log[0]["usage"]["output_tokens"] == 1024
    assert generated_by(client)["model"] == "glm-5.3"
    assert client.decisions_by_backend == {"vultr": 1}


def test_strict_tool_rejection_retries_verified_json_schema_method() -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(422, json={"error": "unsupported schema"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '\n\n{"choice":"second"}'}}
                ],
                "usage": {"prompt_tokens": 250, "completion_tokens": 10},
            },
        )

    client = VultrDecisionClient(
        api_key="test-only",
        model="glm-5.3-flash",
        fallback_model="glm-5.3",
        client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    assert client.complete_json("phase1.prd", "draft", SCHEMA) == {"choice": "second"}
    assert "tools" in calls[0]
    assert calls[1]["model"] == "glm-5.3-flash"
    assert calls[1]["response_format"]["type"] == "json_schema"
    assert [record["status"] for record in client.call_log] == ["http_422", "ok"]
    assert client.call_log[0]["usage"]["est_usd"] is None


def test_both_invalid_attempts_fail_closed_and_keep_cost_records() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"tool_calls": []}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            },
        )

    client = VultrDecisionClient(
        api_key="test-only",
        model="glm-5.3-flash",
        client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    with pytest.raises(TypeError, match="after two attempts"):
        client.complete_json("phase1.prd", "draft", SCHEMA)
    assert [record["status"] for record in client.call_log] == [
        "invalid_response",
        "invalid_response",
    ]
    assert all(record["usage"]["est_usd"] > 0 for record in client.call_log)


def test_missing_vultr_credentials_fail_closed(monkeypatch) -> None:
    monkeypatch.delenv("VULTR_INFERENCE_API_KEY", raising=False)
    monkeypatch.delenv("VULTR_INFERENCE_MODEL", raising=False)
    with pytest.raises(RuntimeError):
        VultrDecisionClient.from_env()
