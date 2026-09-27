"""A retrieval route counts only when its rows represent ontology entities."""

from __future__ import annotations

import json

import pytest

from ontofill.phases.p3_fanout.discovery_loop import NoConfirmedSources
from tests.test_r35_p3_capability import ONTOLOGY, POLICY, CapabilityCritic, _loop


class TableCritic(CapabilityCritic):
    def complete_json(self, purpose: str, prompt: str, schema: dict) -> dict:
        result = super().complete_json(purpose, prompt, schema)
        if purpose == "critic.phase3.capability":
            for verdict in result["verdicts"]:
                verdict["provides"] = True
                verdict["access_path"] = {
                    "kind": "listing",
                    "access_path_quote": "Establishment date",
                    "property_quote": "Establishment date",
                    "record_granularity": "entity_records",
                    "granularity_quote": "Establishment date",
                    "granularity_reason": "Rows represent records.",
                }
                verdict["reason"] = "Captured listing exposes record fields."
        return result


class AggregateFormCritic(CapabilityCritic):
    def complete_json(self, purpose: str, prompt: str, schema: dict) -> dict:
        result = super().complete_json(purpose, prompt, schema)
        if purpose == "critic.phase3.capability":
            for verdict in result["verdicts"]:
                if verdict["access_path"] is not None:
                    verdict["access_path"].update(
                        record_granularity="aggregate_statistics",
                        granularity_quote="Record identifier",
                        granularity_reason="The form searches aggregate counts, not individual records.",
                    )
        return result


def test_aggregate_table_cannot_cover_primary_entity_properties(tmp_path) -> None:
    url = "https://registry.synthetic.test/statistics"
    page = (
        "<html><body><h1>Record statistics</h1><table>"
        "<tr><th>Region</th><th>Establishment date</th><th>Record count</th></tr>"
        "<tr><td>North</td><td>2022</td><td>12</td></tr>"
        "<tr><td>South</td><td>2023</td><td>18</td></tr>"
        "</table></body></html>"
    )
    loop = _loop(tmp_path, page, url)

    with pytest.raises(NoConfirmedSources):
        loop.discover_sources(
            tmp_path,
            ONTOLOGY,
            TableCritic(),
            gaps=("establishment_date",),
        )
    assert any("granularity" in str(step.get("evaluated", {})).lower() for step in loop.trace)


def test_entity_table_covers_primary_entity_property(tmp_path) -> None:
    url = "https://registry.synthetic.test/records"
    page = (
        "<html><body><h1>Individual records</h1><table>"
        "<tr><th>Record identifier</th><th>Establishment date</th></tr>"
        "<tr><td>R-1</td><td>2022-01-01</td></tr>"
        "<tr><td>R-2</td><td>2023-01-01</td></tr>"
        "</table></body></html>"
    )
    loop = _loop(tmp_path, page, url)

    result = loop.discover_sources(
        tmp_path,
        ONTOLOGY,
        TableCritic(),
        gaps=("establishment_date",),
    )

    path = result["objectives"][0]["access_path"]["establishment_date"]
    assert path["record_granularity"] == "entity_records"
    assert path["granularity_quote"] == "Record identifier"


def test_critic_aggregate_verdict_rejects_even_an_identity_search_field(tmp_path) -> None:
    url = "https://registry.synthetic.test/summary-search"
    page = (
        "<html><body><form role='search'>"
        "<label>Record identifier<input type='search' name='record_id'></label>"
        "<button>Find counts</button></form></body></html>"
    )
    loop = _loop(tmp_path, page, url)

    with pytest.raises(NoConfirmedSources):
        loop.discover_sources(
            tmp_path,
            ONTOLOGY,
            AggregateFormCritic(),
            gaps=("record_identifier",),
        )


def test_legacy_capability_cache_is_reconsidered(tmp_path) -> None:
    url = "https://registry.synthetic.test/records"
    page = (
        "<html><body><table>"
        "<tr><th>Record identifier</th><th>Establishment date</th></tr>"
        "<tr><td>R-1</td><td>2022-01-01</td></tr>"
        "<tr><td>R-2</td><td>2023-01-01</td></tr>"
        "</table></body></html>"
    )
    loop = _loop(tmp_path, page, url)
    first = loop.discover_sources(tmp_path, ONTOLOGY, TableCritic(), gaps=("establishment_date",))
    assert first["objectives"]
    objectives_path = tmp_path / "03-fanout/objectives.json"
    old = json.loads(objectives_path.read_text())
    for objective in old["objectives"]:
        for path in objective["access_path"].values():
            path.pop("record_granularity")
            path.pop("granularity_quote")
            path.pop("granularity_reason")
    objectives_path.write_text(json.dumps(old))
    leads_path = tmp_path / "03-fanout/surface-map/leads.json"
    leads = json.loads(leads_path.read_text())
    for candidate in leads["candidates"]:
        for path in candidate.get("access_path", {}).values():
            path.pop("record_granularity", None)
            path.pop("granularity_quote", None)
            path.pop("granularity_reason", None)
    leads_path.write_text(json.dumps(leads))
    original_capture = loop.capture
    recaptured: list[str] = []

    def capture_again(captured_url: str, **kwargs) -> dict:
        recaptured.append(captured_url)
        return original_capture(captured_url, **kwargs)

    loop.capture = capture_again
    second = loop.discover_sources(tmp_path, ONTOLOGY, TableCritic(), gaps=("establishment_date",))
    assert url in recaptured
    assert (
        second["objectives"][0]["access_path"]["establishment_date"]["record_granularity"]
        == "entity_records"
    )


def test_deterministic_search_targets_inferred_entity_identifier(tmp_path) -> None:
    loop = _loop(tmp_path, "<html></html>", "https://registry.synthetic.test/")

    class RecordedDecision:
        backend = "recorded"

    queries = loop._plan_queries(
        RecordedDecision(),
        "Find public records.",
        ONTOLOGY,
        POLICY,
        ["establishment_date"],
        1,
        set(),
    )

    assert len(queries) == 2  # global discovery plus one trusted-publisher site search
    assert all("Record identifier" in query.text for query in queries)
    assert queries[1].text.startswith("site:registry.synthetic.test ")


def test_unrelated_entity_table_cannot_validate_aggregate_download(tmp_path) -> None:
    url = "https://registry.synthetic.test/landing"
    loop = _loop(tmp_path, "<html></html>", url)
    context = {
        "page_text": "Individual records and summary downloads",
        "forms": [],
        "table_headers": [["Record identifier", "Establishment date"]],
        "listing_row_count": 2,
        "links": [
            {
                "url": "https://registry.synthetic.test/summary.csv",
                "text": "Download summary",
                "title": "",
                "context": "Download summary",
            }
        ],
    }
    path, reason = loop._validate_access_path(
        {"url": url, "capture_key": "sha256:synthetic-page"},
        context,
        {
            "kind": "dataset",
            "access_path_quote": "Download summary",
            "link_index": 0,
            "record_granularity": "entity_records",
            "granularity_quote": "Download summary",
        },
        authority_verdict="authoritative",
        critic_reason="Model guessed that linked rows are individual records.",
        primary_target=True,
        identity_tokens={"identifier", "title"},
        class_tokens={"record"},
    )

    assert path is None
    assert "granularity" in reason
