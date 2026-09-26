"""PRD targets identify their source and ungrounded numbers stay proposed."""

import json

import httpx

from ontofill.case.checkpoints import require_approval
from ontofill.inference import RecordedDecisionClient, VultrDecisionClient
from ontofill.phases.p1_scope.phase import (
    _apply_human_authority_revisions,
    _ground_criteria,
    draft_prd,
)
from ontofill.phases.p3_fanout.authority import authority_result


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
            "trusted_publishers": [],
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
    assert calls[0]["max_completion_tokens"] == 4096
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
    assert publishers[0]["tier"] == "secondary"
    assert publishers[1]["tier"] == "review"
    assert not authority_result(
        "https://community.example.test/rooms", policy=document["authority_policy"]
    )[0]
    assert not authority_result(
        "https://archive.example.test/rooms", policy=document["authority_policy"]
    )[0]

    unknown = _prd_response()
    unknown_publisher = publishers[1].copy()
    unknown_publisher.pop("tier")
    unknown["authority_policy"]["trusted_publishers"] = [unknown_publisher]
    _apply_human_authority_revisions(
        unknown,
        [{"reason": "Keep the unnamed supplementary list as a secondary cross-check."}],
    )
    assert unknown["authority_policy"]["trusted_publishers"][0]["tier"] == "review"
    assert not authority_result(
        "https://archive.example.test/rooms", policy=unknown["authority_policy"]
    )[0]


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
    (scope / "APPROVED").write_text(json.dumps(denial), encoding="utf-8")
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
            "kind": "Registry",
            "tier": "secondary",
            "domains": ["registry.example.test"],
            "rationale": "Use as a supplementary cross-check only.",
        }
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
    assert any(
        denial["reason"] in item["rationale"]
        for item in document["authority_policy"]["trusted_publishers"]
        if item["tier"] == "secondary"
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
    (scope / "APPROVED").write_text(json.dumps(denial), encoding="utf-8")
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
