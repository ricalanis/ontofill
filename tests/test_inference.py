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
            200, json={"choices": [{"message": {"content": '{"choice":"second"}'}}]}
        )

    transport = httpx.MockTransport(handle)
    client = VultrDecisionClient(
        api_key="test-only", model="test-model", client=httpx.Client(transport=transport)
    )
    assert client.complete_json("choose", "pick", SCHEMA) == {"choice": "second"}
    assert seen[0].url.path == "/v1/chat/completions"
    assert seen[0].headers["Authorization"] == "Bearer test-only"


def test_missing_vultr_credentials_fail_closed(monkeypatch) -> None:
    monkeypatch.delenv("VULTR_INFERENCE_API_KEY", raising=False)
    monkeypatch.delenv("VULTR_INFERENCE_MODEL", raising=False)
    with pytest.raises(RuntimeError):
        VultrDecisionClient.from_env()
