"""R39: jurisdiction-mismatched government hosts stop before capture or review."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from ontofill.inference import generated_by
from ontofill.lake import FileLake
from ontofill.phase_loop import LoopBudget
from ontofill.phases.p3_fanout.discovery_loop import DiscoveryLoop, NoConfirmedSources
from ontofill.sandbox import CaptureBlocked, parse as parse_module
from tests.r17_helpers import SyntheticParseExecutor
from tests.test_discovery_loop import StaticProvider, _library_case
from tests.test_r35_p3_capability import CapabilityCritic


POLICY_MEXICO = {
    "jurisdiction": "Mexico",
    "trusted_publishers": [
        {
            "kind": "national tax authority",
            "tier": "primary",
            "jurisdiction": "Mexico",
            "domains": ["sat.gob.mx"],
            "rationale": "Approved national tax publisher for the synthetic Mexico policy.",
        }
    ],
    "unknown_source_action": "review",
}

ORIGIN = "https://sat.gob.mx/records"
PERU_TARGET = "https://www.gob.pe/satlima"
MEXICO_TARGET = "https://www.gob.mx/satlima"


@pytest.fixture(autouse=True)
def synthetic_parse_pod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_module, "DockerParseExecutor", SyntheticParseExecutor)


def _redirect_block(source: str, destination: str) -> CaptureBlocked:
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


def _loop(
    tmp_path: Path, capture, url: str, *, max_captures: int = 1
) -> tuple[DiscoveryLoop, CapabilityCritic]:
    decision = CapabilityCritic()
    loop = DiscoveryLoop(
        [StaticProvider("synthetic_policy_search", {url: ("opening_hours",)})],
        capture=capture,
        lake=FileLake(tmp_path / "lake"),
        run_id="run-r39-jurisdiction",
        provenance=generated_by(decision),
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
        max_captures_per_iteration=max_captures,
        parse_executor=SyntheticParseExecutor(),
    )
    return loop, decision


def _recorded_rejection(loop: DiscoveryLoop) -> dict:
    return next(
        step["evaluated"]
        for step in loop.trace
        if step.get("evaluated", {}).get("reason_code") == "government_jurisdiction_mismatch"
    )


def test_wrong_country_government_redirect_is_rejected_before_preview_or_review(
    tmp_path: Path,
) -> None:
    ontology = _library_case(tmp_path, policy=POLICY_MEXICO)
    calls: list[str] = []

    def capture(url: str, **_kwargs) -> dict:
        calls.append(url)
        assert url == ORIGIN
        raise _redirect_block(ORIGIN, PERU_TARGET)

    loop, decision = _loop(tmp_path, capture, ORIGIN)
    with pytest.raises(NoConfirmedSources) as stopped:
        loop.discover_sources(tmp_path, ontology, decision, gaps=("opening_hours",))

    assert calls == [ORIGIN]
    assert stopped.value.checkpoint_pending is None
    assert not list((tmp_path / "03-fanout/sources").glob("*/APPROVAL_PENDING.md"))
    rejected = _recorded_rejection(loop)
    assert rejected["reason_code"] == "government_jurisdiction_mismatch"
    assert rejected["candidate_host"] == "www.gob.pe"
    assert rejected["observed_country_suffix"] == "pe"
    assert rejected["expected_country_suffixes"] == ["mx"]
    summary_row = stopped.value.summary["jurisdiction_rejections"][0]
    assert summary_row == {
        "reason_code": "government_jurisdiction_mismatch",
        "candidate_host": "www.gob.pe",
        "observed_country_suffix": "pe",
        "expected_country_suffixes": ["mx"],
        "policy_jurisdiction": "Mexico",
    }
    ledger = json.loads(
        (tmp_path / "03-fanout/surface-map/discovery.json").read_text(encoding="utf-8")
    )
    assert ledger["rounds"][-1]["jurisdiction_rejections"] == [summary_row]


def test_direct_wrong_country_government_lead_is_rejected_before_capture(tmp_path: Path) -> None:
    ontology = _library_case(tmp_path, policy=POLICY_MEXICO)
    calls: list[str] = []

    def capture(url: str, **kwargs) -> dict:
        calls.append(url)
        assert url == "https://sat.gob.mx/"
        html = "<html><body>Generic public information</body></html>"
        key = kwargs["lake"].put_bytes(html.encode("utf-8"), {"url": url})
        return {
            "url": url,
            "redirect_chain": [url],
            "status": 200,
            "html_key": key,
            "screenshot_key": kwargs["lake"].put_bytes(b"synthetic screenshot"),
            "trace": [],
        }

    loop, decision = _loop(tmp_path, capture, PERU_TARGET, max_captures=2)
    with pytest.raises(NoConfirmedSources) as stopped:
        loop.discover_sources(tmp_path, ontology, decision, gaps=("opening_hours",))

    assert calls == ["https://sat.gob.mx/"]
    assert PERU_TARGET not in calls
    assert stopped.value.checkpoint_pending is None
    assert not list((tmp_path / "03-fanout/sources").glob("*/APPROVAL_PENDING.md"))
    assert _recorded_rejection(loop)["candidate_host"] == "www.gob.pe"
    assert stopped.value.summary["jurisdiction_rejections"][0]["observed_country_suffix"] == "pe"


def test_same_country_government_redirect_keeps_bounded_r33b_review_path(tmp_path: Path) -> None:
    ontology = _library_case(tmp_path, policy=POLICY_MEXICO)
    calls: list[tuple[str, list[str]]] = []
    lake = FileLake(tmp_path / "lake")
    preview_html = (
        "<html><body><h1>National tax authority records</h1>"
        "Search the public tax record registry.</body></html>"
    )

    def capture(url: str, **kwargs) -> dict:
        if url == ORIGIN:
            calls.append((url, list(kwargs["allowed_domains"])))
            raise _redirect_block(ORIGIN, MEXICO_TARGET)
        calls.append((url, list(kwargs["exact_hosts"])))
        assert url == MEXICO_TARGET
        key = lake.put_bytes(preview_html.encode("utf-8"), {"url": url})
        return {
            "url": url,
            "redirect_chain": [url],
            "status": 200,
            "html_key": key,
            "screenshot_key": lake.put_bytes(b"synthetic screenshot"),
            "trace": [],
        }

    loop, decision = _loop(tmp_path, capture, ORIGIN)
    loop.lake = lake
    with pytest.raises(NoConfirmedSources) as stopped:
        loop.discover_sources(tmp_path, ontology, decision, gaps=("opening_hours",))

    target_host = urlsplit(MEXICO_TARGET).hostname
    assert calls == [(ORIGIN, ["sat.gob.mx"]), (MEXICO_TARGET, [target_host])]
    assert stopped.value.checkpoint_pending == "source"
    assert stopped.value.summary["jurisdiction_rejections"] == []
    assert not any(
        step.get("evaluated", {}).get("reason_code") == "government_jurisdiction_mismatch"
        for step in loop.trace
    )
    packet_path = next((tmp_path / "03-fanout/sources").glob("*/candidate.json"))
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet["url"] == MEXICO_TARGET
    assert packet["authority"] == "review"
    assert packet["authority_tier"] == "primary"
    assert (packet_path.parent / "APPROVAL_PENDING.md").exists()
    ledger = json.loads(
        (tmp_path / "03-fanout/surface-map/discovery.json").read_text(encoding="utf-8")
    )
    assert ledger["rounds"][-1]["jurisdiction_rejections"] == []
