"""Headless browser: /watch fits 1280 and 390 px without horizontal page scroll, on the fixtures and with a running,
stale case (loud STALE strip, live polling on)."""

import json
import socket
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
import uvicorn
from conftest import spec_for

from ontofill_console.web import create_app, settings_from_env

pytestmark = pytest.mark.ui
playwright = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(app):
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    return srv, thread, f"http://127.0.0.1:{port}"


@pytest.fixture
def running(cases_dir, tmp_path):
    """libraries: a running run whose last step landed 25 min ago, a failing source, priced steps and caps."""
    now = datetime.now(UTC)
    rid = "run-watch-ui"
    run = cases_dir / "libraries" / "lake" / "runs" / "fixture-libraries" / rid
    run.mkdir(parents=True)
    steps = []
    for i in range(12):
        t = now - timedelta(minutes=70 - 4 * i)
        bad = i % 3 == 0
        steps.append(
            {
                "step_id": f"step:{rid}:{i}",
                "ts": t.isoformat(timespec="seconds"),
                "phase": 5 if i > 3 else 3,
                "mode": "D1",
                "source_id": "bad-src" if bad else "good-src",
                "requested": {"tool": "page.read"},
                "evaluated": {"status": "failed", "reason": "timeout"} if bad else {"status": "ok"},
                "usage": {"model": "m", "backend": "vultr", "est_usd": 0.05},
            }
        )
    (run / "trace.live.jsonl").write_text("".join(json.dumps(s) + "\n" for s in steps))
    (run / "status.json").write_text(
        json.dumps({"run_id": rid, "state": "running", "phase": 5, "updated_at": steps[-1]["ts"]})
    )
    (run.parent / "latest.json").write_text(json.dumps({"run_id": rid}))
    root = tmp_path / "runner"
    (root / "cases" / "libraries").mkdir(parents=True)
    (root / "cases" / "libraries" / "status.json").write_text(
        json.dumps(
            {
                "state": "running",
                "run_id": rid,
                "running_since": (now - timedelta(minutes=75)).isoformat(),
                "spent_usd_global": 3.2,
                "updated_at": now.isoformat(),
            }
        )
    )
    (root / "events.jsonl").write_text(
        json.dumps(
            {
                "ts": (now - timedelta(minutes=75)).isoformat(),
                "case_id": "libraries",
                "kind": "resumed",
                "detail": "resumed after the ontology decision",
                "run_id": rid,
            }
        )
        + "\n"
    )
    return root


def check(server, expect_live: bool):
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for width in (1280, 390):
            page.set_viewport_size({"width": width, "height": 900})
            resp = page.goto(server + "/watch")
            assert resp.status == 200
            scroll = page.evaluate("document.documentElement.scrollWidth")
            assert scroll <= width, (width, scroll)
            live = page.get_attribute("[data-live-root]", "data-live")
            assert live == ("1" if expect_live else "0")
        assert not errors, errors
        browser.close()


def test_watch_fits_on_the_fixtures(cases_dir):
    srv, thread, url = _serve(create_app(settings_from_env({"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir)})))
    try:
        check(url, expect_live=False)
    finally:
        srv.should_exit = True
        thread.join(timeout=5)


def test_watch_fits_with_a_stale_running_case(cases_dir, running, monkeypatch):
    monkeypatch.setenv("ONTOFILL_RUNNER_BUDGETS", "libraries=1.5")
    monkeypatch.setenv("ONTOFILL_RUNNER_GLOBAL_USD", "5")
    app = create_app(
        settings_from_env({"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir), "ONTOFILL_RUNNER_STATE": str(running)})
    )
    srv, thread, url = _serve(app)
    try:
        check(url, expect_live=True)
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(url + "/watch")
            assert page.locator(".wt-stale .strip.st-block").count() == 1
            assert page.locator(".wt-attn li").first.inner_text().count("STALE") == 1
            browser.close()
    finally:
        srv.should_exit = True
        thread.join(timeout=5)
