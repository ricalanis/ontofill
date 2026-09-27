"""The step loop end to end: real Chromium (native backend) on a loopback fixture site, scripted gateway."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pytest
import yaml
from test_controller_support import HOSTILE, FakeBackend, FakeGateway, Site, by_name, check_step, read_steps

from controller.captures import LocalCaptureStore
from controller.loop import Limits, Session
from shared.steps import StepLog


@pytest.fixture(scope="module")
def site():
    with Site() as s:
        yield s


def make_session(tmp_path: Path, gateway, *, backend=None, start_url=None, limits=None, prescreen=True):
    allowed = ["registry.example"] if isinstance(backend, FakeBackend) else ["127.0.0.1"]
    if backend is None:
        from backends.native.backend import NativeBackend
        backend = NativeBackend()
    steps = StepLog(tmp_path / "steps.jsonl", run_id="run-test", session_id="bas-test", source_id="registry-example")
    session = Session(session_id="bas-test", backend=backend, gateway=gateway, steps=steps,
                      captures=LocalCaptureStore(tmp_path / "lake"), allowed_domains=allowed,
                      case_dir=tmp_path / "case", job_id="job:test-0001", limits=limits or Limits(),
                      source_id="registry-example", artifact_paths=["04-local/registry__identity/tdd.md"],
                      start_url=start_url, prescreen=prescreen)
    session.open()
    return session, tmp_path / "steps.jsonl"


@pytest.mark.browser
def test_open_goal_is_reached(site, tmp_path):
    plan = [("navigate", {"url": site.url("search.html"), "expectation": "the search page"}),
            by_name("type", "Nombre de la entidad", text="Entidad Ejemplo", expectation="query typed"),
            by_name("click", "Buscar", expectation="a results list"),
            by_name("click", "Entidad Ejemplo 01", expectation="the entity's detail page"),
            ("extract", {"fields": {"legal_name": "#legal-name", "tax_id": "#tax-id", "address": "#address"}}),
            ("done", {"status": "achieved", "summary": "identity fields captured"})]
    # Jev: confident on navigation and the result click, unsure on typing and the search submit -> vision.
    gw = FakeGateway(plan, progress=[0.97, 0.5, 0.2, 0.95],
                     vision=['{"verdict": "achieved", "confidence": 0.8}',
                             'Sure! ```json\n{"verdict":"achieved","confidence":0.88,"reason":"results"}\n```'])
    session, path = make_session(tmp_path, gw)
    try:
        result = session.run_goal("Find Entidad Ejemplo 01 and record its legal name, tax id and address")
    finally:
        closed = session.close()
    assert result["status"] == "achieved"
    assert result["extracted"]["legal_name"]["value"] == "Entidad Ejemplo 01"
    assert result["extracted"]["tax_id"]["value"] == "XAXX010101000"
    assert KEY(result["extracted"]["tax_id"]["screenshot_key"])
    steps = read_steps(path)
    for s in steps:
        check_step(s)
    verifies = [s for s in steps if s.get("event") == "verify"]
    assert {v["verify"]["backend"] for v in verifies} == {"jev", "vultr"}
    gates = [s for s in steps if s.get("event") == "action_gate"]
    assert len(gates) == 4 and all(g["gate"]["outcome"] == "allowed" for g in gates)
    # verify steps chain to the action step they check
    by_id = {s["step_id"]: s for s in steps}
    # CONTRACT v1.0.5 (proposed): each extracted value cites the extract step that captured it
    for item in result["extracted"].values():
        cited = by_id[item["step_id"]]
        assert cited["requested"]["tool"] == "extract" and item["captured_at"] == cited["ts"] and item["selector"]
    for v in verifies:
        assert by_id[v["parent_step_id"]]["requested"]["tool"] in {"navigate", "type", "click"}
    m = closed["metrics"]
    assert m["vision_calls_avoided"] == 2 and m["vision_calls_made"] == 2
    assert m["backend_breakdown"]["jev"] > 0 and m["backend_breakdown"]["vultr"] > 0
    assert m["estimated_usd"] > 0 and m["jev_observations_screened"] >= 4
    # the vision verifier got the screenshot, never the page text
    assert all(any(p.get("type") == "image_url" for p in msgs[1]["content"]) for msgs in gw.vision_calls)
    # captures land in the bronze layout with a sidecar
    shot = verifies[0]["verify"]["screenshot_key"].removeprefix("sha256:")
    assert (tmp_path / "lake" / "bronze" / "sha256" / shot).exists()
    meta = json.loads((tmp_path / "lake" / "bronze" / "sha256" / f"{shot}.meta.json").read_text())
    assert meta["content_type"] == "image/png" and meta["step_id"].startswith("step:")


def KEY(value: str) -> bool:
    return bool(re.match(r"^sha256:[0-9a-f]{64}$", value or ""))


def _run_submit(site, tmp_path, answer: dict | None, timeout_s: float = 20):
    plan = [by_name("click", "Send complaint", expectation="more results"),
            ("done", {"status": "not_achievable", "summary": "the only way on is a form I may not submit"})]
    gw = FakeGateway(plan, jev_tier="SAFE")  # Jev says SAFE; the code floor still forces HIGH
    session, path = make_session(tmp_path, gw, start_url=site.url("submit.html"),
                                 limits=Limits(approval_timeout_s=timeout_s, approval_poll_s=0.05))
    out: dict = {}
    worker = threading.Thread(target=lambda: out.update(session.run_goal("See more results")))
    worker.start()
    pending = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and pending is None:
        found = list((tmp_path / "case" / "05-actions").glob("*/APPROVAL_PENDING.md"))
        pending = found[0] if found else None
        time.sleep(0.05)
    assert pending is not None, "no approval request was written"
    assert worker.is_alive(), "the loop must block while the approval is pending"
    text = pending.read_text()
    meta = yaml.safe_load(text.split("---")[1])
    if answer is not None:
        (pending.parent / "APPROVED").write_text(json.dumps(answer))
    worker.join(timeout=30)
    session.close()
    return out, read_steps(path), meta, pending


@pytest.mark.browser
def test_disguised_submit_is_denied(site, tmp_path):
    out, steps, meta, _ = _run_submit(site, tmp_path, {
        "approver": "reviewer", "date": "2026-09-26", "checkpoint": "action", "decision": "deny",
        "reason": "this posts a complaint"})
    assert meta["checkpoint"] == "action" and meta["risk_tier"] == "HIGH" and meta["phase"] == 5
    assert meta["job_id"] == "job:test-0001" and KEY(meta["screenshot_key"])
    assert "Send complaint" in meta["intended_action"]
    assert set(meta) >= {"requested_at", "reason", "artifact_paths", "generated_by"}
    gates = [s["gate"] for s in steps if s.get("event") == "action_gate"]
    assert [g["outcome"] for g in gates] == ["pending_approval", "denied"]
    assert gates[0]["decided_by"] == "code" and gates[0]["approval_path"].endswith("APPROVAL_PENDING.md")
    assert site.posts == []  # the form was never submitted
    assert out["status"] == "not_achievable"
    for s in steps:
        check_step(s)


@pytest.mark.browser
def test_disguised_submit_runs_after_approval(site, tmp_path):
    before = len(site.posts)
    _, steps, _, _ = _run_submit(site, tmp_path, {
        "approver": "reviewer", "date": "2026-09-26", "checkpoint": "action", "decision": "approve"})
    gates = [s["gate"]["outcome"] for s in steps if s.get("event") == "action_gate"]
    assert gates == ["pending_approval", "allowed"]
    assert len(site.posts) == before + 1 and site.posts[-1][0] == "/complaint"
    assert any(s["requested"].get("tool") == "click" and s["evaluated"].get("ok") for s in steps
               if s.get("event") is None)


@pytest.mark.browser
def test_unanswered_approval_times_out_as_deny(site, tmp_path):
    before = len(site.posts)
    _, steps, _, pending = _run_submit(site, tmp_path, None, timeout_s=0.6)
    last_gate = [s for s in steps if s.get("event") == "action_gate"][-1]
    assert last_gate["gate"]["outcome"] == "denied" and last_gate["evaluated"]["approval"]["timed_out"]
    assert len(site.posts) == before and not (pending.parent / "APPROVED").exists()


def _hostile_plan(site):
    return [by_name("click", "Claim your prize", expectation="the prize page"),
            ("navigate", {"url": "http://evil.invalid/exfil", "expectation": "the form"}),
            ("navigate", {"url": site.url("search.html"), "expectation": "the registry search"}),
            ("done", {"status": "achieved", "summary": "back on the registry"})]


@pytest.mark.browser
def test_hostile_page_flagged_by_gateway_is_quarantined(site, tmp_path):
    gw = FakeGateway(_hostile_plan(site), flag=lambda text: HOSTILE in text)
    session, path = make_session(tmp_path, gw, start_url=site.url("hostile.html"))
    try:
        result = session.run_goal("Open the registry search page")
    finally:
        closed = session.close()
    steps = read_steps(path)
    for s in steps:
        check_step(s)
    assert result["status"] == "achieved"  # the loop kept going
    quarantine = [s for s in steps if s.get("event") == "quarantine"]
    assert len(quarantine) == 1
    q = quarantine[0]
    assert q["screen"]["flagged"] and q["screen"]["by"] == "gateway" and q["screen"]["safety_verdict"] == "unavailable"
    assert q["evaluated"]["status"] == "quarantined_continue"
    assert q["executed"]["discarded_proposal"]["tool"] == "click"  # the flagged prompt's action never ran
    assert q["requested"]["tool"] == "plan"  # the planner step that saw the flag is the quarantine step
    assert (tmp_path / "lake" / "bronze" / "sha256" / q["screenshot_key"].removeprefix("sha256:")).exists()
    # The first prompt carried the page, and the hostile text only inside <page_content>.
    first = gw.planner_prompts[0]
    outside = re.sub(r"<page_content>.*?</page_content>", "", first, flags=re.DOTALL)
    assert HOSTILE in first and HOSTILE not in outside
    # Every later prompt had it withheld.
    assert all(HOSTILE not in p for p in gw.planner_prompts[1:])
    assert "page content withheld" in gw.planner_prompts[1]
    # The off-domain navigation was denied outright (no approval asked), and the page's own request to
    # evil.invalid was blocked by the allowlist and recorded.
    gates = [s["gate"] for s in steps if s.get("event") == "action_gate"]
    assert any(g["outcome"] == "denied" and "evil.invalid" in g["action"] for g in gates)
    assert not (tmp_path / "case" / "05-actions").exists()
    assert "evil.invalid" in closed["metrics"]["blocked_hosts"]
    assert closed["metrics"]["jev_flagged"] == 1


@pytest.mark.browser
def test_hostile_page_flagged_by_controller_never_reaches_the_planner(site, tmp_path):
    gw = FakeGateway(_hostile_plan(site)[1:], inj=lambda chunk: "injection" if HOSTILE in chunk else "benign")
    session, path = make_session(tmp_path, gw, start_url=site.url("hostile.html"))
    try:
        result = session.run_goal("Open the registry search page")
    finally:
        session.close()
    steps = read_steps(path)
    q = [s for s in steps if s.get("event") == "quarantine"]
    assert len(q) == 1 and q[0]["screen"]["by"] == "controller" and q[0]["screen"]["jev_choice"] == "injection"
    assert all(HOSTILE not in p for p in gw.planner_prompts)
    assert result["status"] == "achieved"


def test_max_steps_emits_limit_kill(tmp_path):
    gw = FakeGateway([("scroll", {"direction": "down"})] * 10)
    session, path = make_session(tmp_path, gw, backend=FakeBackend(), limits=Limits(max_steps=3))
    result = session.run_goal("Scroll forever")
    session.close()
    steps = read_steps(path)
    kills = [s for s in steps if s.get("event") == "limit_kill"]
    assert result["status"] == "killed" and len(kills) == 1 and kills[0]["evaluated"]["reason"] == "max_steps"
    assert len(gw.planner_prompts) == 3
    for s in steps:
        check_step(s)


def test_exhausted_budget_is_a_hard_stop(tmp_path):
    gw = FakeGateway(chat_error=402)
    session, path = make_session(tmp_path, gw, backend=FakeBackend())
    result = session.run_goal("Anything")
    session.close()
    steps = read_steps(path)
    assert result["status"] == "stopped" and "budget" in result["summary"]
    assert steps[-1]["event"] == "hard_stop"


def test_jev_can_raise_but_not_lower_the_tier(tmp_path):
    # Jev rates following a link HIGH: the approval gate opens (and times out to a deny).
    gw = FakeGateway([("click", {"element_id": "e2"}), ("done", {"status": "not_achievable"})], jev_tier="HIGH")
    session, path = make_session(tmp_path, gw, backend=FakeBackend(),
                                 limits=Limits(approval_timeout_s=0.2, approval_poll_s=0.05))
    session.run_goal("Open the entity")
    session.close()
    gates = [s["gate"] for s in read_steps(path) if s.get("event") == "action_gate"]
    assert gates[0]["decided_by"] == "jev" and gates[0]["risk_tier"] == "HIGH"
    assert gates[-1]["outcome"] == "denied"


def test_not_achieved_retries_then_gives_up(tmp_path):
    gw = FakeGateway([("click", {"element_id": "e2", "expectation": "detail page"})] * 5, progress=0.1,
                     vision=['{"verdict": "not_achieved", "confidence": 0.9}'] * 5)
    session, _ = make_session(tmp_path, gw, backend=FakeBackend(), limits=Limits(max_attempts=2))
    result = session.run_goal("Open the entity")
    session.close()
    assert result["status"] == "not_achieved"
    assert "check: not_achieved" in gw.planner_prompts[1]  # the planner was told about the failed check



def test_dead_browser_target_stops_the_session_cleanly(tmp_path):
    """GAPS R16: a closed target ends the session with status stopped + one hard_stop step, not an exception."""
    gw = FakeGateway([("done", {"status": "achieved", "summary": "unused"})])
    session, path = make_session(tmp_path, gw)

    class TargetClosedError(Exception):
        pass

    def dead(*_a, **_k):
        raise TargetClosedError("Page.evaluate: Target page, context or browser has been closed")

    try:
        session.backend.observe = dead
        first = session.run_action({"tool": "scroll", "args": {}})
        second = session.run_action({"tool": "scroll", "args": {}})
    finally:
        session.close()
    assert first["status"] == second["status"] == "stopped" and "browser closed" in first["summary"]
    stops = [s for s in read_steps(path) if s.get("event") == "hard_stop"]
    assert len(stops) == 1 and stops[0]["evaluated"]["reason"] == "browser closed"
