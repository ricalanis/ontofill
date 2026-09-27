"""PRD targets identify their source and ungrounded numbers stay proposed."""

import hashlib
import json
from copy import deepcopy

import httpx
import pytest

from ontofill.case.checkpoints import load_verified_approval, require_approval
from ontofill.inference import RecordedDecisionClient, VultrDecisionClient
from ontofill.phases.p1_scope.phase import (
    PrdDraftUnavailable,
    _apply_human_authority_revisions,
    _authority_policy_check,
    _ground_criteria,
    _objection_subject_changed,
    draft_prd,
)
from ontofill.phases.p3_fanout.authority import authority_result
from tests.approval_support import bind_approval


def _prd_response(criterion: dict | None = None) -> dict:
    return {
        "version": "1",
        "brief_path": "brief.md",
        "personas": [{"id": "reader", "description": "Reads public data"}],
        "jobs_to_be_done": [{"id": "find", "persona_id": "reader", "description": "Find rooms"}],
        "requirements": [{"id": "evidence", "job_id": "find", "description": "Show evidence"}],
        "constraints": ["Read-only sources"],
        "non_goals": [],
        "definition_of_done": [
            criterion
            or {
                "id": "complete-rooms",
                "metric": "Rooms with 80% of required fields",
                "operator": ">=",
                "target": 5,
                "min_ratio": 0.8,
                "basis": "brief",
                "basis_quote": "5 reading rooms, with 80% of required fields",
                "rationale": "The pilot size follows the brief.",
                "feasibility": "Five rooms appear feasible within the test budget and time.",
            }
        ],
        "authority_policy": {
            "jurisdiction": "Example City",
            "trusted_publishers": [
                {
                    "kind": "Example City archive",
                    "tier": "primary",
                    "jurisdiction": "Example City",
                    "domains": ["city.example.test"],
                    "rationale": "A local public publisher.",
                }
            ],
            "unknown_source_action": "review",
        },
    }


def _tool_response(value: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "tool_calls": [
                            {"function": {"name": "emit", "arguments": json.dumps(value)}}
                        ]
                    },
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30},
        },
    )


