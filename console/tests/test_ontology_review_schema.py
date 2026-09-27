"""The ontology review shows everything an approver signs: relations (and how they link), rules with their
predicates, each property's class, the compiled definition of done beside the class it is measured on (a completeness
criterion over a class with no DoD properties is flagged), and the proposals the engine set aside (R31)."""

import json

PAGE = "/cases/libraries/approvals/02-ontology"


def test_review_shows_relations_rules_dod_and_set_aside(client, cases_dir):
    onto_dir = cases_dir / "libraries" / "case" / "02-ontology"
    onto = json.loads((onto_dir / "ontology.json").read_text())
    onto["rules"] = [
        {
            "id": "r_hours",
            "label": "Hours are published",
            "checks": "hours exist",
            "verify": ["look"],
            "predicate": {"all": [{"op": "equals", "property": "opening_hours", "value": "published"}]},
        }
    ]
    (onto_dir / "ontology.json").write_text(json.dumps(onto))
    (onto_dir / "dod-queries.json").write_text(
        json.dumps(
            {
                "generated_by": onto["generated_by"],
                "queries": [
                    {
                        "criterion_id": "complete_ops",
                        "aggregate": "entities_meeting_completeness",
                        "class": "operator",
                        "properties": "dod",
                        "min_ratio": 0.8,
                        "target": 0.8,
                        "operator": ">=",
                    },
                    {
                        "criterion_id": "libraries_found",
                        "aggregate": "count_entities",
                        "class_id": "library",
                        "target": 20,
                        "operator": ">=",
                    },
                ],
            }
        )
    )
    (onto_dir / "recommendations").mkdir(exist_ok=True)
    (onto_dir / "recommendations" / "unresolved.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "ontology_path": "02-ontology/ontology.json",
                "generated_by": onto["generated_by"],
                "unresolved": [
                    {
                        "kind": "rule",
                        "id": "r_cross",
                        "reason": "rule `r_cross` is invalid: spans two classes",
                        "proposal": {"id": "r_cross", "label": "Library matches its operator"},
                    }
                ],
            }
        )
    )
    html = client.get(PAGE).text
    assert "Relations" in html and "same_operator" in html and "library → library" in html
    assert "Hours are published" in html and "opening_hours = published" in html
    assert "Set aside: not in this schema (1)" in html and "Library matches its operator" in html
    assert "spans two classes" in html
    assert "Definition of done, compiled" in html
    assert "operator has no definition-of-done properties, so this criterion can never be met" in html
    lib_dod = sum(1 for p in onto["properties"] if p.get("domain") == "library" and p.get("dod"))
    assert f"{lib_dod} DoD properties" in html
    # the set-aside file is shown, never bound: the approval digests still name only the reviewed artifact
    assert 'name="artifact_sha256.02-ontology/recommendations' not in html


def test_no_set_aside_section_without_the_file(client):
    assert "Set aside" not in client.get(PAGE).text
