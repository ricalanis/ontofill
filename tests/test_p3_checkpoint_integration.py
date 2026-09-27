"""End-to-end recorded restart coverage for the durable Phase 3 cache."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import ontofill.sandbox.parse as parse_module
from ontofill.lake import FileLake
from ontofill.phase_loop import LoopBudget
from ontofill.phases.p3_fanout.discovery_loop import DiscoveryLoop
from ontofill.phases.p3_fanout.leads import LeadQuery
from tests.r17_helpers import SyntheticParseExecutor
from tests.test_discovery_loop import FakeVultr, StaticProvider, _library_case

URL = "https://libraries.example.test/branches"
PAGE = (
    "<html><body><h1>Branch directory</h1><table><thead><tr>"
    "<th>Branch name</th><th>Opening hours</th><th>Free internet</th>"
    "</tr></thead><tbody><tr><td>Central</td><td>Weekdays</td><td>Available</td>"
    "</tr></tbody></table></body></html>"
)
POLICY = {
    "jurisdiction": "Example City",
    "trusted_publishers": [
        {
            "kind": "city library office",
            "tier": "primary",
            "domains": ["libraries.example.test"],
            "rationale": "Synthetic primary publisher",
        }
    ],
    "unknown_source_action": "review",
}


def _metadata_capture(lake: FileLake, calls: list[str]):
    def capture(url: str, **kwargs) -> dict:
        calls.append(url)
        assert kwargs["phase"] == 3
        assert (urlsplit(url).hostname or "") in kwargs["allowed_domains"]
        captured_at = datetime.now(UTC).isoformat()
        source_id = kwargs["source_id"]
        trace = {
            "step_id": f"step:{uuid.uuid4().hex}",
            "run_id": kwargs["run_id"],
            "phase": 3,
            "source_id": source_id,
            "objective_id": None,
            "tdd_path": kwargs["tdd_path"],
            "mode": "S1",
            "observed": {"url": url},
            "requested": {"url": url},
            "executed": {},
            "evaluated": {"status": "captured"},
            "parent_step_id": None,
            "value_ids": [],
            "ts": captured_at,
            "generated_by": kwargs["generated_by"],
        }
        key = lake.put_bytes(
            PAGE.encode("utf-8"),
            {
                "url": url,
                "source_id": source_id,
                "captured_at": captured_at,
                "content_type": "text/html",
                "step_id": trace["step_id"],
            },
        )
        trace["executed"] = {"bronze_key": key}
        screenshot_key = lake.put_bytes(b"synthetic screenshot")
        return {
            "url": url,
            "redirect_chain": [url],
            "status": 200,
            "html": PAGE,
            "html_key": key,
            "screenshot_key": screenshot_key,
            "trace": [trace],
        }

    return capture


def _loop(case_dir: Path, run_id: str, lake: FileLake, provider, capture) -> DiscoveryLoop:
    loop = DiscoveryLoop(
        [provider],
        capture=capture,
        lake=lake,
        run_id=run_id,
        provenance={
            "backend": "vultr",
            "model": "synthetic-live",
            "at": datetime.now(UTC).isoformat(),
        },
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
        max_captures_per_iteration=1,
    )
    loop._plan_queries = lambda *_args: [  # type: ignore[method-assign]
        LeadQuery("opening_hours", "Example City library opening hours")
    ]
    return loop


def test_restart_reuses_checkpoint_without_provider_capture_or_critic_calls(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(parse_module, "DockerParseExecutor", SyntheticParseExecutor)
    ontology = _library_case(tmp_path, POLICY)
    prd_path = tmp_path / "01-scope/prd.json"
    prd = json.loads(prd_path.read_text(encoding="utf-8"))
    prd["authority_policy"] = POLICY
    prd_path.write_text(json.dumps(prd), encoding="utf-8")

    lake = FileLake(tmp_path / "lake")
    first_provider = StaticProvider("synthetic", {URL: ("opening_hours",)})
    first_capture_calls: list[str] = []
    first_loop = _loop(
        tmp_path,
        "run-first",
        lake,
        first_provider,
        _metadata_capture(lake, first_capture_calls),
    )
    decision = FakeVultr()
    critic_calls: list[str] = []
    complete_json = decision.complete_json

    def count_critic(purpose: str, prompt: str, schema: dict) -> dict:
        critic_calls.append(purpose)
        # FakeVultr supplies this optional field; current parsed evidence lets
        # the deterministic validator reconstruct the route without it.
        return complete_json(purpose, prompt, schema)

    decision.complete_json = count_critic  # type: ignore[method-assign]
    first_loop.discover_sources(tmp_path, ontology, decision, gaps=("opening_hours",))

    assert first_provider.calls == 1
    assert len(first_capture_calls) == 1
    assert critic_calls == ["critic.phase3.capability"]
    first_stop = next(
        step
        for step in reversed(first_loop.trace)
        if step.get("event") == "loop" and step.get("loop", {}).get("role") == "decide"
    )
    assert first_stop["executed"]["stop_details"] == {
        "candidate_count": 1,
        "judged_candidate_count": 1,
    }
    checkpoint_path = tmp_path / "03-fanout/cache/p3-checkpoint.json"
    assert checkpoint_path.is_file()
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["captures"] and checkpoint["critic_verdicts"]
    # Model a process stop after checkpoint persistence but before Phase 3's
    # completed ledger/objectives are published.
    for published in (
        tmp_path / "03-fanout/objectives.json",
        tmp_path / "03-fanout/surface-map/discovery.json",
        tmp_path / "03-fanout/surface-map/leads.json",
    ):
        published.unlink(missing_ok=True)

    second_provider = StaticProvider("synthetic", {URL: ("opening_hours",)})
    second_capture_calls: list[str] = []
    second_loop = _loop(
        tmp_path,
        "run-second",
        lake,
        second_provider,
        _metadata_capture(lake, second_capture_calls),
    )
    second_loop.discover_sources(tmp_path, ontology, decision, gaps=("opening_hours",))

    assert second_provider.calls == 0
    assert second_capture_calls == []
    assert critic_calls == ["critic.phase3.capability"]
    assert any(
        step.get("executed", {}).get("status") == "reused from checkpoint"
        for step in second_loop.trace
    )
    assert any(
        step.get("executed", {}).get("status") == "reused from checkpoint"
        and step.get("requested", {}).get("tool") == "critic.phase3.capability"
        for step in second_loop.trace
    )
    assert all(step.get("run_id") != "run-first" for step in second_loop.trace)


def test_changed_approval_fingerprint_drops_only_policy_bound_parts() -> None:
    from ontofill.phases.p3_fanout.checkpoint import check
    from tests.test_p3_checkpoint import NOW, make_checkpoint, make_inputs

    original_inputs = make_inputs()
    result = check(
        make_checkpoint(original_inputs),
        replace(original_inputs, approvals_digest="9" * 64),
        now=NOW,
    )

    present = {
        name
        for name, values in (
            ("query_runs", result.checkpoint.query_runs),
            ("leads", result.checkpoint.leads),
            ("captures", result.checkpoint.captures),
            ("critic_verdicts", result.checkpoint.critic_verdicts),
            ("iteration_count", result.checkpoint.iteration_count),
            ("gap_state", result.checkpoint.gap_state),
        )
        if bool(values)
    }

    assert present == {"query_runs", "iteration_count"}
    assert {"leads", "captures", "critic_verdicts", "gap_state"}.issubset(result.invalidated_parts)
