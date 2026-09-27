"""Tour · a guided walkthrough for reviewers: the ten acceptance questions (architecture §7a), each with one deep link
into the view that answers it for a chosen case and run, and a readiness chip computed from that run's artifacts; then
the track requirements, each with the view that proves it and the same kind of chip.

It never re-derives what a view shows: readiness comes from each view's own view-model (`model(...)`, `list_model`,
`lineage_model`, `case_items`, `cases_model`) or, where no view-model applies, straight from the artifacts (jobs, step
kinds, approvals, the saved remote check). A probe that fails is shown as empty with its error, never as a 500.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote, urlencode

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import evidence, live
from ..gold import backend_of
from . import compare, cost, definition, discovery, entities, failures, inbox, learning, output, pages, sites
from . import health_common as hc
from .core import SAFE_ERRORS, Artifacts, VizContext, gap
from .operation import decided_by, is_live, last_activity
from .output_common import q
from .output_common import resolve_run as gold_run

ORDER = 5
KEY, HREF, LABEL = "tour", "/tour", "Tour"
SOURCES = "every view's own view-model · runs/<case>/<run>/status.json · trace.live.jsonl · jobs.jsonl · gold/<case>/<run>/ · */APPROVAL_PENDING.md"
STATE_CSS = {"ready": "done", "partial": "pause", "empty": "none"}
STATE_WORDS = {"ready": "ready", "partial": "partial", "empty": "empty"}


# helpers ------------------------------------------------------------------------------------------------------------
def _grade(parts: list[tuple[bool, str]]) -> tuple[str, list[str]]:
    """ready when every part holds, partial when some do, empty when none do; returns the missing parts' labels."""
    have = [ok for ok, _ in parts]
    missing = [label for ok, label in parts if not ok]
    return ("ready" if all(have) else "partial" if any(have) else "empty"), missing


def _url(path: str, **params) -> str:
    qs = {k: v for k, v in params.items() if v}
    return path + (f"?{urlencode(qs)}" if qs else "")


def _probe(fn, *args, **kwargs) -> tuple[dict | None, str | None]:
    try:
        return fn(*args, **kwargs), None
    except HTTPException as exc:
        return None, str(exc.detail)
    except SAFE_ERRORS as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _gap_of(*candidates) -> dict | None:
    """The views' own empty states for the missing parts: the first one naming a gap row, else the first one."""
    found = [g for g in candidates if isinstance(g, dict)]
    return next((g for g in found if g.get("row")), found[0] if found else None)


def pick_case(settings, case_id: str | None):
    """`?case=` (404 when unknown), else the first registered case with a run, else the first case."""
    cases = list(settings.cases.values())
    if case_id:
        case = settings.cases.get(case_id)
        if case is None:
            raise HTTPException(404, f"unknown case {case_id}")
        return case
    return next((c for c in cases if hc.run_ids(Artifacts(c))), cases[0] if cases else None)


def run_rows(a: Artifacts, case_id: str, selected: str | None, now: datetime) -> list[dict]:
    """Every run of the case (live feed and gold export), newest first, marked live by operation's liveness rule."""
    live_ids = set(a.run_ids())
    latest = a.latest_run_id()
    rows = []
    for rid in reversed(hc.run_ids(a)):
        status = a.status(rid) if rid in live_ids else {}
        on = bool(status) and is_live(status, now)
        rows.append({"run_id": rid, "live": on, "kind": "live" if on else "recorded", "latest": rid == latest,
                     "state": status.get("state") or ("gold export only" if rid not in live_ids else "no status yet"),
                     "updated_at": last_activity(status, a.steps(rid)) if status else None,
                     "has_feed": rid in live_ids, "selected": rid == selected,
                     "href": _url("/tour", case=case_id, run=rid)})
    return rows


# the ten questions ------------------------------------------------------------------------------------------------
def _step(n: int, question: str, proves: str, href: str, view: str, hint: str, state: str,
          why: str, gap_row: dict | None, also: list[dict] | None = None) -> dict:
    return {"n": n, "id": f"q{n}", "question": question, "proves": proves, "href": href, "view": view,
            "hint": hint, "state": state, "css": STATE_CSS[state], "state_label": STATE_WORDS[state], "why": why,
            "gap": None if state == "ready" else gap_row, "also": also or []}


