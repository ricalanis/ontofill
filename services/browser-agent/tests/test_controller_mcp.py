"""The MCP façade: tool functions callable directly, token lifecycle through the gateway admin, tool registry."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_controller_support import FakeAdmin, FakeBackend, FakeGateway, check_step, read_steps

from controller import server


@pytest.fixture
def broker(tmp_path):
    admin = FakeAdmin()
    gateways: dict[str, FakeGateway] = {}

    def gateway_factory(token):
        gw = FakeGateway([("click", {"element_id": "e2", "expectation": "detail"}),
                          ("extract", {"fields": {"legal_name": "#legal-name"}}),
                          ("done", {"status": "achieved"})])
        gateways[token] = gw
        return gw

    b = server.Broker(admin=admin, gateway_factory=gateway_factory, backend_factory=lambda cdp: FakeBackend(),
                      steps_dir=tmp_path / "steps", captures_dir=tmp_path / "lake", case_dir=tmp_path / "case")
    b.gateways = gateways
    server.set_broker(b)
    yield b
    b.close_all()
    server.set_broker(None)


def test_tools_callable_directly(broker, tmp_path):
    opened = server.session_open({"run_id": "run-x", "source_id": "registry-example", "job_id": "job:x",
                                  "tdd_path": "04-local/registry__identity/tdd.md"},
                                 ["registry.example"], {"budget_usd": 0.2, "ttl_s": 600, "max_steps": 10})
    sid = opened["session_id"]
    assert opened["live_view_url"] is None
    assert broker.admin.opened[sid] == {"ttl_s": 600, "budget_usd": 0.2, "run_id": "run-x"}
    # the session holds only its token, never a key
    assert f"tok-{sid}" in broker.gateways
    result = server.session_act(sid, goal="Open Entidad Ejemplo 01 and record its name")
    assert result["status"] == "achieved" and result["extracted"]["legal_name"]["value"] == "Entidad Ejemplo 01"
    seen = server.session_observe(sid)
    assert seen["observation"]["url"].startswith("https://registry.example")
    assert set(seen["metrics"]) >= {"jev_observations_screened", "jev_flagged", "vision_calls_avoided",
                                    "vision_calls_made", "estimated_usd", "backend_breakdown"}
    one = server.session_act(sid, action={"tool": "click", "args": {"element_id": "e2"}})
    assert one["status"] in {"achieved", "not_achieved", "uncertain"}
    closed = server.session_close(sid)
    assert closed["closed"] and closed["token_revoked"] and broker.admin.revoked == [sid]
    assert closed["metrics"]["usd_source"] == "gateway" and closed["metrics"]["estimated_usd"] == 0.0123
    steps = read_steps(tmp_path / "steps" / f"{sid}.jsonl")
    assert steps and all(s["run_id"] == "run-x" and s["source_id"] == "registry-example" for s in steps)
    for s in steps:
        check_step(s)
    assert server.session_close(sid)["closed"] is False


def test_act_needs_exactly_one_of_goal_or_action(broker):
    sid = server.session_open({}, ["registry.example"], {})["session_id"]
    with pytest.raises(ValueError):
        server.session_act(sid)
    with pytest.raises(ValueError):
        server.session_act(sid, goal="x", action={"tool": "scroll"})
    with pytest.raises(ValueError):
        server.session_open({}, [], {})


def test_mcp_registry_and_call(broker):
    srv = server.build_server()
    names = [t.name for t in asyncio.run(srv.list_tools())]
    assert names == ["session.open", "session.act", "session.observe", "session.close"]

    async def flow():
        opened = await srv.call_tool("session.open", {"tdd": {"run_id": "r"}, "allowed_domains": ["registry.example"]})
        sid = json.loads(opened.content[0].text)["session_id"]
        observed = await srv.call_tool("session.observe", {"session_id": sid})
        closed = await srv.call_tool("session.close", {"session_id": sid})
        return json.loads(observed.content[0].text), json.loads(closed.content[0].text)

    observed, closed = asyncio.run(flow())
    assert observed["observation"]["title"] == "Registry" and closed["closed"]
