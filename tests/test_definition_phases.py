import json

import yaml
from rdflib import Graph

from ontofill.case.checkpoints import require_approval
from ontofill.inference import RecordedDecisionClient
from ontofill.phases.p1_scope.phase import draft_prd
from ontofill.phases.p2_ontology.phase import draft_factors, draft_ontology


def test_brief_to_reviewed_factors_and_one_level_ontology(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Map suppliers in Example City.", encoding="utf-8")
    prd_response = {
        "version": "1",
        "brief_path": "brief.md",
        "personas": [{"id": "persona-1", "description": "Researcher"}],
        "jobs_to_be_done": [
            {"id": "job-1", "persona_id": "persona-1", "description": "Inspect public suppliers"}
        ],
        "requirements": [
            {"id": "req-1", "job_id": "job-1", "description": "Show evidence for each value"}
        ],
        "constraints": ["Public read-only sources"],
        "non_goals": ["Private records"],
        "definition_of_done": [
            {"id": "dod-1", "metric": "supplier_count", "operator": ">=", "target": 1}
        ],
    }
    decisions = RecordedDecisionClient(
        {
            "phase1.prd": [prd_response],
            "phase2.factors": [
                {
                    "factors": [
                        {
                            "id": "supplier_type",
                            "label": "Supplier type",
                            "description": "How suppliers differ",
                            "kind": "conceptual",
                            "evidence": [],
                        },
                        {
                            "id": "unneeded_factor",
                            "label": "Unneeded factor",
                            "description": "Rejected during review",
                            "kind": "conceptual",
                            "evidence": [],
                        },
                    ]
                }
            ],
            "phase2.taxonomies": [
                {
                    "taxonomies": [
                        {
                            "factor_id": "supplier_type",
                            "root_label": "Supplier type",
                            "children": [
                                {
                                    "id": "company",
                                    "label": "Company",
                                    "level": 1,
                                    "critic_label": "Good-Exclusive",
                                }
                            ],
                        }
                    ]
                }
            ],
        }
    )
    prd = draft_prd(tmp_path, decisions)
    assert draft_prd(tmp_path, decisions) == prd
    assert [purpose for purpose, _ in decisions.calls] == ["phase1.prd"]
    scope = tmp_path / "01-scope"
    assert not require_approval(
        scope,
        phase=1,
        checkpoint="prd",
        artifact_paths=["01-scope/prd.md"],
        generated_by=prd["generated_by"],
    )
    assert (scope / "APPROVAL_PENDING.md").exists()
    front_matter = (scope / "APPROVAL_PENDING.md").read_text().split("---", 2)[1]
    assert yaml.safe_load(front_matter)["generated_by"]["backend"] == "recorded"
    (scope / "APPROVED").write_text(
        json.dumps({"approver": "Test Reviewer", "date": "2026-09-26", "checkpoint": "prd"}),
        encoding="utf-8",
    )
    assert not require_approval(
        scope,
        phase=1,
        checkpoint="prd",
        artifact_paths=["01-scope/prd.md"],
        generated_by=prd["generated_by"],
    )
    live_provenance = {"backend": "vultr", "model": "test-model", "at": "2026-09-26T14:00:00Z"}
    assert require_approval(
        scope,
        phase=1,
        checkpoint="prd",
        artifact_paths=["01-scope/prd.md"],
        generated_by=live_provenance,
    )
    factors = draft_factors(tmp_path, prd, decisions)
    assert factors["factors"][0]["id"] == "supplier_type"
    factor_dir = tmp_path / "02-ontology/factors"
    (factor_dir / "APPROVED").write_text(
        json.dumps(
            {
                "approver": "Test Reviewer",
                "date": "2026-09-26",
                "checkpoint": "factors",
                "decisions": {"supplier_type": "accept", "unneeded_factor": "reject"},
            }
        ),
        encoding="utf-8",
    )
    ontology = draft_ontology(tmp_path, prd, factors, decisions)
    assert ontology["taxonomies"][0]["children"][0]["id"] == "company"
    assert [factor["id"] for factor in ontology["factors"]] == ["supplier_type"]
    assert ontology["taxonomies"][0]["soundness"] == 1
    shape = Graph().parse(tmp_path / "02-ontology/supplier-shape.ttl", format="turtle")
    assert len(shape) > 0
