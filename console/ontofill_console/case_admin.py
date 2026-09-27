"""Case CRUD (CONTRACT v1.0.6): create a case from its question, read its status, edit it before any run, revise the
question as a new version after, archive and restore. Every write is an approver action under the console's identity
rules (the same `authorize` and origin check as approvals) and is logged in the case's decisions.jsonl."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import approvals as ap
from . import registry, runner_state

STATUS_STATE = {
    "not started": "none",
    "running": "run",
    "waiting on you": "need",
    "done": "done",
    "archived": "pause",
    "stopped": "block",
}


@dataclass
class AdminContext:
    app: Any
    render: Callable
    get_case: Callable
    settings: Any
    authorize: Callable
    check_origin: Callable
    read_form: Callable
    live_run_id: Callable
    env: Any


def run_ids(case, runner_root=None) -> list[str]:
    """Runs of the case, plus a marker when the runner already has it (a start was requested or it knows a run):
    from that moment the brief and budget are bound, even before the engine writes its first folder."""
    try:
        ids = list(case.store.live_run_ids())
    except (LookupError, OSError, ValueError, AttributeError):
        ids = []
    if runner_root is not None and runner_root.is_dir():
        st, ctl = runner_state.status(runner_root, case.id), runner_state.control(runner_root, case.id)
        if st.get("run_id") or ctl.get("start_requested") or st.get("state") not in (None, "idle"):
            ids.append(st.get("run_id") or "runner:start-requested")
    return ids


def case_status(case, settings) -> dict:
    """not started / running / waiting on you / done / stopped (why) / archived, from the runner and the run feed."""
    if case.meta and case.meta.get("archived"):
        return {
            "label": "archived",
            "state": "pause",
            "detail": case.meta.get("superseded_by") and f"revised as {case.meta['superseded_by']}",
        }
    pending = sum(1 for a in ap.approvals(case.dir) if a.approved is None)
    runner = runner_state.status(settings.runner_state, case.id) if settings.runner_state.is_dir() else {}
    rstate = runner.get("state")
    try:
        rid = case.store.live_run_id()
        live = (case.store.live_status(rid) or {}) if rid else {}
    except (LookupError, OSError, ValueError, AttributeError):
        rid, live = None, {}
    if pending or rstate == "waiting_approval":
        return {
            "label": "waiting on you",
            "state": "need",
            "detail": f"{pending} checkpoint(s) waiting" if pending else None,
        }
    if rstate == "running" or live.get("state") == "running":
        return {"label": "running", "state": "run", "detail": rid}
    if rstate in ("killed", "budget_stop", "failed", "paused"):
        return {
            "label": "stopped",
            "state": "block" if rstate != "paused" else "pause",
            "detail": (runner.get("reason") or rstate.replace("_", " ")),
        }
    if rstate == "done" or live.get("state") == "done":
        return {"label": "done", "state": "done", "detail": rid}
    if not rid and not registry.has_run(case.dir.root):
        return {"label": "not started", "state": "none", "detail": None}
    return {"label": (live.get("state") or rstate or "idle"), "state": "pause", "detail": rid}


def _by(who: str, source: str, extra: dict | None) -> dict:
    return {
        "approver": who,
        "identity_source": source,
        **({"unverified_name": extra["unverified_name"]} if extra and extra.get("unverified_name") else {}),
    }


def install(ctx: AdminContext) -> None:
    app, render, settings = ctx.app, ctx.render, ctx.settings
    ctx.env.globals["case_status"] = lambda case: case_status(case, settings)
    ctx.env.globals["registry_enabled"] = lambda: settings.cases_root is not None

    def root_or_503():
        if settings.cases_root is None:
            raise HTTPException(503, "the case registry is not configured (ONTOFILL_CASES_ROOT)")
        return settings.cases_root

    def form_page(request, error=None, form=None, status_code=200):
        r = render(
            request,
            "case_new.html",
            nav="cases",
            error=error,
            form=form or {},
            max_budget=registry.max_budget(),
            default_budget=registry.DEFAULT_BUDGET,
            has_template=bool(settings.cases_root and (settings.cases_root / registry.TEMPLATE).is_file()),
            registry_on=settings.cases_root is not None,
        )
        r.status_code = status_code
        return r

    @app.get("/cases/new", response_class=HTMLResponse)
    def new_case(request: Request):
        return form_page(request)

    @app.post("/cases")
    async def create_case(request: Request):
        root = root_or_503()
        ctx.check_origin(request)
        form = await ctx.read_form(request)
        who, source, extra = ctx.authorize(request, form)
        try:
            item = registry.create(
                root,
                title=form.get("title", ""),
                question=form.get("question", ""),
                notes=form.get("notes"),
                budget_usd=form.get("budget_usd"),
                lake=form.get("lake", "default"),
                to_phase=form.get("to_phase"),
                created_by=_by(who, source, extra),
            )
        except registry.RegistryError as exc:
            return form_page(request, str(exc), form, exc.status)
        settings._registry_stamp = None  # re-read now
        from .web import sync_registry

        sync_registry(settings)
        if form.get("start_now") in ("1", "on", "true"):
            runner_state.apply_action(settings.runner_state, item["id"], "start", who, item.get("to_phase"))
            registry.audit(
                registry.resolve(root, item["path"]),
                {
                    "ts": runner_state.now_iso(),
                    "case_id": item["id"],
                    "checkpoint": "runner",
                    "decision": "start",
                    **_by(who, source, extra),
                    "to_phase": item.get("to_phase"),
                },
            )
        return RedirectResponse(f"/cases/{item['id']}?created=1", status_code=303)

    def manage_page(request, case, error=None, status_code=200):
        r = render(
            request,
            "case_manage.html",
            nav="overview",
            case=case,
            meta=case.meta or {},
            error=error,
            started=registry.has_run(case.dir.root, run_ids(case, settings.runner_state)),
            status=case_status(case, settings),
            max_budget=registry.max_budget(),
            registry_on=settings.cases_root is not None,
        )
        r.status_code = status_code
        return r

    @app.get("/cases/{case_id}/manage", response_class=HTMLResponse)
    def manage(request: Request, case_id: str):
        return manage_page(request, ctx.get_case(case_id))

    async def write(request: Request, case_id: str, action: Callable[[dict, dict], dict | None], done: str):
        root_or_503()
        case = ctx.get_case(case_id)
        if not case.meta:
            raise HTTPException(
                409, "this case is registered from the environment, not the registry; it cannot be edited here"
            )
        ctx.check_origin(request)
        form = await ctx.read_form(request)
        who, source, extra = ctx.authorize(request, form)
        try:
            out = action(form, _by(who, source, extra)) or {}
        except registry.RegistryError as exc:
            return manage_page(request, case, str(exc), exc.status)
        settings._registry_stamp = None
        from .web import sync_registry

        sync_registry(settings)
        target = out.get("id") or case.id
        return RedirectResponse(f"/cases/{target}/manage?done={done}", status_code=303)

    @app.post("/cases/{case_id}/brief")
    async def edit_brief(request: Request, case_id: str):
        case = ctx.get_case(case_id)
        return await write(
            request,
            case_id,
            lambda f, by: registry.update_brief(
                settings.cases_root,
                case.id,
                question=f.get("question", ""),
                notes=f.get("notes"),
                by=by,
                run_ids=run_ids(case, settings.runner_state),
            ),
            "brief",
        )

    @app.post("/cases/{case_id}/meta")
    async def edit_meta(request: Request, case_id: str):
        case = ctx.get_case(case_id)
        return await write(
            request,
            case_id,
            lambda f, by: registry.update_meta(
                settings.cases_root,
                case.id,
                title=f.get("title"),
                budget_usd=f.get("budget_usd"),
                by=by,
                run_ids=run_ids(case, settings.runner_state),
            ),
            "meta",
        )

    @app.post("/cases/{case_id}/revise")
    async def revise(request: Request, case_id: str):
        case = ctx.get_case(case_id)
        return await write(
            request,
            case_id,
            lambda f, by: registry.revise(
                settings.cases_root, case.id, question=f.get("question", ""), notes=f.get("notes"), by=by
            ),
            "revise",
        )

    @app.post("/cases/{case_id}/archive")
    async def archive(request: Request, case_id: str):
        case = ctx.get_case(case_id)
        return await write(
            request,
            case_id,
            lambda f, by: registry.set_archived(settings.cases_root, case.id, True, by=by, reason=f.get("reason")),
            "archive",
        )

    @app.post("/cases/{case_id}/restore")
    async def restore(request: Request, case_id: str):
        case = ctx.get_case(case_id)
        return await write(
            request, case_id, lambda f, by: registry.set_archived(settings.cases_root, case.id, False, by=by), "restore"
        )

    @app.get("/cases-archived", response_class=HTMLResponse)
    def archived(request: Request):
        return render(
            request,
            "cases_archived.html",
            nav="cases",
            rows=list(settings.archived.values()),
            registry_on=settings.cases_root is not None,
        )

    @app.get("/api/cases")
    def cases_api() -> dict:
        def row(c):
            return {
                "id": c.id,
                "title": c.title,
                "status": case_status(c, settings),
                "meta": c.meta,
                "from": "registry" if c.meta else "env",
            }

        return {
            "registry": str(settings.cases_root) if settings.cases_root else None,
            "registry_error": settings.registry_error,
            "cases": [row(c) for c in settings.cases.values()],
            "archived": [row(c) for c in settings.archived.values()],
        }
