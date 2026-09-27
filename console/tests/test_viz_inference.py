"""Inference view (R19): the gateway call log joined with trace steps by step_id, the share of reasoning on Vultr,
unattributed calls, provenance flags and who acted. Gateway records are written in the exact shape of the gateway's
CallLog (services/browser-agent/gateway/calllog.py): ts, session_id, run_id, step_id, upstream, purpose, model,
status, input_tokens, output_tokens, est_usd, est_tokens?, gate?, latency_ms, prompt_chars."""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import jsonschema
import pytest
from conftest import spec_for
from fastapi.testclient import TestClient

from ontofill_console.viz import inference
from ontofill_console.web import create_app, settings_from_env

PKG = Path(__file__).resolve().parents[1] / "ontofill_console"
LIB_RUN = "run-libraries-0001"


# recorded gateway log fixtures ---------------------------------------------------------------------------------
def rec(
    ts,
    step_id,
    *,
    session="engine",
    run_id=None,
    upstream="vultr",
    purpose="chat",
    model="glm-5.3",
    inp=900,
    out=120,
    usd=0.0010350,
    latency=812.4,
    status=200,
    gate=None,
    chars=3600,
    est_tokens=None,
):
    """One call-log line, keys and value types exactly as the gateway writes them (None keys are dropped)."""
    r = {
        "ts": ts,
        "session_id": session,
        "run_id": run_id,
        "step_id": step_id,
        "upstream": upstream,
        "purpose": purpose,
        "model": model,
        "status": status,
        "input_tokens": inp,
        "output_tokens": out,
        "est_usd": usd,
        "est_tokens": est_tokens,
        "gate": gate,
        "latency_ms": latency,
        "prompt_chars": chars,
    }
    return {k: v for k, v in r.items() if v is not None}


def _plus(ts: str, secs: float = 1) -> str:
    return (datetime.fromisoformat(ts) + timedelta(seconds=secs)).isoformat(timespec="milliseconds")


def screen_gate(flagged=False):
    return {
        "checked": 1,
        "flagged": int(flagged),
        "chunks": [
            {
                "chars": 1800,
                "flagged": flagged,
                "cached": False,
                "jev": {
                    "choice": "injection" if flagged else "clean",
                    "confidence": 0.97,
                    "p_injection": 0.97 if flagged else 0.02,
                    "request_id": "jev-req-0001",
                },
                "safety": {"verdict": "unsafe" if flagged else "safe", "model": "nemotron-3.5-content-safety"},
            }
        ],
    }


def libraries_log(lake: Path) -> list[dict]:
    """The libraries fixture run's live model steps, each with the calls the gateway would have logged: loop proposals
    and critiques (engine principal, with step ids), the vision verify, and the quarantined observe (Jev screen + the
    Vultr safety model + the chat call the screen guarded)."""
    feed = lake / "runs" / "fixture-libraries" / LIB_RUN / "trace.live.jsonl"
    out = []
    for line in feed.read_text().splitlines():
        s = json.loads(line)
        lp = s.get("loop") or {}
        ts = _plus(s["ts"])
        if lp.get("model"):
            out.append(rec(ts, s["step_id"], model=lp["model"], inp=1400, out=300, usd=0.004, latency=1320.0))
        elif s.get("event") == "verify":
            out.append(
                rec(
                    ts,
                    s["step_id"],
                    session="bas-fixture-0001",
                    run_id=LIB_RUN,
                    model=s["verify"]["model"],
                    inp=1100,
                    out=60,
                    usd=0.000185,
                    latency=940.5,
                )
            )
        elif s.get("event") == "quarantine":
            g = screen_gate(True)
            out += [
                rec(
                    ts,
                    s["step_id"],
                    session="bas-fixture-0001",
                    run_id=LIB_RUN,
                    upstream="jev",
                    purpose="screen",
                    model="jev-1.13.0",
                    inp=450,
                    out=0,
                    usd=0.0000045,
                    latency=88.0,
                ),
                rec(
                    ts,
                    s["step_id"],
                    session="bas-fixture-0001",
                    run_id=LIB_RUN,
                    purpose="screen",
                    model="nemotron-3.5-content-safety",
                    inp=450,
                    out=4,
                    usd=0.0000470,
                    latency=210.0,
                ),
                rec(
                    _plus(s["ts"], 2),
                    s["step_id"],
                    session="bas-fixture-0001",
                    run_id=LIB_RUN,
                    model="glm-5.3",
                    inp=2100,
                    out=180,
                    usd=0.002115,
                    latency=1604.2,
                    gate=g,
                ),
            ]
    return out


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


