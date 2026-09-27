"""Watch (/watch, /api/watch): global supervision for unattended runs. Runner state, running / stale / done runs and
caps are all built in tmp; nothing reads a real runner directory or lake."""

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest
from conftest import spec_for
from fastapi.testclient import TestClient

from ontofill_console.viz import watch
from ontofill_console.web import create_app, settings_from_env

NOW = datetime(2026, 9, 27, 3, 0, tzinfo=UTC)
LIVE_RID = "run-watch-live"
STORE = "fixture-libraries"  # the libraries fixture lake's case id under runs/ and gold/


def iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def ago(minutes: float) -> str:
    return iso(NOW - timedelta(minutes=minutes))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def step(i: int, minutes_ago: float, *, src="ok-src", phase=5, usd=None, failed=False, reason=None) -> dict:
    s = {
        "step_id": f"step:{LIVE_RID}:{i:04d}",
        "run_id": LIVE_RID,
        "ts": ago(minutes_ago),
        "phase": phase,
        "mode": "D1",
        "source_id": src,
        "requested": {"tool": "page.read"},
        "executed": {"ok": not failed},
        "evaluated": {"status": "failed", "reason": reason or "timeout"} if failed else {"status": "ok"},
    }
    if usd is not None:
        s["usage"] = {"model": "m", "backend": "vultr", "input_tokens": 10, "output_tokens": 5, "est_usd": usd}
    return s


def live_run(cases_dir: Path, steps: list[dict], state="running", metrics=None, jobs=()) -> Path:
    run = cases_dir / "libraries" / "lake" / "runs" / STORE / LIVE_RID
    write_json(
        run / "status.json",
        {
            "run_id": LIVE_RID,
            "case_id": STORE,
            "state": state,
            "phase": 5,
            "updated_at": steps[-1]["ts"] if steps else ago(1),
            **({"metrics": metrics} if metrics else {}),
        },
    )
    write_jsonl(run / "trace.live.jsonl", steps)
    write_jsonl(run / "jobs.jsonl", list(jobs))
    write_json(cases_dir / "libraries" / "lake" / "runs" / STORE / "latest.json", {"run_id": LIVE_RID})
    return run


def runner_case(root: Path, case_id: str, **status) -> None:
    write_json(root / "cases" / case_id / "status.json", {"updated_at": ago(0.5), **status})


def events(root: Path, rows) -> None:
    write_jsonl(root / "events.jsonl", rows)


def settings(cases_dir: Path, root: Path):
    return settings_from_env(
        {
            "ONTOFILL_CONSOLE_CASES": spec_for(cases_dir),
            "ONTOFILL_CONSOLE_IDENTITY": "local",
            "ONTOFILL_RUNNER_STATE": str(root),
        }
    )


def case_of(m: dict, cid: str = "libraries") -> dict:
    return next(c for c in m["cases"] if c["case_id"] == cid)


