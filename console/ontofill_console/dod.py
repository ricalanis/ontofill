"""Recompute the definition of done from the gold entities, and cross-check the engine's metrics.json.

    uv run pa-app dod [--gold-dir DIR] [--run-id ID]

Generic over the case's ontology (CONTRACT v0.7 §11): which class is primary and which properties count toward
the DoD come from the ontology.
Exit code 0 when the recomputed values match the engine's metrics.json, 1 on any mismatch.
"""

from __future__ import annotations

import re

from .domain import Domain

# Criteria used when neither metrics.json nor the PRD states them (labelled as defaults in the UI).
DEFAULT_CRITERIA = [
    {"criterion_id": "entities_meeting_dod", "query": "entities_meeting_dod", "target": 50},
    {"criterion_id": "distinct_source_classes", "query": "distinct_source_classes", "target": 4},
    {"criterion_id": "values_without_evidence", "query": "values_without_evidence", "target": 0},
]
LOWER_IS_BETTER = ("values_without_evidence",)


def jev_only(value: dict | None) -> bool:
    """A value whose only decision came from Jev (CONTRACT section 10): never enough for gold on its own."""
    return bool(value) and (value.get("generated_by") or {}).get("backend") == "jev"


def is_filled(value: dict | None) -> bool:
    """A value counts when it is gold, backed by evidence, and not decided by Jev alone."""
    return bool(value) and value.get("status") == "gold" and bool(value.get("evidence")) and not jev_only(value)


def dod_ratio(entity: dict, domain: Domain) -> float:
    props = domain.dod_props(entity.get("class"))
    if not props:
        return 0.0
    values = entity.get("properties") or {}
    return sum(is_filled(values.get(p.id)) for p in props) / len(props)


def compute(entities: list[dict], domain: Domain) -> dict:
    """The §11 metric keys, recomputed from entities."""
    primary = [e for e in entities if e.get("class") == domain.primary_class]
    totals: dict[str, int] = {}
    for e in entities:
        totals[e.get("class") or "?"] = totals.get(e.get("class") or "?", 0) + 1
    per_prop = {
        p.id: (sum(is_filled((e.get("properties") or {}).get(p.id)) for e in primary) / len(primary))
        if primary
        else 0.0
        for p in domain.dod_props()
    }
    classes: set[str] = set()
    without_evidence = 0
    for e in entities:
        for v in (e.get("properties") or {}).values():
            if not isinstance(v, dict) or v.get("status") != "gold":
                continue
            if not v.get("evidence"):
                without_evidence += 1
            classes.update(ev["source_type"] for ev in v.get("evidence") or [] if ev.get("source_type"))
    return {
        "primary_class": domain.primary_class,
        "entities_total": totals,
        "entities_meeting_dod": {
            domain.primary_class: sum(dod_ratio(e, domain) >= domain.dod_threshold - 1e-9 for e in primary)
        },
        "per_property_completeness": {domain.primary_class: per_prop},
        "distinct_source_classes": len(classes),
        "values_without_evidence": without_evidence,
    }


def _engine_view(engine: dict, domain: Domain) -> dict:
    """The engine's metrics in §11 terms."""
    cls = domain.primary_class
    return {
        "entities_total": (engine.get("entities_total") or {}).get(cls),
        "entities_meeting_dod": (engine.get("entities_meeting_dod") or {}).get(cls),
        "per_property_completeness": (engine.get("per_property_completeness") or {}).get(cls) or {},
        "distinct_source_classes": engine.get("distinct_source_classes"),
        "values_without_evidence": engine.get("values_without_evidence"),
    }


def cross_check(recomputed: dict, engine: dict, domain: Domain, tol: float = 1e-6) -> list[str]:
    """Human-readable mismatches between our recomputation and the engine's metrics.json."""
    cls = domain.primary_class
    ours = {
        "entities_total": recomputed["entities_total"].get(cls, 0),
        "entities_meeting_dod": recomputed["entities_meeting_dod"][cls],
        "distinct_source_classes": recomputed["distinct_source_classes"],
        "values_without_evidence": recomputed["values_without_evidence"],
    }
    theirs = _engine_view(engine, domain)
    problems = []
    for key, value in ours.items():
        if theirs.get(key) is None:
            problems.append(f"{key}: missing from engine metrics.json")
        elif theirs[key] != value:
            problems.append(f"{key}: engine={theirs[key]} recomputed={value}")
    for prop, ratio in recomputed["per_property_completeness"][cls].items():
        engine_ratio = theirs["per_property_completeness"].get(prop)
        if engine_ratio is None:
            problems.append(f"per_property_completeness.{prop}: missing from engine metrics.json")
        elif abs(engine_ratio - ratio) > tol:
            problems.append(f"per_property_completeness.{prop}: engine={engine_ratio:.4f} recomputed={ratio:.4f}")
    return problems


