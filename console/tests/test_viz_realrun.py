"""Real-run readiness: every console page over a recorded run in the engine's own shapes.

`build(root)` writes a case package and a file lake the way the engine does on a real run (records modelled on the
engine's emitters and recorded fixtures: `phase_loop`, `p3_fanout.search`, `sandbox.capture`, `p5_execute.phase`
(D0 downloads, column maps, complete-list membership derivation and its refusals), `p5_execute.controller` + the
browser controller (S1 sessions, Jev→Vultr verify verdicts, action gates allowed/pending/denied, quarantine, hard
stops, limit kills), `repair.runner` / `repair.pattern_a` (repairs, crystallization, escalation), `outer_gap`, and
`refiner.export` (gold entities with hashed ids, a missing value without a value id, a conflict)). Every live and gold
record is validated against the engine's JSON Schemas (`schemas/*.schema.json`) when they are next to the console.

The tests then render every console page for that run and check: 200, no template leaks, the key facts present,
hard stops / refusals / gates classified where an operator looks for them, and the JSON twins well-formed.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import spec_for
from fastapi.testclient import TestClient

from ontofill_console.web import create_app, settings_from_env

# --- the recorded run ------------------------------------------------------------------------------------------

CASE = "realrun"  # console registry id
LAKE_CASE = "city-libraries"  # the engine's case_id in the lake
RUN = "run-4e1d0c9a7b22"  # paused at an action checkpoint, gold exported (preview)
RUN_FAILED = "run-1a2b3c4d5e6f"  # an earlier run the engine stopped in phase 3 (no gold)
T0 = datetime(2026, 9, 26, 19, 0, tzinfo=UTC)
PLANNER, VISION, JEV = "planner-model", "vision-model", "jev-screen"
SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"

S_OPEN, S_REG, S_REG2, S_NOFILE = (
    "source-8c20f7d1e5b3",
    "source-51aa09e3c7d4",
    "source-e0c4b8a2f196",
    "source-0d7f3e9b1a58",
)
S_DIR, S_PORTAL, S_SITE, S_SITE2 = (
    "source-3b9e41c0d2aa",
    "source-a9d2c6e0f4b7",
    "source-7e5b1d3a9c02",
    "source-c4f0a8e2d913",
)
SOURCES = {  # source_id: (url, source_type, format, objective_id, target_fields, membership)
    S_OPEN: (
        "https://data.example.test/libraries",
        "open_data_portal",
        "csv",
        "objective-2f6c1a9e04b7",
        ["branch_code", "name", "opening_hours"],
        False,
    ),
    S_REG: (
        "https://registry.example.test/list",
        "complete_reference_list",
        "csv",
        "objective-9a3e5c7b1d20",
        ["in_registry"],
        True,
    ),
    S_REG2: (
        "https://archive.example.test/list",
        "complete_reference_list",
        "csv",
        "objective-6d1b8f4a2c93",
        ["in_registry"],
        True,
    ),
    S_NOFILE: (
        "https://notices.example.test/list",
        "complete_reference_list",
        "html",
        "objective-0b7e2d5f9a64",
        ["in_registry"],
        True,
    ),
    S_DIR: (
        "https://libraries.example.test/branches",
        "city_directory",
        "html",
        "objective-5c2aa7e47110",
        ["branch_code", "name", "free_internet"],
        False,
    ),
    S_PORTAL: (
        "https://portal.example.test/services",
        "library_website",
        "html",
        "objective-7c4d9e1b3f05",
        ["free_internet", "opening_hours"],
        False,
    ),
    S_SITE: (
        "https://branch.example.test/hours",
        "library_website",
        "html",
        "objective-3e8a0c6d2b71",
        ["branch_code", "free_internet", "opening_hours"],
        False,
    ),
    S_SITE2: (
        "https://annex.example.test/hours",
        "library_website",
        "html",
        "objective-a1f5b9d3e7c2",
        ["free_internet", "opening_hours"],
        False,
    ),
}
APPROVAL_PENDING_DIR = "05-actions/act-20260926T191500-3f9a1c"
APPROVAL_DENIED_DIR = "05-actions/act-20260926T191000-8b2e4d"


def _h(*parts, n: int = 24) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:n]


def gen(backend: str = "vultr", model: str = PLANNER) -> dict:
    return {"backend": backend, "model": model, "at": T0.isoformat()}


def png(seed: int, w: int = 96, h: int = 60) -> bytes:
    """A small valid PNG, a different colour per seed (the controller's screenshots are PNGs)."""
    color = bytes(((seed * 67) % 200 + 30, (seed * 131) % 200 + 30, (seed * 29) % 200 + 30))
    raw = b"".join(b"\x00" + color * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class Lake:
    def __init__(self, root: Path):
        self.root = root

    def put(self, data: bytes, content_type: str, url: str, source_id: str, step_id: str, at: str) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self.root / "bronze" / "sha256" / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        meta = {"content_type": content_type, "url": url, "captured_at": at, "source_id": source_id, "step_id": step_id}
        path.with_name(digest + ".meta.json").write_text(json.dumps(meta))
        return f"sha256:{digest}"


class Recorder:
    """Appends steps in the engine's trace-step shape (a missing `event` key, like most engine emitters, unless
    the emitter writes one; `event: null` where `sandbox.capture._trace` writes it)."""

    def __init__(self, lake: Lake, run_id: str, t0: datetime):
        self.lake, self.run_id, self.t0 = lake, run_id, t0
        self.steps: list[dict] = []
        self.jobs: list[dict] = []
        self.shots = 0
        self.loop_usd: dict = {}

    def ts(self) -> str:
        return (self.t0 + timedelta(seconds=3 * len(self.steps))).isoformat()

    def step(
        self,
        phase,
        mode,
        observed,
        requested,
        executed,
        evaluated,
        *,
        source=None,
        objective=None,
        tdd=None,
        parent=None,
        value_ids=(),
        g=None,
        step_id=None,
        **extra,
    ) -> dict:
        s = {
            "step_id": step_id or f"step:{_h(self.run_id, len(self.steps), n=32)}",
            "run_id": self.run_id,
            "phase": phase,
            "source_id": source,
            "objective_id": objective,
            "tdd_path": tdd,
            "mode": mode,
            "observed": observed,
            "requested": requested,
            "executed": executed,
            "evaluated": evaluated,
            "parent_step_id": parent,
            "value_ids": list(value_ids),
            "ts": self.ts(),
            "generated_by": g or gen(),
        }
        s.update(extra)
        self.steps.append(s)
        return s

    def shot(self, url: str, source_id: str, step_hint: str) -> str:
        self.shots += 1
        return self.lake.put(png(self.shots), "image/png", url, source_id, f"pending:{step_hint}", self.ts())

    # engine emitters ------------------------------------------------------------------------------------------
    def loop(
        self,
        phase,
        iteration,
        role,
        verdict,
        *,
        objections=(),
        stop=None,
        usd=0.0,
        reopen=False,
        reason=None,
        gaps=None,
    ):
        """phase_loop._emit: usage per stage, `evaluated.usd` the loop's running total, generated_by from usage."""
        total = self.loop_usd[phase] = round(self.loop_usd.get(phase, 0.0) + usd, 6)
        model = PLANNER if usd or phase == "outer" else "none"
        lp = {"phase": phase, "iteration": iteration, "role": role, "verdict": verdict, "objections": list(objections)}
        if model != "none":
            lp["model"] = model
        if role != "gather":
            lp["draft_sha256"] = _h("draft", phase, iteration, n=64)
        if stop:
            lp["stop_reason"] = stop
        usage = {
            "model": model,
            "backend": "vultr",
            "input_tokens": 1800 if usd else 0,
            "output_tokens": 420 if usd else 0,
            "est_usd": usd,
        }
        g = gen("vultr", model)
        if phase == "outer":
            return self.step(
                5,
                "D0",
                {"gaps": gaps or []},
                {"tool": "outer.gap_decision"},
                {"reopen": reopen},
                {"reason": reason, "usd": total},
                event="loop",
                loop=lp,
                usage=usage,
                g=g,
            )
        executed = (
            {"stop_reason": stop} if role == "decide" and stop else {"calls": 1 if usd else 0, "status": "completed"}
        )
        return self.step(
            phase,
            "D0",
            {"iteration": iteration},
            {"role": role},
            executed,
            {"usd": total, "verdict": verdict},
            event="loop",
            loop=lp,
            usage=usage,
            g=g,
            step_id=f"loop-{phase}-{iteration}-{role}-{_h(role, iteration, phase, n=12)}",
        )

    def decision(self, phase, purpose, usd, *, model=PLANNER, tokens=(2400, 350)):
        """workflow._publish_decision_calls: one D1 step per model call, with the call's usage (est_usd may be
        null for a model without a price)."""
        return self.step(
            phase,
            "D1",
            {"artifact": purpose},
            {"tool": "decision.complete_json"},
            {"artifact": purpose},
            {"status": "ok"},
            g=gen("vultr", model),
            usage={
                "model": model,
                "backend": "vultr",
                "input_tokens": tokens[0],
                "output_tokens": tokens[1],
                "est_usd": usd,
            },
        )

    def artifact(self, phase, tool, path, *, source=None, objective=None):
        tdd = path if path.startswith("04-local/") else None
        return self.step(
            phase,
            "D0",
            {"artifact": path},
            {"tool": tool},
            {"artifact": path},
            {"status": "ok"},
            source=source,
            objective=objective,
            tdd=tdd,
        )

    def capture(
        self,
        phase,
        source,
        objective,
        tdd,
        url,
        domains,
        *,
        parent=None,
        status="captured",
        reason=None,
        event="__none__",
        seed_label="page",
    ):
        """sandbox.capture: a dispatch row (html/a11y/screenshot keys) plus its proof rows."""
        shot = self.shot(url, source, seed_label)
        html_key = self.lake.put(
            f"<html><body><h1>{url}</h1></body></html>".encode(), "text/html", url, source, "pending:html", self.ts()
        )
        evaluated = {"bronze_objects": 3, "proof_checkpoint": "dispatch_result", "status": status}
        if reason:
            evaluated["reason"] = reason
        extra = {} if event == "__none__" else {"event": event}
        d = self.step(
            phase,
            "S1",
            {"status": 200, "url": url},
            {"allowed_domains": domains, "capture": ["html", "a11y", "screenshot"], "url": url},
            {"html_key": html_key, "network_request": True, "screenshot_key": shot},
            evaluated,
            source=source,
            objective=objective,
            tdd=tdd,
            parent=parent,
            screenshot_key=shot,
            **extra,
        )
        for cp, executed, ev in (
            (
                "host_check",
                {"runtime": "runsc", "runtime_available": True, "docker_host": "sandbox-host"},
                {"proof_checkpoint": "host_check", "status": "verified"},
            ),
            (
                "pod_identity",
                {
                    "hostname": f"cell-{_h(d['step_id'], n=8)}",
                    "uname": {"system": "Linux", "release": "4.4.0", "machine": "x86_64"},
                },
                {"proof_checkpoint": "pod_identity", "status": "verified"},
            ),
            (
                "isolation_probe",
                {"blocked": True, "network": {"blocked": True, "status": 403, "host": "proof-denied.invalid"}},
                {"proof_checkpoint": "isolation_probe", "status": "blocked"},
            ),
            (
                "teardown",
                {"pod_gone": True, "network_removed": True, "proxy_gone": True, "verified": True},
                {"proof_checkpoint": "teardown", "status": "verified"},
            ),
        ):
            self.step(
                phase,
                "S1",
                {"checkpoint": cp},
                {"proof_checkpoint": cp},
                executed,
                ev,
                source=source,
                objective=objective,
                tdd=tdd,
                parent=d["step_id"],
            )
        return d, shot, html_key

    def job(self, step, source, *, runtime="runsc", failure=None, task_ok=True, job_id=None, value_ids=()):
        ok = failure is None
        cps = {
            "host": {
                "ok": True,
                "sandbox_host": "sandbox-host",
                "runtime": runtime,
                "virt": {"cpu_virtualization_flags": ["vmx"], "dev_kvm_present": True},
            },
            "task": {
                "ok": task_ok and ok,
                "requested": {"url": SOURCES.get(source, ("https://x.example.test",))[0]},
                "result": {"status": 200} if ok else {"reason": failure},
                "value_ids": list(value_ids),
            },
            "where": {
                "ok": True,
                "hostname": f"cell-{_h(step['step_id'], n=8)}",
                "uname": {"system": "Linux", "release": "4.4.0", "machine": "x86_64"},
            },
            "isolation": {
                "probes": [
                    {"probe": "network_non_allowlisted", "result": "BLOCKED"},
                    {"probe": "write_outside_pod", "result": "BLOCKED"},
                ]
            },
            "teardown": {
                "ok": True,
                "detail": {"pod_gone": True, "network_removed": True, "proxy_gone": True, "verified": True},
            },
            "secrets": {
                "ok": True,
                "env_keys_found": 0,
                "files_with_keys": 0,
                "metadata_ip": "BLOCKED",
                "mesh": "BLOCKED",
            },
        }
        if failure == "timeout":  # a capture killed before its probes ran (sandbox.jobs shape)
            cps["where"] = {"ok": False, "not_run": True}
            cps["isolation"] = {"probes": [], "not_run": True}
            cps["secrets"] = {"ok": False, "not_run": True}
        rec = {
            "job_id": job_id or f"job:{_h(step['step_id'], n=32)}",
            "run_id": self.run_id,
            "step_id": step["step_id"],
            "source_id": source,
            "started_at": step["ts"],
            "ended_at": step["ts"],
            "generated_by": gen(),
            "checkpoints": cps,
            "limits": {"cpus": 1.0, "max_steps": 30, "memory_mb": 1024, "pids": 256, "timeout_s": 900},
            "usage": {"peak_memory_mb": 212, "steps": 9, "wall_s": 14 if ok else 900},
        }
        if failure:
            rec["failure_reason"] = failure
        self.jobs.append(rec)
        return rec


def _tdd_path(sid: str) -> str:
    return f"04-local/{sid}__{SOURCES[sid][3]}/tdd.json"


def _entity_id(code: str) -> str:
    return f"library:{_h(code.casefold())}"


def _value_id(eid: str, prop: str, value, source: str) -> str:
    return f"val:{_h(eid, prop, value, source)}"


def _evidence(url, bronze_key, selector, shot, at, source, fmt, step_id=None) -> dict:
    ev = {
        "url": url,
        "bronze_key": bronze_key,
        "selector": selector,
        "screenshot_key": shot,
        "captured_at": at,
        "source_id": source,
        "source_type": SOURCES[source][1],
        "format": fmt,
    }
    if step_id:
        ev["step_id"] = step_id
    return ev


class Values:
    """Gold values as `refiner.export` writes them: one field per property, value ids hashed, evidence per value."""

    def __init__(self):
        self.by_entity: dict[str, dict] = {}
        self.codes: dict[str, str] = {}

    def add(self, code, prop, value, source, evidence) -> str:
        """One observation. The first becomes the gold field; an agreeing one adds its evidence, a disagreeing one
        turns the field into a kept conflict. Returns this observation's own value id (what its step cites)."""
        eid = _entity_id(code)
        self.codes[eid] = code
        vid = _value_id(eid, prop, value, source)
        props = self.by_entity.setdefault(eid, {})
        field = props.get(prop)
        if field is None:
            props[prop] = {
                "value_id": vid,
                "value": value,
                "confidence": 1.0,
                "status": "gold",
                "evidence": [evidence],
                "generated_by": gen(),
            }
        else:
            field["evidence"].append(evidence)
            if field["value"] != value:
                field.update(status="conflict", confidence=0.5)
        return vid

    def entities(self, all_props: list[str]) -> list[dict]:
        out = []
        for eid, props in sorted(self.by_entity.items()):
            fields = dict(props)
            for p in all_props:  # a property the run looked for and did not find: missing, no value id
                fields.setdefault(
                    p, {"value": None, "confidence": 0.0, "status": "missing", "evidence": [], "generated_by": gen()}
                )
            out.append(
                {
                    "id": eid,
                    "class": "library",
                    "classified_as": [],
                    "properties": fields,
                    "links": [],
                    "flags": [],
                    "generated_by": gen(),
                }
            )
        return out


ONTOLOGY = {
    "version": "3",
    "prd_path": "01-scope/prd.json",
    "shacl_path": "02-ontology/shapes.ttl",
    "dod_queries_path": "02-ontology/dod-queries.json",
    "generated_by": gen(),
    "primary_class": "library",
    "classes": [
        {
            "id": "library",
            "label": "Library",
            "label_plural": "Libraries",
            "description": "Public library branch",
            "identifier_property": "branch_code",
            "title_property": "name",
            "aligned_to": "https://schema.org/Library",
        }
    ],
    "properties": [
        {
            "id": "name",
            "label": "Name",
            "domain": "library",
            "datatype": "string",
            "dod": True,
            "order": 0,
            "description": "Displayed branch name",
            "aligned_to": "https://schema.org/name",
        },
        {
            "id": "branch_code",
            "label": "Branch code",
            "domain": "library",
            "datatype": "string",
            "dod": True,
            "order": 1,
            "description": "Code the city assigns to the branch",
            "aligned_to": None,
        },
        {
            "id": "free_internet",
            "label": "Free internet",
            "domain": "library",
            "datatype": "boolean",
            "dod": True,
            "order": 2,
            "description": "Free internet is offered",
            "aligned_to": None,
        },
        {
            "id": "opening_hours",
            "label": "Opening hours",
            "domain": "library",
            "datatype": "string",
            "dod": True,
            "order": 3,
            "description": "Displayed public hours",
            "aligned_to": "https://schema.org/openingHours",
        },
        {
            "id": "in_registry",
            "label": "In the city registry",
            "domain": "library",
            "datatype": "boolean",
            "dod": False,
            "order": 4,
            "description": "Listed in the complete city registry of branches",
            "aligned_to": None,
        },
    ],
    "relations": [],
    "rules": [],
    "revisions": [],
    "factors": [
        {
            "id": "access",
            "label": "Public access",
            "kind": "conceptual",
            "description": "Availability and opening times",
            "evidence": [],
        }
    ],
    "taxonomies": [
        {
            "factor_id": "access",
            "root_label": "Public access",
            "coverage": 1.0,
            "soundness": 1.0,
            "children": [
                {"id": "internet_access", "label": "Internet access", "level": 1, "critic_label": "Good-Exclusive"}
            ],
        }
    ],
    "source_classes": [
        {"id": "open_data_portal", "label": "Open data portal"},
        {"id": "complete_reference_list", "label": "Complete reference list"},
        {"id": "city_directory", "label": "City library directory"},
        {"id": "library_website", "label": "Library website"},
    ],
}
DOD_QUERIES = {
    "queries": [
        {
            "criterion_id": "libraries_found",
            "aggregate": "count_entities",
            "class_id": "library",
            "operator": ">=",
            "target": 5,
        },
        {
            "criterion_id": "with_hours_and_internet",
            "aggregate": "count_entities_with_properties",
            "class_id": "library",
            "properties": ["name", "opening_hours", "free_internet"],
            "operator": ">=",
            "target": 20,
        },
        {"criterion_id": "source_classes", "aggregate": "count_distinct_source_classes", "operator": ">=", "target": 3},
        {
            "criterion_id": "evidence_integrity",
            "aggregate": "count_values_without_evidence",
            "operator": "=",
            "target": 0,
        },
    ],
    "generated_by": gen(),
    "prd_path": "01-scope/prd.json",
    "ontology_version": "3",
}
PRD = {
    "version": "2",
    "brief_path": "brief.md",
    "personas": [{"id": "resident", "description": "Local resident"}],
    "jobs_to_be_done": [
        {
            "id": "find_access",
            "persona_id": "resident",
            "description": "Find free internet and hours at public libraries",
        }
    ],
    "requirements": [
        {
            "id": "evidence",
            "job_id": "find_access",
            "description": "Show opening hours and internet availability with public evidence",
        }
    ],
    "constraints": ["Public read-only sources"],
    "non_goals": ["Private patron records"],
    "definition_of_done": [
        {
            "id": "with_hours_and_internet",
            "metric": "libraries_with_access_and_hours",
            "operator": ">=",
            "target": 20,
            "basis": "brief",
            "basis_quote": "every branch in Example City",
        },
        {
            "id": "evidence_integrity",
            "metric": "values_without_evidence",
            "operator": "=",
            "target": 0,
            "basis": "brief",
            "basis_quote": "show the source for each value",
        },
    ],
    "authority_policy": {
        "jurisdiction": "Example City",
        "unknown_source_action": "review",
        "trusted_publishers": [
            {
                "kind": "city library office",
                "tier": "primary",
                "jurisdiction": "Example City",
                "domains": ["libraries.example.test", "registry.example.test"],
                "rationale": "Official directory for this synthetic city",
            }
        ],
    },
    "revisions": [],
    "generated_by": gen(),
}


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(root: Path, rel: str, data) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data, indent=2))
    return path


