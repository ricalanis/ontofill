"""Inference · who reasoned inside a run, on which provider, at what cost, and who acted (GAPS R19, architecture §7a).

Joins the inference gateway's call log (JSONL, one line per upstream call; the gateway never logs the key or the
prompt) with the run's trace steps by `step_id`. Read-only: the log path comes from `ONTOFILL_CONSOLE_GATEWAY_LOG`
(a file, or a directory of *.jsonl), which the operator mounts read-only.

Gateway record fields relied on (services/browser-agent gateway/calllog.py `CallLog.FIELDS`):
  ts, session_id, run_id, step_id, upstream (vultr | jev), purpose (chat | decision | screen), model, status,
  input_tokens, output_tokens, est_usd, est_tokens, gate, latency_ms, prompt_chars.
Optional fields read when a later gateway writes them: route, tool, engine_purpose (an engine purpose such as
`phase1.prd.section` or `critic.ontology`), case_id.

Definitions (all computed here, nothing extrapolated):
- A *model step* is a trace step with evidence of a model call: a non-empty `usage` (CONTRACT v1.0.5), `loop.model`,
  `verify.backend|model`, `gate.decided_by` jev|vultr, or `screen.by == "gateway"`. `generated_by` alone is provenance,
  not evidence of a call. A model step backed by the recorded double (`generated_by.backend` recorded|mock) makes no
  gateway call; it is flagged, not counted as unattributed.
- A *reasoning call* is a gateway record whose purpose is not `screen` (screening page content is a supporting
  safety check, listed but not counted in the Vultr share).
- *Joined*: a record joins a trace step on step_id (and session_id when the step names one). A record with no
  run_id and no step_id whose ts falls inside the run's window (first step → last step or status.updated_at) belongs
  to the run *by time window, not by step*: listed and counted in cost and the Vultr share, and also counted as
  unattributed. Engine calls (session_id "engine") carry neither id today.
- *Unattributed* (to a step): records naming this run or inside its window that match no step, plus live model steps
  with no gateway record. Must be 0. Across cases: records whose step_id matches no step of any case.
- *Decided by*: vultr / jev from the joined calls (else from the step's evidence), recorded when the step's
  generated_by is recorded|mock (outside the code-only modes D0/D1), human when generated_by says human, else code.
- *Purpose*: gateway `screen` is screen and `decision` (Jev) is decide. A `chat` call takes the engine purpose when a
  later log carries one (engine_purpose / route / tool: `critic.*` critique; `*prd*`, `phase1-4.*`, `plan*` plan;
  `extract*`, `phase5.*` extract; `verify*`, `vision*` verify; `decide*` decide), else the matched step's role (verify
  step verify; action gate decide; loop critique critique, check verify, decide decide, other roles plan; an extract
  tool or value_ids extract; else plan), else it stays `chat`.
"""

from __future__ import annotations

import json
import os
import statistics
from pathlib import Path
from urllib.parse import quote

import yaml
from fastapi import Request
from fastapi.responses import HTMLResponse

from .. import live
from ..gold import backend_of
from . import health_common as hc
from .core import SAFE_ERRORS, Artifacts, VizContext, gap, parse_ts

ORDER = 65
KEY, SLUG, LABEL = "inference", "inference", "Inference"
LOG_ENV = "ONTOFILL_CONSOLE_GATEWAY_LOG"
SOURCES = (f"{LOG_ENV} (gateway call log, JSONL) · runs/<case>/<run>/trace.live.jsonl · status.json · "
           "gold/<case>/<run>/ (generated_by) · case package generated_by · decisions.jsonl")
PROVIDERS = {"vultr": "Vultr Serverless Inference", "jev": "Jev"}
PURPOSES = ("plan", "critique", "extract", "verify", "screen", "decide")
LIVE_BACKENDS = ("vultr", "jev")
SIMULATED = ("recorded", "mock")
ENGINE_ACTORS = {"", "none", "engine", "ontofill", "runner", "engine runner", "ontofill-runner", "vultr", "jev", "code",
                 "deterministic", "gateway", "controller", "recorded", "mock", "human"}
MAX_ARTIFACT_FILES = 600
MAX_LISTED = 40
FLAG_ORDER = ("no-record", "artifact", "run", "values", "step")
ARTIFACT_SUFFIXES = (".json", ".yaml", ".yml", ".md")
SKIP_NAMES = {"README.md", "brief.md", "LICENSE", "APPROVED", "APPROVED.md", "decisions.jsonl"}
CONTRACT_REQUEST = ("CONTRACT REQUEST: every engine model call sends X-BA-Step-Id (the trace step it belongs to) and "
                    "X-BA-Run-Id, and the gateway log records run_id and the engine purpose (engine_purpose) for "
                    "service principals too; status.json names the runner that resumed the run (runner{id, host, "
                    "resumed_at}, R18).")
