"""Synthetic checks for generic authority hierarchy and matrix tiers."""

from __future__ import annotations

import json

from ontofill.phases.p3_fanout.authority import (
    authority_matrix_tier,
    government_level_in_scope,
)
from ontofill.phases.p3_fanout.discovery_loop import (
    _captured_matrix_assessment,
    authority_tier,
)


def _policy(*, root_level: str = "national_federal", descendants: bool = True) -> dict:
    return {
        "schema_version": "1",
        "jurisdiction": "Example Republic",
        "jurisdiction_hierarchy": {
            "root_level": root_level,
            "include_descendants": descendants,
        },
        "trusted_publishers": [],
        "authority_matrix": [
            {
                "source_class": "executive_agency",
                "government_levels": ["municipal"],
                "channel_types": ["registries"],
                "default_tier": "secondary",
            },
            {
                "source_class": "independent_regulator",
                "government_levels": ["national_federal"],
                "channel_types": ["apis"],
                "default_tier": "primary",
            },
        ],
        "recall_coverage": {
            "government_levels": ["national_federal", "state_provincial", "municipal"],
            "channel_types": ["registries", "apis"],
            "minimum_independent_publishers_per_property": 3,
        },
        "unknown_source_action": "review",
        "unknown_official_action": "admit_low_tier_flagged",
    }


def test_jurisdiction_hierarchy_includes_only_configured_descendants() -> None:
    national = _policy()
    assert government_level_in_scope(national, "national_federal")
    assert government_level_in_scope(national, "state_provincial")
    assert government_level_in_scope(national, "municipal")
    assert not government_level_in_scope(national, "unknown")

    narrow = _policy(root_level="state_provincial", descendants=False)
    assert government_level_in_scope(narrow, "state_provincial")
    assert not government_level_in_scope(narrow, "municipal")
    assert not government_level_in_scope(narrow, "national_federal")


def test_authority_matrix_matches_publisher_class_level_and_channel() -> None:
    policy = _policy()
    assert (
        authority_matrix_tier(
            policy,
            source_class="executive_agency",
            government_level="municipal",
            channel_type="registries",
        )
        == "secondary"
    )
    assert (
        authority_matrix_tier(
            policy,
            source_class="executive_agency",
            government_level="municipal",
            channel_type="apis",
        )
        is None
    )
    assert (
        authority_matrix_tier(
            _policy(root_level="state_provincial", descendants=False),
            source_class="executive_agency",
            government_level="municipal",
            channel_type="registries",
        )
        is None
    )


def test_mixed_policy_domains_use_the_least_trusted_tier() -> None:
    policy = _policy()
    policy["trusted_publishers"] = [
        {"kind": "A", "domains": ["records.example.test"], "tier": "secondary"},
        {"kind": "B", "domains": ["records.example.test"], "tier": "low"},
    ]
    assert authority_tier("https://records.example.test/list", policy) == "low"


def test_captured_unknown_official_host_is_admitted_low_and_flagged(tmp_path, monkeypatch) -> None:
    from ontofill.phase_loop import LoopBudget
    from ontofill.sandbox import parse as parse_module
    from tests.r17_helpers import SyntheticParseExecutor
    from tests.test_discovery_loop import (
        FakeVultr,
        StaticProvider,
        _library_case,
        _loop,
    )

    monkeypatch.setattr(parse_module, "DockerParseExecutor", SyntheticParseExecutor)
    url = "https://records.gov.example.test/branches"
    page = (
        "<html><body><h1>Municipal Executive Agency, Example City</h1>"
        "<p>Example City Municipal Government. Official registry for library branches.</p>"
        "<table><thead><tr><th>Branch name</th><th>Opening hours</th>"
        "<th>Free internet</th></tr></thead><tbody>"
        "<tr><td>North Branch</td><td>Weekdays</td><td>Available</td></tr>"
        "</tbody></table></body></html>"
    )
    policy = _policy()
    policy["jurisdiction"] = "Example City"
    policy["authority_matrix"] = [
        {
            "source_class": "executive_agency",
            "government_levels": ["municipal"],
            "channel_types": ["registries"],
            "default_tier": "primary",
        }
    ]
    ontology = _library_case(tmp_path, policy)
    provider = StaticProvider("synthetic", {url: ("opening_hours",)})
    loop, _capture = _loop(
        tmp_path,
        [provider],
        {url: page},
        backend="vultr",
        budget=LoopBudget(max_iterations=2, wall_seconds=60),
    )

    class MatrixDecision(FakeVultr):
        def complete_json(self, purpose: str, prompt: str, schema: dict) -> dict:
            result = super().complete_json(purpose, prompt, schema)
            if purpose == "critic.phase3.capability":
                for verdict in result["verdicts"]:
                    verdict["authority_context"] = {
                        "official": True,
                        "publisher_kind": "municipal executive agency",
                        "publisher_quote": "Municipal Executive Agency, Example City",
                        "jurisdiction_quote": "Example City Municipal Government",
                        "source_class": "executive_agency",
                        "government_level": "municipal",
                        "government_level_quote": "Example City Municipal Government",
                        "channel_type": "registries",
                        "channel_quote": "Official registry for library branches",
                    }
            return result

    objectives = loop.discover_sources(
        tmp_path, ontology, MatrixDecision(), gaps=("opening_hours",)
    )

    selected = next(item for item in objectives["objectives"] if item["source_url"] == url)
    assert selected["authority_tier"] == "low"
    source_dir = tmp_path / "03-fanout/sources" / selected["source_id"]
    candidate = json.loads((source_dir / "candidate.json").read_text())
    assert candidate["authority"] == "auto"
    assert candidate["authority_tier"] == "low"
    assert candidate["publisher_of_record"]["tier"] == "low"
    assert candidate["publisher_of_record"]["basis"] == "captured_page"
    assert "flagged:" in candidate["authority_reason"]
    assert not (source_dir / "APPROVAL_PENDING.md").exists()

    discovery = json.loads((tmp_path / "03-fanout/surface-map/discovery.json").read_text())
    coverage = discovery["rounds"][-1]["coverage"]["opening_hours"]
    assert coverage["required"] == 1
    assert coverage["target"] == 3
    assert coverage["shortfall"] == 2
    assert not coverage["target_met"]


