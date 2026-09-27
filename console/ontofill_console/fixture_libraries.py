"""A second, unrelated synthetic domain (public libraries) for the genericity proof: the same app must render it
with no code changes, taking every label, DoD property, relation and rule from this case's ontology.

    uv run pa-app fixtures --domain libraries [OUT]

Everything is invented: "Biblioteca Ejemplo NN", codes starting with ZZ-LIB, hosts on the reserved `.example` TLD.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import dod
from .domain import Domain

CASE_ID = "fixture-libraries"
RUN_ID = "run-libraries-0001"
GEN = {"backend": "recorded", "model": "synthetic-fixture", "at": "2026-09-26T18:00:00+00:00"}
SOURCES = {  # source_id: (host, source class)
    "registry-example": ("bibliotecas-registro.example", "national_library_registry"),
    "municipal-example": ("municipio.example", "municipal_portal"),
    "website-example": ("biblioteca.example", "library_website"),
}
PROP_SOURCE = {
    "name": "registry-example",
    "registry_code": "registry-example",
    "address": "registry-example",
    "opening_hours": "municipal-example",
    "website": "website-example",
    "founded": "registry-example",
}

ONTOLOGY = {
    "version": "v1",
    "prd_path": "01-scope/prd.json",
    "shacl_path": "02-ontology/schema/shapes.ttl",
    "dod_queries_path": "02-ontology/dod-queries.json",
    "generated_by": GEN,
    "factors": [
        {
            "id": "operator_kind",
            "label": "Operator",
            "description": "Who runs the library",
            "kind": "grounded",
            "evidence": [
                {"url": "https://bibliotecas-registro.example/", "description": "Registry lists the operator"}
            ],
        }
    ],
    "taxonomies": [
        {
            "factor_id": "operator_kind",
            "root_id": "operator",
            "root_label": "Operator",
            "soundness": 1.0,
            "coverage": 1.0,
            "children": [
                {"id": "municipal", "label": "Municipal", "level": 1, "critic_label": "Good-Exclusive"},
                {"id": "state", "label": "State", "level": 1, "critic_label": "Good-Exclusive"},
            ],
        }
    ],
    "primary_class": "library",
    "classes": [
        {
            "id": "library",
            "label": "Library",
            "label_plural": "Libraries",
            "description": "A public library branch",
            "title_property": "name",
            "identifier_property": "registry_code",
            "aligned_to": "https://schema.org/Library",
        },
        {
            "id": "operator",
            "label": "Operator",
            "label_plural": "Operators",
            "description": "Who runs a branch",
            "title_property": "operator_name",
            "identifier_property": "operator_name",
            "aligned_to": None,
        },
    ],
    "properties": [
        {
            "id": "name",
            "label": "Branch name",
            "domain": "library",
            "datatype": "xsd:string",
            "dod": True,
            "order": 1,
            "description": "Official branch name",
            "aligned_to": "https://schema.org/name",
        },
        {
            "id": "registry_code",
            "label": "Registry code",
            "domain": "library",
            "datatype": "xsd:string",
            "dod": True,
            "order": 2,
            "description": "Code in the national registry",
            "aligned_to": None,
        },
        {
            "id": "address",
            "label": "Street address",
            "domain": "library",
            "datatype": "xsd:string",
            "dod": True,
            "order": 3,
            "description": "Where the branch is",
            "aligned_to": "https://schema.org/address",
        },
        {
            "id": "opening_hours",
            "label": "Opening hours",
            "domain": "library",
            "datatype": "xsd:string",
            "dod": True,
            "order": 4,
            "description": "Published opening hours",
            "aligned_to": "https://schema.org/openingHours",
        },
        {
            "id": "website",
            "label": "Website",
            "domain": "library",
            "datatype": "xsd:anyURI",
            "dod": False,
            "order": 5,
            "description": "Official page",
            "aligned_to": "https://schema.org/url",
        },
        {
            "id": "founded",
            "label": "Year founded",
            "domain": "library",
            "datatype": "xsd:gYear",
            "dod": False,
            "order": 6,
            "description": "Year the branch opened",
            "aligned_to": "https://schema.org/foundingDate",
        },
        {
            "id": "operator_name",
            "label": "Operator",
            "domain": "operator",
            "datatype": "xsd:string",
            "dod": False,
            "order": 1,
            "description": "Name of the operating body",
            "aligned_to": "https://schema.org/name",
        },
        {
            "id": "kind",
            "label": "Kind",
            "domain": "operator",
            "datatype": "xsd:string",
            "dod": False,
            "order": 2,
            "description": "Municipal or state",
            "aligned_to": None,
        },
    ],
    "relations": [
        {"id": "same_operator", "label": "Same operator", "domain": "library", "range": "library", "symmetric": True},
        {"id": "operated_by", "label": "Operated by", "domain": "library", "range": "operator", "symmetric": False},
    ],
    "rules": [
        {
            "id": "hours_not_published",
            "label": "Opening hours not published officially",
            "checks": "Whether any official source publishes the branch's opening hours.",
            "verify": ["Check the municipal portal page for the branch.", "Call the branch and note the answer."],
        },
    ],
    "source_classes": [
        {"id": "national_library_registry", "label": "National library registry"},
        {"id": "municipal_portal", "label": "Municipal portal"},
        {"id": "library_website", "label": "Library website"},
    ],
}
DOD_QUERIES = {
    "generated_by": GEN,
    "queries": [
        {
            "criterion_id": "libraries_found",
            "aggregate": "count_entities",
            "class_id": "library",
            "target": 20,
            "operator": ">=",
        },
        {
            "criterion_id": "with_hours",
            "aggregate": "count_entities_with_properties",
            "class_id": "library",
            "properties": ["name", "address", "opening_hours"],
            "target": 20,
            "operator": ">=",
        },
        {
            "criterion_id": "evidence_integrity",
            "aggregate": "count_values_without_evidence",
            "target": 0,
            "operator": "=",
        },
    ],
}


def generate(out: Path, n: int = 24, seed: int = 11) -> Path:
    """Write OUT/lake and OUT/case for the libraries domain. Returns the lake root."""
    from .fixtures import _Bronze  # local import: fixtures builds on this module

    lake, case = out / "lake", out / "case"
    bronze = _Bronze(lake)
    rng = random.Random(seed)
    ts0 = datetime(2026, 9, 26, 18, 0, tzinfo=UTC)
    trace: list[dict] = []

    def step(phase, parent, requested, **kw):
        s = {
            "step_id": f"step:{RUN_ID}:{len(trace) + 1:05d}",
            "run_id": RUN_ID,
            "phase": phase,
            "source_id": kw.get("source_id"),
            "objective_id": kw.get("objective_id"),
            "tdd_path": kw.get("tdd_path"),
            "mode": kw.get("mode", "S1"),
            "observed": kw.get("observed", requested),
            "requested": requested,
            "executed": kw.get("executed", "done"),
            "evaluated": kw.get("evaluated", "ok"),
            "parent_step_id": parent,
            "value_ids": kw.get("value_ids", []),
            "ts": (ts0 + timedelta(seconds=len(trace) * 5)).isoformat(),
            "generated_by": GEN,
        }
        trace.append(s)
        return s

    p1 = step(1, None, "draft a PRD from the brief")
    p2 = step(2, p1["step_id"], "derive the ontology")
    tdd = {}
    for sid, (host, _cls) in SOURCES.items():
        p3 = step(3, p2["step_id"], f"map {host}", source_id=sid, objective_id=f"{sid}-profile")
        tdd[sid] = step(
            4,
            p3["step_id"],
            "agree a TDD",
            source_id=sid,
            objective_id=f"{sid}-profile",
            tdd_path=f"04-local/{sid}__profile/tdd.md",
        )

    def captured(sid: str, path: str, label: str, value: str) -> dict:
        host, source_class = SOURCES[sid]
        meta = {"captured_at": ts0.isoformat(), "source_id": sid, "step_id": tdd[sid]["step_id"]}
        return {
            "url": f"https://{host}{path}",
            "selector": f"dd.{label}",
            "bronze_key": bronze.page(host, path, label, value, **meta),
            "screenshot_key": bronze.screenshot(host, label, value, **meta),
            "captured_at": ts0.isoformat(),
            "source_id": sid,
            "source_type": source_class,
        }

    operators = []
    for k in range(1, 5):
        name, kind = f"Ayuntamiento Ejemplo {k}", "municipal" if k % 2 else "state"
        operators.append(
            {
                "id": f"op:fixture-{k}",
                "class": "operator",
                "classified_as": [],
                "flags": [],
                "links": [],
                "generated_by": GEN,
                "properties": {
                    "operator_name": {
                        "value_id": f"val:op-{k}-name",
                        "value": name,
                        "confidence": 0.99,
                        "status": "gold",
                        "generated_by": GEN,
                        "evidence": [captured("registry-example", f"/operador/{k}", "operator", name)],
                    },
                    "kind": {
                        "value_id": f"val:op-{k}-kind",
                        "value": kind,
                        "confidence": 0.99,
                        "status": "gold",
                        "generated_by": GEN,
                        "evidence": [captured("registry-example", f"/operador/{k}", "kind", kind)],
                    },
                },
            }
        )
    libraries = []
    for i in range(n):
        nn = i + 1
        facts = {
            "name": f"Biblioteca Ejemplo {nn:02d}",
            "registry_code": f"ZZ-LIB-{nn:04d}",
            "address": f"Avenida Ficticia {nn}, Ciudad Ejemplo",
            "opening_hours": "Mo-Fr 09:00-19:00",
            "website": f"https://biblioteca.example/{nn:02d}",
            "founded": str(1950 + (i * 3) % 70),
        }
        props, by_source = {}, {}
        for prop, value in facts.items():
            vid = f"val:lib-{nn:03d}-{prop}"
            if prop == "opening_hours" and i % 6 == 5:
                props[prop] = {
                    "value_id": vid,
                    "value": None,
                    "confidence": 0.0,
                    "status": "missing",
                    "evidence": [],
                    "generated_by": GEN,
                }
                continue
            sid = PROP_SOURCE[prop]
            ev = captured(sid, f"/ficha/{facts['registry_code']}", prop, value)
            props[prop] = {
                "value_id": vid,
                "value": value,
                "confidence": round(rng.uniform(0.85, 0.99), 2),
                "status": "gold",
                "evidence": [ev],
                "generated_by": GEN,
            }
            by_source.setdefault(sid, []).append(vid)
        for sid, vids in by_source.items():
            step(
                5,
                tdd[sid]["step_id"],
                f"extract {len(vids)} properties",
                source_id=sid,
                objective_id=f"{sid}-profile",
                tdd_path=tdd[sid]["tdd_path"],
                mode="D1" if sid != "website-example" else "S1",
                value_ids=vids,
            )
        op = operators[i % len(operators)]
        links = [{"property": "operated_by", "target": op["id"], "via_value_id": props["name"]["value_id"]}]
        flags = []
        if props["opening_hours"]["status"] == "missing":
            flags.append(
                {
                    "rule_id": "hours_not_published",
                    "label": "Opening hours not published officially",
                    "explanation": "No official source lists this branch's opening hours.",
                    "evidence_value_ids": [props["name"]["value_id"]],
                }
            )
        libraries.append(
            {
                "id": f"lib:fixture-{nn:03d}",
                "class": "library",
                "classified_as": [],
                "properties": props,
                "links": links,
                "flags": flags,
                "generated_by": GEN,
            }
        )
    for lib in libraries:  # branches run by the same operator are connected
        op = lib["links"][0]["target"]
        for other in libraries:
            if other is not lib and other["links"][0]["target"] == op and len(lib["links"]) < 3:
                lib["links"].append(
                    {
                        "property": "same_operator",
                        "target": other["id"],
                        "via_value_id": lib["properties"]["name"]["value_id"],
                    }
                )
    entities = operators + libraries

    domain = Domain.from_ontology(ONTOLOGY)
    m = dod.compute(entities, domain)
    m.pop("primary_class")
    rows = []
    for q in DOD_QUERIES["queries"]:
        actual = dod.evaluate_query(q, entities, domain)
        rows.append(
            {
                "criterion_id": q["criterion_id"],
                "query": dod.query_text(q),
                "target": q["target"],
                "actual": actual,
                "met": dod.OPS[q["operator"]](actual, q["target"]),
            }
        )
    modes = {k: sum(1 for s in trace if s["mode"] == k) for k in ("D0", "D1", "S1", "S2")}
    metrics = {
        "run_id": RUN_ID,
        **m,
        "level_ratio_coverage": {"operator_kind": [1.0]},
        "mode_counts": modes,
        "decisions_by_backend": {"recorded": len(entities)},
        "inference_backend": "recorded",
        "dod": rows,
        "jobs": {"ok": sum(1 for s in trace if s["phase"] == 5), "failed_by_reason": {}},
        "generated_by": GEN,
    }

    run_dir = lake / "gold" / CASE_ID / RUN_ID
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "entities.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entities))
    (run_dir / "trace.jsonl").write_text("".join(json.dumps(s) + "\n" for s in trace))
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    (run_dir / "ontology.json").write_text(json.dumps(ONTOLOGY, indent=2))
    (run_dir / "dod-queries.json").write_text(json.dumps(DOD_QUERIES, indent=2))
    (lake / "gold" / CASE_ID / "latest.json").write_text(json.dumps({"run_id": RUN_ID}))
    files = {
        "brief.md": "# Brief (synthetic fixture)\n\nWhich public libraries are open, and when?\n",
        "02-ontology/ontology.json": json.dumps(ONTOLOGY, indent=2),
        "02-ontology/dod-queries.json": json.dumps(DOD_QUERIES, indent=2),
    }
    files |= {
        f"04-local/{sid}__profile/tdd.md": f"# TDD: {host} (synthetic fixture)\n" for sid, (host, _c) in SOURCES.items()
    }
    for rel, text in files.items():
        (case / rel).parent.mkdir(parents=True, exist_ok=True)
        (case / rel).write_text(text)
    return lake