def test_prd_uses_planning_model_and_independent_critic(tmp_path) -> None:
    (tmp_path / "brief.md").write_text(
        "At least 5 reading rooms, with 80% of required fields per room need evidence.",
        encoding="utf-8",
    )
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) <= 3:
            fields = body["tools"][0]["function"]["parameters"]["required"]
            return _tool_response({name: _prd_response()[name] for name in fields})
        return _tool_response({"accepted": True, "reason": "Grounded in the brief"})

    decision = VultrDecisionClient(
        api_key="test-only",
        model="glm-5.3-flash",
        prd_model="glm-5.3",
        critic_model="minimax-m3",
        client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    document = draft_prd(tmp_path, decision, budget_usd=2.0)
    criterion = document["definition_of_done"][0]
    assert (criterion["basis"], criterion["min_ratio"]) == ("brief", 0.8)
    assert document["generated_by"]["model"] == "glm-5.3"
    assert [body["model"] for body in calls] == ["glm-5.3", "glm-5.3", "glm-5.3", "minimax-m3"]
    assert calls[0]["max_completion_tokens"] == 8192
    assert [call["usage"]["model"] for call in decision.call_log] == [
        "glm-5.3",
        "glm-5.3",
        "glm-5.3",
        "minimax-m3",
    ]


def test_unsupported_numeric_target_becomes_proposed(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Find public reading rooms.", encoding="utf-8")
    criterion = {
        "id": "volume",
        "metric": "reading rooms",
        "operator": ">=",
        "target": 1000,
        "basis": "brief",
        "basis_quote": "Find public reading rooms.",
    }
    decision = RecordedDecisionClient({"phase1.prd": [_prd_response(criterion)]})
    item = draft_prd(tmp_path, decision, budget_usd=1.5)["definition_of_done"][0]
    assert item["basis"] == "proposed"
    assert "$1.50" in item["feasibility"]
    assert "basis_quote" not in item


def test_human_numbers_can_be_in_separate_clauses_and_percent_is_normalized() -> None:
    reason = (
        "At least 50 reading rooms should be linked; at least 80% of them need "
        "a complete profile. Check the registry as a secondary cross-check."
    )
    revision = {"reason": reason}
    document = _prd_response(
        {
            "id": "profiles",
            "metric": "reading rooms with complete profiles",
            "operator": ">=",
            "target": 50,
            "min_ratio": 0.8,
            "basis": "human",
            "basis_quote": "At least 50 reading rooms should be linked",
        }
    )
    document["definition_of_done"].append(
        {
            "id": "percentage",
            "metric": "percentage of reading rooms with complete profiles",
            "operator": ">=",
            "target": 80,
            "min_ratio": 0.8,
            "basis": "human",
            "basis_quote": "at least 80% of them need a complete profile",
        }
    )
    _ground_criteria(document, "Find reading rooms.", [revision], 2.0)
    assert [item["basis"] for item in document["definition_of_done"]] == ["human", "human"]

    document["definition_of_done"].append(
        {
            "id": "count",
            "metric": "reading rooms total",
            "operator": ">=",
            "target": 80,
            "basis": "human",
            "basis_quote": "at least 80% of them need a complete profile",
        }
    )
    _ground_criteria(document, "Find reading rooms.", [revision], 2.0)
    assert document["definition_of_done"][-1]["basis"] == "proposed"


def test_proposed_percent_is_promoted_from_exact_human_revision() -> None:
    document = _prd_response(
        {
            "id": "complete-profiles",
            "metric": "percentage of rooms with a complete profile",
            "operator": ">=",
            "target": 80,
            "min_ratio": 0.8,
            "basis": "proposed",
            "rationale": "Model missed the human number.",
            "feasibility": "Needs source check.",
        }
    )
    reason = ">=80% of them with a complete core profile"
    _ground_criteria(document, "Find public rooms.", [{"reason": reason}], 2.0)
    criterion = document["definition_of_done"][0]
    assert criterion["basis"] == "human"
    assert criterion["basis_quote"] == reason


def test_human_primary_tier_is_applied_and_broad_domain_is_reviewed() -> None:
    document = _prd_response()
    publisher = document["authority_policy"]["trusted_publishers"][0]
    publisher.pop("tier")
    publisher["domains"] = ["gov.xy"]
    _apply_human_authority_revisions(
        document, [{"reason": "Make Example City archive the primary publisher."}]
    )
    assert publisher["tier"] == "primary"
    assert any(
        "broad namespace" in issue for issue in _authority_policy_check(document, []).objections
    )


def test_denied_number_and_embedded_percent_cannot_ground_a_count() -> None:
    document = _prd_response(
        {
            "id": "rejected",
            "metric": "reading rooms total",
            "operator": ">=",
            "target": 1000,
            "basis": "human",
            "basis_quote": "1000 is unsupported",
        }
    )
    document["definition_of_done"].append(
        {
            "id": "percentage-as-count",
            "metric": "Rooms with 80% of core fields",
            "operator": ">=",
            "target": 80,
            "basis": "human",
            "basis_quote": "at least 80% of rooms need complete fields",
        }
    )
    _ground_criteria(
        document,
        "The old target was 1000 rooms.",
        [{"reason": "1000 is unsupported; at least 80% of rooms need complete fields."}],
        2.0,
    )
    assert [item["basis"] for item in document["definition_of_done"]] == [
        "proposed",
        "proposed",
    ]


def test_human_secondary_revision_downgrades_named_model_domain() -> None:
    document = _prd_response()
    publishers = document["authority_policy"]["trusted_publishers"]
    publishers.extend(
        [
            {
                "kind": "Community listing",
                "domains": ["community.example.test"],
                "rationale": "A public directory of reading rooms.",
            },
            {
                "kind": "University archive",
                "domains": ["archive.example.test"],
                "rationale": "Official university data.",
            },
        ]
    )
    _apply_human_authority_revisions(
        document,
        [{"reason": "Keep the community listing as a secondary cross-check."}],
    )
    assert publishers[1]["tier"] == "secondary"
    assert publishers[0]["tier"] == "primary"
    assert publishers[2].get("tier", "primary") == "primary"
    assert not authority_result(
        "https://community.example.test/rooms", policy=document["authority_policy"]
    )[0]
    assert authority_result(
        "https://archive.example.test/rooms", policy=document["authority_policy"]
    )[0]

    unknown = _prd_response()
    unknown_publisher = publishers[2].copy()
    unknown_publisher.pop("tier", None)
    unknown["authority_policy"]["trusted_publishers"] = [unknown_publisher]
    _apply_human_authority_revisions(
        unknown,
        [{"reason": "Keep the unnamed supplementary list as a secondary cross-check."}],
    )
    assert unknown["authority_policy"]["trusted_publishers"][0].get("tier", "primary") == "primary"
    assert not _authority_policy_check(
        unknown, [{"reason": "Keep the unnamed supplementary list as a secondary cross-check."}]
    ).passed


def test_shared_generic_word_does_not_demote_unrelated_publishers() -> None:
    document = _prd_response()
    publishers = document["authority_policy"]["trusted_publishers"]
    publishers.extend(
        [
            {
                "kind": "City archive",
                "jurisdiction": "Example City",
                "domains": ["archive.example.test"],
                "rationale": "Publishes community records.",
            },
            {
                "kind": "Public directory",
                "jurisdiction": "Example City",
                "domains": ["directory.example.test"],
                "rationale": "Publishes community reports.",
            },
        ]
    )
    revision = [{"reason": "Keep the community listing as a secondary cross-check."}]
    _apply_human_authority_revisions(document, revision)
    assert all(item.get("tier", "primary") == "primary" for item in publishers)
    assert any(
        "Human secondary cross-check lacks" in issue
        for issue in _authority_policy_check(document, revision).objections
    )


def test_overlapping_publisher_kinds_match_only_the_full_named_kind() -> None:
    document = _prd_response()
    publishers = document["authority_policy"]["trusted_publishers"]
    publishers.extend(
        [
            {
                "kind": "Registry",
                "tier": "primary",
                "jurisdiction": "Example City",
                "domains": ["registry.example.test"],
                "rationale": "An official registry.",
            },
            {
                "kind": "US Registry",
                "jurisdiction": "Other Place",
                "domains": ["us-registry.example.test"],
                "rationale": "A supplementary registry.",
            },
        ]
    )
    revision = [{"reason": "Keep US Registry as a secondary cross-check."}]
    _apply_human_authority_revisions(document, revision)
    assert publishers[1].get("tier", "primary") == "primary"
    assert publishers[2]["tier"] == "secondary"
    assert _authority_policy_check(document, revision).passed


def test_authority_policy_requires_local_primary_domains_and_unique_publishers() -> None:
    document = _prd_response()
    revision = [{"reason": "Keep the community listing as a secondary cross-check."}]
    document["authority_policy"]["trusted_publishers"].append(
        {
            "kind": "Community listing",
            "tier": "secondary",
            "jurisdiction": "Other City",
            "domains": ["community.example.test"],
            "rationale": "A community listing for comparison.",
        }
    )
    assert _authority_policy_check(document, revision).passed

    invalid = deepcopy(document)
    publishers = invalid["authority_policy"]["trusted_publishers"]
    publishers[0]["tier"] = "secondary"
    publishers[1]["domains"] = []
    publishers.append(deepcopy(publishers[0]))
    result = _authority_policy_check(invalid, revision)
    assert not result.passed
    assert any("primary publisher in the case jurisdiction" in issue for issue in result.objections)
    assert any("has no domain" in issue for issue in result.objections)
    assert any("Duplicate trusted publisher" in issue for issue in result.objections)
    assert any("Duplicate trusted domain" in issue for issue in result.objections)
    generic_claim = deepcopy(document)
    generic_claim["authority_policy"]["trusted_publishers"][1]["rationale"] = (
        "A primary public portal for city records."
    )
    assert not _authority_policy_check(generic_claim, revision).passed


def test_objection_subject_in_rationale_needs_semantic_correction() -> None:
    before = _prd_response()
    before["authority_policy"]["trusted_publishers"].append(
        {
            "kind": "Procurement portal",
            "tier": "secondary",
            "jurisdiction": "Example City",
            "domains": ["portal.example.test"],
            "rationale": "CivicPortal is the primary portal for public records.",
        }
    )
    objection = (
        "authority_policy tier contradiction: CivicPortal tagged secondary "
        "while its rationale says primary"
    )
    text_only = deepcopy(before)
    text_only["authority_policy"]["trusted_publishers"][-1]["rationale"] = (
        "CivicPortal is a primary public portal with a new description."
    )
    assert not _objection_subject_changed(before, text_only, objection)
    review_only = deepcopy(before)
    review_only["authority_policy"]["trusted_publishers"][-1]["tier"] = "review"
    assert not _objection_subject_changed(before, review_only, objection)
    corrected_rationale = deepcopy(before)
    corrected_rationale["authority_policy"]["trusted_publishers"][-1]["rationale"] = (
        "CivicPortal is a secondary cross-check for public records."
    )
    assert _objection_subject_changed(before, corrected_rationale, objection)
    assert _authority_policy_check(corrected_rationale, []).passed
    corrected = deepcopy(text_only)
    corrected["authority_policy"]["trusted_publishers"][-1]["tier"] = "primary"
    assert _objection_subject_changed(before, corrected, objection)


def test_live_prd_semantic_exhaustion_pauses_before_critic_or_write(tmp_path) -> None:
    (tmp_path / "brief.md").write_text(
        "Find 5 reading rooms, with 80% of required fields per room.", encoding="utf-8"
    )
    draft = _prd_response()
    draft["authority_policy"]["trusted_publishers"].append(
        {
            "kind": "Procurement portal",
            "tier": "secondary",
            "jurisdiction": "Example City",
            "domains": ["portal.example.test"],
            "rationale": "CivicPortal is the primary portal for public records.",
        }
    )
    critic_calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal critic_calls
        body = json.loads(request.content)
        if body["model"] == "minimax-m3":
            critic_calls += 1
            return _tool_response(
                {
                    "accepted": critic_calls == 2,
                    "reason": "authority_policy tier contradiction: CivicPortal tagged secondary "
                    "while its rationale says primary",
                }
            )
        fields = body["tools"][0]["function"]["parameters"]["required"]
        return _tool_response({name: draft[name] for name in fields})

    decision = VultrDecisionClient(
        api_key="test-only",
        model="glm-5.3-flash",
        prd_model="glm-5.3",
        critic_model="minimax-m3",
        client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    steps: list[dict] = []
    with pytest.raises(PrdDraftUnavailable) as raised:
        draft_prd(tmp_path, decision, budget_usd=1, emit=steps.append)
    assert raised.value.purpose == "phase1.prd"
    assert raised.value.attempts == 3
    assert "rationale claims primary" in raised.value.reason
    assert critic_calls == 0
    assert len(decision.call_log) == 9
    assert decision.call_log[-1]["status"] == "validation_failed"
    assert decision.call_log[-1]["reason"] == raised.value.reason
    assert not (tmp_path / "01-scope/prd.json").exists()
    assert [step["loop"]["role"] for step in steps] == ["gather"]


def test_schema_valid_cached_prd_with_invalid_authority_is_regenerated(tmp_path) -> None:
    (tmp_path / "brief.md").write_text(
        "Find 5 reading rooms, with 80% of required fields per room.", encoding="utf-8"
    )
    decision = RecordedDecisionClient({"phase1.prd": [_prd_response(), _prd_response()]})
    draft_prd(tmp_path, decision)
    path = tmp_path / "01-scope/prd.json"
    invalid = json.loads(path.read_text())
    invalid["authority_policy"]["trusted_publishers"] = []
    invalid["open_issues"] = ["Earlier loop did not resolve authority"]
    path.write_text(json.dumps(invalid), encoding="utf-8")
    regenerated = draft_prd(tmp_path, decision)
    assert len(decision.calls) == 2
    assert _authority_policy_check(regenerated, []).passed
    assert regenerated == json.loads(path.read_text())


def test_rejected_live_prd_persists_open_issues_and_human_cross_check(tmp_path) -> None:
    brief = "Find public reading rooms and report a complete profile for each one."
    (tmp_path / "brief.md").write_text(brief, encoding="utf-8")
    old = draft_prd(tmp_path, RecordedDecisionClient({"phase1.prd": [_prd_response()]}))
    scope = tmp_path / "01-scope"
    assert not require_approval(
        scope,
        phase=1,
        checkpoint="prd",
        artifact_paths=["01-scope/prd.json"],
        generated_by=old["generated_by"],
    )
    denial = {
        "approver": "Example Reviewer",
        "date": "2026-09-26",
        "checkpoint": "prd",
        "decision": "deny",
        "reason": (
            "At least 50 reading rooms should be linked; at least 80% of them need "
            "a complete profile. Keep the registry as a secondary cross-check."
        ),
    }
    (scope / "APPROVED").write_text(
        json.dumps(bind_approval(tmp_path, ["01-scope/prd.json"], denial)), encoding="utf-8"
    )
    revised = _prd_response(
        {
            "id": "profiles",
            "metric": "reading rooms with complete profiles",
            "operator": ">=",
            "target": 50,
            "min_ratio": 0.8,
            "basis": "human",
            "basis_quote": "At least 50 reading rooms should be linked",
            "rationale": "The reviewer specified the pilot size.",
            "feasibility": "Source count and elapsed time remain unverified.",
        }
    )
    revised["requirements"][0]["description"] = "Show evidence; basis=human, quote: reviewer"
    revised["authority_policy"]["trusted_publishers"] = [
        {
            "kind": "Example City archive",
            "tier": "primary",
            "jurisdiction": "Example City",
            "domains": ["city.example.test"],
            "rationale": "A local public publisher.",
        },
        {
            "kind": "Registry",
            "tier": "secondary",
            "domains": ["registry.example.test"],
            "rationale": "Use as a supplementary cross-check only.",
        },
    ]
    calls: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if body["model"] == "minimax-m3":
            return _tool_response({"accepted": False, "reason": "Verify the secondary cross-check"})
        fields = body["tools"][0]["function"]["parameters"]["required"]
        return _tool_response({name: revised[name] for name in fields})

    decision = VultrDecisionClient(
        api_key="test-only",
        model="glm-5.3-flash",
        prd_model="glm-5.3",
        critic_model="minimax-m3",
        client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    steps: list[dict] = []
    document = draft_prd(tmp_path, decision, budget_usd=2.0, emit=steps.append)
    assert document["definition_of_done"][0]["basis"] == "human"
    assert document["requirements"][0]["description"] == "Show evidence"
    assert document["open_issues"] == ["Verify the secondary cross-check"]
    assert (scope / "prd.json").exists()
    assert "Open issues" in (scope / "prd.md").read_text()
    assert not require_approval(
        scope,
        phase=1,
        checkpoint="prd",
        artifact_paths=["01-scope/prd.json"],
        generated_by=document["generated_by"],
    )
    pending = (scope / "APPROVAL_PENDING.md").read_text()
    assert "Verify the secondary cross-check" in pending
    assert "secondary" in pending
    assert any(
        item["tier"] == "secondary" for item in document["authority_policy"]["trusted_publishers"]
    )
    assert document["revisions"][0]["reason"] == denial["reason"]
    assert all(
        item["kind"] != "Human-requested cross-check"
        for item in document["authority_policy"]["trusted_publishers"]
    )
    trusted, reason = authority_result(
        "https://registry.example.test/rooms", policy=document["authority_policy"]
    )
    assert not trusted and "secondary" in reason
    conflicting = {
        "trusted_publishers": [
            {"kind": "Primary", "domains": ["registry.example.test"]},
            {"kind": "Cross-check", "tier": "secondary", "domains": ["registry.example.test"]},
        ]
    }
    assert not authority_result("https://registry.example.test/rooms", policy=conflicting)[0]
    critic_prompt = next(
        body["messages"][1]["content"] for body in calls if body["model"] == "minimax-m3"
    )
    assert brief in critic_prompt
    assert denial["reason"] in critic_prompt
    assert steps[-1]["loop"]["stop_reason"] == "max_iterations"
    assert all(
        "model" not in step["loop"]
        for step in steps
        if step["loop"]["role"] in {"gather", "check", "decide"}
    )


def test_critic_objection_causes_one_revised_draft(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Find public reading rooms.", encoding="utf-8")
    first = _prd_response(
        {
            "id": "volume",
            "metric": "rooms",
            "operator": ">=",
            "target": 1000,
            "basis": "proposed",
            "rationale": "A large draft target.",
            "feasibility": "Unknown under the available budget and run time.",
        }
    )
    second = _prd_response(
        {
            "id": "evidence",
            "metric": "rooms with evidence",
            "operator": ">=",
            "target": 1,
            "basis": "proposed",
            "rationale": "Small pilot target.",
            "feasibility": "One room may fit the available budget and run time.",
        }
    )
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) in {4, 8}:
            return _tool_response(
                {
                    "accepted": len(calls) == 8,
                    "reason": "Revision fixes the target"
                    if len(calls) == 8
                    else "Target is unfeasible",
                }
            )
        fields = body["tools"][0]["function"]["parameters"]["required"]
        source = first if len(calls) < 4 else second
        return _tool_response({name: source[name] for name in fields})

    decision = VultrDecisionClient(
        api_key="test-only",
        model="glm-5.3-flash",
        prd_model="glm-5.3",
        critic_model="minimax-m3",
        client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    result = draft_prd(tmp_path, decision)
    assert result["definition_of_done"][0]["id"] == "evidence"
    assert [body["model"] for body in calls] == [
        "glm-5.3",
        "glm-5.3",
        "glm-5.3",
        "minimax-m3",
        "glm-5.3",
        "glm-5.3",
        "glm-5.3",
        "minimax-m3",
    ]
    assert "Target is unfeasible" in calls[4]["messages"][1]["content"]


def test_denied_prd_is_archived_and_human_reason_regenerates(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Find public reading rooms.", encoding="utf-8")
    first = _prd_response(
        {
            "id": "volume",
            "metric": "rooms",
            "operator": ">=",
            "target": 1000,
            "basis": "proposed",
            "rationale": "A broad target.",
            "feasibility": "The source count is unknown for this budget and run time.",
        }
    )
    second = _prd_response(
        {
            "id": "evidence",
            "metric": "rooms with evidence",
            "operator": ">=",
            "target": 1,
            "basis": "human",
            "basis_quote": "at least 1 room with evidence",
        }
    )
    decision = RecordedDecisionClient({"phase1.prd": [first, second]})
    old = draft_prd(tmp_path, decision)
    scope = tmp_path / "01-scope"
    assert not require_approval(
        scope,
        phase=1,
        checkpoint="prd",
        artifact_paths=["01-scope/prd.json"],
        generated_by=old["generated_by"],
    )
    denial = {
        "approver": "Example Reviewer",
        "date": "2026-09-26",
        "checkpoint": "prd",
        "decision": "deny",
        "reason": "Use at least 1 room with evidence; 1000 is unsupported.",
    }
    (scope / "APPROVED").write_text(
        json.dumps(bind_approval(tmp_path, ["01-scope/prd.json"], denial)), encoding="utf-8"
    )
    new = draft_prd(tmp_path, decision)
    assert new["definition_of_done"][0]["id"] == "evidence"
    assert new["definition_of_done"][0]["basis"] == "human"
    assert new["revisions"] == [
        {"n": 1, **{k: denial[k] for k in ("decision", "reason", "approver", "date")}}
    ]
    assert json.loads((scope / "revisions/1/prd.json").read_text()) == old
    assert (scope / "revisions/1/APPROVED").exists()
    assert not (scope / "APPROVED").exists()
    assert "1000 is unsupported" in decision.calls[1][1]
    assert not require_approval(
        scope,
        phase=1,
        checkpoint="prd",
        artifact_paths=["01-scope/prd.json"],
        generated_by=new["generated_by"],
    )
    pending = (scope / "APPROVAL_PENDING.md").read_text()
    assert "1000 is unsupported" in pending
    assert "**human**" in pending
    assert draft_prd(tmp_path, decision) == new
    assert len(decision.calls) == 2


def test_failed_denial_regeneration_preserves_prd_pending_and_approval(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Find public reading rooms.", encoding="utf-8")
    old = draft_prd(tmp_path, RecordedDecisionClient({"phase1.prd": [_prd_response()]}))
    scope = tmp_path / "01-scope"
    assert not require_approval(
        scope,
        phase=1,
        checkpoint="prd",
        artifact_paths=["01-scope/prd.json"],
        generated_by=old["generated_by"],
    )
    denial = {
        "approver": "Example Reviewer",
        "date": "2026-09-26",
        "checkpoint": "prd",
        "decision": "deny",
        "reason": "Use a smaller grounded target.",
    }
    (scope / "APPROVED").write_text(
        json.dumps(bind_approval(tmp_path, ["01-scope/prd.json"], denial)), encoding="utf-8"
    )
    names = ("prd.json", "prd.md", "prd.input.sha256", "APPROVAL_PENDING.md", "APPROVED")
    before = {name: (scope / name).read_bytes() for name in names}

    class UnavailableDecision:
        backend = "vultr"
        model = "test-model"

        def __init__(self) -> None:
            self.call_log: list[dict] = []

        def complete_json(self, purpose: str, prompt: str, schema: dict) -> dict:
            raise TypeError("typed decision unavailable")

    with pytest.raises(TypeError, match="typed decision unavailable"):
        draft_prd(tmp_path, UnavailableDecision())
    assert {name: (scope / name).read_bytes() for name in names} == before
    assert not (scope / "revisions").exists()


def test_budget_change_keeps_the_prd_fingerprint_and_approval(tmp_path) -> None:
    """R20: the run budget informs feasibility text but must not change the artifact's identity."""
    (tmp_path / "brief.md").write_text(
        "Find 5 reading rooms, with 80% of required fields per room.", encoding="utf-8"
    )
    decision = RecordedDecisionClient({"phase1.prd": [_prd_response()]})
    first = draft_prd(tmp_path, decision, budget_usd=1.0)
    scope = tmp_path / "01-scope"
    fingerprint = (scope / "prd.input.sha256").read_text(encoding="utf-8").strip()

    # a different budget with the same brief and revisions: same fingerprint, no redraft
    second = draft_prd(tmp_path, decision, budget_usd=9.0)
    assert second == first
    assert len(decision.calls) == 1, "a budget change must not redraft the PRD"
    assert (scope / "prd.input.sha256").read_text(encoding="utf-8").strip() == fingerprint

    # an approval written for the first draft survives the budget change
    (scope / "APPROVED").write_text(
        json.dumps(
            bind_approval(
                tmp_path,
                ["01-scope/prd.json"],
                {"approver": "Test Reviewer", "date": "2026-09-26", "checkpoint": "prd"},
            )
        ),
        encoding="utf-8",
    )
    assert not require_approval(
        scope,
        phase=1,
        checkpoint="prd",
        artifact_paths=["01-scope/prd.json"],
        generated_by=first["generated_by"],
    )
    assert (scope / "APPROVED").exists()
    assert not (scope / f"APPROVED.stale.{fingerprint[:12]}").exists()
    assert (
        load_verified_approval(scope / "APPROVED", tmp_path, ["01-scope/prd.json"], "prd")[
            "checkpoint"
        ]
        == "prd"
    )


def test_legacy_budget_fingerprint_migrates_without_invalidating_approval(tmp_path) -> None:
    """A pending pre-R20 draft adopts the content key before budgets are changed."""
    brief = "Find 5 reading rooms, with 80% of required fields per room."
    (tmp_path / "brief.md").write_text(brief, encoding="utf-8")
    decision = RecordedDecisionClient({"phase1.prd": [_prd_response()]})
    first = draft_prd(tmp_path, decision, budget_usd=1.0)
    scope = tmp_path / "01-scope"
    prd_bytes = (scope / "prd.json").read_bytes()
    content_digest = (scope / "prd.input.sha256").read_text(encoding="utf-8").strip()
    legacy_digest = hashlib.sha256(
        json.dumps([brief, [], 1.0, "prd-steering-v4"], ensure_ascii=False).encode()
    ).hexdigest()
    (scope / "prd.input.sha256").write_text(legacy_digest + "\n", encoding="utf-8")
    (scope / "APPROVED").write_text(
        json.dumps(
            bind_approval(
                tmp_path,
                ["01-scope/prd.json"],
                {"approver": "Test Reviewer", "date": "2026-09-26", "checkpoint": "prd"},
            )
        ),
        encoding="utf-8",
    )

    assert draft_prd(tmp_path, decision, budget_usd=1.0) == first
    assert (scope / "prd.input.sha256").read_text(encoding="utf-8").strip() == content_digest
    assert draft_prd(tmp_path, decision, budget_usd=9.0) == first
    assert len(decision.calls) == 1
    assert (scope / "prd.json").read_bytes() == prd_bytes
    assert (
        load_verified_approval(scope / "APPROVED", tmp_path, ["01-scope/prd.json"], "prd")[
            "checkpoint"
        ]
        == "prd"
    )
