"""R29(c,d): source identity and terminal reasons reach the live status artifact."""

from __future__ import annotations

import json

import pytest

from ontofill.inference import generated_by
from ontofill.lake import FileLake
from ontofill.phases.p3_fanout.authority import source_fingerprint
from ontofill.runfeed import RunFeed
from ontofill.workflow import _status_source_record, _trace_step, run_case
from tests.genericity.fixtures.libraries import library_decisions

RECORDED = {"backend": "recorded", "model": "synthetic-recording", "at": "2026-01-01T00:00:00Z"}


def test_captured_source_name_and_host_are_persisted_in_status(tmp_path) -> None:
    case_dir = tmp_path / "case"
    candidate_dir = case_dir / "03-fanout/sources/source-synthetic"
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "candidate.json").write_text(
        json.dumps(
            {
                "source_id": "source-synthetic",
                "url": "https://registry.synthetic.test/public",
                "landing_url": "https://registry.synthetic.test/public",
                "title": "Synthetic Public Registry",
            }
        ),
        encoding="utf-8",
    )
    objective = {
        "source_id": "source-synthetic",
        "source_url": "https://registry.synthetic.test/public",
        "source_type": "public_registry",
    }
    source = _status_source_record(case_dir, objective, {"ok": 0, "failed": 0, "yield": 0})
    lake = FileLake(tmp_path / "lake")
    feed = RunFeed(lake, "synthetic-case", "mock-r29-source", RECORDED, start_heartbeat=False)
    feed.update_status(state="running", phase=3, sources=[source])
    feed.update_status(state="failed", phase=3)

    status = json.loads(lake.read_key("runs/synthetic-case/mock-r29-source/status.json"))
    assert status["sources"] == [
        {
            "source_id": "source-synthetic",
            "source_type": "public_registry",
            "source_label": "Synthetic Public Registry",
            "source_host": "registry.synthetic.test",
            "health": {"ok": 0, "failed": 0, "yield": 0},
        }
    ]
    assert isinstance(status["reason"], str) and status["reason"].strip()
    feed.close()


def test_recorded_workflow_publishes_source_identity_on_trace_and_status(
    tmp_path, monkeypatch
) -> None:
    original = tmp_path / "synthetic-case"
    original.mkdir()
    (original / "brief.md").write_text("Find public library information.\n", encoding="utf-8")
    lake = FileLake(tmp_path / "lake")
    run_id = "mock-r29-workflow"

    def scratch_case(_case_dir, _run_id):
        scratch = tmp_path / "scratch" / "case"
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "brief.md").write_text(
            (original / "brief.md").read_text(encoding="utf-8"), encoding="utf-8"
        )
        return scratch, lake

    monkeypatch.setattr("ontofill.workflow._scratch_case", scratch_case)
    decision = library_decisions()
    run_provenance = generated_by(decision)

    def recorded_discovery(case_dir, ontology, _decision, search, *, gaps=None, max_sources=1):
        prd = json.loads((case_dir / "01-scope/prd.json").read_text(encoding="utf-8"))
        source_id = "source-synthetic"
        source_url = "https://libraries.example.test/public"
        title = "Synthetic Public Registry"
        fingerprint = source_fingerprint(
            url=source_url,
            title=title,
            snippet="Public registry records",
            provider="synthetic-recorded",
            capture_key=None,
            authority_policy=prd["authority_policy"],
        )
        candidate_dir = case_dir / "03-fanout/sources" / source_id
        candidate_dir.mkdir(parents=True)
        (candidate_dir / "candidate.json").write_text(
            json.dumps(
                {
                    "source_id": source_id,
                    "url": source_url,
                    "landing_url": source_url,
                    "title": title,
                    "snippet": "Public registry records",
                    "provider": "synthetic-recorded",
                    "capture_key": None,
                    "fingerprint": fingerprint,
                    "authority": "auto",
                    "source_type": "public_registry",
                }
            ),
            encoding="utf-8",
        )
        objective = {
            "id": "objective-synthetic",
            "source_id": source_id,
            "source_url": source_url,
            "source_type": "public_registry",
            "source_fingerprint": fingerprint,
            "discovered_by": {
                "provider": "synthetic-recorded",
                "at": "2026-01-01T00:00:00Z",
            },
            "target_fields": ["name"],
            "priority": 1,
            "expected_contribution": 1.0,
        }
        trace_step = _trace_step(
            run_id,
            3,
            run_provenance,
            "source.capture",
            "03-fanout/surface-map/discovery.json",
        )
        trace_step["source_id"] = source_id
        search.trace.append(trace_step)
        return {
            "ontology_version": ontology["version"],
            "prd_path": "01-scope/prd.json",
            "generated_by": run_provenance,
            "objectives": [objective],
        }

    class SearchTrace:
        def __init__(self) -> None:
            self.trace: list[dict] = []
            self.jobs: list[dict] = []

    monkeypatch.setattr("ontofill.workflow.discover_objectives", recorded_discovery)
    result = run_case(
        original,
        to_phase=3,
        run_id=run_id,
        preview_past_checkpoints=True,
        decision=decision,
        search_client=SearchTrace(),
        lake=lake,
    )

    assert result == 3
    status = json.loads(lake.read_key(f"runs/{original.name}/{run_id}/status.json"))
    assert status["state"] == "paused"
    assert status["checkpoint_pending"] == "prd"
    source = status["sources"][0]
    assert source["source_label"] == "Synthetic Public Registry"
    assert source["source_host"] == "libraries.example.test"
    assert status["reason"] == "waiting for approval: prd"
    trace = [
        json.loads(line)
        for line in lake.read_key(f"runs/{original.name}/{run_id}/trace.live.jsonl").splitlines()
    ]
    source_trace = next(step for step in trace if step.get("source_id") == "source-synthetic")
    assert source_trace["source_label"] == "Synthetic Public Registry"
    assert source_trace["source_host"] == "libraries.example.test"


@pytest.mark.parametrize(
    ("state", "checkpoint", "expected"),
    [
        ("paused", None, "run paused"),
        ("paused", "source", "waiting for approval: source"),
        ("done", None, "run completed"),
        ("failed", None, "run failed"),
    ],
)
def test_terminal_status_without_explicit_reason_gets_a_fallback(
    tmp_path, state: str, checkpoint: str | None, expected: str
) -> None:
    lake = FileLake(tmp_path / "lake")
    feed = RunFeed(lake, "synthetic-case", "mock-r29-stop", RECORDED, start_heartbeat=False)
    feed.update_status(state=state, phase=3, checkpoint_pending=checkpoint)

    status = json.loads(lake.read_key("runs/synthetic-case/mock-r29-stop/status.json"))
    assert status["reason"] == expected
    feed.close()


def test_explicit_failure_reason_is_preserved(tmp_path) -> None:
    lake = FileLake(tmp_path / "lake")
    feed = RunFeed(lake, "synthetic-case", "mock-r29-explicit", RECORDED, start_heartbeat=False)
    feed.update_status(state="failed", phase=3, reason="sandbox limit exceeded: memory")

    status = json.loads(lake.read_key("runs/synthetic-case/mock-r29-explicit/status.json"))
    assert status["reason"] == "sandbox limit exceeded: memory"
    feed.close()