def steps_model(settings, domain_of, case, rid: str | None, now: datetime) -> list[dict]:
    a = Artifacts(case)
    base = f"/cases/{case.id}"
    run_q = {"run": rid} if rid else {}
    live_ids = a.run_ids()
    out = []

    # Q1 · the inbox, scoped to this case
    items, err = _probe(inbox.case_items, case, now)
    items = items or []
    need = sum(1 for i in items if i["state"] == "need")
    has_run = any(i["kind"] == "run" for i in items)
    state, _ = _grade([(need > 0 or has_run, "strips"), (has_run, "a run")])
    out.append(_step(1, "What is the engine doing right now, and what needs me?",
                     "One inbox across cases ranks what waits on a person above what is blocked, contained or running.",
                     "/inbox", "Needs you",
                     "Start at the top strip: checkpoints come first, then blocked or contained work, then runs in motion.",
                     state, err or f"{len(items)} strips for this case · {need} waiting on a person",
                     gap(None, "Checkpoints and runs appear once the engine writes APPROVAL_PENDING.md or a run feed.",
                         "*/APPROVAL_PENDING.md · runs/<case>/<run>/status.json"),
                     [{"label": "Operation", "href": _url(f"{base}/operation", **({"run": rid} if rid in live_ids else {}))}]))

    # Q2 · brief → PRD → DoD → ontology
    m, err = _probe(definition.model, case, rid)
    if m:
        state, missing = _grade([(m["prd"]["present"], "PRD"), (not m["dod"]["empty"], "done-when criteria"),
                                 (not m["ontology"]["empty"], "ontology"), (not m["queries"]["empty"], "compiled queries")])
        why = (f"PRD {len(m['prd']['drafts'])} drafts · {len(m['dod']['rows'])} criteria · "
               f"{len(m['ontology']['classes'])} classes"
               + (f" · missing: {', '.join(missing)}" if missing else ""))
        g = _gap_of(m["prd"].get("empty"), m["dod"]["empty"], m["ontology"]["empty"], m["queries"]["empty"])
    else:
        state, why, g = "empty", err, None
    out.append(_step(2, "How was this question turned into a contract?",
                     "The brief becomes a revised PRD whose done-when criteria each cite a basis, then an ontology and "
                     "queries compiled from those criteria.",
                     _url(f"{base}/definition", **run_q), "Definition",
                     "Read the PRD revision diffs, then follow one criterion's basis quote back to the brief.",
                     state, why, g))

    # Q3 · discovery funnel + authority
    m, err = _probe(discovery.model, case, rid)
    if m:
        n_cand, n_plans = len(m["candidates"]["rows"]), len(m["plans"]["rows"])
        state, missing = _grade([(bool(m["funnel"]["providers"]), "funnel"),
                                 (bool(n_cand or m["trace_sources"]), "sources with a decision"),
                                 (n_plans > 0, "plans")])
        why = (f"{len(m['funnel']['providers'])} providers · {n_cand} candidates · {n_plans} plans"
               + (f" · missing: {', '.join(missing)}" if missing else ""))
        g = _gap_of(m["funnel"]["empty"], m["candidates"]["empty"], m["plans"]["empty"])
    else:
        state, why, g = "empty", err, None
    out.append(_step(3, "Where did it look, and why did it trust or reject each source?",
                     "Leads narrow through capture, critic and authority checks; each source keeps the reason it was "
                     "kept or dropped.",
                     _url(f"{base}/discovery", **run_q), "Discovery",
                     "Follow one provider through the funnel, then open a candidate's decision and reason.",
                     state, why, g))

    # Q4 · pages visited + site graphs
    m, err = _probe(pages.model, case, rid)
    sm, _ = _probe(sites.list_model, case, domain_of(case))
    if m:
        n_graphs = (sm or {}).get("n_graphs", 0) or m["n_site_graphs"]
        state, missing = _grade([(m["n_pages"] > 0, "captured pages"), (n_graphs > 0, "site graphs")])
        why = (f"{m['n_pages']} pages · {m['n_images']} with a capture · {n_graphs} site graphs"
               + (f" · missing: {', '.join(missing)}" if missing else ""))
        g = _gap_of(m["empty"], m["graph_empty"], (sm or {}).get("empty"))
    else:
        state, why, g = "empty", err, None
    out.append(_step(4, "Which pages did it visit, and what did it do on each?",
                     "Every visited page keeps its capture, the mode that handled it and the verdict, grouped by source.",
                     _url(f"{base}/pages", **run_q), "Pages",
                     "Filter by a verdict, then open a card's step in the run.",
                     state, why, g, [{"label": "Site graphs", "href": f"{base}/sites"}]))

    # Q5 · one concrete value's lineage
    q5 = _lineage_step(case, a, base, rid)
    out.append(q5)

    # Q6 · DoD progress and gap decisions
    m, err = _probe(output.model, case, rid)
    if m:
        heat_rows = len((m.get("heat") or {}).get("rows") or [])
        state, missing = _grade([(bool(m["dod"]), "DoD progress"), (heat_rows > 0, "completeness heatmap"),
                                 (bool(m["gap_decisions"] or m["reopened"]), "outer-loop decisions")])
        why = (f"DoD {m['dod_met']} of {len(m['dod'])} met · {heat_rows} rows in the heatmap · "
               f"{len(m['gap_decisions'])} gap decisions" + (f" · missing: {', '.join(missing)}" if missing else ""))
        g = _gap_of(m["empty"], m["empty_heat"], m["empty_gap"])
    else:
        state, why, g = "empty", err, None
    out.append(_step(6, "How close is it to done, and what is it doing about the gaps?",
                     "Progress is measured on reconciled output against each done-when criterion, next to the reopen "
                     "decisions that chase the remaining gaps.",
                     _url(f"{base}/output", **run_q), "Output",
                     "Scan the heatmap for empty cells, then read the gap decisions below it.",
                     state, why, g))

    # Q7 · failures and containment
    m, err = _probe(failures.model, case, rid)
    if m:
        state, missing = _grade([(m["n_strips"] > 0, "failures or containment"), (bool(m["cells"]), "sandbox cells")])
        why = (f"{m['n_strips']} strips · {m['contained']} contained · {m['totals']['all_pass']} of "
               f"{m['totals']['jobs']} jobs pass all six checkpoints" + (f" · missing: {', '.join(missing)}" if missing else ""))
        g = _gap_of(m["empty"], m["cells_empty"])
    else:
        state, why, g = "empty", err, None
    out.append(_step(7, "What went wrong, and what was contained?",
                     "Stops, quarantines, limit kills and the six sandbox checkpoints are listed per run, each linked "
                     "to its step.",
                     _url(f"{base}/failures", **run_q), "Failures",
                     "Open a quarantine or limit-kill strip, then read the cell table's six checkpoints.",
                     state, why, g))

    # Q8 · cost
    m, err = _probe(cost.model, case, rid)
    if m:
        state = "ready" if m["has_cost"] else "partial" if m["n_steps"] else "empty"
        usd = f"${m['usd_total']:.4f}" if isinstance(m.get("usd_total"), (int, float)) else "no priced steps"
        why = f"{usd} · {m['n_priced']} priced of {m['n_steps']} steps"
        g = m["empty"]
    else:
        state, why, g = "empty", err, None
    out.append(_step(8, "What did it cost, and where did the money go?",
                     "Spend is split by phase, model, mode and source, and divided by the values it produced.",
                     _url(f"{base}/cost", **run_q), "Cost",
                     "Compare the bars by mode, then read cost per value.",
                     state, why, g))

    # Q9 · learning
    m, err = _probe(learning.model, case, rid)
    if m:
        n_chart = sum(1 for r in m["runs"] if r["n_moded"])
        state, missing = _grade([(m["n_repairs"] > 0, "repairs"),
                                 (bool(m["macros"] or m["crystallizations"]), "promoted macros"),
                                 (n_chart > 1, "a trend over two runs")])
        why = (f"{m['n_repairs']} repairs · {m['n_macro_versions']} macro versions · {n_chart} runs with modes"
               + (f" · missing: {', '.join(missing)}" if missing else ""))
        g = _gap_of(m["empty"], m["repairs_empty"], m["macros_empty"]) or \
            (gap("R11", "A trend needs a second run of the same case.", "runs/<case>/*") if n_chart < 2 else None)
    else:
        state, why, g = "empty", err, None
    out.append(_step(9, "Did it get better or cheaper over time?",
                     "Repairs, promoted macros and the share of code-only steps are tracked across runs.",
                     _url(f"{base}/learning", **run_q), "Learning",
                     "Open a repair attempt to see its stderr, diff and test result.",
                     state, why, g))

    # Q10 · case vs case
    others = [c for c in settings.cases if c != case.id]
    b = others[0] if others else None
    m, err = _probe(compare.cases_model, settings, domain_of, case.id, b)
    if m:
        cols = m["cols"]
        with_output = sum(1 for c in cols if c.get("output"))
        state, missing = _grade([(len(cols) == 2, "a second case"),
                                 (len(cols) == 2 and with_output == 2, "output on both sides")])
        why = (f"{len(cols)} cases side by side · {with_output} with output"
               + (f" · missing: {', '.join(missing)}" if missing else ""))
        g = m["empty"] or (gap(None, "The second case has no gold output yet; its question, PRD and ontology still "
                                     "compare.", "gold/<case>/<run>/entities.jsonl") if state != "ready" else None)
    else:
        state, why, g = "empty", err, None
    out.append(_step(10, "Same engine, different question: how do two cases compare?",
                     "Two briefs run through the same phases side by side: contracts, ontologies and outputs.",
                     _url("/compare", a=case.id, b=b), "Compare",
                     "Read across one row: question, PRD, ontology, output.",
                     state, why, g,
                     [{"label": "Run vs run", "href": f"{base}/compare"}]))
    out.sort(key=lambda s: s["n"])
    return out


