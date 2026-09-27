"""R33b proves an unknown redirect is previewed once before source review."""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from ontofill.case.checkpoints import load_json, write_json
from ontofill.contracts import validate_document
from ontofill.inference import generated_by
from ontofill.lake import FileLake
from ontofill.phase_loop import LoopBudget
from ontofill.phases.p3_fanout import discovery_loop as discovery_module
from ontofill.phases.p3_fanout.discovery_loop import DiscoveryLoop, NoConfirmedSources
from ontofill.sandbox import CaptureBlocked
from ontofill.sandbox import parse as parse_module
from ontofill.sandbox.capture import CaptureError
from ontofill.sandbox.domains import same_registrable_domain
from tests.r17_helpers import SyntheticParseExecutor
from tests.test_discovery_loop import POLICY, StaticProvider, _library_case
from tests.test_r35_p3_capability import CapabilityCritic


@pytest.fixture(autouse=True)
def synthetic_parse_pod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_module, "DockerParseExecutor", SyntheticParseExecutor)


def _chain_block(source: str, destination: str) -> CaptureBlocked:
    chain = [source, destination]
    return CaptureBlocked(
        "redirect left the source host allowlist",
        [],
        {
            "url": destination,
            "status": 0,
            "redirect_chain": chain,
            "proof": {
                "dispatch_result": {
                    "reason": "redirect_outside_allowlist",
                    "redirect_chain": chain,
                }
            },
        },
    )


def _tls_aia_failure(source: str, destination: str) -> CaptureError:
    chain = [source, destination]
    return CaptureError(
        "synthetic TLS intermediate fetch failure",
        [],
        {
            "url": destination,
            "status": 0,
            "redirect_chain": chain,
            "capture_reason": "tls_aia_failure",
            "proof": {
                "dispatch_result": {
                    "reason": "tls_aia_failure",
                    "redirect_chain": chain,
                }
            },
        },
    )


def _preview_html() -> str:
    return (
        "<html><head><title>City Library Office Registry</title></head><body>"
        "Search the official city library office directory."
        '<form role="search"><label>Library name'
        '<input type="search" name="library_query"></label>'
        '<button type="submit">Search records</button></form></body></html>'
    )


def _new_loop(
    lake: FileLake,
    capture,
    decision: CapabilityCritic,
    *,
    run_id: str,
) -> DiscoveryLoop:
    return DiscoveryLoop(
        [StaticProvider("synthetic_redirect", {ORIGIN: ("opening_hours", "free_internet")})],
        capture=capture,
        lake=lake,
        run_id=run_id,
        provenance=generated_by(decision),
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
        max_captures_per_iteration=1,
        parse_executor=SyntheticParseExecutor(),
    )


ORIGIN = "https://libraries.example.test/branches"
TARGET = "https://registry.other.test/catalog"
TARGET_HOST = urlsplit(TARGET).hostname


def test_same_host_http_upgrade_failure_does_not_mint_review_candidate(tmp_path: Path) -> None:
    ontology = _library_case(tmp_path, policy=POLICY)
    source = "http://portal.example.test/branches"
    destination = "https://portal.example.test/branches"
    decision = CapabilityCritic()
    lake = FileLake(tmp_path / "lake")
    calls: list[str] = []
    preview = _preview_html()

    def capture(url: str, **_kwargs) -> dict:
        calls.append(url)
        if url == source:
            raise _tls_aia_failure(source, destination)
        assert url == destination
        key = lake.put_bytes(preview.encode(), {"url": url})
        return {
            "url": url,
            "redirect_chain": [url],
            "status": 200,
            "html_key": key,
            "screenshot_key": lake.put_bytes(b"synthetic screenshot"),
            "trace": [],
        }

    loop = _new_loop(lake, capture, decision, run_id="run-r33b-same-host-failure")
    candidate = {
        "url": source,
        "title": "Synthetic portal",
        "snippet": "Synthetic public records",
        "property_ids": ["opening_hours"],
        "iteration": 1,
        "providers": ["synthetic_redirect"],
        "score": 0.0,
    }

    assert loop._capture_lead(candidate, POLICY, ontology, decision, tmp_path) is None
    assert candidate["status"] == "capture_failed"
    assert candidate["capture_reason"] == "tls_aia_failure"
    assert candidate["capture_outcome"] == "failed"
    assert calls == [source]
    source_root = tmp_path / "03-fanout/sources"
    assert not list(source_root.glob("*/candidate.json"))
    assert not list(source_root.glob("*/APPROVAL_PENDING.md"))