# JSON schema --------------------------------------------------------------------------------------------------------
NUM = {"type": ["number", "null"]}
STR = {"type": ["string", "null"]}
WATCH = {
    "type": "object",
    "required": [
        "generated_at",
        "stale_min",
        "runner",
        "global_spend",
        "cases",
        "attention",
        "n_attention",
        "n_stale",
        "n_running",
        "live",
    ],
    "properties": {
        "generated_at": {"type": "string"},
        "runner": {
            "type": "object",
            "required": ["on", "killed", "state_dir_ok", "state_dir", "last_write_at", "last_write_age_s", "n_events"],
            "properties": {
                "on": {"type": "boolean"},
                "killed": {"type": "boolean"},
                "state_dir_ok": {"type": "boolean"},
            },
        },
        "global_spend": {
            "type": "object",
            "required": ["global_usd", "global_cap_usd", "burn_usd_per_h", "projection"],
        },
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "case_id",
                    "title",
                    "runner",
                    "run_id",
                    "liveness",
                    "throughput",
                    "failures",
                    "spend",
                    "dod",
                    "links",
                    "moving",
                ],
                "properties": {
                    "runner": {
                        "type": "object",
                        "required": [
                            "state",
                            "run_id",
                            "checkpoint",
                            "reason",
                            "paused",
                            "pid",
                            "running_since",
                            "last_resumed_at",
                            "updated_at",
                            "spent_usd_case",
                            "spent_usd_global",
                            "last_resume",
                        ],
                    },
                    "liveness": {
                        "type": "object",
                        "required": ["last_step_age_s", "status_age_s", "stale", "stale_min"],
                        "properties": {
                            "stale": {"type": "boolean"},
                            "last_step_age_s": {"type": ["integer", "null"]},
                            "status_age_s": {"type": ["integer", "null"]},
                        },
                    },
                    "throughput": {
                        "type": "object",
                        "required": ["phases", "total", "n_cells", "cells_per_h_60", "cells_per_h_run", "anchor"],
                        "properties": {
                            "phases": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["phase", "name", "n", "per_min_15", "per_min_60", "per_min_run"],
                                },
                            }
                        },
                    },
                    "failures": {
                        "type": "object",
                        "required": [
                            "n_steps",
                            "n_failures",
                            "rate_per_100",
                            "rate_per_100_15",
                            "rising",
                            "by_kind",
                            "sources",
                            "only_failures",
                        ],
                        "properties": {
                            "sources": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": [
                                        "source_id",
                                        "steps",
                                        "failures",
                                        "rate_per_100",
                                        "by_kind",
                                        "only_failures",
                                    ],
                                },
                            }
                        },
                    },
                    "spend": {
                        "type": "object",
                        "required": [
                            "case_usd",
                            "case_cap_usd",
                            "global_usd",
                            "global_cap_usd",
                            "burn_usd_per_h",
                            "projected_cap_hit_at",
                        ],
                        "properties": {
                            "case_usd": NUM,
                            "case_cap_usd": NUM,
                            "global_usd": NUM,
                            "global_cap_usd": NUM,
                            "burn_usd_per_h": NUM,
                            "projected_cap_hit_at": STR,
                        },
                    },
                    "dod": {
                        "type": "object",
                        "required": [
                            "entities_total",
                            "pct_meeting",
                            "distinct_sources",
                            "criteria",
                            "series",
                            "n_met",
                            "n_criteria",
                        ],
                        "properties": {
                            "criteria": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["criterion_id", "label", "actual", "target", "met"],
                                },
                            }
                        },
                    },
                },
            },
        },
        "attention": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["severity", "level", "state", "case_id", "text", "href"],
                "properties": {
                    "severity": {"type": "integer", "minimum": 1, "maximum": 5},
                    "href": {"type": "string", "pattern": "^/"},
                    "case_id": STR,
                },
            },
        },
    },
}


# tests --------------------------------------------------------------------------------------------------------------
def test_json_schema_and_html_on_the_fixtures(client):
    r = client.get("/api/watch")
    assert r.status_code == 200
    m = r.json()
    jsonschema.validate(m, WATCH)
    assert {c["case_id"] for c in m["cases"]} == {"libraries", "parks"}
    assert m["runner"]["state_dir_ok"] is False  # no runner dir: said, not guessed
    lib = case_of(m)
    assert lib["run_id"] == "run-libraries-0001" and lib["throughput"]["total"]["n"] > 0
    assert lib["spend"]["case_cap_usd"] is None  # no cap visible: unknown, never invented
    page = client.get("/watch")
    assert page.status_code == 200
    html = page.text
    assert "Undefined" not in html and "built-in method" not in html
    assert "What I'd look at first" in html and "/api/watch" in html and "cap unknown" in html
    assert str(lib["throughput"]["total"]["n"]) in html and str(lib["failures"]["n_failures"]) in html
    assert 'data-live="0"' in html  # nothing running in the fixtures
    parks = case_of(m, "parks")
    assert parks["run_id"] is None and parks["dod"]["criteria"] == []


def test_api_watch_is_fast(client):
    """Polled every minute: well under 300 ms on the fixtures (best of 5, so a loaded CI host does not flake it)."""
    times = []
    for _ in range(5):
        t = time.perf_counter()
        assert client.get("/api/watch").status_code == 200
        times.append(time.perf_counter() - t)
    assert min(times) < 0.3, times