VULTR = {"backend": "vultr", "model": "glm-5.3", "at": "2026-09-27T10:00:00+00:00"}


def _run_case(root: Path, cid: str, run_id: str, t0: datetime, dirty: bool) -> tuple[Path, Path, list[dict]]:
    """A small recorded run in the lake layout plus its case package and the gateway records its model calls made.
    Clean: every model step is a Vultr call with a gateway record, every artifact says vultr. Dirty adds a Jev-only
    decision, an engine call with no step id inside the run's window, a record naming the run but an unknown step, a
    model step with no record, a mock artifact, a recorded step and a harness actor."""
    case, lake = root / cid / "case", root / cid / "lake"
    trace, log = [], []

    def step(phase, mode, requested, **kw):
        s = {
            "step_id": f"step:{run_id}:{len(trace) + 1:04d}",
            "run_id": run_id,
            "phase": phase,
            "source_id": None,
            "objective_id": None,
            "tdd_path": None,
            "mode": mode,
            "observed": requested,
            "requested": requested,
            "executed": "done",
            "evaluated": "ok",
            "parent_step_id": None,
            "value_ids": [],
            "ts": (t0 + timedelta(seconds=10 * len(trace))).isoformat(),
            **kw,
        }
        trace.append(s)
        return s

    def usage(model, inp, out, usd):
        return {"model": model, "backend": "vultr", "input_tokens": inp, "output_tokens": out, "est_usd": usd}

    s1 = step(
        1,
        "S1",
        "propose (iteration 1)",
        event="loop",
        generated_by=VULTR,
        usage=usage("glm-5.3", 1500, 400, 0.002325),
        loop={"phase": 1, "iteration": 1, "role": "propose", "model": "glm-5.3", "verdict": "completed"},
    )
    log.append(rec(_plus(s1["ts"]), s1["step_id"], inp=1500, out=400, usd=0.002325))
    s2 = step(
        1,
        "S1",
        "critique (iteration 1)",
        event="loop",
        generated_by=VULTR,
        usage=usage("minimax-m3", 1600, 200, 0.0005),
        loop={"phase": 1, "iteration": 1, "role": "critique", "model": "minimax-m3", "verdict": "accepted"},
    )
    log.append(rec(_plus(s2["ts"]), s2["step_id"], model="minimax-m3", inp=1600, out=200, usd=0.0005))
    step(
        1,
        "D0",
        "decide (iteration 1)",
        event="loop",
        loop={"phase": 1, "iteration": 1, "role": "decide", "verdict": "stop", "stop_reason": "checks_passed"},
    )
    s4 = step(
        5,
        "S1",
        {"tool": "browser.act", "action": "open the listing page"},
        session_id="bas-0001",
        generated_by=VULTR,
        usage=usage("qwen3.8-flash-next", 2000, 150, 0.00023),
    )
    log.append(
        rec(
            _plus(s4["ts"]),
            s4["step_id"],
            session="bas-0001",
            run_id=run_id,
            model="qwen3.8-flash-next",
            inp=2000,
            out=150,
            usd=0.00023,
        )
    )
    s5 = step(
        5,
        "S1",
        {"tool": "vision.verify"},
        event="verify",
        session_id="bas-0001",
        generated_by=VULTR,
        verify={
            "goal": "listing visible",
            "verdict": "achieved",
            "confidence": 0.9,
            "backend": "vultr",
            "model": "qwen3.8-27b",
        },
    )
    log.append(
        rec(
            _plus(s5["ts"]),
            s5["step_id"],
            session="bas-0001",
            run_id=run_id,
            model="qwen3.8-27b",
            inp=1200,
            out=40,
            usd=0.00022,
        )
    )
    step(5, "D1", {"tool": "extract.fields"}, value_ids=["val:x-1"])
    s7 = step(
        5,
        "S1",
        "observe the detail page",
        event="quarantine",
        session_id="bas-0001",
        generated_by=VULTR,
        screen={
            "flagged": True,
            "jev_choice": "injection",
            "jev_confidence": 0.95,
            "safety_verdict": "unsafe",
            "reason": "instructions aimed at an agent",
            "by": "gateway",
        },
    )
    log += [
        rec(
            _plus(s7["ts"]),
            s7["step_id"],
            session="bas-0001",
            run_id=run_id,
            upstream="jev",
            purpose="screen",
            model="jev-1.13.0",
            inp=500,
            out=0,
            usd=0.000005,
            latency=70.0,
        ),
        rec(
            _plus(s7["ts"]),
            s7["step_id"],
            session="bas-0001",
            run_id=run_id,
            purpose="screen",
            model="nemotron-3.5-content-safety",
            inp=500,
            out=3,
            usd=0.00005,
            latency=190.0,
        ),
        rec(
            _plus(s7["ts"], 2),
            s7["step_id"],
            session="bas-0001",
            run_id=run_id,
            model="glm-5.3",
            inp=1800,
            out=90,
            usd=0.00162,
            gate=screen_gate(True),
        ),
    ]
    artifacts = {
        "brief.md": "# A small question (synthetic)\n\nWhich example things exist?\n",
        "01-scope/prd.json": json.dumps({"version": "v1", "generated_by": VULTR}),
        "02-ontology/ontology.json": json.dumps({"version": "v1", "classes": [], "generated_by": VULTR}),
        "04-local/src__obj/tdd.md": "---\ngenerated_by:\n  backend: vultr\n  model: glm-5.3\n---\n# TDD\n",
    }
    if dirty:
        step(
            5,
            "S1",
            {"tool": "browser.act", "action": "open page 2"},
            session_id="bas-0001",
            generated_by=VULTR,
            usage=usage("glm-5.3", 900, 60, 0.00085),
        )  # no gateway record: the call went around the gateway
        s9 = step(
            5,
            "S1",
            {"tool": "browser.act", "action": "submit search"},
            event="action_gate",
            session_id="bas-0001",
            generated_by={"backend": "jev", "model": "jev-1.13.0"},
            gate={"action": "submit search", "risk_tier": "LOW", "decided_by": "jev", "outcome": "allowed"},
        )
        log.append(
            rec(
                _plus(s9["ts"]),
                s9["step_id"],
                session="bas-0001",
                run_id=run_id,
                upstream="jev",
                purpose="decision",
                model="jev-1.13.0",
                inp=300,
                out=5,
                usd=0.000003,
                latency=65.0,
            )
        )
        step(3, "S1", "map the host", generated_by={"backend": "mock", "model": "recorded-double"})
        step(5, "S1", "patched the extractor by hand", actor="operator-shell")
        log.append(rec(_plus(trace[1]["ts"], 3), None, model="glm-5.3"))  # engine call, no step id, in window
        log.append(rec(_plus(trace[3]["ts"], 3), "step:ghost-0001", session="bas-0001", run_id=run_id))
        artifacts["02-ontology/factors/factors.json"] = json.dumps(
            {"factors": [], "generated_by": {"backend": "recorded", "model": "synthetic-fixture"}}
        )
    feed = lake / "runs" / cid / run_id
    write_jsonl(feed / "trace.live.jsonl", trace)
    (feed / "status.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "case_id": cid,
                "state": "done",
                "phase": 5,
                "updated_at": trace[-1]["ts"],
                "metrics": {"inference_backend": "vultr"},
                "generated_by": VULTR,
            }
        )
    )
    (lake / "runs" / cid / "latest.json").write_text(json.dumps({"run_id": run_id}))
    for rel, text in artifacts.items():
        (case / rel).parent.mkdir(parents=True, exist_ok=True)
        (case / rel).write_text(text)
    write_jsonl(
        case / "decisions.jsonl",
        [
            {
                "ts": (t0 - timedelta(minutes=5)).isoformat(),
                "case_id": cid,
                "checkpoint": "prd",
                "phase_dir": "01-scope",
                "decision": "approve",
                "approver": "reviewer@example.org",
                "identity_source": "sso",
                "artifact_sha256": {},
                "run_id": run_id,
            }
        ],
    )
    return case, lake, log