R19 = "R19"
R19_DESC = "inference provenance visible in the console (gateway call log joined with the trace)"


# gateway log ----------------------------------------------------------------------------------------------------
def log_paths() -> tuple[str | None, list[Path]]:
    raw = os.environ.get(LOG_ENV) or None
    if not raw:
        return None, []
    p = Path(raw)
    if p.is_file():
        return raw, [p]
    if p.is_dir():
        return raw, sorted(x for x in p.glob("*.jsonl") if x.is_file())
    return raw, []


def load_log() -> dict:
    """Every record of the mounted gateway log(s). Torn or non-object lines are counted, never fatal."""
    raw, paths = log_paths()
    records, bad = [], 0
    for path in paths:
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            bad += 1
            continue
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if not isinstance(rec, dict):
                bad += 1
                continue
            rec = dict(rec)
            rec["_ref"] = f"{path.name}:{i + 1}"
            records.append(rec)
    return {"env": LOG_ENV, "path": raw, "mounted": bool(paths), "files": [p.name for p in paths],
            "n_records": len(records), "bad_lines": bad, "records": records}


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# steps ----------------------------------------------------------------------------------------------------------
def _d(v) -> dict:
    return v if isinstance(v, dict) else {}


def expected_backend(s: dict) -> str | None:
    """The backend a step's own fields say made a model call, or None when nothing in the step shows a call."""
    usage, lp, ver, gate, screen, gen = (_d(s.get(k)) for k in ("usage", "loop", "verify", "gate", "screen",
                                                                   "generated_by"))
    gen_backend = str(gen.get("backend") or "").lower() or None
    if usage and (usage.get("model") or usage.get("backend") or usage.get("input_tokens") or usage.get("output_tokens")):
        return str(usage.get("backend") or gen_backend or "vultr").lower()
    if lp.get("model"):
        return str(usage.get("backend") or gen_backend or "vultr").lower()
    if ver.get("backend") or ver.get("model"):
        return str(ver.get("backend") or gen_backend or "vultr").lower()
    if str(gate.get("decided_by") or "").lower() in LIVE_BACKENDS:
        return str(gate["decided_by"]).lower()
    if screen and str(screen.get("by") or "").lower() == "gateway":
        return "gateway"
    return None


def step_model(s: dict) -> str | None:
    usage, lp, ver = _d(s.get("usage")), _d(s.get("loop")), _d(s.get("verify"))
    return usage.get("model") or lp.get("model") or ver.get("model") or None


def step_purpose(s: dict | None) -> str | None:
    if not s:
        return None
    kind = s.get("kind") or live.step_kind(s)
    if kind == "verify":
        return "verify"
    if kind == "gate":
        return "decide"
    if kind == "loop":
        role = _d(s.get("loop")).get("role")
        return {"critique": "critique", "check": "verify", "decide": "decide"}.get(role, "plan")
    req = _d(s.get("requested"))
    tool = str(req.get("tool") or "").lower()
    if "extract" in tool or s.get("value_ids"):
        return "extract"
    if "vision" in tool or "verify" in tool:
        return "verify"
    return "plan"


def _route_purpose(text: str) -> str | None:
    t = text.lower()
    if not t:
        return None
    if t.startswith("critic") or "critique" in t:
        return "critique"
    if t.startswith("screen"):
        return "screen"
    if t.startswith(("verify", "vision")) or ".verify" in t:
        return "verify"
    if t.startswith("extract") or ".extract" in t or t.startswith("phase5"):
        return "extract"
    if t.startswith(("decide", "decision.")) or ".decide" in t:
        return "decide"
    if "prd" in t or t.startswith(("phase1", "phase2", "phase3", "phase4", "plan")):
        return "plan"
    return None


def call_purpose(rec: dict, step: dict | None) -> tuple[str, str]:
    """(purpose, where it came from). Gateway screen is screen and decision (Jev) is decide; a chat call takes the
    engine purpose when the log carries one, else the matched step's role, else stays 'chat'."""
    gp = str(rec.get("purpose") or "").lower()
    if gp == "screen":
        return "screen", "log purpose"
    if gp == "decision":
        return "decide", "log purpose"
    for k in ("engine_purpose", "route", "tool"):
        p = _route_purpose(str(rec.get(k) or ""))
        if p:
            return p, f"log {k}"
    p = _route_purpose(gp) if gp != "chat" else None
    if p:
        return p, "log purpose"
    sp = step_purpose(step)
    if sp:
        return sp, "step"
    return "chat", "log purpose"


