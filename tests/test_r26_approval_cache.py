"""R26: a stale input fingerprint must not erase a byte-verified approval."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontofill.case.checkpoints import ApprovalArtifactMismatch, write_json
from ontofill.phases.p1_scope.phase import _authority_policy_check, draft_prd
from ontofill.phases.p2_ontology.phase import draft_factors, draft_ontology
from tests.approval_support import bind_approval

VULTR = {
    "backend": "vultr",
    "model": "synthetic-vultr",
    "at": "2026-09-26T00:00:00Z",
}


class NoCallDecision:
    backend = "vultr"
    model = "synthetic-vultr"

    def complete_json(self, *_args, **_kwargs):
        raise AssertionError("an approved artifact should be reused without inference")

    def review_json(self, *_args, **_kwargs):
        raise AssertionError("an approved artifact should be reused without review inference")


class InferenceReached(Exception):
    """Raised by the fake client to prove a denied artifact was not reused."""


class RecordInferenceDecision:
    backend = "vultr"
    model = "synthetic-vultr"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete_json(self, purpose: str, *_args, **_kwargs):
        self.calls.append(purpose)
        raise InferenceReached(purpose)

    def review_json(self, purpose: str, *_args, **_kwargs):
        self.calls.append(purpose)
        raise InferenceReached(purpose)


def _prd() -> dict:
    return {
        "version": "1",
        "brief_path": "brief.md",
        "personas": [{"id": "reader", "description": "Reads public records"}],
        "jobs_to_be_done": [
            {"id": "find", "persona_id": "reader", "description": "Find public records"}
        ],
        "requirements": [
            {"id": "evidence", "job_id": "find", "description": "Show public evidence"}
        ],
        "constraints": ["Use public sources"],
        "non_goals": [],
        "definition_of_done": [
            {
                "id": "records",
                "metric": "public records",
                "operator": ">=",
                "target": 1,
                "basis": "proposed",
                "rationale": "One record exercises the synthetic workflow.",
                "feasibility": "The synthetic record fits the test budget.",
            }
        ],
        "authority_policy": {
            "jurisdiction": "Example City",
            "trusted_publishers": [
                {
                    "kind": "Example City archive",
                    "tier": "primary",
                    "jurisdiction": "Example City",
                    "domains": ["archive.example.test"],
                    "rationale": "A local public publisher.",
                }
            ],
            "unknown_source_action": "review",
        },
        "generated_by": VULTR,
    }


def _factor() -> dict:
    return {
        "id": "variation",
        "label": "Variation",
        "description": "How records differ",
        "kind": "conceptual",
        "evidence": [],
    }


def _factors() -> dict:
    return {"factors": [_factor()], "revisions": [], "generated_by": VULTR}


def _ontology(prd: dict, factors: dict) -> tuple[dict, dict]:
    ontology = {
        "version": "1",
        "prd_path": "01-scope/prd.json",
        "factors": factors["factors"],
        "taxonomies": [
            {
                "factor_id": "variation",
                "root_label": "Variation",
                "children": [
                    {
                        "id": "stable",
                        "label": "Stable",
                        "level": 1,
                        "critic_label": "Good-Exclusive",
                    }
                ],
                "soundness": 1.0,
                "coverage": None,
            }
        ],
        "primary_class": "record",
        "classes": [
            {
                "id": "record",
                "label": "Record",
                "label_plural": "Records",
                "description": "A public record",
                "title_property": "name",
                "identifier_property": "name",
                "aligned_to": None,
            }
        ],
        "properties": [
            {
                "id": "name",
                "label": "Name",
                "domain": "record",
                "datatype": "string",
                "dod": True,
                "order": 0,
                "description": "The displayed name",
                "aligned_to": None,
            }
        ],
        "relations": [],
        "rules": [],
        "source_classes": [{"id": "directory", "label": "Directory"}],
        "dod_queries_path": "02-ontology/dod-queries.json",
        "shacl_path": "02-ontology/shapes.ttl",
        "generated_by": VULTR,
        "revisions": [],
    }
    queries = {
        "queries": [
            {
                "criterion_id": prd["definition_of_done"][0]["id"],
                "aggregate": "count_entities",
                "class_id": "record",
                "target": prd["definition_of_done"][0]["target"],
                "operator": prd["definition_of_done"][0]["operator"],
            }
        ],
        "generated_by": VULTR,
        "prd_path": "01-scope/prd.json",
        "ontology_version": "1",
    }
    return ontology, queries


def _seed_approved_case(case_dir: Path, phase: str) -> tuple[dict, str, str]:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "brief.md").write_text("Find public records in Example City.", encoding="utf-8")
    prd = _prd()

    if phase == "prd":
        directory = case_dir / "01-scope"
        artifact_path = directory / "prd.json"
        artifact = prd
        relative_path = "01-scope/prd.json"
        fingerprint_path = directory / "prd.input.sha256"
        checkpoint = "prd"
    elif phase == "factors":
        directory = case_dir / "02-ontology/factors"
        artifact_path = directory / "factors.json"
        artifact = _factors()
        relative_path = "02-ontology/factors/factors.json"
        fingerprint_path = directory / "factors.input.sha256"
        checkpoint = "factors"
    else:
        directory = case_dir / "02-ontology"
        artifact_path = directory / "ontology.json"
        artifact, queries = _ontology(prd, _factors())
        write_json(directory / "dod-queries.json", queries)
        (directory / "shapes.ttl").write_text("# synthetic shape\n", encoding="utf-8")
        relative_path = "02-ontology/ontology.json"
        fingerprint_path = directory / "ontology.input.sha256"
        checkpoint = "ontology"

    write_json(artifact_path, artifact)
    write_json(
        directory / "APPROVED",
        bind_approval(
            case_dir,
            [relative_path],
            {
                "approver": "Synthetic reviewer",
                "date": "2026-09-26",
                "checkpoint": checkpoint,
                "decision": "approve",
            },
        ),
    )
    (directory / "APPROVAL_PENDING.md").write_text("synthetic review\n", encoding="utf-8")
    fingerprint_path.write_text("old-input-key\n", encoding="utf-8")
    return artifact, relative_path, fingerprint_path.relative_to(case_dir).as_posix()


def _invoke(case_dir: Path, phase: str, decision: NoCallDecision) -> dict:
    if phase == "prd":
        return draft_prd(case_dir, decision)
    if phase == "factors":
        return draft_factors(case_dir, _prd(), decision)
    return draft_ontology(case_dir, _prd(), _factors(), decision)


def _snapshot(case_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(case_dir).as_posix(): path.read_bytes()
        for path in case_dir.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("phase", ["prd", "factors", "ontology"])
def test_approved_artifact_survives_input_fingerprint_mismatch(tmp_path, phase: str) -> None:
    artifact, _relative_path, fingerprint_path = _seed_approved_case(tmp_path, phase)
    before = _snapshot(tmp_path)

    reused = _invoke(tmp_path, phase, NoCallDecision())

    after = _snapshot(tmp_path)
    assert reused == artifact
    assert reused["generated_by"] == VULTR
    assert after[fingerprint_path] != before[fingerprint_path]
    assert after[fingerprint_path].strip() != b"old-input-key"
    assert {key: value for key, value in after.items() if key != fingerprint_path} == {
        key: value for key, value in before.items() if key != fingerprint_path
    }
    assert not list(tmp_path.rglob("APPROVED.stale.*"))


@pytest.mark.parametrize("phase", ["prd", "factors", "ontology"])
def test_wrong_approval_digest_refuses_without_writes(tmp_path, phase: str) -> None:
    _artifact, _relative_path, _fingerprint_path = _seed_approved_case(tmp_path, phase)
    artifact_name = {
        "prd": "01-scope/prd.json",
        "factors": "02-ontology/factors/factors.json",
        "ontology": "02-ontology/ontology.json",
    }[phase]
    artifact_path = tmp_path / artifact_name
    artifact_path.write_bytes(artifact_path.read_bytes() + b" ")
    before = _snapshot(tmp_path)

    with pytest.raises(ApprovalArtifactMismatch, match="different artifact version"):
        _invoke(tmp_path, phase, NoCallDecision())

    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("phase", ["prd", "factors", "ontology"])
def test_denied_artifact_with_input_key_drift_is_redrafted(tmp_path, phase: str) -> None:
    _artifact, relative_path, _fingerprint_path = _seed_approved_case(tmp_path, phase)
    approval_path = tmp_path / Path(relative_path).parent / "APPROVED"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approval.update(
        decision="deny",
        reason="Revise the synthetic proposal.",
    )
    write_json(approval_path, approval)
    before = _snapshot(tmp_path)
    decision = RecordInferenceDecision()

    with pytest.raises(InferenceReached):
        _invoke(tmp_path, phase, decision)

    assert decision.calls
    assert _snapshot(tmp_path) == before


def test_model_named_secondary_domains_satisfy_generic_jurisdiction_clause() -> None:
    document = _prd()
    document["authority_policy"]["trusted_publishers"].append(
        {
            "kind": "North Republic public list",
            "tier": "secondary",
            "jurisdiction": "North Republic",
            "domains": ["records.example.test"],
            "rationale": "A public list for secondary comparison.",
        }
    )
    document["authority_policy"]["trusted_publishers"].append(
        {
            "kind": "North Republic access list",
            "tier": "secondary",
            "jurisdiction": "North Republic",
            "domains": ["access.example.test"],
            "rationale": "A public access list for secondary comparison.",
        }
    )
    revision = [{"reason": "Keep NR lists as secondary cross-checks."}]

    assert _authority_policy_check(document, revision).passed

    unknown = [{"reason": "Keep XY lists as secondary cross-checks."}]
    assert not _authority_policy_check(document, unknown).passed

    domainless = json.loads(json.dumps(document))
    domainless["authority_policy"]["trusted_publishers"][1]["domains"] = []
    domainless["authority_policy"]["trusted_publishers"][2]["domains"] = []
    assert not _authority_policy_check(domainless, revision).passed

    unjustified = json.loads(json.dumps(document))
    unjustified["authority_policy"]["trusted_publishers"][1]["kind"] = "North Republic portal"
    unjustified["authority_policy"]["trusted_publishers"][1]["rationale"] = (
        "A general public information source."
    )
    unjustified["authority_policy"]["trusted_publishers"][2]["kind"] = "North Republic service"
    unjustified["authority_policy"]["trusted_publishers"][2]["rationale"] = (
        "A general public information source."
    )
    assert not _authority_policy_check(unjustified, revision).passed