def _approve(case: Path, rel_dir: str, checkpoint: str, paths: list[str], **extra) -> None:
    marker = {
        "approver": "reviewer@example.org",
        "date": "2026-09-26",
        "checkpoint": checkpoint,
        "identity_source": "sso",
        "artifact_sha256": {p: _sha_file(case / p) for p in paths},
        **extra,
    }
    _write(case, f"{rel_dir}/APPROVED", marker)


def _pending(case: Path, rel_dir: str, meta: dict, title: str) -> None:
    import yaml

    _write(
        case,
        f"{rel_dir}/APPROVAL_PENDING.md",
        f"---\n{yaml.safe_dump(meta, sort_keys=False)}---\n# Approval pending: {title}\n\n"
        f"The browser agent wants to **{meta.get('intended_action', title)}**.\n",
    )


def build_case(case: Path) -> None:
    """The case package as the engine leaves it: phases 1-4 approved, objectives and TDDs per source, a promoted
    macro, a column map, one action waiting for a person and one an approver denied."""
    _write(
        case,
        "brief.md",
        "# Public library access (recorded run)\n\nWhich public libraries in Example City offer "
        "free internet access, and when are they open? Show the source for each value.\n",
    )
    _write(case, "01-scope/prd.json", PRD)
    _write(case, "01-scope/prd.md", "# Global PRD\n")
    _approve(case, "01-scope", "prd", ["01-scope/prd.json"])
    _write(case, "02-ontology/factors/factors.json", {"factors": ONTOLOGY["factors"], "generated_by": gen()})
    _approve(case, "02-ontology/factors", "factors", ["02-ontology/factors/factors.json"])
    _write(case, "02-ontology/ontology.json", ONTOLOGY)
    _write(case, "02-ontology/dod-queries.json", DOD_QUERIES)
    _write(case, "02-ontology/shapes.ttl", "@prefix sh: <http://www.w3.org/ns/shacl#> .\n")
    _approve(case, "02-ontology", "ontology", ["02-ontology/ontology.json"])
    objectives = []
    for i, (sid, (url, stype, _fmt, oid, fields, membership)) in enumerate(SOURCES.items()):
        fp = _h("fp", sid, n=64)
        discovered = {"provider": "search-a", "at": T0.isoformat()}
        objectives.append(
            {
                "id": oid,
                "source_id": sid,
                "source_url": url,
                "source_type": stype,
                "source_fingerprint": fp,
                "discovery_provider": "search-a",
                "discovered_by": discovered,
                "target_fields": fields,
                "priority": i + 1,
                "expected_contribution": 0.25,
                "authority_tier": "primary" if "registry" in url or "libraries" in url else "review",
            }
        )
        _write(
            case,
            f"03-fanout/sources/{sid}/candidate.json",
            {
                "source_id": sid,
                "url": url,
                "title": stype.replace("_", " ").capitalize(),
                "snippet": "Public page",
                "provider": "search-a",
                "discovered_by": discovered,
                "capture_key": None,
                "source_type": stype,
                "authority": "auto" if "registry" in url or "libraries" in url else "review",
                "authority_reason": "approved publisher kind: city library office",
                "fingerprint": fp,
                "generated_by": gen(),
            },
        )
        base = f"04-local/{sid}__{oid}"
        tdd = {
            "source_id": sid,
            "objective_id": oid,
            "local_prd_path": f"{base}/local-prd.json",
            "ontology_version": "3",
            "source_url": url,
            "allowed_domains": [url.split("/")[2]],
            "target_fields": fields,
            "target_volume": 50,
            "extraction_method": "dom" if _fmt == "html" else "download",
            "validation_rules": ["Only literal observed cells"],
            "rate_limit_per_minute": 6,
            "budget_usd": 0.5,
            "steps": [
                {
                    "id": "read",
                    "description": "Read the public list",
                    "risk_tier": "SAFE",
                    "starting_mode": "S1" if sid in (S_DIR, S_PORTAL) else "D0",
                    "allowed_modes": ["D0", "D1", "S1"],
                    "observation_channel": "text_structure",
                    "termination_predicate": "All rows read",
                }
            ],
            "allowed_tools": ["page.snapshot", "page.query", "emit.observation"],
            "generated_by": gen(),
        }
        if membership:
            tdd["membership"] = {
                "property_id": "in_registry",
                "identifier_property_id": "branch_code",
                "complete": True,
            }
        _write(case, f"{base}/tdd.json", tdd)
        _write(case, f"{base}/tdd.md", f"# TDD {sid}\n\nRead {url} (read-only).\n")
        _write(
            case,
            f"{base}/local-prd.json",
            {
                "source_id": sid,
                "objective_id": oid,
                "global_prd_path": "01-scope/prd.json",
                "global_requirement_ids": ["evidence"],
                "target_fields": fields,
                "local_definition_of_done": [
                    {"metric": "libraries_with_access_and_hours", "operator": ">=", "target": 1}
                ],
                "ontology_recommendations": [],
                "generated_by": gen(),
            },
        )
    doc = {"ontology_version": "3", "prd_path": "01-scope/prd.json", "generated_by": gen(), "objectives": objectives}
    _write(case, "03-fanout/objectives.json", doc)
    import yaml

    _write(case, "03-fanout/objectives.yaml", yaml.safe_dump(doc, sort_keys=False))
    # discovery_loop: leads are never evidence; captured, critic-checked candidates become objectives
    query = "public libraries free internet opening hours"
    props = ["free_internet", "opening_hours", "in_registry"]
    leads = [
        {
            "url": SOURCES[sid][0],
            "title": SOURCES[sid][1].replace("_", " "),
            "snippet": "Public page",
            "discovered_by": "search-a",
            "providers": ["search-a"],
            "query": query,
            "property_ids": props[:2],
            "score": 0.8,
            "iteration": 1,
        }
        for sid in SOURCES
    ]
    leads.append(
        {
            "url": "https://blog.example.test/libraries",
            "title": "Library blog",
            "snippet": "Opinion",
            "discovered_by": "search-a",
            "providers": ["search-a"],
            "query": query,
            "property_ids": ["opening_hours"],
            "score": 0.3,
            "iteration": 1,
        }
    )
    candidates = [
        {
            "url": SOURCES[sid][0],
            "title": SOURCES[sid][1].replace("_", " "),
            "snippet": "Public page",
            "discovered_by": "search-a",
            "providers": ["search-a"],
            "query": query,
            "property_ids": SOURCES[sid][4],
            "iteration": 1,
            "status": "confirmed",
            "covers": SOURCES[sid][4],
            "critic": {},
            "source_id": sid,
            "source_type": SOURCES[sid][1],
            "authority": "auto" if "registry" in SOURCES[sid][0] or "libraries" in SOURCES[sid][0] else "review",
            "capture_key": None,
        }
        for sid in SOURCES
    ]
    candidates.append(
        {
            "url": "https://blog.example.test/libraries",
            "title": "Library blog",
            "snippet": "Opinion",
            "discovered_by": "search-a",
            "providers": ["search-a"],
            "query": query,
            "property_ids": ["opening_hours"],
            "iteration": 1,
            "status": "rejected",
            "covers": [],
            "critic": {"opening_hours": "the page quotes hours second-hand"},
            "capture_key": None,
        }
    )
    _write(
        case,
        "03-fanout/surface-map/leads.json",
        {
            "note": "Leads are never evidence; only captured, authority-checked candidates become objectives.",
            "leads": leads,
            "candidates": candidates,
            "generated_by": gen(),
        },
    )
    attempts = [
        {
            "provider": "search-a",
            "query": query,
            "outcome": "ok",
            "capture_key": None,
            "result_count": 9,
            "step_ids": [],
        },
        {
            "provider": "search-b",
            "query": query,
            "outcome": "blocked",
            "capture_key": None,
            "result_count": 0,
            "step_ids": [],
        },
        {
            "provider": "search-c",
            "query": query,
            "outcome": "blocked",
            "capture_key": None,
            "result_count": 0,
            "step_ids": [],
        },
    ]
    _write(
        case,
        "03-fanout/surface-map/discovery.json",
        {
            "request_fingerprint": _h("req", n=64),
            "generated_by": gen(),
            "rounds": [
                {
                    "request_fingerprint": _h("req", n=64),
                    "mode": "loop",
                    "gaps": props,
                    "required": {p: 1 for p in props},
                    "coverage": {
                        "free_internet": {"required": 1, "hosts": ["libraries.example.test"]},
                        "opening_hours": {"required": 1, "hosts": ["data.example.test"]},
                        "in_registry": {"required": 1, "hosts": ["registry.example.test"]},
                    },
                    "iterations": 2,
                    "stop_reason": "checks_passed",
                    "usd": 0.0041,
                    "objections": ["opening_hours: 0/1 confirmed sources passing the authority policy"],
                    "queries": [query],
                    "provider_yield": {
                        "search-a": {"leads": 9, "captured": 9, "confirmed": 8, "sources": 3, "calls": 1},
                        "search-b": {"leads": 0, "captured": 0, "confirmed": 0, "sources": 0, "calls": 1},
                        "search-c": {"leads": 0, "captured": 0, "confirmed": 0, "sources": 0, "calls": 1},
                    },
                    "attempts": attempts,
                    "selected_source_ids": list(SOURCES),
                    "candidate_count": len(candidates),
                    "lead_count": len(leads),
                    "generated_by": gen(),
                }
            ],
        },
    )
    _write(case, f"05-macros/{S_SITE}/v1/extractor.py", "def extract(page):\n    return page.select('dl')\n")
    _write(
        case,
        f"05-macros/{S_SITE}/v1/manifest.json",
        {
            "format": "html",
            "version": 1,
            "source_id": S_SITE,
            "run_id": RUN,
            "tdd_path": _tdd_path(S_SITE),
            "ontology_fingerprint": _h("onto", n=64),
            "target_fields": SOURCES[S_SITE][4],
            "code_key": "sha256:" + _h("code", n=64),
            "code_sha256": _h("code", n=64),
            "test": {"pages": 3, "precision": 1.0, "coverage": 1.0},
            "attempts": 2,
            "generated_by": gen(),
            "promoted_at": (T0 + timedelta(minutes=9)).isoformat(),
        },
    )
    _write(
        case,
        f"05-execute/macros/{S_OPEN}-{_h('sig', n=16)}.json",
        {
            "class_id": "library",
            "columns": [
                {"header": "code", "property_id": "branch_code"},
                {"header": "branch", "property_id": "name"},
                {"header": "hours", "property_id": "opening_hours"},
            ],
            "generated_by": gen(),
        },
    )
    action = {
        "phase": 5,
        "checkpoint": "action",
        "requested_at": (T0 + timedelta(minutes=15)).isoformat(),
        "reason": "Jev rated the action HIGH (code floor: form submission)",
        "artifact_paths": [f"{APPROVAL_PENDING_DIR}/APPROVAL_PENDING.md"],
        "intended_action": "click 'Request a library card'",
        "risk_tier": "HIGH",
        "job_id": f"job:{RUN}:{S_PORTAL}",
        "screenshot_key": "sha256:" + "0" * 64,
        "generated_by": gen(),
    }
    _pending(case, APPROVAL_PENDING_DIR, action, "action")
    denied = dict(
        action,
        requested_at=(T0 + timedelta(minutes=10)).isoformat(),
        intended_action="type into 'Email for updates'",
        artifact_paths=[f"{APPROVAL_DENIED_DIR}/APPROVAL_PENDING.md"],
    )
    _pending(case, APPROVAL_DENIED_DIR, denied, "action")
    _approve(
        case,
        APPROVAL_DENIED_DIR,
        "action",
        [f"{APPROVAL_DENIED_DIR}/APPROVAL_PENDING.md"],
        decision="deny",
        reason="Not part of a read-only capture",
        run_id=RUN,
    )


