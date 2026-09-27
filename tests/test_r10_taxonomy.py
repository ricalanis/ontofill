"""R10: honest taxonomy metrics — a separate critic labels nodes and refine classifies entities."""

from __future__ import annotations

import hashlib
import json

import pytest

from ontofill.inference import ModelValidationExhausted, RecordedDecisionClient
from ontofill.lake import FileLake
from ontofill.phases.p2_ontology.phase import OntologyDraftUnavailable, draft_ontology
from ontofill.refiner import Observation, classify_entities, refine_observations, taxonomy_levels

RECORDED = {"backend": "recorded", "model": "synthetic", "at": "2026-01-01T00:00:00Z"}
RUN_ID = "mock-r10"


def _prd() -> dict:
    return {
        "version": "1",
        "brief_path": "brief.md",
        "personas": [{"id": "resident", "description": "Local resident"}],
        "jobs_to_be_done": [
            {"id": "check", "persona_id": "resident", "description": "Check library access"}
        ],
        "requirements": [{"id": "evidence", "job_id": "check", "description": "Show evidence"}],
        "constraints": [],
        "non_goals": [],
        "definition_of_done": [
            {
                "id": "dod-1",
                "metric": "libraries",
                "operator": ">=",
                "target": 1,
                "basis": "proposed",
                "rationale": "One library proves the flow.",
                "feasibility": "One record fits the synthetic budget.",
            }
        ],
        "authority_policy": {
            "jurisdiction": "Example City",
            "trusted_publishers": [],
            "unknown_source_action": "review",
        },
    }


def _factors() -> dict:
    return {
        "factors": [
            {
                "id": "access",
                "label": "Public access",
                "description": "How access varies",
                "kind": "conceptual",
                "evidence": [],
            }
        ]
    }


def _schema() -> dict:
    return {
        "primary_class": "library",
        "classes": [
            {
                "id": "library",
                "label": "Library",
                "label_plural": "Libraries",
                "description": "Public library",
                "title_property": "name",
                "identifier_property": "name",
                "aligned_to": None,
            }
        ],
        "properties": [
            {
                "id": "name",
                "label": "Name",
                "domain": "library",
                "datatype": "string",
                "dod": True,
                "order": 0,
                "description": "Displayed name",
                "aligned_to": None,
            }
        ],
        "relations": [],
        "rules": [],
        "source_classes": [{"id": "directory", "label": "Directory"}],
    }


def _client(node_label: str) -> RecordedDecisionClient:
    return RecordedDecisionClient(
        {
            "phase2.factors": [_factors()],
            "phase2.schema": [_schema()],
            "phase2.dod_queries": [
                {
                    "queries": [
                        {
                            "criterion_id": "dod-1",
                            "aggregate": "count_entities",
                            "class_id": "library",
                            "target": 1,
                            "operator": ">=",
                        }
                    ]
                }
            ],
            "phase2.taxonomies": [
                {
                    "taxonomies": [
                        {
                            "factor_id": "access",
                            "root_label": "Public access",
                            "children": [
                                {"id": "internet", "label": "Internet", "level": 1},
                                {"id": "hours", "label": "Hours", "level": 1},
                            ],
                        }
                    ]
                }
            ],
            "critic.phase2.taxonomy_nodes": [
                {
                    "labels": [
                        {"factor_id": "access", "node_id": "internet", "critic_label": node_label},
                        {
                            "factor_id": "access",
                            "node_id": "hours",
                            "critic_label": "Good-Overlapping",
                        },
                    ]
                }
            ],
        }
    )


