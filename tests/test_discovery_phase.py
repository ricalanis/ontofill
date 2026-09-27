from datetime import UTC, datetime

from ontofill_scrape import SearchResult

from ontofill.contracts import validate_document
from ontofill.inference import RecordedDecisionClient
from ontofill.lake import FileLake
from ontofill.phases.p3_fanout.phase import _choose, _queries, discover_objective
from ontofill.phases.p3_fanout.search import parse_search_results
from ontofill.sandbox import parse_bronze
from tests.genericity.fixtures.discovery import discovery_case
from tests.r17_helpers import SyntheticParseExecutor


class SyntheticSearch:
    def __init__(self) -> None:
        self.queries = []
        self.capture_key = None

    def search(self, query: str):
        self.queries.append(query)
        return [SearchResult("https://directory.example.test/rooms", "Public room directory")]


def test_live_search_planning_retries_invalid_typed_outputs() -> None:
    decision = RecordedDecisionClient(
        {
            "phase3.plan_search": [
                {"queries": ["no"]},
                {"queries": ["public room directory Example City"]},
            ],
            "phase3.select_sources": [
                {"indexes": [1], "reason": "outside the captured candidate list"},
                {"indexes": [0], "reason": "matches the requested gap"},
            ],
        }
    )
    decision.backend = "vultr"
    assert _queries("Find public rooms in Example City", ("Opening hours",), decision) == (
        "public room directory Example City",
    )

    candidate = SearchResult(
        "https://directory.example.test/rooms", "Public room directory", "Public room data"
    )
    entry = (
        candidate,
        {"expected_contribution": 1.0, "source_type": "catalog"},
        {"authority": "auto"},
        True,
    )
    assert _choose([entry], ("opening_hours",), decision, 1) == [entry]
    assert len(decision.calls) == 4
    for failed, retry in ((0, 1), (2, 3)):
        reason = str(decision.call_log[failed]["reason"])
        assert decision.call_log[failed]["status"] == "invalid_response"
        assert reason in decision.calls[retry][1]


def test_legacy_injected_search_scopes_exhausted_decisions_to_failed_steps(tmp_path) -> None:
    ontology = discovery_case(tmp_path)
    search = SyntheticSearch()
    search.trace = []
    search.run_id = "mock-p3-validation-fallback"
    decision = RecordedDecisionClient(
        {
            "phase3.plan_search": [{"queries": ["x"]}] * 3,
            "phase3.select_sources": [{"indexes": [1], "reason": "not a captured candidate"}] * 3,
        }
    )
    decision.backend = "vultr"

    result = discover_objective(tmp_path, ontology, decision, search)

    assert result["objectives"]
    assert len(search.queries) == 1
    assert "Example City" in search.queries[0]
    assert len(decision.calls) == 6
    assert [step["requested"]["tool"] for step in search.trace] == [
        "phase3.plan_search",
        "phase3.select_sources",
    ]
    assert all(step["evaluated"]["reason"] == "ModelValidationExhausted" for step in search.trace)
    for step in search.trace:
        validate_document("trace-step", step)


def test_discovery_uses_brief_and_records_selected_result(tmp_path) -> None:
    ontology = discovery_case(tmp_path)
    search = SyntheticSearch()
    decision = RecordedDecisionClient({})
    document = discover_objective(tmp_path, ontology, decision, search)
    assert "Example City" in search.queries[0]
    assert document["objectives"][0]["source_url"] == "https://directory.example.test/rooms"
    assert (tmp_path / "03-fanout/objectives.yaml").exists()
    assert decision.calls == []
    assert discover_objective(tmp_path, ontology, decision, search) == document
    assert len(search.queries) == 1


def test_search_result_parser_ranks_live_catalog_cards_from_brief(tmp_path) -> None:
    html = (
        "<article><h3>Example City contracts</h3><p>Public awards and suppliers</p>"
        '<a href="/en/publication/12">Details</a></article>'
        '<article><h3>Other Town purchases</h3><a href="/en/publication/13">Details</a></article>'
    )
    lake = FileLake(tmp_path / "lake")
    base_url = "https://catalog.example.test/"
    key = lake.put_bytes(html.encode(), {"content_type": "text/html", "url": base_url})
    parsed = parse_bronze(
        lake,
        key,
        format="html",
        base_url=base_url,
        run_id="mock-search-parser",
        source_id="search-provider",
        tdd_path="03-fanout/search-policy.json",
        generated_by={
            "backend": "recorded",
            "model": "synthetic-search-test",
            "at": datetime.now(UTC).isoformat(),
        },
        executor=SyntheticParseExecutor(),
    )
    assert parse_search_results(parsed.links, "Example City supplier data", base_url=base_url) == (
        SearchResult(
            "https://catalog.example.test/en/publication/12",
            "Example City contracts",
            "Example City contracts Public awards and suppliers Details",
        ),
    )
