"""Cell pool semantics (fake provider), the docker stub's command construction (mocked CLI), and the broker's
lease/release wiring. No Docker, no browser."""

from __future__ import annotations

import subprocess
import threading

import pytest
from test_controller_support import FakeAdmin, FakeBackend, FakeGateway, check_step, read_steps

from cells.docker_stub import DockerStubProvider
from controller import cells, server
from controller.cells import Cell, CellError, CellPool


class FakeProvider:
    def __init__(self, fail: bool = False):
        self.created: list[dict] = []
        self.destroyed: list[str] = []
        self.fail = fail
        self._n = 0
        self._lock = threading.Lock()

    def create(self, backend, allowed_domains, limits=None, placement="sandbox_vm"):
        if self.fail:
            raise RuntimeError("substrate down")
        with self._lock:
            self._n += 1
            cid = f"cell-{self._n}"
        self.created.append({"cell_id": cid, "backend": backend, "domains": list(allowed_domains),
                             "limits": dict(limits or {}), "placement": placement})
        # the contract's dict shape
        return {"cell_id": cid, "cdp_url": f"http://127.0.0.1:{9000 + self._n}", "brain_url": None,
                "live_view_port": None, "isolation": {"runtime": "runsc", "tier": 3}}

    def destroy(self, cell_id):
        self.destroyed.append(cell_id)

    def status(self, cell_id):
        return {"cell_id": cell_id, "state": "missing" if cell_id in self.destroyed else "running"}


def test_lease_creates_then_warm_pool_serves_matching_domains():
    p = FakeProvider()
    pool = CellPool(p, k_native=1)
    first = pool.lease("native", ["registry.example"], {"memory_mb": 512})
    assert isinstance(first, Cell) and first.cdp_url and first.isolation["tier"] == 3
    assert pool.timings(first.cell_id)["warm"] is False and pool.timings(first.cell_id)["lease_ms"] >= 0
    pool.wait()
    assert len(p.created) == 2  # one leased + one warm for the same domain set
    second = pool.lease("native", ["REGISTRY.example"], {"memory_mb": 512})  # same set, normalized
    assert pool.timings(second.cell_id)["warm"] is True
    assert second.cell_id == p.created[1]["cell_id"]
    assert pool.timings(second.cell_id)["create_ms"] is not None
    pool.shutdown()


def test_different_domains_get_a_fresh_cell_and_stale_warm_cells_are_recycled():
    p = FakeProvider()
    pool = CellPool(p, k_native=1)
    pool.lease("native", ["a.example"])
    pool.wait()
    warm_for_a = p.created[1]["cell_id"]
    other = pool.lease("native", ["b.example"])
    assert pool.timings(other.cell_id)["warm"] is False and p.created[2]["domains"] == ["b.example"]
    pool.wait()
    assert warm_for_a in p.destroyed  # made for the old domain set: destroyed, not handed out
    assert p.created[-1]["domains"] == ["b.example"]
    pool.shutdown()


def test_release_destroys_replenishes_and_never_reuses():
    p = FakeProvider()
    pool = CellPool(p, k_native=1)
    cell = pool.lease("native", ["a.example"])
    pool.wait()
    out = pool.release(cell)
    assert out["released"] and cell.cell_id in p.destroyed and out["destroy_ms"] >= 0
    assert pool.release(cell)["released"] is False  # a released cell is gone for good
    pool.wait()
    leased = {cell.cell_id}
    for _ in range(3):
        c = pool.lease("native", ["a.example"])
        assert c.cell_id not in leased and c.cell_id not in p.destroyed
        leased.add(c.cell_id)
        pool.release(c)
        pool.wait()
    assert len(set(p.destroyed)) == len(p.destroyed)
    pool.shutdown()


def test_shutdown_destroys_warm_and_leased():
    p = FakeProvider()
    pool = CellPool(p, k_native=2)
    held = pool.lease("native", ["a.example"])
    pool.wait()
    pool.shutdown()
    assert set(p.destroyed) == {c["cell_id"] for c in p.created} and held.cell_id in p.destroyed
    with pytest.raises(CellError):
        pool.lease("native", ["a.example"])


def test_provider_failure_is_a_cell_error_and_k_zero_keeps_nothing_warm():
    with pytest.raises(CellError, match="substrate down"):
        CellPool(FakeProvider(fail=True)).lease("native", ["a.example"])
    p = FakeProvider()
    pool = CellPool(p, k_native=0, background=False)
    pool.lease("native", ["a.example"])
    assert len(p.created) == 1
    pool.shutdown()


def test_provider_from_env(monkeypatch):
    monkeypatch.delenv(cells.PROVIDER_ENV, raising=False)
    assert cells.provider_from_env() is None and cells.pool_from_env() is None
    assert isinstance(cells.provider_from_env("docker-stub"), DockerStubProvider)
    with pytest.raises(CellError, match="ontofill.cells"):
        cells.provider_from_env("ontofill")
    with pytest.raises(CellError, match="unknown"):
        cells.provider_from_env("k8s")


