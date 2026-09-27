"""Ontofill Console: a generic, multi-case operator and approver UI over case packages and their lake runs.

Every domain word on screen comes from a case's own ontology. Decisions (CONTRACT v0.9.7) take the approver from the
SSO proxy's identity header, are bound to the exact artifact bytes the approver reviewed, and are logged append-only.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import approvals as ap
from . import dod, evidence, live, registry, runner_state
from .domain import Domain
from .gold import GoldStore, UnavailableStore, _store_from_lake_yaml, backend_of, sniff_media_type, store_for_case

HERE = Path(__file__).parent
CASE_ID = re.compile(r"^[a-z0-9-]{1,40}$")
DEFAULT_IDENTITY_HEADERS = ("X-NetBird-User",)
DEFAULT_DEADLINE = "2026-09-27T12:00:00-07:00"


@dataclass
class Case:
    id: str
    root: Path
    store: GoldStore | UnavailableStore
    lake_error: str | None = None
    meta: dict | None = None  # its entry in the case registry (CONTRACT v1.0.6), None for env-registered cases

    @property
    def dir(self) -> ap.CaseDir:
        return ap.CaseDir(self.root)

    @property
    def title(self) -> str:
        text = self.dir.read("brief.md", limit=4000) or ""
        for line in text.splitlines():
            if line.startswith("#"):
                return line.lstrip("#").strip() or self.id
        return self.id

    @property
    def brief(self) -> str | None:
        text = self.dir.read("brief.md", limit=4000) or ""
        body = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
        return body[0] if body else None

    @property
    def pending(self) -> int:
        return sum(1 for a in ap.approvals(self.dir) if a.approved is None)

    @property
    def synthetic(self) -> bool:
        try:
            return str(getattr(self.store, "case_id", "") or "").startswith("fixture")
        except LookupError:  # an unresolvable lake is not synthetic; the pages say what is missing instead
            return False


@dataclass
class Settings:
    cases: dict[str, Case] = field(default_factory=dict)
    identity_mode: str = "sso"  # sso | sso-group | local (dev only, explicit)
    identity_headers: tuple[str, ...] = DEFAULT_IDENTITY_HEADERS
    groups_header: str = "X-NetBird-Groups"  # sso-group: the proxy's verified group membership
    approver_group: str = "approvers"
    direct_deny: tuple = ()  # ip networks (our own mesh peers) whose direct requests may not decide
    runner_state: Path = runner_state.DEFAULT_STATE  # shared with the ontofill-runner service (R18)
    cases_root: Path | None = None  # ONTOFILL_CASES_ROOT: the data-driven case registry (v1.0.6); None = env only
    env_cases: dict[str, Case] = field(default_factory=dict)  # from ONTOFILL_CONSOLE_CASES (read-only fallback)
    archived: dict[str, Case] = field(default_factory=dict)
    registry_error: str | None = None
    _registry_stamp: tuple | None = None

    def __post_init__(self) -> None:
        if self.identity_mode not in ("sso", "sso-group", "local"):
            raise ValueError(f"ONTOFILL_CONSOLE_IDENTITY must be sso, sso-group or local, got {self.identity_mode!r}")


def parse_cases(spec: str, env: dict[str, str] | None = None) -> dict[str, Case]:
    """`id=/path/to/case[:/path/to/local/lake],…` → registry. The lake is `<case parent>/lake.yaml` unless given."""
    cases: dict[str, Case] = {}
    for part in (p.strip() for p in (spec or "").split(",")):
        if not part:
            continue
        cid, sep, rest = part.partition("=")
        cid = cid.strip()
        if not sep or not CASE_ID.match(cid):
            raise ValueError(f"bad case entry {part!r}: expected id=/path (id: a-z, 0-9, -; at most 40 chars)")
        if cid in cases:
            raise ValueError(f"case id {cid!r} registered twice")
        case_path, _, lake = rest.partition(":")
        root = Path(case_path.strip())
        try:
            store: GoldStore | UnavailableStore = store_for_case(root, lake.strip() or None, env)
            error = None
        except (FileNotFoundError, ValueError, ImportError) as exc:  # the case still registers; approvals work
            message = str(exc) if isinstance(exc, FileNotFoundError) else f"lake unavailable ({type(exc).__name__})"
            store, error = UnavailableStore(message), message
        cases[cid] = Case(cid, root, store, error)
    return cases


def settings_from_env(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if env is None else env)
    headers = tuple(h.strip() for h in env.get("ONTOFILL_CONSOLE_IDENTITY_HEADER", "").split(",") if h.strip())
    env_cases = parse_cases(env.get("ONTOFILL_CONSOLE_CASES", ""), env)
    settings = Settings(cases=dict(env_cases), env_cases=env_cases, cases_root=registry.root_from_env(env),
                    identity_mode=env.get("ONTOFILL_CONSOLE_IDENTITY", "sso").strip() or "sso",
                    identity_headers=headers or DEFAULT_IDENTITY_HEADERS,
                    groups_header=env.get("ONTOFILL_CONSOLE_GROUPS_HEADER", "").strip() or "X-NetBird-Groups",
                    approver_group=env.get("ONTOFILL_CONSOLE_APPROVER_GROUP", "").strip() or "approvers",
                    direct_deny=ap.parse_networks(env.get("ONTOFILL_CONSOLE_DIRECT_DENY")),
                    runner_state=runner_state.state_dir(env))
    sync_registry(settings, env)
    return settings


def _case_from_entry(root: Path, item: dict, env: dict[str, str] | None) -> Case:
    case_dir = registry.resolve(root, item["path"])
    lake_path = registry.resolve(root, item.get("lake") or str(case_dir.parent / "lake.yaml"))
    try:
        store: GoldStore | UnavailableStore = _store_from_lake_yaml(lake_path, dict(os.environ if env is None else env))
        store.case_dir = case_dir
        error = None
    except (FileNotFoundError, ValueError, ImportError) as exc:
        message = str(exc) if isinstance(exc, FileNotFoundError) else f"lake unavailable ({type(exc).__name__})"
        store, error = UnavailableStore(message), message
    return Case(item["id"], case_dir, store, error, meta=item)


def sync_registry(settings: Settings, env: dict[str, str] | None = None) -> None:
    """Merge the registry (if any) over the env cases, in place, when cases.json changed; the registry wins on ids.
    A torn or broken registry keeps the last good set and shows the error."""
    root = settings.cases_root
    if root is None:
        return
    path = root / registry.REGISTRY
    try:
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except FileNotFoundError:
        stamp = None
    if stamp == settings._registry_stamp and settings._registry_stamp is not None:
        return
    try:
        data = registry.load(root)
    except registry.RegistryError as exc:
        settings.registry_error = str(exc)
        return
    active, archived, errors = dict(settings.env_cases), {}, []
    for item in data["cases"]:
        if not isinstance(item, dict) or not CASE_ID.match(str(item.get("id") or "")):
            continue
        try:
            case = _case_from_entry(root, item, env)
        except registry.RegistryError as exc:
            errors.append(f"{item.get('id')}: {exc}")  # an unsafe entry is skipped, never served, and reported
            continue
        (archived if item.get("archived") else active)[case.id] = case
    settings.cases.clear()
    settings.cases.update(active)
    settings.archived.clear()
    settings.archived.update(archived)
    settings._registry_stamp = stamp
    settings.registry_error = "; ".join(errors) or None


def brief(value, limit: int = 240) -> str:
    """Readable one-liner for trace/proof fields, which the engine may write as strings or objects."""
    def fmt(v):
        if isinstance(v, dict):
            return ", ".join(f"{k}: {fmt(x)}" for k, x in v.items() if x not in (None, "", [], {}))
        if isinstance(v, list):
            shown = [fmt(x) for x in v[:4]]
            return "[" + ", ".join(shown) + (f", +{len(v) - 4}" if len(v) > 4 else "") + "]"
        if isinstance(v, str) and v.startswith("sha256:") and len(v) > 20:
            return v[:15] + "…"
        return str(v)

    text = "" if value is None else fmt(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def host_of(url: str) -> str:
    return urlsplit(url).hostname or url


def safe_url(url: str) -> str | None:
    return url if urlsplit(str(url)).scheme in ("http", "https") else None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or settings_from_env()
    app = FastAPI(title="Ontofill Console", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings

    @app.middleware("http")
    async def pick_up_registry_changes(request: Request, call_next):
        sync_registry(settings)  # new, archived or revised cases appear without a restart (cheap: stat unless changed)
        return await call_next(request)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    env = templates.env
    env.globals.update(host_of=host_of, safe_url=safe_url, role="approver")
    env.filters["pct"] = lambda x: f"{round((x or 0) * 100)}%"
    env.filters["money"] = lambda x: f"{x:,.2f}" if isinstance(x, (int, float)) else (x or "—")
    env.filters["brief"] = brief

    # identity ---------------------------------------------------------------------------------------------------
    def identity(request: Request) -> str | None:
        """The signed-in approver from the SSO proxy's header (first non-empty candidate), sanitized."""
        if settings.identity_mode != "sso":
            return None
        for name in settings.identity_headers:
            value = ap.clean_identity(request.headers.get(name))
            if value:
                return value
        return None

    def direct_denied(request: Request) -> bool:
        """The TCP peer (never X-Forwarded-For / X-Real-IP, which a direct client can forge) is one of our own mesh
        peers: its headers did not come through the proxy, so it may not decide."""
        return ap.address_denied(request.client.host if request.client else None, settings.direct_deny)

    def group_verified(request: Request) -> bool:
        """sso-group: the proxy-supplied groups header names the approver group, and the request came via the proxy."""
        return (settings.identity_mode == "sso-group" and not direct_denied(request)
                and ap.groups_contain(request.headers.get(settings.groups_header), settings.approver_group))

    # helpers ----------------------------------------------------------------------------------------------------
    def get_case(case_id: str) -> Case:
        case = (settings.cases.get(case_id) or settings.archived.get(case_id)) if CASE_ID.match(case_id or "") else None
        if case is None:
            raise HTTPException(404, "unknown case")
        return case

    def case_domain(case: Case) -> Domain:
        try:
            return case.store.run(None).domain
        except (LookupError, OSError, ValueError):
            text = case.dir.read("02-ontology/ontology.json", limit=10**7)
            try:
                onto = json.loads(text) if text else {}
            except ValueError:
                onto = {}
            return Domain.from_ontology(onto if isinstance(onto, dict) else {})

    def live_run_id(case: Case) -> str | None:
        try:
            return case.store.live_run_id()
        except (LookupError, OSError, ValueError):
            return None

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        case: Case | None = ctx.get("case")
        ctx.setdefault("nav", "")
        ctx.setdefault("cases", settings.cases)
        ctx.setdefault("base", f"/cases/{case.id}" if case else "")
        ctx.setdefault("domain", case_domain(case) if case else Domain.from_ontology({}))
        ctx.setdefault("backend", None)
        ctx.setdefault("preview", False)
        ctx.setdefault("synthetic", bool(case and case.synthetic))
        ctx.setdefault("identity", identity(request))
        ctx.setdefault("identity_mode", settings.identity_mode)
        ctx.setdefault("approver_group", settings.approver_group)
        ctx.setdefault("group_verified", group_verified(request))
        ctx.setdefault("live_run_id", None)
        ctx.setdefault("runner", runner_state.line(settings.runner_state, case.id) if case else None)
        ctx.setdefault("runner_kill", runner_state.killed(settings.runner_state))
        return templates.TemplateResponse(request, name, ctx)

    def check_origin(request: Request) -> None:
        host = request.headers.get("host")
        for header in (request.headers.get("origin"), request.headers.get("referer")):
            if header and urlsplit(header).netloc != host:
                raise HTTPException(403, "cross-origin decision refused")

    def authorize(request: Request, form: dict) -> tuple[str, str, dict | None]:
        """Who is deciding, under the console's identity mode: (approver, identity_source, extra) or HTTP 403/400."""
        if settings.identity_mode == "sso":
            who = identity(request)
            if not who:
                raise HTTPException(403, "no signed-in identity: open the console through its sign-in URL")
            return who, "sso", None
        if settings.identity_mode == "sso-group":
            if direct_denied(request):
                raise HTTPException(403, "decisions must come through the NetBird proxy")
            if not ap.groups_contain(request.headers.get(settings.groups_header), settings.approver_group):
                raise HTTPException(403, f"not signed in as a member of {settings.approver_group}")
            name = ap.clean_display_name(form.get("display_name"))
            if not name:
                raise HTTPException(400, "enter your name (self-declared, at most 100 characters)")
            return (f"group:{settings.approver_group}", "sso-group",
                    {"unverified_name": name,
                     "verified": {"group": settings.approver_group, "via": "NetBird SSO (x-netbird-groups)"}})
        who = ap.clean_display_name(form.get("approver"))
        if not who:
            raise HTTPException(400, "enter your name (local mode)")
        return who, "local", None

    async def read_form(request: Request) -> dict:
        return {k: v[0] for k, v in parse_qs((await request.body()).decode(errors="replace")).items()}

    def whoami_model(request: Request) -> dict:
        """Header NAMES only (never values), and which candidate identity header the console would use."""
        names = sorted({k.lower() for k in request.headers})
        candidates, used = [], None
        for name in settings.identity_headers:
            raw = request.headers.get(name)
            usable = ap.clean_identity(raw) is not None
            candidates.append({"name": name, "present": raw is not None, "non_empty": bool(raw and raw.strip()),
                               "usable": usable})
            if usable and used is None and settings.identity_mode == "sso":
                used = name
        groups_raw = request.headers.get(settings.groups_header)
        return {"identity_mode": settings.identity_mode, "header_names": names, "candidates": candidates,
                "used_header": used, "identity_detected": used is not None,
                "groups_header": settings.groups_header, "groups_header_present": groups_raw is not None,
                "approver_group": settings.approver_group,
                "in_approver_group": ap.groups_contain(groups_raw, settings.approver_group),
                "direct_denied": direct_denied(request), "group_verified": group_verified(request)}

    # read-only visualization views (CONTRACT v1.0.4b: Claude Product); they never write
    from .viz import VizContext, register

    register(VizContext(app=app, render=render, get_case=get_case, case_domain=case_domain, settings=settings, env=env))

    # case CRUD (CONTRACT v1.0.6: Claude Product): approver-only writes, logged in the case's decisions.jsonl
    from .case_admin import AdminContext
    from .case_admin import install as install_case_admin

    install_case_admin(AdminContext(app=app, render=render, get_case=get_case, settings=settings, authorize=authorize,
                                    check_origin=check_origin, read_form=read_form, live_run_id=live_run_id, env=env))

    @app.get("/whoami", response_class=HTMLResponse)
    def whoami(request: Request):
        return render(request, "whoami.html", nav="whoami", w=whoami_model(request))

    @app.get("/api/whoami")
    def whoami_api(request: Request) -> dict:
        return whoami_model(request)

    # pages ------------------------------------------------------------------------------------------------------
    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "cases": sorted(settings.cases), "identity": settings.identity_mode}

    @app.get("/", response_class=HTMLResponse)
    def cases(request: Request):
        rows = []
        for c in settings.cases.values():
            rid = live_run_id(c)
            status = (c.store.live_status(rid) if rid else None) or {}
            rows.append({"id": c.id, "title": c.title, "run_id": rid, "state": status.get("state"),
                         "checkpoint_pending": status.get("checkpoint_pending"), "pending": c.pending,
                         "lake_error": c.lake_error})
        return render(request, "cases.html", nav="cases", rows=rows)

    @app.get("/cases/{case_id}", response_class=HTMLResponse)
    def case_overview(request: Request, case_id: str):
        case = get_case(case_id)
        rid = live_run_id(case)
        latest = None
        if rid:
            status = case.store.live_status(rid) or {}
            latest = {"run_id": rid, "state": status.get("state"), "checkpoint_pending": status.get("checkpoint_pending")}
        waiting = [a for a in ap.approvals(case.dir) if a.approved is None]
        return render(request, "case.html", nav="overview", case=case, latest=latest, waiting=waiting,
                      brief=case.brief, log=ap.decisions_log(case.dir)[:50], lake_error=case.lake_error)

    @app.get("/cases/{case_id}/runs", response_class=HTMLResponse)
    def runs(request: Request, case_id: str):
        case = get_case(case_id)
        latest = live_run_id(case)
        rows = []
        for rid in reversed(case.store.live_run_ids()):
            status = case.store.live_status(rid) or {}
            rows.append({"run_id": rid, "latest": rid == latest, "state": status.get("state"),
                         "checkpoint_pending": status.get("checkpoint_pending"),
                         "backend": backend_of(status.get("metrics"), [status])})
        return render(request, "runs.html", nav="run", case=case, rows=rows, lake_error=case.lake_error)

    def run_view_model(case: Case, run_id: str, after: int = 0) -> dict:
        steps = live.annotate(case.store.live_steps(run_id))
        status = case.store.live_status(run_id)
        if not steps and status is None:
            raise HTTPException(404, f"no live feed for run {run_id}")
        jobs = case.store.live_jobs(run_id)
        backend = backend_of((status or {}).get("metrics"), [*steps, status or {}])
        panel = live.summarize(steps, status, case_domain(case))
        return {"run_id": run_id, "steps": steps, "new": steps[after:][::-1], "panel": panel,
                "threads": live.loop_threads(steps), "proof": live.proof(jobs, steps), "backend": backend}

    @app.get("/cases/{case_id}/runs/{run_id}", response_class=HTMLResponse)
    def run_view(request: Request, case_id: str, run_id: str, limit: int = 150):
        case = get_case(case_id)
        m = run_view_model(case, run_id)
        limit = max(1, min(limit, 2000))
        status = case.store.live_status(run_id) or {}
        items, _ = live.stream_items(m["steps"], max(0, len(m["steps"]) - limit), m["threads"])
        return render(request, "run.html", nav="run", case=case, live_run_id=run_id, m=m, items=items, limit=limit,
                      backend=m["backend"],
                      preview=bool(status.get("preview") or (status.get("metrics") or {}).get("preview")),
                      PHASES=live.PHASES, MODE_NAMES=live.MODE_NAMES, CHECKPOINT_PHASE=live.CHECKPOINT_PHASE,
                      others=case.store.live_run_ids())

    @app.get("/cases/{case_id}/api/runs/{run_id}")
    def run_api(case_id: str, run_id: str, after: int = 0) -> dict:
        case = get_case(case_id)
        m = run_view_model(case, run_id, max(after, 0))
        d = case_domain(case)
        base = f"/cases/{case.id}"
        start = max(after, 0, len(m["steps"]) - 150)
        items, updated = live.stream_items(m["steps"], start, m["threads"], incremental=after > 0)
        steps_html = env.get_template("_steps.html").render(items=items, MODE_NAMES=live.MODE_NAMES, base=base)
        thread_tpl = env.get_template("_loop_thread.html")
        threads_html = [{"id": t["id"], "html": thread_tpl.render(t=t, MODE_NAMES=live.MODE_NAMES, base=base)}
                        for t in updated]
        panel_html = env.get_template("_run_panel.html").render(
            m=m, PHASES=live.PHASES, CHECKPOINT_PHASE=live.CHECKPOINT_PHASE, MODE_NAMES=live.MODE_NAMES, domain=d,
            base=base)
        proof_html = env.get_template("_proof.html").render(m=m, base=base)
        timeline_html = env.get_template("_timeline.html").render(
            m=m, PHASES=live.PHASES, CHECKPOINT_PHASE=live.CHECKPOINT_PHASE, base=base)
        return {"count": len(m["steps"]), "state": m["panel"]["state"], "steps_html": steps_html,
                "threads_html": threads_html, "panel_html": panel_html, "timeline_html": timeline_html,
                "proof_html": proof_html}

    @app.get("/cases/{case_id}/files/{path:path}", response_class=HTMLResponse)
    def case_file(request: Request, case_id: str, path: str):
        case = get_case(case_id)
        text = case.dir.read(path, limit=200_000)
        if text is None:
            raise HTTPException(404, "not in the case package")
        return render(request, "files.html", case=case, path=path, text=text)

    @app.get("/cases/{case_id}/bronze/{key}")
    def bronze(case_id: str, key: str):
        case = get_case(case_id)
        data = case.store.bronze(key)
        if data is None:
            raise HTTPException(404, "bronze object not found")
        declared = str(case.store.bronze_meta(key).get("content_type") or "")
        media_type = declared if declared.startswith(("image/", "application/pdf")) else sniff_media_type(data)
        return Response(data, media_type=media_type, headers={
            "Cache-Control": "private, max-age=31536000, immutable",  # content-addressed
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "X-Content-Type-Options": "nosniff"})

    # approvals --------------------------------------------------------------------------------------------------
    @app.get("/cases/{case_id}/approvals", response_class=HTMLResponse)
    def approvals(request: Request, case_id: str, done: str = "", error: str = ""):
        case = get_case(case_id)
        return render(request, "approvals.html", nav="approvals", case=case, items=ap.approvals(case.dir),
                      done=done, error=error, log=ap.decisions_log(case.dir)[:100])

    def review_page(request: Request, case: Case, item: ap.Approval, error: str = "", reason: str = "",
                    status_code: int = 200) -> HTMLResponse:
        docs = ap.load_artifacts(case.dir, item)
        paths = [{"path": p, "exists": case.dir.exists(p)} for p in ap.artifact_paths(item)]
        gen = item.meta.get("generated_by") or next((d.get("generated_by") for d in docs.values()
                                                    if d.get("generated_by")), None)
        shot = item.meta.get("screenshot_key") if item.checkpoint == "action" else None
        has_screenshot = bool(shot) and case.store.bronze(str(shot)) is not None
        who = identity(request)
        can_decide = item.approved is None and (settings.identity_mode == "local" or bool(who)
                                                or group_verified(request))
        log = [d for d in ap.decisions_log(case.dir) if d.get("phase_dir") == item.phase_dir]
        response = render(request, "approval.html", nav="approvals", case=case, a=item, docs=docs, paths=paths,
                          error=error, gen=gen, backend=(gen or {}).get("backend"), taxonomy_stats=ap.taxonomy_stats,
                          has_screenshot=has_screenshot, history=ap.revision_history(docs),
                          drafts=ap.archived_drafts(case.dir, item), reason=reason, reason_max=ap.DENY_REASON_MAX,
                          digests=ap.artifact_digests(case.dir, item), can_decide=can_decide, log=log)
        response.status_code = status_code
        return response

    def find_item(case: Case, phase_dir: str) -> ap.Approval | None:
        return next((a for a in ap.approvals(case.dir) if a.phase_dir == phase_dir), None)

    @app.get("/cases/{case_id}/approvals/{phase_dir:path}", response_class=HTMLResponse)
    def approval_detail(request: Request, case_id: str, phase_dir: str, error: str = ""):
        case = get_case(case_id)
        item = find_item(case, phase_dir)
        if not item:
            raise HTTPException(404, f"no approval checkpoint in {phase_dir}")
        return review_page(request, case, item, error)

    @app.post("/cases/{case_id}/approvals")
    async def decide(request: Request, case_id: str):
        case = get_case(case_id)
        check_origin(request)
        form = await read_form(request)
        phase_dir = form.get("phase_dir", "")
        if settings.identity_mode == "local":  # local mode: the approvals module validates the typed name itself
            who, source, extra = form.get("approver", ""), "local", None
        else:
            who, source, extra = authorize(request, form)
        seen = {k.removeprefix("artifact_sha256."): v for k, v in form.items() if k.startswith("artifact_sha256.")}
        decisions = {k.removeprefix("decision."): v for k, v in form.items() if k.startswith("decision.")}
        rid = live_run_id(case)
        status = (case.store.live_status(rid) if rid else None) or {}
        item = find_item(case, phase_dir)
        hint = rid if item and status.get("checkpoint_pending") in (item.checkpoint, item.phase_dir) else None
        try:
            ap.decide(case.dir, case.id, phase_dir, who, source, seen, decisions=decisions or None,
                      decision=form.get("decision"), reason=form.get("reason"), run_id=hint, extra=extra)
        except ap.DecisionError as exc:
            if item is None:
                raise HTTPException(exc.status, str(exc)) from exc
            return review_page(request, case, item, str(exc), reason=form.get("reason", "")[:5000],
                               status_code=exc.status)
        return RedirectResponse(f"/cases/{case.id}/approvals?done={quote(phase_dir)}", status_code=303)

    # runner control (R18): operator actions, same identity rules as approvals, logged in decisions.jsonl ----------
    @app.post("/cases/{case_id}/runner")
    async def runner_action(request: Request, case_id: str):
        case = get_case(case_id)
        check_origin(request)
        form = await read_form(request)
        action = form.get("action", "")
        if action not in runner_state.ACTIONS:
            raise HTTPException(400, "action must be start, pause or resume")
        who, source, extra = authorize(request, form)
        to_phase = None
        if action == "start" and form.get("to_phase"):
            if not form["to_phase"].isdigit() or not 1 <= int(form["to_phase"]) <= 5:
                raise HTTPException(400, "to_phase must be 1-5")
            to_phase = int(form["to_phase"])
        runner_state.apply_action(settings.runner_state, case.id, action, who, to_phase)
        runner_state.append_line(case.dir.root / "decisions.jsonl",
                                 {"ts": runner_state.now_iso(), "case_id": case.id, "checkpoint": "runner",
                                  "decision": action, "approver": who, "identity_source": source, **(extra or {}),
                                  **({"to_phase": to_phase} if to_phase else {})})
        return RedirectResponse(f"/cases/{case.id}?runner={quote(action)}", status_code=303)

    @app.post("/runner/kill")
    async def runner_kill(request: Request):
        check_origin(request)
        form = await read_form(request)
        state = form.get("state", "")
        if state not in ("on", "off"):
            raise HTTPException(400, "state must be on or off")
        who, source, extra = authorize(request, form)
        runner_state.set_kill(settings.runner_state, state == "on",
                              {"ts": runner_state.now_iso(), "checkpoint": "runner_kill", "decision": state,
                               "approver": who, "identity_source": source, **(extra or {})})
        return RedirectResponse("/?kill=" + state, status_code=303)

    # console-wide pages ----------------------------------------------------------------------------------------
    @app.get("/spend", response_class=HTMLResponse)
    def spend(request: Request):
        """Billing view from the spend tracker's saved history only; never calls billing APIs."""
        raw = os.environ.get("ONTOFILL_CONSOLE_SPEND_HISTORY")
        path = Path(raw) if raw else None
        history = []
        if path and path.is_file():
            for line in path.read_text().splitlines():
                try:
                    history.append(json.loads(line))
                except ValueError:
                    continue
        history = [h for h in history if isinstance(h, dict) and h.get("ts") and "credit_used" in h]
        latest = history[-1] if history else None
        rows, prev = [], None
        for s in history[-48:]:
            dt = (datetime.fromisoformat(s["ts"]) - datetime.fromisoformat(prev["ts"])).total_seconds() / 3600 if prev else 0
            rate = (s["credit_used"] - prev["credit_used"]) / dt if prev and dt > 0 else None
            rows.append({"ts": s["ts"], "used": s["credit_used"], "left": s.get("credit_remaining"), "rate": rate})
            prev = s
        deadline = datetime.fromisoformat(os.environ.get("ONTOFILL_CONSOLE_DEADLINE", DEFAULT_DEADLINE))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone(timedelta(0)))
        hours_left = max(0.0, (deadline - datetime.fromisoformat(latest["ts"])).total_seconds() / 3600) if latest else 0
        rates = [x["rate"] for x in rows[-1:] if x["rate"] is not None] + [(latest or {}).get("resource_rate_usd_per_hour") or 0]
        projected = (latest["credit_used"] + max(rates) * hours_left) if latest else None
        if latest:
            latest = {"by_category": {}, "inference": [], "resources": [], "engine": {}, "credit_total": 0.0,
                      "credit_remaining": None, **latest}
        return render(request, "spend.html", nav="spend", latest=latest, rows=rows[::-1], projected=projected,
                      hours_left=hours_left, per_entity=None, history_path=str(path or "(ONTOFILL_CONSOLE_SPEND_HISTORY unset)"))

    @app.get("/evidence", response_class=HTMLResponse)
    def track_evidence(request: Request):
        """The track checklist with proof from saved files only (no API calls, no probing)."""
        ev = evidence.rows([(c.id, c.store) for c in settings.cases.values()])
        synthetic = bool(ev.get("jobs")) and settings.cases[ev["jobs"]["case_id"]].synthetic
        return render(request, "track_evidence.html", nav="evidence", ev=ev, synthetic=synthetic)

    _ = dod  # the DoD evaluator is used by the run panel through live.summarize
    return app