def test_government_looking_host_without_captured_official_signal_stays_review(
    tmp_path, monkeypatch
) -> None:
    from ontofill.phase_loop import LoopBudget
    from ontofill.sandbox import parse as parse_module
    from tests.r17_helpers import SyntheticParseExecutor
    from tests.test_discovery_loop import FakeVultr, StaticProvider, _library_case, _loop

    monkeypatch.setattr(parse_module, "DockerParseExecutor", SyntheticParseExecutor)
    url = "https://records.gov.example.test/branches"
    page = (
        "<html><body><h1>Community library directory, Example City</h1>"
        "<p>Example City. Community-maintained branch listing.</p>"
        "<table><thead><tr><th>Branch name</th><th>Opening hours</th>"
        "<th>Free internet</th></tr></thead><tbody>"
        "<tr><td>North Branch</td><td>Weekdays</td><td>Available</td></tr>"
        "</tbody></table></body></html>"
    )
    policy = _policy()
    policy["jurisdiction"] = "Example City"
    ontology = _library_case(tmp_path, policy)
    provider = StaticProvider("synthetic", {url: ("opening_hours",)})
    loop, _capture = _loop(
        tmp_path,
        [provider],
        {url: page},
        backend="vultr",
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
    )

    class UnverifiedDecision(FakeVultr):
        def complete_json(self, purpose: str, prompt: str, schema: dict) -> dict:
            result = super().complete_json(purpose, prompt, schema)
            if purpose == "critic.phase3.capability":
                for verdict in result["verdicts"]:
                    verdict["authority_context"] = {
                        "official": True,
                        "publisher_kind": "community directory",
                        "publisher_quote": "Community library directory, Example City",
                        "jurisdiction_quote": "Example City",
                        "source_class": None,
                        "government_level": None,
                        "government_level_quote": None,
                        "channel_type": "registries",
                        "channel_quote": "Community-maintained branch listing",
                    }
            return result

    objectives = loop.discover_sources(
        tmp_path, ontology, UnverifiedDecision(), gaps=("opening_hours",)
    )
    selected = next(item for item in objectives["objectives"] if item["source_url"] == url)
    assert selected["authority_tier"] == "unknown"
    source_dir = tmp_path / "03-fanout/sources" / selected["source_id"]
    candidate = json.loads((source_dir / "candidate.json").read_text())
    assert candidate["authority"] == "review"
    assert (source_dir / "APPROVAL_PENDING.md").exists()


def test_captured_official_publisher_outside_hierarchy_requires_review() -> None:
    candidate = {"url": "https://records.example.test/", "authority": "review"}
    policy = _policy(root_level="municipal", descendants=False)
    policy["jurisdiction"] = "Example City"
    context = {
        "page_text": (
            "Municipal Executive Agency, Example City. "
            "Example City Municipal Government. National Government of Example City. "
            "Official registry."
        )
    }
    assessment = {
        "official": True,
        "publisher_kind": "municipal executive agency",
        "publisher_quote": "Municipal Executive Agency, Example City",
        "jurisdiction_quote": "Example City Municipal Government",
        "source_class": "executive_agency",
        "government_level": "national_federal",
        "government_level_quote": "National Government of Example City",
        "channel_type": "registries",
        "channel_quote": "Official registry",
    }

    status, reason = _captured_matrix_assessment(
        candidate,
        policy,
        assessment,
        context,
        {"kind": "listing"},
    )

    assert status == "review"
    assert "outside the configured government-level hierarchy" in reason
    assert candidate["authority"] == "review"


def test_uncertain_official_matrix_mapping_is_admitted_low_and_flagged() -> None:
    candidate = {"url": "https://agency.example.test/registry", "authority": "review"}
    policy = _policy()
    policy["jurisdiction"] = "Example City"
    policy["trusted_publishers"] = [
        {
            "kind": "municipal executive agency",
            "tier": "primary",
            "domains": ["agency.example.test"],
        }
    ]
    context = {
        "page_text": (
            "Municipal Executive Agency, Example City. "
            "Example City Municipal Government. Official registry."
        )
    }
    assessment = {
        "official": True,
        "publisher_kind": "municipal executive agency",
        "publisher_quote": "Municipal Executive Agency, Example City",
        "jurisdiction_quote": "Example City Municipal Government",
        "source_class": None,
        "government_level": "municipal",
        "government_level_quote": "Example City Municipal Government",
        "channel_type": "registries",
        "channel_quote": "Official registry",
    }

    status, reason = _captured_matrix_assessment(
        candidate,
        policy,
        assessment,
        context,
        {"kind": "listing"},
    )

    assert status == "admitted"
    assert candidate["authority"] == "auto"
    assert candidate["authority_tier"] == "low"
    assert candidate["publisher_of_record"]["tier"] == "low"
    assert "flagged:" in reason
