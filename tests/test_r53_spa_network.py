"""Sandbox browser network metadata is a bounded discovery lead, never evidence."""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

from ontofill.lake import FileLake
from ontofill.sandbox import capture_url
from ontofill.sandbox.capture import _validated_network_requests
from tests.test_agent_pod_capture import _import_capture_module
from tests.test_sandbox import _mock_capture_runtime


class _Request:
    def __init__(self, url: str, *, method: str = "GET", resource_type: str = "xhr") -> None:
        self.url = url
        self.method = method
        self.resource_type = resource_type
        self.frame = None

    def is_navigation_request(self) -> bool:
        return False


class _Response:
    def __init__(
        self, request: _Request, status: int = 200, content_type: str = "application/json"
    ) -> None:
        self.request = request
        self.status = status
        self.headers = {"content-type": content_type}


def test_spa_get_absent_from_dom_and_blocked_off_host_attempt_are_leads(monkeypatch) -> None:
    pod = _import_capture_module(monkeypatch)
    recorder = pod.NetworkRequestRecorder()
    spa = _Request("https://portal.example.test/api/records.json")
    blocked = _Request(
        "https://files.other.example.test/exports/records.csv", resource_type="fetch"
    )
    recorder.observe_request(spa)
    recorder.observe_response(_Response(spa))
    recorder.observe_request(blocked)  # The proxy blocks it before any response.
    recorder.observe_request(_Request("https://portal.example.test/write", method="POST"))
    assert recorder.records == [
        {
            "url": spa.url,
            "method": "GET",
            "resource_type": "xhr",
            "status": 200,
            "content_type": "application/json",
        },
        {"url": blocked.url, "method": "GET", "resource_type": "fetch"},
    ]
    assert all(
        set(record) <= {"url", "method", "resource_type", "status", "content_type"}
        for record in recorder.records
    )


def test_pod_capture_emits_spa_requests_not_present_in_dom(tmp_path, monkeypatch) -> None:
    pod = _import_capture_module(monkeypatch)
    target = "https://portal.example.test/open-data"
    spa = "https://portal.example.test/api/records.json"
    blocked = "https://files.other.example.test/records.csv"

    class Page:
        main_frame = object()
        url = target

        def __init__(self) -> None:
            self.handlers = {}

        def on(self, event, handler) -> None:
            self.handlers[event] = handler

        async def goto(self, url, **kwargs):
            main = _Request(url, resource_type="document")
            self.handlers["request"](main)
            self.handlers["request"](_Request(spa))
            self.handlers["response"](_Response(_Request(spa)))
            self.handlers["request"](_Request(blocked, resource_type="fetch"))
            return _Response(main)

        async def wait_for_load_state(self, state, *, timeout):
            return None

        async def content(self):
            return "<html><body>Open data portal</body></html>"

        def locator(self, selector):
            return self

        async def aria_snapshot(self):
            return "Open data portal"

        async def title(self):
            return "Open data portal"

        async def screenshot(self, **kwargs):
            return b"synthetic screenshot"

    page = Page()

    class Context:
        async def route(self, pattern, handler):
            self.route_handler = handler

        async def new_page(self):
            return page

    class Browser:
        async def new_context(self, **kwargs):
            return Context()

        async def close(self):
            return None

    class Playwright:
        class chromium:
            @staticmethod
            async def launch(**kwargs):
                return Browser()

    class PlaywrightContext:
        async def __aenter__(self):
            return Playwright()

        async def __aexit__(self, *args):
            return None

    original_path = Path
    monkeypatch.setattr(
        pod, "Path", lambda value: tmp_path if value == "/out" else original_path(value)
    )
    monkeypatch.setattr(pod, "async_playwright", lambda: PlaywrightContext())
    monkeypatch.setattr(pod, "pod_identity", lambda: {"hostname": "synthetic-pod"})
    monkeypatch.setattr(pod, "isolation_probes", lambda proxy: {})
    monkeypatch.setattr(
        pod,
        "secret_probes",
        lambda: {
            "env_keys_found": 0,
            "files_with_keys": 0,
            "metadata_ip": "BLOCKED",
            "mesh": "BLOCKED",
        },
    )
    monkeypatch.setenv("CAPTURE_URL", target)
    monkeypatch.setenv("PROXY_URL", "http://egress:8888")
    monkeypatch.setenv("CAPTURE_MAX_STEPS", "4")
    asyncio.run(pod.capture())
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["network_requests"] == [
        {
            "url": spa,
            "method": "GET",
            "resource_type": "xhr",
            "status": 200,
            "content_type": "application/json",
        },
        {"url": blocked, "method": "GET", "resource_type": "fetch"},
    ]
    assert spa not in (tmp_path / "page.html").read_text(encoding="utf-8")


