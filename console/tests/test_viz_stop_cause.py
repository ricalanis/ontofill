"""A run that fails because a phase loop gave up (the iteration cap, every candidate rejected) says so on /watch, the
failures view and the summary, in its own trace's words, and not only "engine exited 1". Proof jobs carry the phase of
the step that dispatched them and their own failure reason."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from test_viz_live import RUN, run_dir, set_status

from ontofill_console.viz import health_common as hc

OBJECTION = "https://branch.example/hours: opening_hours: the publisher is not authoritative for this jurisdiction"
T0 = datetime(2031, 1, 1, tzinfo=UTC)


def loop_step(i: int, stop: str | None) -> dict:
    return {
        "step_id": f"step:{RUN}:p3loop{i}",
        "run_id": RUN,
        "phase": 3,
        "mode": "D1",
        "observed": "candidates captured",
        "requested": "critique the candidates",
        "executed": "decision.complete_json",
        "evaluated": {"verdict": "rejected"},
        "loop": {
            "phase": 3,
            "role": "critique",
            "iteration": i,
            "objections": [OBJECTION],
            **({"stop_reason": stop} if stop else {}),
        },
        "value_ids": [],
        "ts": (T0 + timedelta(seconds=i)).isoformat(),
    }


@pytest.fixture
def p3_gave_up(cases_dir):
    with (run_dir(cases_dir) / "trace.live.jsonl").open("a") as f:
        for i in (1, 2, 3):
            f.write(json.dumps(loop_step(i, "max_iterations" if i == 3 else None)) + "\n")
    job = {
        "job_id": "job:p3probe",
        "run_id": RUN,
        "step_id": f"step:{RUN}:p3loop2",
        "source_id": "branch-example",
        "checkpoints": {
            "task": {
                "ok": False,
                "requested": {"url": "https://branch.example/"},
                "result": {"status": "http_403"},
                "value_ids": [],
            }
        },
        "failure_reason": "capture refused: http_403",
    }
    with (run_dir(cases_dir) / "jobs.jsonl").open("a") as f:
        f.write(json.dumps(job) + "\n")
    set_status(cases_dir, state="failed", phase=3, reason=None, checkpoint_pending=None)
    return cases_dir


def test_stop_cause_reads_the_last_loop():
    steps = [
        {
            **loop_step(i, "max_iterations" if i == 3 else None),
            "kind": "loop",
            "detail": {
                "phase": 3,
                "iteration": i,
                "role": "critique",
                "objections": [OBJECTION],
                "stop_reason": "max_iterations" if i == 3 else None,
                "usd": None,
            },
        }
        for i in (1, 2, 3)
    ]
    assert hc.stop_cause(steps, "running", "c", "r") is None  # a healthy run has no stop cause
    c = hc.stop_cause(steps, "failed", "c", "r")
    assert c["phase"] == 3 and c["stop_label"] == "iteration cap" and c["iterations"] == 3
    assert c["href"] == "/cases/c/discovery?run=r" and OBJECTION[:40] in c["objection"]


def test_failures_view_leads_with_why_the_run_stopped(client, p3_gave_up):
    m = client.get(f"/cases/libraries/api/viz/failures?run={RUN}").json()
    first = m["strips"][0]
    assert first["kind"] == "run_stop" and first["phase"] == 3 and "iteration cap" in first["title"]
    job = next(s for s in m["strips"] if s["kind"] == "checkpoint_fail" and "branch.example" in s["title"])
    assert job["phase"] == 3  # the dispatching step's phase, not an assumed P5
    assert "capture refused: http_403" in job["detail"]
    page = client.get(f"/cases/libraries/failures?run={RUN}").text
    assert "Run stopped" in page and "failed: capture refused: http_403" in page


def test_summary_and_watch_say_why(client, p3_gave_up):
    s = client.get(f"/cases/libraries/api/viz/summary?run={RUN}").json()
    assert any("iteration cap" in x for x in s["run"]["stop"])
    assert any(x["state"] == "block" and "phase 3" in x["title"] for x in s["next"])  # after pending approvals
    w = client.get("/api/watch").json()
    c = next(x for x in w["cases"] if x["case_id"] == "libraries")
    assert c["stop_cause"]["phase"] == 3
    assert "Why it stopped" in client.get("/watch").text


def test_pages_name_each_source_by_its_hosts(client):
    m = client.get("/cases/libraries/api/viz/pages").json()
    with_hosts = [g for g in m["groups"] if g["hosts"]]
    assert with_hosts, "fixture pages carry URLs"
    for g in with_hosts:
        chip = next(f for f in m["source_filters"] if f["source_id"] == g["source_id"])
        assert chip["label"].startswith(g["hosts"][0])  # the chip reads as a site, the id stays in its title
    page = client.get("/cases/libraries/pages").text
    assert f'title="{with_hosts[0]["source_id"]}"' in page and with_hosts[0]["hosts"][0] in page


def test_blocked_captures_name_the_host_and_the_redirect_chain(client, cases_dir):
    """Live, federal pages were blocked for redirecting out of their registrable domain and the view showed only
    opaque source ids with a truncated dump. Strips now name the host and show the reason, the chain and the
    allowlist; a job names what egress blocked (not the isolation probe's *.invalid host)."""
    step = {
        "step_id": f"step:{RUN}:blocked1",
        "run_id": RUN,
        "phase": 3,
        "mode": "S1",
        "source_id": "source-abc123",
        "observed": "dispatch",
        "executed": "dispatch",
        "requested": {"url": "https://www.dept.example/files/list", "allowed_domains": ["dept.example"]},
        "evaluated": {
            "proof_checkpoint": "dispatch_result",
            "status": "blocked",
            "reason": "redirect_outside_registrable_domain",
            "redirect_chain": ["https://www.dept.example/files/list", "https://www.portal.example/dept/list"],
        },
        "value_ids": [],
        "ts": (T0 + timedelta(seconds=30)).isoformat(),
    }
    with (run_dir(cases_dir) / "trace.live.jsonl").open("a") as f:
        f.write(json.dumps(step) + "\n")
    job = {
        "job_id": "job:egress1",
        "run_id": RUN,
        "step_id": step["step_id"],
        "source_id": "source-abc123",
        "checkpoints": {
            "task": {
                "ok": False,
                "requested": {"url": "https://procure.example/search"},
                "result": {
                    "egress_events": [
                        {"decision": "block", "host": "probe-1.invalid", "method": "GET"},
                        {"decision": "block", "host": "procure.example", "method": "CONNECT"},
                    ]
                },
                "value_ids": [],
            }
        },
    }
    with (run_dir(cases_dir) / "jobs.jsonl").open("a") as f:
        f.write(json.dumps(job) + "\n")
    m = client.get(f"/cases/libraries/api/viz/failures?run={RUN}").json()
    blocked = next(s for s in m["strips"] if s["kind"] == "blocked_domain" and "abc123" in s["title"])
    assert blocked["title"] == "Domain blocked · www.dept.example (source-abc123)"
    assert "redirect outside registrable domain" in blocked["detail"]
    assert "redirects: www.dept.example → www.portal.example" in blocked["detail"]
    assert "allowed: dept.example" in blocked["detail"]
    jstrip = next(s for s in m["strips"] if s["kind"] == "checkpoint_fail" and "procure.example" in s["title"])
    assert "egress blocked: procure.example" in jstrip["detail"] and "invalid" not in jstrip["detail"]
    row = next(c for c in m["cells"] if c["job_id"] == "job:egress1")
    assert row["host"] == "procure.example"


def test_pages_show_a_document_parse_outcome(client, cases_dir):
    """A captured document shows what file.parse made of it, and says when the row sample cap was reached."""
    base = {"run_id": RUN, "phase": 3, "mode": "S1", "value_ids": [], "observed": "document"}
    rows = [
        {
            **base,
            "step_id": f"step:{RUN}:parse1",
            "source_id": "source-link-doc1",
            "requested": {
                "tool": "file.parse",
                "url": "https://files.example/list.xls",
                "bronze_key": "sha256:" + "a" * 64,
                "max_rows": 300,
                "format": "auto",
            },
            "executed": {"bronze_key": "sha256:" + "a" * 64, "format": "xls", "row_count": 15, "sheet": "Hoja1"},
            "evaluated": "ok",
            "ts": (T0 + timedelta(seconds=40)).isoformat(),
        },
        {
            **base,
            "step_id": f"step:{RUN}:parse2",
            "source_id": "source-link-doc2",
            "requested": {"tool": "file.parse", "url": "https://files.example/big.xls", "max_rows": 300},
            "executed": {"bronze_key": "sha256:" + "b" * 64, "format": "xls", "row_count": 300},
            "evaluated": "ok",
            "ts": (T0 + timedelta(seconds=41)).isoformat(),
        },
    ]
    with (run_dir(cases_dir) / "trace.live.jsonl").open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    m = client.get(f"/cases/libraries/api/viz/pages?run={RUN}&all=1").json()
    cards = {c["path"]: c for g in m["groups"] for c in g["cards"]}
    assert cards["/list.xls"]["parsed"]["text"] == "xls · sheet Hoja1 · 15 rows parsed of a 300-row sample"
    assert cards["/big.xls"]["parsed"]["capped"] is True
    assert "300+ rows (sample cap 300 reached; there may be more)" in cards["/big.xls"]["parsed"]["text"]
    page = client.get(f"/cases/libraries/pages?run={RUN}&all=1").text
    assert "15 rows parsed of a 300-row sample" in page and "pg-parsed--capped" in page


def test_wall_clock_stop_reads_as_a_time_limit():
    steps = [
        {
            **loop_step(i, "wall_clock" if i == 4 else None),
            "kind": "loop",
            "detail": {
                "phase": 3,
                "iteration": i,
                "role": "critique",
                "objections": [OBJECTION],
                "stop_reason": "wall_clock" if i == 4 else None,
                "usd": None,
            },
        }
        for i in (1, 2, 3, 4)
    ]
    c = hc.stop_cause(steps, "failed", "c", "r")
    assert c["stop_label"] == "time limit"
    assert "its loop stopped at the time limit after 4 iterations" in c["text"]


def test_a_phase_that_gives_up_pauses_and_still_explains_why(client, p3_gave_up):
    """Live (run-a3f49f634dce): P3 found no authoritative source and the engine exited leaving status "paused" with no
    checkpoint pending and a reason; only the runner said needs_human. /failures showed "Run stopped: 0" and /summary
    had no loop cause. A reasoned pause with nothing to approve now reads as a stop."""
    why = "sources unreachable (7 blocked/redirected/403); no authoritative source found for library_name"
    set_status(p3_gave_up, state="paused", phase=3, reason=why, checkpoint_pending=None)
    first = client.get(f"/cases/libraries/api/viz/failures?run={RUN}").json()["strips"][0]
    assert first["kind"] == "run_stop" and first["title"].startswith("Run needs a person: phase 3 loop stopped")
    stop = client.get(f"/cases/libraries/api/viz/summary?run={RUN}").json()["run"]["stop"]
    assert any("iteration cap" in x for x in stop) and why in stop
    c = next(x for x in client.get("/api/watch").json()["cases"] if x["case_id"] == "libraries")
    assert c["stop_cause"]["phase"] == 3


def test_a_checkpoint_pause_is_not_a_stop(client, p3_gave_up):
    set_status(p3_gave_up, state="paused", phase=3, reason="waiting for approval: source", checkpoint_pending="source")
    strips = client.get(f"/cases/libraries/api/viz/failures?run={RUN}").json()["strips"]
    assert not any(s["kind"] == "run_stop" for s in strips)
    assert hc.stopped_state({"state": "paused", "checkpoint_pending": "prd", "reason": "x"}) == "paused"
    assert hc.stopped_state({"state": "paused"}) == "paused"


def capture_step(n: int, egress: list[dict]) -> dict:
    return {
        "step_id": f"step:{RUN}:capture{n}",
        "run_id": RUN,
        "phase": 3,
        "mode": "S1",
        "source_id": f"source-cap{n}",
        "requested": {"url": "https://portal.example/", "allowed_domains": ["portal.example"]},
        "executed": {"network_request": True, "html_key": "sha256:" + "a" * 64},
        "evaluated": {"bronze_objects": 3, "egress_events": egress},
        "value_ids": [],
        "ts": (T0 + timedelta(seconds=40 + n)).isoformat(),
    }


PROBE = {
    "decision": "block",
    "host": "ontofill-proof-denied-1a2b.invalid",
    "method": "GET",
    "reason": "domain_not_allowed",
}
OWN = {"decision": "allow", "host": "portal.example", "method": "CONNECT", "reason": "domain_allowed"}


def test_the_isolation_probe_is_not_a_blocked_domain(client, cases_dir):
    """Live (run-a3f49f634dce): 48 "Domain blocked · <the source's own host> · domain not allowed" strips, each naming
    a host that was on its own allowlist. They were successful captures (3 bronze objects each): the only block was
    the sandbox's isolation probe to *.invalid, which every capture makes on purpose. A real off-list request (a CDN)
    now names that host and says the page itself was captured."""
    cdn = {"decision": "block", "host": "cdn.thirdparty.example", "method": "CONNECT", "reason": "domain_not_allowed"}
    with (run_dir(cases_dir) / "trace.live.jsonl").open("a") as f:
        f.write(json.dumps(capture_step(1, [PROBE, OWN])) + "\n")
        f.write(json.dumps(capture_step(2, [PROBE, OWN, cdn])) + "\n")
    strips = client.get(f"/cases/libraries/api/viz/failures?run={RUN}").json()["strips"]
    mine = [s for s in strips if "source-cap" in (s.get("title") or "")]
    assert len(mine) == 1  # the probe-only capture is not a failure at all
    s = mine[0]
    assert s["kind"] == "blocked_domain"
    assert s["title"].startswith("Domain blocked · cdn.thirdparty.example (from ")
    assert "the page itself was captured (3 bronze objects)" in s["detail"] and ".invalid" not in s["detail"]
    assert s["detail"].startswith("domain not allowed") and "domain allowed" not in s["detail"]