@pytest.fixture
def world(cases_dir, tmp_path):
    """Libraries + parks fixtures, a clean run and a dirty run, and one gateway log holding all their calls."""
    clean_case, clean_lake, clean_log = _run_case(
        tmp_path / "extra", "clean", "run-clean-0001", datetime.fromisoformat("2026-09-27T10:00:00+00:00"), dirty=False
    )
    dirty_case, dirty_lake, dirty_log = _run_case(
        tmp_path / "extra", "dirty", "run-dirty-0001", datetime.fromisoformat("2026-09-27T12:00:00+00:00"), dirty=True
    )
    lib_log = libraries_log(cases_dir / "libraries" / "lake")
    log = write_jsonl(tmp_path / "gateway-log" / "gateway-calls.jsonl", lib_log + clean_log + dirty_log)
    spec = f"{spec_for(cases_dir)},clean={clean_case}:{clean_lake},dirty={dirty_case}:{dirty_lake}"
    return {"spec": spec, "log": log, "lib_log": lib_log, "clean_log": clean_log, "dirty_log": dirty_log}


@pytest.fixture
def wclient(world, monkeypatch):
    monkeypatch.setenv(inference.LOG_ENV, str(world["log"].parent))  # a directory of *.jsonl, as mounted
    return TestClient(create_app(settings_from_env({"ONTOFILL_CONSOLE_CASES": world["spec"]})))