def build_main_run(lake: Lake, case: Path) -> tuple[list[dict], list[dict], Values]:
    r = Recorder(lake, RUN, T0)
    vals = Values()

    # P1: the scope loop (a critic objection, a failed check, then checks passed) --------------------------------
    r.loop(1, 1, "gather", "completed")
    r.loop(1, 1, "propose", "completed", usd=0.0031)
    r.loop(1, 1, "critique", "rejected", objections=["The branch count target has no stated basis"], usd=0.0012)
    r.loop(1, 1, "revise", "completed", usd=0.0027)
    r.loop(1, 1, "check", "failed")
    r.loop(1, 1, "decide", "continue")
    r.loop(1, 2, "propose", "completed", usd=0.0029)
    r.loop(1, 2, "critique", "accepted", usd=0.0011)
    r.loop(1, 2, "check", "passed")
    r.loop(1, 2, "decide", "stop", stop="checks_passed")
    r.artifact(1, "phase1.prd", "01-scope/prd.json")
    r.decision(2, "phase2.factors", 0.0022)
    r.artifact(2, "phase2.factors", "02-ontology/factors/factors.json")
    r.decision(2, "phase2.ontology", 0.0048, tokens=(6100, 1400))
    r.artifact(2, "phase2.ontology", "02-ontology/ontology.json")

    # P3: discovery through sandboxed search captures; one provider answers with a captcha, one with a bot wall ---
    policy = "03-fanout/search-policy.json"
    ok, _, _ = r.capture(
        3, "search-provider", None, policy, "https://search-a.example.test/?q=libraries", ["search-a.example.test"]
    )
    r.job(ok, "search-provider", runtime="runc")
    r.capture(
        3,
        "search-provider",
        None,
        policy,
        "https://search-b.example.test/?q=libraries",
        ["search-b.example.test"],
        reason="blocked: captcha",
        event="hard_stop",
    )
    r.capture(
        3,
        "search-provider",
        None,
        policy,
        "https://search-c.example.test/?q=libraries",
        ["search-c.example.test"],
        reason="blocked: bot_wall",
        event="hard_stop",
    )
    # the phase 3 discovery loop: a critic objection on coverage, then checks passed
    r.loop(3, 1, "gather", "completed")
    r.loop(3, 1, "propose", "completed", usd=0.0014)
    r.loop(3, 1, "critique", "rejected", objections=["opening_hours: the blog quotes hours second-hand"], usd=0.0009)
    r.loop(3, 1, "revise", "completed")
    r.loop(3, 1, "check", "failed", objections=["opening_hours: 0/1 confirmed sources passing the authority policy"])
    r.loop(3, 1, "decide", "continue")
    r.loop(3, 2, "propose", "completed", usd=0.0011)
    r.loop(3, 2, "critique", "accepted", usd=0.0007)
    r.loop(3, 2, "check", "passed")
    r.loop(3, 2, "decide", "stop", stop="checks_passed")
    r.decision(3, "phase3.rank_sources", None, model="unpriced-model")
    r.artifact(3, "source.discover", "03-fanout/objectives.json")
    for sid in SOURCES:
        r.decision(4, "phase4.local_scope", 0.0009)
        r.artifact(4, "phase4.local_scope", _tdd_path(sid), source=sid, objective=SOURCES[sid][3])

    # P5 · D0 download from an open data portal, a column map, literal cells ----------------------------------
    def download(sid: str, payload: bytes, *, status="captured"):
        url, _stype, fmt, oid, _f, _m = SOURCES[sid]
        tdd = _tdd_path(sid)
        page, shot, _ = r.capture(5, sid, oid, tdd, url, [url.split("/")[2]])
        r.decision(5, "phase5.select_download", 0.0006)
        data_url = f"https://{url.split('/')[2]}/data.{fmt}"
        key = lake.put(payload, "text/csv", data_url, sid, page["step_id"], r.ts())
        fetched = r.step(
            5,
            "D0",
            {"status": 200, "url": data_url},
            {"url": data_url, "allowed_domains": [url.split("/")[2]]},
            {"bronze_key": key, "network_request": True},
            {"status": status, "proof_checkpoint": "dispatch_result", "bronze_objects": 1},
            source=sid,
            objective=oid,
            tdd=tdd,
            parent=page["step_id"],
            event=None,
        )
        return page, shot, fetched, key, data_url

    page, shot, fetched, key, data_url = download(
        S_OPEN,
        b"code,branch,hours\nL-101,North Branch,Mon-Fri 09:00-17:00\nL-102,River Branch,Tue-Fri 10:00-18:00\n"
        b"L-103,Hill Branch,Mon-Thu 08:00-20:00\nL-104,Harbor Branch,\n",
    )
    r.job(page, S_OPEN)
    oid, tdd = SOURCES[S_OPEN][3], _tdd_path(S_OPEN)
    r.decision(5, "phase5.map_columns", 0.0011)
    cmap = r.step(
        5,
        "D1",
        {"sheet": None, "headers": ["code", "branch", "hours"]},
        {"tool": "column.map", "target_fields": SOURCES[S_OPEN][4]},
        {"macro_path": f"05-execute/macros/{S_OPEN}-{_h('sig', n=16)}.json", "replay": False},
        {"status": "ok", "mapped_columns": 3},
        source=S_OPEN,
        objective=oid,
        tdd=tdd,
        parent=fetched["step_id"],
    )
    rows = [
        ("L-101", "North Branch", "Mon-Fri 09:00-17:00"),
        ("L-102", "River Branch", "Tue-Fri 10:00-18:00"),
        ("L-103", "Hill Branch", "Mon-Thu 08:00-20:00"),
        ("L-104", "Harbor Branch", None),
    ]
    for n, (code, name, hours) in enumerate(rows, start=2):
        at = r.ts()
        vids = []
        for prop, header, value in (
            ("branch_code", "code", code),
            ("name", "branch", name),
            ("opening_hours", "hours", hours),
        ):
            if value is None:
                continue
            vids.append(
                vals.add(
                    code, prop, value, S_OPEN, _evidence(data_url, key, f"table:{n}:{header}", shot, at, S_OPEN, "csv")
                )
            )
        r.step(
            5,
            "D0",
            {"sheet": None, "row": n},
            {
                "tool": "emit.observation",
                "properties": [p for p, v in (("branch_code", code), ("name", name), ("opening_hours", hours)) if v],
            },
            {"tool": "emit.observation", "count": len(vids)},
            {"status": "ok", "literal_cells": True},
            source=S_OPEN,
            objective=oid,
            tdd=tdd,
            parent=cmap["step_id"],
            value_ids=vids,
        )

    # P5 · S1 browser controller on the city directory: screen, gate, act, Jev then Vultr verify, extract ----------
    url, _, _, oid, fields, _ = SOURCES[S_DIR]
    tdd = _tdd_path(S_DIR)
    session = f"browser-{_h('session-dir', n=12)}"
    opened = r.step(
        5,
        "S1",
        {"target_fields": fields},
        {"tool": "browser_agent.session.open"},
        {"tool": "browser_agent.session.open"},
        {"status": "started"},
        source=S_DIR,
        objective=oid,
        tdd=tdd,
    )
    s1 = r.shot(url, S_DIR, "plan")
    plan = r.step(
        5,
        "S1",
        {"url": url, "title": "Branches", "page_withheld": False},
        {"tool": "plan", "goal": "Read branch codes, names and free internet", "model": PLANNER},
        {
            "proposed": {"tool": "click", "args": {"element": "e12", "expectation": "branch table"}},
            "cell": {"cell_id": "cell:dir01", "isolation": {"runtime": "runsc"}},
        },
        {"ok": True},
        source=S_DIR,
        objective=oid,
        tdd=tdd,
        parent=opened["step_id"],
        session_id=session,
        screenshot_key=s1,
        screen={
            "flagged": False,
            "jev_choice": "benign",
            "jev_confidence": 0.96,
            "safety_verdict": "safe",
            "reason": "no instructions aimed at an agent",
            "by": "gateway",
        },
        usage={"model": PLANNER, "backend": "vultr", "input_tokens": 5400, "output_tokens": 180, "est_usd": 0.0042},
    )
    gate_ok = r.step(
        5,
        "S1",
        {"url": url},
        {"tool": "click", "args": {"element": "e12"}},
        {"gated": True},
        {
            "guard": {
                "tier": "LOW",
                "decided_by": "code",
                "code_tier": "LOW",
                "code_reason": "navigation inside the allowed domain",
                "jev": None,
                "hard": False,
            }
        },
        source=S_DIR,
        objective=oid,
        tdd=tdd,
        parent=plan["step_id"],
        session_id=session,
        event="action_gate",
        screenshot_key=s1,
        gate={
            "action": "click 'Branch table'",
            "risk_tier": "LOW",
            "decided_by": "code",
            "outcome": "allowed",
            "approval_path": None,
        },
    )
    s2 = r.shot(url, S_DIR, "act")
    act = r.step(
        5,
        "S1",
        {"url": url},
        {"tool": "click", "args": {"element": "e12"}},
        {"ok": True, "url_after": url + "#table", "blocked_hosts": []},
        {"ok": True},
        source=S_DIR,
        objective=oid,
        tdd=tdd,
        parent=gate_ok["step_id"],
        session_id=session,
        screenshot_key=s2,
    )
    r.step(
        5,
        "S1",
        {"diff_summary": "table appeared"},
        {"tool": "verify", "first_pass": "jev"},
        {"p_yes": 0.58},
        {"verdict": "uncertain"},
        source=S_DIR,
        objective=oid,
        tdd=tdd,
        parent=act["step_id"],
        session_id=session,
        event="verify",
        g=gen("jev", JEV),
        usage={"model": JEV, "backend": "jev", "input_tokens": 900, "output_tokens": 4, "est_usd": 0.0001},
        verify={
            "goal": "branch table visible",
            "verdict": "uncertain",
            "confidence": 0.58,
            "backend": "jev",
            "model": JEV,
            "screenshot_key": s2,
        },
    )
    r.step(
        5,
        "S1",
        {"url": url + "#table", "jev_first_pass": {"verdict": "uncertain", "p_yes": 0.58}},
        {"tool": "verify", "model": VISION},
        {"verdict": "achieved"},
        {"reason": "the branch table with codes is visible"},
        source=S_DIR,
        objective=oid,
        tdd=tdd,
        parent=act["step_id"],
        session_id=session,
        event="verify",
        g=gen("vultr", VISION),
        usage={"model": VISION, "backend": "vultr", "input_tokens": 1500, "output_tokens": 40, "est_usd": 0.0021},
        verify={
            "goal": "branch table visible",
            "verdict": "achieved",
            "confidence": 0.91,
            "backend": "vultr",
            "model": VISION,
            "screenshot_key": s2,
        },
    )
    s3 = r.shot(url, S_DIR, "quarantine")
    r.step(
        5,
        "S1",
        {"url": url + "/notice", "injection_suspected": True},
        {"tool": "screen", "goal": "Read branch codes", "by": "controller"},
        {"withheld_from_planning": True, "captured_as": s3},
        {"status": "quarantined_continue", "reason": "text instructs the reader to submit a form"},
        source=S_DIR,
        objective=oid,
        tdd=tdd,
        parent=act["step_id"],
        session_id=session,
        event="quarantine",
        screenshot_key=s3,
        g=gen("jev", JEV),
        screen={
            "flagged": True,
            "jev_choice": "injection",
            "jev_confidence": 0.97,
            "safety_verdict": "unsafe",
            "reason": "text instructs the reader to submit a form",
            "by": "controller",
        },
    )
    selectors = {
        "branch_code": "table tr:nth-child({n}) td:nth-child(1)",
        "name": "table tr:nth-child({n}) td:nth-child(2)",
        "free_internet": "table tr:nth-child({n}) td:nth-child(3)",
    }
    table = {"L-101": ("North Branch", "true"), "L-102": ("River Branch", "false")}
    extract = r.step(
        5,
        "S1",
        {"url": url + "#table"},
        {"tool": "extract", "args": {"fields": {k: v.format(n=2) for k, v in selectors.items()}}},
        {"ok": True, "values": {"branch_code": {"value": "L-101", "selector": selectors["branch_code"].format(n=2)}}},
        {"ok": True, "fields_found": sorted(selectors), "fields_missing": []},
        source=S_DIR,
        objective=oid,
        tdd=tdd,
        parent=act["step_id"],
        session_id=session,
        screenshot_key=s2,
    )
    r.step(
        5,
        "S1",
        {"url": url + "#table"},
        {"tool": "done", "args": {"status": "achieved"}},
        {"done": True},
        {"goal": "Read branch codes", "goal_status": "achieved", "summary": "two rows read"},
        source=S_DIR,
        objective=oid,
        tdd=tdd,
        parent=plan["step_id"],
        session_id=session,
        screenshot_key=s2,
    )
    r.step(
        5,
        "S1",
        {"controller_session": session},
        {"tool": "browser_agent.session.act"},
        {"tool": "browser_agent.session.act"},
        {"status": "ok", "rows": 2},
        source=S_DIR,
        objective=oid,
        tdd=tdd,
        parent=opened["step_id"],
    )
    r.job(opened, S_DIR, job_id="job:dir01")
    for n, (code, (name, internet)) in enumerate(table.items(), start=2):
        for prop, value in (("branch_code", code), ("name", name), ("free_internet", internet == "true")):
            ev = _evidence(
                url + "#table", s2, selectors[prop].format(n=n), s2, extract["ts"], S_DIR, "html", extract["step_id"]
            )
            vid = vals.add(code, prop, value, S_DIR, ev)
            r.step(
                5,
                "S1",
                {"property_id": prop, "extract_step_id": extract["step_id"]},
                {"tool": "emit.observation", "property_id": prop},
                {"tool": "emit.observation", "count": 1},
                {"status": "ok", "literal_controller_value": True},
                source=S_DIR,
                objective=oid,
                tdd=tdd,
                parent=extract["step_id"],
                value_ids=[vid],
            )

    # P5 · complete-list membership: derived true/false, and two honest refusals ------------------------------------
    page, shot, fetched, key, data_url = download(S_REG, "code\nL-101\nＬ－101\nL-103\nL-105\n".encode())
    oid, tdd = SOURCES[S_REG][3], _tdd_path(S_REG)
    r.decision(5, "phase5.map_columns", 0.0008)
    mmap = r.step(
        5,
        "D1",
        {"sheet": None, "headers": ["code"]},
        {"tool": "column.map", "target_fields": ["in_registry"]},
        {"macro_path": f"05-execute/macros/{S_REG}-{_h('reg', n=16)}.json", "replay": False},
        {"status": "ok", "mapped_columns": 1},
        source=S_REG,
        objective=oid,
        tdd=tdd,
        parent=fetched["step_id"],
    )
    derived = []
    listed = {"L-103": 4, "L-105": 5}
    for code in ("L-102", "L-103", "L-104", "L-105"):  # L-101 is ambiguous in the list (two spellings): no value
        row = listed.get(code, 0)
        sel = f"table:{row}:code" if row else "complete-list:identifier-absence"
        ev = _evidence(data_url, key, sel, shot, r.ts(), S_REG, "csv")
        if row:
            derived.append(vals.add(code, "branch_code", code, S_REG, ev))
        derived.append(vals.add(code, "in_registry", bool(row), S_REG, ev))
    r.step(
        5,
        "D0",
        {
            "list_entries": 2,
            "entities_checked": 4,
            "ambiguous_identifiers": 1,
            "complete_capture": True,
            "truncated": False,
        },
        {"tool": "membership.derive", "property_id": "in_registry"},
        {"bronze_key": key, "true_count": 2},
        {"status": "derived", "false_count": 2},
        source=S_REG,
        objective=oid,
        tdd=tdd,
        parent=mmap["step_id"],
        value_ids=derived,
    )
    page, _, fetched, _, _ = download(S_REG2, b"code\nL-201\n" * 3)
    r.step(
        5,
        "D0",
        {"complete_capture": False, "truncated": True, "format": "csv"},
        {"tool": "membership.derive", "property_id": "in_registry"},
        {"derived": False},
        {"status": "refused", "reason": "list_parse_truncated"},
        source=S_REG2,
        objective=SOURCES[S_REG2][3],
        tdd=_tdd_path(S_REG2),
        parent=fetched["step_id"],
    )
    url, _, _, oid, _, _ = SOURCES[S_NOFILE]
    page, _, _ = r.capture(5, S_NOFILE, oid, _tdd_path(S_NOFILE), url, [url.split("/")[2]])
    r.step(
        5,
        "D0",
        {"complete_capture": False, "download_candidates": 0},
        {"tool": "membership.derive", "property_id": "in_registry"},
        {"derived": False},
        {"status": "refused", "reason": "membership_requires_downloaded_file"},
        source=S_NOFILE,
        objective=oid,
        tdd=_tdd_path(S_NOFILE),
        parent=page["step_id"],
    )

    # P5 · Pattern A on a branch site: a failing repair, a passing one, the promoted macro, a sandbox timeout -------
    url, _, _, oid, _, _ = SOURCES[S_SITE]
    tdd = _tdd_path(S_SITE)
    page, shot, html_key = r.capture(5, S_SITE, oid, tdd, url, [url.split("/")[2]])
    r.decision(5, "phase5.map_columns", 0.0010)
    smap = r.step(
        5,
        "D1",
        {"sheet": "html", "headers": ["Code", "Internet", "Hours"]},
        {"tool": "column.map", "target_fields": SOURCES[S_SITE][4]},
        {"macro_path": f"05-execute/macros/{S_SITE}-{_h('site', n=16)}.json", "replay": False},
        {"status": "ok", "mapped_columns": 3},
        source=S_SITE,
        objective=oid,
        tdd=tdd,
        parent=page["step_id"],
    )
    code_key = lake.put(
        b"def extract(page):\n    return page['hours']\n", "text/x-python", url, S_SITE, smap["step_id"], r.ts()
    )
    diff_key = lake.put(b"+    return page.select('dl')\n", "text/x-diff", url, S_SITE, smap["step_id"], r.ts())
    rep1 = r.step(
        5,
        "D1",
        {"capture_keys": [html_key]},
        {"action": "code.write", "code_key": code_key},
        {
            "action": "code.test",
            "network": "none",
            "runtime": "runsc_requested",
            "limits": {"timeout_s": 30, "memory_mb": 256},
        },
        {"status": "fail", "error": None},
        source=S_SITE,
        objective=oid,
        tdd=tdd,
        parent=smap["step_id"],
        event="repair",
        repair={
            "attempt": 1,
            "max_attempts": 3,
            "code_key": code_key,
            "result": "fail",
            "stderr_excerpt": "KeyError: 'hours'",
            "diff_key": diff_key,
            "test": {"pages": 3, "precision": 0.0, "coverage": 0.0},
        },
    )
    rep2 = r.step(
        5,
        "D1",
        {"capture_keys": [html_key]},
        {"action": "code.write", "code_key": code_key},
        {
            "action": "code.test",
            "network": "none",
            "runtime": "runsc_requested",
            "limits": {"timeout_s": 30, "memory_mb": 256},
        },
        {"status": "pass", "error": None},
        source=S_SITE,
        objective=oid,
        tdd=tdd,
        parent=rep1["step_id"],
        event="repair",
        repair={
            "attempt": 2,
            "max_attempts": 3,
            "code_key": code_key,
            "result": "pass",
            "stderr_excerpt": "",
            "diff_key": diff_key,
            "test": {"pages": 3, "precision": 1.0, "coverage": 1.0},
        },
    )
    r.step(
        5,
        "D1",
        {"repair_attempts": 2, "test": {"pages": 3, "precision": 1.0, "coverage": 1.0}},
        {"tool": "code.promote", "source_id": S_SITE},
        {"macro_path": f"05-macros/{S_SITE}/v1", "version": 1, "code_key": code_key},
        {"status": "promoted"},
        source=S_SITE,
        objective=oid,
        tdd=tdd,
        parent=rep2["step_id"],
        event="crystallization",
    )
    at = r.ts()
    vids = [
        vals.add(
            "L-103", "free_internet", True, S_SITE, _evidence(url, html_key, "dl dd.internet", shot, at, S_SITE, "html")
        ),
        vals.add(
            "L-103",
            "opening_hours",
            "Mon-Fri 08:00-20:00",
            S_SITE,
            _evidence(url, html_key, "dl dd.hours", shot, at, S_SITE, "html"),
        ),
    ]
    r.step(
        5,
        "D1",
        {"sheet": "html", "row": 1},
        {"tool": "emit.observation", "properties": ["free_internet", "opening_hours"]},
        {"tool": "emit.observation", "count": 2},
        {"status": "ok", "literal_cells": True},
        source=S_SITE,
        objective=oid,
        tdd=tdd,
        parent=smap["step_id"],
        value_ids=vids,
    )
    more = f"{url}/annex"
    killed = r.step(
        5,
        "S1",
        {"url": more},
        {"url": more},
        {"bronze_key": html_key},
        {"status": "hard_stop", "reason": "timeout"},
        source=S_SITE,
        objective=oid,
        tdd=tdd,
        event="limit_kill",
    )
    r.job(killed, S_SITE, failure="timeout")

    # P5 · a repair that never passes: escalation to S1, then the cell's browser dies (hard stop) ---------------
    url, _, _, oid, _, _ = SOURCES[S_SITE2]
    tdd = _tdd_path(S_SITE2)
    page, shot, html_key = r.capture(5, S_SITE2, oid, tdd, url, [url.split("/")[2]])
    rep = r.step(
        5,
        "D1",
        {"capture_keys": [html_key]},
        {"action": "code.write", "code_key": code_key},
        {"action": "code.test", "network": "none", "runtime": "runsc_requested"},
        {"status": "fail", "error": "executor_error"},
        source=S_SITE2,
        objective=oid,
        tdd=tdd,
        parent=page["step_id"],
        event="repair",
        repair={
            "attempt": 1,
            "max_attempts": 1,
            "code_key": code_key,
            "result": "fail",
            "stderr_excerpt": "RuntimeError: sandbox unavailable",
            "diff_key": diff_key,
            "test": {"pages": 1, "precision": 0.0, "coverage": 0.0},
        },
    )
    r.step(
        5,
        "S1",
        {"repair_failure": "executor_error"},
        {"action": "code.repair", "next_mode": "S1"},
        {"strategy": "browser_agent.session.act"},
        {"status": "escalated"},
        source=S_SITE2,
        objective=oid,
        tdd=tdd,
        parent=rep["step_id"],
        event="escalation",
    )
    session2 = f"browser-{_h('session-annex', n=12)}"
    opened2 = r.step(
        5,
        "S1",
        {"target_fields": SOURCES[S_SITE2][4]},
        {"tool": "browser_agent.session.open"},
        {"tool": "browser_agent.session.open"},
        {"status": "started"},
        source=S_SITE2,
        objective=oid,
        tdd=tdd,
    )
    r.step(
        5,
        "S1",
        {"error": "TargetClosedError"},
        {"tool": "browser"},
        {"status": "failed"},
        {"status": "stopped", "reason": "browser closed"},
        source=S_SITE2,
        objective=oid,
        tdd=tdd,
        parent=opened2["step_id"],
        session_id=session2,
        event="hard_stop",
    )
    r.step(
        5,
        "S1",
        {"controller_session": session2},
        {"tool": "browser_agent.session.act"},
        {"tool": "browser_agent.session.act"},
        {"status": "hard_stop", "rows": 0},
        source=S_SITE2,
        objective=oid,
        tdd=tdd,
        parent=opened2["step_id"],
    )
    r.job(opened2, S_SITE2, job_id="job:annex01", failure="browser_closed", task_ok=False)

    # P5 · the portal: a redirect off the allowed domain (hard stop), gates denied by code and by a person,
    # a step cap, then a HIGH action waiting for approval (the run pauses here) -------------------------------
    url, _, _, oid, fields, _ = SOURCES[S_PORTAL]
    tdd = _tdd_path(S_PORTAL)
    session5 = f"browser-{_h('session-portal-login', n=12)}"
    opened5 = r.step(
        5,
        "S1",
        {"target_fields": fields},
        {"tool": "browser_agent.session.open"},
        {"tool": "browser_agent.session.open"},
        {"status": "started"},
        source=S_PORTAL,
        objective=oid,
        tdd=tdd,
    )
    s5 = r.shot(url + "/account", S_PORTAL, "login")
    wall = "a login wall hides the hours page"
    plan5 = r.step(
        5,
        "S1",
        {"url": url + "/account", "title": "Sign in", "page_withheld": False},
        {"tool": "plan", "goal": "Read hours and internet", "model": PLANNER},
        {"proposed": {"tool": "done", "args": {"status": "not_achievable", "summary": wall}}},
        {"ok": True},
        source=S_PORTAL,
        objective=oid,
        tdd=tdd,
        parent=opened5["step_id"],
        session_id=session5,
        screenshot_key=s5,
        usage={"model": PLANNER, "backend": "vultr", "input_tokens": 4100, "output_tokens": 90, "est_usd": 0.0031},
    )
    r.step(
        5,
        "S1",
        {"url": url + "/account"},
        {"tool": "done", "args": {"status": "not_achievable", "summary": wall}},
        {"done": True},
        {"goal": "Read hours and internet", "goal_status": "not_achievable", "summary": wall},
        source=S_PORTAL,
        objective=oid,
        tdd=tdd,
        parent=plan5["step_id"],
        session_id=session5,
        screenshot_key=s5,
    )
    r.step(
        5,
        "S1",
        {"controller_session": session5},
        {"tool": "browser_agent.session.act"},
        {"tool": "browser_agent.session.act"},
        {"status": "not_achievable", "rows": 0},
        source=S_PORTAL,
        objective=oid,
        tdd=tdd,
        parent=opened5["step_id"],
    )
    r.capture(
        5,
        S_PORTAL,
        oid,
        tdd,
        url,
        [url.split("/")[2]],
        status="blocked",
        reason="redirect_outside_registrable_domain",
        event="hard_stop",
    )
    session3 = f"browser-{_h('session-portal', n=12)}"
    opened3 = r.step(
        5,
        "S1",
        {"target_fields": fields},
        {"tool": "browser_agent.session.open"},
        {"tool": "browser_agent.session.open"},
        {"status": "started"},
        source=S_PORTAL,
        objective=oid,
        tdd=tdd,
    )
    s4 = r.shot(url, S_PORTAL, "gate")
    guard_high = {
        "tier": "HIGH",
        "decided_by": "code",
        "code_tier": "HIGH",
        "code_reason": "form submission",
        "jev": None,
        "hard": True,
    }
    r.step(
        5,
        "S1",
        {"url": url},
        {"tool": "click", "args": {"element": "e40"}},
        {"gated": True},
        {"guard": guard_high, "reason": "form submission"},
        source=S_PORTAL,
        objective=oid,
        tdd=tdd,
        parent=opened3["step_id"],
        session_id=session3,
        event="action_gate",
        screenshot_key=s4,
        gate={
            "action": "click 'Submit'",
            "risk_tier": "HIGH",
            "decided_by": "code",
            "outcome": "denied",
            "approval_path": None,
        },
    )
    denied_path = f"{APPROVAL_DENIED_DIR}/APPROVAL_PENDING.md"
    pend = r.step(
        5,
        "S1",
        {"url": url},
        {"tool": "type", "args": {"element": "e44", "text": "…"}},
        {"approval_request": APPROVAL_DENIED_DIR.split("/")[1]},
        {"guard": dict(guard_high, decided_by="jev", hard=False), "waiting_s": 600},
        source=S_PORTAL,
        objective=oid,
        tdd=tdd,
        parent=opened3["step_id"],
        session_id=session3,
        event="action_gate",
        screenshot_key=s4,
        g=gen("jev", JEV),
        gate={
            "action": "type into 'Email for updates'",
            "risk_tier": "HIGH",
            "decided_by": "jev",
            "outcome": "pending_approval",
            "approval_path": denied_path,
        },
    )
    r.step(
        5,
        "S1",
        {"url": url},
        {"tool": "type", "args": {"element": "e44", "text": "…"}},
        {"approval_request": APPROVAL_DENIED_DIR.split("/")[1]},
        {
            "approval": {"approved": False, "approver": "reviewer@example.org"},
            "reason": "Not part of a read-only capture",
        },
        source=S_PORTAL,
        objective=oid,
        tdd=tdd,
        parent=pend["step_id"],
        session_id=session3,
        event="action_gate",
        screenshot_key=s4,
        g=gen("jev", JEV),
        gate={
            "action": "type into 'Email for updates'",
            "risk_tier": "HIGH",
            "decided_by": "jev",
            "outcome": "denied",
            "approval_path": denied_path,
        },
    )
    r.step(
        5,
        "S1",
        {"url": url},
        {"tool": "goal", "goal": "Read hours and internet"},
        {"stopped": True, "turns": 30},
        {"reason": "max_steps", "limit": 30},
        source=S_PORTAL,
        objective=oid,
        tdd=tdd,
        parent=opened3["step_id"],
        session_id=session3,
        event="limit_kill",
    )
    r.step(
        5,
        "S1",
        {"controller_session": session3},
        {"tool": "browser_agent.session.act"},
        {"tool": "browser_agent.session.act"},
        {"status": "limit_kill", "rows": 0},
        source=S_PORTAL,
        objective=oid,
        tdd=tdd,
        parent=opened3["step_id"],
    )

    # outer gap loop: reopen phase 4 for the truncated list, then the pending gate --------------------------------
    r.loop(
        "outer",
        1,
        "decide",
        "reopen",
        reopen=4,
        reason="in_registry below target: one list was truncated",
        gaps=["in_registry"],
    )
    session4 = f"browser-{_h('session-portal-2', n=12)}"
    opened4 = r.step(
        5,
        "S1",
        {"target_fields": fields},
        {"tool": "browser_agent.session.open"},
        {"tool": "browser_agent.session.open"},
        {"status": "started"},
        source=S_PORTAL,
        objective=oid,
        tdd=tdd,
    )
    r.step(
        5,
        "S1",
        {"url": url},
        {"tool": "click", "args": {"element": "e51"}},
        {"approval_request": APPROVAL_PENDING_DIR.split("/")[1]},
        {"guard": dict(guard_high, decided_by="jev", hard=False, code_reason="form submission"), "waiting_s": 600},
        source=S_PORTAL,
        objective=oid,
        tdd=tdd,
        parent=opened4["step_id"],
        session_id=session4,
        event="action_gate",
        screenshot_key=s4,
        g=gen("jev", JEV),
        gate={
            "action": "click 'Request a library card'",
            "risk_tier": "HIGH",
            "decided_by": "jev",
            "outcome": "pending_approval",
            "approval_path": f"{APPROVAL_PENDING_DIR}/APPROVAL_PENDING.md",
        },
    )
    return r.steps, r.jobs, vals