def test_stale_detection_at_the_threshold(cases_dir, tmp_path):
    root = tmp_path / "runner"
    runner_case(root, "libraries", state="running", run_id=LIVE_RID, running_since=ago(40))
    live_run(cases_dir, [step(1, 30), step(2, 10)])  # last step exactly 10 min before NOW
    s = settings(cases_dir, root)
    at = watch.model(s, now=NOW, env={})
    lv = case_of(at)["liveness"]
    assert lv["last_step_age_s"] == 600 and lv["stale"] is False  # at the threshold: not stale
    past = watch.model(s, now=NOW + timedelta(seconds=1), env={})
    assert case_of(past)["liveness"]["stale"] is True
    assert past["attention"][0]["severity"] == 1 and "STALE" in past["attention"][0]["text"]
    assert watch.model(s, now=NOW, stale_min=5, env={})["n_stale"] == 1  # the threshold is configurable
    # a run the runner just (re)started has not gone stale even though its last step is old
    runner_case(root, "libraries", state="running", run_id=LIVE_RID, running_since=ago(3))
    assert case_of(watch.model(s, now=NOW + timedelta(seconds=1), env={}))["liveness"]["stale"] is False
    # a finished run is never stale
    runner_case(root, "libraries", state="done", run_id=LIVE_RID)
    live_run(cases_dir, [step(1, 30), step(2, 20)], state="done")
    assert case_of(watch.model(s, now=NOW, env={}))["liveness"]["stale"] is False


def test_stale_page_is_loud_and_live(cases_dir, tmp_path):
    root = tmp_path / "runner"
    now = datetime.now(UTC)
    runner_case(root, "libraries", state="running", run_id=LIVE_RID, running_since=iso(now - timedelta(minutes=60)))
    run = live_run(cases_dir, [step(1, 0)])
    write_jsonl(run / "trace.live.jsonl", [{**step(1, 0), "ts": iso(now - timedelta(minutes=25))}])
    c = TestClient(create_app(settings(cases_dir, root)))
    html = c.get("/watch").text
    assert 'data-live="1"' in html and "STALE · no step has landed for 25 min" in html
    assert c.get("/api/watch?stale_min=30").json()["n_stale"] == 0
    assert c.get("/api/watch?stale_min=0").status_code == 422


def test_failure_rates_by_source(cases_dir, tmp_path):
    steps = [step(i, 50 - i, src="ok-src") for i in range(6)]
    steps += [step(10 + i, 5 - i, src="bad-src", failed=True) for i in range(3)]
    steps.append(step(20, 1, src="bad-src", failed=True, reason="captcha wall"))
    live_run(cases_dir, steps)
    f = case_of(watch.model(settings(cases_dir, tmp_path / "runner"), now=NOW, env={}))["failures"]
    assert f["n_steps"] == 10 and f["n_failures"] == 4 and f["rate_per_100"] == 40.0
    by = {s["source_id"]: s for s in f["sources"]}
    assert by["bad-src"]["rate_per_100"] == 100.0 and by["ok-src"]["rate_per_100"] == 0.0
    assert by["bad-src"]["by_kind"]["failure"] == 3 and by["bad-src"]["by_kind"]["stop"] == 1
    assert f["only_failures"] == ["bad-src"]
    assert f["steps_15"] == 4 and f["rate_per_100_15"] == 100.0 and f["rising"] is True


def test_spend_projection_math():
    steps = [
        {"ts": iso(NOW - timedelta(minutes=30)), "usage": {"model": "m", "est_usd": 0.2}},
        {"ts": iso(NOW - timedelta(minutes=10)), "usage": {"model": "m", "est_usd": 0.3}},
    ]
    burn, usd, hours = watch.burn_rate(steps, NOW)
    assert usd == 0.5 and hours == 0.5 and burn == 1.0  # the run is 30 min old: 0.50 USD over half an hour
    p = watch.projection(1.5, 2.0, burn, NOW)
    assert p["remaining_usd"] == 0.5 and p["hours"] == 0.5 and p["at"] == iso(NOW + timedelta(minutes=30))
    assert watch.projection(2.5, 2.0, burn, NOW)["hours"] == 0.0  # already over
    assert watch.projection(1.0, None, burn, NOW)["at"] is None  # unknown cap: no projection
    assert watch.projection(1.0, 2.0, None, NOW)["at"] is None  # no burn: no projection
    old = [{"ts": iso(NOW - timedelta(minutes=m)), "usage": {"model": "m", "est_usd": 0.1}} for m in (180, 90, 45)]
    burn, usd, hours = watch.burn_rate(old, NOW)
    assert hours == 1.0 and usd == 0.1 and burn == 0.1  # only the last hour counts