# schema -------------------------------------------------------------------------------------------------------
NUM = {"type": ["number", "null"]}
CALL = {
    "type": "object",
    "required": [
        "ts",
        "model",
        "upstream",
        "provider",
        "phase",
        "step_id",
        "step_href",
        "purpose",
        "input_tokens",
        "output_tokens",
        "usd",
        "latency_ms",
        "status",
        "ok",
        "attributed",
        "attribution",
    ],
    "properties": {
        "model": {"type": "string"},
        "provider": {"type": "string"},
        "purpose": {"enum": ["plan", "critique", "extract", "verify", "screen", "decide", "chat"]},
        "input_tokens": {"type": "integer"},
        "output_tokens": {"type": "integer"},
        "usd": NUM,
        "latency_ms": NUM,
        "attributed": {"type": "boolean"},
    },
}
FLAG = {
    "type": "object",
    "required": ["kind", "what", "backend", "detail", "href", "state", "n"],
    "properties": {
        "kind": {"enum": ["artifact", "run", "values", "step", "no-record"]},
        "href": {"type": ["string", "null"]},
        "n": {"type": "integer"},
    },
}
SCHEMA = {
    "type": "object",
    "required": [
        "case_id",
        "run_id",
        "runs",
        "log",
        "calls",
        "n_calls",
        "n_reasoning",
        "n_reasoning_vultr",
        "n_screen",
        "pct_vultr",
        "usd_total",
        "input_tokens",
        "output_tokens",
        "latency_ms_median",
        "n_steps",
        "n_model_steps",
        "deciders",
        "decider_counts",
        "by_purpose",
        "by_model",
        "unattributed",
        "flags",
        "n_flags",
        "flag_counts",
        "artifacts",
        "actors",
        "purpose_map",
        "empty",
        "contract_request",
        "backend",
    ],
    "properties": {
        "run_id": {"type": ["string", "null"]},
        "runs": {"type": "array", "items": {"type": "string"}},
        "log": {
            "type": "object",
            "required": ["env", "path", "mounted", "files", "n_records", "bad_lines"],
            "properties": {"mounted": {"type": "boolean"}, "n_records": {"type": "integer"}},
        },
        "calls": {"type": "array", "items": CALL},
        "n_calls": {"type": "integer"},
        "pct_vultr": NUM,
        "usd_total": NUM,
        "input_tokens": {"type": "integer"},
        "output_tokens": {"type": "integer"},
        "deciders": {"type": "array", "items": {"type": "object", "required": ["name", "steps", "share", "c"]}},
        "decider_counts": {"type": "object", "additionalProperties": {"type": "integer"}},
        "unattributed": {
            "type": "object",
            "required": ["n", "n_records", "n_steps", "n_window", "reasons", "records", "steps"],
            "properties": {"n": {"type": ["integer", "null"]}, "records": {"type": "array", "items": CALL}},
        },
        "flags": {"type": "array", "items": FLAG},
        "n_flags": {"type": "integer"},
        "artifacts": {
            "type": "array",
            "items": {"type": "object", "required": ["path", "backend", "model", "at", "href"]},
        },
        "actors": {
            "type": "object",
            "required": [
                "runner",
                "engine_label",
                "n_engine_steps",
                "harness",
                "n_harness",
                "harness_line",
                "humans_in_run",
                "decisions",
            ],
            "properties": {
                "n_harness": {"type": "integer"},
                "harness_line": {"type": "string"},
                "decisions": {"type": "array", "items": {"type": "object", "required": ["who", "when", "checkpoint"]}},
            },
        },
        "empty": {"type": ["object", "null"]},
    },
}
GLOBAL_SCHEMA = {
    "type": "object",
    "required": [
        "log",
        "rows",
        "n_cases",
        "n_calls",
        "n_joined",
        "n_reasoning",
        "n_reasoning_vultr",
        "pct_vultr",
        "usd_total",
        "orphans",
        "n_orphans",
        "n_unattributed",
        "n_flags",
        "n_harness",
        "purpose_map",
        "empty",
        "contract_request",
    ],
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "case_id",
                    "run_id",
                    "n_calls",
                    "pct_vultr",
                    "usd",
                    "unattributed",
                    "n_flags",
                    "n_harness",
                    "href",
                ],
            },
        },
        "orphans": {"type": "array", "items": CALL},
    },
}


