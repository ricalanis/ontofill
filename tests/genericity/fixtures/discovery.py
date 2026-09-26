"""Minimal approved context for source discovery tests."""

import json


def discovery_case(case_dir, brief="Find public reading rooms in Example City"):
    (case_dir / "brief.md").write_text(brief, encoding="utf-8")
    prd_path = case_dir / "01-scope/prd.json"
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    prd_path.write_text(
        json.dumps(
            {
                "authority_policy": {
                    "jurisdiction": "Example City",
                    "trusted_publishers": [
                        {
                            "kind": "city office",
                            "domains": ["city.example.test"],
                            "rationale": "Approved in this synthetic case",
                        }
                    ],
                    "unknown_source_action": "review",
                }
            }
        ),
        encoding="utf-8",
    )
    return {
        "version": "1",
        "primary_class": "room",
        "classes": [{"id": "room", "title_property": "name", "identifier_property": "name"}],
        "properties": [
            {"id": "name", "label": "Name", "domain": "room", "dod": True},
            {"id": "opening_hours", "label": "Opening hours", "domain": "room", "dod": True},
            {"id": "free_access", "label": "Free access", "domain": "room", "dod": True},
        ],
        "source_classes": [{"id": "public_directory", "label": "Public directory"}],
    }
