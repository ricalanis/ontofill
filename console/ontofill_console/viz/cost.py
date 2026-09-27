"""Q8 · What did it cost, and where did the money go? Spend by phase, model, mode and source, cost per gold value and
run budget burn, from per-step `usage{model, backend, input_tokens, output_tokens, est_usd}` (CONTRACT v1.0.5),
`metrics.loops[].usd`, `metrics.decisions_by_backend` and, read-only, the spend tracker's history
(ONTOFILL_CONSOLE_SPEND_HISTORY, the same file /spend reads). When no step carries a price, it shows counts and says so.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

from .. import live
from ..gold import backend_of
from . import health_common as hc
from .core import Artifacts, VizContext, gap

ORDER = 70
CONTRACT_REQUEST = (
    "CONTRACT REQUEST: every model call's trace step carries usage{model, backend, input_tokens, "
    "output_tokens, est_usd} (v1.0.5 trace bridge), and status.json carries the run's budget_usd."
)


def _budget(status: dict, metrics: dict) -> float | None:
    for src in (status, metrics, status.get("budget") if isinstance(status.get("budget"), dict) else {}):
        for k in ("budget_usd", "usd"):
            v = (src or {}).get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                return float(v)
    return None


def _group(steps: list[dict], keyf) -> list[dict]:
    rows: dict[str, dict] = {}
    for s in steps:
        name = keyf(s)
        r = rows.setdefault(
            name,
            {
                "name": name,
                "usd": 0.0,
                "priced": 0,
                "unpriced": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "calls": 0,
                "steps": 0,
            },
        )
        r["steps"] += 1
        u = hc.usage_of(s)
        if u is None:
            continue
        r["calls"] += 1
        r["input_tokens"] += int(u.get("input_tokens") or 0)
        r["output_tokens"] += int(u.get("output_tokens") or 0)
        usd = hc.usd_of(s)
        if usd is None:
            r["unpriced"] += 1
        else:
            r["priced"] += 1
            r["usd"] += usd
    for r in rows.values():
        r["usd"] = round(r["usd"], 6)
    return list(rows.values())


def _model_name(s: dict, priced_trace: bool = False) -> str:
    if priced_trace and hc.usage_of(s) is None:
        return "no model call"  # CONTRACT v1.0.5: model calls carry usage; generated_by is the run's provenance
    u = hc.usage_of(s) or {}
    gen = s.get("generated_by") if isinstance(s.get("generated_by"), dict) else {}
    lp = s.get("loop") if isinstance(s.get("loop"), dict) else {}
    return str(u.get("model") or lp.get("model") or gen.get("model") or "unknown")


def _backend_name(s: dict) -> str:
    u = hc.usage_of(s) or {}
    gen = s.get("generated_by") if isinstance(s.get("generated_by"), dict) else {}
    return str(u.get("backend") or gen.get("backend") or "unknown")


def model(case, run: str | None = None) -> dict:
    a = Artifacts(case)
    rid = hc.resolve_run(a, run)
    steps = hc.steps_of(a, rid)
    status = a.status(rid)
    metrics = hc.metrics_of(a, rid)
    priced = [s for s in steps if hc.usd_of(s) is not None]
    with_usage = [s for s in steps if hc.usage_of(s) is not None]
    total = round(sum(hc.usd_of(s) for s in priced), 6) if priced else None
    has_cost = bool(priced)

    dims = {
        "phase": _group(
            steps, lambda s: f"P{s.get('phase')} · {live.phase_name(s.get('phase'))}" if s.get("phase") else "no phase"
        ),
        "model": _group(steps, lambda s: _model_name(s, bool(with_usage))),
        "mode": _group(steps, lambda s: s.get("mode") or "no mode"),
        "source": _group(steps, lambda s: s.get("source_id") or "no source (case-level steps)"),
        "backend": _group(steps, _backend_name),
    }
    # Engine-reported loop cost per phase (metrics.loops[].usd), shown when the trace has no priced steps.
    loops = [
        {
            "name": (
                "Gap loop" if r.get("phase") == "outer" else f"P{r.get('phase')} · {live.phase_name(r.get('phase'))}"
            ),
            "usd": float(r["usd"]),
            "iterations": r.get("iterations"),
        }
        for r in metrics.get("loops") or []
        if isinstance(r, dict) and isinstance(r.get("usd"), (int, float))
    ]
    loops_total = round(sum(r["usd"] for r in loops), 6) if loops else None

    bars = []
    for key, label in (
        ("phase", "By phase"),
        ("model", "By model"),
        ("mode", "By execution mode"),
        ("source", "By source"),
    ):
        rows = dims[key]
        colours = hc.MODE_COLOURS if key == "mode" else None
        if key == "mode":
            rows = sorted(rows, key=lambda r: hc.MODES.index(r["name"]) if r["name"] in hc.MODES else 9)
        if has_cost:
            bars.append(
                {
                    "key": key,
                    "label": label,
                    "metric": "usd",
                    "rows": hc.shares(rows, "usd", colours=colours, keep_order=key == "mode"),
                    "table": sorted(rows, key=lambda r: -r["usd"]),
                }
            )
        else:
            bars.append(
                {
                    "key": key,
                    "label": label,
                    "metric": "steps",
                    "rows": hc.shares(rows, "steps", colours=colours, keep_order=key == "mode"),
                    "table": sorted(rows, key=lambda r: -r["steps"]),
                }
            )

    # Output: gold values of this run (exact export), else values emitted in the trace.
    gold = hc.gold_run(a, rid)
    if gold is not None:
        n_values = sum(1 for v in gold.values.values() if v.data.get("status") == "gold")
        values_from = "gold export"
    else:
        n_values = len({vid for s in steps for vid in s.get("value_ids") or []})
        values_from = "value_ids emitted in the trace"
    primary = (
        (metrics.get("entities_meeting_dod") or {}) if isinstance(metrics.get("entities_meeting_dod"), dict) else {}
    )
    meeting = sum(v for v in primary.values() if isinstance(v, (int, float))) if primary else None

    budget = _budget(status, metrics)
    spent = total if total is not None else loops_total
    snap = hc.spend_history_latest()
    eng = (snap or {}).get("engine") if isinstance((snap or {}).get("engine"), dict) else {}
    tracker_run = (eng.get("runs") or {}).get(rid) if isinstance(eng.get("runs"), dict) and rid else None

    decisions = metrics.get("decisions_by_backend") if isinstance(metrics.get("decisions_by_backend"), dict) else {}
    backend_rows = [{"name": b, "calls": int(n)} for b, n in decisions.items() if isinstance(n, (int, float))]
    step_models = [{"name": r["name"], "steps": r["steps"]} for r in dims["model"]]

    if rid is None:
        empty = gap(
            None,
            "Spend by phase, model, mode and source for this case's runs, and cost per gold value.",
            "trace usage, metrics.loops, spend history",
        )
    elif not has_cost:
        empty = gap(
            None,
            "Cost per call isn't recorded yet: no step in this run carries usage.est_usd, so the bars below "
            "count steps instead of dollars. " + CONTRACT_REQUEST,
            "trace.live.jsonl usage",
        )
    else:
        empty = None
    return {
        "case_id": case.id,
        "run_id": rid,
        "runs": hc.run_ids(a),
        "state": status.get("state"),
        "n_steps": len(steps),
        "has_cost": has_cost,
        "usd_total": total,
        "n_priced": len(priced),
        "n_usage": len(with_usage),
        "n_unpriced": len(with_usage) - len(priced),
        "input_tokens": sum(int((hc.usage_of(s) or {}).get("input_tokens") or 0) for s in with_usage),
        "output_tokens": sum(int((hc.usage_of(s) or {}).get("output_tokens") or 0) for s in with_usage),
        "bars": bars,
        "loops": loops,
        "loops_total": loops_total,
        "n_values": n_values,
        "values_from": values_from,
        "entities_meeting_dod": meeting,
        "per_value": spent / n_values if spent is not None and n_values else None,
        "per_entity": spent / meeting if spent is not None and meeting else None,
        "spent_basis": "priced trace steps"
        if total is not None
        else ("metrics.loops" if loops_total is not None else None),
        "budget_usd": budget,
        "burn": round(spent / budget, 4) if budget and spent is not None else None,
        "tracker_run_usd": tracker_run,
        "tracker_ts": (snap or {}).get("ts"),
        "tracker_reported": bool(eng.get("reported")),
        "decisions_by_backend": hc.shares(backend_rows, "calls"),
        "inference_backend": metrics.get("inference_backend"),
        "steps_by_model": hc.shares(step_models, "steps"),
        "empty": empty,
        "contract_request": None if has_cost and budget else CONTRACT_REQUEST,
        "backend": backend_of(status.get("metrics"), [*steps, status]) if rid else None,
    }


def install(ctx: VizContext) -> None:
    app, render = ctx.app, ctx.render
    ctx.case_view("cost", "cost", "Cost", ORDER)

    @app.get("/cases/{case_id}/cost", response_class=HTMLResponse)
    def cost_page(request: Request, case_id: str, run: str | None = None):
        case = ctx.get_case(case_id)
        m = model(case, run)
        return render(request, "viz/cost.html", nav="cost", case=case, m=m, backend=m["backend"])

    @app.get("/cases/{case_id}/api/viz/cost")
    def cost_api(case_id: str, run: str | None = None) -> dict:
        return model(ctx.get_case(case_id), run)
