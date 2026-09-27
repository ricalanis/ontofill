"""R33 P3 frontier, redirect-review, retry, and budget regressions."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ontofill.case.checkpoints import load_json, write_json
from ontofill.inference import generated_by
from ontofill.lake import FileLake
from ontofill.phase_loop import LoopBudget, LoopResult
from ontofill.phases.p3_fanout.discovery_loop import DiscoveryLoop, NoConfirmedSources
from ontofill.phases.p3_fanout.leads import (
    Lead,
    LeadContext,
    LeadProvider,
    LeadQuery,
    TavilyLeadProvider,
)
from ontofill.sandbox import CaptureBlocked
from ontofill.workflow import NEEDS_HUMAN_EXIT, run_case
from tests.genericity.fixtures.libraries import library_decisions
from tests.test_discovery_loop import BRIEF, PAGE, POLICY, StaticProvider, _library_case


class EmptyProvider(LeadProvider):
    name = "synthetic-empty"

    def leads(self, context: LeadContext) -> list[Lead]:
        self._attempt("; ".join(query.text for query in context.queries), "empty", 0)
        return []


def _single_domain_policy() -> dict:
    return {
        "jurisdiction": "Synthetic jurisdiction",
        "trusted_publishers": [
            {
                "kind": "public records publisher",
                "tier": "primary",
                "domains": ["records.example.test"],
                "rationale": "Synthetic approved publisher",
            }
        ],
        "unknown_source_action": "review",
    }


def test_property_queries_are_site_restricted_and_empty_search_seeds_policy_root(
    tmp_path: Path,
) -> None:
    policy = _single_domain_policy()
    ontology = _library_case(tmp_path, policy=policy)
    lake = FileLake(tmp_path / "lake")
    root_url = "https://records.example.test/"
    root_html = PAGE.format(title="Public records")
    calls: list[tuple[str, list[str]]] = []

    def capture(url: str, **kwargs) -> dict:
        calls.append((url, list(kwargs["allowed_domains"])))
        assert url == root_url
        key = lake.put_bytes(root_html.encode())
        return {
            "url": url,
            "redirect_chain": [url],
            "status": 200,
            "html_key": key,
            "screenshot_key": lake.put_bytes(b"synthetic screenshot"),
            "trace": [],
        }

    decision = library_decisions()
    loop = DiscoveryLoop(
        [EmptyProvider()],
        capture=capture,
        lake=lake,
        run_id="run-r33-roots",
        provenance=generated_by(decision),
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
    )
    queries = loop._plan_queries(
        decision,
        BRIEF.read_text(encoding="utf-8"),
        ontology,
        policy,
        [ontology["properties"][0]["id"]],
        1,
        set(),
    )

    assert len(queries) == 1
    assert queries[0].text.startswith("site:records.example.test ")
    with pytest.raises(NoConfirmedSources):
        loop.discover_sources(tmp_path, ontology, decision)
    assert calls == [(root_url, ["records.example.test"])]
    leads = load_json(tmp_path / "03-fanout/surface-map/leads.json")["leads"]
    assert any(
        item["url"] == root_url and "authority_policy" in item["providers"] for item in leads
    )


def test_tavily_site_query_is_sent_as_a_single_restricted_domain() -> None:
    provider = TavilyLeadProvider(api_key="synthetic-key")
    query = LeadQuery("records", "site:records.example.test public records")
    context = LeadContext(
        "synthetic brief",
        {"properties": []},
        _single_domain_policy(),
        (query,),
        iteration=3,
    )

    request = provider.request_body(query, context)

    assert request["include_domains"] == ["records.example.test"]
    assert request["include_domains_mode"] == "restrict"


def test_policy_roots_reject_local_ip_internal_and_malformed_hosts(tmp_path: Path) -> None:
    policy = _single_domain_policy()
    policy["trusted_publishers"][0]["domains"] = [
        "localhost",
        "127.0.0.1",
        "metadata.google.internal",
        "not a domain",
        "records.example.test",
    ]
    ontology = _library_case(tmp_path, policy=policy)
    decision = library_decisions()
    lake = FileLake(tmp_path / "lake")
    calls: list[str] = []

    def capture(url: str, **_kwargs) -> dict:
        calls.append(url)
        key = lake.put_bytes(b"forbidden fixture")
        return {"url": url, "status": 403, "html_key": key, "trace": []}

    loop = DiscoveryLoop(
        [EmptyProvider()],
        capture=capture,
        lake=lake,
        run_id="run-r33-valid-roots-only",
        provenance=generated_by(decision),
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
    )
    queries = loop._plan_queries(
        decision,
        BRIEF.read_text(encoding="utf-8"),
        ontology,
        policy,
        [ontology["properties"][0]["id"]],
        1,
        set(),
    )
    assert queries[0].text.startswith("site:records.example.test ")

    with pytest.raises(NoConfirmedSources):
        loop.discover_sources(tmp_path, ontology, decision)
    assert calls == ["https://records.example.test/"] * 2  # one bounded 403 retry


def test_trusted_roots_are_seeded_even_when_provider_returns_other_leads(tmp_path: Path) -> None:
    policy = _single_domain_policy()
    ontology = _library_case(tmp_path, policy=policy)
    decision = library_decisions()
    lake = FileLake(tmp_path / "lake")
    provider_url = "https://records.example.test/catalog"
    root_url = "https://records.example.test/"
    calls: list[str] = []

    def capture(url: str, **_kwargs) -> dict:
        calls.append(url)
        key = lake.put_bytes(PAGE.format(title="Public records").encode())
        return {
            "url": url,
            "redirect_chain": [url],
            "status": 200,
            "html_key": key,
            "screenshot_key": lake.put_bytes(b"synthetic screenshot"),
            "trace": [],
        }

    loop = DiscoveryLoop(
        [StaticProvider("synthetic", {provider_url: (ontology["properties"][0]["id"],)})],
        capture=capture,
        lake=lake,
        run_id="run-r33-roots-with-provider",
        provenance=generated_by(decision),
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
    )
    with pytest.raises(NoConfirmedSources):
        loop.discover_sources(tmp_path, ontology, decision)

    assert provider_url in calls
    assert root_url in calls
    assert root_url in loop.result.artifact["leads"]


def test_cross_domain_redirect_is_a_separate_review_lead(tmp_path: Path) -> None:
    ontology = _library_case(tmp_path, policy=POLICY)
    decision = library_decisions()
    source = "https://libraries.example.test/branches"
    destination = "https://records.other.example/registry"
    chain = [source, destination]
    lake = FileLake(tmp_path / "lake")
    calls: list[str] = []

    def capture(url: str, **kwargs) -> dict:
        calls.append(url)
        if url == destination:
            pytest.fail("an unapproved redirect target must not be captured in this run")
        if url != source:
            key = lake.put_bytes(PAGE.format(title="Public records").encode())
            return {
                "url": url,
                "redirect_chain": [url],
                "status": 200,
                "html_key": key,
                "screenshot_key": lake.put_bytes(b"synthetic screenshot"),
                "trace": [],
            }
        assert "libraries.example.test" in kwargs["allowed_domains"]
        raise CaptureBlocked(
            "redirect left the job allowlist",
            [],
            {
                "url": destination,
                "status": 0,
                "redirect_chain": chain,
                "allowed_domains": kwargs["allowed_domains"],
                "proof": {
                    "dispatch_result": {
                        "reason": "redirect_outside_allowlist",
                        "redirect_chain": chain,
                        "allowed_domains": kwargs["allowed_domains"],
                    }
                },
            },
        )

    loop = DiscoveryLoop(
        [StaticProvider("synthetic", {source: (ontology["properties"][0]["id"],)})],
        capture=capture,
        lake=lake,
        run_id="run-r33-redirect",
        provenance=generated_by(decision),
        budget=LoopBudget(max_iterations=2, wall_seconds=60),
    )
    with pytest.raises(NoConfirmedSources) as stopped:
        loop.discover_sources(tmp_path, ontology, decision)

    assert source in calls
    assert destination not in calls
    redirect_lead = next(
        lead for lead in loop.result.artifact["leads"].values() if lead["url"] == destination
    )
    assert redirect_lead["redirect_chain"] == chain
    assert redirect_lead["redirect_review_required"] is True
    source_id = redirect_lead["review_source_id"]
    packet = tmp_path / "03-fanout/sources" / source_id
    assert (packet / "candidate.json").exists()
    assert (packet / "APPROVAL_PENDING.md").exists()
    assert stopped.value.checkpoint_pending == "source"
    assert stopped.value.review_source_ids == [source_id]


def test_metadata_redirect_is_never_queued_as_a_source(tmp_path: Path) -> None:
    ontology = _library_case(tmp_path, policy=POLICY)
    decision = library_decisions()
    source = "https://libraries.example.test/branches"
    blocked_target = "http://169.254.169.254/latest/meta-data"
    chain = [source, blocked_target]
    lake = FileLake(tmp_path / "lake")
    calls: list[str] = []

    def capture(url: str, **kwargs) -> dict:
        calls.append(url)
        if url != source:
            key = lake.put_bytes(PAGE.format(title="Public records").encode())
            return {
                "url": url,
                "redirect_chain": [url],
                "status": 200,
                "html_key": key,
                "screenshot_key": lake.put_bytes(b"synthetic screenshot"),
                "trace": [],
            }
        raise CaptureBlocked(
            "redirect target blocked",
            [],
            {
                "url": blocked_target,
                "redirect_chain": chain,
                "allowed_domains": kwargs["allowed_domains"],
                "proof": {
                    "dispatch_result": {
                        "reason": "redirect_outside_allowlist",
                        "redirect_chain": chain,
                        "allowed_domains": kwargs["allowed_domains"],
                    }
                },
            },
        )

    loop = DiscoveryLoop(
        [StaticProvider("synthetic", {source: (ontology["properties"][0]["id"],)})],
        capture=capture,
        lake=lake,
        run_id="run-r33-metadata",
        provenance=generated_by(decision),
        budget=LoopBudget(max_iterations=2, wall_seconds=60),
    )
    with pytest.raises(NoConfirmedSources):
        loop.discover_sources(tmp_path, ontology, decision)

    assert source in calls
    assert blocked_target not in calls
    assert all(lead["url"] != blocked_target for lead in loop.result.artifact["leads"].values())
    assert not list((tmp_path / "03-fanout/sources").glob("*/APPROVAL_PENDING.md"))


def test_approved_unrelated_publisher_is_queued_for_a_new_capture_job(tmp_path: Path) -> None:
    policy = _single_domain_policy()
    policy["trusted_publishers"].append(
        {
            "kind": "other public publisher",
            "tier": "primary",
            "domains": ["archive.other.test"],
            "rationale": "Synthetic separately approved publisher",
        }
    )
    ontology = _library_case(tmp_path, policy=policy)
    decision = library_decisions()
    source = "https://records.example.test/start"
    destination = "https://archive.other.test/catalog"
    chain = [source, destination]
    lake = FileLake(tmp_path / "lake")
    calls: list[str] = []
    html = PAGE.format(title="Public records")

    def capture(url: str, **kwargs) -> dict:
        calls.append(url)
        if url == source:
            raise CaptureBlocked(
                "redirect left the job allowlist",
                [],
                {
                    "url": destination,
                    "redirect_chain": chain,
                    "allowed_domains": kwargs["allowed_domains"],
                    "proof": {"dispatch_result": {"redirect_chain": chain}},
                },
            )
        if url == destination:
            assert "archive.other.test" in kwargs["allowed_domains"]
        key = lake.put_bytes(html.encode())
        return {
            "url": url,
            "redirect_chain": [url],
            "status": 200,
            "html_key": key,
            "screenshot_key": lake.put_bytes(b"synthetic screenshot"),
            "trace": [],
        }

    loop = DiscoveryLoop(
        [StaticProvider("synthetic", {source: (ontology["properties"][0]["id"],)})],
        capture=capture,
        lake=lake,
        run_id="run-r33-approved-redirect",
        provenance=generated_by(decision),
        budget=LoopBudget(max_iterations=2, wall_seconds=60),
    )
    with pytest.raises(NoConfirmedSources):
        loop.discover_sources(tmp_path, ontology, decision)
    assert source in calls
    assert destination in calls
    assert calls.index(destination) > calls.index(source)


def test_redirect_review_requires_current_digest_and_preserves_packet_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ontofill.phases.p3_fanout import discovery_loop as discovery_loop_module

    validation_events: list[tuple[str, str, str | None]] = []
    real_validate = discovery_loop_module.validate_document
    real_write = discovery_loop_module.write_json

    def record_validation(schema_name: str, document: object) -> None:
        if schema_name == "source-candidate" and isinstance(document, dict):
            validation_events.append(("validate", str(document.get("url")), None))
        real_validate(schema_name, document)

    def record_write(path: Path, document: object) -> None:
        if path.name in {"candidate.json", "capture.json"} and isinstance(document, dict):
            validation_events.append(("write", str(document.get("url")), path.name))
        real_write(path, document)

    monkeypatch.setattr(discovery_loop_module, "validate_document", record_validation)
    monkeypatch.setattr(discovery_loop_module, "write_json", record_write)
    ontology = _library_case(tmp_path, policy=_single_domain_policy())
    decision = library_decisions()
    provenance = {
        "backend": "vultr",
        "model": "synthetic-live",
        "at": datetime.now(UTC).isoformat(),
    }
    source = "https://records.example.test/start"
    destination = "https://registry.unknown.test/records"
    chain = [source, destination]
    lake = FileLake(tmp_path / "lake")
    calls: list[str] = []

    def capture(url: str, **kwargs) -> dict:
        calls.append(url)
        if url == source:
            raise CaptureBlocked(
                "redirect left the job allowlist",
                [],
                {
                    "url": destination,
                    "redirect_chain": chain,
                    "allowed_domains": kwargs["allowed_domains"],
                    "proof": {"dispatch_result": {"redirect_chain": chain}},
                },
            )
        if url == destination:
            key = lake.put_bytes(PAGE.format(title="Public records").encode())
            return {
                "url": url,
                "redirect_chain": [url],
                "status": 200,
                "html_key": key,
                "screenshot_key": lake.put_bytes(b"synthetic screenshot"),
                "trace": [],
            }
        key = lake.put_bytes(PAGE.format(title="Public records").encode())
        return {
            "url": url,
            "redirect_chain": [url],
            "status": 200,
            "html_key": key,
            "screenshot_key": lake.put_bytes(b"synthetic screenshot"),
            "trace": [],
        }

    first = DiscoveryLoop(
        [StaticProvider("synthetic", {source: (ontology["properties"][0]["id"],)})],
        capture=capture,
        lake=lake,
        run_id="run-r33-review-pending",
        provenance=provenance,
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
    )
    with pytest.raises(NoConfirmedSources):
        first.discover_sources(tmp_path, ontology, decision)
    assert source in calls
    review_lead = next(
        lead for lead in first.result.artifact["leads"].values() if lead["url"] == destination
    )
    source_id = review_lead["review_source_id"]
    candidate_path = tmp_path / "03-fanout/sources" / source_id / "candidate.json"
    pending_path = candidate_path.parent / "APPROVAL_PENDING.md"
    candidate_bytes = candidate_path.read_bytes()
    pending_bytes = pending_path.read_bytes()
    candidate_write = validation_events.index(("write", destination, "candidate.json"))
    assert validation_events[candidate_write - 1] == ("validate", destination, None)
    manifest = load_json(candidate_path)
    approval_path = candidate_path.parent / "APPROVED"
    relative = candidate_path.relative_to(tmp_path).as_posix()
    approval = {
        "approver": "synthetic reviewer",
        "date": "2026-09-27",
        "checkpoint": "source",
        "source_fingerprint": manifest["fingerprint"],
        "identity_source": "local",
        "artifact_sha256": {relative: "0" * 64},
    }
    write_json(approval_path, approval)

    rejected_resume = DiscoveryLoop(
        [EmptyProvider()],
        capture=capture,
        lake=lake,
        run_id="run-r33-review-wrong-fingerprint",
        provenance=provenance,
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
    )
    from ontofill.case.checkpoints import ApprovalArtifactMismatch

    with pytest.raises(ApprovalArtifactMismatch):
        rejected_resume.discover_sources(tmp_path, ontology, decision)
    assert destination not in calls
    assert candidate_path.read_bytes() == candidate_bytes
    assert pending_path.read_bytes() == pending_bytes
    approval["artifact_sha256"] = {relative: hashlib.sha256(candidate_bytes).hexdigest()}
    write_json(approval_path, approval)

    resumed = DiscoveryLoop(
        [EmptyProvider()],
        capture=capture,
        lake=lake,
        run_id="run-r33-review-approved",
        provenance=provenance,
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
    )
    try:
        resumed.discover_sources(tmp_path, ontology, decision)
    except NoConfirmedSources:
        pass
    assert destination in calls
    assert candidate_path.read_bytes() == candidate_bytes
    capture_write = validation_events.index(("write", destination, "capture.json"))
    assert validation_events[capture_write - 1] == ("validate", destination, None)


def test_denied_redirect_review_never_schedules_target_capture(tmp_path: Path) -> None:
    ontology = _library_case(tmp_path, policy=_single_domain_policy())
    decision = library_decisions()
    provenance = {
        "backend": "vultr",
        "model": "synthetic-live",
        "at": datetime.now(UTC).isoformat(),
    }
    source = "https://records.example.test/start"
    destination = "https://registry.unknown.test/records"
    viable = "https://records.example.test/catalog"
    chain = [source, destination]
    lake = FileLake(tmp_path / "lake")
    calls: list[str] = []

    def capture(url: str, **kwargs) -> dict:
        calls.append(url)
        if url == destination:
            pytest.fail("a denied source must never be captured")
        if url != source:
            key = lake.put_bytes(PAGE.format(title="Public records").encode())
            return {
                "url": url,
                "redirect_chain": [url],
                "status": 200,
                "html_key": key,
                "screenshot_key": lake.put_bytes(b"synthetic screenshot"),
                "trace": [],
            }
        raise CaptureBlocked(
            "redirect left the job allowlist",
            [],
            {
                "url": destination,
                "redirect_chain": chain,
                "allowed_domains": kwargs["allowed_domains"],
                "proof": {"dispatch_result": {"redirect_chain": chain}},
            },
        )

    first = DiscoveryLoop(
        [StaticProvider("synthetic", {source: (ontology["properties"][0]["id"],)})],
        capture=capture,
        lake=lake,
        run_id="run-r33-denied-pending",
        provenance=provenance,
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
    )
    with pytest.raises(NoConfirmedSources):
        first.discover_sources(tmp_path, ontology, decision)
    redirect_lead = next(
        lead for lead in first.result.artifact["leads"].values() if lead["url"] == destination
    )
    candidate_path = (
        tmp_path / "03-fanout/sources" / redirect_lead["review_source_id"] / "candidate.json"
    )
    candidate_bytes = candidate_path.read_bytes()
    pending_path = candidate_path.parent / "APPROVAL_PENDING.md"
    pending_bytes = pending_path.read_bytes()
    pending_mtime = pending_path.stat().st_mtime_ns
    relative = candidate_path.relative_to(tmp_path).as_posix()
    write_json(
        candidate_path.parent / "APPROVED",
        {
            "approver": "synthetic reviewer",
            "date": "2026-09-27",
            "checkpoint": "source",
            "identity_source": "local",
            "source_fingerprint": redirect_lead["review_fingerprint"],
            "artifact_sha256": {relative: hashlib.sha256(candidate_bytes).hexdigest()},
            "decision": "deny",
            "reason": "synthetic refusal",
        },
    )
    provider = StaticProvider(
        "synthetic-resume",
        {
            source: (ontology["properties"][0]["id"],),
            viable: tuple(item["id"] for item in ontology["properties"]),
        },
    )
    resumed = DiscoveryLoop(
        [provider],
        capture=capture,
        lake=lake,
        run_id="run-r33-denied-resume",
        provenance=provenance,
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
    )
    try:
        document = resumed.discover_sources(tmp_path, ontology, decision)
    except NoConfirmedSources as stopped:
        assert stopped.checkpoint_pending is None
    else:
        assert any(item["source_url"] == viable for item in document["objectives"])
    assert provider.calls > 0
    assert resumed.result.artifact["candidates"][viable]["status"] == "confirmed"
    assert viable in calls
    assert destination not in calls
    assert candidate_path.read_bytes() == candidate_bytes
    assert pending_path.read_bytes() == pending_bytes
    assert pending_path.stat().st_mtime_ns == pending_mtime


def test_denied_ordinary_candidate_is_not_rewritten_or_re_requested(tmp_path: Path) -> None:
    ontology = _library_case(tmp_path, policy=_single_domain_policy())
    decision = library_decisions()
    provenance = generated_by(decision)
    url = "https://unlisted.example.test/catalog"
    source_id = "source-ordinary-denial"
    fingerprint = "a" * 64
    capture_key = "sha256:" + "b" * 64
    source_directory = tmp_path / "03-fanout/sources" / source_id
    source_directory.mkdir(parents=True)
    candidate_path = source_directory / "candidate.json"
    packet = {
        "source_id": source_id,
        "url": url,
        "title": "Synthetic catalog",
        "snippet": "A public catalog.",
        "provider": "synthetic",
        "providers": ["synthetic"],
        "capture_key": capture_key,
        "source_type": "directory",
        "authority": "review",
        "authority_tier": "unknown",
        "authority_reason": "Publisher requires review.",
        "fingerprint": fingerprint,
        "generated_by": provenance,
    }
    write_json(candidate_path, packet)
    pending_path = source_directory / "APPROVAL_PENDING.md"
    pending_path.write_text("Original pending source review\n", encoding="utf-8")
    candidate_bytes = candidate_path.read_bytes()
    pending_bytes = pending_path.read_bytes()
    relative = candidate_path.relative_to(tmp_path).as_posix()
    write_json(
        source_directory / "APPROVED",
        {
            "approver": "Synthetic reviewer",
            "date": "2026-09-27",
            "checkpoint": "source",
            "identity_source": "local",
            "source_fingerprint": fingerprint,
            "artifact_sha256": {relative: hashlib.sha256(candidate_bytes).hexdigest()},
            "decision": "deny",
            "reason": "Synthetic refusal",
        },
    )
    property_id = ontology["properties"][0]["id"]
    candidate = {
        "url": url,
        "title": packet["title"],
        "snippet": packet["snippet"],
        "source_id": source_id,
        "discovered_by": "synthetic",
        "providers": ["synthetic"],
        "query": "synthetic records query",
        "property_ids": [property_id],
        "status": "confirmed",
        "covers": [property_id],
        "capture_key": capture_key,
        "screenshot_key": None,
        "source_type": packet["source_type"],
        "authority": "review",
        "authority_tier": "unknown",
        "authority_reason": packet["authority_reason"],
        "fingerprint": fingerprint,
        "property_evidence": {},
        "redirect_chain": [url],
    }
    loop = DiscoveryLoop(
        [EmptyProvider()],
        capture=lambda *_args, **_kwargs: pytest.fail("denied source must not be captured"),
        lake=FileLake(tmp_path / "lake"),
        run_id="run-r34-ordinary-denial",
        provenance=provenance,
    )
    result = LoopResult(
        artifact={"candidates": {url: candidate}, "leads": {}},
        iterations=1,
        stop_reason="max_iterations",
        usd=0,
        objections=(),
    )
    with pytest.raises(NoConfirmedSources) as stopped:
        loop._write(
            tmp_path,
            ontology,
            decision,
            _single_domain_policy(),
            result.artifact,
            result,
            previous=None,
            ledger={},
            request_key="synthetic-denied-candidate",
            targets=(property_id,),
            required={property_id: 1},
            coverage={property_id: set()},
            max_sources=1,
        )

    assert stopped.value.checkpoint_pending is None
    assert candidate_path.read_bytes() == candidate_bytes
    assert pending_path.read_bytes() == pending_bytes
    assert not (source_directory / "capture.json").exists()


def test_status_403_retries_once_and_terminal_failure_is_counted(tmp_path: Path) -> None:
    ontology = _library_case(tmp_path, policy=_single_domain_policy())
    decision = library_decisions()
    url = "https://records.example.test/record"
    lake = FileLake(tmp_path / "lake")
    calls: list[str] = []

    def capture(target: str, **kwargs) -> dict:
        calls.append(target)
        key = lake.put_bytes(b"synthetic forbidden page")
        return {"url": target, "status": 403, "html_key": key, "trace": []}

    loop = DiscoveryLoop(
        [StaticProvider("synthetic", {url: (ontology["properties"][0]["id"],)})],
        capture=capture,
        lake=lake,
        run_id="run-r33-403",
        provenance=generated_by(decision),
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
    )
    with pytest.raises(NoConfirmedSources) as stopped:
        loop.discover_sources(tmp_path, ontology, decision)

    assert calls.count(url) == 2
    assert "sources unreachable (2 blocked/redirected/403)" in stopped.value.reason
    candidate = loop.result.artifact["candidates"][url]
    assert candidate["capture_attempts"] == 2
    assert candidate["capture_outcome"] == "blocked"


def test_captureblocked_http_403_retries_once_in_a_fresh_s1_capture(tmp_path: Path) -> None:
    ontology = _library_case(tmp_path, policy=_single_domain_policy())
    decision = library_decisions()
    url = "https://records.example.test/record"
    lake = FileLake(tmp_path / "lake")
    calls: list[str] = []

    def capture(target: str, **_kwargs) -> dict:
        calls.append(target)
        if target == url and calls.count(url) == 1:
            raise CaptureBlocked(
                "publisher refused the first request",
                [],
                {
                    "url": target,
                    "status": 403,
                    "reason": "http_403",
                    "proof": {"dispatch_result": {"status": 403, "reason": "http_403"}},
                },
            )
        key = lake.put_bytes(PAGE.format(title="Public records").encode())
        return {
            "url": target,
            "redirect_chain": [target],
            "status": 200,
            "html_key": key,
            "screenshot_key": lake.put_bytes(b"synthetic screenshot"),
            "trace": [],
        }

    loop = DiscoveryLoop(
        [StaticProvider("synthetic", {url: (ontology["properties"][0]["id"],)})],
        capture=capture,
        lake=lake,
        run_id="run-r33-captureblocked-403",
        provenance=generated_by(decision),
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
    )
    try:
        loop.discover_sources(tmp_path, ontology, decision)
    except NoConfirmedSources:
        pass

    assert calls.count(url) == 2
    assert loop.result.artifact["candidates"][url]["capture_attempts"] == 2


def test_p3_iteration_cap_scales_with_remaining_budget_and_stays_bounded() -> None:
    from ontofill.phases.p3_fanout import discovery_loop

    iteration_limit = getattr(discovery_loop, "p3_iteration_limit", lambda **_: None)
    assert iteration_limit(remaining_usd=0) == 3
    assert iteration_limit(remaining_usd=0.25) > 3
    assert iteration_limit(remaining_usd=100) == 12
    assert iteration_limit(remaining_usd=None) == 3


def urlsplit_host(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).hostname or ""


def test_workflow_stops_at_source_review_for_an_unapproved_redirect(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    original = tmp_path / "case"
    original.mkdir()
    (original / "brief.md").write_text(
        "Find public library hours and internet access in Example City.\n", encoding="utf-8"
    )
    lake = FileLake(tmp_path / "lake")

    def scratch_case(_case_dir: Path, run_id: str) -> tuple[Path, FileLake]:
        scratch = tmp_path / "scratch" / run_id
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "brief.md").write_text(
            (original / "brief.md").read_text(encoding="utf-8"), encoding="utf-8"
        )
        return scratch, lake

    monkeypatch.setattr("ontofill.workflow._scratch_case", scratch_case)
    decision = library_decisions()
    destination = "https://registry.unknown.test/records"

    class RedirectProvider(LeadProvider):
        name = "synthetic-redirect"

        def leads(self, context: LeadContext) -> list[Lead]:
            self._attempt("; ".join(q.text for q in context.queries), "ok", 1)
            return [
                Lead(
                    url="https://libraries.example.test/branches",
                    title="Library records",
                    snippet="Public library records.",
                    discovered_by=self.name,
                    query=context.queries[0].text,
                    property_ids=tuple(q.property_id for q in context.queries),
                )
            ]

    def capture(url: str, **kwargs) -> dict:
        if url != "https://libraries.example.test/branches":
            key = lake.put_bytes(PAGE.format(title="Public records").encode())
            return {
                "url": url,
                "redirect_chain": [url],
                "status": 200,
                "html_key": key,
                "screenshot_key": lake.put_bytes(b"synthetic screenshot"),
                "trace": [],
            }
        raise CaptureBlocked(
            "redirect left the job allowlist",
            [],
            {
                "url": destination,
                "redirect_chain": [url, destination],
                "allowed_domains": kwargs["allowed_domains"],
                "trace": [],
            },
        )

    discovery = DiscoveryLoop(
        [RedirectProvider()],
        capture=capture,
        lake=lake,
        run_id="mock-r33-source-pause",
        provenance=generated_by(decision),
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
    )
    outcome = run_case(
        original,
        to_phase=3,
        run_id="mock-r33-source-pause",
        preview_past_checkpoints=True,
        decision=decision,
        search_client=discovery,
        lake=lake,
    )

    assert outcome == NEEDS_HUMAN_EXIT
    status = load_json_from_lake(lake, f"runs/{original.name}/mock-r33-source-pause/status.json")
    assert status["state"] == "paused"
    assert status["checkpoint_pending"] == "source"
    assert "source authority review required" in status["reason"]
    assert "registry.unknown.test" in status["reason"]
    assert "needs_human=true" in capsys.readouterr().out


def load_json_from_lake(lake: FileLake, key: str) -> dict:
    import json

    return json.loads(lake.read_key(key))