def build_failed_run(lake: Lake) -> tuple[list[dict], dict]:
    r = Recorder(lake, RUN_FAILED, T0 - timedelta(hours=3))
    r.artifact(1, "phase1.prd", "01-scope/prd.json")
    r.artifact(2, "phase2.ontology", "02-ontology/ontology.json")
    r.capture(
        3,
        "search-provider",
        None,
        "03-fanout/search-policy.json",
        "https://search-a.example.test/?q=x",
        ["search-a.example.test"],
        reason="blocked: http_403",
        event="hard_stop",
    )
    status = {
        "run_id": RUN_FAILED,
        "state": "failed",
        "phase": 3,
        "checkpoint_pending": None,
        "reason": "capture blocked: http_403",
        "updated_at": r.steps[-1]["ts"],
        "sources": [],
        "metrics": {
            "mode_counts": {"D0": 2, "D1": 0, "S1": 5, "S2": 0},
            "inference_backend": "vultr",
            "generated_by": gen(),
        },
        "generated_by": gen(),
    }
    return r.steps, status


def metrics_for(entities: list[dict], steps: list[dict], jobs: list[dict]) -> dict:
    """refiner.export._metrics: only `gold` fields count; `met` is false for a preview export."""
    props = [p["id"] for p in ONTOLOGY["properties"]]
    n = len(entities)
    gold = lambda e, p: e["properties"][p]["status"] == "gold" and bool(e["properties"][p]["evidence"])  # noqa: E731
    dod_props = [p["id"] for p in ONTOLOGY["properties"] if p["dod"]]
    meeting = sum(1 for e in entities if sum(gold(e, p) for p in dod_props) / len(dod_props) >= 0.8)
    with_three = sum(1 for e in entities if all(gold(e, p) for p in ("name", "opening_hours", "free_internet")))
    classes = {
        ev["source_type"]
        for e in entities
        for f in e["properties"].values()
        if f["status"] == "gold"
        for ev in f["evidence"]
    }
    modes = {m: sum(1 for s in steps if s["mode"] == m and s.get("event") != "loop") for m in ("D0", "D1", "S1", "S2")}
    backends: dict[str, int] = {}
    for s in steps:
        if s["requested"].get("tool") == "decision.complete_json":
            backends[s["usage"]["backend"]] = backends.get(s["usage"]["backend"], 0) + 1
    failed: dict[str, int] = {}
    for j in jobs:
        if j.get("failure_reason"):
            failed[j["failure_reason"]] = failed.get(j["failure_reason"], 0) + 1
    loops = [
        {
            "phase": s["loop"]["phase"],
            "iterations": s["loop"]["iteration"],
            "stop_reason": s["loop"]["stop_reason"],
            "usd": s["evaluated"]["usd"],
        }
        for s in steps
        if s.get("event") == "loop" and s["loop"]["role"] == "decide" and "stop_reason" in s["loop"]
    ]
    dod = [
        {"criterion_id": "libraries_found", "query": 'count_entities(class_id="library")', "target": 5, "actual": n},
        {
            "criterion_id": "with_hours_and_internet",
            "query": 'count_entities_with_properties(class_id="library", '
            'properties=["name","opening_hours","free_internet"])',
            "target": 20,
            "actual": with_three,
        },
        {
            "criterion_id": "source_classes",
            "query": "count_distinct_source_classes",
            "target": 3,
            "actual": len(classes),
        },
        {"criterion_id": "evidence_integrity", "query": "count_values_without_evidence", "target": 0, "actual": 0},
    ]
    for d in dod:
        d["met"] = False  # preview export: the engine never marks a preview as meeting its DoD
    return {
        "run_id": RUN,
        "entities_total": {"library": n},
        "entities_meeting_dod": {"library": meeting},
        "per_property_completeness": {"library": {p: round(sum(gold(e, p) for e in entities) / n, 4) for p in props}},
        "distinct_source_classes": len(classes),
        "values_without_evidence": 0,
        "level_ratio_coverage": {},
        "mode_counts": modes,
        "decisions_by_backend": backends,
        "inference_backend": "vultr",
        "dod": dod,
        "jobs": {"ok": sum(1 for j in jobs if not j.get("failure_reason")), "failed_by_reason": failed},
        "preview": True,
        "loops": loops,
        "generated_by": gen(),
    }