def test_generator_does_not_grade_itself_and_critic_labels_each_node(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Check library access.", encoding="utf-8")
    factors = _factors()
    decision = _client("Bad")
    ontology = draft_ontology(tmp_path, _prd(), factors, decision)
    taxonomy = ontology["taxonomies"][0]
    labels = {child["id"]: child["critic_label"] for child in taxonomy["children"]}
    assert labels == {"internet": "Bad", "hours": "Good-Overlapping"}
    # soundness is the share of Good-* labels the separate critic gave; coverage is unknown here
    assert taxonomy["soundness"] == 0.5
    assert taxonomy["coverage"] is None
    assert taxonomy["soundness"] != taxonomy["coverage"]
    purposes = [purpose for purpose, _ in decision.calls]
    assert "phase2.taxonomies" in purposes and "critic.phase2.taxonomy_nodes" in purposes


def test_critic_labels_can_differ_from_a_self_assessed_tree(tmp_path) -> None:
    """The generator proposes; the critic's labels are what soundness reports."""
    (tmp_path / "brief.md").write_text("Check library access.", encoding="utf-8")
    factors = _factors()
    decision = _client("Good-Exclusive")
    ontology = draft_ontology(tmp_path, _prd(), factors, decision)
    taxonomy = ontology["taxonomies"][0]
    labels = {child["id"]: child["critic_label"] for child in taxonomy["children"]}
    assert labels == {"internet": "Good-Exclusive", "hours": "Good-Overlapping"}
    assert taxonomy["soundness"] == 1.0
    assert taxonomy["coverage"] is None


def test_legacy_ontology_cache_is_regenerated_and_critic_regrades_nodes(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Check library access.", encoding="utf-8")
    prd = _prd()
    factors = _factors()
    draft_ontology(tmp_path, prd, factors, _client("Good-Exclusive"))

    ontology_path = tmp_path / "02-ontology/ontology.json"
    legacy = json.loads(ontology_path.read_text(encoding="utf-8"))
    for taxonomy in legacy["taxonomies"]:
        for child in taxonomy["children"]:
            child["critic_label"] = "Good-Exclusive"
        taxonomy["soundness"] = 1.0
    ontology_path.write_text(json.dumps(legacy), encoding="utf-8")

    # Before the separate critic existed, the ontology cache used these inputs only.
    legacy_digest = hashlib.sha256(
        json.dumps(
            [prd, factors["factors"], []],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    (tmp_path / "02-ontology/ontology.input.sha256").write_text(
        f"{legacy_digest}\n", encoding="utf-8"
    )

    decision = _client("Bad")
    regenerated = draft_ontology(tmp_path, prd, factors, decision)
    taxonomy = regenerated["taxonomies"][0]
    labels = {child["id"]: child["critic_label"] for child in taxonomy["children"]}

    assert labels == {"internet": "Bad", "hours": "Good-Overlapping"}
    assert taxonomy["soundness"] == 0.5
    assert "critic.phase2.taxonomy_nodes" in [purpose for purpose, _ in decision.calls]


def test_critic_rejects_duplicate_node_labels(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Check library access.", encoding="utf-8")
    decision = _client("Good-Exclusive")
    invalid_labels = {
        "labels": [
            {"factor_id": "access", "node_id": "internet", "critic_label": "Bad"},
            {"factor_id": "access", "node_id": "internet", "critic_label": "Good-Exclusive"},
        ]
    }
    critic_responses = decision.responses["critic.phase2.taxonomy_nodes"]
    critic_responses.clear()
    critic_responses.extend([invalid_labels, invalid_labels, invalid_labels])

    with pytest.raises(OntologyDraftUnavailable, match="duplicate node labels"):
        draft_ontology(tmp_path, _prd(), _factors(), decision)


def _ontology() -> dict:
    return {
        "version": "1",
        "prd_path": "01-scope/prd.json",
        "factors": [
            {
                "id": "access",
                "label": "Access",
                "description": "d",
                "kind": "conceptual",
                "evidence": [],
            }
        ],
        "taxonomies": [
            {
                "factor_id": "access",
                "root_label": "Access",
                "children": [
                    {
                        "id": "internet",
                        "label": "Internet",
                        "level": 1,
                        "critic_label": "Good-Exclusive",
                    },
                    {"id": "hours", "label": "Hours", "level": 1, "critic_label": "Good-Exclusive"},
                ],
                "soundness": 1.0,
                "coverage": None,
            }
        ],
        "primary_class": "library",
        "classes": [
            {
                "id": "library",
                "label": "Library",
                "label_plural": "Libraries",
                "description": "Public library",
                "title_property": "name",
                "identifier_property": "name",
                "aligned_to": None,
            }
        ],
        "properties": [
            {
                "id": "name",
                "label": "Name",
                "domain": "library",
                "datatype": "string",
                "dod": True,
                "order": 0,
                "description": "Displayed name",
                "aligned_to": None,
            }
        ],
        "relations": [],
        "rules": [],
        "source_classes": [{"id": "directory", "label": "Directory"}],
        "dod_queries_path": "02-ontology/dod-queries.json",
        "shacl_path": "02-ontology/shapes.ttl",
        "generated_by": RECORDED,
    }


def _observation(lake: FileLake, entity_id: str, name: str) -> Observation:
    return Observation(
        run_id=RUN_ID,
        entity_id=entity_id,
        entity_class="library",
        property_id="name",
        value=name,
        evidence={
            "url": "https://example.invalid/list",
            "bronze_key": lake.put_bytes(b"<html>x</html>"),
            "selector": "td",
            "screenshot_key": lake.put_bytes(b"png"),
            "captured_at": "2026-01-01T00:00:00Z",
            "source_id": "directory",
            "source_type": "directory",
        },
        step_id="step-1",
        generated_by=RECORDED,
    )


def test_coverage_is_null_before_classification_and_real_after(tmp_path) -> None:
    lake = FileLake(tmp_path / "lake")
    model = _ontology()
    observations = [
        _observation(lake, "library:a", "Alpha"),
        _observation(lake, "library:b", "Beta"),
    ]
    before = refine_observations(observations, ontology=model, generated_by=RECORDED)
    assert before.classified is False
    assert all(entity["classified_as"] == [] for entity in before.entities)

    decision = RecordedDecisionClient(
        {
            "refine.classify_entities": [
                {
                    "assignments": [
                        {"entity_id": "library:a", "node_ids": ["access:internet"]},
                        {"entity_id": "library:b", "node_ids": ["access:hours"]},
                    ]
                }
            ]
        }
    )
    after = refine_observations(
        observations, ontology=model, generated_by=RECORDED, decision=decision
    )
    assert after.classified is True
    assert {entity["classified_as"][0] for entity in after.entities} == {
        "access:internet",
        "access:hours",
    }


def test_taxonomy_levels_qualify_nodes(tmp_path) -> None:
    assert taxonomy_levels(_ontology()) == {"access": [["access:hours", "access:internet"]]}


def test_metrics_coverage_is_null_until_classified(tmp_path) -> None:
    """level_ratio_coverage and taxonomy_metrics.coverage stay null without a classification pass."""
    from ontofill.refiner import export_run
    from tests.test_refiner import dod_queries, observed, ontology, trace, write_lineage

    lake = FileLake(tmp_path / "lake")
    model, queries = ontology(), dod_queries()
    entries = [observed(lake, "title", "First")]
    case_dir = tmp_path / "case"
    write_lineage(case_dir, model, queries, RECORDED)
    unclassified = refine_observations(entries, ontology=model, generated_by=RECORDED)
    metrics = export_run(
        lake,
        case_dir,
        "books",
        RUN_ID,
        unclassified.entities,
        ontology=model,
        dod_queries=queries,
        trace=trace([item.value_id for item in entries], run_id=RUN_ID),
        generated_by=RECORDED,
    )
    assert metrics["level_ratio_coverage"]["edition"] == [None]
    assert metrics["taxonomy_metrics"][0]["coverage"] is None
    assert metrics["taxonomy_metrics"][0]["coverage_basis"] == "none"
    assert metrics["taxonomy_metrics"][0]["soundness"] == 1
    assert metrics["taxonomy_metrics"][0]["soundness_basis"] == "critic"
    assert metrics["taxonomy_metrics"][0]["soundness"] != metrics["taxonomy_metrics"][0]["coverage"]


def test_metrics_report_real_coverage_after_classification(tmp_path) -> None:
    """The brief's check: after classification, metrics carry a non-zero, basis-tagged coverage."""
    from ontofill.refiner import export_run
    from tests.test_refiner import dod_queries, observed, ontology, trace, write_lineage

    lake = FileLake(tmp_path / "lake")
    model, queries = ontology(), dod_queries()
    model["taxonomies"][0]["children"].append(
        {"id": "other", "label": "Other", "level": 1, "critic_label": "Good-Exclusive"}
    )
    entries = [
        observed(lake, "title", "First", entity_id="Book:one"),
        observed(lake, "title", "Second", entity_id="Book:two"),
    ]
    decision = RecordedDecisionClient(
        {
            "refine.classify_entities": [
                {
                    "assignments": [
                        {"entity_id": "Book:one", "node_ids": ["edition:local"]},
                        {"entity_id": "Book:two", "node_ids": []},
                    ]
                }
            ]
        }
    )
    refined = refine_observations(entries, ontology=model, generated_by=RECORDED, decision=decision)
    case_dir = tmp_path / "case"
    write_lineage(case_dir, model, queries, RECORDED)
    metrics = export_run(
        lake,
        case_dir,
        "books",
        RUN_ID,
        refined.entities,
        ontology=model,
        dod_queries=queries,
        trace=trace([item.value_id for item in entries], run_id=RUN_ID),
        generated_by=RECORDED,
        taxonomy_classified=refined.classified,
    )
    metric = metrics["taxonomy_metrics"][0]
    assert metric["coverage_basis"] == "classification"
    assert metric["coverage"] == 0.5  # one of two nodes covered
    assert metric["coverage"] != metric["soundness"]
    assert metrics["level_ratio_coverage"]["edition"] == [0.5]


def test_classification_rejects_an_unknown_node(tmp_path) -> None:
    lake = FileLake(tmp_path / "lake")
    model = _ontology()
    entities = refine_observations(
        [_observation(lake, "library:a", "Alpha")], ontology=model, generated_by=RECORDED
    ).entities
    invalid = {"assignments": [{"entity_id": "library:a", "node_ids": ["access:invented"]}]}
    decision = RecordedDecisionClient({"refine.classify_entities": [invalid] * 3})
    with pytest.raises(ModelValidationExhausted, match="unknown taxonomy node"):
        classify_entities(entities, taxonomy_levels(model), decision)
    assert len(decision.calls) == 3
    assert "unknown taxonomy node" in decision.calls[1][1]


@pytest.mark.parametrize(
    ("assignments", "message"),
    [
        ([{"entity_id": "library:a", "node_ids": []}], "every entity exactly once"),
        (
            [
                {"entity_id": "library:a", "node_ids": []},
                {"entity_id": "library:a", "node_ids": []},
                {"entity_id": "library:b", "node_ids": []},
            ],
            "more than once",
        ),
        (
            [
                {"entity_id": "library:a", "node_ids": []},
                {"entity_id": "library:b", "node_ids": []},
                {"entity_id": "library:unknown", "node_ids": []},
            ],
            "unknown entity",
        ),
    ],
)
def test_classification_requires_exactly_one_assignment_per_entity(
    tmp_path, assignments: list[dict], message: str
) -> None:
    lake = FileLake(tmp_path / "lake")
    model = _ontology()
    entities = refine_observations(
        [
            _observation(lake, "library:a", "Alpha"),
            _observation(lake, "library:b", "Beta"),
        ],
        ontology=model,
        generated_by=RECORDED,
    ).entities
    invalid = {"assignments": assignments}
    decision = RecordedDecisionClient({"refine.classify_entities": [invalid] * 3})

    with pytest.raises(ModelValidationExhausted, match=message):
        classify_entities(entities, taxonomy_levels(model), decision)
    assert len(decision.calls) == 3
    assert message in decision.calls[1][1]


def test_classification_input_is_evidence_only(tmp_path) -> None:
    lake = FileLake(tmp_path / "lake")
    model = _ontology()
    entities = refine_observations(
        [_observation(lake, "library:a", "Alpha")], ontology=model, generated_by=RECORDED
    ).entities
    captured: list[str] = []

    class Capturing:
        def complete_json(self, purpose: str, prompt: str, schema: dict) -> dict:
            captured.append(prompt)
            return {"assignments": [{"entity_id": "library:a", "node_ids": []}]}

    classify_entities(entities, taxonomy_levels(model), Capturing())
    assert json.dumps({"name": "Alpha"}) in captured[0]
    assert "bronze_key" not in captured[0]
    assert "screenshot_key" not in captured[0]
    assert "captured_at" not in captured[0]