def test_spend_caps_and_projection_in_the_model(cases_dir, tmp_path):
    root = tmp_path / "runner"
    runner_case(root, "libraries", state="running", run_id=LIVE_RID, running_since=ago(30), spent_usd_global=4.0)
    live_run(cases_dir, [step(1, 30, usd=0.2), step(2, 1, usd=0.3)])
    env = {"ONTOFILL_RUNNER_BUDGETS": "libraries=1.016,other=9", "ONTOFILL_RUNNER_GLOBAL_USD": "5"}
    m = watch.model(settings(cases_dir, root), now=NOW, env=env)
    s = case_of(m)["spend"]
    assert s["case_usd"] == pytest.approx(0.516)  # every run's trace: 0.016 (fixture run) + 0.50 (this run)
    assert s["case_cap_usd"] == 1.016 and s["case_cap_basis"] == "ONTOFILL_RUNNER_BUDGETS"
    assert s["global_usd"] == 4.0 and s["global_cap_usd"] == 5.0
    assert s["burn_usd_per_h"] == 1.0
    assert s["projected_cap_hit_at"] == iso(NOW + timedelta(minutes=30))
    assert m["global_spend"]["projection"]["hours"] == 1.0
    assert any(a["level"] == "spend" and a["case_id"] == "libraries" for a in m["attention"])
    assert any(a["level"] == "spend" and a["case_id"] is None for a in m["attention"])  # the global cap too
    # caps from the runner's own budget-stop reason when the env is not visible
    runner_case(root, "libraries", state="budget_stop", run_id=LIVE_RID, reason="case spend $1.52 ≥ $1.50")
    stopped = case_of(watch.model(settings(cases_dir, root), now=NOW + timedelta(hours=1), env={}))
    assert stopped["moving"] is False and stopped["liveness"]["stale"] is False  # the runner stopped it
    s = stopped["spend"]
    assert s["case_cap_usd"] == 1.5 and s["case_cap_basis"] == "runner budget-stop reason"
    assert s["burn_usd_per_h"] is None  # not running: no burn, no projection


def test_attention_ranking_and_runner_resume(cases_dir, tmp_path):
    root = tmp_path / "runner"
    runner_case(root, "libraries", state="running", run_id=LIVE_RID, running_since=ago(60), last_resumed_at=ago(60))
    runner_case(root, "parks", state="waiting_approval", checkpoint="prd")
    events(
        root,
        [
            {
                "ts": ago(61),
                "case_id": "libraries",
                "kind": "paused_at_checkpoint",
                "detail": "waiting",
                "run_id": LIVE_RID,
            },
            {
                "ts": ago(60),
                "case_id": "libraries",
                "kind": "resumed",
                "detail": "resumed after the ontology decision",
                "run_id": LIVE_RID,
            },
        ],
    )
    steps = [step(i, 40 - i, src="ok-src", usd=0.01) for i in range(4)]
    steps += [step(10 + i, 20 - i, src="bad-src", failed=True) for i in range(3)]
    live_run(
        cases_dir,
        steps,
        metrics={
            "entities_total": {"library": 10},
            "entities_meeting_dod": {"library": 2},
            "distinct_source_classes": 1,
            "dod": [
                {
                    "criterion_id": "libraries_found",
                    "query": "count_entities(library) >= 20",
                    "target": 20,
                    "actual": 10,
                    "met": False,
                }
            ],
        },
    )
    (root / "KILL").write_text("{}")
    m = watch.model(settings(cases_dir, root), now=NOW, env={"ONTOFILL_RUNNER_DEFAULT_CASE_USD": "0.1"})
    jsonschema.validate(m, WATCH)
    sev = [a["severity"] for a in m["attention"]]
    assert sev == sorted(sev)
    levels = [a["level"] for a in m["attention"]]
    assert levels[0] == "critical" and "Kill switch" in m["attention"][0]["text"]
    assert m["runner"]["on"] is False and m["runner"]["killed"] is True
    assert any("STALE" in a["text"] for a in m["attention"] if a["severity"] == 1)
    assert {"person", "failures", "spend", "dod"} <= set(levels)
    assert (
        levels.index("critical")
        < levels.index("person")
        < levels.index("failures")
        < levels.index("spend")
        < levels.index("dod")
    )
    lib = case_of(m)
    assert lib["runner"]["last_resume"]["detail"] == "resumed after the ontology decision"
    assert lib["dod"]["basis"].startswith("status.json") and lib["dod"]["criteria"][0]["actual"] == 10
    assert lib["dod"]["pct_meeting"] == 20.0 and lib["dod"]["series"][-1]["source"] == "status"
    hrefs = {a["level"]: a["href"] for a in m["attention"] if a["case_id"] == "libraries"}
    assert (
        hrefs["failures"] == f"/cases/libraries/failures?run={LIVE_RID}" and hrefs["dod"] == "/cases/libraries/output"
    )
    assert next(a for a in m["attention"] if a["case_id"] == "parks" and a["level"] == "person")["href"] == "/inbox"


