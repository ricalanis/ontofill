"""Live view (architecture §8): a read-only screencast per session over a second CDP client, token-gated, gone when
the session closes, and never written into trace steps."""

from __future__ import annotations

import re
import threading
import time

import httpx
import pytest
from test_controller_support import PNG, FakeAdmin, FakeBackend, FakeGateway, Site

from controller import server
from controller.liveview import LiveViewHub

URL_RE = re.compile(r"^http://127\.0\.0\.1:(\d+)/live/(bas-[0-9a-f]+)\?t=([A-Za-z0-9_-]{20,})$")


def _broker(tmp_path, hub, plan, backend_factory=None):
    kw = {"backend_factory": backend_factory} if backend_factory else {}
    return server.Broker(admin=FakeAdmin(), gateway_factory=lambda token: FakeGateway(list(plan)),
                         steps_dir=tmp_path / "steps", captures_dir=tmp_path / "lake", case_dir=tmp_path / "case",
                         pool=False, liveview=hub, **kw)


def _first_part(url: str, timeout: float = 10.0) -> tuple[str, bytes]:
    """Read one multipart part from the MJPEG stream: (its content type, its bytes)."""
    with httpx.stream("GET", url, timeout=timeout) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("multipart/x-mixed-replace; boundary=")
        buf = b""
        for chunk in r.iter_bytes():
            buf += chunk
            m = re.search(rb"Content-Type: ([^\r]+)\r\nContent-Length: (\d+)\r\n\r\n", buf)
            if m and len(buf) >= m.end() + int(m.group(2)):
                return m.group(1).decode(), buf[m.end():m.end() + int(m.group(2))]
    raise AssertionError("stream ended before a frame")


@pytest.fixture(scope="module")
def site():
    with Site() as s:
        yield s


@pytest.mark.browser
def test_live_screencast_follows_the_session_and_dies_with_it(tmp_path, site):
    hub = LiveViewHub(port=0)  # ephemeral loopback port
    plan = [("navigate", {"url": site.url("search.html"), "expectation": "the search page"}),
            ("navigate", {"url": site.url("detail.html"), "expectation": "the detail page"}),
            ("extract", {"fields": {"legal_name": "#legal-name"}}),
            ("done", {"status": "achieved", "summary": "read"})]
    b = _broker(tmp_path, hub, plan)
    try:
        opened = b.open({"run_id": "run-live"}, ["127.0.0.1"], {"max_steps": 12})
        sid, url = opened["session_id"], opened["live_view_url"]
        m = URL_RE.match(url or "")
        assert m and m.group(2) == sid and int(m.group(1)) == hub.port
        token = m.group(3)
        base = f"http://127.0.0.1:{hub.port}/live/{sid}"
        # the session's own Playwright client and the viewer's CDP client work at the same time
        res = b.act(sid, goal="read the entity's legal name")
        assert res["status"] == "achieved", res
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            r = httpx.get(f"{base}/frame.jpg", params={"t": token})
            if r.status_code == 200:
                break
            time.sleep(0.1)
        assert r.status_code == 200 and r.content[:2] == b"\xff\xd8" and r.headers["content-type"] == "image/jpeg"
        source = hub.sessions[sid][1]
        assert source.mode == "live" and source.error is None
        # a visible change on the session's page reaches the viewer as a new screencast frame
        session = b.sessions[sid]
        deadline = time.monotonic() + 5
        while source.frames < 2 and time.monotonic() < deadline:
            session.call(lambda: session.backend.page.evaluate("document.body.style.background = '#123456'"))
            time.sleep(0.2)
            session.call(lambda: session.backend.page.evaluate("document.body.style.background = '#654321'"))
            time.sleep(0.2)
        assert source.frames >= 2, "only the seed frame: the screencast is not following the session's page"
        assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"
        assert "x-frame-options" not in r.headers  # the investigation app may embed the view
        ctype, frame = _first_part(f"{base}/stream?t={token}")
        assert ctype == "image/jpeg" and frame[:2] == b"\xff\xd8"
        page = httpx.get(base, params={"t": token})
        assert page.status_code == 200 and "read-only · ends when the session closes" in page.text
        assert "not live" not in page.text
        # wrong or missing token: indistinguishable from an unknown session
        for params in ({"t": "nope"}, {}):
            assert httpx.get(f"{base}/frame.jpg", params=params).status_code == 404
        assert httpx.get(f"http://127.0.0.1:{hub.port}/live/bas-unknown/frame.jpg",
                         params={"t": token}).status_code == 404

        # an open stream ends when the session closes
        ended = threading.Event()

        def watch():
            try:
                with httpx.stream("GET", f"{base}/stream", params={"t": token}, timeout=15) as s:
                    for _ in s.iter_bytes():
                        pass
            except httpx.HTTPError:
                pass
            ended.set()

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        time.sleep(0.5)
        t0 = time.monotonic()
        b.close(sid)
        assert ended.wait(5), "stream still open after the session closed"
        assert time.monotonic() - t0 < 5
        assert httpx.get(f"{base}/frame.jpg", params={"t": token}).status_code == 404
        steps_text = (tmp_path / "steps" / f"{sid}.jsonl").read_text()
        assert token not in steps_text and "/live/" not in steps_text  # the view token never reaches the lake
    finally:
        b.close_all()
        hub.shutdown()


def test_capture_fallback_without_a_cdp_endpoint(tmp_path):
    hub = LiveViewHub(port=0)
    b = _broker(tmp_path, hub, [("done", {"status": "achieved", "summary": "ok"})],
                backend_factory=lambda cdp: FakeBackend())
    try:
        opened = b.open({"run_id": "run-cap"}, ["registry.example"], {"max_steps": 5})
        sid, token = opened["session_id"], URL_RE.match(opened["live_view_url"]).group(3)
        base = f"http://127.0.0.1:{hub.port}/live/{sid}"
        b.act(sid, goal="look at the page")
        r = httpx.get(f"{base}/frame.jpg", params={"t": token})
        assert r.status_code == 200 and r.content.startswith(PNG) and r.headers["content-type"] == "image/png"
        assert "latest capture, not live" in httpx.get(base, params={"t": token}).text
        b.close(sid)
        assert httpx.get(base, params={"t": token}).status_code == 404
    finally:
        b.close_all()
        hub.shutdown()


def test_disabled_hub_and_public_base(tmp_path, monkeypatch):
    monkeypatch.setenv("BA_LIVEVIEW_PORT", "0")
    assert LiveViewHub.from_env() is None
    b = _broker(tmp_path, None, [("done", {"status": "achieved"})], backend_factory=lambda cdp: FakeBackend())
    try:
        assert b.liveview is None
        assert b.open({"run_id": "r"}, ["registry.example"], {})["live_view_url"] is None
    finally:
        b.close_all()
    hub = LiveViewHub(port=0, public_base="https://live.example.netbird.services/")
    try:
        url, source = hub.register("bas-0001", None)
        assert url.startswith("https://live.example.netbird.services/live/bas-0001?t=")
        assert hub.lookup("bas-0001", url.split("t=")[1]) is source
        assert hub.lookup("bas-0001", "x" * 22) is None
        hub.unregister("bas-0001")
        assert source.stopped and hub.lookup("bas-0001", url.split("t=")[1]) is None
    finally:
        hub.shutdown()