# --- docker stub: command construction with a mocked CLI ------------------------------------------------------
class FakeDocker:
    def __init__(self, port="127.0.0.1:55001"):
        self.calls: list[list[str]] = []
        self.port = port

    def __call__(self, args, capture_output=True, text=True, check=True):
        self.calls.append(list(args))
        out = ""
        if args[1] == "port":
            out = self.port + "\n"
        elif args[1] == "ps":
            out = "abc123\n"
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")


def test_stub_hands_command_caps_loopback_publish_labels_and_no_env():
    stub = DockerStubProvider(runner=FakeDocker())
    cmd = stub.hands_cmd("cell-x", {"memory_mb": 768, "cpus": 0.5, "pids": 256, "budget_usd": 1})
    joined = " ".join(cmd)
    assert "--memory 768m" in joined and "--cpus 0.5" in joined and "--pids-limit 256" in joined
    assert "-p 127.0.0.1::9222" in joined and "0.0.0.0" not in joined
    assert "--label ba.cell=cell-x" in joined and "--network ba-cell-x" in joined
    assert "-e" not in cmd and "--env" not in cmd and "--env-file" not in cmd
    for word in ("VULTR", "JEV", "TOKEN", "KEY"):
        assert word not in joined.upper().replace("SKYVERN", "")
    assert stub.hands_cmd("cell-y")[stub.hands_cmd("cell-y").index("--memory") + 1] == "1024m"  # defaults


def test_stub_create_uses_published_port_and_reports_stub_isolation(monkeypatch):
    docker = FakeDocker()
    stub = DockerStubProvider(runner=docker)
    monkeypatch.setattr(stub, "_wait_ready", lambda url, name: None)
    cell = stub.create("native", ["web"], {})
    assert cell.cdp_url == "http://127.0.0.1:55001" and cell.brain_url is None
    assert cell.isolation["runtime"] == "runc" and cell.isolation["tier"] == 2
    assert "not enforced" in cell.isolation["egress"]
    assert [c[1:3] for c in docker.calls[:2]] == [["network", "create"], ["run", "-d"]]
    sky = stub.create("skyvern", ["web"], {})
    assert sky.brain_url is None and "NotImplemented" in sky.meta["note"]
    with pytest.raises(NotImplementedError):
        stub.create("native", ["web"], {}, placement="throwaway_vx1")


def test_stub_create_failure_cleans_up(monkeypatch):
    docker = FakeDocker(port="0.0.0.0:55001")  # not loopback: refused
    stub = DockerStubProvider(runner=docker)
    with pytest.raises(CellError, match="127.0.0.1"):
        stub.create("native", ["web"], {})
    assert any(c[1:3] == ["rm", "-f"] for c in docker.calls)
    assert any(c[1:3] == ["network", "rm"] for c in docker.calls)


# --- broker wiring --------------------------------------------------------------------------------------------
@pytest.fixture
def broker_with_pool(tmp_path):
    p = FakeProvider()
    pool = CellPool(p, k_native=1)
    cdps: list = []

    def backend_factory(cdp):
        cdps.append(cdp)
        return FakeBackend()

    b = server.Broker(admin=FakeAdmin(), gateway_factory=lambda token: FakeGateway([("done", {"status": "achieved"})]),
                      backend_factory=backend_factory, steps_dir=tmp_path / "steps", captures_dir=tmp_path / "lake",
                      case_dir=tmp_path / "case", pool=pool)
    b.provider, b.cdps = p, cdps
    yield b
    b.close_all()


def test_session_leases_a_cell_and_close_recycles_it(broker_with_pool, tmp_path):
    b = broker_with_pool
    opened = b.open({"run_id": "run-c", "source_id": "registry-example"}, ["registry.example"], {"max_steps": 5})
    sid, cell_id = opened["session_id"], opened["cell_id"]
    assert opened["isolation"] == {"runtime": "runsc", "tier": 3}
    assert b.cdps == ["http://127.0.0.1:9001"]  # the backend drives the cell's browser, not a host one
    b.act(sid, goal="anything")
    closed = b.close(sid)
    assert closed["cell"]["released"] and cell_id in b.provider.destroyed
    assert set(closed["metrics"]["cell"]) >= {"create_ms", "lease_ms", "destroy_ms", "warm"}
    steps = read_steps(tmp_path / "steps" / f"{sid}.jsonl")
    for s in steps:
        check_step(s)
    first = steps[0]["executed"]["cell"]
    assert first["cell_id"] == cell_id and first["isolation"]["tier"] == 3
    assert all("cell" not in s["executed"] for s in steps[1:] if isinstance(s["executed"], dict))


def test_lease_failure_is_a_clear_error_and_revokes_the_token(tmp_path):
    admin = FakeAdmin()
    b = server.Broker(admin=admin, gateway_factory=lambda t: FakeGateway(), backend_factory=lambda c: FakeBackend(),
                      steps_dir=tmp_path / "s", captures_dir=tmp_path / "l", pool=CellPool(FakeProvider(fail=True)))
    with pytest.raises(RuntimeError, match="could not lease a cell"):
        b.open({}, ["a.example"])
    assert len(admin.revoked) == 1 and not b.sessions
    b.close_all()
