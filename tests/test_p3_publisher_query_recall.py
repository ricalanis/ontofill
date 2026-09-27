"""Approved publishers and captured synonym headers expand source recall safely."""

from __future__ import annotations

import json

from ontofill.inference import RecordedDecisionClient
from ontofill.phases.p3_fanout.discovery_loop import DiscoveryLoop
from tests.test_discovery_loop import StaticProvider, _library_case, _loop


def test_query_plan_seeds_approved_publisher_kind_and_domain(tmp_path) -> None:
    ontology = _library_case(tmp_path)
    policy = json.loads((tmp_path / "01-scope/prd.json").read_text())["authority_policy"]
    policy["trusted_publishers"].append(
        {
            "kind": "Civic records exchange",
            "tier": "primary",
            "domains": ["records.example.test"],
            "rationale": "synthetic approved source",
        }
    )
    loop, _capture = _loop(tmp_path, [StaticProvider("synthetic", {})], {})

    queries = loop._plan_queries(
        RecordedDecisionClient({}),
        "Find public library branches.",
        ontology,
        policy,
        ["name"],
        1,
        set(),
    )

    assert any(
        "Civic records exchange" in query.text and "site:records.example.test" in query.text
        for query in queries
    )


def test_captured_synonym_header_proves_capability_but_missing_quote_does_not() -> None:
    candidate = {"url": "https://records.example.test/list", "capture_key": "sha256:" + "a" * 64}
    context = {
        "page_text": "",
        "table_headers": [["Identifier", "Display name"]],
        "listing_row_count": 2,
    }
    proposed = {
        "kind": "listing",
        "access_path_quote": "Display name",
        "record_granularity": "entity_records",
    }
    accepted, reason = DiscoveryLoop._validate_access_path(
        None,
        candidate,
        context,
        proposed,
        authority_verdict="authoritative",
        critic_reason="The captured listing can provide the selected title field.",
        property_quote="Display name",
        wanted={"public", "title"},
        primary_target=True,
        identity_tokens={"identifier"},
        class_tokens={"library"},
    )
    assert reason is None
    assert accepted is not None and accepted["property_quote"] == "Display name"

    refused, reason = DiscoveryLoop._validate_access_path(
        None,
        candidate,
        context,
        proposed,
        authority_verdict="authoritative",
        critic_reason="unverified",
        property_quote="Invented field",
        wanted={"public", "title"},
        primary_target=True,
        identity_tokens={"identifier"},
        class_tokens={"library"},
    )
    assert refused is None and "not present" in reason
