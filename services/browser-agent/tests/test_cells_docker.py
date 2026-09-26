"""A real docker-stub cell end to end (run with -m docker): lease → the native loop drives the cell's Chromium over
CDP against fixture pages served on the cell's own network → release → nothing left behind. Prints timings."""

from __future__ import annotations

import json
import subprocess
import time

import pytest
from test_controller_support import PAGES, FakeAdmin, FakeGateway, by_name, check_step, read_steps

from cells.docker_stub import IMAGE, OWNER_LABEL, DockerStubProvider
from controller import server
from controller.cells import CellPool

pytestmark = [pytest.mark.docker, pytest.mark.browser]


def _docker(*args: str) -> str:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=True).stdout


def _mem(container: str) -> str:
    return _docker("stats", "--no-stream", "--format", "{{.MemUsage}}", container).strip()


def _serve_pages(cell) -> None:
    """Fixture site inside the cell network as http://web:8080 (the hands cannot see the host's loopback)."""
    _docker("run", "-d", "--name", f"ba-{cell.cell_id}-web", "--label", f"ba.cell={cell.cell_id}", "--label",
            OWNER_LABEL, "--network", cell.meta["network"], "--network-alias", "web", "-v", f"{PAGES}:/site:ro",
            "--entrypoint", "python", IMAGE, "-m", "http.server", "8080", "-d", "/site")
    for _ in range(80):
        probe = subprocess.run(["docker", "exec", cell.meta["hands"], "python3", "-c",
                                "import urllib.request;urllib.request.urlopen('http://web:8080/search.html',timeout=1)"],
                               capture_output=True, check=False)
        if probe.returncode == 0:
            return
        time.sleep(0.25)
    raise AssertionError("fixture web container not reachable from the hands")


def test_stub_cell_end_to_end(tmp_path):
    """The cell path: lease, drive the cell's Chromium over CDP (navigate, type, extract, a blocked host), note the
    cell on the first step, release, a warm lease for the same domains, and nothing left behind."""
    stub = DockerStubProvider()
    stub.sweep()
    pool = CellPool(stub, k_native=1)
    plan = [("navigate", {"url": "http://evil.invalid/exfil", "expectation": "blocked"}),
            ("navigate", {"url": "http://web:8080/search.html", "expectation": "the search page"}),
            by_name("type", "Nombre de la entidad", text="Entidad Ejemplo", expectation="query typed"),
            ("navigate", {"url": "http://web:8080/detail.html", "expectation": "the entity's detail page"}),
            ("extract", {"fields": {"legal_name": "#legal-name", "tax_id": "#tax-id"}}),
            ("done", {"status": "achieved", "summary": "captured"})]
    b = server.Broker(admin=FakeAdmin(), gateway_factory=lambda token: FakeGateway(list(plan)),
                      steps_dir=tmp_path / "steps", captures_dir=tmp_path / "lake", case_dir=tmp_path / "case",
                      pool=pool)
    report: dict = {}
    try:
        opened = b.open({"run_id": "run-docker"}, ["web"], {"max_steps": 12})
        sid, cell = opened["session_id"], b.cells[opened["session_id"]]
        report["cold"] = pool.timings(cell.cell_id)
        assert opened["isolation"]["runtime"] == "runc" and opened["isolation"]["tier"] == 2
        _serve_pages(cell)
        report["mem_idle"] = _mem(cell.meta["hands"])
        result = b.act(sid, goal="Find Entidad Ejemplo 01 and record its legal name and tax id")
        report["mem_during"] = _mem(cell.meta["hands"])
        assert result["status"] == "achieved", result
        assert result["extracted"]["legal_name"]["value"] == "Entidad Ejemplo 01"
        closed = b.close(sid)
        assert closed["cell"]["released"]
        report["cold"] = pool.timings(cell.cell_id)
        steps = read_steps(tmp_path / "steps" / f"{sid}.jsonl")
        for s in steps:
            check_step(s)
        assert steps[0]["executed"]["cell"]["cell_id"] == cell.cell_id
        denied = [x for x in steps if x.get("event") == "action_gate" and "evil.invalid" in json.dumps(x["requested"])]
        assert denied and denied[-1]["gate"]["outcome"] == "denied"  # off the allowlist: denied outright
        assert not _docker("ps", "-aq", "--filter", f"label=ba.cell={cell.cell_id}").strip()

        pool.wait(timeout=120)  # a warm replacement for ["web"]
        again = b.open({"run_id": "run-docker"}, ["web"], {"max_steps": 12})
        report["warm"] = pool.timings(again["cell_id"])
        assert report["warm"]["warm"] is True
        b.close(again["session_id"])
    finally:
        b.close_all()  # also shuts the pool down: warm cells destroyed
    left = _docker("ps", "-aq", "--filter", f"label={OWNER_LABEL}").strip()
    nets = _docker("network", "ls", "-q", "--filter", f"label={OWNER_LABEL}").strip()
    report["leftover_containers"], report["leftover_networks"] = len(left.split()), len(nets.split())
    print("\nCELL TIMINGS", json.dumps(report))
    assert not left and not nets


def test_stub_cell_search_submit_then_click(tmp_path):
    stub = DockerStubProvider()
    pool = CellPool(stub, k_native=0, background=False)
    plan = [("navigate", {"url": "http://web:8080/search.html", "expectation": "the search page"}),
            by_name("type", "Nombre de la entidad", text="Entidad Ejemplo", expectation="query typed"),
            by_name("click", "Buscar", expectation="a results list"),
            by_name("click", "Entidad Ejemplo 01", expectation="the entity's detail page"),
            ("extract", {"fields": {"legal_name": "#legal-name"}}),
            ("done", {"status": "achieved", "summary": "captured"})]
    b = server.Broker(admin=FakeAdmin(), gateway_factory=lambda token: FakeGateway(list(plan)),
                      steps_dir=tmp_path / "steps", captures_dir=tmp_path / "lake", pool=pool)
    try:
        opened = b.open({"run_id": "run-docker"}, ["web"], {"max_steps": 12})
        _serve_pages(b.cells[opened["session_id"]])
        result = b.act(opened["session_id"], goal="Find Entidad Ejemplo 01 and record its legal name")
        assert result["extracted"]["legal_name"]["value"] == "Entidad Ejemplo 01"
    finally:
        b.close_all()
        assert not _docker("ps", "-aq", "--filter", f"label={OWNER_LABEL}").strip()