def _clean(html: str) -> None:
    assert "built-in method" not in html and "Undefined" not in html


# without the log ----------------------------------------------------------------------------------------------
def test_pages_render_without_the_log(client, monkeypatch):
    monkeypatch.delenv(inference.LOG_ENV, raising=False)
    for cid in ("libraries", "parks"):
        r = client.get(f"/cases/{cid}/inference")
        assert r.status_code == 200, cid
        _clean(r.text)
        m = client.get(f"/cases/{cid}/api/viz/inference").json()
        jsonschema.validate(m, SCHEMA)
    html = client.get("/cases/libraries/inference").text
    assert "Gateway log not mounted" in html and "R19" in html and inference.LOG_ENV in html
    m = client.get("/cases/libraries/api/viz/inference").json()
    assert m["unattributed"]["n"] is None and m["n_calls"] == 0 and m["pct_vultr"] is None
    assert m["n_model_steps"] == 6 and m["decider_counts"]["vultr"] == 5
    parks = client.get("/cases/parks/api/viz/inference").json()
    assert parks["run_id"] is None and parks["empty"] and parks["artifacts"]
    g = client.get("/inference")
    assert g.status_code == 200 and "Gateway log not mounted" in g.text
    _clean(g.text)
    jsonschema.validate(client.get("/api/viz/inference").json(), GLOBAL_SCHEMA)


def test_unknown_case_and_run(client):
    assert client.get("/cases/nope/inference").status_code == 404
    assert client.get("/cases/nope/api/viz/inference").status_code == 404
    assert client.get("/cases/libraries/inference?run=no-such-run").status_code == 404
    assert client.get("/cases/libraries/api/viz/inference?run=no-such-run").status_code == 404


# the libraries fixture run with its recorded gateway log -------------------------------------------------------
def test_libraries_every_call_listed_all_on_vultr_and_mock_flagged(wclient, world):
    m = wclient.get("/cases/libraries/api/viz/inference").json()
    jsonschema.validate(m, SCHEMA)
    assert m["run_id"] == LIB_RUN and m["log"]["mounted"] and m["log"]["files"] == ["gateway-calls.jsonl"]
    assert m["n_calls"] == len(world["lib_log"]) == 8
    assert all(c["model"] != "unknown" and c["usd"] is not None and c["attributed"] for c in m["calls"])
    assert {c["step_id"] for c in m["calls"]} == {r["step_id"] for r in world["lib_log"]}
    assert m["pct_vultr"] == 100 and m["n_reasoning"] == 6 and m["n_screen"] == 2
    assert m["unattributed"]["n"] == 0
    assert m["usd_total"] == pytest.approx(sum(r["est_usd"] for r in world["lib_log"]))
    purposes = {c["purpose"] for c in m["calls"]}
    assert {"plan", "critique", "verify", "screen"} <= purposes
    # recorded inference in the fixture is flagged: a case artifact, the run's status/metrics, gold values, steps
    kinds = {(f["kind"], f["backend"]) for f in m["flags"]}
    assert ("artifact", "recorded") in kinds and ("values", "recorded") in kinds and ("step", "recorded") in kinds
    art = next(f for f in m["flags"] if f["kind"] == "artifact")
    assert (
        art["what"] == "02-ontology/dod-queries.json"
        and art["href"] == "/cases/libraries/files/02-ontology/dod-queries.json"
    )
    assert wclient.get(art["href"]).status_code == 200
    step_flag = next(f for f in m["flags"] if f["kind"] == "step")
    assert step_flag["href"].startswith(f"/cases/libraries/runs/{LIB_RUN}#step:")
    html = wclient.get("/cases/libraries/inference").text
    _clean(html)
    assert "100%" in html and "0 unattributed" in html and f"{m['n_flags']} provenance flags" in html
    assert "fixture-planner" in html and "Vultr Serverless Inference" in html and "Jev" in html
    assert f'href="/cases/libraries/runs/{LIB_RUN}#' in html
    assert "0 harness actions inside this run" in html