def build(root: Path) -> tuple[Path, Path]:
    """Write ROOT/case and ROOT/lake; returns (case_dir, lake_dir)."""
    case, lake_dir = root / "case", root / "lake"
    build_case(case)
    lake = Lake(lake_dir)
    steps, jobs, vals = build_main_run(lake, case)
    entities = vals.entities([p["id"] for p in ONTOLOGY["properties"]])
    metrics = metrics_for(entities, steps, jobs)
    feed = lake_dir / "runs" / LAKE_CASE / RUN
    feed.mkdir(parents=True, exist_ok=True)
    (feed / "trace.live.jsonl").write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in steps))
    (feed / "jobs.jsonl").write_text("".join(json.dumps(j) + "\n" for j in jobs))
    health: dict[str, dict] = {}
    for s in steps:
        if s["phase"] == 5 and s.get("source_id") in SOURCES:
            h = health.setdefault(s["source_id"], {"ok": 0, "failed": 0, "yield": 0})
            bad = s.get("event") in ("hard_stop", "limit_kill") or (
                isinstance(s["evaluated"], dict)
                and s["evaluated"].get("status") in ("refused", "hard_stop", "limit_kill")
            )
            h["failed" if bad else "ok"] += 1
            h["yield"] += len(s["value_ids"])
    status = {
        "run_id": RUN,
        "state": "paused",
        "phase": 5,
        "checkpoint_pending": "action",
        "reason": "an action waits for approval",
        "updated_at": steps[-1]["ts"],
        "sources": [
            {
                "source_id": sid,
                "source_type": SOURCES[sid][1],
                "format": SOURCES[sid][2],
                "discovered_by": {"provider": "search-a", "at": T0.isoformat()},
                "health": h,
            }
            for sid, h in health.items()
        ],
        "metrics": metrics,
        "generated_by": gen(),
        "preview": True,
    }
    (feed / "status.json").write_text(json.dumps(status, indent=2))
    gold = lake_dir / "gold" / LAKE_CASE / RUN
    gold.mkdir(parents=True, exist_ok=True)
    (gold / "entities.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entities))
    trace = sorted(steps, key=lambda s: (s["ts"], s["step_id"]))
    (gold / "trace.jsonl").write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in trace))
    (gold / "metrics.json").write_text(json.dumps(metrics, indent=2))
    (gold / "ontology.json").write_text(json.dumps(ONTOLOGY, indent=2))
    (gold / "dod-queries.json").write_text(json.dumps(DOD_QUERIES, indent=2))
    (lake_dir / "gold" / LAKE_CASE / "latest.json").write_text(json.dumps({"run_id": RUN}))
    fsteps, fstatus = build_failed_run(lake)
    ffeed = lake_dir / "runs" / LAKE_CASE / RUN_FAILED
    ffeed.mkdir(parents=True, exist_ok=True)
    (ffeed / "trace.live.jsonl").write_text("".join(json.dumps(s) + "\n" for s in fsteps))
    (ffeed / "status.json").write_text(json.dumps(fstatus, indent=2))
    (lake_dir / "runs" / LAKE_CASE / "latest.json").write_text(json.dumps({"run_id": RUN}))
    return case, lake_dir


