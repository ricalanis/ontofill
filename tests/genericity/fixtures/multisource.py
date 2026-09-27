"""Synthetic source objectives for recorded R2 execution checks."""

from __future__ import annotations

from copy import deepcopy

SOURCE_CLASSES = [
    {"id": "public_index", "label": "Public index"},
    {"id": "open_data_feed", "label": "Open data feed"},
    {"id": "contract_publication", "label": "Contract publication"},
    {"id": "complete_reference_list", "label": "Complete reference list"},
]

OBJECTIVES = [
    {
        "id": "objective-membership",
        "source_id": "source-membership",
        "source_url": "https://reference.example.test/catalog",
        "source_type": "complete_reference_list",
        "target_fields": ["is_listed"],
        "priority": 1,
        "expected_contribution": 0.25,
    },
    {
        "id": "objective-index",
        "source_id": "source-index",
        "source_url": "https://index.example.test/catalog",
        "source_type": "public_index",
        "target_fields": ["record_id", "title"],
        "priority": 2,
        "expected_contribution": 0.25,
    },
    {
        "id": "objective-json",
        "source_id": "source-json",
        "source_url": "https://data.example.test/catalog",
        "source_type": "open_data_feed",
        "target_fields": ["record_id", "summary"],
        "priority": 3,
        "expected_contribution": 0.25,
    },
    {
        "id": "objective-ocds",
        "source_id": "source-ocds",
        "source_url": "https://publication.example.test/catalog",
        "source_type": "contract_publication",
        "target_fields": ["record_id", "category"],
        "priority": 4,
        "expected_contribution": 0.25,
    },
]


def multisource_objectives() -> list[dict]:
    return deepcopy(OBJECTIVES)