def test_clean_run_is_all_vultr_with_nothing_flagged(wclient):
    m = wclient.get("/cases/clean/api/viz/inference").json()
    jsonschema.validate(m, SCHEMA)
    assert m["n_calls"] == 7 and m["n_reasoning"] == 5 and m["pct_vultr"] == 100
    assert m["unattributed"]["n"] == 0 and m["n_flags"] == 0
    assert m["decider_counts"] == {"code": 2, "vultr": 5}
    assert all(c["attribution"] == "step" for c in m["calls"])
    assert m["actors"]["n_harness"] == 0 and m["actors"]["harness_line"].startswith("0 harness actions")
    assert m["actors"]["decisions"][0]["who"] == "reviewer@example.org"
    assert {a["backend"] for a in m["artifacts"]} == {"vultr"}
    html = wclient.get("/cases/clean/inference").text
    _clean(html)
    assert "0 unattributed" in html and "0 provenance flags" in html and "strip st-block" not in html


def test_dirty_run_shows_unattributed_loudly_and_flags(wclient):
    m = wclient.get("/cases/dirty/api/viz/inference").json()
    jsonschema.validate(m, SCHEMA)
    un = m["unattributed"]
    # engine call with no step id in the window + a record naming the run with an unknown step + a step with no record
    assert un["n"] == 3 and un["n_records"] == 2 and un["n_steps"] == 1 and un["n_window"] == 1
    assert any("X-BA-Step-Id" in r for r in un["reasons"])
    assert {r["attribution"] for r in un["records"]} == {"window", "run"}
    assert m["pct_vultr"] == pytest.approx(round(100 * 7 / 8, 1))  # the Jev decision is reasoning not on Vultr
    assert m["decider_counts"]["jev"] == 1 and m["decider_counts"]["mock"] == 1
    kinds = {(f["kind"], f["backend"]) for f in m["flags"]}
    assert {("artifact", "recorded"), ("step", "mock"), ("step", "jev"), ("no-record", "vultr")} <= kinds
    assert m["actors"]["n_harness"] == 1 and m["actors"]["harness"][0]["actor"] == "operator-shell"
    html = wclient.get("/cases/dirty/inference").text
    _clean(html)
    assert "strip st-block" in html and "3 unattributed" in html and "is-bad" in html
    assert "1 harness action inside this run" in html and "0 harness actions" not in html
    assert "/cases/dirty/files/02-ontology/factors/factors.json" in html


def test_file_path_and_torn_lines(world, monkeypatch):
    log = world["log"]
    log.write_text(log.read_text() + "{not json\n[1, 2]\n")
    monkeypatch.setenv(inference.LOG_ENV, str(log))
    c = TestClient(create_app(settings_from_env({"ONTOFILL_CONSOLE_CASES": world["spec"]})))
    m = c.get("/cases/clean/api/viz/inference").json()
    assert m["log"]["bad_lines"] == 2 and m["pct_vultr"] == 100 and m["unattributed"]["n"] == 0


def test_global_view(wclient, world):
    r = wclient.get("/inference")
    assert r.status_code == 200
    _clean(r.text)
    g = wclient.get("/api/viz/inference").json()
    jsonschema.validate(g, GLOBAL_SCHEMA)
    total = len(world["lib_log"]) + len(world["clean_log"]) + len(world["dirty_log"])
    assert g["n_calls"] == total and g["n_orphans"] == 2 and g["n_unattributed"] == 3
    assert {o["why"] for o in g["orphans"]} == {"no step_id", "step_id matches no step"}
    rows = {(x["case_id"], x["run_id"]): x for x in g["rows"]}
    assert (
        rows[("clean", "run-clean-0001")]["pct_vultr"] == 100 and rows[("dirty", "run-dirty-0001")]["unattributed"] == 3
    )
    assert "/cases/clean/inference?run=run-clean-0001" in r.text
    assert 'href="/inference"' in wclient.get("/cases/clean/inference").text  # global nav entry


