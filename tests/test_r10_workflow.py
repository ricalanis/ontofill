"""R10 workflow handoff: live classification reaches the trace and export."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ontofill import workflow
from ontofill.lake import FileLake


def _refine_fixture(tmp_path, monkeypatch, *, backend: str):
    case_dir = tmp_path / "case"
    ontology_path = case_dir / "02-ontology/ontology.json"
    ontology_path.parent.mkdir(parents=True)
    ontology_path.write_text(json.dumps({"shacl_path": "02-ontology/shapes.ttl"}))
    (case_dir / "02-ontology/dod-queries.json").write_text("{}")
    lake = FileLake(tmp_path / "lake")
    monkeypatch.setattr(workflow, "_scratch_case", lambda _case_dir, _run_id: (case_dir, lake))
    run_id = "mock-r10" if backend == "recorded" else "run-r10"
    provenance = {
        "backend": backend,
        "model": "recorded-response" if backend == "recorded" else "glm-5.3",
        "at": "2026-09-27T00:00:00+00:00",
    }
    lake.write_key(f"runs/synthetic/{run_id}/trace.live.jsonl", b"")
    monkeypatch.setattr(
        workflow,
        "_existing_run",
        lambda _case_dir, _run_id: (
            "synthetic",
            lake,
            run_id,
            {"generated_by": provenance, "preview": backend == "recorded"},
        ),
    )
    monkeypatch.setattr(
        workflow,
        "silver_store_from_env",
        lambda: SimpleNamespace(list_for_run=lambda _run_id: [object()]),
    )
    return case_dir, lake, run_id


def test_live_refine_requires_screened_gateway_before_classification(tmp_path, monkeypatch) -> None:
    case_dir, _lake, _run_id = _refine_fixture(tmp_path, monkeypatch, backend="vultr")
    monkeypatch.delenv("ONTOFILL_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr(
        workflow.VultrDecisionClient,
        "from_env",
        classmethod(lambda _cls: pytest.fail("direct inference client must not start")),
    )

    with pytest.raises(RuntimeError, match="ONTOFILL_GATEWAY_TOKEN"):
        workflow.refine_case(case_dir, run_id="run-r10")


def test_live_refine_appends_classifier_usage_and_exports_measured_coverage(
    tmp_path, monkeypatch
) -> None:
    case_dir, lake, run_id = _refine_fixture(tmp_path, monkeypatch, backend="vultr")
    monkeypatch.setenv("ONTOFILL_GATEWAY_TOKEN", "synthetic-token")
    decision = SimpleNamespace(call_log=[])
    monkeypatch.setattr(
        workflow.VultrDecisionClient,
        "from_env",
        classmethod(lambda _cls: decision),
    )

    def fake_refine(_observations, *, decision, **_kwargs):
        assert decision is not None
        decision.call_log.append(
            {
                "purpose": "refine.classify_entities",
                "backend": "vultr",
                "model": "minimax-m3",
                "at": "2026-09-27T00:00:01+00:00",
                "status": "ok",
                "usage": {
                    "backend": "vultr",
                    "model": "minimax-m3",
                    "input_tokens": 20,
                    "output_tokens": 5,
                    "est_usd": 0.001,
                },
            }
        )
        return SimpleNamespace(entities=[], classified=True)

    exported = {}

    def fake_export(*_args, **kwargs):
        exported.update(kwargs)

    monkeypatch.setattr(workflow, "refine_observations", fake_refine)
    monkeypatch.setattr(workflow, "export_run", fake_export)

    assert workflow.refine_case(case_dir, run_id=run_id) == 0
    assert exported["taxonomy_classified"] is True
    steps = [
        json.loads(line)
        for line in lake.read_key(f"runs/synthetic/{run_id}/trace.live.jsonl").splitlines()
    ]
    assert len(steps) == 1
    assert steps[0]["requested"]["tool"] == "decision.complete_json"
    assert steps[0]["executed"]["artifact"] == "refine.classify_entities"
    assert steps[0]["usage"]["input_tokens"] == 20
    assert exported["trace"] == steps


def test_recorded_refine_keeps_coverage_unmeasured_without_gateway(tmp_path, monkeypatch) -> None:
    case_dir, _lake, run_id = _refine_fixture(tmp_path, monkeypatch, backend="recorded")
    monkeypatch.delenv("ONTOFILL_GATEWAY_TOKEN", raising=False)

    def fake_refine(_observations, *, decision, **_kwargs):
        assert decision is None
        return SimpleNamespace(entities=[], classified=False)

    exported = {}
    monkeypatch.setattr(workflow, "refine_observations", fake_refine)
    monkeypatch.setattr(workflow, "export_run", lambda *_args, **kwargs: exported.update(kwargs))

    assert workflow.refine_case(case_dir, run_id=run_id) == 0
    assert exported["taxonomy_classified"] is False