def test_throughput_per_phase_and_cells(cases_dir, tmp_path):
    root = tmp_path / "runner"
    runner_case(root, "libraries", state="running", run_id=LIVE_RID, running_since=ago(120))
    steps = [step(i, 100 - i, phase=3) for i in range(10)]  # 10 P3 steps 100..91 min ago
    steps += [step(20 + i, 30 - 2 * i, phase=5) for i in range(15)]  # 15 P5 steps from 30 to 2 min ago
    jobs = [{"job_id": f"job:{i}", "step_id": steps[10 + i]["step_id"], "source_id": "ok-src"} for i in range(6)]
    live_run(cases_dir, steps, jobs=jobs)
    t = case_of(watch.model(settings(cases_dir, root), now=NOW, env={}))["throughput"]
    ph = {p["phase"]: p for p in t["phases"]}
    assert ph[3]["n"] == 10 and ph[3]["n_60"] == 0 and ph[3]["per_min_15"] == 0
    assert ph[5]["n_15"] == 7 and ph[5]["per_min_15"] == round(7 / 15, 2) and ph[5]["per_min_60"] == 0.25
    assert t["total"]["per_min_run"] == 0.25  # 25 steps over the 100 min since the first step
    assert t["n_cells"] == 6 and t["cells_per_h_60"] == 6 and t["cells_per_h_run"] == 3.6


def test_unknown_runner_state_and_empty_registry():
    c = TestClient(
        create_app(
            settings_from_env(
                {
                    "ONTOFILL_CONSOLE_CASES": "",
                    "ONTOFILL_CONSOLE_IDENTITY": "local",
                    "ONTOFILL_RUNNER_STATE": "/nonexistent/ontofill-runner-test",
                }
            )
        )
    )
    m = c.get("/api/watch").json()
    assert m["cases"] == [] and m["runner"]["state_dir_ok"] is False
    assert m["attention"][0]["severity"] == 1 and "Runner state not found" in m["attention"][0]["text"]
    assert "No cases registered" in c.get("/watch").text


def test_registry_budget_is_the_case_cap():
    from ontofill_console.viz.watch import caps

    got = caps("x", {"_status": {}}, {"ONTOFILL_RUNNER_BUDGETS": "x=9"}, {"budget_usd": 1.5})
    assert got["case_cap_usd"] == 1.5 and got["case_cap_basis"] == "cases.json budget_usd"
    assert caps("x", {"_status": {}}, {"ONTOFILL_RUNNER_BUDGETS": "x=9"})["case_cap_usd"] == 9


