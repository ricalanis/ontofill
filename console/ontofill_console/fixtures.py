"""Synthetic, domain-neutral cases for rehearsal and tests (two cases, `.example` hosts, invented values).

`generate(out)` writes:
  OUT/libraries/{case,lake}   a full case: gold export, a live run feed (loop steps with a critic objection, a human
                              revision and a gap-loop reopen, a quarantined page, vision verdicts, a repair attempt, an
                              action gate, a limit kill), sandbox jobs with all six proof checkpoints, and pending
                              approvals (PRD with revisions and DoD basis/quotes, factors, ontology, one action)
  OUT/parks/case              a second case with only a brief and a pending PRD, and no lake yet
and returns the registry spec for ONTOFILL_CONSOLE_CASES.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from . import fixture_libraries as libs

GEN = libs.GEN
VULTR = {"backend": "vultr", "model": "fixture-planner", "at": "2026-09-26T18:00:00+00:00"}


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class _Bronze:
    """Content-addressed captures in the lake layout: bronze/sha256/<hex> + <hex>.meta.json."""

    def __init__(self, lake: Path):
        self.lake = lake

    def put(self, data: bytes, content_type: str, url: str, captured_at: str, source_id: str, step_id: str) -> str:
        key = _sha(data)
        path = self.lake / "bronze" / "sha256" / key.split(":", 1)[1]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        meta = {
            "content_type": content_type,
            "url": url,
            "captured_at": captured_at,
            "source_id": source_id,
            "step_id": step_id,
        }
        path.with_name(path.name + ".meta.json").write_text(json.dumps(meta))
        return key

    def screenshot(self, host: str, label: str, value: str, **meta) -> str:
        esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;")  # noqa: E731
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">'
            '<rect width="640" height="360" fill="#f4f4f1"/><rect width="640" height="36" fill="#2b2b2b"/>'
            f'<text x="16" y="24" font-family="monospace" font-size="14" fill="#f4f4f1">https://{esc(host)}</text>'
            '<text x="16" y="84" font-family="monospace" font-size="13" fill="#9a3b10">SYNTHETIC FIXTURE</text>'
            f'<text x="16" y="140" font-family="sans-serif" font-size="15" fill="#555">{esc(label)}</text>'
            '<rect x="12" y="152" width="616" height="44" fill="#ffe28a"/>'
            f'<text x="20" y="181" font-family="sans-serif" font-size="20" fill="#111">{esc(value)}</text></svg>'
        )
        return self.put(svg.encode(), "image/svg+xml", f"https://{host}", **meta)

    def page(self, host: str, path: str, label: str, value: str, **meta) -> str:
        html = f"<!-- synthetic fixture --><html><body><h1>{host}{path}</h1><dl><dt>{label}</dt><dd>{value}</dd></dl>"
        return self.put(html.encode(), "text/html", f"https://{host}{path}", **meta)


PRD = {
    "version": "v2",
    "brief_path": "brief.md",
    "personas": [
        {"id": "resident", "description": "Resident looking for an open branch with free internet"},
        {"id": "librarian", "description": "Staff member checking that published hours are current"},
    ],
    "jobs_to_be_done": [
        {"id": "find_open", "persona_id": "resident", "description": "Find a branch open now"},
        {"id": "check_hours", "persona_id": "librarian", "description": "Compare hours with the source"},
    ],
    "requirements": [
        {"id": "r_hours", "job_id": "find_open", "description": "Opening hours per branch, with source"},
        {"id": "r_evidence", "job_id": "check_hours", "description": "Every value keeps its capture"},
    ],
    "constraints": ["Public sources only; no logins"],
    "non_goals": ["Rating library staff"],
    "definition_of_done": [
        {
            "id": "dod_branches",
            "metric": "branches_with_hours",
            "operator": ">=",
            "target": 20,
            "basis": "human",
            "basis_quote": "at least twenty branches with published hours",
        },
        {
            "id": "dod_evidence",
            "metric": "values_without_evidence",
            "operator": "=",
            "target": 0,
            "basis": "brief",
            "basis_quote": "Show the source for each value.",
        },
        {
            "id": "dod_sources",
            "metric": "distinct_source_classes",
            "operator": ">=",
            "target": 2,
            "basis": "proposed",
            "rationale": "Two publisher kinds let the hours be cross-checked",
            "feasibility": "About 0.40 USD of the 1.00 USD run budget",
        },
    ],
    "revisions": [
        {
            "n": 1,
            "decision": "deny",
            "reason": "at least twenty branches with published hours",
            "approver": "fixture-approver@example.org",
            "date": "2026-09-26",
        }
    ],
    "authority_policy": {
        "jurisdiction": "Example City (synthetic)",
        "unknown_source_action": "review",
        "trusted_publishers": [
            {
                "kind": "city government",
                "domains": ["registry.example"],
                "tier": "primary",
                "rationale": "Synthetic stand-in",
            }
        ],
    },
    "generated_by": VULTR,
}
PRD_DRAFT_1 = {k: v for k, v in PRD.items() if k != "revisions"} | {
    "version": "v1",
    "definition_of_done": [
        {"id": "dod_all", "metric": "branches_with_hours", "operator": ">=", "target": 500, "basis": "proposed"}
    ],
}

FACTORS = {
    "factors": [
        {
            "id": "operator_kind",
            "label": "Operator",
            "kind": "grounded",
            "description": "Who runs the branch",
            "evidence": [{"url": "https://registry.example/", "description": "Registry lists the operator"}],
        },
        {
            "id": "service_level",
            "label": "Service level",
            "kind": "conceptual",
            "description": "What a branch offers",
            "evidence": [],
        },
    ],
    "generated_by": VULTR,
}


def _node(id_, label, level, critic, parent=None, children=()):
    node = {"id": id_, "label": label, "level": level, "critic_label": critic}
    if parent:
        node["parent_id"] = parent
    if children:
        node["children"] = list(children)
    return node


TAXONOMIES = [
    {
        "factor_id": "operator_kind",
        "root_id": "operator",
        "root_label": "Operator",
        "soundness": 0.9,
        "coverage": 0.85,
        "children": [
            _node("municipal", "Municipal", 1, "Good-Exclusive", "operator"),
            _node("state", "State", 1, "Good-Exclusive", "operator"),
            _node("other", "Other", 1, "Bad", "operator"),
        ],
    }
]
SHAPES = "@prefix sh: <http://www.w3.org/ns/shacl#> .\n# Synthetic fixture shapes.\n"
ACTION = {
    "phase": 5,
    "checkpoint": "action",
    "requested_at": "2026-09-26T18:20:00+00:00",
    "reason": "The form is not on the TDD's list of read-only actions, so the engine asks first",
    "artifact_paths": ["04-local/website-example__profile/tdd.md"],
    "intended_action": "Submit the contact form on website.example",
    "risk_tier": "HIGH",
    "job_id": "job:fixture-action-0001",
    "generated_by": VULTR,
}


def _pending(meta: dict, title: str) -> str:
    return f"---\n{yaml.safe_dump(meta, sort_keys=False)}---\n# Approval pending: {title} (synthetic fixture)\n"


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def _approvals(case: Path) -> None:
    onto = json.loads((case / "02-ontology" / "ontology.json").read_text())
    onto |= {
        "factors": FACTORS["factors"],
        "taxonomies": TAXONOMIES,
        "shacl_path": "02-ontology/schema/shapes.ttl",
        "prd_path": "01-scope/prd.json",
        "generated_by": VULTR,
    }
    at = "2026-09-26T18:00:30+00:00"
    pend = lambda phase, cp, reason, paths: _pending(  # noqa: E731
        {
            "phase": phase,
            "checkpoint": cp,
            "requested_at": at,
            "reason": reason,
            "artifact_paths": paths,
            "generated_by": VULTR,
        },
        cp,
    )
    _write(
        case,
        {
            "01-scope/prd.json": json.dumps(PRD, indent=2),
            "01-scope/prd.md": "# Global PRD (synthetic fixture)\n",
            "01-scope/revisions/1/prd.json": json.dumps(PRD_DRAFT_1, indent=2),
            "01-scope/APPROVAL_PENDING.md": pend(
                1, "prd", "The PRD is ready for review", ["01-scope/prd.json", "01-scope/prd.md"]
            ),
            "02-ontology/factors/factors.json": json.dumps(FACTORS, indent=2),
            "02-ontology/factors/APPROVAL_PENDING.md": pend(
                2, "factors", "Factors proposed from the PRD", ["02-ontology/factors/factors.json"]
            ),
            "02-ontology/ontology.json": json.dumps(onto, indent=2),
            "02-ontology/schema/shapes.ttl": SHAPES,
            "02-ontology/APPROVAL_PENDING.md": pend(
                2,
                "ontology",
                "Taxonomies expanded with a critic pass",
                ["02-ontology/ontology.json", "02-ontology/schema/shapes.ttl"],
            ),
            "05-actions/req-0001/APPROVAL_PENDING.md": _pending(ACTION, "action"),
        },
    )


def _live_feed(lake: Path, bronze: _Bronze) -> None:
    run_id, case_id = libs.RUN_ID, libs.CASE_ID
    gold = lake / "gold" / case_id / run_id
    trace = [json.loads(line) for line in (gold / "trace.jsonl").read_text().splitlines()]
    ts0 = datetime(2026, 9, 26, 18, 30, tzinfo=UTC)
    extra: list[dict] = []

    def step(phase: int, mode: str, requested, evaluated, **kw) -> dict:
        s = {
            "step_id": f"step:{run_id}:x{len(extra) + 1:04d}",
            "run_id": run_id,
            "phase": phase,
            "source_id": kw.pop("source_id", None),
            "objective_id": None,
            "tdd_path": None,
            "mode": mode,
            "observed": kw.pop("observed", requested),
            "requested": requested,
            "executed": kw.pop("executed", "done"),
            "evaluated": evaluated,
            "parent_step_id": kw.pop("parent", None),
            "value_ids": [],
            "ts": (ts0 + timedelta(seconds=len(extra) * 4)).isoformat(),
            "generated_by": kw.pop("gen", VULTR),
            **kw,
        }
        extra.append(s)
        return s

    def loop(phase, it, role, model, verdict, objections=(), stop=None, evaluated="ok", executed="done", gen=VULTR):
        return step(
            phase if phase != "outer" else 5,
            "S1",
            f"{role} (iteration {it})",
            evaluated,
            executed=executed,
            event="loop",
            gen=gen,
            loop={
                "phase": phase,
                "iteration": it,
                "role": role,
                "model": model,
                "verdict": verdict,
                "objections": list(objections),
                "stop_reason": stop,
            },
            usage={"model": model, "est_usd": 0.004} if model else {},
        )

    loop(1, 1, "gather", None, "completed")
    loop(1, 1, "propose", "fixture-planner", "completed")
    loop(1, 1, "critique", "fixture-critic", "rejected", ["The branch count target has no stated basis"])
    loop(
        1,
        1,
        "revise",
        None,
        "completed",
        evaluated={"source": "human", "reason": "at least twenty branches"},
        gen={"backend": "human", "model": None},
    )
    loop(1, 1, "check", None, "failed")
    loop(1, 2, "propose", "fixture-planner", "completed")
    loop(1, 2, "critique", "fixture-critic", "accepted")
    loop(1, 2, "decide", None, "stop", stop="checks_passed")
    host = "website.example"
    shot = bronze.screenshot(
        host,
        "page with hidden instructions",
        "flagged",
        captured_at=ts0.isoformat(),
        source_id="website-example",
        step_id="quarantine",
    )
    q = step(
        5,
        "S1",
        "observe the branch page",
        {"status": "quarantined_continue"},
        source_id="website-example",
        event="quarantine",
        screenshot_key=shot,
        screen={
            "flagged": True,
            "jev_choice": "injection",
            "jev_confidence": 0.97,
            "safety_verdict": "unsafe",
            "reason": "instructions aimed at an agent reading the page",
            "by": "gateway",
        },
    )
    act = step(
        5,
        "S1",
        {"tool": "browser.act", "action": "open the hours page"},
        {"status": "ok"},
        source_id="website-example",
        parent=q["step_id"],
    )
    vshot = bronze.screenshot(
        host,
        "after: hours page",
        "achieved",
        captured_at=ts0.isoformat(),
        source_id="website-example",
        step_id=act["step_id"],
    )
    step(
        5,
        "S1",
        {"tool": "vision.verify"},
        {"status": "ok"},
        source_id="website-example",
        parent=act["step_id"],
        event="verify",
        screenshot_key=vshot,
        verify={
            "goal": "hours page visible",
            "verdict": "achieved",
            "confidence": 0.93,
            "backend": "vultr",
            "model": "fixture-vision",
            "screenshot_key": vshot,
        },
    )
    code = bronze.put(
        b"def extract(page):\n    return page['hours']\n",
        "text/x-python",
        f"https://{host}",
        captured_at=ts0.isoformat(),
        source_id="website-example",
        step_id="code",
    )
    diff = bronze.put(
        b"+def extract(page):\n",
        "text/x-diff",
        f"https://{host}",
        captured_at=ts0.isoformat(),
        source_id="website-example",
        step_id="code",
    )
    step(
        5,
        "D1",
        {"tool": "code.test", "attempt": 1},
        {"status": "failed"},
        source_id="website-example",
        event="repair",
        repair={
            "attempt": 1,
            "max_attempts": 3,
            "code_key": code,
            "diff_key": diff,
            "result": "fail",
            "stderr_excerpt": "KeyError: 'hours'",
            "test": {"pages": 6, "precision": 0.5, "coverage": 0.5},
        },
    )
    step(
        5,
        "S1",
        {"tool": "browser.act", "action": "submit the contact form"},
        {"status": "ok"},
        source_id="website-example",
        event="action_gate",
        gate={
            "action": "submit the contact form",
            "risk_tier": "HIGH",
            "decided_by": "code",
            "outcome": "pending_approval",
            "approval_path": "05-actions/req-0001/APPROVAL_PENDING.md",
        },
    )
    step(
        5,
        "S2",
        {"tool": "pdf.ocr_loop"},
        {"status": "killed", "reason": "timeout"},
        source_id="registry-example",
        event="limit_kill",
    )
    loop("outer", 1, "decide", None, "reopen", executed={"reopen": 3}, evaluated={"reason": "hours below target"})
    loop("outer", 2, "decide", None, "stop", stop="checks_passed", executed={"reopen": None})

    feed = lake / "runs" / case_id / run_id
    feed.mkdir(parents=True, exist_ok=True)
    (feed / "trace.live.jsonl").write_text("".join(json.dumps(s) + "\n" for s in trace + extra))
    metrics = json.loads((gold / "metrics.json").read_text())
    metrics["loops"] = [
        {"phase": 1, "iterations": 2, "stop_reason": "checks_passed", "usd": 0.012},
        {"phase": "outer", "iterations": 2, "stop_reason": "checks_passed", "usd": 0.0},
    ]
    status = {
        "run_id": run_id,
        "case_id": case_id,
        "state": "paused",
        "phase": 5,
        "checkpoint_pending": "action",
        "updated_at": "2026-09-26T18:40:00+00:00",
        "live_view_url": "https://live.example/view/fixture",
        "metrics": metrics,
        "generated_by": GEN,
    }
    (feed / "status.json").write_text(json.dumps(status, indent=2))
    job = {
        "job_id": "job:fixture-0001",
        "run_id": run_id,
        "step_id": extra[-1]["step_id"],
        "source_id": "website-example",
        "limits": {"memory_mb": 512, "cpus": 1, "pids": 128, "timeout_s": 60, "max_steps": 40},
        "checkpoints": {
            "host": {"ok": True, "sandbox_host": "sandbox.example", "runtime": "runsc", "virt": {"svm": True}},
            "task": {"ok": True, "requested": "open the hours page", "result": "hours captured"},
            "where": {"ok": True, "hostname": "cell-fixture", "uname": "Linux 4.4.0"},
            "isolation": {
                "probes": [
                    {"probe": "network_non_allowlisted", "result": "BLOCKED"},
                    {"probe": "write_outside_pod", "result": "BLOCKED"},
                ]
            },
            "teardown": {"ok": True, "detail": "destroyed"},
            "secrets": {
                "ok": True,
                "env_keys_found": 0,
                "files_with_keys": 0,
                "metadata_ip": "BLOCKED",
                "mesh": "BLOCKED",
            },
        },
    }
    (feed / "jobs.jsonl").write_text(json.dumps(job) + "\n")
    (lake / "runs" / case_id / "latest.json").write_text(json.dumps({"run_id": run_id}))


def generate(out: Path) -> str:
    """Write both fixture cases under OUT and return the ONTOFILL_CONSOLE_CASES spec for them."""
    out = Path(out)
    lib_root = out / "libraries"
    lake = libs.generate(lib_root)
    (lib_root / "case" / "brief.md").write_text(
        "# Public library access (synthetic fixture)\n\nWhich public libraries in Example City are open, and when? "
        "Show the source for each value.\n"
    )
    _approvals(lib_root / "case")
    _live_feed(lake, _Bronze(lake))
    parks = out / "parks" / "case"
    _write(
        parks,
        {
            "brief.md": "# Public parks (synthetic fixture)\n\nWhich parks have drinking water?\n",
            "01-scope/prd.json": json.dumps(PRD | {"revisions": []}, indent=2),
            "01-scope/APPROVAL_PENDING.md": _pending(
                {
                    "phase": 1,
                    "checkpoint": "prd",
                    "requested_at": "2026-09-26T18:00:30+00:00",
                    "reason": "The PRD is ready for review",
                    "artifact_paths": ["01-scope/prd.json"],
                    "generated_by": VULTR,
                },
                "prd",
            ),
        },
    )
    return f"libraries={lib_root / 'case'}:{lake},parks={parks}"