def decided_by(s: dict, calls: list[dict]) -> str:
    reasoning = [c for c in calls if c["purpose"] != "screen"]
    if any(c["upstream"] == "vultr" for c in reasoning):
        return "vultr"
    if any(c["upstream"] == "jev" for c in reasoning):
        return "jev"
    exp = expected_backend(s)
    gen = str(_d(s.get("generated_by")).get("backend") or "").lower()
    if gen in SIMULATED and s.get("mode") not in ("D0", "D1"):
        return gen
    if exp in LIVE_BACKENDS:
        return exp
    if exp == "gateway" and calls:
        return "vultr" if any(c["upstream"] == "vultr" for c in calls) else "jev"
    if gen == "human" or _d(s.get("detail")).get("human"):
        return "human"
    return "code"


def _step_href(case_id: str, run_id: str | None, step_id: str | None) -> str | None:
    if not run_id or not step_id:
        return None
    return f"/cases/{case_id}/runs/{quote(run_id)}#{quote(str(step_id), safe=':@-_.~')}"


# provenance -----------------------------------------------------------------------------------------------------
def _front_matter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        return {}
    try:
        meta = yaml.safe_load(parts[0][3:])
    except yaml.YAMLError:
        return {}
    return meta if isinstance(meta, dict) else {}


def artifact_provenance(a: Artifacts) -> list[dict]:
    """generated_by of every engine-written file in the case package (current versions; revisions/ skipped)."""
    rows = []
    if not a.root.is_dir():
        return rows
    n = 0
    for p in sorted(a.root.rglob("*")):
        if n >= MAX_ARTIFACT_FILES:
            break
        if not p.is_file() or p.suffix not in ARTIFACT_SUFFIXES or p.name in SKIP_NAMES:
            continue
        rel = str(p.relative_to(a.root))
        if "revisions" in p.relative_to(a.root).parts or any(part.startswith(".") for part in p.relative_to(a.root).parts):
            continue
        n += 1
        text = a.text(rel, limit=2_000_000)
        if text is None:
            continue
        meta: dict = {}
        if p.suffix == ".json":
            try:
                obj = json.loads(text)
            except ValueError:
                obj = None
            meta = obj if isinstance(obj, dict) else {}
        elif p.suffix in (".yaml", ".yml"):
            try:
                obj = yaml.safe_load(text)
            except yaml.YAMLError:
                obj = None
            meta = obj if isinstance(obj, dict) else {}
        else:
            meta = _front_matter(text)
        gen = _d(meta.get("generated_by"))
        rows.append({"path": rel, "backend": str(gen.get("backend") or "").lower() or None, "model": gen.get("model"),
                     "at": gen.get("at"), "href": f"/cases/{a.case.id}/files/{quote(rel)}"})
    return rows


def _flag(kind: str, what: str, backend: str | None, detail: str, href: str | None, state: str = "block",
          n: int = 1) -> dict:
    return {"kind": kind, "what": what, "backend": backend or "none", "detail": detail, "href": href,
            "state": state, "n": n}


