"""The PRD review shows what an approver is actually deciding about: the authority policy (which publishers the
engine may trust, at which tier, on which domains) and the engine's own open issues with the draft. Live
(run-a3f49f634dce): five denials were about publishers and domains, and the draft's critic flagged a stale entry,
yet the page showed neither."""

import json

from conftest import IDENTITY

PAGE = "/cases/libraries/approvals/01-scope"


def prd_path(cases_dir):
    return cases_dir / "libraries" / "case" / "01-scope" / "prd.json"


def edit_prd(cases_dir, **changes):
    p = prd_path(cases_dir)
    prd = json.loads(p.read_text())
    prd.update(changes)
    p.write_text(json.dumps(prd))


def test_authority_policy_is_on_the_review(client, cases_dir):
    edit_prd(
        cases_dir,
        authority_policy={
            "jurisdiction": "Example City (synthetic)",
            "unknown_source_action": "review",
            "trusted_publishers": [
                {
                    "kind": "city open-data portal",
                    "tier": "primary",
                    "jurisdiction": "Example City",
                    "domains": ["data.example"],
                    "rationale": "stale: cites old.example, which no longer resolves",
                },
                {"kind": "state registry cross-check", "tier": "secondary", "domains": ["registry.example"]},
            ],
        },
    )
    html = client.get(PAGE, headers=IDENTITY).text
    assert "Authority policy" in html and "Trusted publishers (2)" in html
    assert "data.example" in html and "registry.example" in html
    assert "Why: stale: cites old.example" in html
    assert "<b>primary</b>" in html and ">secondary<" in html
    assert "waits for a person's review at a source checkpoint" in html


def test_versioned_policy_shows_the_matrix_and_recall(client, cases_dir):
    edit_prd(
        cases_dir,
        authority_policy={
            "schema_version": "1",
            "jurisdiction": "Example Country",
            "jurisdiction_hierarchy": {"root_level": "national_federal", "include_descendants": True},
            "trusted_publishers": [],
            "authority_matrix": [
                {
                    "source_class": "procurement",
                    "government_levels": ["national_federal", "municipal"],
                    "channel_types": ["open_data", "procurement_portals"],
                    "default_tier": "primary",
                }
            ],
            "recall_coverage": {
                "government_levels": ["national_federal"],
                "channel_types": ["registries"],
                "minimum_independent_publishers_per_property": 2,
            },
            "unknown_source_action": "review",
            "unknown_official_action": "admit_low_tier_flagged",
        },
    )
    html = client.get(PAGE, headers=IDENTITY).text
    assert "from national / federal down" in html
    assert "No trusted publishers listed" in html
    assert "procurement portals" in html and "national / federal, municipal" in html
    assert "at least 2 independent publishers per property" in html
    assert "admitted at low tier and flagged" in html


def test_open_issues_lead_the_review(client, cases_dir):
    issue = "Material defect in authority_policy.trusted_publishers[0]: its rationale cites a dead domain."
    edit_prd(cases_dir, open_issues=[issue])
    html = client.get(PAGE, headers=IDENTITY).text
    assert "The engine flagged 1 open issue in this draft" in html and issue in html
    assert html.index("open issue") < html.index("Definition of done") < html.index("Sign off")


def test_no_issue_block_without_open_issues(client, cases_dir):
    edit_prd(cases_dir, open_issues=[])
    assert "open issue" not in client.get(PAGE, headers=IDENTITY).text
