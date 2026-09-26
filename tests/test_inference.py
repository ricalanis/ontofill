import httpx
import pytest

from ontofill.inference import RecordedDecisionClient, VultrDecisionClient

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
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "emit",
                                        "arguments": '{"choice":"second"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handle)
    client = VultrDecisionClient(
        api_key="test-only", model="test-model", client=httpx.Client(transport=transport)
    )
    assert client.complete_json("choose", "pick", SCHEMA) == {"choice": "second"}
    assert seen[0].url.path == "/v1/chat/completions"
    assert seen[0].headers["Authorization"] == "Bearer test-only"
    body = __import__("json").loads(seen[0].content)
    assert body["tool_choice"] == {"type": "function", "function": {"name": "emit"}}
    assert body["tools"][0]["function"]["parameters"] == SCHEMA


def test_vultr_discovers_two_model_families_and_routes_critic(monkeypatch) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "glm-5.3"}, {"id": "qwen3.8-27b"}]})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {"function": {"name": "emit", "arguments": '{"choice":"first"}'}}
                            ]
                        }
                    }
                ]
            },
        )

    monkeypatch.setenv("VULTR_INFERENCE_API_KEY", "test-only")
    monkeypatch.delenv("VULTR_INFERENCE_MODEL", raising=False)
    client = VultrDecisionClient.from_env(
        client=httpx.Client(transport=httpx.MockTransport(handle))
    )
    assert client.model == "glm-5.3"
    assert client.critic_model == "qwen3.8-27b"
    client.complete_json("phase1.prd", "generate", SCHEMA)
    client.complete_json("critic.prd", "review", SCHEMA)
    assert [__import__("json").loads(request.content)["model"] for request in calls[1:]] == [
        "glm-5.3",
        "qwen3.8-27b",
    ]


def test_missing_vultr_credentials_fail_closed(monkeypatch) -> None:
    monkeypatch.delenv("VULTR_INFERENCE_API_KEY", raising=False)
    monkeypatch.delenv("VULTR_INFERENCE_MODEL", raising=False)
    with pytest.raises(RuntimeError):
        VultrDecisionClient.from_env()