def test_cross_host_sibling_redirect_still_requires_review_under_same_psl_root(
    tmp_path: Path,
) -> None:
    ontology = _library_case(tmp_path, policy=POLICY)
    destination = "https://catalog.example.test/branches"
    assert (urlsplit(destination).hostname or "") != (urlsplit(ORIGIN).hostname or "")
    assert same_registrable_domain(ORIGIN, destination)
    decision = CapabilityCritic()
    lake = FileLake(tmp_path / "lake")
    calls: list[str] = []
    preview = _preview_html()

    def capture(url: str, **_kwargs) -> dict:
        calls.append(url)
        if url == ORIGIN:
            raise _chain_block(ORIGIN, destination)
        assert url == destination
        key = lake.put_bytes(preview.encode(), {"url": url})
        return {
            "url": url,
            "redirect_chain": [url],
            "status": 200,
            "html_key": key,
            "screenshot_key": lake.put_bytes(b"synthetic screenshot"),
            "trace": [],
        }

    loop = _new_loop(lake, capture, decision, run_id="run-r33b-same-registrable-host")

    with pytest.raises(NoConfirmedSources) as stopped:
        loop.discover_sources(tmp_path, ontology, decision, gaps=("opening_hours",))

    assert stopped.value.checkpoint_pending == "source"
    assert calls == [ORIGIN, destination]
    packet_path = next((tmp_path / "03-fanout/sources").glob("*/candidate.json"))
    packet = load_json(packet_path)
    assert packet["url"] == destination
    assert packet["authority"] == "review"
    assert packet["redirect_chain"] == [ORIGIN, destination]
    assert (packet_path.parent / "APPROVAL_PENDING.md").is_file()


def test_redirect_preview_is_single_host_capture_with_critic_evidence_before_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ontology = _library_case(tmp_path, policy=POLICY)
    # The source has two requested DoD properties. The preview critic may only add
    # the access paths it validates against the bounded parse-pod result.
    properties = {item["id"] for item in ontology["properties"]}
    target_properties = ("opening_hours", "free_internet")
    assert set(target_properties) <= properties
    decision = CapabilityCritic()
    lake = FileLake(tmp_path / "lake")
    calls: list[tuple[str, list[str], list[str], int, int]] = []
    preview = _preview_html()

    def capture(url: str, **kwargs) -> dict:
        if url == ORIGIN:
            calls.append((url, list(kwargs["allowed_domains"]), [], 0, 0))
            raise _chain_block(ORIGIN, TARGET)
        limits = kwargs["limits"]
        calls.append(
            (
                url,
                list(kwargs["allowed_domains"]),
                list(kwargs["exact_hosts"]),
                limits.timeout_s,
                limits.max_steps,
            )
        )
        assert url == TARGET
        key = lake.put_bytes(preview.encode(), {"url": url})
        return {
            "url": url,
            "redirect_chain": [url],
            "status": 200,
            "html_key": key,
            "screenshot_key": lake.put_bytes(b"synthetic screenshot"),
            "trace": [],
        }

    requested_packet: list[dict] = []
    require_approval = discovery_module.require_approval

    def observe_packet_before_request(directory: Path, **kwargs):
        requested_packet.append(load_json(directory / "candidate.json"))
        return require_approval(directory, **kwargs)

    monkeypatch.setattr(discovery_module, "require_approval", observe_packet_before_request)
    loop = _new_loop(lake, capture, decision, run_id="run-r33b-preview")

    with pytest.raises(NoConfirmedSources) as stopped:
        loop.discover_sources(tmp_path, ontology, decision, gaps=target_properties)

    assert stopped.value.checkpoint_pending == "source"
    assert calls[0][0] == ORIGIN
    assert calls[1] == (TARGET, [TARGET_HOST], [TARGET_HOST], 30, 8)
    assert len(calls) == 2
    assert len(requested_packet) == 1
    packet = requested_packet[0]
    packet_path = tmp_path / "03-fanout/sources" / packet["source_id"] / "candidate.json"
    assert load_json(packet_path) == packet
    assert packet["url"] == TARGET
    assert packet["landing_url"] == TARGET
    assert packet["title"].startswith("Search the official city library office directory.")
    assert packet["screenshot_key"]
    assert packet["capture_key"] == lake.put_bytes(preview.encode(), {"url": TARGET})
    assert packet["source_type"] == "city_directory"
    assert packet["authority"] == "review"
    assert packet["authority_tier"] == "primary"
    assert packet["authority_reason"].startswith("publisher authority needs human review")
    assert "full trusted policy publisher kind 'city library office'" in packet["authority_reason"]
    assert set(packet["covers"]) == set(target_properties)
    assert set(packet["access_path"]) == set(target_properties)
    assert all(item["kind"] == "search_form" for item in packet["access_path"].values())
    assert all(item["authority_verdict"] == "unknown" for item in packet["access_path"].values())
    assert packet["redirect_chain"] == [ORIGIN, TARGET]
    assert packet["fingerprint"] == discovery_module.source_fingerprint(
        url=packet["url"],
        title=packet["title"],
        snippet=packet["snippet"],
        provider=packet["provider"],
        capture_key=packet["capture_key"],
        authority_policy=POLICY,
        access_path=packet["access_path"],
    )
    validate_document("source-candidate", packet)


