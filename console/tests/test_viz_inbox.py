"""Q1 · the cross-case inbox: HTML renders, the JSON view-model has its documented shape, order is by need."""

import jsonschema

STRIP = {
    "type": "object",
    "required": ["state", "case_id", "kind", "title", "detail", "when", "since", "href"],
    "properties": {
        "state": {"enum": ["need", "block", "quar", "run", "pause", "done"]},
        "href": {"type": "string", "pattern": "^/"},
    },
}
INBOX = {
    "type": "object",
    "required": ["strips", "cases", "needs_you", "empty"],
    "properties": {
        "strips": {"type": "array", "items": STRIP},
        "cases": {"type": "array", "items": {"type": "object", "required": ["id", "title", "question", "needs_you"]}},
        "needs_you": {"type": "integer", "minimum": 0},
    },
}


def test_inbox_json_shape_and_order(client):
    m = client.get("/api/viz/inbox").json()
    jsonschema.validate(m, INBOX)
    states = [s["state"] for s in m["strips"]]
    order = ["need", "block", "quar", "run", "pause", "done"]
    assert states == sorted(states, key=order.index)
    kinds = {s["kind"] for s in m["strips"]}
    assert {"checkpoint", "run", "quarantine", "limit_kill"} <= kinds  # the fixture run has all of them
    assert m["needs_you"] == sum(1 for s in m["strips"] if s["state"] == "need") > 0


def test_inbox_page_renders_every_strip(client):
    page = client.get("/inbox").text
    m = client.get("/api/viz/inbox").json()
    assert page.count('class="strip st-') >= len(m["strips"])
    for c in m["cases"]:
        assert c["title"] in page
    assert "built-in method" not in page and "Needs you" in page


def test_inbox_empty_state(make_client, monkeypatch):
    monkeypatch.setenv("ONTOFILL_CONSOLE_CASES", "")
    from fastapi.testclient import TestClient

    from ontofill_console.web import create_app, settings_from_env

    c = TestClient(create_app(settings_from_env({"ONTOFILL_CONSOLE_CASES": "", "ONTOFILL_CONSOLE_IDENTITY": "sso"})))
    assert "All quiet" in c.get("/inbox").text and c.get("/api/viz/inbox").json()["strips"] == []
