"""Q9 · Did it get better or cheaper over time? Repair attempts (Pattern A) grouped by source, promoted macro versions in
`05-macros/<source>/v*/`, crystallization events, and the share of code-only steps (D0/D1) per run across every run
of the case.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse

from ..gold import backend_of
from . import health_common as hc
from .core import Artifacts, VizContext, gap

ORDER = 80
DATE_KEYS = ("promoted_at", "created_at", "at", "date", "ts")


def _vnum(name: str) -> tuple:
    m = re.match(r"v(\d+)(?:\.(\d+))?(?:\.(\d+))?", name)
    return tuple(int(x or 0) for x in m.groups()) if m else (10**9,)


def macros(a: Artifacts) -> list[dict]:
    """Promoted macro versions: 05-macros/<source>/v<N>/... files, with a date from a manifest when present."""
    by_source: dict[str, dict[str, list[str]]] = {}
    for rel in a.glob("05-macros/*/v*/**/*"):
        parts = Path(rel).parts
        if len(parts) < 4:
            continue
        by_source.setdefault(parts[1], {}).setdefault(parts[2], []).append("/".join(parts[3:]))
    out = []
    for source in sorted(by_source):
        versions = []
        for ver in sorted(by_source[source], key=_vnum):
            files = sorted(by_source[source][ver])
            date, date_from, test = None, None, None
            for f in files:
                if not f.endswith(".json"):
                    continue
                doc = a.json(f"05-macros/{source}/{ver}/{f}")
                if not isinstance(doc, dict):
                    continue
                for k in DATE_KEYS:
                    if doc.get(k):
                        date, date_from = str(doc[k]), f
                        break
                gen = doc.get("generated_by") if isinstance(doc.get("generated_by"), dict) else {}
                if not date and gen.get("at"):
                    date, date_from = str(gen["at"]), f
                t = doc.get("test") or doc.get("replay") or doc.get("tests")
                if isinstance(t, dict) and test is None:
                    test = ", ".join(f"{k} {v}" for k, v in t.items() if isinstance(v, (int, float, str)))
                if date:
                    break
            if not date:  # fall back to the newest file's modification time, labelled as such
                try:
                    newest = max((a.root / "05-macros" / source / ver / f).stat().st_mtime for f in files)
                    date, date_from = datetime.fromtimestamp(newest, UTC).isoformat(timespec="seconds"), "file time"
                except (OSError, ValueError):
                    pass
            versions.append({"version": ver, "files": files, "n_files": len(files), "date": date,
                             "date_from": date_from, "test": test,
                             "href": f"/cases/{a.case.id}/files/05-macros/{source}/{ver}/{files[0]}" if files else None})
        out.append({"source_id": source, "versions": versions, "n_versions": len(versions)})
    return out


def chart(points: list[dict], width: int = 560, height: int = 180) -> dict:
    """Plain-SVG geometry for the code-only share per run (0–100 %, runs in start order)."""
    left, right, top, bottom = 44, 16, 14, 34
    pw, ph = width - left - right, height - top - bottom
    pts = [p for p in points if p["share"] is not None]
    n = len(points)
    out = []
    for i, p in enumerate(points):
        if p["share"] is None:
            continue
        inset = 28  # keep the first and last point clear of the axis labels
        x = left + inset + ((pw - 2 * inset) * (i / (n - 1)) if n > 1 else (pw - 2 * inset) / 2)
        y = top + ph * (1 - p["share"])
        out.append({**p, "x": round(x, 1), "y": round(y, 1), "pct": round(p["share"] * 100, 1),
                    "label_y": round(y - 7 if y > top + 14 else y + 16, 1)})
    return {"width": width, "height": height, "left": left, "top": top, "right": width - right,
            "bottom": height - bottom, "points": out, "n": len(pts),
            "path": " ".join(f"{'M' if i == 0 else 'L'}{q['x']},{q['y']}" for i, q in enumerate(out)),
            "yticks": [{"v": v, "y": round(top + ph * (1 - v / 100), 1)} for v in (0, 25, 50, 75, 100)]}


def model(case, run: str | None = None) -> dict:
    a = Artifacts(case)
    current = hc.resolve_run(a, run)
    base = f"/cases/{case.id}"
    repairs, crystal, runs = [], [], []
    last_steps: list[dict] = []
    last_status: dict = {}
    for rid in hc.run_ids(a):
        steps = hc.steps_of(a, rid)
        counts = hc.mode_counts(steps, hc.metrics_of(a, rid))
        runs.append({"run_id": rid, "started": hc.started(a, rid, steps), "modes": counts,
                     "n_moded": sum(counts.values()), "share": hc.code_only_share(counts),
                     "usd": hc.run_step_usd(steps), "href": f"{base}/runs/{rid}", "current": rid == current})
        for s in steps:
            if s.get("kind") == "repair":
                d = s.get("detail") or {}
                test = d.get("test") if isinstance(d.get("test"), dict) else {}
                repairs.append({"run_id": rid, "step_id": s.get("step_id"), "source_id": s.get("source_id") or "no source",
                                "attempt": d.get("attempt"), "max_attempts": d.get("max_attempts"),
                                "result": d.get("result"), "stderr": d.get("stderr"),
                                "diff_key": d.get("diff_key"), "code_key": d.get("code_key"),
                                "precision": test.get("precision"), "coverage": test.get("coverage"),
                                "pages": test.get("pages"), "ts": s.get("ts"),
                                "detail": " · ".join(str(x) for x in (
                                    d.get("stderr") and f"stderr: {d['stderr']}",
                                    test.get("precision") is not None and f"precision {test['precision']}",
                                    test.get("coverage") is not None and f"coverage {test['coverage']}",
                                    test.get("pages") is not None and f"pages {test['pages']}",
                                    d.get("diff_key") and f"diff {str(d['diff_key'])[:19]}…",
                                    d.get("code_key") and f"code {str(d['code_key'])[:19]}…") if x) or None,
                                "state": "done" if str(d.get("result")).lower() in ("pass", "passed", "ok") else "block",
                                "href": f"{base}/runs/{rid}#{s.get('step_id')}"})
            elif s.get("event") == "crystallization":
                crystal.append({"run_id": rid, "step_id": s.get("step_id"), "source_id": s.get("source_id"),
                                "what": hc.text_of(s.get("executed")) or hc.text_of(s.get("requested")),
                                "ts": s.get("ts"), "href": f"{base}/runs/{rid}#{s.get('step_id')}"})
        last_steps, last_status = steps, a.status(rid)
    runs.sort(key=lambda r: (r["started"], r["run_id"]))
    groups: dict[str, list[dict]] = {}
    for r in repairs:
        groups.setdefault(r["source_id"], []).append(r)
    repair_groups = [{"source_id": sid, "attempts": rs, "n_attempts": len(rs),
                      "passed": sum(1 for r in rs if r["state"] == "done")} for sid, rs in sorted(groups.items())]
    mac = macros(a)
    chart_runs = [r for r in runs if r["n_moded"]]
    empty_all = not (repairs or crystal or mac or chart_runs)
    return {
        "case_id": case.id, "run_id": current, "runs": runs, "n_runs": len(runs), "chart": chart(chart_runs),
        "repair_groups": repair_groups, "n_repairs": len(repairs),
        "n_repairs_passed": sum(1 for r in repairs if r["state"] == "done"),
        "crystallizations": crystal, "macros": mac, "n_macro_versions": sum(m["n_versions"] for m in mac),
        "empty": gap("R4", "Repair attempts, promoted macro versions and the share of code-only steps over runs.",
                     "trace (event repair, crystallization) · case 05-macros/<source>/v*/") if empty_all else None,
        "repairs_empty": None if repairs else gap("R4", "Pattern A repair attempts: stderr, diff, replay test precision "
                                                        "and coverage, per source.", "trace event repair"),
        "macros_empty": None if mac else gap("R4", "Promoted macro versions, one folder per source and version.",
                                             "case 05-macros/<source>/v*/"),
        "trend_note": None if len(chart_runs) > 1 else
        f"{len(chart_runs)} run with mode counts: a trend needs at least two runs." if chart_runs else None,
        "backend": backend_of((last_status or {}).get("metrics"), [*last_steps, last_status or {}]) if runs else None,
    }


def install(ctx: VizContext) -> None:
    app, render = ctx.app, ctx.render
    ctx.case_view("learning", "learning", "Learning", ORDER)

    @app.get("/cases/{case_id}/learning", response_class=HTMLResponse)
    def learning_page(request: Request, case_id: str, run: str | None = None):
        case = ctx.get_case(case_id)
        m = model(case, run)
        return render(request, "viz/learning.html", nav="learning", case=case, m=m, backend=m["backend"])

    @app.get("/cases/{case_id}/api/viz/learning")
    def learning_api(case_id: str, run: str | None = None) -> dict:
        return model(ctx.get_case(case_id), run)

