"""Live streaming on the viz views: a run in motion makes /inbox and /cases/<id>/operation carry data-live="1" and
[data-live-region] markers, and static/viz-live.js swaps those regions in place (no reload). Finished runs never poll."""

import json
import re
import socket
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
import uvicorn
from conftest import spec_for

from ontofill_console.viz import operation as op
from ontofill_console.web import create_app, settings_from_env

RUN = "run-libraries-0001"


def run_dir(cases_dir):
    return cases_dir / "libraries" / "lake" / "runs" / "fixture-libraries" / RUN


def set_status(cases_dir, **changes) -> dict:
    path = run_dir(cases_dir) / "status.json"
    status = {**json.loads(path.read_text()), **changes}
    path.write_text(json.dumps(status))
    return status


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def append_steps(cases_dir, n: int, start: int, when: datetime) -> None:
    with (run_dir(cases_dir) / "trace.live.jsonl").open("a") as f:
        for i in range(n):
            f.write(
                json.dumps(
                    {
                        "step_id": f"step:{RUN}:live{start + i:04d}",
                        "run_id": RUN,
                        "phase": 5,
                        "source_id": "registry-example",
                        "objective_id": "profile",
                        "mode": "D1",
                        "observed": "page loaded",
                        "requested": "extract hours",
                        "executed": "extract",
                        "evaluated": "ok",
                        "value_ids": [],
                        "ts": iso(when + timedelta(seconds=i)),
                    }
                )
                + "\n"
            )


LIVE_ATTR = re.compile(r'data-live-root data-live="(\d)"')


# pure liveness rule ---------------------------------------------------------------------------------------------------
def test_is_live_rule():
    now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    assert op.is_live({"state": "running", "updated_at": "2026-01-01T00:00:00+00:00"}, now)
    assert op.is_live({"state": "paused", "updated_at": iso(now - timedelta(seconds=30))}, now)
    assert not op.is_live({"state": "paused", "updated_at": iso(now - timedelta(minutes=10))}, now)
    assert not op.is_live({"state": "done", "updated_at": iso(now)}, now)
    assert not op.is_live({"state": "failed", "updated_at": iso(now)}, now)
    assert not op.is_live({}, now)
    m = op.live_marker(True, "2026-09-26T18:40:05+00:00")
    assert m == {
        "on": True,
        "updated_at": "2026-09-26T18:40:05+00:00",
        "hhmmss": "18:40:05",
        "poll_ms": op.LIVE_POLL_MS,
    }


# server side: markers for a running run, none for a finished one ------------------------------------------------------
def test_running_run_marks_operation_and_inbox_live(cases_dir, make_client):
    set_status(cases_dir, state="running", updated_at=iso(datetime.now(UTC)))
    client = make_client()
    page = client.get("/cases/libraries/operation").text
    assert LIVE_ATTR.search(page).group(1) == "1"
    assert 'data-live-poll="2500"' in page and "/static/viz-live.js" in page
    for region in ("runs", "runhead", "kv", "pipe", "notes", "reopens", "loops", "deciders"):
        assert f'data-live-region="{region}"' in page, region
    assert 'class="chip st-run">live · <span data-live-updated aria-live="polite"' in page
    api = client.get("/cases/libraries/api/viz/operation").json()
    assert api["live"]["on"] is True and api["live"]["hhmmss"] in page

    inbox = client.get("/inbox").text
    assert LIVE_ATTR.search(inbox).group(1) == "1"
    assert 'data-live-region="strips"' in inbox and 'data-live-region="cases"' in inbox
    assert client.get("/api/viz/inbox").json()["live"]["on"] is True
    assert "Undefined" not in page + inbox and "built-in method" not in page + inbox


def test_done_run_is_static(cases_dir, make_client):
    set_status(cases_dir, state="done", updated_at=iso(datetime.now(UTC)))
    client = make_client()
    for url, off in (("/cases/libraries/operation", "not in motion"), ("/inbox", "no run in motion")):
        page = client.get(url).text
        assert LIVE_ATTR.search(page).group(1) == "0", url
        assert "data-live-poll" not in page and "data-live-updated" not in page
        assert re.search(r'<span class="chip st-done" title="[^"]*not refreshing">' + off + "</span>", page), url
    lv = client.get("/cases/libraries/api/viz/operation").json()["live"]
    assert lv["on"] is False and lv["poll_ms"] is None and lv["hhmmss"]


def test_stale_paused_fixture_and_empty_case_are_static(client):
    assert LIVE_ATTR.search(client.get("/cases/libraries/operation").text).group(1) == "0"  # paused, updated long ago
    parks = client.get("/cases/parks/operation")
    assert parks.status_code == 200 and "data-live-root" not in parks.text  # no run, no indicator
    assert client.get("/cases/parks/api/viz/operation").json()["live"]["on"] is False