def test_links_from_run_cost_and_tour(wclient, client, monkeypatch):
    html = wclient.get(f"/cases/libraries/runs/{LIB_RUN}").text
    assert "Inference: 8 calls · 100% on Vultr" in html and f"/cases/libraries/inference?run={LIB_RUN}" in html
    assert "Inference: 7 calls · 100% on Vultr" in wclient.get("/cases/clean/runs/run-clean-0001").text
    assert "3 unattributed" in wclient.get("/cases/dirty/runs/run-dirty-0001").text
    assert "Inference: 8 calls · 100% on Vultr" in wclient.get("/cases/libraries/cost").text
    tour = wclient.get("/api/viz/tour?case=clean").json()
    row = next(r for r in tour["track"] if "Vultr" in r["requirement"])
    assert row["view"] == "Inference" and row["href"] == "/cases/clean/inference?run=run-clean-0001"
    assert row["state"] == "ready"
    q8 = next(s for s in tour["steps"] if s["n"] == 8)
    assert {"label": "Inference", "href": "/cases/clean/inference?run=run-clean-0001"} in q8["also"]
    dirty = next(r for r in wclient.get("/api/viz/tour?case=dirty").json()["track"] if "Vultr" in r["requirement"])
    assert dirty["state"] == "partial" and "3 unattributed" in dirty["why"]
    monkeypatch.delenv(inference.LOG_ENV, raising=False)
    assert "Inference: gateway log not mounted" in client.get(f"/cases/libraries/runs/{LIB_RUN}").text


def test_engine_calls_without_step_ids_join_by_time_window(world, monkeypatch, tmp_path):
    """Today's engine principal logs run_id null and step_id null: such calls inside the run's window are listed as
    attributed by time window (not by step), counted in cost and the Vultr share, and counted as unattributed."""
    clean = world["clean_log"]
    stripped = [
        ({k: v for k, v in r.items() if k not in ("step_id",)} if r["session_id"] == "engine" else r) for r in clean
    ]
    log = write_jsonl(tmp_path / "engine-only" / "calls.jsonl", stripped)
    monkeypatch.setenv(inference.LOG_ENV, str(log))
    c = TestClient(create_app(settings_from_env({"ONTOFILL_CONSOLE_CASES": world["spec"]})))
    m = c.get("/cases/clean/api/viz/inference").json()
    window = [x for x in m["calls"] if x["attribution"] == "window"]
    assert len(window) == 2 and all(x["session_id"] == "engine" for x in window)
    assert m["n_calls"] == 7 and m["pct_vultr"] == 100
    # the two engine calls cannot be tied to a step, and the two loop steps have no joined record
    assert m["unattributed"]["n"] == 4 and m["unattributed"]["n_window"] == 2
    assert any("engine call" in r and "X-BA-Step-Id" in r for r in m["unattributed"]["reasons"])
    assert "time window (no step id)" in c.get("/cases/clean/inference").text


def test_purpose_mapping():
    step = {"kind": "loop", "loop": {"role": "critique"}}
    assert inference.call_purpose({"purpose": "screen"}, step)[0] == "screen"
    assert inference.call_purpose({"purpose": "decision"}, None)[0] == "decide"
    assert inference.call_purpose({"purpose": "chat"}, step)[0] == "critique"
    assert inference.call_purpose({"purpose": "chat"}, {"kind": "verify"})[0] == "verify"
    assert inference.call_purpose({"purpose": "chat"}, {"requested": {"tool": "extract.fields"}})[0] == "extract"
    assert inference.call_purpose({"purpose": "chat", "engine_purpose": "critic.ontology"}, None)[0] == "critique"
    assert inference.call_purpose({"purpose": "chat", "engine_purpose": "phase1.prd.section"}, None)[0] == "plan"
    assert inference.call_purpose({"purpose": "chat"}, None)[0] == "chat"


def test_no_domain_words_in_inference_files():
    deny = (
        "supplier",
        "proveedor",
        "rfc",
        "sat",
        "compranet",
        "procurement",
        "mexico",
        "méxico",
        "contrato",
        "licitación",
        "licitacion",
        "tax list",
        "sanction registry",
    )
    for rel in (
        "viz/inference.py",
        "templates/viz/inference.html",
        "templates/viz/inference_all.html",
        "static/viz-inference.css",
    ):
        text = (PKG / rel).read_text().lower()
        hits = [w for w in deny if re.search(rf"\b{re.escape(w)}\b", text)]
        assert not hits, (rel, hits)
