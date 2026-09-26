from ontofill_scrape import SearchResult

from ontofill.inference import RecordedDecisionClient
from ontofill.phases.p3_fanout.phase import discover_objective
from ontofill.phases.p3_fanout.search import parse_search_results


class SyntheticSearch:
    def __init__(self) -> None:
        self.queries = []
        self.capture_key = None

    def search(self, query: str):
        self.queries.append(query)
        return [
            SearchResult("https://index.example.test/", "General index"),
            SearchResult("https://registry.example.test/suppliers", "Public supplier register"),
        ]


def test_discovery_uses_brief_and_records_selected_result(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Who supplies Example City?", encoding="utf-8")
    search = SyntheticSearch()
    decision = RecordedDecisionClient(
        {"phase3.select_source": [{"index": 1, "reason": "Likely supplier records"}]}
    )
    document = discover_objective(tmp_path, {"version": "1"}, decision, search)
    assert "Example City" in search.queries[0]
    assert document["objectives"][0]["source_url"] == "https://registry.example.test/suppliers"
    assert (tmp_path / "03-fanout/objectives.yaml").exists()
    assert discover_objective(tmp_path, {"version": "1"}, decision, search) == document
    assert len(search.queries) == 1


def test_search_result_parser_ranks_live_catalog_cards_from_brief() -> None:
    html = (
        "<article><h3>Example City contracts</h3><p>Public awards and suppliers</p>"
        '<a href="/en/publication/12">Details</a></article>'
        '<article><h3>Other Town purchases</h3><a href="/en/publication/13">Details</a></article>'
    )
    assert parse_search_results(
        html, "Example City supplier data", base_url="https://catalog.example.test/"
    ) == (
        SearchResult(
            "https://catalog.example.test/en/publication/12",
            "Example City contracts",
            "Example City contracts Public awards and suppliers Details",
        ),
    )
