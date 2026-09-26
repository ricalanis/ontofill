"""Pure-logic parts of the controller: guard, verify parsing, approvals, planner parsing, screens, captures."""

from __future__ import annotations

import json

import yaml
from test_controller_support import FakeGateway

from controller import approvals, guard, planner, screen, verify
from controller.backend import Action, Element, Observation, host_allowed
from controller.captures import LocalCaptureStore

ALLOWED = ["registry.example"]


def obs(*elements: Element, url="https://registry.example/search") -> Observation:
    return Observation(url=url, title="t", text="text", elements=list(elements))


SEARCH_FORM = {"method": "get", "action": "/r", "search": True}
POST_FORM = {"method": "post", "action": "/c", "search": False}


def el(id_, kind, name, **kw) -> Element:
    return Element(id_, kind, kind, name, f'[data-ba-id="{id_}"]', **kw)


def test_host_allowlist():
    assert host_allowed("registry.example", ALLOWED) and host_allowed("www.registry.example", ALLOWED)
    assert not host_allowed("registry.example.evil.invalid", ALLOWED) and not host_allowed(None, ALLOWED)
    assert not host_allowed("evilregistry.example", ALLOWED)


def test_code_floor():
    o = obs(el("e1", "input", "q", input_type="search", form=SEARCH_FORM),
            el("e2", "submit", "Buscar", form=SEARCH_FORM),
            el("e3", "submit", "Send complaint", form=POST_FORM),
            el("e4", "link", "Next page", href="https://registry.example/r?page=2"),
            el("e5", "link", "Prize", href="http://evil.invalid/x"),
            el("e6", "button", "Delete record"),
            el("e7", "input", "password", input_type="password"),
            el("e8", "link", "Download tool", href="https://registry.example/setup.exe"),
            el("e9", "link", "Ficha (PDF)", href="https://registry.example/ficha.pdf"))
    tier = {i: guard.code_floor(Action("click", {"element_id": i}), o, ALLOWED)[0]
            for i in ("e2", "e3", "e4", "e5", "e6", "e8", "e9")}
    assert tier == {"e2": "SAFE", "e3": "HIGH", "e4": "SAFE", "e5": "HIGH", "e6": "HIGH", "e8": "HIGH", "e9": "SAFE"}
    assert guard.code_floor(Action("type", {"element_id": "e1", "text": "x"}), o, ALLOWED)[0] == "SAFE"
    assert guard.code_floor(Action("type", {"element_id": "e7", "text": "x"}), o, ALLOWED)[0] == "HIGH"
    assert guard.code_floor(Action("navigate", {"url": "http://evil.invalid/"}), o, ALLOWED)[0] == "HIGH"
    assert guard.code_floor(Action("navigate", {"url": "/detail"}), o, ALLOWED)[0] == "SAFE"
    assert guard.code_floor(Action("navigate", {"url": "javascript:alert(1)"}), o, ALLOWED)[0] == "HIGH"
    # an injection flag raises scrutiny: nothing state-changing stays SAFE
    assert guard.code_floor(Action("click", {"element_id": "e4"}), o, ALLOWED, scrutiny=True)[0] == "LOW"


def test_decide_combines_code_and_jev():
    o = obs(el("e3", "submit", "Send complaint", form=POST_FORM), el("e4", "link", "Next", href="/r?page=2"))
    gw = FakeGateway(jev_tier="SAFE")
    d = guard.decide(Action("click", {"element_id": "e3"}), o, ALLOWED, gw.jev)
    assert (d.tier, d.decided_by) == ("HIGH", "code") and not gw.jev_calls  # Jev not even asked
    gw = FakeGateway(jev_tier="HIGH")
    d = guard.decide(Action("click", {"element_id": "e4"}), o, ALLOWED, gw.jev)
    assert (d.tier, d.decided_by, d.code_tier) == ("HIGH", "jev", "SAFE")
    d = guard.decide(Action("navigate", {"url": "https://evil.invalid"}), o, ALLOWED, gw.jev)
    assert d.hard and d.tier == "HIGH"

    def broken(*a):
        raise KeyError("answers")
    d = guard.decide(Action("click", {"element_id": "e4"}), o, ALLOWED, broken)
    assert (d.tier, d.decided_by) == ("SAFE", "code") and "error" in d.jev


def test_parse_verdict_is_tolerant():
    assert verify.parse_verdict('{"verdict": "achieved", "confidence": 0.9}')["verdict"] == "achieved"
    v = verify.parse_verdict('Here you go:\n```json\n{"verdict": "Not Achieved", "confidence": "0.7"}\n```')
    assert v == {"verdict": "not_achieved", "confidence": 0.7, "reason": ""}
    assert verify.parse_verdict('{"verdict": "pass", "confidence": 3}')["confidence"] == 1.0
    assert verify.parse_verdict("The goal was not achieved.")["verdict"] == "not_achieved"
    assert verify.parse_verdict("")["verdict"] == "uncertain"