def test_preview_blocks_a_further_host_without_following_it(tmp_path: Path) -> None:
    ontology = _library_case(tmp_path, policy=POLICY)
    decision = CapabilityCritic()
    lake = FileLake(tmp_path / "lake")
    next_host = "https://downloads.third.test/records"
    calls: list[tuple[str, list[str], list[str]]] = []

    def capture(url: str, **kwargs) -> dict:
        calls.append((url, list(kwargs["allowed_domains"]), list(kwargs.get("exact_hosts", []))))
        if url == ORIGIN:
            raise _chain_block(ORIGIN, TARGET)
        assert url == TARGET
        chain = [TARGET, next_host]
        raise CaptureBlocked(
            "preview redirect left the exact host allowlist",
            [],
            {
                "url": next_host,
                "redirect_chain": chain,
                "allowed_domains": kwargs["allowed_domains"],
                "proof": {"dispatch_result": {"redirect_chain": chain}},
            },
        )

    loop = _new_loop(lake, capture, decision, run_id="run-r33b-follow-on")

    with pytest.raises(NoConfirmedSources) as stopped:
        loop.discover_sources(tmp_path, ontology, decision, gaps=("opening_hours",))

    assert stopped.value.checkpoint_pending == "source"
    assert calls[0][0] == ORIGIN
    assert calls[1] == (TARGET, [TARGET_HOST], [TARGET_HOST])
    assert len(calls) == 2
    packet = next((tmp_path / "03-fanout/sources").glob("*/candidate.json"))
    candidate = load_json(packet)
    assert candidate["capture_key"] is None
    assert candidate.get("screenshot_key") is None
    assert candidate["authority"] == "review"
    assert candidate["redirect_chain"] == [ORIGIN, TARGET]
    assert candidate.get("landing_url") is None
    assert candidate.get("covers", []) == []


def test_denial_after_preview_skips_capture_and_preserves_packet_bytes(tmp_path: Path) -> None:
    ontology = _library_case(tmp_path, policy=POLICY)
    decision = CapabilityCritic()
    lake = FileLake(tmp_path / "lake")
    calls: list[str] = []
    preview = _preview_html()

    def capture(url: str, **kwargs) -> dict:
        calls.append(url)
        if url == ORIGIN:
            raise _chain_block(ORIGIN, TARGET)
        assert url == TARGET
        key = lake.put_bytes(preview.encode(), {"url": url})
        return {
            "url": url,
            "redirect_chain": [url],
            "status": 200,
            "html_key": key,
            "screenshot_key": lake.put_bytes(b"synthetic screenshot"),
            "trace": [],
        }

    first = _new_loop(lake, capture, decision, run_id="run-r33b-before-denial")
    with pytest.raises(NoConfirmedSources):
        first.discover_sources(tmp_path, ontology, decision, gaps=("opening_hours",))
    packet_path = next((tmp_path / "03-fanout/sources").glob("*/candidate.json"))
    pending_path = packet_path.parent / "APPROVAL_PENDING.md"
    packet_bytes = packet_path.read_bytes()
    pending_bytes = pending_path.read_bytes()
    packet = load_json(packet_path)
    relative = packet_path.relative_to(tmp_path).as_posix()
    write_json(
        packet_path.parent / "APPROVED",
        {
            "approver": "synthetic reviewer",
            "date": "2026-09-27",
            "checkpoint": "source",
            "source_fingerprint": packet["fingerprint"],
            "identity_source": "local",
            "artifact_sha256": {relative: hashlib.sha256(packet_bytes).hexdigest()},
            "decision": "deny",
            "reason": "synthetic refusal",
        },
    )

    resumed = _new_loop(lake, capture, decision, run_id="run-r33b-after-denial")
    with pytest.raises(NoConfirmedSources) as stopped:
        resumed.discover_sources(tmp_path, ontology, decision, gaps=("opening_hours",))

    assert stopped.value.checkpoint_pending is None
    assert calls.count(TARGET) == 1
    assert packet_path.read_bytes() == packet_bytes
    assert pending_path.read_bytes() == pending_bytes
