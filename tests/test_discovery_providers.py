"""Synthetic provider captures, fallback, and fingerprinted source review."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest
import yaml
from ontofill_scrape import SearchResult
from ontofill_scrape.models import FailureKind, ToolFailure

from ontofill.case.checkpoints import require_approval
from ontofill.inference import RecordedDecisionClient
from ontofill.lake import FileLake
from ontofill.phases.p3_fanout.authority import authority_result
from ontofill.phases.p3_fanout.phase import discover_objectives
from ontofill.phases.p3_fanout.search import (
    ProviderSearchClient,
    SandboxWebSearchProvider,
    parse_web_results,
)
from tests.genericity.fixtures.discovery import discovery_case


def test_web_result_links_are_decoded_only_from_captured_html() -> None:
    target = "https://agency.example.gov/list"
    wrapped = "a1" + base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    html = f'<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u={wrapped}">Official list</a></h2></li>'
    assert parse_web_results(html, provider="bing_html") == (
        SearchResult(target, "Official list", "Official list"),
    )
    assert (
        parse_web_results('<a href="https://unlisted.example.test/">Ad</a>', provider="bing_html")
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
            "html_key": lake.put_bytes(b"captcha"),
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
    assert len(provider.trace) == 1


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
    (directory / "APPROVED").write_text(
        json.dumps(
            {
                "approver": "Reviewer",
                "date": "2026-09-26",
                "checkpoint": "source",
                "source_fingerprint": "0" * 64,
            }
        )
    )
    assert not require_approval(
        directory,
        phase=3,
        checkpoint="source",
        artifact_paths=["candidate.json"],
        generated_by=live,
        source_fingerprint=objective["source_fingerprint"],
    )
    (directory / "APPROVED").write_text(
        json.dumps(
            {
                "approver": "Reviewer",
                "date": "2026-09-26",
                "checkpoint": "source",
                "source_fingerprint": objective["source_fingerprint"],
            }
        )
    )
    assert require_approval(
        directory,
        phase=3,
        checkpoint="source",
        artifact_paths=["candidate.json"],
        generated_by=live,
        source_fingerprint=objective["source_fingerprint"],
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