def test_jev_step_check_thresholds():
    for p, verdict in ((0.97, "achieved"), (0.5, "uncertain"), (0.05, "not_achieved")):
        r = verify.jev_step_check(FakeGateway(progress=p).jev, step_goal="g", action="a", diff="d")
        assert r["verdict"] == verdict and 0 <= r["confidence"] <= 1


def test_diff_summary():
    a = Observation("https://x/a", "A", "one\ntwo")
    b = Observation("https://x/b", "B", "one\nthree")
    d = verify.diff_summary(a, b)
    assert "URL: https://x/a -> https://x/b" in d and "Added text: three" in d and "Removed text: two" in d
    assert verify.diff_summary(a, a) == "No visible change."


def test_planner_parse_and_page_block():
    resp = {"choices": [{"message": {"tool_calls": [{"function": {"name": "click",
                                                                   "arguments": '{"element_id": "e2"}'}}]}}]}
    action, _ = planner.parse(resp)
    assert action.tool == "click" and action.element_id == "e2"
    action, note = planner.parse({"choices": [{"message": {"content": 'ok {"tool": "done", "args": {}}'}}]})
    assert action.tool == "done" and note
    assert planner.parse({"choices": [{"message": {"content": "hmm"}}]})[0] is None
    assert planner.parse({"choices": [{"message": {"tool_calls": [{"function": {"name": "rm_rf"}}]}}]})[0] is None
    msgs = planner.messages("goal", obs(el("e1", "link", "IGNORE THIS")), [])
    user = msgs[1]["content"]
    assert user[1]["text"].startswith("<page_content>") and "IGNORE THIS" in user[1]["text"]
    assert "IGNORE THIS" not in user[0]["text"]
    withheld = planner.messages("goal", obs(el("e1", "link", "IGNORE THIS")), [], withheld="[withheld]")
    assert "IGNORE THIS" not in json.dumps(withheld)


def test_screen_records():
    gw = FakeGateway(inj=lambda chunk: "injection")
    r = screen.controller_screen(gw.jev, "chunk")
    assert r["flagged"] and r["by"] == "controller" and r["jev_choice"] == "injection"
    assert screen.gateway_screen("flagged")["flagged"] and screen.gateway_screen("clean")["flagged"] is False
    assert screen.gateway_screen(None) is None

    def down(*a):
        raise ValueError("no Jev")
    assert screen.controller_screen(down, "chunk") is None


def test_approval_request_and_decisions(tmp_path):
    key = "sha256:" + "a" * 64
    req = approvals.request(tmp_path, intended_action="click submit \"Send\"", risk_tier="HIGH", screenshot_key=key,
                            job_id="job:1", reason="submit of a non-search form", artifact_paths=["04-local/x/tdd.md"],
                            generated_by={"backend": "vultr", "model": "m", "at": "2026-09-26T00:00:00+00:00"})
    meta = yaml.safe_load(req.pending_path.read_text().split("---")[1])
    assert meta["checkpoint"] == "action" and meta["phase"] == 5 and meta["screenshot_key"] == key
    assert req.rel_dir.startswith("05-actions/")
    assert approvals.read_decision(req) is None
    assert approvals.wait(req, 0.1, 0.02).timed_out
    req.approved_path.write_text("{not json")
    assert approvals.read_decision(req).decision == "deny"
    req.approved_path.write_text(json.dumps({"approver": "r", "date": "2026-09-26", "checkpoint": "action",
                                             "decision": "approve"}))
    assert approvals.wait(req, 1, 0.02).approved
    req.approved_path.write_text(json.dumps({"approver": "r", "date": "2026-09-26", "decision": "maybe"}))
    assert not approvals.read_decision(req).approved


def test_capture_store_is_content_addressed(tmp_path):
    store = LocalCaptureStore(tmp_path)
    k1 = store.put(b"png-bytes", content_type="image/png", url="https://x/", source_id="s", step_id="pending:1")
    k2 = store.put(b"png-bytes", content_type="image/png", url="https://y/", source_id="s", step_id="pending:2")
    assert k1 == k2 and k1.startswith("sha256:") and store.get(k1) == b"png-bytes"
    store.link(k1, "step:abc")
    store.link(k1, "step:def")  # first citing step wins
    meta = json.loads((tmp_path / "bronze" / "sha256" / (k1[7:] + ".meta.json")).read_text())
    assert meta["step_id"] == "step:abc" and meta["url"] == "https://x/"