def spec_with_realrun(cases_dir: Path) -> str:
    case, lake = build(cases_dir / "realrun")
    return f"{spec_for(cases_dir)},{CASE}={case}:{lake}"


@pytest.fixture
def rr_client(cases_dir) -> TestClient:
    env = {"ONTOFILL_CONSOLE_CASES": spec_with_realrun(cases_dir), "ONTOFILL_CONSOLE_IDENTITY": "sso"}
    return TestClient(create_app(settings_from_env(env)))


# --- checks ----------------------------------------------------------------------------------------------------

LEAKS = ("built-in method", "bound method", "Undefined", "{'", "object at 0x", "Traceback", "by none", "· none ·")
PY_REPR = re.compile(r"(?<![\w.-])(True|False|None)(?![\w-])")


def visible(page: str) -> str:
    import html

    text = re.sub(r"<script.*?</script>|<style.*?</style>|<head.*?</head>", " ", page, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def clean(page: str) -> str:
    text = visible(page)
    for leak in LEAKS:
        assert leak not in text, (leak, text[max(0, text.index(leak) - 120) : text.index(leak) + 80])
    m = PY_REPR.search(text)
    assert m is None, text[max(0, m.start() - 120) : m.end() + 60]
    return text


def _gold(client: TestClient):
    return client.app.state.settings.cases[CASE].store.run(RUN)


@pytest.fixture(scope="module")
def recorded(tmp_path_factory) -> tuple[Path, Path]:
    return build(tmp_path_factory.mktemp("realrun"))


def test_recorded_run_is_engine_shaped(recorded):
    """Every live and gold record validates against the engine's own JSON Schemas."""
    if not SCHEMAS.is_dir():
        pytest.skip("engine schemas are not next to the console")
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource

    registry = Registry()
    for p in SCHEMAS.glob("*.schema.json"):
        doc = json.loads(p.read_text())
        registry = registry.with_resource(doc["$id"], Resource.from_contents(doc))

    def check(name: str, docs: list) -> None:
        v = Draft202012Validator(
            json.loads((SCHEMAS / f"{name}.schema.json").read_text()), registry=registry, format_checker=FormatChecker()
        )
        errors = [(name, e.message[:160]) for d in docs for e in v.iter_errors(d)]
        assert not errors, errors[:5]

    case, lake = recorded
    lines = lambda p: [json.loads(x) for x in p.read_text().splitlines() if x.strip()]  # noqa: E731
    feed, gold = lake / "runs" / LAKE_CASE / RUN, lake / "gold" / LAKE_CASE / RUN
    check(
        "trace-step",
        lines(feed / "trace.live.jsonl")
        + lines(gold / "trace.jsonl")
        + lines(lake / "runs" / LAKE_CASE / RUN_FAILED / "trace.live.jsonl"),
    )
    check(
        "run-status",
        [
            json.loads((feed / "status.json").read_text()),
            json.loads((lake / "runs" / LAKE_CASE / RUN_FAILED / "status.json").read_text()),
        ],
    )
    check("jobs", lines(feed / "jobs.jsonl"))
    check("entity", lines(gold / "entities.jsonl"))
    check("metrics", [json.loads((gold / "metrics.json").read_text())])
    check("ontology", [json.loads((gold / "ontology.json").read_text())])
    check("dod-queries", [json.loads((gold / "dod-queries.json").read_text())])
    check("objectives", [json.loads((case / "03-fanout/objectives.json").read_text())])
    check("tdd", [json.loads(p.read_text()) for p in case.glob("04-local/*/tdd.json")])
    check("approved", [json.loads(p.read_text()) for p in case.rglob("APPROVED")])
    check("bronze-sidecar", [json.loads(p.read_text()) for p in (lake / "bronze").rglob("*.meta.json")])
    steps = lines(feed / "trace.live.jsonl")
    events = {s.get("event") for s in steps}
    assert {
        "loop",
        "hard_stop",
        "verify",
        "action_gate",
        "limit_kill",
        "quarantine",
        "repair",
        "crystallization",
        "escalation",
    } <= events
    tools = {(s["requested"] or {}).get("tool") for s in steps if isinstance(s["requested"], dict)}
    assert {
        "membership.derive",
        "column.map",
        "decision.complete_json",
        "browser_agent.session.open",
        "emit.observation",
    } <= tools
    outcomes = {s["gate"]["outcome"] for s in steps if s.get("event") == "action_gate"}
    assert outcomes == {"allowed", "pending_approval", "denied"}
    assert {s["verify"]["backend"] for s in steps if s.get("event") == "verify"} == {"jev", "vultr"}
    refusals = {s["evaluated"]["reason"] for s in steps if s["evaluated"].get("status") == "refused"}
    assert refusals == {"list_parse_truncated", "membership_requires_downloaded_file"}
    entities = lines(gold / "entities.jsonl")
    fields = [f for e in entities for f in e["properties"].values()]
    assert any(f["status"] == "missing" and "value_id" not in f for f in fields)
    assert any(f["status"] == "conflict" for f in fields)
    assert {f["value"] for e in entities for k, f in e["properties"].items() if k == "in_registry"} >= {True, False}


PAGES = {  # path -> facts that must be on the page (visible text)
    "/": ["Public library access (recorded run)"],
    "/inbox": [
        "Stopped: captcha",
        "Refused: membership.derive",
        "Domain blocked",
        "Action denied",
        "Page quarantined",
        "Job stopped by a resource limit",
        "click 'Request a library card'",
    ],
    f"/cases/{CASE}": ["click 'Request a library card'", RUN],
    f"/cases/{CASE}/runs": [RUN, RUN_FAILED, "failed"],
    f"/cases/{CASE}/runs/{RUN}": [
        "Hard stops",
        "Vision checks",
        "Approve-before-submit gate",
        "list_parse_truncated",
        "the branch table with codes is visible",
    ],
    f"/cases/{CASE}/runs/{RUN_FAILED}": ["blocked: http_403"],
    f"/cases/{CASE}/operation": [
        "an action waits for approval",
        "reopened P4",
        "Phase 1 loop",
        "Phase 3 loop",
        "revised by planner-model",
    ],
    f"/cases/{CASE}/operation?run={RUN_FAILED}": ["capture blocked: http_403"],
    f"/cases/{CASE}/definition": ["with_hours_and_internet", "every branch in Example City"],
    f"/cases/{CASE}/discovery": ["Authority-passed", "search-b", "the page quotes hours second-hand", "Phase 3 loop"],
    f"/cases/{CASE}/pages": [S_DIR, "quarantined", "stopped by a limit"],
    f"/cases/{CASE}/sites": ["No site graph yet"],
    f"/cases/{CASE}/output": ["Libraries found", "Conflicts · 1 kept"],
    f"/cases/{CASE}/entities": ["Hill Branch", "L-105"],
    f"/cases/{CASE}/graph": ["Hill Branch", "5 of 5 entities"],
    f"/cases/{CASE}/failures": [
        "Stopped: captcha",
        "Stopped: bot_wall",
        "Stopped: login",
        "Stopped: browser closed",
        "Domain blocked",
        "Refused: membership.derive",
        "list_parse_truncated",
        "membership_requires_downloaded_file",
        "Action denied",
        "Action awaits approval",
        "Stopped by a resource limit",
        "Page quarantined",
        "Escalated to S1",
    ],
    f"/cases/{CASE}/cost": ["planner-model", "vision-model", "jev-screen", "unpriced-model"],
    f"/cases/{CASE}/learning": ["Crystallization", S_SITE, "KeyError: 'hours'"],
    f"/cases/{CASE}/compare": [RUN, RUN_FAILED, "no gold"],
    f"/cases/{CASE}/approvals": ["type into 'Email for updates'", "click 'Request a library card'"],
    f"/cases/{CASE}/approvals/{APPROVAL_PENDING_DIR}": ["click 'Request a library card'", "HIGH"],
    f"/compare?a={CASE}&b=libraries": ["Public library access (recorded run)"],
    "/evidence": [],
}


@pytest.mark.parametrize("path", list(PAGES))
def test_page_renders_real_run(rr_client, path):
    r = rr_client.get(path)
    assert r.status_code == 200, (path, r.text[:300])
    text = clean(r.text)
    for fact in PAGES[path]:
        assert fact in text, (path, fact)


def test_entity_lineage_and_values_read_as_text(rr_client):
    eid = _entity_id("L-103")
    text = clean(rr_client.get(f"/cases/{CASE}/entities/{eid}").text)
    assert "Hill Branch" in text and "conflict" in text and "yes" in text  # free_internet true, in_registry true
    m = rr_client.get(f"/cases/{CASE}/api/viz/entities/{eid}").json()
    assert any(p.get("value") is True for p in m["props"])  # the JSON twin keeps the boolean
    missing = _entity_id("L-105")
    text = clean(rr_client.get(f"/cases/{CASE}/entities/{missing}").text)
    assert "L-105" in text
    run = _gold(rr_client)
    vid = next(f["value_id"] for f in run.entities_by_id[eid]["properties"].values() if f["value"] is True)
    text = clean(rr_client.get(f"/cases/{CASE}/lineage/{vid}").text)
    assert "yes" in text
    lin = rr_client.get(f"/cases/{CASE}/api/viz/lineage/{vid}").json()
    assert lin["value"] and lin["chains"]


def test_failures_classify_real_stops_refusals_and_gates(rr_client):
    m = rr_client.get(f"/cases/{CASE}/api/viz/failures").json()
    by_kind: dict[str, list[str]] = {}
    for s in m["strips"]:
        by_kind.setdefault(s["kind"], []).append(s["title"])
    assert sorted(by_kind["stop"]) == sorted(
        [
            "Stopped: captcha · search-provider",
            "Stopped: bot_wall · search-provider",
            f"Stopped: browser closed · {S_SITE2}",
            f"Stopped: login · {S_PORTAL}",
        ]
    )
    assert by_kind["blocked_domain"] == [f"Domain blocked · {S_PORTAL}"]  # a redirect off the allowed domain
    assert sorted(by_kind["refusal"]) == sorted(
        [f"Refused: membership.derive · {S_REG2}", f"Refused: membership.derive · {S_NOFILE}"]
    )
    gates = by_kind["gate"]
    assert gates.count(f"Action denied · {S_PORTAL}") == 2  # by code, and by the approver
    assert gates.count(f"Action awaits approval · {S_PORTAL}") == 1  # the answered request is not still waiting
    assert len(by_kind["limit_kill"]) == 2 and "failure" not in by_kind  # no session summary duplicates
    counts = {c["kind"]: c["n"] for c in m["counts"]}
    assert counts["refusal"] == 2 and counts["stop"] == 4
    assert m["totals"]["killed"] == 2  # jobs.schema.json failure_reason, not a guessed killed_by
    timeout = next(c for c in m["cells"] if c["killed_by"] == "timeout")
    states = {c["key"]: c["state"] for c in timeout["checkpoints"]}
    assert states["where"] == states["secrets"] == states["isolation"] == "pending"  # not_run is not a failure
    assert states["teardown"] == "pass"


def test_inbox_lists_stops_refusals_and_denied_actions(rr_client):
    m = rr_client.get("/api/viz/inbox").json()
    mine = [s for s in m["strips"] if s["case_id"] == CASE]
    kinds = [s["kind"] for s in mine]
    assert kinds.count("checkpoint") == 1 and mine[0]["state"] == "need"  # the pending action comes first
    assert kinds.count("stop") == 4 and kinds.count("refusal") == 2 and kinds.count("blocked_domain") == 1
    assert kinds.count("gate") == 2 and all(s["state"] == "block" for s in mine if s["kind"] == "gate")
    assert {"quarantine", "limit_kill", "run"} <= set(kinds)
    run = next(s for s in mine if s["kind"] == "run")
    assert "an action waits for approval" in run["detail"]
    for s in mine:
        assert s["href"].startswith(f"/cases/{CASE}/")


def test_run_panel_counts_real_events_and_verdicts(rr_client):
    r = rr_client.get(f"/cases/{CASE}/api/runs/{RUN}").json()
    assert r["state"] == "paused" and r["count"] > 100
    panel = visible(r["panel_html"])
    assert "Hard stops 4" in panel and "Limit kills 2" in panel
    assert "Vision checks 2 : 1 achieved, 0 not achieved, 1 uncertain" in panel
    steps = visible(r["steps_html"])
    assert "the branch table with codes is visible" in steps  # the verifier's reason (evaluated.reason)
    assert not PY_REPR.search(steps)


def test_operation_attributes_decisions_like_the_engine(rr_client):
    m = rr_client.get(f"/cases/{CASE}/api/viz/operation").json()
    who = {d["key"]: d["n"] for d in m["deciders"]}
    live_steps = [
        json.loads(x)
        for x in (
            rr_client.app.state.settings.cases[CASE].store.source.root / "runs" / LAKE_CASE / RUN / "trace.live.jsonl"
        )
        .read_text()
        .splitlines()
    ]
    jev = sum(
        1
        for s in live_steps
        if (s.get("usage") or {}).get("backend") == "jev"
        or (s.get("gate") or {}).get("decided_by") == "jev"
        or (s.get("event") == "quarantine" and s["generated_by"]["backend"] == "jev")
    )
    vultr = sum(
        1 for s in live_steps if (s.get("usage") or {}).get("backend") == "vultr" and (s["usage"]["model"] != "none")
    )
    assert who["jev"] == jev - sum(1 for s in live_steps if s.get("event") == "quarantine")  # quarantine: code+screen
    assert who["vultr"] == vultr and who["code"] > who["vultr"]  # generated_by alone is the run's provenance
    assert m["reason"] == "an action waits for approval"
    fail = rr_client.get(f"/cases/{CASE}/api/viz/operation?run={RUN_FAILED}").json()
    assert fail["state"] == "failed" and fail["reason"] == "capture blocked: http_403"


def test_cost_counts_model_calls_only(rr_client):
    m = rr_client.get(f"/cases/{CASE}/api/viz/cost").json()
    live_steps = [
        json.loads(x)
        for x in (
            rr_client.app.state.settings.cases[CASE].store.source.root / "runs" / LAKE_CASE / RUN / "trace.live.jsonl"
        )
        .read_text()
        .splitlines()
    ]
    calls = [s["usage"] for s in live_steps if s.get("usage") and s["usage"]["model"] != "none"]
    assert m["n_usage"] == len(calls) and m["n_unpriced"] == 1  # one model without a price
    assert m["usd_total"] == pytest.approx(sum(u["est_usd"] or 0 for u in calls), abs=1e-6)
    models = {r["name"] for b in m["bars"] if b["key"] == "model" for r in b["table"]}
    assert "none" not in models
    text = clean(rr_client.get(f"/cases/{CASE}/cost").text)
    assert f"${m['usd_total']:.4f}" in text


VIEW_KEYS = {  # JSON twin -> required keys (with types) for the recorded run
    "operation": {
        "run_id": str,
        "runs": list,
        "pipe": list,
        "deciders": list,
        "modes": list,
        "threads": list,
        "reason": str,
    },
    "definition": {"run_id": str, "prd": dict, "dod": dict, "queries": dict},
    "discovery": {"funnel": dict, "rounds": list, "candidates": dict, "plans": dict},
    "pages": {"n_pages": int, "groups": list, "verdict_filters": list},
    "sites": {"rows": list, "empty": dict},
    "output": {"dod": list, "heat": dict, "conflicts_kept": int},
    "entities": {"rows": list, "n_total": int},
    "graph": {"nodes": list, "edges": list, "n_entities": int},
    "failures": {"strips": list, "counts": list, "cells": list, "totals": dict},
    "cost": {"usd_total": float, "bars": list, "n_priced": int},
    "learning": {"repair_groups": list, "crystallizations": list, "macros": list},
    "compare": {"run_a": str, "run_b": str, "metrics": list},
}


@pytest.mark.parametrize("view", list(VIEW_KEYS))
def test_json_twins_are_well_formed(rr_client, view):
    import jsonschema

    for q in ("", f"?run={RUN}", f"?run={RUN_FAILED}"):
        r = rr_client.get(f"/cases/{CASE}/api/viz/{view}{q}")
        assert r.status_code == 200, (view, q, r.text[:200])
        m = r.json()
        json.dumps(m)
        if q != f"?run={RUN_FAILED}":
            types = {str: "string", int: "integer", float: "number", list: "array", dict: "object"}
            schema = {
                "type": "object",
                "required": list(VIEW_KEYS[view]),
                "properties": {k: {"type": types[t]} for k, t in VIEW_KEYS[view].items()},
            }
            jsonschema.validate(m, schema)
    assert rr_client.get(f"/cases/{CASE}/api/viz/{view}?run=run-000000000000").status_code in (200, 404)
    assert rr_client.get(f"/cases/{CASE}/{view}?run=run-000000000000").status_code in (200, 404)
