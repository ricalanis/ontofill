"""Every engine inference call joins its gateway record to the same run and trace step."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from ontofill import workflow
from ontofill.inference import VultrDecisionClient
from ontofill.workflow import _publish_decision_calls, run_case
from tests.approval_support import bind_approval


@pytest.mark.parametrize("supplied_run_id", [None, "run-r29-explicit"])
def test_run_id_exists_before_gateway_catalog_call(
    tmp_path: Path, monkeypatch, supplied_run_id: str | None
) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "brief.md").write_text("Find public library records.", encoding="utf-8")
    monkeypatch.setenv("ONTOFILL_GATEWAY_TOKEN", "synthetic-token")
    seen: list[str] = []

    def capture_factory(*, run_id: str):
        seen.append(run_id)
        raise RuntimeError("catalog intercepted")

    monkeypatch.setattr(VultrDecisionClient, "from_env", capture_factory)

    with pytest.raises(RuntimeError, match="catalog intercepted"):
        run_case(case, run_id=supplied_run_id)

    assert len(seen) == 1
    if supplied_run_id is None:
        assert seen[0].startswith("run-")
    else:
        assert seen[0] == supplied_run_id


def test_model_call_trace_uses_gateway_step_id() -> None:
    class Feed:
        def __init__(self) -> None:
            self.steps: list[dict] = []

        def append_step(self, step: dict, *, screenshot_key=None) -> None:
            self.steps.append(step)

    class Decision:
        def __init__(self) -> None:
            self.call_log = [
                {
                    "purpose": "phase2.schema",
                    "backend": "vultr",
                    "model": "synthetic-vultr",
                    "at": datetime.now(UTC).isoformat(),
                    "run_id": "run-r29",
                    "step_id": "step:gateway-r29",
                    "status": "ok",
                }
            ]

    feed = Feed()
    trace: list[dict] = []
    _publish_decision_calls(feed, trace, Decision(), 0, "run-r29", 2)

    assert trace[0]["run_id"] == "run-r29"
    assert trace[0]["step_id"] == "step:gateway-r29"
    assert feed.steps == trace


def test_standalone_refine_attributes_gateway_catalog_to_existing_run(
    tmp_path: Path, monkeypatch
) -> None:
    case = tmp_path / "case"
    ontology_path = case / "02-ontology/ontology.json"
    ontology_path.parent.mkdir(parents=True)
    ontology_path.write_text("{}\n", encoding="utf-8")
    approval = bind_approval(
        case,
        ["02-ontology/ontology.json"],
        {"approver": "Test Reviewer", "date": "2026-09-27", "checkpoint": "ontology"},
    )
    (ontology_path.parent / "APPROVED").write_text(json.dumps(approval), encoding="utf-8")

    class Lake:
        def read_key(self, _key: str) -> bytes:
            return b""

    class Store:
        def list_for_run(self, _run_id: str) -> list[object]:
            return [object()]

    monkeypatch.setattr(
        workflow,
        "_existing_run",
        lambda *_args, **_kwargs: (
            "test-case",
            Lake(),
            "run-r29-refine",
            {
                "generated_by": {
                    "backend": "vultr",
                    "model": "test-model",
                    "at": "2026-09-27T00:00:00Z",
                }
            },
        ),
    )
    monkeypatch.setattr(workflow, "silver_store_from_env", Store)
    monkeypatch.setattr(
        workflow,
        "replay_bronze_observations",
        lambda **_kwargs: SimpleNamespace(
            parse_job_records=[], parse_trace_steps=[], observations=[]
        ),
    )
    monkeypatch.setenv("ONTOFILL_GATEWAY_TOKEN", "synthetic-token")
    seen: list[str] = []

    def capture_factory(*, run_id: str):
        seen.append(run_id)
        raise RuntimeError("catalog intercepted")

    monkeypatch.setattr(VultrDecisionClient, "from_env", capture_factory)
    with pytest.raises(RuntimeError, match="catalog intercepted"):
        workflow.refine_case(case, run_id="run-r29-refine")
    assert seen == ["run-r29-refine"]
