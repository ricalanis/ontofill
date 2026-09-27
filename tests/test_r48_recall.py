"""Synthetic regressions for broad, bounded phase-three source recall."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ontofill.contracts import validate_document
from ontofill.inference import RecordedDecisionClient
from ontofill.lake import FileLake
from ontofill.phase_loop import LoopBudget
from ontofill.phases.p3_fanout.discovery_loop import (
    DiscoveryLoop,
    _parsed_document_headers,
    _record_granularity,
)
from ontofill.phases.p3_fanout.leads import Lead
from ontofill.sandbox import parse as parse_module
from tests.r17_helpers import SyntheticParseExecutor
from tests.test_discovery_loop import PAGE, POLICY, FakeVultr, StaticProvider, _library_case, _loop


@pytest.fixture(autouse=True)
def synthetic_parse_pod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_module, "DockerParseExecutor", SyntheticParseExecutor)


def test_legacy_workbook_headers_prove_entity_rows_for_only_present_columns() -> None:
    parsed = SimpleNamespace(
        format="xls",
        rows=(
            {"sheet": "Records", "row_number": 1, "values": ["Branch name", "Opening hours"]},
            {"sheet": "Records", "row_number": 2, "values": ["North", "Weekdays"]},
        ),
    )
    headers = _parsed_document_headers(parsed)
    assert headers == ["Branch name", "Opening hours"]
    context = {"document": {"headers": headers, "row_count": 2}}
    granularity, quote, _ = _record_granularity(
        context, {"kind": "dataset"}, {"name", "branch"}, {"branch"}
    )
    assert (granularity, quote) == ("entity_records", "Branch name")
    assert not any("Free internet" in header for header in headers)


def test_approved_source_packet_outranks_search_leads() -> None:
    loop = object.__new__(DiscoveryLoop)
    root = {
        "url": "https://data.example.test/",
        "providers": ["approved_source"],
        "score": 0,
    }
    ordinary = {
        "url": "https://data.example.test/news/records",
        "providers": ["tavily", "model"],
        "score": 1,
    }
    assert loop._rank_lead(root, POLICY) > loop._rank_lead(ordinary, POLICY)


def test_portal_card_is_explored_one_level_before_rejecting_root(tmp_path) -> None:
    ontology = _library_case(tmp_path, POLICY)
    root = "https://data.example.test/"
    child = "https://data.example.test/records"
    provider = StaticProvider("synthetic", {root: ("name",)})
    pages = {
        root: '<html><body><h1>Open data</h1><a href="/records">Branch directory</a></body></html>',
        child: PAGE.format(title="Branch directory"),
    }
    loop, capture = _loop(
        tmp_path,
        [provider],
        pages,
        budget=LoopBudget(max_iterations=3, wall_seconds=60),
    )
    result = loop.discover_sources(tmp_path, ontology, RecordedDecisionClient({}), gaps=("name",))
    assert root in capture.calls and child in capture.calls
    assert child in {objective["source_url"] for objective in result["objectives"]}
    assert not any(capture.calls.count(url) > 1 for url in capture.calls)


def test_nested_spanish_open_data_page_is_explored_one_level_only(tmp_path) -> None:
    ontology = _library_case(tmp_path, POLICY)
    root = "https://data.example.test/catalogo/datos-abiertos"
    child = "https://data.example.test/datasets/records"
    grandchild = "https://data.example.test/datasets/records/branch-1"

    class PortalProvider(StaticProvider):
        def leads(self, context):
            self.calls += 1
            wanted = {query.property_id for query in context.queries}
            found = [
                Lead(
                    root,
                    "Official data directory",
                    "Public records",
                    self.name,
                    "official directory",
                    props,
                )
                for props in self.urls.values()
                if wanted & set(props)
            ]
            self._attempt("datos abiertos", "ok" if found else "empty", len(found))
            return found

    provider = PortalProvider("synthetic", {root: ("name",)})
    pages = {
        root: '<html><body><h1>Official portal</h1><a href="/datasets/records">Listado de registros</a></body></html>',
        child: PAGE.format(title="Branch directory").replace(
            "</body>", '<a href="/datasets/records/branch-1">Branch details</a></body>'
        ),
        grandchild: PAGE.format(title="Branch detail"),
    }
    loop, capture = _loop(
        tmp_path,
        [provider],
        pages,
        budget=LoopBudget(max_iterations=3, wall_seconds=60),
    )
    result = loop.discover_sources(tmp_path, ontology, RecordedDecisionClient({}), gaps=("name",))

    assert root in capture.calls and child in capture.calls
    assert grandchild not in capture.calls
    assert child in {objective["source_url"] for objective in result["objectives"]}


def _entity_anchor_ontology() -> dict:
    return {
        "primary_class": "record",
        "classes": [
            {
                "id": "record",
                "label": "Record",
                "label_plural": "Records",
                "identifier_property": "record_id",
                "title_property": "display_name",
            },
            {
                "id": "program",
                "label": "Program",
                "label_plural": "Programs",
                "identifier_property": "program_id",
                "title_property": "program_name",
            },
        ],
        "properties": [
            {"id": "record_id", "label": "Record identifier", "domain": "record"},
            {"id": "display_name", "label": "Display name", "domain": "record"},
            {"id": "status", "label": "Eligibility status", "domain": "record", "dod": True},
            {"id": "program_id", "label": "Program identifier", "domain": "program"},
            {"id": "program_name", "label": "Program name", "domain": "program"},
        ],
        "relations": [
            {
                "id": "record_program",
                "label": "Enrolled in program",
                "domain": "record",
                "range": "program",
            }
        ],
    }


def test_primary_entity_query_uses_ontology_anchor_and_model_local_terms() -> None:
    ontology = _entity_anchor_ontology()
    brief = "Find a public source for each record."
    loop = object.__new__(DiscoveryLoop)

    class QueryPlanner:
        backend = "vultr"
        model = "synthetic-query-planner"

        def complete_json(self, purpose, prompt, schema):
            assert purpose == "phase3.plan_queries"
            self.prompt = prompt
            return {
                "queries": [
                    {
                        "property_id": "status",
                        "query": "localized roster open dataset API enrolled in program",
                    }
                ]
            }

    planner = QueryPlanner()
    planned = loop._plan_queries(
        planner,
        brief,
        ontology,
        {"jurisdiction": "Example region", "trusted_publishers": []},
        ["status"],
        1,
        set(),
    )
    query = planned[0].text.casefold()
    for anchor in ("record", "record identifier", "eligibility status", "enrolled in program"):
        assert anchor in query
    assert "display name" in planner.prompt.casefold()
    assert "local-language" in planner.prompt.casefold()
    assert "entity-level rows" in planner.prompt.casefold()
    assert "enrolled in program" in planner.prompt.casefold()

    fallback_first = loop._plan_queries(
        RecordedDecisionClient({}),
        brief,
        ontology,
        {"jurisdiction": "Example region", "trusted_publishers": []},
        ["status"],
        1,
        set(),
    )
    first_query = fallback_first[0].text.casefold()
    assert "record" in first_query and "record identifier" in first_query
    assert "eligibility status" in first_query
    fallback_second = loop._plan_queries(
        RecordedDecisionClient({}),
        brief,
        ontology,
        {"jurisdiction": "Example region", "trusted_publishers": []},
        ["status"],
        2,
        {fallback_first[0].text},
    )
    relation_query = fallback_second[0].text.casefold()
    assert "enrolled in program" in relation_query and "program" in relation_query
    assert "record identifier" in relation_query and "eligibility status" in relation_query


def test_relation_rows_out_rank_property_only_aggregate_leads() -> None:
    loop = object.__new__(DiscoveryLoop)
    ontology = _entity_anchor_ontology()
    relation_rows = {
        "url": "https://data.example.test/records-programs",
        "title": "Records enrolled in Programs",
        "snippet": "Record identifier, display name, and program link for each row.",
        "providers": ["tavily"],
        "property_ids": ["status"],
        "score": 0.2,
    }
    aggregate = {
        "url": "https://data.example.test/status-summary",
        "title": "Eligibility status totals",
        "snippet": "Aggregate dashboard of status totals.",
        "providers": ["tavily"],
        "property_ids": ["status"],
        "score": 1.0,
    }

    assert loop._rank_lead(relation_rows, POLICY, ontology) > loop._rank_lead(
        aggregate, POLICY, ontology
    )
    lower_score = {**aggregate, "score": 0.0}
    assert loop._rank_lead(aggregate, POLICY, ontology) > loop._rank_lead(
        lower_score, POLICY, ontology
    )


def test_new_public_portal_is_handed_to_later_provider_in_same_round(tmp_path) -> None:
    ontology = _library_case(tmp_path, POLICY)
    portal = "https://data.example.test/open-data"

    class PortalProvider(StaticProvider):
        def leads(self, context):
            self.calls += 1
            self._attempt("portal discovery", "ok", 1)
            return [
                Lead(
                    portal,
                    "Open data catalog",
                    "Public datasets and API",
                    self.name,
                    "portal discovery",
                    ("name",),
                )
            ]

    class CkanObserver(StaticProvider):
        seen_portals: tuple[str, ...] = ()

        def leads(self, context):
            self.seen_portals = context.open_data_portals
            self.calls += 1
            self._attempt("catalog query", "empty", 0)
            return []

    producer = PortalProvider("model", {})
    observer = CkanObserver("ckan", {})
    loop, _ = _loop(
        tmp_path,
        [producer, observer],
        {portal: PAGE.format(title="Open data catalog")},
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
    )

    loop.discover_sources(tmp_path, ontology, RecordedDecisionClient({}), gaps=("name",))

    assert observer.seen_portals == (portal,)


def test_blank_portal_is_inconclusive_and_capture_error_is_recorded(tmp_path) -> None:
    ontology = _library_case(tmp_path)
    blank = "https://data.example.test/"
    missing = "https://libraries.example.test/unreachable"
    provider = StaticProvider("synthetic", {blank: ("name",), missing: ("name",)})
    loop, _ = _loop(
        tmp_path,
        [provider],
        {blank: "<html><body></body></html>"},
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
    )
    with pytest.raises(ValueError, match="confirmed no source candidates"):
        loop.discover_sources(tmp_path, ontology, RecordedDecisionClient({}), gaps=("name",))
    ledger = json.loads((tmp_path / "03-fanout/surface-map/leads.json").read_text())
    candidates = {item["url"]: item for item in ledger["candidates"]}
    assert candidates[blank]["status"] == "inconclusive"
    assert candidates[blank]["capture_reason"] == "empty_render"
    assert "URL domain is not allowed" in candidates[missing]["capture_error"]


def test_approved_document_from_prior_run_is_parsed_and_confirmed(tmp_path, monkeypatch) -> None:
    ontology = _library_case(tmp_path, POLICY)
    lake = FileLake(tmp_path / "lake")
    url = "https://downloads.example.test/branches.csv"
    parent_key = lake.put_bytes(b"Synthetic public portal page")
    document = b"Branch name,Opening hours\nNorth,Weekdays\nSouth,Weekends\n"
    doc_key = lake.put_bytes(document)
    source_id = "source-link-synthetic"
    directory = tmp_path / "03-fanout/sources" / source_id
    directory.mkdir(parents=True)
    fingerprint = hashlib.sha256(url.encode()).hexdigest()
    packet = {
        "source_id": source_id,
        "url": url,
        "title": "Branch directory",
        "snippet": "Public branch records",
        "provider": "sandbox_page_link",
        "providers": ["sandbox_page_link"],
        "capture_key": parent_key,
        "fingerprint": fingerprint,
        "authority": "review",
        "authority_tier": "unknown",
        "authority_reason": "Document host needs review",
        "link_provenance": {
            "parent_source_id": "source-parent",
            "parent_page_url": "https://data.example.test/",
            "parent_capture_key": parent_key,
            "link_url": url,
            "link_text": "Branch directory",
        },
        "generated_by": {
            "backend": "vultr",
            "model": "synthetic",
            "at": datetime.now(UTC).isoformat(),
        },
    }
    validate_document("source-candidate", packet)
    candidate_path = directory / "candidate.json"
    candidate_path.write_text(json.dumps(packet))
    candidate_bytes = candidate_path.read_bytes()
    relative = candidate_path.relative_to(tmp_path).as_posix()
    (directory / "APPROVED").write_text(
        json.dumps(
            {
                "approver": "Reviewer",
                "date": "2026-09-27",
                "checkpoint": "source",
                "decision": "approve",
                "source_fingerprint": fingerprint,
                "identity_source": "local",
                "artifact_sha256": {relative: hashlib.sha256(candidate_bytes).hexdigest()},
            }
        )
    )
    captured_urls: list[str] = []

    def capture(target: str, **kwargs) -> dict:
        captured_urls.append(target)
        if target != url:
            raise RuntimeError("synthetic publisher root unavailable")
        assert "downloads.example.test" in kwargs["allowed_domains"]
        return {
            "url": target,
            "status": 200,
            "document_key": doc_key,
            "document_content_type": "text/csv",
            "document_size_bytes": len(document),
            "trace": [],
        }

    loop = DiscoveryLoop(
        [StaticProvider("empty", {})],
        capture=capture,
        lake=lake,
        run_id="run-r48-approved",
        provenance={"backend": "vultr", "model": "synthetic", "at": datetime.now(UTC).isoformat()},
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
        parse_executor=SyntheticParseExecutor(),
    )
    monkeypatch.setattr(
        loop,
        "_model_verdicts",
        lambda _decision, draft, case_ontology, _policy: loop._code_verdicts(draft, case_ontology),
    )
    result = loop.discover_sources(tmp_path, ontology, FakeVultr(), gaps=("name",))
    assert url in captured_urls
    assert candidate_path.read_bytes() == candidate_bytes
    objective = next(item for item in result["objectives"] if item["source_url"] == url)
    assert objective["document_key"] == doc_key
    assert objective["access_path"]["name"]["record_granularity"] == "entity_records"
    assert (directory / "capture.json").is_file()
    assert any(
        job.get("checkpoints", {}).get("host", {}).get("runtime") == "runsc" for job in loop.jobs
    )
