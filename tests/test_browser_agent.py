"""The engine speaks MCP to the controller and fails closed on unscreened pages."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from ontofill.browser_agent import BrowserAgentClient, BrowserTraceBridge, ObservationQuarantined
from ontofill.lake import FileLake
from ontofill.runfeed import RunFeed


def test_mcp_session_lifecycle_and_screening() -> None:
    called: list[str] = []
    observations = [
        {"observation": {"text_excerpt": "untrusted page bytes"}},
        {
            "observation": {"text_excerpt": "unsafe page bytes"},
            "screen": {"flagged": True, "safety_verdict": "unsafe"},
        },
        {
            "observation": {"text_excerpt": "uncertain page bytes"},
            "screen": {
                "flagged": False,
                "jev_choice": "benign",
                "jev_confidence": 0.4,
                "safety_verdict": "unavailable",
            },
        },
        {
            "observation": {"text_excerpt": "confident benign page bytes"},
            "screen": {
                "flagged": False,
                "jev_choice": "benign",
                "jev_confidence": 0.95,
                "safety_verdict": "unavailable",
            },
        },
        {
            "observation": {"text_excerpt": "safe page bytes"},
            "screen": {"flagged": False, "safety_verdict": "safe"},
        },
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        if method == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": body["id"], "result": {}},
                headers={"Mcp-Session-Id": "mcp-session"},
            )
        assert request.headers["Mcp-Session-Id"] == "mcp-session"
        if method == "notifications/initialized":
            return httpx.Response(202)
        assert method == "tools/call"
        name = body["params"]["name"]
        called.append(name)
        result = {
            "session.open": {"session_id": "browser-session", "live_view_url": None},
            "session.act": {"status": "achieved"},
            "session.close": {"closed": True},
        }.get(name)
        if name == "session.observe":
            result = observations.pop(0)
        response = {
            "jsonrpc": "2.0",
            "id": body["id"],
            "result": {"structuredContent": result},
        }
        if name == "session.open":
            return httpx.Response(
                200,
                text="event: message\ndata: " + json.dumps(response) + "\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=response)

    client = BrowserAgentClient(
        "http://127.0.0.1:8701/mcp", client=httpx.Client(transport=httpx.MockTransport(handle))
    )
    opened = client.session_open({}, ["example.test"])
    assert opened["session_id"] == "browser-session"
    assert (
        client.session_act("browser-session", action={"tool": "click", "args": {}})["status"]
        == "achieved"
    )
    with pytest.raises(ObservationQuarantined) as missing:
        client.session_observe("browser-session")
    assert "untrusted page bytes" not in str(missing.value)
    with pytest.raises(ObservationQuarantined):
        client.session_observe("browser-session")
    with pytest.raises(ObservationQuarantined):
        client.session_observe("browser-session")
    assert (
        client.session_observe("browser-session")["observation"]["text_excerpt"]
        == "confident benign page bytes"
    )
    assert (
        client.session_observe("browser-session")["observation"]["text_excerpt"]
        == "safe page bytes"
    )
    assert client.session_close("browser-session")["closed"]
    assert called == [
        "session.open",
        "session.act",
        *("session.observe" for _ in range(5)),
        "session.close",
    ]


def test_mcp_act_requires_one_goal_or_action() -> None:
    client = BrowserAgentClient("http://127.0.0.1:8701/mcp")
    with pytest.raises(ValueError, match="exactly one"):
        client.session_act("session")
    with pytest.raises(ValueError, match="exactly one"):
        client.session_act("session", goal="open", action={"tool": "click"})


def test_controller_trace_bridge_mirrors_bronze_and_appends_complete_rows(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    captures = tmp_path / "captures"
    steps_root = tmp_path / "steps"
    steps_root.mkdir()
    session_id = "bas-synthetic"
    steps_path = steps_root / f"{session_id}.jsonl"
    blob = b"synthetic screened screenshot"
    hexdigest = hashlib.sha256(blob).hexdigest()
    key = f"sha256:{hexdigest}"
    capture = captures / "bronze" / "sha256" / hexdigest
    capture.parent.mkdir(parents=True)
    capture.write_bytes(blob)
    capture.with_name(hexdigest + ".meta.json").write_text(
        json.dumps(
            {
                "content_type": "image/png",
                "url": "https://example.test",
                "captured_at": "2026-09-26T00:00:00Z",
                "source_id": "source-1",
                "step_id": "step-1",
            }
        )
    )
    live = {"backend": "vultr", "model": "synthetic", "at": "2026-09-26T00:00:00Z"}
    jev = {"backend": "jev", "model": "synthetic", "at": "2026-09-26T00:00:00Z"}
    step = {
        "step_id": "step-1",
        "session_id": session_id,
        "run_id": "live-run",
        "phase": 5,
        "source_id": "source-1",
        "objective_id": "objective-1",
        "tdd_path": "04-local/source-1__objective-1/tdd.json",
        "mode": "S1",
        "event": "quarantine",
        "observed": {"page_withheld": True},
        "requested": {},
        "executed": {},
        "evaluated": {"reason": "possible injection"},
        "parent_step_id": None,
        "screenshot_key": key,
        "value_ids": [],
        "ts": "2026-09-26T00:00:00Z",
        "generated_by": jev,
        "screen": {
            "flagged": True,
            "jev_choice": "injection",
            "jev_confidence": 0.9,
            "safety_verdict": "unavailable",
            "reason": "possible injection",
            "by": "controller",
        },
    }
    first = json.dumps(step) + "\n"
    second = json.dumps({**step, "step_id": "step-2", "generated_by": live}) + "\n"
    feed = RunFeed(lake, "case", "live-run", live, start_heartbeat=False)
    feed.update_status(state="running", phase=5)
    bridge = BrowserTraceBridge(
        session_id=session_id,
        steps_path=steps_path,
        steps_root=steps_root,
        captures_root=captures,
        feed=feed,
        lake=lake,
    )
    assert bridge.drain() == 0
    steps_path.write_text(first + second[:20])
    assert bridge.drain() == 1
    assert lake.read_key(key) == blob
    assert lake.read_metadata(key)["content_type"] == "image/png"
    steps_path.write_text(first + second)
    assert bridge.drain() == 1
    assert bridge.drain() == 0
    lines = lake.read_key("runs/case/live-run/trace.live.jsonl").splitlines()
    assert len(lines) == 2
    feed.close()
