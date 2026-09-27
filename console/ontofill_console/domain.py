"""A case's domain, read from its approved ontology (CONTRACT v0.7 §11/§11a).

The console is generic: every domain word it shows comes from the case's ontology (primary class, property labels
and order, which properties count toward the definition of done, relation labels, rule labels, source classes).
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_DOD_THRESHOLD = 0.8  # used when the DoD criteria do not state the per-entity share; labelled in the UI


def humanize(ident: str) -> str:
    return (ident or "").replace("_", " ").replace("-", " ").strip().capitalize()


@dataclass
class Prop:
    id: str
    label: str
    datatype: str = ""
    domain: str | None = None
    dod: bool = False
    order: int = 1000
    description: str = ""
    aligned_to: str | None = None


@dataclass
class Domain:
    primary_class: str
    classes: dict[str, dict] = field(default_factory=dict)
    properties: dict[str, list[Prop]] = field(default_factory=dict)  # class id -> ordered properties
    relations: dict[str, dict] = field(default_factory=dict)
    rules: dict[str, dict] = field(default_factory=dict)
    source_classes: dict[str, str] = field(default_factory=dict)
    dod_threshold: float = DEFAULT_DOD_THRESHOLD
    threshold_stated: bool = False
    legacy: bool = False

    # labels ---------------------------------------------------------------------------------------------------
    def class_label(self, cls: str | None = None, plural: bool = False) -> str:
        c = self.classes.get(cls or self.primary_class) or {}
        if plural:
            return c.get("label_plural") or (c.get("label") or humanize(cls or self.primary_class)) + "s"
        return c.get("label") or humanize(cls or self.primary_class)

    def props(self, cls: str | None = None) -> list[Prop]:
        return self.properties.get(cls or self.primary_class, [])

    def dod_props(self, cls: str | None = None) -> list[Prop]:
        return [p for p in self.props(cls) if p.dod]

    def prop_label(self, prop_id: str, cls: str | None = None) -> str:
        for p in self.props(cls) if cls else [q for ps in self.properties.values() for q in ps]:
            if p.id == prop_id:
                return p.label
        return humanize(prop_id)

    def relation_label(self, rel_id: str) -> str:
        return (self.relations.get(rel_id) or {}).get("label") or humanize(rel_id)

    def source_label(self, source_class: str | None) -> str:
        return self.source_classes.get(source_class or "") or humanize(source_class or "")

    def title_property(self, cls: str | None = None) -> str | None:
        c = self.classes.get(cls or self.primary_class) or {}
        if c.get("title_property"):
            return c["title_property"]
        props = self.props(cls)
        return props[0].id if props else None

    def identifier_property(self, cls: str | None = None) -> str | None:
        return (self.classes.get(cls or self.primary_class) or {}).get("identifier_property")

    def rule(self, rule_id: str, label: str = "") -> dict:
        r = self.rules.get(rule_id) or {}
        return {
            "label": r.get("label") or label or humanize(rule_id),
            "checks": r.get("checks") or label or rule_id,
            "verify": list(r.get("verify") or []),
        }

    def peer_relations(self) -> list[str]:
        """Relations between two entities of the primary class (drawn in the relationship view)."""
        return [
            rid
            for rid, r in self.relations.items()
            if (r.get("domain") in (None, self.primary_class)) and (r.get("range") in (None, self.primary_class))
        ]

    # building ---------------------------------------------------------------------------------------------------
    @classmethod
    def from_ontology(cls, onto: dict, legacy: bool = False) -> Domain:
        classes = {c["id"]: c for c in onto.get("classes") or [] if isinstance(c, dict) and c.get("id")}
        primary = (
            onto.get("primary_class")
            or next((cid for cid, c in classes.items() if c.get("primary")), None)
            or next(iter(classes), "entity")
        )
        props: dict[str, list[Prop]] = {}
        for i, p in enumerate(onto.get("properties") or []):
            if not isinstance(p, dict) or not p.get("id"):
                continue
            dom = p.get("domain") or primary
            props.setdefault(dom, []).append(
                Prop(
                    id=p["id"],
                    label=p.get("label") or humanize(p["id"]),
                    datatype=p.get("datatype") or "",
                    domain=dom,
                    dod=bool(p.get("dod")),
                    order=int(p.get("order", 1000 + i)),
                    description=p.get("description") or "",
                    aligned_to=p.get("aligned_to"),
                )
            )
        for plist in props.values():
            plist.sort(key=lambda p: p.order)
        relations = {r["id"]: r for r in onto.get("relations") or [] if isinstance(r, dict) and r.get("id")}
        rules = {r["id"]: r for r in onto.get("rules") or [] if isinstance(r, dict) and r.get("id")}
        sources = {
            s["id"]: s.get("label") or humanize(s["id"])
            for s in onto.get("source_classes") or []
            if isinstance(s, dict) and s.get("id")
        }
        threshold = onto.get("dod_threshold")
        return cls(
            primary_class=primary,
            classes=classes,
            properties=props,
            relations=relations,
            rules=rules,
            source_classes=sources,
            dod_threshold=float(threshold) if threshold else DEFAULT_DOD_THRESHOLD,
            threshold_stated=threshold is not None,
            legacy=legacy,
        )

    def usable(self) -> bool:
        return bool(self.props())
