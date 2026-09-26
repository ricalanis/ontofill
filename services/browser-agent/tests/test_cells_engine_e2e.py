"""The controller on the engine's real cell substrate (run with -m docker, and BA_CELLS_URL + BA_CELLS_TOKEN pointing
at a running ontofill `serve_cells`): lease a gVisor/runc hands pod behind the allowlist proxy, drive it over CDP
against a public read-only page, a guard-denied off-allowlist navigation, the task result reported, teardown with the
six-checkpoint job record."""

from __future__ import annotations

import json
import os
import time

import pytest
from test_controller_support import FakeAdmin, FakeGateway, check_step, read_steps

from cells.ontofill_http import OntofillHttpProvider
from controller import server
from controller.cells import CellPool

pytestmark = [pytest.mark.docker, pytest.mark.browser,
              pytest.mark.skipif(not os.environ.get("BA_CELLS_TOKEN"), reason="needs a running engine cell API")]


def test_engine_cell_end_to_end(tmp_path):
    pool = CellPool(OntofillHttpProvider(), k_native=0, background=False)
    plan = [("navigate", {"url": "https://example.com/", "expectation": "the example page"}),
            ("navigate", {"url": "https://evil.invalid/exfil", "expectation": "blocked"}),
            ("extract", {"fields": {"heading": "h1"}}),
            ("done", {"status": "achieved", "summary": "captured the heading"})]
    b = server.Broker(admin=FakeAdmin(), gateway_factory=lambda token: FakeGateway(list(plan)),
                      steps_dir=tmp_path / "steps", captures_dir=tmp_path / "lake", case_dir=tmp_path / "case",
                      pool=pool)
    t0 = time.monotonic()
    opened = b.open({"run_id": "run-engine-cell"}, ["example.com"], {"max_steps": 12, "timeout_s": 120})
    open_s = time.monotonic() - t0
    try:
        assert opened["cell_id"].startswith("cell:")
        res = b.act(opened["session_id"], goal="read the example page's heading")
        assert res["status"] == "achieved", res
        assert "Example Domain" in json.dumps(res.get("extracted") or {})
    finally:
        closed = b.close(opened["session_id"])
    steps = read_steps(tmp_path / "steps" / f"{opened['session_id']}.jsonl")
    for s in steps:
        check_step(s)
    assert steps[0]["executed"]["cell"]["cell_id"] == opened["cell_id"]
    gates = [s for s in steps if s.get("event") == "action_gate"]
    assert any(g["gate"]["outcome"] == "denied" and "evil.invalid" in g["gate"]["action"] for g in gates)
    teardown = closed["cell"].get("teardown") or {}
    assert teardown.get("state") == "destroyed"
    record = teardown.get("job_record") or {}
    print("\nENGINE CELL", json.dumps({"open_s": round(open_s, 2), "timings": closed["metrics"].get("cell"),
                                       "checkpoints": {k: (v or {}).get("ok") for k, v in
                                                       (record.get("checkpoints") or {}).items()}}))
    assert record, "the substrate returned no job record"