# browser: regions update in place while running; a done run makes no requests ---------------------------------------
@pytest.fixture
def server(cases_dir):
    app = create_app(
        settings_from_env({"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir), "ONTOFILL_CONSOLE_IDENTITY": "local"})
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=srv.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)
    sock.close()


def p5_steps(page) -> int:
    text = page.locator('[data-live-region="pipe"] li[data-k="phase-5"]').inner_text()
    return int(re.search(r"(\d+) steps?", text).group(1))


def updated(page) -> str:
    return page.locator("[data-live-updated]").inner_text()


@pytest.mark.ui
def test_operation_and_inbox_stream_in_place(server, cases_dir):
    playwright = pytest.importorskip("playwright.sync_api")
    t0 = datetime.now(UTC) - timedelta(seconds=20)
    set_status(cases_dir, state="running", updated_at=iso(t0))
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # Operation: new phase-5 steps and a bumped status.json show up without a reload.
        page.goto(f"{server}/cases/libraries/operation")
        page.evaluate("window.__noReload = 1")
        before_steps, before_time = p5_steps(page), updated(page)
        assert "live" in page.locator(".live-ind .chip.st-run").inner_text()
        run_link = f"/cases/libraries/operation?run={RUN}"
        page.focus(f'[data-live-region="runs"] a[href="{run_link}"]')
        page.evaluate("document.querySelector('[data-live-region=\"runs\"]').dataset.old = '1'")
        append_steps(cases_dir, 3, 0, t0 + timedelta(seconds=5))
        set_status(cases_dir, state="running", updated_at=iso(t0 + timedelta(seconds=10)))
        page.wait_for_function(
            f"""() => {{ const li = document.querySelector('[data-live-region="pipe"] li[data-k="phase-5"]');
                        return li && !li.textContent.includes('{before_steps} steps'); }}""",
            timeout=8000,
        )
        assert p5_steps(page) == before_steps + 3
        page.wait_for_function(
            f"() => document.querySelector('[data-live-updated]').textContent.trim() !== {json.dumps(before_time.strip())}",
            timeout=8000,
        )
        assert page.evaluate("window.__noReload") == 1
        assert updated(page) != before_time
        # The runs region was swapped (its age ticked) and focus stayed on the same link.
        assert page.evaluate("document.querySelector('[data-live-region=\"runs\"]').dataset.old") is None
        assert page.evaluate("document.activeElement.getAttribute('href')") == run_link

        # Inbox: a new pending action checkpoint and a phase change arrive in place; the masthead count follows.
        page.goto(f"{server}/inbox")
        page.evaluate("window.__noReload = 1")
        before_time = updated(page)
        count_before = page.locator('.masthead a[href="/inbox"] .count').inner_text()
        pending = cases_dir / "libraries" / "case" / "05-actions" / "req-0099"
        pending.mkdir(parents=True)
        (pending / "APPROVAL_PENDING.md").write_text(
            "---\nphase: 5\ncheckpoint: action\nrequested_at: '" + iso(datetime.now(UTC)) + "'\n"
            "intended_action: submit the live-test search\nreason: live streaming test\n---\n# Approval pending\n"
        )
        append_steps(cases_dir, 1, 10, t0 + timedelta(seconds=15))
        set_status(cases_dir, state="running", updated_at=iso(t0 + timedelta(seconds=16)))
        page.wait_for_selector("text=submit the live-test search", timeout=8000)
        page.wait_for_function(
            f"() => document.querySelector('[data-live-updated]').textContent.trim() !== {json.dumps(before_time.strip())}",
            timeout=8000,
        )
        assert page.evaluate("window.__noReload") == 1
        assert int(page.locator('.masthead a[href="/inbox"] .count').inner_text()) == int(count_before) + 1

        # When the run finishes, the next poll swaps in the static "recorded"-style chip and polling stops.
        set_status(cases_dir, state="done", updated_at=iso(t0 + timedelta(seconds=20)))
        page.wait_for_selector('[data-live-root][data-live="0"]', timeout=8000)
        assert page.evaluate("window.__noReload") == 1

        # The run page (P5 step stream, static/app.js polling /api/runs) streams too.
        set_status(cases_dir, state="running", updated_at=iso(t0 + timedelta(seconds=25)))
        page.goto(f"{server}/cases/libraries/runs/{RUN}")
        page.wait_for_selector("#steps li")
        page.evaluate("window.__noReload = 1")
        append_steps(cases_dir, 1, 20, t0 + timedelta(seconds=26))
        page.wait_for_selector("#steps li.step--new", timeout=8000)
        assert page.evaluate("window.__noReload") == 1

        assert not errors, errors
        browser.close()


@pytest.mark.ui
def test_done_run_makes_no_polling_requests(server, cases_dir):
    playwright = pytest.importorskip("playwright.sync_api")
    set_status(cases_dir, state="done", updated_at=iso(datetime.now(UTC)))
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for path in ("/cases/libraries/operation", "/inbox"):
            hits = []
            page.on(
                "request",
                lambda r, hits=hits, path=path: hits.append(r.url) if r.url.split("?")[0].endswith(path) else None,
            )
            page.goto(server + path)
            page.wait_for_timeout(5000)
            assert len(hits) == 1, (path, hits)  # the navigation itself, nothing after
            assert page.locator('[data-live-root][data-live="0"]').count() == 1
        browser.close()


@pytest.mark.ui
def test_live_pages_fit_phone_width(server, cases_dir):
    playwright = pytest.importorskip("playwright.sync_api")
    set_status(cases_dir, state="running", updated_at=iso(datetime.now(UTC)))
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for width in (1280, 390):
            page.set_viewport_size({"width": width, "height": 900})
            for url in ("/cases/libraries/operation", "/inbox"):
                page.goto(server + url)
                assert page.locator(".live-ind .chip.st-run").count() == 1
                assert page.evaluate("document.documentElement.scrollWidth") <= width, (url, width)
        browser.close()
