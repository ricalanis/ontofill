"""Per-session `netbird expose` for the live view (NetBird approach 4): the URL itself dies with the session.

A fake `netbird` stands in for the real CLI: it prints the "Service exposed successfully!" block to stderr (as the
real one does) with a URL pointing at the session's own listener, then waits for SIGTERM and records that it exited.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import sys
import time

import httpx
import pytest
from test_controller_support import FakeAdmin, FakeBackend, FakeGateway

from controller import liveview, server
from controller.liveview import ExposedLiveView

FAKE = r'''#!{python}
import json, os, signal, sys, time
args = sys.argv[1:]
assert args[0] == "expose", args
port = args[1]
marker = args[args.index("--marker") + 1]
json.dump({{"argv": args, "env": sorted(os.environ)}}, open(marker + ".start", "w"))
def bye(*_):
    open(marker + ".exit", "w").write("sigterm")
    sys.exit(0)
signal.signal(signal.SIGTERM, bye)
if "--never" not in args:
    print("Service exposed successfully!", file=sys.stderr)
    print("  Name:     pa-live-test", file=sys.stderr)
    print(f"  URL:      http://127.0.0.1:{{port}}", file=sys.stderr)
    print(f"  Internal: {{port}}", file=sys.stderr)
    print("\nPress Ctrl+C to stop exposing.", file=sys.stderr, flush=True)
while True:
    time.sleep(0.1)
'''


@pytest.fixture
def fake_netbird(tmp_path):
    path = tmp_path / "netbird"
    path.write_text(FAKE.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _view(fake, marker, *extra, timeout_s=10.0):
    return ExposedLiveView(host="127.0.0.1", ports=[0], binary=str(fake), timeout_s=timeout_s,
                           extra_args=["--marker", str(marker), *extra])


def _broker(tmp_path, view):
    return server.Broker(admin=FakeAdmin(), gateway_factory=lambda token: FakeGateway([("done", {"status": "achieved"})]),
                         backend_factory=lambda cdp: FakeBackend(), steps_dir=tmp_path / "steps",
                         captures_dir=tmp_path / "lake", case_dir=tmp_path / "case", pool=False, liveview=view)


def _refused(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _wait_for(path, timeout=6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def test_session_gets_its_own_exposed_url_that_dies_with_it(tmp_path, fake_netbird, monkeypatch):
    monkeypatch.setenv("BA_GATEWAY_ADMIN_TOKEN", "must-not-leak")
    marker = tmp_path / "m1"
    view = _view(fake_netbird, marker)
    b = _broker(tmp_path, view)
    try:
        t0 = time.monotonic()
        opened = b.open({"run_id": "run-expose"}, ["registry.example"], {"max_steps": 5})
        open_s = time.monotonic() - t0
        sid, url = opened["session_id"], opened["live_view_url"]
        exp = view.exposures[sid]
        port = exp.hub.port
        assert url == f"http://127.0.0.1:{port}/live/{sid}?t={url.split('?t=')[1]}"  # the exposed URL, not a base env
        started = json.loads((tmp_path / "m1.start").read_text())
        assert started["argv"][:4] == ["expose", str(port), "--with-name-prefix", "pa-live"]
        assert "BA_GATEWAY_ADMIN_TOKEN" not in started["env"]  # minimal child environment
        assert set(started["env"]) <= {"PATH", "HOME", "LANG", "__CF_USER_TEXT_ENCODING", "PWD", "SHLVL", "_"}
        b.act(sid, goal="look at the page")
        page = httpx.get(url, timeout=5)
        assert page.status_code == 200 and "read-only · ends when the session closes" in page.text
        assert httpx.get(url.split("?")[0], params={"t": "wrong"}, timeout=5).status_code == 404
        assert httpx.get(url.split("?")[0], timeout=5).status_code == 404
        print(f"\nEXPOSE startup {exp.startup_s:.3f}s, session.open {open_s:.3f}s")

        t0 = time.monotonic()
        b.close(sid)
        assert _wait_for(tmp_path / "m1.exit"), "the expose process did not get SIGTERM at close"
        assert time.monotonic() - t0 < 6
        assert exp.proc is None and _refused(port)  # listener closed: the exposed URL has nothing behind it
        with pytest.raises(httpx.HTTPError):
            httpx.get(url, timeout=2)
        assert sid not in view.exposures
    finally:
        b.close_all()


def test_expose_timeout_opens_the_session_without_a_view(tmp_path, fake_netbird):
    marker = tmp_path / "m2"
    view = _view(fake_netbird, marker, "--never", timeout_s=1.0)
    b = _broker(tmp_path, view)
    try:
        t0 = time.monotonic()
        opened = b.open({"run_id": "run-timeout"}, ["registry.example"], {"max_steps": 5})
        assert time.monotonic() - t0 < 5  # never blocks past the timeout (plus teardown)
        assert opened["live_view_url"] is None
        assert "no URL from expose within 1s" in opened["live_view"]["error"]
        assert _wait_for(tmp_path / "m2.exit"), "the stuck expose process was not killed"
        assert b.act(opened["session_id"], goal="still works")["status"] == "achieved"
    finally:
        b.close_all()


def test_missing_binary_is_a_clean_error(tmp_path):
    view = ExposedLiveView(binary=str(tmp_path / "nope"), timeout_s=1.0)
    b = _broker(tmp_path, view)
    try:
        opened = b.open({"run_id": "run-missing"}, ["registry.example"], {})
        assert opened["live_view_url"] is None and "cannot start" in opened["live_view"]["error"]
    finally:
        b.close_all()


def test_close_all_and_atexit_leave_no_expose(tmp_path, fake_netbird):
    view = _view(fake_netbird, tmp_path / "m3")
    b = _broker(tmp_path, view)
    opened = b.open({"run_id": "run-all"}, ["registry.example"], {})
    pid = view.exposures[opened["session_id"]].proc.pid
    b.close_all()
    assert _wait_for(tmp_path / "m3.exit")
    with pytest.raises(ProcessLookupError):
        for _ in range(40):
            os.kill(pid, 0)
            time.sleep(0.05)
    assert view not in ExposedLiveView._live
    liveview._stop_all_exposures()  # idempotent at interpreter exit


def test_env_selection(monkeypatch):
    monkeypatch.delenv("BA_LIVEVIEW_EXPOSE", raising=False)
    monkeypatch.setenv("BA_LIVEVIEW_PORT", "0")
    assert liveview.live_view_from_env() is None  # unchanged: the shared hub, here disabled
    monkeypatch.setenv("BA_LIVEVIEW_EXPOSE", "netbird")
    monkeypatch.setenv("BA_LIVEVIEW_HOST", "100.64.0.10")
    monkeypatch.setenv("BA_LIVEVIEW_PORTS", "8710-8712")
    monkeypatch.setenv("BA_LIVEVIEW_EXPOSE_ARGS", "--with-user-groups approvers")
    monkeypatch.setenv("BA_LIVEVIEW_EXPOSE_TIMEOUT_S", "7")
    v = liveview.live_view_from_env()
    try:
        assert isinstance(v, ExposedLiveView)
        assert (v.host, v.ports, v.extra_args, v.timeout_s, v.binary) == (
            "100.64.0.10", [8710, 8711, 8712], ["--with-user-groups", "approvers"], 7.0, "netbird")
    finally:
        v.shutdown()