def _lineage_step(case, a: Artifacts, base: str, rid: str | None) -> dict:
    question = "Why is this value here?"
    proves = "One value walks back through evidence, step, plan, objective, ontology and PRD to the brief."
    hint = "Open the evidence, then climb the chain to the brief."
    try:
        g, gold_id, note = gold_run(a, rid)
    except HTTPException as exc:
        g, gold_id, note = None, None, str(exc.detail)
    if g is None:
        why = "this run has no gold export yet" if note == "live-only" else (note or "no gold export for this case")
        return _step(5, question, proves, _url(f"{base}/entities", run=rid), "Entities", hint, "empty", why,
                     gap(None, "A value to trace appears once a run publishes gold with evidence.",
                         "gold/<case>/<run>/entities.jsonl"))
    primary = {e["id"] for e in g.primary}
    refs = sorted(g.values.values(), key=lambda r: (
        r.entity_id not in primary, not r.data.get("evidence"), not g.steps_by_value.get(r.value_id), r.value_id))
    ref = refs[0] if refs else None
    if ref is None:
        return _step(5, question, proves, _url(f"{base}/entities", run=gold_id), "Entities", hint, "empty",
                     f"{len(g.entities)} entities but no value carries a value_id",
                     gap("R2", "Values with a value_id and evidence, written by the real execution path.",
                         "gold/<case>/<run>/entities.jsonl"))
    m, err = _probe(entities.lineage_model, case, ref.value_id, gold_id)
    entity = g.entities_by_id.get(ref.entity_id)
    also = [{"label": f"Entity · {g.title(entity)}", "href": f"{base}/entities/{q(ref.entity_id)}?run={q(gold_id)}"}]
    href = f"{base}/lineage/{q(ref.value_id)}?run={q(gold_id)}"
    if m is None:
        return _step(5, question, proves, href, "Lineage", hint, "empty", err, None, also)
    state, missing = _grade([(bool(ref.data.get("evidence")), "evidence"), (m["empty_steps"] is None, "producing step"),
                             (m["empty_plan"] is None, "plan"), (m["empty_objective"] is None, "objective")])
    why = f"value {ref.value_id} · {ref.prop}" + (f" · missing: {', '.join(missing)}" if missing else "")
    return _step(5, question, proves, href, "Lineage", hint, state, why,
                 _gap_of(m["empty_steps"], m["empty_plan"], m["empty_objective"]), also)