def _resolve(query: str, recomputed: dict, domain: Domain) -> float | None:
    """Value of a DoD query we can recompute: queries that name a §11 metric key (optionally class-qualified)."""
    q = (query or "").lower()
    cls = domain.primary_class
    for key in ("entities_meeting_dod", "entities_total"):
        if key in q:
            return recomputed[key].get(cls, 0)
    if "distinct_source" in q:
        return recomputed["distinct_source_classes"]
    if "without_evidence" in q:
        return recomputed["values_without_evidence"]
    m = re.search(r"per_property_completeness[.\[]\W*(\w+)\W*[.\[]\W*(\w+)", q)
    if m:
        return (recomputed["per_property_completeness"].get(m.group(1)) or {}).get(m.group(2))
    return None


# Declarative DoD queries (ontofill schemas/dod-queries.schema.json) ------------------------------------------------

OPS = {
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
    "=": lambda a, b: a == b,
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
}


def _condition(entity: dict, cond: dict) -> bool:
    v = (entity.get("properties") or {}).get(cond.get("property") or "") or {}
    if cond.get("operator") == "exists":
        return is_filled(v)
    value = v.get("value") if v.get("status") == "gold" else None
    return (value == cond.get("value")) if cond.get("operator") == "eq" else (value != cond.get("value"))


def evaluate_query(query: dict, entities: list[dict], domain: Domain) -> float:
    """Exact value of one declarative DoD query over the gold entities."""
    cls = query.get("class_id") or query.get("class")
    if query.get("aggregate") == "entities_meeting_completeness":
        cls = cls or domain.primary_class
    pool = [e for e in entities if not cls or e.get("class") == cls]
    pool = [e for e in pool if all(_condition(e, c) for c in query.get("conditions") or [])]
    agg = query.get("aggregate")
    if agg == "entities_meeting_completeness":
        props = query.get("properties")
        names = [p.id for p in domain.dod_props(cls)] if props in (None, "dod") else list(props)
        ratio = float(query.get("min_ratio") or domain.dod_threshold)

        def share(e: dict) -> float:
            values = e.get("properties") or {}
            return sum(is_filled(values.get(n)) for n in names) / len(names) if names else 0.0

        return sum(share(e) >= ratio - 1e-9 for e in pool)
    if agg == "count_entities":
        return len(pool)
    if agg == "count_entities_with_relation":  # as the engine's export: entities with a link of that relation
        rel = query.get("relation_id")
        return sum(
            any(isinstance(link, dict) and link.get("property") == rel for link in e.get("links") or []) for e in pool
        )
    if agg == "count_entities_with_properties":
        return sum(
            all(is_filled((e.get("properties") or {}).get(p)) for p in query.get("properties") or []) for e in pool
        )
    values = [
        v
        for e in pool
        for v in (e.get("properties") or {}).values()
        if isinstance(v, dict) and v.get("status") == "gold"
    ]
    if agg == "count_distinct_source_classes":
        return len({ev["source_type"] for v in values for ev in v.get("evidence") or [] if ev.get("source_type")})
    if agg == "count_values_without_evidence":
        return sum(1 for v in values if not v.get("evidence"))
    raise ValueError(f"unknown DoD aggregate {agg!r}")


def query_text(query: dict) -> str:
    """Compact, readable rendering of a declarative query (what metrics.dod[].query shows)."""
    head = query.get("aggregate", "?")
    cls = query.get("class_id") or query.get("class")
    parts = [f'class_id="{cls}"'] if cls and query.get("relation_id") else ([cls] if cls else [])
    if query.get("relation_id"):
        parts.append(f'relation_id="{query["relation_id"]}"')
    props = query.get("properties")
    if props:
        parts.append(props if isinstance(props, str) else ", ".join(props))
    if query.get("min_ratio") is not None:
        parts.append(f"min_ratio {query['min_ratio']}")
    for c in query.get("conditions") or []:
        parts.append(f"{c['property']} {c['operator']}" + (f" {c['value']!r}" if "value" in c else ""))
    return f"{head}({'; '.join(parts)}) {query.get('operator', '>=')} {query.get('target')}"