def test_pod_drops_credential_queries_private_hosts_and_bounds_output(monkeypatch) -> None:
    pod = _import_capture_module(monkeypatch)
    recorder = pod.NetworkRequestRecorder()
    for url in (
        "https://files.example.test/data.csv?sig=synthetic-secret",
        "https://files.example.test/data.csv?file=eyJhbGciOiJIUzI1NiJ9.abc.def",
        "http://169.254.169.254/latest/meta-data",
        "http://0177.0.0.1/private",
        "http://localhost/private",
        "https://user:pass@files.example.test/data.csv",
    ):
        recorder.observe_request(_Request(url))
    for index in range(60):
        recorder.observe_request(_Request(f"https://files.example.test/data/{index}.csv"))
    assert len(recorder.records) == 40
    serialized = json.dumps(recorder.records)
    assert "synthetic-secret" not in serialized
    assert "169.254" not in serialized
    assert "localhost" not in serialized


def test_host_revalidates_pod_metadata_and_exposes_only_safe_leads(tmp_path, monkeypatch) -> None:
    target = "https://portal.example.test/open-data"
    module = importlib.import_module("ontofill.sandbox.capture")
    _mock_capture_runtime(monkeypatch, target=target, final_url=target)
    base_agent = module._run_agent_pod

    def agent_with_requests(name, output, *args, limits=None):
        completed = base_agent(name, output, *args, limits=limits)
        path = output / "result.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        result["network_requests"] = [
            {
                "url": "https://portal.example.test/api/records.json",
                "method": "GET",
                "resource_type": "xhr",
                "status": 200,
                "content_type": "application/json",
                "body": "must not survive",
            },
            {
                "url": "https://files.other.example.test/records.csv",
                "method": "GET",
                "resource_type": "fetch",
            },
            {
                "url": "https://files.other.example.test/records.csv?signature=synthetic-secret",
                "method": "GET",
                "resource_type": "fetch",
            },
            {"url": "http://127.0.0.1/private", "method": "GET", "resource_type": "xhr"},
            {"url": "http://0177.0.0.1/private", "method": "GET", "resource_type": "xhr"},
        ]
        path.write_text(json.dumps(result), encoding="utf-8")
        return completed

    monkeypatch.setattr(module, "_run_agent_pod", agent_with_requests)
    captured = capture_url(
        target,
        allowed_domains=["portal.example.test"],
        lake=FileLake(tmp_path / "lake"),
        run_id="mock-r53-spa",
        source_id="source-spa",
        objective_id=None,
        tdd_path="03-fanout/discovery-loop.json",
        phase=3,
    )
    assert captured["network_requests"] == [
        {
            "url": "https://portal.example.test/api/records.json",
            "method": "GET",
            "resource_type": "xhr",
            "status": 200,
            "content_type": "application/json",
        },
        {
            "url": "https://files.other.example.test/records.csv",
            "method": "GET",
            "resource_type": "fetch",
        },
    ]
    assert "network_requests" not in captured["trace"][0]["executed"]
    assert "synthetic-secret" not in json.dumps(captured)


def test_host_bounds_and_rejects_non_get_or_bad_status() -> None:
    items = [
        {
            "url": f"https://data.example.test/{index}.csv",
            "method": "GET",
            "resource_type": "download",
        }
        for index in range(60)
    ]
    items.extend(
        [
            {
                "url": "https://data.example.test/write.csv",
                "method": "POST",
                "resource_type": "xhr",
            },
            {
                "url": "https://data.example.test/missing.csv",
                "method": "GET",
                "resource_type": "xhr",
                "status": 404,
            },
        ]
    )
    assert len(_validated_network_requests(items)) == 40
