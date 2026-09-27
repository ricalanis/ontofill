"""Shared helpers for the Output and Entities views: which gold run to read, which domain explains it, and how one
property value and one piece of evidence are shown. Pure and read-only; no routes here."""

from __future__ import annotations

from urllib.parse import quote, urlsplit

from fastapi import HTTPException

from .. import dod
from ..domain import Domain
from ..gold import inferred_ontology
from .core import Artifacts

# Cell / value states in the completeness views. "weak" = gold but not enough on its own (Jev alone, or no evidence).
STATE_LABELS = {
    "gold": "gold with evidence",
    "weak": "gold, not enough alone",
    "conflict": "conflict kept",
    "missing": "missing",
}


def q(ident: str) -> str:
    """URL-quote an id (entity and value ids contain ':')."""
    return quote(str(ident), safe="")


def brief_text(a: Artifacts) -> str | None:
    """The case's question: brief.md without its heading lines."""
    text = a.text("brief.md", limit=20_000) or ""
    body = [ln.rstrip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    return "\n".join(body) or None


def resolve_run(a: Artifacts, run: str | None):
    """(gold Run or None, run id or None, note). An explicit run id that is neither a gold nor a live run is a 404;
    a live run without a gold export yet is an honest empty state."""
    gold_ids = a.gold_run_ids()
    if run:
        if run in gold_ids:
            return a.gold(run), run, None
        if run in a.run_ids():
            return None, run, "live-only"
        raise HTTPException(404, f"no run {run} in this case's lake")
    g = a.gold(None)
    if g is not None:
        return g, g.run_id, None
    return None, None, None


def domain_for(run) -> Domain:
    """The run's domain; the inferred ontology when the ontology is missing or describes no primary properties."""
    d = run.domain
    if not run.entities or (d.usable() and any(e.get("class") == d.primary_class for e in run.entities)):
        return d
    return Domain.from_ontology(inferred_ontology(run.entities))


def props_for(domain: Domain, run, cls: str | None) -> tuple[list, Domain]:
    """Ordered properties for a class, falling back to the inferred ontology when the domain does not know it."""
    props = domain.props(cls)
    if props:
        return props, domain
    inferred = Domain.from_ontology(inferred_ontology(run.entities))
    return inferred.props(cls), inferred


def value_state(f: dict | None) -> str:
    if not isinstance(f, dict):
        return "missing"
    status = f.get("status")
    if status == "conflict":
        return "conflict"
    if status == "gold":
        return "gold" if dod.is_filled(f) else "weak"
    return "missing"


def evidence_view(case_id: str, a: Artifacts, ev: dict, with_meta: bool = True) -> dict:
    url = str(ev.get("url") or "")
    parts = urlsplit(url)
    shot, raw = ev.get("screenshot_key"), ev.get("bronze_key")
    base = f"/cases/{case_id}/bronze/"
    meta = a.bronze_meta(raw) if (with_meta and raw) else {}
    return {
        "url": url if parts.scheme in ("http", "https") else None,
        "host": parts.hostname or url,
        "path": (parts.path or "/") + (f"?{parts.query}" if parts.query else ""),
        "selector": ev.get("selector"),
        "captured_at": ev.get("captured_at"),
        "source_id": ev.get("source_id"),
        "source_type": ev.get("source_type"),
        "format": ev.get("format"),
        "screenshot_key": shot,
        "bronze_key": raw,
        "screenshot_href": base + shot if shot else None,
        "raw_href": base + raw if raw else None,
        "raw_content_type": meta.get("content_type"),
        "raw_step_id": meta.get("step_id"),
    }


def entity_title(domain: Domain, entity: dict) -> str:
    prop = domain.title_property(entity.get("class"))
    f = (entity.get("properties") or {}).get(prop or "") or {}
    return str(f.get("value")) if isinstance(f, dict) and f.get("value") not in (None, "") else str(entity.get("id"))


def entity_identifier(domain: Domain, entity: dict) -> str | None:
    prop = domain.identifier_property(entity.get("class"))
    f = (entity.get("properties") or {}).get(prop or "") or {}
    return f.get("value") if isinstance(f, dict) else None