def criterion_label(query: str, criterion_id: str, domain: Domain) -> str:
    q = f"{query} {criterion_id}".lower()
    plural = domain.class_label(plural=True)
    if "entities_meeting_dod" in q or "entities_meeting_completeness" in q:
        share = round(domain.dod_threshold * 100)
        return f"{plural} with ≥ {share}% of their definition-of-done properties"
    if "entities_total" in q:
        return f"{plural} found"
    if "distinct_source" in q:
        return "Distinct public source classes"
    if "without_evidence" in q:
        return "Gold values without evidence"
    rel = re.search(r'relation_id="?([\w-]+)', query)
    if "count_entities_with_relation" in q and rel:
        cls = re.search(r'class(?:_id)?="?([\w-]+)', query)
        who = domain.class_label(cls.group(1) if cls else None, plural=True)
        return f"{who} with a “{domain.relation_label(rel.group(1))}” link"
    return (criterion_id or query).replace("_", " ").capitalize()


def _met(key: str, actual: float | None, target: float | None) -> bool | None:
    if actual is None or target is None:
        return None
    return actual <= target if any(k in key for k in LOWER_IS_BETTER) else actual >= target


def criteria(
    recomputed: dict,
    engine: dict,
    domain: Domain,
    backend: str | None = None,
    queries: list[dict] | None = None,
    entities: list[dict] | None = None,
) -> list[dict]:
    """The DoD criteria to show: the engine's `metrics.dod[]` when present, else the case's declarative queries,
    else defaults. Each row carries the engine's actual/met and our recomputation: exact when a declarative query
    for the criterion is available, else when the query names a metric we can recompute. Recorded (simulated)
    inference never counts as met."""
    by_id = {q["criterion_id"]: q for q in queries or [] if isinstance(q, dict) and q.get("criterion_id")}
    rows = engine.get("dod") if isinstance(engine.get("dod"), list) else None
    source = "engine" if rows else ("queries" if by_id else "default")
    if not rows and by_id:
        rows_in = [
            {"criterion_id": q["criterion_id"], "query": query_text(q), "target": q.get("target")}
            for q in by_id.values()
        ]
    else:
        rows_in = rows or DEFAULT_CRITERIA
    out = []
    for row in rows_in:
        query = str(row.get("query") or row.get("criterion_id") or "")
        target = row.get("target")
        declared = by_id.get(str(row.get("criterion_id") or ""))
        key = query.lower() + " " + str(row.get("criterion_id") or "").lower()
        note = None
        if declared is not None and entities is not None:
            try:
                ours = evaluate_query(declared, entities, domain)
            except ValueError as exc:  # an aggregate this console does not know: show the row, never fail the page
                ours, note = None, f"not computed here ({exc})"
            met_ours = (
                OPS.get(declared.get("operator", ">="), OPS[">="])(ours, declared.get("target", target))
                if ours is not None
                else None
            )
        else:
            ours = _resolve(query, recomputed, domain)
            met_ours = _met(key, ours, target)
        met_engine = row.get("met") if rows else None
        mock = backend == "recorded"
        out.append(
            {
                "criterion_id": row.get("criterion_id") or query,
                "query": query,
                "target": target,
                "label": criterion_label(query, str(row.get("criterion_id") or ""), domain),
                "lower_is_better": (declared or {}).get("operator") in ("<=", "<", "=") and not target
                if declared
                else any(k in key for k in LOWER_IS_BETTER),
                "engine_actual": row.get("actual") if rows else None,
                "engine_met": met_engine,
                "actual": ours,
                "met": (False if mock else (met_ours if met_ours is not None else met_engine)),
                "recomputed": ours is not None,
                "agrees": None
                if ours is None or not rows or row.get("actual") is None
                else abs(float(row["actual"]) - float(ours)) < 1e-6,
                "mock": mock,
                "source": source,
                "note": note,
            }
        )
    return out
