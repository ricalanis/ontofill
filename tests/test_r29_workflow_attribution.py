"""Every engine inference call joins its gateway record to the same run and trace step."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ontofill.inference import VultrDecisionClient
from ontofill.workflow import _publish_decision_calls, run_case


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
