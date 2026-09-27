"""R56 regressions for trusted portal child following and query vocabulary."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ontofill.inference import RecordedDecisionClient
from ontofill.lake import FileLake
from ontofill.phase_loop import LoopBudget
from ontofill.phases.p3_fanout.discovery_loop import (
    _MAX_PORTAL_FOLLOW_CHILDREN,
    DiscoveryLoop,
    _canonical_dataset_link_url,
    _follow_portal_children,
)
from ontofill.phases.p3_fanout.leads import Lead, LeadProvider
from tests.r17_helpers import SyntheticParseExecutor
from tests.test_discovery_loop import (
    PAGE,
    POLICY,
    FakeCapture,
    _library_case,
)

_ROOT = "https://data.example.test/"
_CHILD = "https://data.example.test/datasets/branches"
_OTHER = "https://libraries.example.test/branches"
_PROVENANCE = {
    "backend": "recorded",
    "model": "synthetic",
    "at": "2026-09-27T00:00:00Z",
}


class _CatalogProvider(LeadProvider):
    name = "synthetic_catalog"

    def __init__(self, *, include_other: bool = False):
        super().__init__()
        self.include_other = include_other

    def leads(self, context):
        self._attempt("catalog query", "ok", 1)
        leads = [
            Lead(
                _ROOT,
                "Official open data catalog",
                "Public branch records and datasets",
                self.name,
                "catalog query",
                tuple(query.property_id for query in context.queries),
            )
        ]
        if self.include_other:
            leads.append(
                Lead(
                    _OTHER,
                    "City library registry",
                    "Public branch records",
                    self.name,
                    "catalog query",
                    tuple(query.property_id for query in context.queries),
                )
            )
        return leads


def _loop(tmp_path: Path, provider: LeadProvider, pages: dict[str, str], *, max_captures: int):
    lake = FileLake(tmp_path / "lake")
    capture = FakeCapture(lake, pages)
    loop = DiscoveryLoop(
        [provider],
        capture=capture,
        lake=lake,
        run_id="mock-r56-follow",
        provenance=_PROVENANCE,
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
        parse_executor=SyntheticParseExecutor(),
        max_captures_per_iteration=max_captures,
    )
    return loop, capture


def test_trusted_catalog_child_is_captured_before_the_empty_root_is_judged(tmp_path: Path):
    ontology = _library_case(tmp_path, POLICY)
    root_page = (
        "<html><body><h1>Official open data catalog</h1>"
        '<a href="/datasets/branches">Entity level branch records</a></body></html>'
    )
    child_page = PAGE.format(title="Library branch records")
    loop, capture = _loop(
        tmp_path,
        _CatalogProvider(include_other=True),
        {
            _ROOT: root_page,
            _CHILD: child_page,
            _OTHER: PAGE.format(title="City library registry"),
        },
        max_captures=2,
    )

    result = loop.discover_sources(
        tmp_path,
        ontology,
        RecordedDecisionClient({}),
        gaps=("opening_hours",),
    )

    assert capture.calls[:2] == [_ROOT, _CHILD]
    assert len(capture.calls) <= 2
    candidates = json.loads(
        (tmp_path / "03-fanout/surface-map/leads.json").read_text(encoding="utf-8")
    )["candidates"]
    by_url = {candidate["url"]: candidate for candidate in candidates}
    assert by_url[_ROOT]["status"] == "rejected"
    assert by_url[_CHILD]["status"] == "confirmed"
    assert result["objectives"][0]["source_url"] == _CHILD

    child_capture_index = next(
        index
        for index, step in enumerate(loop.trace)
        if step.get("observed", {}).get("url") == _CHILD
    )
    critic_index = next(
        index
        for index, step in enumerate(loop.trace)
        if step.get("requested", {}).get("role") == "critique"
    )
    assert child_capture_index < critic_index
    follow_receipt = next(
        step
        for step in loop.trace
        if step.get("requested", {}).get("tool") == "p3.discovery.follow"
        and step.get("requested", {}).get("child_url") == _CHILD
    )
    assert follow_receipt["executed"]["status"] == "queued_displacing_lower_ranked_lead"
    assert follow_receipt["executed"]["displaced_url"] == _OTHER

    for name in ("leads.json", "discovery.json"):
        document = json.loads(
            (tmp_path / "03-fanout/surface-map" / name).read_text(encoding="utf-8")
        )
        assert document["run_id"] == "mock-r56-follow"
        written_at = datetime.fromisoformat(document["written_at"])
    assert written_at.tzinfo == UTC


def test_empty_catalog_does_not_reserve_unused_child_capture_slots(tmp_path: Path):
    ontology = _library_case(tmp_path, POLICY)
    loop, capture = _loop(
        tmp_path,
        _CatalogProvider(include_other=True),
        {
            _ROOT: "<html><body><h1>Official open data catalog</h1></body></html>",
            _OTHER: PAGE.format(title="City library registry"),
        },
        max_captures=2,
    )

    loop.discover_sources(
        tmp_path,
        ontology,
        RecordedDecisionClient({}),
        gaps=("opening_hours",),
    )

    assert capture.calls == [_ROOT, _OTHER]
    assert len(capture.calls) <= 2


def test_follow_children_are_bounded_and_deduplicated_across_anchors_and_spa_requests():
    candidate = {
        "source_id": "source-catalog",
        "url": _ROOT,
        "landing_url": _ROOT,
        "capture_key": "sha256:" + "a" * 64,
        "property_ids": ["opening_hours"],
        "title": "Official open data catalog",
    }
    ontology = {
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
    }
    anchors = [
        {
            "url": f"/datasets/branches-{index}.csv",
            "text": "CSV export",
        }
        for index in range(6)
    ]
    anchors.append({"url": "/datasets/branches-0.csv#download", "text": "Duplicate resource"})
    children = _follow_portal_children(
        candidate,
        {
            "links": anchors,
            "network_requests": [
                {
                    "url": f"{_ROOT}datasets/branches-0.csv",
                    "method": "GET",
                    "resource_type": "download",
                    "content_type": "text/csv",
                },
                {
                    "url": f"{_ROOT}api/library-branch-opening-hours",
                    "method": "GET",
                    "resource_type": "fetch",
                    "content_type": "application/json",
                },
            ],
        },
        ontology,
    )

    urls = [child["url"] for child in children]
    canonical_urls = [_canonical_dataset_link_url(url) for url in urls]
    assert len(urls) <= _MAX_PORTAL_FOLLOW_CHILDREN
    assert len(canonical_urls) == len(set(canonical_urls))
    assert any(child.get("network_observed") for child in children)


def test_off_host_api_child_is_reviewed_without_a_capture(tmp_path: Path):
    ontology = _library_case(tmp_path, POLICY)
    off_host = "https://files.other.example.test/api/branch-records?resource_id=public"
    root_page = (
        "<html><body><h1>Official open data catalog</h1>"
        f'<a href="{off_host}">Branch records API</a></body></html>'
    )
    loop, capture = _loop(tmp_path, _CatalogProvider(), {_ROOT: root_page}, max_captures=2)

    with pytest.raises(ValueError, match="confirmed no source candidates"):
        loop.discover_sources(
            tmp_path,
            ontology,
            RecordedDecisionClient({}),
            gaps=("opening_hours",),
        )

    assert off_host not in capture.calls
    packets = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "03-fanout/sources").glob("*/candidate.json")
    ]
    packet = next(item for item in packets if item["url"] == off_host)
    review_dir = tmp_path / "03-fanout/sources" / packet["source_id"]
    assert (review_dir / "APPROVAL_PENDING.md").is_file()
    assert packet["link_provenance"]["parent_capture_key"] == next(
        candidate["capture_key"]
        for candidate in json.loads(
            (tmp_path / "03-fanout/surface-map/leads.json").read_text(encoding="utf-8")
        )["candidates"]
        if candidate["url"] == _ROOT
    )


def test_query_plan_appends_model_generated_subject_standard_names():
    ontology = {
        "primary_class": "contract",
        "classes": [
            {
                "id": "contract",
                "label": "Public contract",
                "label_plural": "Public contracts",
                "identifier_property": "contract_id",
                "title_property": "contract_title",
            },
            {
                "id": "supplier",
                "label": "Supplier",
                "label_plural": "Suppliers",
                "identifier_property": "supplier_id",
                "title_property": "supplier_name",
            },
        ],
        "properties": [
            {"id": "contract_id", "label": "Contract identifier", "domain": "contract"},
            {"id": "contract_title", "label": "Contract title", "domain": "contract"},
            {"id": "supplier_id", "label": "Supplier identifier", "domain": "supplier"},
            {
                "id": "supplier_name",
                "label": "Supplier name",
                "domain": "supplier",
            },
            {
                "id": "award_value",
                "label": "Award value",
                "domain": "contract",
                "dod": True,
            },
        ],
        "relations": [
            {
                "id": "awarded_to",
                "label": "Awarded to",
                "domain": "contract",
                "range": "supplier",
            }
        ],
    }

    class Planner:
        backend = "vultr"
        model = "synthetic-standard-planner"

        def complete_json(self, purpose: str, prompt: str, schema: dict) -> dict:
            assert purpose == "phase3.plan_queries"
            assert "subject-specific open-data vocabulary" in prompt
            assert "standard_terms" in schema["properties"]["queries"]["items"]["properties"]
            return {
                "queries": [
                    {
                        "property_id": "award_value",
                        "query": "official contract award records by supplier",
                        "standard_terms": [
                            "Open Contracting Data Standard",
                            "Contrataciones Abiertas",
                        ],
                    }
                ]
            }

    loop = object.__new__(DiscoveryLoop)
    queries = loop._plan_queries(
        Planner(),
        "Find government contract awards and supplier records.",
        ontology,
        {"jurisdiction": "Example region", "trusted_publishers": []},
        ["award_value"],
        1,
        set(),
    )

    query_text = " ".join(query.text for query in queries).casefold()
    assert "open contracting data standard" in query_text
    assert "contrataciones abiertas" in query_text
    assert "https://" not in query_text