def provenance_flags(case, a: Artifacts, rid: str | None, steps: list[dict], unmatched_steps: list[dict],
                     artifacts: list[dict]) -> list[dict]:
    flags = []
    cid = case.id
    for r in artifacts:
        if r["backend"] and r["backend"] != "vultr":
            flags.append(_flag("artifact", r["path"], r["backend"],
                               f"generated_by.backend {r['backend']}" + (f" · model {r['model']}" if r["model"] else ""),
                               r["href"]))
    if rid:
        if rid.startswith("mock-"):
            flags.append(_flag("run", rid, "mock", "a mock- run id: written by the recorded double (CONTRACT §7)",
                               f"/cases/{cid}/runs/{quote(rid)}" if rid in a.run_ids() else None))
        status = a.status(rid)
        sgen = str(_d(status.get("generated_by")).get("backend") or "").lower()
        if sgen and sgen != "vultr":
            flags.append(_flag("run", f"runs/{cid}/{rid}/status.json", sgen, f"status.json generated_by.backend {sgen}",
                               f"/cases/{cid}/runs/{quote(rid)}" if rid in a.run_ids() else None))
        gold = hc.gold_run(a, rid)
        if gold is not None:
            mb = str(_d((gold.metrics or {}).get("generated_by")).get("backend") or (gold.metrics or {}).get("inference_backend")
                     or "").lower()
            if mb and mb != "vultr":
                flags.append(_flag("run", f"gold/{cid}/{rid}/metrics.json", mb, f"gold metrics generated_by / inference_backend {mb}",
                                   f"/cases/{cid}/output?run={quote(rid)}"))
            by_backend: dict[str, list] = {}
            for ref in gold.values.values():
                b = str(_d(ref.data.get("generated_by")).get("backend") or "").lower()
                if b and b != "vultr":
                    by_backend.setdefault(b, []).append(ref)
            for b, refs in sorted(by_backend.items()):
                first = refs[0]
                flags.append(_flag("values", f"{len(refs)} gold values", b,
                                   f"generated_by.backend {b} · e.g. {first.value_id} ({first.prop})",
                                   f"/cases/{cid}/lineage/{quote(first.value_id)}?run={quote(rid)}", n=len(refs)))
    # steps whose own provenance is not Vultr (recorded/mock double, jev-only, other)
    for s in steps:
        b = str(_d(s.get("generated_by")).get("backend") or "").lower()
        if b and b not in ("vultr", "human", "code"):
            flags.append(_flag("step", str(s.get("step_id")), b,
                               f"generated_by.backend {b} · P{s.get('phase')} {s.get('mode') or ''} · {_short(s.get('requested'))}",
                               _step_href(cid, rid if rid in a.run_ids() else None, s.get("step_id"))))
    for s in unmatched_steps:
        flags.append(_flag("no-record", str(s.get("step_id")), s["_expected"],
                           f"model step with no gateway record · {_short(s.get('requested'))}",
                           _step_href(cid, rid if rid in a.run_ids() else None, s.get("step_id")), state="block"))
    return flags