# track requirements -----------------------------------------------------------------------------------------------
def _cp_row(jobs: list[dict], keys: tuple[str, ...]) -> tuple[str, str]:
    if not jobs:
        return "empty", "no sandbox jobs in this run"
    ok = sum(1 for j in jobs if all(live.checkpoint_state(j, k) == "pass" for k in keys))
    labels = {k: lbl for k, lbl, _ in live.CHECKPOINTS}
    state = "ready" if ok == len(jobs) else "partial" if ok else "empty"
    return state, f"{ok} of {len(jobs)} jobs pass " + " + ".join(labels[k].lower() for k in keys)


def track_model(case, rid: str | None) -> list[dict]:
    a = Artifacts(case)
    base = f"/cases/{case.id}"
    has_feed = bool(rid) and rid in a.run_ids()
    run_href = f"{base}/runs/{quote(rid)}" if has_feed else _url(f"{base}/operation")
    fail_href = _url(f"{base}/failures", run=rid)
    steps = hc.steps_of(a, rid) if rid else []
    jobs = a.jobs(rid) if has_feed else []
    kinds: dict[str, int] = {}
    for s in steps:
        if s.get("kind"):
            kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
    no_jobs = gap("R3", "One row per sandbox job with its six checkpoints, limits and usage.", "runs/<case>/<run>/jobs.jsonl")
    rows = []

    def row(req: str, proof: str, href: str, view: str, state: str, why: str, g: dict | None) -> None:
        rows.append({"requirement": req, "proof": proof, "href": href, "view": view, "state": state,
                     "css": STATE_CSS[state], "state_label": STATE_WORDS[state], "why": why,
                     "gap": None if state == "ready" else g})

    # inference on Vultr: who decided each step
    deciders: dict[str, int] = {}
    for s in steps:
        who = decided_by(s)
        deciders[who] = deciders.get(who, 0) + 1
    status = a.status(rid) if has_feed else {}
    backend = backend_of(status.get("metrics"), [*steps, status]) if rid else None
    n_vultr = deciders.get("vultr", 0)
    state = "ready" if n_vultr and backend != "recorded" else "partial" if n_vultr or deciders.get("recorded") else "empty"
    row("Agent LLM calls via Vultr Serverless Inference",
        "Steps decided by a Vultr model, per the trace's generated_by / usage backend", _url(f"{base}/cost", run=rid), "Cost",
        state, f"{n_vultr} Vultr-decided steps of {len(steps)}" + (" · run uses recorded (simulated) inference" if backend == "recorded" else ""),
        gap(None, "Model steps carrying generated_by.backend or usage.backend.", "trace.live.jsonl"))

    state, why = _cp_row(jobs, ("host", "where", "isolation"))
    row("Sandboxes never in the app process; process isolation",
        "Host check (runtime), hostname/uname from inside the pod, isolation probe BLOCKED", fail_href, "Failures",
        state, why, no_jobs)
    state, why = _cp_row(jobs, ("secrets",))
    row("No API keys or credentials inside the sandbox",
        "Secrets checkpoint: 0 keys in the pod; metadata IP and mesh BLOCKED", fail_href, "Failures", state, why, no_jobs)
    with_limits = sum(1 for j in jobs if j.get("limits"))
    state = "empty" if not jobs else "ready" if with_limits == len(jobs) else "partial" if with_limits else "empty"
    row("Resource limits: time and memory caps on every run",
        "Caps recorded per job in jobs.jsonl", fail_href, "Failures", state,
        f"{with_limits} of {len(jobs)} jobs record their caps" if jobs else "no sandbox jobs in this run", no_jobs)
    state, why = _cp_row(jobs, ("teardown",))
    row("Lifecycle discipline: destroy after each task", "Teardown checkpoint: pod gone, no sandboxes left",
        fail_href, "Failures", state, why, no_jobs)

    n_verify = kinds.get("verify", 0)
    row("Vision model verifies each browser step", "Steps of kind verify with a verdict on the post-action screenshot",
        run_href, "Run", "ready" if n_verify else "empty", f"{n_verify} verify steps",
        gap("R3", "Vision verdicts per browser step.", "trace.live.jsonl event verify"))

    n_gate = kinds.get("gate", 0)
    actions = [x for x in a.approvals() if x.checkpoint == "action"]
    state, _ = _grade([(n_gate > 0, "gate"), (bool(actions), "approval")])
    row("Human approves anything final (approve-before-submit)",
        "An action_gate step paused the run and an action checkpoint waits for or holds a person's decision",
        f"{base}/approvals", "Approvals", state, f"{n_gate} gate steps · {len(actions)} action checkpoints",
        gap("R3", "Action gates on the engine path.", "trace.live.jsonl event action_gate · 05-actions/*/APPROVAL_PENDING.md"))

    n_repair = kinds.get("repair", 0)
    row("On error, stderr is fed back for a retry (Pattern A)", "Repair attempts with stderr, diff and test result",
        _url(f"{base}/learning", run=rid), "Learning", "ready" if n_repair else "empty", f"{n_repair} repair attempts",
        gap("R4", "Extractor repairs inside runs.", "trace.live.jsonl event repair"))

    n_quar = kinds.get("quarantine", 0)
    row("Containment: a hostile page is flagged and BLOCKED", "Quarantine steps: page withheld from planning, kept as evidence",
        fail_href, "Failures", "ready" if n_quar else "empty", f"{n_quar} quarantined pages",
        gap("R8", "A hostile page quarantined on a real run.", "trace.live.jsonl event quarantine"))

    n_kill = kinds.get("kill", 0) + sum(1 for j in jobs if j.get("killed_by"))
    row("Containment: rm -rf or an endless loop is killed by the caps", "Limit kills: job stopped, host untouched",
        fail_href, "Failures", "ready" if n_kill else "empty", f"{n_kill} limit kills",
        gap("R8", "A limit kill on a real run.", "trace.live.jsonl event limit_kill · jobs.jsonl killed_by"))

    v = evidence.load_verify()
    ports = v.ports
    fails = [t for k, t in ports if k == "FAIL"]
    state = "empty" if not ports else "partial" if fails else "ready"
    row("Zero inbound ports; public URL only through the reverse proxy",
        "Saved outside-in remote check (verify-remote.txt): VM ports closed", "/evidence", "Evidence", state,
        (f"{len(ports) - len(fails)} of {len(ports)} port checks pass · saved {v.saved_at}" if ports
         else "no saved remote check in ONTOFILL_CONSOLE_EVIDENCE_DIR"),
        gap(None, "The remote verification output, saved as verify-remote.txt.", "ONTOFILL_CONSOLE_EVIDENCE_DIR/verify-remote.txt"))
    return rows


