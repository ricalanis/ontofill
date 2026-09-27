"""Synthetic provider captures, fallback, and fingerprinted source review."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import yaml
from ontofill_scrape import SearchResult
from ontofill_scrape.models import FailureKind, ToolFailure

from ontofill.case.checkpoints import require_approval
from ontofill.inference import RecordedDecisionClient
from ontofill.lake import FileLake
from ontofill.phases.p3_fanout.authority import authority_result
from ontofill.phases.p3_fanout.leads import (
    CkanLeadProvider,
    JsonCache,
    LeadContext,
    LeadQuery,
    TavilyLeadProvider,
    default_lead_providers,
    is_open_data_portal,
    tavily_credit_cap_for_budget,
)
from ontofill.phases.p3_fanout.phase import discover_objectives
from ontofill.phases.p3_fanout.search import (
    ProviderSearchClient,
    SandboxWebSearchProvider,
    parse_web_results,
)
from ontofill.sandbox import parse as parse_module
from ontofill.sandbox import parse_bronze
from tests.approval_support import bind_approval
from tests.genericity.fixtures.discovery import discovery_case
from tests.r17_helpers import SyntheticParseExecutor


@pytest.fixture(autouse=True)
def synthetic_parse_pod(monkeypatch):
    monkeypatch.setattr(parse_module, "DockerParseExecutor", SyntheticParseExecutor)


def _parsed_links(lake: FileLake, html: str, base_url: str):
    key = lake.put_bytes(html.encode("utf-8"), {"content_type": "text/html", "url": base_url})
    return parse_bronze(
        lake,
        key,
        format="html",
        base_url=base_url,
        run_id="mock-search-links",
        source_id="search-provider",
        tdd_path="03-fanout/search-policy.json",
        generated_by={
            "backend": "recorded",
            "model": "synthetic-search-test",
            "at": datetime.now(UTC).isoformat(),
        },
    ).links


def test_web_result_links_are_decoded_only_from_sandboxed_capture(tmp_path) -> None:
    target = "https://agency.example.gov/list"
    wrapped = "a1" + base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    html = f'<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u={wrapped}">Official list</a></h2></li>'
    lake = FileLake(tmp_path / "lake")
    assert parse_web_results(
        _parsed_links(lake, html, "https://www.bing.com/search"), provider="bing_html"
    ) == (SearchResult(target, "Official list", "Official list"),)
    assert (
        parse_web_results(
            _parsed_links(
                lake,
                '<a href="https://unlisted.example.test/">Ad</a>',
                "https://www.bing.com/search",
            ),
            provider="bing_html",
        )
        == ()
    )


class FakeProvider:
    def __init__(self, name, result=None, failure=None):
        self.name, self.result, self.failure = name, result, failure
        self.trusted_origin = None
        self.capture_key = "sha256:" + "a" * 64
        self.trace = []
        self.jobs = []

    def search(self, query):
        self.trace.append({"step_id": f"step:{self.name}", "query": query})
        if self.failure:
            raise ToolFailure(self.failure, f"{self.name} stopped")
        return self.result or ()


def test_provider_block_falls_back_to_distinct_provider_and_keeps_attempts() -> None:
    target = SearchResult("https://agency.example.gov/list", "Official sanction list")
    router = ProviderSearchClient(
        [
            FakeProvider("blocked", failure=FailureKind.BLOCKED),
            FakeProvider("second", result=(target,)),
        ]
    )
    assert router.search("sanction status") == (target,)
    assert [item["outcome"] for item in router.attempts] == ["blocked", "ok"]
    assert [item["step_id"] for item in router.trace] == ["step:blocked", "step:second"]
    assert router.result_metadata[target.url]["provider"] == "second"
    with pytest.raises(ToolFailure) as error:
        ProviderSearchClient([FakeProvider("only", failure=FailureKind.BLOCKED)]).search("x")
    assert error.value.kind == FailureKind.BLOCKED


def test_blocked_provider_marks_only_dispatch_with_reason() -> None:
    class BlockedProofProvider(FakeProvider):
        def search(self, query):
            for checkpoint in (
                "dispatch_result",
                "host_check",
                "pod_identity",
                "isolation_probe",
                "teardown",
            ):
                self.trace.append(
                    {
                        "step_id": f"step:{checkpoint}",
                        "evaluated": {"proof_checkpoint": checkpoint},
                    }
                )
            raise ToolFailure(FailureKind.BLOCKED, "captcha wall detected")

    router = ProviderSearchClient(
        [
            BlockedProofProvider("blocked"),
            FakeProvider("second", result=(SearchResult("https://example.test", "Result"),)),
        ]
    )
    router.search("public records")
    assert [step.get("event") for step in router.trace[:5]] == ["hard_stop", None, None, None, None]
    assert router.trace[0]["evaluated"]["reason"] == "blocked: captcha"
    assert all("reason" not in step["evaluated"] for step in router.trace[1:5])


def test_web_provider_stops_on_captcha_after_sandbox_capture(tmp_path) -> None:
    lake = FileLake(tmp_path / "lake")
    calls = []

    def capture(url, **kwargs):
        calls.append((url, kwargs["allowed_domains"]))
        return {
            "url": url,
            "status": 200,
            "html": '<input type="password" name="captcha">',
            "html_key": lake.put_bytes(
                b'<input type="password" name="captcha">', {"content_type": "text/html"}
            ),
            "trace": [{"step_id": "step:captcha"}],
        }

    provider = SandboxWebSearchProvider(
        "bing_html",
        lake,
        "mock-test",
        {"backend": "recorded", "model": "test", "at": datetime.now(UTC).isoformat()},
        capture=capture,
        min_interval_seconds=0,
    )
    with pytest.raises(ToolFailure) as error:
        provider.search("public suppliers")
    assert error.value.kind == FailureKind.BLOCKED
    assert len(calls) == 1
    assert len(provider.trace) == 7
    assert {
        row["evaluated"]["proof_checkpoint"]
        for row in provider.trace
        if row.get("evaluated", {}).get("proof_checkpoint")
    } == {"task", "host", "where", "isolation", "secrets", "teardown"}


class CapturedSearch:
    name = "synthetic_search"
    trusted_origin = None
    capture_key = "sha256:" + "b" * 64

    def __init__(self):
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return [
            SearchResult(
                "https://registry.example.test/list",
                "Example City supplier list",
                "Public list of company names",
            )
        ]


def test_unrecognized_source_is_queued_with_stale_approval_rejected(tmp_path) -> None:
    ontology = discovery_case(tmp_path)
    search = CapturedSearch()
    decision = RecordedDecisionClient({})
    document = discover_objectives(
        tmp_path, ontology, decision, search, gaps=("name", "opening_hours"), max_sources=1
    )
    objective = document["objectives"][0]
    directory = tmp_path / "03-fanout/sources" / objective["source_id"]
    manifest = json.loads((directory / "candidate.json").read_text())
    assert manifest["capture_key"] == search.capture_key
    assert manifest["authority"] == "review"
    pending = (directory / "APPROVAL_PENDING.md").read_text()
    assert yaml.safe_load(pending.split("---", 2)[1])["checkpoint"] == "source"
    live = {"backend": "vultr", "model": "synthetic-test", "at": datetime.now(UTC).isoformat()}
    relative = (directory / "candidate.json").relative_to(tmp_path).as_posix()
    (directory / "APPROVED").write_text(
        json.dumps(
            bind_approval(
                tmp_path,
                [relative],
                {
                    "approver": "Reviewer",
                    "date": "2026-09-26",
                    "checkpoint": "source",
                    "source_fingerprint": "0" * 64,
                },
            )
        )
    )
    assert not require_approval(
        directory,
        phase=3,
        checkpoint="source",
        artifact_paths=[relative],
        generated_by=live,
        source_fingerprint=objective["source_fingerprint"],
        case_dir=tmp_path,
    )
    (directory / "APPROVED").write_text(
        json.dumps(
            bind_approval(
                tmp_path,
                [relative],
                {
                    "approver": "Reviewer",
                    "date": "2026-09-26",
                    "checkpoint": "source",
                    "source_fingerprint": objective["source_fingerprint"],
                },
            )
        )
    )
    assert require_approval(
        directory,
        phase=3,
        checkpoint="source",
        artifact_paths=[relative],
        generated_by=live,
        source_fingerprint=objective["source_fingerprint"],
        case_dir=tmp_path,
    )
    policy = json.loads((tmp_path / "01-scope/prd.json").read_text())["authority_policy"]
    assert authority_result("https://city.example.test/list", policy=policy)[0]
    assert not authority_result("https://registry.example.test/list")[0]


def test_gap_query_is_not_truncated_or_served_from_stale_cache(tmp_path) -> None:
    ontology = discovery_case(tmp_path)
    search = CapturedSearch()
    decision = RecordedDecisionClient({})
    discover_objectives(tmp_path, ontology, decision, search, gaps=("name",), max_sources=1)
    assert len(search.queries) == 1
    discover_objectives(tmp_path, ontology, decision, search, gaps=("name",), max_sources=1)
    assert len(search.queries) == 1
    discover_objectives(
        tmp_path, ontology, decision, search, gaps=("opening_hours",), max_sources=1
    )
    assert len(search.queries) == 2
    assert "Opening hours" in search.queries[-1]


def test_ckan_queries_each_gap_at_trusted_and_discovered_open_data_portals() -> None:
    calls = []

    def fetch_json(url: str, domain: str):
        calls.append((domain, parse_qs(urlsplit(url).query)["q"][0]))
        return {"success": True, "result": {"results": []}}

    provider = CkanLeadProvider(fetch_json=fetch_json)
    context = LeadContext(
        brief="",
        ontology={},
        policy={
            "trusted_publishers": [
                {
                    "kind": "official open_data portal",
                    "domains": ["trusted-data.example.test"],
                },
                {"kind": "government agency", "domains": ["agency.example.test"]},
            ]
        },
        queries=(
            LeadQuery("supplier_name", "public suppliers"),
            LeadQuery("supplier_name", "public suppliers"),
            LeadQuery("supplier_id", "supplier registration number"),
        ),
        iteration=1,
        open_data_portals=(
            "https://discovered-data.example.test/catalog/",
            "trusted-data.example.test",
            "https://user:password@ignored.example.test/",
            "http://localhost/",
            "https://192.168.1.4/",
        ),
    )

    assert provider.leads(context) == []
    assert calls == [
        ("trusted-data.example.test", "public suppliers"),
        ("trusted-data.example.test", "supplier registration number"),
        ("discovered-data.example.test", "public suppliers"),
        ("discovered-data.example.test", "supplier registration number"),
    ]
    assert [(attempt["domain"], attempt["query"]) for attempt in provider.attempts] == calls
    assert provider.calls == len(calls)


def test_ckan_records_the_call_cap_and_deduplicates_queries_and_hosts() -> None:
    calls = []

    def fetch_json(url: str, domain: str):
        calls.append(domain)
        return {"success": True, "result": {"results": []}}

    provider = CkanLeadProvider(fetch_json=fetch_json, max_calls=1)
    context = LeadContext(
        brief="",
        ontology={},
        policy={
            "trusted_publishers": [
                {"kind": "open data catalog", "domains": ["first.example.test"]},
                {"kind": "data portal", "domains": ["second.example.test"]},
            ]
        },
        queries=(
            LeadQuery("supplier_name", "public suppliers"),
            LeadQuery("supplier_name", "public suppliers"),
            LeadQuery("supplier_id", "supplier registration number"),
        ),
        iteration=1,
    )

    provider.leads(context)

    assert calls == ["first.example.test"]
    assert [attempt["outcome"] for attempt in provider.attempts] == ["empty", "cap_reached"]
    assert provider.attempts[-1] == {
        "provider": "ckan",
        "query": "supplier registration number",
        "outcome": "cap_reached",
        "result_count": 0,
        "domain": "first.example.test",
        "max_calls": 1,
        "calls": 1,
        "pending_queries": 3,
    }


def test_open_data_portal_classifier_uses_generic_lead_and_source_class_labels() -> None:
    assert is_open_data_portal({"title": "Portal de datos abiertos"}, {})
    assert is_open_data_portal(
        {"title": "Procurement information", "source_class_id": "data-catalog"},
        {"source_classes": [{"id": "data-catalog", "label": "Public dataset catalog"}]},
    )
    assert not is_open_data_portal({"title": "Public supplier registry"}, {})


def test_tavily_default_credit_cap_tracks_remaining_p3_budget(monkeypatch) -> None:
    class VultrDecision:
        backend = "vultr"

    monkeypatch.setenv("TAVILY_API_KEY", "synthetic-test-key")
    monkeypatch.delenv("ONTOFILL_TAVILY_MAX_CREDITS", raising=False)
    assert tavily_credit_cap_for_budget(None) == 12
    assert tavily_credit_cap_for_budget(0.10) == 20
    assert tavily_credit_cap_for_budget(1.00) == 48
    providers = default_lead_providers(VultrDecision(), cache_root=None, remaining_budget_usd=0.10)
    tavily = next(provider for provider in providers if provider.name == "tavily")
    assert tavily.max_credits == 20

    monkeypatch.setenv("ONTOFILL_TAVILY_MAX_CREDITS", "7")
    overridden = default_lead_providers(VultrDecision(), cache_root=None, remaining_budget_usd=1.00)
    assert next(provider for provider in overridden if provider.name == "tavily").max_credits == 7
    explicit = default_lead_providers(
        VultrDecision(),
        cache_root=None,
        tavily_max_credits=3,
        remaining_budget_usd=1.00,
    )
    assert next(provider for provider in explicit if provider.name == "tavily").max_credits == 3


def test_tavily_attempts_include_credit_used_and_cap() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "usage": {"credits": 1},
                "results": [
                    {
                        "url": "https://agency.example.test/suppliers",
                        "title": "Supplier list",
                        "content": "Public supplier records",
                    }
                ],
            },
        )

    provider = TavilyLeadProvider(
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        cache=JsonCache(None),
        max_credits=1,
        api_key="synthetic-test-key",
    )
    context = LeadContext(
        brief="",
        ontology={},
        policy={},
        queries=(LeadQuery("supplier_name", "public suppliers"),),
        iteration=1,
    )

    assert len(provider.leads(context)) == 1
    assert provider.attempts[0]["credits"] == 1
    assert provider.attempts[0]["credits_used"] == 1
    assert provider.attempts[0]["credit_cap"] == 1
