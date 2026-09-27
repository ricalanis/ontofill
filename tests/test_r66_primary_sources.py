"""R66: primary publishers lead search and satisfy default coverage."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from ontofill.inference import RecordedDecisionClient
from ontofill.sandbox import parse as parse_module
from tests.r17_helpers import SyntheticParseExecutor
from tests.test_discovery_loop import (
    BRIEF,
    PAGE,
    POLICY,
    FakeVultr,
    StaticProvider,
    _library_case,
    _loop,
    _policy_with_recall_minimum,
    _require_two_independent_publishers,
)


@pytest.fixture(autouse=True)
def synthetic_parse_pod(monkeypatch):
    monkeypatch.setattr(parse_module, "DockerParseExecutor", SyntheticParseExecutor)


def test_first_query_batch_starts_with_trusted_primary_namespaces(tmp_path) -> None:
    ontology = _library_case(tmp_path)
    loop, _capture = _loop(tmp_path, [StaticProvider("synthetic", {})], {})

    queries = loop._plan_queries(
        RecordedDecisionClient({}),
        BRIEF.read_text(encoding="utf-8"),
        ontology,
        deepcopy(POLICY),
        ["opening_hours"],
        1,
        set(),
    )

    assert len(queries) >= 2
    assert queries[0].text.startswith("site:libraries.example.test ")
    assert queries[1].text.startswith("site:data.example.test ")


def test_primary_index_satisfies_acceptance_when_recall_target_is_higher(tmp_path) -> None:
    policy = _policy_with_recall_minimum(POLICY, 2)
    ontology = _library_case(tmp_path, policy)
    prd_path = tmp_path / "01-scope/prd.json"
    prd = json.loads(prd_path.read_text(encoding="utf-8"))
    prd["constraints"].append("A primary publisher alone is sufficient for confirmation.")
    prd_path.write_text(json.dumps(prd), encoding="utf-8")
    index_url = "https://libraries.example.test/"
    listing_url = "https://libraries.example.test/branches"
    properties = tuple(item["id"] for item in ontology["properties"] if item.get("dod"))
    provider = StaticProvider("synthetic", {listing_url: properties})
    authority_title = "City Library Office (Municipal Government of Example City) — Open Data lists"
    pages = {
        index_url: PAGE.format(title=authority_title),
        listing_url: PAGE.format(title=authority_title),
    }
    loop, capture = _loop(tmp_path, [provider], pages, backend="vultr")

    loop.discover_sources(tmp_path, ontology, FakeVultr())

    assert any(url.startswith("https://libraries.example.test/") for url in capture.calls)
    assert loop.result is not None and loop.result.stop_reason == "checks_passed"
    assert provider.calls == 1

    ledger = json.loads((tmp_path / "03-fanout/surface-map/discovery.json").read_text())
    coverage = ledger["rounds"][-1]["coverage"]
    assert coverage
    assert "A primary publisher alone is sufficient for confirmation." in prd["constraints"]
    assert all(
        entry["required"] == 1 and entry["target"] == 2 and entry["publishers"] == 1
        for entry in coverage.values()
    )


def test_approved_dod_requires_two_independent_publishers(tmp_path) -> None:
    ontology = _library_case(tmp_path, _policy_with_recall_minimum(POLICY, 1))
    _require_two_independent_publishers(tmp_path)
    url = "https://libraries.example.test/branches"
    provider = StaticProvider("synthetic", {url: ("name", "free_internet", "opening_hours")})
    authority_title = "City Library Office (Municipal Government of Example City) — Open Data lists"
    loop, _capture = _loop(tmp_path, [provider], {url: PAGE.format(title=authority_title)})

    loop.discover_sources(tmp_path, ontology, FakeVultr())

    assert loop.result is not None and loop.result.stop_reason == "max_iterations"
    assert any("free_internet: 1/2 approved publisher" in item for item in loop.result.objections)
    ledger = json.loads((tmp_path / "03-fanout/surface-map/discovery.json").read_text())
    coverage = ledger["rounds"][-1]["coverage"]
    assert all(entry["required"] == 2 and entry["publishers"] == 1 for entry in coverage.values())


def test_explicit_property_corroboration_does_not_raise_other_floors(tmp_path) -> None:
    ontology = _library_case(tmp_path, _policy_with_recall_minimum(POLICY, 1))
    _require_two_independent_publishers(tmp_path, "Opening hours")
    url = "https://libraries.example.test/branches"
    properties = ("name", "free_internet", "opening_hours")
    provider = StaticProvider("synthetic", {url: properties})
    authority_title = "City Library Office (Municipal Government of Example City) — Open Data lists"
    loop, _capture = _loop(tmp_path, [provider], {url: PAGE.format(title=authority_title)})

    loop.discover_sources(tmp_path, ontology, FakeVultr())

    assert loop.result is not None and loop.result.stop_reason == "max_iterations"
    assert any("opening_hours: 1/2 approved publisher" in item for item in loop.result.objections)
    assert all(
        "free_internet:" not in item and "name:" not in item for item in loop.result.objections
    )
    ledger = json.loads((tmp_path / "03-fanout/surface-map/discovery.json").read_text())
    coverage = ledger["rounds"][-1]["coverage"]
    assert coverage["opening_hours"]["required"] == 2
    assert coverage["free_internet"]["required"] == 1