def _short(v, n: int = 70) -> str:
    if isinstance(v, dict):
        v = v.get("action") or v.get("tool") or json.dumps(v, ensure_ascii=False)
    text = " ".join(str(v or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


# who acted ------------------------------------------------------------------------------------------------------
def _actor_names(s: dict) -> list[str]:
    gen = _d(s.get("generated_by"))
    out = []
    for v in (s.get("actor"), s.get("by"), gen.get("actor"), gen.get("by"), gen.get("backend")):
        if isinstance(v, dict):
            v = v.get("id") or v.get("name") or v.get("kind")
        if v not in (None, ""):
            out.append(str(v))
    return out


def actors(a: Artifacts, rid: str | None, steps: list[dict]) -> dict:
    status = a.status(rid) if rid else {}
    runner = status.get("runner") or status.get("resumed_by") or status.get("orchestrated_by")
    if isinstance(runner, dict):
        runner_label = " · ".join(str(runner[k]) for k in ("id", "host", "resumed_at") if runner.get(k)) or "engine runner"
    else:
        runner_label = str(runner) if runner else None
    harness, humans_in_run = [], []
    for s in steps:
        names = _actor_names(s)
        odd = [x for x in names if x.lower() not in ENGINE_ACTORS]
        if odd:
            harness.append({"step_id": s.get("step_id"), "actor": ", ".join(odd), "requested": _short(s.get("requested")),
                            "href": _step_href(a.case.id, rid if rid in a.run_ids() else None, s.get("step_id"))})
        elif any(x.lower() == "human" for x in names) or _d(s.get("detail")).get("human"):
            humans_in_run.append({"step_id": s.get("step_id"), "requested": _short(s.get("requested")),
                                  "href": _step_href(a.case.id, rid if rid in a.run_ids() else None, s.get("step_id"))})
    decisions = []
    for d in a.decisions():
        if not isinstance(d, dict):
            continue
        if rid and d.get("run_id") and d.get("run_id") != rid:
            continue
        decisions.append({"who": d.get("approver") or d.get("unverified_name") or "unknown", "when": d.get("ts"),
                          "checkpoint": d.get("checkpoint"), "decision": d.get("decision") or "approve",
                          "identity_source": d.get("identity_source"), "run_id": d.get("run_id"),
                          "phase_dir": d.get("phase_dir")})
    n_engine = sum(1 for s in steps if not any(x.lower() not in ENGINE_ACTORS for x in _actor_names(s)))
    line = (f"0 harness actions inside this run ({len(steps)} steps checked for an actor outside the engine)"
            if steps and not harness else
            f"{len(harness)} harness action{'s' if len(harness) != 1 else ''} inside this run" if harness else
            "No steps to check yet")
    return {"runner": runner_label, "runner_known": bool(runner_label),
            "engine_label": runner_label or "engine runner (the run does not name its runner yet)",
            "n_engine_steps": n_engine, "harness": harness, "n_harness": len(harness), "harness_line": line,
            "humans_in_run": humans_in_run, "decisions": decisions}


# the view-model -------------------------------------------------------------------------------------------------
def _step_ids_everywhere(cases, skip: tuple[str, str] | None = None) -> set[str]:
    ids: set[str] = set()
    for c in cases or []:
        a = Artifacts(c)
        for rid in hc.run_ids(a):
            if skip and (c.id, rid) == skip:
                continue
            ids.update(str(s.get("step_id")) for s in hc.steps_of(a, rid) if s.get("step_id"))
    return ids


def _window(steps: list[dict], status: dict):
    """The run's time window: first step → last step (or status.updated_at, whichever is later)."""
    ts = [t for t in (parse_ts(s.get("ts")) for s in steps) if t is not None]
    upd = parse_ts(status.get("updated_at"))
    if upd is not None:
        ts.append(upd)
    return (min(ts), max(ts)) if ts else (None, None)


ATTRIBUTION = {"step": "step", "window": "time window (no step id)", "run": "run id only (step not in the trace)",
               "unknown-step": "time window (step id matches no step)"}


def _call_row(rec: dict, step: dict | None, case_id: str, run_id: str | None, how: str = "step") -> dict:
    upstream = str(rec.get("upstream") or "").lower() or "unknown"
    purpose, basis = call_purpose(rec, step)
    status = rec.get("status")
    ok = status == 200 or status == "200"
    gate = _d(rec.get("gate"))
    return {"ts": rec.get("ts"), "model": rec.get("model") or "unknown", "upstream": upstream,
            "provider": PROVIDERS.get(upstream, upstream), "phase": step.get("phase") if step else None,
            "phase_name": live.phase_name(step.get("phase")) if step and step.get("phase") else None,
            "step_id": rec.get("step_id"), "step_href": _step_href(case_id, run_id, rec.get("step_id")) if step else None,
            "mode": step.get("mode") if step else None, "purpose": purpose, "purpose_from": basis,
            "input_tokens": int(_num(rec.get("input_tokens")) or 0), "output_tokens": int(_num(rec.get("output_tokens")) or 0),
            "est_tokens": bool(rec.get("est_tokens")), "usd": _num(rec.get("est_usd")),
            "latency_ms": _num(rec.get("latency_ms")), "status": status, "ok": ok,
            "gate_flagged": bool(gate.get("flagged")), "session_id": rec.get("session_id"), "ref": rec.get("_ref"),
            "attributed": step is not None, "attribution": how, "attribution_label": ATTRIBUTION.get(how, how)}


def _pct(n: int, d: int) -> float | None:
    return round(100.0 * n / d, 1) if d else None


def _joins(rec: dict, step: dict) -> bool:
    """Same step id, and the same session when both sides name one (the trace bridge stamps session_id)."""
    rs, ss = rec.get("session_id"), step.get("session_id")
    return not (rs and ss and str(rs) != str(ss))


def model(case, run: str | None = None, all_cases=None) -> dict:
    a = Artifacts(case)
    rid = hc.resolve_run(a, run)
    steps = hc.steps_of(a, rid) if rid else []
    has_feed = bool(rid) and rid in a.run_ids()
    link_run = rid if has_feed else None
    status = a.status(rid) if has_feed else {}
    log = load_log()
    by_step = {str(s.get("step_id")): s for s in steps if s.get("step_id")}
    lo, hi = _window(steps, status)
    cases = list(all_cases or [case])
    if not any(c.id == case.id for c in cases):
        cases.append(case)
    known_elsewhere = _step_ids_everywhere(cases, (case.id, rid or "")) if log["mounted"] and rid else set()
    mine, stray = [], []
    for rec in log["records"]:
        sid = str(rec.get("step_id") or "")
        if sid and sid in by_step and _joins(rec, by_step[sid]):
            mine.append((rec, by_step[sid]))
            continue
        if not rid or (sid and sid in known_elsewhere):
            continue
        if rec.get("run_id"):
            if rec.get("run_id") == rid:
                stray.append((rec, "run"))
            continue
        t = parse_ts(rec.get("ts"))
        if t is not None and lo is not None and lo <= t <= hi:
            stray.append((rec, "unknown-step" if sid else "window"))
    calls_by_step: dict[str, list[dict]] = {}
    rows = []
    for rec, s in mine:
        r = _call_row(rec, s, case.id, link_run)
        rows.append(r)
        calls_by_step.setdefault(str(s.get("step_id")), []).append(r)
    stray_rows = [_call_row(rec, None, case.id, link_run, how) for rec, how in stray]
    all_rows = sorted(rows + stray_rows, key=lambda r: str(r["ts"] or ""))

    model_steps, unmatched = [], []
    deciders: dict[str, int] = {}
    for s in steps:
        exp = expected_backend(s)
        gen = str(_d(s.get("generated_by")).get("backend") or "").lower()
        calls = calls_by_step.get(str(s.get("step_id")), [])
        if exp and gen not in SIMULATED:
            model_steps.append(s)
            if log["mounted"] and not calls:
                unmatched.append({**s, "_expected": exp})
        who = decided_by(s, calls)
        deciders[who] = deciders.get(who, 0) + 1

    reasoning = [r for r in all_rows if r["purpose"] != "screen"]
    n_vultr = sum(1 for r in reasoning if r["upstream"] == "vultr")
    lat = [r["latency_ms"] for r in all_rows if r["latency_ms"] is not None]
    priced = [r["usd"] for r in all_rows if r["usd"] is not None]
    unattributed_n = len(stray_rows) + len(unmatched) if log["mounted"] else None
    artifacts = artifact_provenance(a)
    flags = provenance_flags(case, a, rid, steps, unmatched, artifacts)
    flags.sort(key=lambda f: FLAG_ORDER.index(f["kind"]) if f["kind"] in FLAG_ORDER else 9)
    by_purpose = {p: 0 for p in (*PURPOSES, "chat")}
    for r in all_rows:
        by_purpose[r["purpose"]] = by_purpose.get(r["purpose"], 0) + 1
    by_model: dict[str, dict] = {}
    for r in all_rows:
        m = by_model.setdefault(f"{r['model']} · {r['provider']}", {"name": f"{r['model']} · {r['provider']}", "calls": 0,
                                                                     "usd": 0.0, "input_tokens": 0, "output_tokens": 0})
        m["calls"] += 1
        m["usd"] = round(m["usd"] + (r["usd"] or 0), 8)
        m["input_tokens"] += r["input_tokens"]
        m["output_tokens"] += r["output_tokens"]
    decider_rows = [{"name": k, "steps": deciders.get(k, 0)}
                    for k in ("code", "jev", "vultr", "recorded", "mock", "human", "other") if deciders.get(k)]

    if rid is None:
        empty = gap(None, "Model calls, providers and costs appear once this case has a run.", SOURCES)
    elif not log["mounted"]:
        empty = gap(None, f"The gateway call log is not mounted, so calls, costs and latency cannot be joined yet "
                          f"(gap {R19}: {R19_DESC}). Set {LOG_ENV} to the gateway's calls JSONL (a file or a "
                          f"directory of *.jsonl), read-only. The decisions, flags and who-acted sections below read "
                          f"only the trace and the case package.", LOG_ENV)
    elif not all_rows:
        empty = gap(None, f"The gateway log is mounted ({log['n_records']} records) but none of them names a step of "
                          "this run. " + CONTRACT_REQUEST, LOG_ENV)
    else:
        empty = None
    return {
        "case_id": case.id, "run_id": rid, "runs": hc.run_ids(a), "has_feed": has_feed,
        "state": status.get("state"),
        "log": {k: v for k, v in log.items() if k != "records"},
        "calls": all_rows, "n_calls": len(all_rows), "n_reasoning": len(reasoning), "n_reasoning_vultr": n_vultr,
        "n_screen": len(all_rows) - len(reasoning),
        "pct_vultr": _pct(n_vultr, len(reasoning)),
        "usd_total": round(sum(priced), 8) if priced else None, "n_priced": len(priced),
        "input_tokens": sum(r["input_tokens"] for r in all_rows), "output_tokens": sum(r["output_tokens"] for r in all_rows),
        "latency_ms_median": round(statistics.median(lat), 1) if lat else None,
        "trace_usd": hc.run_step_usd(steps),
        "n_steps": len(steps), "n_model_steps": len(model_steps),
        "deciders": hc.shares(decider_rows, "steps", colours=DECIDER_COLOURS, keep_order=True, limit=8),
        "decider_counts": {r["name"]: r["steps"] for r in decider_rows},
        "by_purpose": [{"name": k, "calls": v} for k, v in by_purpose.items() if v],
        "by_model": sorted(by_model.values(), key=lambda m: -m["calls"]),
        "unattributed": {"n": unattributed_n, "n_records": len(stray_rows), "n_steps": len(unmatched),
                         "n_window": sum(1 for r in stray_rows if r["attribution"] != "run"),
                         "reasons": _unattributed_reasons(stray_rows, unmatched), "records": stray_rows,
                         "steps": [{"step_id": s.get("step_id"), "expected": s["_expected"],
                                    "requested": _short(s.get("requested")), "phase": s.get("phase"),
                                    "href": _step_href(case.id, link_run, s.get("step_id"))} for s in unmatched]},
        "flags": flags[:MAX_LISTED * 3], "n_flags": len(flags),
        "flag_counts": _flag_counts(flags),
        "artifacts": artifacts, "actors": actors(a, rid, steps),
        "purpose_map": PURPOSE_MAP,
        "empty": empty, "contract_request": CONTRACT_REQUEST,
        "backend": backend_of(status.get("metrics"), [*steps, status]) if rid else None,
    }


def _unattributed_reasons(stray_rows: list[dict], unmatched: list[dict]) -> list[str]:
    out = []
    engine = sum(1 for r in stray_rows if str(r.get("session_id") or "") == "engine" and not r.get("step_id"))
    other = len(stray_rows) - engine
    if engine:
        out.append(f"{engine} engine call{'s' if engine != 1 else ''} (session engine) inside this run's time window "
                   "carry no step id: engine calls do not send X-BA-Step-Id or a run id yet (CONTRACT REQUEST to the "
                   "engine owner: send X-BA-Step-Id and the run id on every gateway call).")
    if other:
        out.append(f"{other} gateway record{'s' if other != 1 else ''} name this run or fall in its time window but match "
                   "no step of its trace.")
    if unmatched:
        out.append(f"{len(unmatched)} model step{'s' if len(unmatched) != 1 else ''} in the trace ha"
                   f"{'ve' if len(unmatched) != 1 else 's'} no gateway record (the call went around the gateway, or the "
                   "log is incomplete).")
    return out


def _flag_counts(flags: list[dict]) -> list[dict]:
    out: dict[tuple, int] = {}
    for f in flags:
        key = (f["kind"], f["backend"])
        out[key] = out.get(key, 0) + f["n"]
    return [{"kind": k, "backend": b, "n": n} for (k, b), n in sorted(out.items())]


DECIDER_COLOURS = {"code": "var(--heat-3)", "vultr": "var(--st-run)", "jev": "var(--st-quar)",
                   "recorded": "var(--st-block)", "mock": "var(--st-block)", "human": "var(--st-need)",
                   "other": "var(--st-pause)"}
PURPOSE_MAP = [
    {"purpose": "screen", "from": "gateway purpose screen: Jev + the Vultr content-safety model on page content"},
    {"purpose": "decide", "from": "gateway purpose decision (Jev); a chat call on an action gate or a loop decide step"},
    {"purpose": "plan", "from": "a chat call on a loop propose/gather/revise step or an S1/S2 act; engine purpose phase1-4.*, *prd*"},
    {"purpose": "critique", "from": "a chat call on a loop critique step; engine purpose critic.*"},
    {"purpose": "extract", "from": "a chat call on a step whose tool extracts or that emits value_ids; engine purpose extract.*, phase5.*"},
    {"purpose": "verify", "from": "a chat call on a verify step or a loop check; engine purpose verify*, vision*"},
    {"purpose": "chat", "from": "a chat call that joins no step and names no engine purpose"},
]


def summary(case, run_id: str | None, all_cases=None) -> dict | None:
    """One line for the run and cost pages: 'Inference: N calls · X% on Vultr'. Never raises."""
    try:
        m = model(case, run_id, all_cases)
    except Exception:  # noqa: BLE001 — a link must never break the page it sits on
        return None
    href = f"/cases/{case.id}/{SLUG}" + (f"?run={quote(m['run_id'])}" if m["run_id"] else "")
    if not m["log"]["mounted"]:
        text = f"Inference: gateway log not mounted · {m['n_model_steps']} model steps"
    else:
        pct = f"{m['pct_vultr']:g}% on Vultr" if m["pct_vultr"] is not None else "no reasoning calls"
        text = f"Inference: {m['n_calls']} calls · {pct}"
        if m["unattributed"]["n"]:
            text += f" · {m['unattributed']['n']} unattributed"
    return {"href": href, "text": text, "unattributed": m["unattributed"]["n"], "pct_vultr": m["pct_vultr"],
            "n_calls": m["n_calls"], "n_flags": m["n_flags"], "mounted": m["log"]["mounted"]}


def global_model(settings) -> dict:
    cases = list(settings.cases.values())
    log = load_log()
    known: dict[str, tuple[str, str]] = {}
    rows = []
    for c in cases:
        a = Artifacts(c)
        for rid in hc.run_ids(a):
            for s in hc.steps_of(a, rid):
                if s.get("step_id"):
                    known[str(s["step_id"])] = (c.id, rid)
    for c in cases:
        a = Artifacts(c)
        for rid in hc.run_ids(a):
            try:
                m = model(c, rid, cases)
            except SAFE_ERRORS:
                continue
            rows.append({"case_id": c.id, "title": c.title, "run_id": rid, "n_calls": m["n_calls"],
                         "n_reasoning": m["n_reasoning"], "pct_vultr": m["pct_vultr"], "usd": m["usd_total"],
                         "input_tokens": m["input_tokens"], "output_tokens": m["output_tokens"],
                         "unattributed": m["unattributed"]["n"], "unmatched_steps": m["unattributed"]["n_steps"],
                         "n_window": m["unattributed"]["n_window"], "n_flags": m["n_flags"],
                         "n_harness": m["actors"]["n_harness"], "deciders": m["decider_counts"],
                         "href": f"/cases/{c.id}/{SLUG}?run={quote(rid)}"})
    orphans = []
    for rec in log["records"]:
        sid = str(rec.get("step_id") or "")
        if not sid or sid not in known:
            orphans.append(_call_row(rec, None, "", None) | {"run_id": rec.get("run_id"),
                                                             "why": "no step_id" if not sid else "step_id matches no step"})
    joined = [rec for rec in log["records"] if str(rec.get("step_id") or "") in known]
    reasoning = [r for r in joined if str(r.get("purpose") or "").lower() != "screen"]
    n_vultr = sum(1 for r in reasoning if str(r.get("upstream") or "").lower() == "vultr")
    usd = [x for x in (_num(r.get("est_usd")) for r in log["records"]) if x is not None]
    empty = None
    if not log["mounted"]:
        empty = gap(None, f"The gateway call log is not mounted (gap {R19}: {R19_DESC}). Set {LOG_ENV} to the gateway's "
                          "calls JSONL, read-only; per-run decisions and flags still show on each case's Inference view.",
                    LOG_ENV)
    return {"log": {k: v for k, v in log.items() if k != "records"}, "rows": rows, "n_cases": len(cases),
            "n_calls": log["n_records"], "n_joined": len(joined), "n_reasoning": len(reasoning),
            "n_reasoning_vultr": n_vultr, "pct_vultr": _pct(n_vultr, len(reasoning)),
            "usd_total": round(sum(usd), 8) if usd else None,
            "input_tokens": sum(int(_num(r.get("input_tokens")) or 0) for r in log["records"]),
            "output_tokens": sum(int(_num(r.get("output_tokens")) or 0) for r in log["records"]),
            "orphans": orphans[:200], "n_orphans": len(orphans) if log["mounted"] else None,
            "n_unattributed": (len(orphans) + sum(r["unmatched_steps"] for r in rows)) if log["mounted"] else None,
            "n_flags": sum(r["n_flags"] for r in rows), "n_harness": sum(r["n_harness"] for r in rows),
            "purpose_map": PURPOSE_MAP, "empty": empty, "contract_request": CONTRACT_REQUEST, "sources": SOURCES}


def install(ctx: VizContext) -> None:
    app, render, settings = ctx.app, ctx.render, ctx.settings
    ctx.case_view(KEY, SLUG, LABEL, ORDER)
    ctx.global_view(KEY + "-all", "/inference", LABEL, 8)

    def _all_cases():
        return list(settings.cases.values())

    ctx.env.globals["inference_summary"] = lambda case, run_id=None: summary(case, run_id, _all_cases())

    @app.get(f"/cases/{{case_id}}/{SLUG}", response_class=HTMLResponse)
    def inference_page(request: Request, case_id: str, run: str | None = None):
        case = ctx.get_case(case_id)
        m = model(case, run or None, _all_cases())
        return render(request, "viz/inference.html", nav=KEY, case=case, m=m, backend=m["backend"])

    @app.get(f"/cases/{{case_id}}/api/viz/{SLUG}")
    def inference_api(case_id: str, run: str | None = None) -> dict:
        return model(ctx.get_case(case_id), run or None, _all_cases())

    @app.get("/inference", response_class=HTMLResponse)
    def inference_all(request: Request):
        return render(request, "viz/inference_all.html", nav=KEY + "-all", m=global_model(settings))

    @app.get("/api/viz/inference")
    def inference_all_api() -> dict:
        return global_model(settings)
