"""The engine's cell API provider (CONTRACT §13a), against a mocked loopback API."""

import httpx
import pytest
import respx

from cells.ontofill_http import OntofillHttpProvider
from controller.cells import Cell, CellError, CellPool, provider_from_env

BASE = "http://127.0.0.1:8766"
CELL = {"cell_id": "cell:abc", "cdp_url": "ws://127.0.0.1:40001/devtools/browser/x", "brain_url": None,
        "live_view_port": None}


@respx.mock
def test_lifecycle_calls_and_bearer_token():
    create = respx.post(f"{BASE}/cells").mock(return_value=httpx.Response(201, json=CELL))
    steps = respx.post(f"{BASE}/cells/cell:abc/steps").mock(return_value=httpx.Response(200, json={"steps": 3}))
    result = respx.post(f"{BASE}/cells/cell:abc/task-result").mock(return_value=httpx.Response(200, json={"ok": True}))
    status = respx.get(f"{BASE}/cells/cell:abc").mock(return_value=httpx.Response(200, json={"state": "ready"}))
    destroy = respx.delete(f"{BASE}/cells/cell:abc").mock(
        return_value=httpx.Response(200, json={"cell_id": "cell:abc", "state": "destroyed", "job_record": {}}))
    p = OntofillHttpProvider(BASE, token="t0ken-value")
    cell = p.create("native", ["compras.example"], {"max_steps": 5, "timeout_s": 60, "budget_usd": 0.5})
    req = create.calls[0].request
    assert req.headers["Authorization"] == "Bearer t0ken-value"
    import json

    sent = json.loads(req.content)
    # the complete substrate mapping, the session's values winning; no controller-only keys
    assert sent["limits"] == {"memory_mb": 1024, "cpus": 1, "pids": 256, "timeout_s": 60, "max_steps": 5}
    assert sent["backend"] == "native" and sent["placement"] == "sandbox_vm"
    assert cell["cdp_url"].startswith("ws://127.0.0.1") and cell["isolation"]["egress"] == "allowlist proxy"
    assert cell["isolation"]["runtime"] == "runsc" and cell["isolation"]["tier"] == 3
    assert p.record_step("cell:abc") == 3
    p.report_task_result("cell:abc", {"outcomes": []}, ok=False)
    assert result.calls[0].request.content and p.status("cell:abc")["state"] == "ready"
    assert p.destroy("cell:abc")["state"] == "destroyed"
    assert steps.called and status.called and destroy.called
    assert "t0ken" not in repr(p)


@respx.mock
def test_errors_become_cell_errors():
    respx.post(f"{BASE}/cells").mock(return_value=httpx.Response(409, json={"error": "skyvern brains fail closed"}))
    with pytest.raises(CellError, match="409"):
        OntofillHttpProvider(BASE, token="t").create("skyvern", ["x.example"])
    respx.get(f"{BASE}/cells/cell:gone").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(CellError, match="unreachable"):
        OntofillHttpProvider(BASE, token="t").status("cell:gone")


def test_requires_token(monkeypatch):
    monkeypatch.delenv("BA_CELLS_TOKEN", raising=False)
    with pytest.raises(CellError, match="BA_CELLS_TOKEN"):
        OntofillHttpProvider(BASE)


def test_env_selection_and_cold_pool(monkeypatch):
    monkeypatch.setenv("BA_CELLS_TOKEN", "t")
    monkeypatch.delenv("BA_CELL_K_NATIVE", raising=False)
    assert isinstance(provider_from_env("ontofill-http"), OntofillHttpProvider)
    from controller import cells

    monkeypatch.setenv("BA_CELL_PROVIDER", "ontofill-http")
    pool = cells.pool_from_env()
    assert pool.k["native"] == 0  # engine cells age from create: no warm pool by default
    pool.shutdown()


def test_pool_hooks_delegate_and_tolerate_missing():
    calls = []

    class P:
        def create(self, *a, **k):
            return dict(CELL)

        def destroy(self, cell_id):
            calls.append(("destroy", cell_id))

        def status(self, cell_id):
            return {}

        def record_step(self, cell_id):
            calls.append(("step", cell_id))
            return 1

        def report_task_result(self, cell_id, result, ok):
            calls.append(("result", cell_id, ok))

    pool = CellPool(P(), k_native=0, background=False)
    cell = pool.lease("native", ["x.example"])
    assert pool.record_step(cell) == 1
    pool.report_task_result(cell, {}, True)
    pool.release(cell)
    assert calls == [("step", "cell:abc"), ("result", "cell:abc", True), ("destroy", "cell:abc")]

    class Bare(P):
        record_step = None
        report_task_result = None

    bare = CellPool(Bare(), k_native=0, background=False)
    c2 = bare.lease("native", ["x.example"])
    assert bare.record_step(c2) is None and isinstance(c2, Cell)
    bare.report_task_result(c2, {}, True)
    bare.release(c2)
