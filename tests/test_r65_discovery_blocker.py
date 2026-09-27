"""R65 query vocabulary, review, blocked-download, and static-child regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontofill.inference import RecordedDecisionClient
from ontofill.lake import FileLake
from ontofill.phase_loop import LoopBudget
from ontofill.phases.p3_fanout.discovery_loop import (
    DiscoveryLoop,
    _dataset_file_suffix,
    _dataset_index_links,
    _download_access_blocker,
)
from ontofill.phases.p3_fanout.leads import Lead, LeadContext, LeadProvider
from tests.r17_helpers import SyntheticParseExecutor
from tests.test_discovery_loop import (
    POLICY,
    FakeCapture,
    _library_case,
)

_CHILD = "https://data.example.test/datasets/branches.csv"
_ROOT = "https://data.example.test/"
_OFFICIAL_MIRROR = "https://mirror.example.test/releases/ocds-contracts.csv"
_PROVENANCE = {
    "backend": "recorded",
    "model": "synthetic",
    "at": "2026-09-27T00:00:00Z",
}
_DATA_POLICY = {
    **POLICY,
    "trusted_publishers": [
        publisher
        for publisher in POLICY["trusted_publishers"]
        if "data.example.test" in publisher["domains"]
    ],
}


class _RootProvider(LeadProvider):
    name = "synthetic_catalog"

    def leads(self, context: LeadContext) -> list[Lead]:
        self._attempt("catalog query", "ok", 1)
        return [
            Lead(
                _ROOT,
                "Official open data catalog",
                "Public branch records and datasets",
                self.name,
                "catalog query",
                tuple(query.property_id for query in context.queries),
            )
        ]


def _loop(tmp_path: Path, provider: LeadProvider, pages: dict[str, str], capture=None):
    lake = FileLake(tmp_path / "lake")
    capture = capture or FakeCapture(lake, pages)
    loop = DiscoveryLoop(
        [provider],
        capture=capture,
        lake=lake,
        run_id="mock-r65-discovery",
        provenance=_PROVENANCE,
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
        parse_executor=SyntheticParseExecutor(),
        max_captures_per_iteration=3,
    )
    return loop, capture


def _contract_ontology() -> dict:
    return {
        "primary_class": "contract",
        "classes": [
            {
                "id": "contract",
                "label": "Public contract",
                "label_plural": "Public contracts",
                "identifier_property": "contract_id",
                "title_property": "contract_title",
            }
        ],
        "properties": [
            {"id": "contract_id", "label": "Contract identifier", "domain": "contract"},
            {"id": "contract_title", "label": "Contract title", "domain": "contract"},
            {"id": "award_value", "label": "Award value", "domain": "contract", "dod": True},
        ],
        "relations": [],
    }


def test_fallback_query_uses_case_ontology_without_a_fixed_subject_standard():
    loop = object.__new__(DiscoveryLoop)
    queries = loop._plan_queries(
        RecordedDecisionClient({}),
        "Find public procurement contract awards.",
        _contract_ontology(),
        {"jurisdiction": "Example region", "trusted_publishers": []},
        ["award_value"],
        1,
        set(),
    )
    assert queries
    assert "contract" in queries[0].text.casefold()
    assert "https://" not in queries[0].text.casefold()


def test_jsonl_release_resources_are_recognized_without_accepting_arbitrary_gzip():
    assert _dataset_file_suffix("https://data.example.test/release.jsonl.gz") == ".jsonl.gz"
    assert _dataset_file_suffix("https://data.example.test/archive.tar.gz") is None
    links = _dataset_index_links(
        {
            "url": "https://data.example.test/",
            "landing_url": "https://data.example.test/",
            "property_ids": ["opening_hours"],
        },
        {
            "links": [
                {"url": "/datasets/branches.jsonl.gz", "text": "Download branch records"},
                {"url": "/datasets/archive.gz", "text": "Download branch records"},
            ]
        },
        {
            "primary_class": "branch",
            "classes": [
                {
                    "id": "branch",
                    "label": "Library branch",
                    "label_plural": "Library branches",
                    "identifier_property": "branch_id",
                    "title_property": "branch_name",
                }
            ],
            "properties": [
                {"id": "opening_hours", "label": "Opening hours"},
                {"id": "branch_id", "label": "Branch ID"},
                {"id": "branch_name", "label": "Branch name"},
            ],
        },
    )
    assert [item["url"] for item in links] == [
        "https://data.example.test/datasets/branches.jsonl.gz"
    ]


def test_metadata_get_plus_file_403_requires_explicit_post_token_evidence():
    candidate = {
        "url": _CHILD,
        "dataset_link": True,
        "parent_metadata_gets": [
            {
                "url": "https://data.example.test/api/metadata.json",
                "method": "GET",
                "content_type": "application/json",
            }
        ],
    }
    unresolved = _download_access_blocker(candidate, 403)
    assert unresolved is not None
    assert unresolved["code"] == "download_control_unresolved"
    assert unresolved["post_attempted"] is False

    confirmed = _download_access_blocker({**candidate, "parent_post_token_required": True}, 403)
    assert confirmed is not None
    assert confirmed["code"] == "download_requires_antibot_post"
    assert confirmed["post_antibot_token_requirement_observed"] is True


def test_metadata_json_and_explicit_post_token_requirement_emit_blocker_without_retry(
    tmp_path: Path,
):
    ontology = _library_case(tmp_path, _DATA_POLICY)
    root_page = (
        "<html><body><h1>Official open data catalog</h1>"
        "<p>Downloading branch records requires POST with an anti-bot token.</p>"
        '<a href="/datasets/branches.csv">Branch records CSV dataset</a></body></html>'
    )
    lake = FileLake(tmp_path / "lake")
    base_capture = FakeCapture(lake, {_ROOT: root_page, _CHILD: "<html>denied</html>"})

    def capture(url: str, **kwargs) -> dict:
        result = base_capture(url, **kwargs)
        if url == _ROOT:
            result["network_requests"] = [
                {
                    "url": "https://data.example.test/api/metadata.json",
                    "method": "GET",
                    "resource_type": "xhr",
                    "status": 200,
                    "content_type": "application/json",
                }
            ]
        elif url == _CHILD:
            result.update(status=403, content_type="text/csv", capture_reason="http_403")
        return result

    loop, _ = _loop(tmp_path, _RootProvider(), {}, capture)
    with pytest.raises(ValueError, match="confirmed no source candidates"):
        loop.discover_sources(
            tmp_path,
            ontology,
            RecordedDecisionClient({}),
            gaps=("opening_hours",),
        )

    assert base_capture.calls == [_ROOT, _CHILD]
    surface = json.loads(
        (tmp_path / "03-fanout/surface-map/leads.json").read_text(encoding="utf-8")
    )
    candidate = next(item for item in surface["candidates"] if item["url"] == _CHILD)
    assert candidate["access_blocker"]["code"] == "download_requires_antibot_post"
    assert candidate["capture_reason"] == "download_requires_antibot_post"
    assert candidate["capture_attempts"] == 1
    assert candidate["access_blocker"]["post_attempted"] is False
    blocker_trace = next(
        step
        for step in loop.trace
        if step.get("requested", {}).get("tool") == "p3.source.access_blocker"
    )
    assert blocker_trace["executed"]["method"] == "GET"
    assert blocker_trace["executed"]["post_attempted"] is False


def test_static_follow_child_is_filtered_before_source_review_or_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ontology = _library_case(tmp_path, _DATA_POLICY)
    root_page = "<html><body><h1>Official open data catalog</h1>Catalog page.</body></html>"
    loop, capture = _loop(
        tmp_path,
        _RootProvider(),
        {_ROOT: root_page},
    )

    def favicon_child(*_args, **_kwargs):
        return [
            {
                "url": "https://outside.example.test/favicon.ico",
                "title": "Branch records dataset icon",
                "link_text": "Branch records dataset",
                "follow_kind": "dataset",
            }
        ]

    monkeypatch.setattr(
        "ontofill.phases.p3_fanout.discovery_loop._follow_portal_children",
        favicon_child,
    )
    with pytest.raises(ValueError, match="confirmed no source candidates"):
        loop.discover_sources(
            tmp_path,
            ontology,
            RecordedDecisionClient({}),
            gaps=("opening_hours",),
        )

    assert capture.calls == [_ROOT]
    assert not list((tmp_path / "03-fanout/sources").glob("*/candidate.json"))
    skipped = next(
        step
        for step in loop.trace
        if step.get("requested", {}).get("tool") == "lead.skip"
        and step.get("requested", {}).get("url") == "https://outside.example.test/favicon.ico"
    )
    assert skipped["evaluated"]["before_review_or_queue"] is True
    assert "POST" not in " ".join(capture.calls)


def test_open_contracting_mirror_link_becomes_secondary_review_candidate(tmp_path: Path):
    ontology = _library_case(tmp_path, _DATA_POLICY)
    root_page = (
        "<html><body><h1>Official open data catalog</h1>"
        f'<a href="{_OFFICIAL_MIRROR}">Open Contracting data standard CSV dataset</a>'
        "</body></html>"
    )
    loop, capture = _loop(tmp_path, _RootProvider(), {_ROOT: root_page})
    with pytest.raises(ValueError, match="confirmed no source candidates"):
        loop.discover_sources(
            tmp_path,
            ontology,
            RecordedDecisionClient({}),
            gaps=("opening_hours",),
        )

    assert capture.calls == [_ROOT]
    surface = json.loads(
        (tmp_path / "03-fanout/surface-map/leads.json").read_text(encoding="utf-8")
    )
    mirror = next(item for item in surface["leads"] if item["url"] == _OFFICIAL_MIRROR)
    assert mirror["source_review_required"] is True
    assert mirror["authority_tier_suggestion"] == "secondary"
    assert mirror["source_role"] == "secondary_cross_check"
    assert mirror["source_state"] == "pending"
    packet = next(
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "03-fanout/sources").glob("*/candidate.json")
        if json.loads(path.read_text(encoding="utf-8"))["url"] == _OFFICIAL_MIRROR
    )
    assert packet["authority"] == "review"
    assert packet["authority_tier"] == "unknown"
