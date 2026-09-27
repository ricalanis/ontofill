"""Typed recorded responses keyed only by decision purpose."""

from ontofill.inference import RecordedDecisionClient


def library_decisions() -> RecordedDecisionClient:
    return RecordedDecisionClient(
        {
            "phase1.prd": [
                {
                    "version": "1",
                    "brief_path": "brief.md",
                    "personas": [{"id": "resident", "description": "Local resident"}],
                    "jobs_to_be_done": [
                        {
                            "id": "find_access",
                            "persona_id": "resident",
                            "description": "Find free internet and hours at public libraries",
                        }
                    ],
                    "requirements": [
                        {
                            "id": "evidence",
                            "job_id": "find_access",
                            "description": "Show opening hours and internet availability with public evidence",
                        }
                    ],
                    "constraints": ["Public read-only sources"],
                    "non_goals": ["Private patron records"],
                    "definition_of_done": [
                        {
                            "id": "library_count",
                            "metric": "libraries_with_access_and_hours",
                            "operator": ">=",
                            "target": 1,
                            "basis": "proposed",
                            "rationale": "One library proves the end-to-end example.",
                            "feasibility": "One record fits the synthetic test budget and run time.",
                        },
                        {
                            "id": "evidence_integrity",
                            "metric": "values_without_evidence",
                            "operator": "=",
                            "target": 0,
                            "basis": "proposed",
                            "rationale": "Every exported value needs evidence.",
                            "feasibility": "A small synthetic case can verify every value in its run time.",
                        },
                    ],
                    "authority_policy": {
                        "jurisdiction": "Example City",
                        "trusted_publishers": [
                            {
                                "kind": "city library office",
                                "tier": "primary",
                                "jurisdiction": "Example City",
                                "domains": ["libraries.example.test"],
                                "rationale": "Official directory for this synthetic city",
                            }
                        ],
                        "unknown_source_action": "review",
                    },
                }
            ],
            "phase2.factors": [
                {
                    "factors": [
                        {
                            "id": "access",
                            "label": "Public access",
                            "description": "Availability and opening times",
                            "kind": "conceptual",
                            "evidence": [],
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
                                {
                                    "id": "internet_access",
                                    "label": "Internet access",
                                    "level": 1,
                                    "critic_label": "Good-Exclusive",
                                }
                            ],
                        }
                    ]
                }
            ],
            "phase2.schema": [
                {
                    "primary_class": "library",
                    "classes": [
                        {
                            "id": "library",
                            "label": "Library",
                            "label_plural": "Libraries",
                            "description": "Public library location",
                            "title_property": "name",
                            "identifier_property": "name",
                            "aligned_to": "https://schema.org/Library",
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
                            "description": "Displayed branch name",
                            "aligned_to": "https://schema.org/name",
                        },
                        {
                            "id": "free_internet",
                            "label": "Free internet",
                            "domain": "library",
                            "datatype": "boolean",
                            "dod": True,
                            "order": 1,
                            "description": "Free internet is offered",
                            "aligned_to": None,
                        },
                        {
                            "id": "opening_hours",
                            "label": "Opening hours",
                            "domain": "library",
                            "datatype": "string",
                            "dod": True,
                            "order": 2,
                            "description": "Displayed public hours",
                            "aligned_to": "https://schema.org/openingHours",
                        },
                    ],
                    "relations": [],
                    "rules": [],
                    "source_classes": [{"id": "city_directory", "label": "City library directory"}],
                }
            ],
            "phase2.dod_queries": [
                {
                    "queries": [
                        {
                            "criterion_id": "library_count",
                            "aggregate": "count_entities_with_properties",
                            "class_id": "library",
                            "properties": ["name", "free_internet", "opening_hours"],
                            "conditions": [
                                {"property": "free_internet", "operator": "eq", "value": True}
                            ],
                            "operator": ">=",
                            "target": 1,
                        },
                        {
                            "criterion_id": "evidence_integrity",
                            "aggregate": "count_values_without_evidence",
                            "operator": "=",
                            "target": 0,
                        },
                    ]
                }
            ],
            "phase4.local_scope": [
                {
                    "global_requirement_ids": ["evidence"],
                    "local_definition_of_done": [
                        {"metric": "libraries_with_access_and_hours", "operator": ">=", "target": 1}
                    ],
                    "extraction_method": "dom",
                    "validation_rules": ["Only literal observed cells"],
                    "rate_limit_per_minute": 6,
                    "budget_usd": 0,
                    "target_volume": 3,
                    "steps": [
                        {
                            "id": "read_directory",
                            "description": "Read directory table",
                            "starting_mode": "S1",
                            "allowed_modes": ["S1"],
                            "observation_channel": "text_structure",
                            "risk_tier": "SAFE",
                            "termination_predicate": "One evidenced row or no more rows",
                        }
                    ],
                }
            ],
            "phase5.map_columns": [
                {
                    "class_id": "library",
                    "columns": [
                        {"header": "Branch", "property_id": "name"},
                        {"header": "Free internet", "property_id": "free_internet"},
                        {"header": "Hours", "property_id": "opening_hours"},
                    ],
                }
            ],
        }
    )