def test_global_spend_falls_back_to_the_sum_of_case_traces():
    from types import SimpleNamespace

    from ontofill_console.viz import watch

    cases = [
        {"spend": {"global_usd": None, "case_usd": 0.25, "global_cap_usd": 40.0, "burn_usd_per_h": None}},
        {"spend": {"global_usd": None, "case_usd": 0.5, "global_cap_usd": None, "burn_usd_per_h": None}},
    ]
    orig = watch.case_model
    try:
        it = iter(cases)
        watch.case_model = lambda *a, **k: {
            **next(it),
            "liveness": {"last_step_at": None, "status_updated_at": None, "stale": False},
            "moving": False,
        }
        watch_attention, watch.attention = watch.attention, lambda *a, **k: []
        m = watch.model(SimpleNamespace(cases={"a": 1, "b": 2}, runner_state="/nonexistent"))
    finally:
        watch.case_model, watch.attention = orig, watch_attention
    g = m["global_spend"]
    assert g["global_usd"] == 0.75 and "sum of the cases' traces" in g["global_basis"] and g["global_cap_usd"] == 40.0


def test_global_fallback_prefers_the_gateway_log(tmp_path, monkeypatch):
    import json

    from ontofill_console.viz import watch

    log = tmp_path / "gw"
    log.mkdir()
    (log / "gateway-calls.jsonl").write_text(
        "".join(json.dumps({"est_usd": x, "session_id": s}) + "\n" for x, s in ((0.1, "engine"), (0.05, "bas-1")))
    )
    monkeypatch.setenv("ONTOFILL_CONSOLE_GATEWAY_LOG", str(log))
    usd, basis = watch._fallback_global([{"case_usd": 0.01}])
    assert usd == 0.15 and "gateway call log" in basis


def test_engine_exit_and_long_runner_reasons(cases_dir, tmp_path):
    """Runner 1d4d004: engine_stop records the engine's last exit; a failure reason carries the engine's last line
    and can be long, so /watch shows it truncated with the whole text in the title."""
    root = tmp_path / "runner"
    long = "engine exited 1 in phase 3: " + "the critic rejected every candidate; " * 20
    runner_case(
        root,
        "libraries",
        state="failed",
        run_id=LIVE_RID,
        reason=long,
        resumed_from_checkpoint="ontology",
        engine_stop={
            "exit_code": 1,
            "at": ago(2),
            "state": "failed",
            "phase": 3,
            "checkpoint_pending": None,
            "reason": long,
        },
    )
    live_run(cases_dir, [step(i, 10 - i, src="ok-src") for i in range(3)])
    m = watch.model(settings(cases_dir, root), now=NOW)
    r = case_of(m)["runner"]
    assert r["engine_stop"]["exit_code"] == 1 and r["engine_stop"]["phase"] == 3
    assert r["resumed_from_checkpoint"] == "ontology"
    alert = next(a for a in m["attention"] if a["case_id"] == "libraries" and "failed" in a["text"].lower())
    assert len(alert["text"]) < 300 and alert["text"].endswith("…")
    app = create_app(settings(cases_dir, root))
    html = TestClient(app).get("/watch").text
    assert "Engine exit" in html and "code 1" in html and "· P3" in html
    assert f'title="{long}"' in html.replace("&#39;", "'") and long not in html.split('title="')[0]


def test_a_pause_with_nothing_to_decide_is_stuck_not_waiting(cases_dir, tmp_path):
    """The runner can say waiting_approval while the engine wrote no APPROVAL_PENDING (e.g. the PRD failed its own
    validation). No one can decide it, so /watch calls it stuck, not "waiting for a person"."""
    root = tmp_path / "runner"
    runner_case(
        root,
        "libraries",
        state="waiting_approval",
        checkpoint="prd",
        run_id=LIVE_RID,
        reason="phase1.prd failed validation after 3 attempts: a cross-check lacks a named publisher",
    )
    live_run(cases_dir, [step(i, 10 - i, src="ok-src") for i in range(3)])
    for p in (cases_dir / "libraries" / "case").rglob("APPROVAL_PENDING.md"):
        p.unlink()
    m = watch.model(settings(cases_dir, root), now=NOW)
    mine = [a for a in m["attention"] if a["case_id"] == "libraries"]
    assert any(a["text"].startswith("Stuck at the prd checkpoint") and "failed validation" in a["text"] for a in mine)
    assert not any(a["text"].startswith("Waiting for a person") for a in mine)


