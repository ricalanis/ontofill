from datetime import UTC, datetime

from ontofill_scrape import SearchResult

from ontofill.inference import RecordedDecisionClient
from ontofill.lake import FileLake
from ontofill.phases.p3_fanout.phase import discover_objective
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
