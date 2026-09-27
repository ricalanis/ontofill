"""Synthetic coverage for blocked edge interstitials in the P3 capture path."""

from __future__ import annotations

from ontofill.lake import FileLake
from ontofill.phase_loop import LoopBudget
from ontofill.phases.p3_fanout.discovery_loop import DiscoveryLoop
from ontofill.phases.p3_fanout.leads import LeadProvider
from ontofill.sandbox import CaptureBlocked


class _SyntheticProvider(LeadProvider):
    name = "synthetic"


def test_bot_challenge_remains_blocked_and_suggests_an_alternate_channel(tmp_path):
    url = "https://catalog.example.test/public-records"
    blocked = CaptureBlocked(
        "sandbox navigation failed",
        [],
        {
            "url": url,
            "status": 403,
            "reason": "bot_challenge",
            "proof": {"dispatch_result": {"status": 403, "reason": "bot_challenge"}},
        },
    )

    def capture(_url: str, **_kwargs) -> dict:
        raise blocked

    loop = DiscoveryLoop(
        [_SyntheticProvider()],
        capture=capture,
        lake=FileLake(tmp_path / "lake"),
        run_id="synthetic-r53",
        provenance={"backend": "recorded", "model": "synthetic"},
        budget=LoopBudget(max_iterations=1, wall_seconds=10),
    )
    candidate = {"url": url, "title": "Public records", "providers": ["synthetic"]}

    assert loop._capture_lead(candidate, {}, {}, None, tmp_path) is None
    assert candidate["status"] == "inconclusive"
    assert candidate["capture_reason"] == "bot_challenge"
    assert candidate["capture_outcome"] == "blocked"
    assert candidate["capture_attempts"] == 1
    assert "open-data portal" in candidate["alternate_channel_hint"]
    assert "do not retry or bypass" in candidate["alternate_channel_hint"]