# the page ---------------------------------------------------------------------------------------------------------
def model(settings, domain_of, case_id: str | None = None, run: str | None = None) -> dict:
    now = datetime.now(UTC)
    case = pick_case(settings, case_id or None)
    cases = [{"id": c.id, "title": c.title, "has_run": bool(hc.run_ids(Artifacts(c))),
              "selected": case is not None and c.id == case.id} for c in settings.cases.values()]
    if case is None:
        return {"case_id": None, "title": None, "question": None, "cases": cases, "run_id": None, "runs": [],
                "run_live": False, "steps": [], "track": [], "counts": {}, "start_href": None, "backend": None,
                "sources": SOURCES,
                "empty": gap(None, "The tour walks one registered case through the ten questions.", "ONTOFILL_CONSOLE_CASES")}
    a = Artifacts(case)
    ids = hc.run_ids(a)
    if run and run not in ids:
        raise HTTPException(404, f"no run {run} in case {case.id}")
    rid = run or hc.resolve_run(a, None)
    runs = run_rows(a, case.id, rid, now)
    steps = steps_model(settings, domain_of, case, rid, now)
    track = track_model(case, rid)
    counts = {k: sum(1 for s in steps if s["state"] == k) for k in STATE_CSS}
    tcounts = {k: sum(1 for r in track if r["state"] == k) for k in STATE_CSS}
    sel = next((r for r in runs if r["selected"]), None)
    status = a.status(rid) if rid in a.run_ids() else {}
    return {"case_id": case.id, "title": case.title, "question": case.brief, "cases": cases, "run_id": rid,
            "runs": runs, "run_live": bool(sel and sel["live"]), "steps": steps, "track": track,
            "counts": counts, "track_counts": tcounts, "start_href": steps[0]["href"] if steps else None,
            "backend": backend_of(status.get("metrics"), [status]) if status else None, "sources": SOURCES,
            "empty": None if rid else gap(None, f"Case {case.id} has no run yet, so most steps below are empty; the "
                                                "definition steps still work from the case package.",
                                          "runs/<case>/<run>/ · gold/<case>/<run>/")}


def install(ctx: VizContext) -> None:
    app, render, settings = ctx.app, ctx.render, ctx.settings
    ctx.global_view(KEY, HREF, LABEL, ORDER)

    @app.get(HREF, response_class=HTMLResponse)
    def tour(request: Request, case: str | None = None, run: str | None = None):
        m = model(settings, ctx.case_domain, case or None, run or None)
        chosen = settings.cases.get(m["case_id"]) if m["case_id"] else None
        return render(request, "viz/tour.html", nav=KEY, m=m, backend=m["backend"],
                      synthetic=bool(chosen and chosen.synthetic))

    @app.get("/api/viz/tour")
    def tour_api(case: str | None = None, run: str | None = None) -> dict:
        return model(settings, ctx.case_domain, case or None, run or None)