def test_needs_human_is_a_need_not_a_failure(cases_dir, tmp_path):
    """Runner 85c01ee / R28: P3 found no authoritative source, so the runner waits on a person (revise the brief or
    the authority policy, then start a new run). /watch shows it as a need with its reason, never as a crash."""
    root = tmp_path / "runner"
    why = "the engine found no authoritative source for library_name, opening_hours"
    runner_case(root, "libraries", state="needs_human", run_id=LIVE_RID, reason=why)
    events(root, [{"ts": ago(1), "case_id": "libraries", "kind": "needs-human", "detail": why, "run_id": LIVE_RID}])
    live_run(cases_dir, [step(i, 10 - i, src="ok-src") for i in range(3)])
    m = watch.model(settings(cases_dir, root), now=NOW)
    mine = [a for a in m["attention"] if a["case_id"] == "libraries"]
    need = next(a for a in mine if a["text"].startswith("Needs you:"))
    assert need["severity"] == 2 and why in need["text"] and "/discovery" in need["href"]
    assert not any(a["text"].startswith(("Runner failed", "Run failed")) for a in mine)
    assert case_of(m)["run_id"] == LIVE_RID
    client = TestClient(create_app(settings(cases_dir, root)))
    assert 'chip st-need">runner needs human' in client.get("/watch").text
    hub = client.get("/cases/libraries").text
    assert "Needs you: the engine found no authoritative source" in hub and why in hub


def model_call(i: int, minutes_ago: float, purpose="phase3.plan_queries") -> dict:
    return {
        "step_id": f"step:{LIVE_RID}:m{i:04d}",
        "run_id": LIVE_RID,
        "ts": ago(minutes_ago),
        "phase": 3,
        "mode": "D1",
        "requested": {"tool": "decision.complete_json", "engine_purpose": purpose},
        "executed": {"artifact": purpose},
        "evaluated": {"status": "ok"},
    }


def test_moving_but_not_progressing(cases_dir, tmp_path):
    """Live: P3 kept proposing publishers and planning queries every few minutes and never searched or captured.
    Steps kept landing, so it was never STALE; /watch now calls it out."""
    root = tmp_path / "runner"
    runner_case(root, "libraries", state="running", run_id=LIVE_RID, running_since=ago(60))
    steps = [step(0, 40, src="ok-src")]  # the last real progress, 40 min ago
    steps += [model_call(i, 36 - 3 * i, ("phase3.propose_publishers", "phase3.plan_queries")[i % 2]) for i in range(12)]
    live_run(cases_dir, steps)
    m = watch.model(settings(cases_dir, root), now=NOW)
    stall = case_of(m)["stall"]
    assert stall and stall["model_calls"] == 12 and stall["minutes"] == 40
    assert "phase3.plan_queries" in stall["purposes"]
    alert = next(a for a in m["attention"] if a["case_id"] == "libraries" and a["text"].startswith("MOVING BUT"))
    assert alert["severity"] == 2 and "12 model calls in 40 min" in alert["text"]
    html = TestClient(create_app(settings(cases_dir, root))).get("/watch").text
    assert "moving but not progressing" in html


def test_a_progressing_run_is_not_a_stall(cases_dir, tmp_path):
    root = tmp_path / "runner"
    runner_case(root, "libraries", state="running", run_id=LIVE_RID, running_since=ago(60))
    steps = [model_call(i, 40 - 3 * i) for i in range(10)] + [step(99, 2, src="ok-src")]  # a capture 2 min ago
    live_run(cases_dir, steps)
    assert case_of(watch.model(settings(cases_dir, root), now=NOW))["stall"] is None


def test_definition_phases_are_model_work_not_a_stall(cases_dir, tmp_path):
    root = tmp_path / "runner"
    runner_case(root, "libraries", state="running", run_id=LIVE_RID, running_since=ago(60))
    steps = [{**model_call(i, 40 - 3 * i, "phase1.prd"), "phase": 1} for i in range(12)]
    live_run(cases_dir, steps)
    assert case_of(watch.model(settings(cases_dir, root), now=NOW))["stall"] is None
