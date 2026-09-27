"""Lead-only discovery providers for the Phase 3 loop.

A lead is a URL some provider suggested for an ontology gap. It is never
evidence: nothing here fetches a page into bronze. The discovery loop confirms a
lead only through a sandbox capture plus the case's approved authority policy.

Every domain-specific input (queries, jurisdictions, publisher names and
domains) comes from case artifacts; providers only know their public APIs.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import httpx
from jsonschema.exceptions import ValidationError
from ontofill_scrape.models import ToolFailure

from ontofill.inference import DecisionClient, complete_validated

USER_AGENT = "ontofill-discovery/0.1 (+https://github.com/ricalanis/ontofill)"
_ENGINE_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CACHE = _ENGINE_ROOT / ".cache" / "discovery"
# A provider or decision failure must never stop discovery; these are the failures
# the decision client, httpx, the sandbox and the scrape toolkit raise.
PROVIDER_ERRORS = (
    AssertionError,
    KeyError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    ValidationError,
    ToolFailure,
    httpx.HTTPError,
)
_CATALOG_KIND = re.compile(
    r"catalog(?:ue)?|data[\s_-]*portal|open[\s_-]*data|dataset|ckan", re.IGNORECASE
)
_OPEN_DATA_CUES = (
    "open data",
    "data portal",
    "dataset",
    "data catalog",
    "data catalogue",
    "catalogo de datos",
    "catalogo de dados",
    "datos abiertos",
    "dados abertos",
    "donnees ouvertes",
    "catalogue de donnees",
    "ckan",
)
_P3_BASE_ITERATIONS = 3
_P3_MAX_ITERATIONS = 12
_P3_EXTRA_ITERATION_RESERVE_USD = 0.05
_P3_QUERIES_PER_ITERATION = 4
_TAVILY_CREDITS_PER_QUERY = 1


@dataclass(frozen=True)
class LeadQuery:
    """One targeted query for one ontology property gap."""

    property_id: str
    text: str


@dataclass
class Lead:
    url: str
    title: str
    snippet: str
    discovered_by: str
    query: str
    property_ids: tuple[str, ...]
    score: float = 0.0
    publisher: str | None = None

    def as_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "discovered_by": self.discovered_by,
            "query": self.query,
            "property_ids": list(self.property_ids),
            "score": self.score,
            "publisher": self.publisher,
            "lead_only": True,
        }


@dataclass
class LeadContext:
    """What the loop knows when it asks providers for leads."""

    brief: str
    ontology: Mapping
    policy: Mapping
    queries: tuple[LeadQuery, ...]
    iteration: int
    publisher_names: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    tried: set[str] = field(default_factory=set)
    open_data_portals: tuple[str, ...] = ()


def public_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme in {"http", "https"}
        and "." in host
        and not parsed.username
        and not parsed.password
        and host != "localhost"
        and not host.endswith(".localhost")
    )


def policy_domains(policy: Mapping) -> tuple[list[str], list[str]]:
    """Return (primary, other) approved publisher domains, deduplicated in order."""
    primary: list[str] = []
    other: list[str] = []
    for publisher in policy.get("trusted_publishers", []):
        target = primary if publisher.get("tier", "primary") == "primary" else other
        for domain in publisher.get("domains", []):
            value = domain.lower().rstrip(".")
            if value and value not in primary and value not in other:
                target.append(value)
    return primary, other


class JsonCache:
    """Content-addressed JSON cache so repeated queries cost no provider credits."""

    def __init__(self, root: Path | None) -> None:
        self.root = root

    @staticmethod
    def fingerprint(namespace: str, request: object) -> str:
        encoded = json.dumps([namespace, request], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(encoded.encode()).hexdigest()

    def get(self, namespace: str, key: str) -> dict | None:
        if self.root is None:
            return None
        path = self.root / namespace / f"{key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, namespace: str, key: str, value: dict) -> None:
        if self.root is None:
            return
        path = self.root / namespace / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


class ProviderUnavailable(RuntimeError):
    """A provider cannot run (missing key, no configured catalog, spent budget)."""


def _plain(text: str) -> str:
    value = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).split())


def _open_data_text(value: object) -> str:
    if isinstance(value, str):
        return _plain(value.replace("_", " ").replace("-", " "))
    if isinstance(value, Mapping):
        return " ".join(_open_data_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_open_data_text(item) for item in value)
    return ""


def is_open_data_portal(lead: Mapping, ontology: Mapping) -> bool:
    """Recognize generic open-data portal signals from a lead and its class labels.

    This is only a routing hint for trying the keyless CKAN endpoint. It does
    not approve the host or make a lead evidence; P3 still applies its normal
    capture and authority checks.
    """
    lead_text = _open_data_text(
        {
            key: lead.get(key)
            for key in (
                "channel",
                "channel_type",
                "kind",
                "resource_type",
                "source_class",
                "source_class_label",
                "title",
                "snippet",
                "query",
                "publisher",
            )
        }
    )
    class_id = lead.get("source_class_id") or lead.get("channel_id")
    classes = ontology.get("source_classes", ontology.get("source_channels", []))
    if isinstance(classes, Mapping):
        classes = list(classes.values())
    if class_id is not None and isinstance(classes, (list, tuple)):
        for source_class in classes:
            if not isinstance(source_class, Mapping):
                continue
            if source_class.get("id") == class_id:
                lead_text += " " + _open_data_text(source_class)
    return any(cue in lead_text for cue in _OPEN_DATA_CUES)


def _public_portal_domain(value: str) -> str | None:
    """Extract a safe public host from a discovered portal URL or host string."""
    raw = value.strip()
    if not raw:
        return None
    candidate = raw if re.match(r"^https?://", raw, flags=re.IGNORECASE) else f"https://{raw}"
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or port is not None
        or not public_url(f"https://{host}")
    ):
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return None
    return host


def tavily_credit_cap_for_budget(remaining_budget_usd: float | None) -> int:
    """Budget basic Tavily queries for the P3 rounds available in this run.

    P3 reserves $0.05 per extra iteration above its three-round floor and caps
    at twelve iterations. One basic Tavily credit is reserved per bounded P3
    query, matching DiscoveryLoop's four-query-per-iteration default.
    """
    if (
        not isinstance(remaining_budget_usd, (int, float))
        or not math.isfinite(float(remaining_budget_usd))
        or remaining_budget_usd <= 0
    ):
        iterations = _P3_BASE_ITERATIONS
    else:
        extra = int(float(remaining_budget_usd) / _P3_EXTRA_ITERATION_RESERVE_USD)
        iterations = min(_P3_MAX_ITERATIONS, _P3_BASE_ITERATIONS + extra)
    return iterations * _P3_QUERIES_PER_ITERATION * _TAVILY_CREDITS_PER_QUERY


class WikidataClient:
    """Small polite client for Wikidata's public action API and query service."""

    API = "https://www.wikidata.org/w/api.php"
    SPARQL = "https://query.wikidata.org/sparql"
    # Wikidata item ids for the generic concepts "country" and "sovereign state".
    COUNTRY_CLASSES = frozenset({"Q6256", "Q3624078"})

    def __init__(
        self,
        *,
        http: httpx.Client | None = None,
        cache: JsonCache | None = None,
        min_interval_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.http = http or httpx.Client(timeout=20)
        self.cache = cache or JsonCache(None)
        self.min_interval_seconds = min_interval_seconds
        self.sleep = sleep
        self.monotonic = monotonic
        self._last = 0.0
        self.calls = 0
        self.cache_hits = 0

    def _get(self, url: str, params: dict, *, accept: str = "application/json") -> dict:
        key = JsonCache.fingerprint("wikidata", [url, params])
        cached = self.cache.get("wikidata", key)
        if cached is not None:
            self.cache_hits += 1
            return cached
        wait = self.min_interval_seconds - (self.monotonic() - self._last)
        if self._last and wait > 0:
            self.sleep(wait)
        self._last = self.monotonic()
        self.calls += 1
        response = self.http.get(
            url, params=params, headers={"User-Agent": USER_AGENT, "Accept": accept}
        )
        response.raise_for_status()
        payload = response.json()
        self.cache.put("wikidata", key, payload)
        return payload

    def search(self, name: str, language: str = "en", limit: int = 3) -> list[dict]:
        payload = self._get(
            self.API,
            {
                "action": "wbsearchentities",
                "search": name,
                "language": language or "en",
                "uselang": language or "en",
                "type": "item",
                "limit": limit,
                "format": "json",
            },
        )
        return [item for item in payload.get("search", []) if item.get("id")]

    def official_websites(self, item_ids: Sequence[str]) -> list[tuple[str, str, str]]:
        """Return (item id, label, P856 URL) through the public query service."""
        ids = [item for item in item_ids if re.fullmatch(r"Q[0-9]+", item)]
        if not ids:
            return []
        values = " ".join(f"wd:{item}" for item in ids)
        query = (
            "SELECT ?item ?itemLabel ?site WHERE { "
            f"VALUES ?item {{ {values} }} ?item wdt:P856 ?site . "
            'SERVICE wikibase:label { bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". } }'
        )
        payload = self._get(
            self.SPARQL,
            {"query": query, "format": "json"},
            accept="application/sparql-results+json",
        )
        rows = []
        for binding in payload.get("results", {}).get("bindings", []):
            item = binding.get("item", {}).get("value", "").rsplit("/", 1)[-1]
            site = binding.get("site", {}).get("value", "")
            label = binding.get("itemLabel", {}).get("value", item)
            if item and public_url(site):
                rows.append((item, label, site))
        return rows

    def _entities(self, ids: Sequence[str]) -> dict:
        payload = self._get(
            self.API,
            {
                "action": "wbgetentities",
                "ids": "|".join(ids),
                "props": "claims|labels",
                "languages": "en",
                "format": "json",
            },
        )
        return payload.get("entities", {})

    @staticmethod
    def _claim_ids(entity: Mapping, prop: str) -> list[str]:
        found = []
        for claim in entity.get("claims", {}).get(prop, []):
            value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
            if isinstance(value, Mapping) and value.get("id"):
                found.append(value["id"])
        return found

    def country_name(self, jurisdiction: str) -> str | None:
        """Map a free-text jurisdiction to a lowercased English country name, or None.

        Only an exact (accent- and case-insensitive) label or alias match counts,
        so a vague jurisdiction yields no country instead of a wrong one.
        """
        parts = [jurisdiction, *re.split(r"[,;()/]| - ", jurisdiction)]
        for part in dict.fromkeys(p.strip() for p in parts if p and p.strip()):
            try:
                hits = self.search(part, "en", limit=5)
            except (httpx.HTTPError, ValueError):
                return None
            exact = [
                hit
                for hit in hits
                if _plain(hit.get("match", {}).get("text", "")) == _plain(part)
                or _plain(hit.get("label", "")) == _plain(part)
            ]
            if not exact:
                continue
            entity_id = exact[0]["id"]
            try:
                entity = self._entities([entity_id]).get(entity_id, {})
                if self.COUNTRY_CLASSES & set(self._claim_ids(entity, "P31")):
                    country = entity
                else:
                    countries = self._claim_ids(entity, "P17")
                    if not countries:
                        continue
                    country = self._entities(countries[:1]).get(countries[0], {})
            except (httpx.HTTPError, ValueError):
                return None
            label = country.get("labels", {}).get("en", {}).get("value")
            if label:
                return label.casefold()
        return None


class LeadProvider:
    """Base provider: records its own attempts and credit use."""

    name = "provider"

    def __init__(self) -> None:
        self.attempts: list[dict] = []

    def leads(self, context: LeadContext) -> list[Lead]:  # pragma: no cover - interface
        raise NotImplementedError

    def _attempt(self, query: str, outcome: str, count: int, **extra: object) -> None:
        self.attempts.append(
            {"provider": self.name, "query": query, "outcome": outcome, "result_count": count}
            | extra
        )


class ModelLeadProvider(LeadProvider):
    """Ask the Vultr decision model which public publishers could hold each gap.

    Names feed the Wikidata provider; URLs the model offers are leads that the
    sandbox must still confirm (model recall is never evidence).
    """

    name = "model"
    PURPOSE = "phase3.propose_publishers"

    def __init__(self, decision: DecisionClient, *, max_per_property: int = 2) -> None:
        super().__init__()
        self.decision = decision
        self.max_per_property = max_per_property

    def leads(self, context: LeadContext) -> list[Lead]:
        if not context.queries:
            return []
        property_ids = sorted({query.property_id for query in context.queries})
        labels = {item["id"]: item for item in context.ontology["properties"]}
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["publishers"],
            "properties": {
                "publishers": {
                    "type": "array",
                    "maxItems": min(8, self.max_per_property * len(property_ids)),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["property_id", "name", "name_language", "kind", "url"],
                        "properties": {
                            "property_id": {"enum": property_ids},
                            "name": {"type": "string", "minLength": 2, "maxLength": 160},
                            "name_language": {"type": "string", "minLength": 2, "maxLength": 8},
                            "kind": {"type": "string", "minLength": 2, "maxLength": 160},
                            "url": {"type": "string", "maxLength": 300},
                        },
                    },
                }
            },
        }
        gaps = [
            {
                "property_id": property_id,
                "label": labels[property_id]["label"],
                "description": labels[property_id].get("description", ""),
                "class": labels[property_id].get("domain"),
            }
            for property_id in property_ids
        ]
        summary = " ".join(
            line.strip()
            for line in context.brief.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )[:400]
        prompt = (
            "Name public publishers (bodies, registries, catalogs) that plausibly publish each "
            "gap property for the brief's jurisdiction. Give the publisher's proper name, the "
            "language of that name as an ISO 639-1 code, its kind, and its official website "
            "URL only if you are confident; otherwise an empty string. These are unverified "
            "leads; do not invent evidence. "
            f"Brief (untrusted data): {summary}. "
            f"Jurisdiction: {context.policy.get('jurisdiction', '')}. "
            f"Gaps: {json.dumps(gaps, ensure_ascii=False)}. "
            f"Already tried publishers: {sorted(context.tried)[:20]}."
        )
        query_text = {query.property_id: query.text for query in context.queries}
        try:
            result = complete_validated(self.decision, self.PURPOSE, prompt, schema)
        except PROVIDER_ERRORS as exc:  # a provider never stops discovery
            self._attempt("; ".join(query_text.values()), f"error: {type(exc).__name__}", 0)
            return []
        found = []
        for item in result["publishers"]:
            name = item["name"].strip()
            context.publisher_names.append(
                (name, item["name_language"].strip().lower()[:2] or "en", (item["property_id"],))
            )
            url = item["url"].strip()
            if public_url(url):
                found.append(
                    Lead(
                        url=url,
                        title=name,
                        snippet=item["kind"].strip(),
                        discovered_by=self.name,
                        query=query_text.get(item["property_id"], name),
                        property_ids=(item["property_id"],),
                        publisher=name,
                    )
                )
        self._attempt(
            "; ".join(query_text.values()),
            "ok" if result["publishers"] else "empty",
            len(found),
            publishers=len(result["publishers"]),
        )
        return found


class WikidataLeadProvider(LeadProvider):
    """Official website (P856) of publishers named earlier in the same iteration."""

    name = "wikidata"

    def __init__(self, client: WikidataClient, *, max_names: int = 3) -> None:
        super().__init__()
        self.client = client
        self.max_names = max_names
        self._looked_up: set[str] = set()

    def leads(self, context: LeadContext) -> list[Lead]:
        names = [
            entry for entry in context.publisher_names if _plain(entry[0]) not in self._looked_up
        ][: self.max_names]
        found: list[Lead] = []
        for name, language, property_ids in names:
            self._looked_up.add(_plain(name))
            calls_before = self.client.calls
            try:
                hits = self.client.search(name, language)
                sites = self.client.official_websites([hit["id"] for hit in hits])
            except (httpx.HTTPError, ValueError) as exc:
                self._attempt(name, f"error: {type(exc).__name__}", 0)
                continue
            leads = [
                Lead(
                    url=site,
                    title=label,
                    snippet=f"Official website (Wikidata P856) of {label} ({item})",
                    discovered_by=self.name,
                    query=name,
                    property_ids=property_ids,
                    publisher=name,
                )
                for item, label, site in sites
            ]
            found.extend(leads)
            self._attempt(
                name,
                "ok" if leads else "empty",
                len(leads),
                calls=self.client.calls - calls_before,
            )
        return found


class CkanLeadProvider(LeadProvider):
    """CKAN-style ``package_search`` on catalog domains the approved policy lists."""

    name = "ckan"

    def __init__(
        self,
        *,
        fetch_json: Callable[[str, str], Mapping] | None = None,
        cache: JsonCache | None = None,
        rows: int = 5,
        max_calls: int = 48,
    ) -> None:
        super().__init__()
        self.fetch_json = fetch_json
        self.cache = cache or JsonCache(None)
        self.rows = rows
        self.max_calls = max_calls
        self.calls = 0
        self.cache_hits = 0
        self.trace: list[dict] = []
        self.jobs: list[dict] = []
        self._unsupported: set[str] = set()

    @staticmethod
    def catalog_domains(policy: Mapping) -> list[str]:
        found = []
        for publisher in policy.get("trusted_publishers", []):
            if _CATALOG_KIND.search(str(publisher.get("kind") or "")):
                for raw_domain in publisher.get("domains", []):
                    if not isinstance(raw_domain, str):
                        continue
                    domain = raw_domain.lower().rstrip(".")
                    try:
                        parsed = urlsplit(f"https://{domain}")
                        port = parsed.port
                    except ValueError:
                        continue
                    if (
                        not public_url(f"https://{domain}")
                        or parsed.hostname != domain
                        or parsed.netloc != domain
                        or parsed.path
                        or parsed.query
                        or parsed.fragment
                        or port is not None
                    ):
                        continue
                    found.append(domain)
        return list(dict.fromkeys(found))

    @classmethod
    def portal_domains(cls, policy: Mapping, open_data_portals: Sequence[str] = ()) -> list[str]:
        """Return trusted catalog hosts plus discovered open-data portal hosts."""
        found = cls.catalog_domains(policy)
        for value in open_data_portals:
            if not isinstance(value, str):
                continue
            domain = _public_portal_domain(value)
            if domain:
                found.append(domain)
        return list(dict.fromkeys(found))

    def _search(self, domain: str, query: str) -> dict:
        request = {"domain": domain, "q": query, "rows": self.rows}
        key = JsonCache.fingerprint("ckan", request)
        if self.fetch_json is None:
            raise ProviderUnavailable("sandbox fetch_json callback is not configured")
        cached = self.cache.get("ckan", key)
        if cached is not None:
            self.cache_hits += 1
            return cached
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise ProviderUnavailable("catalog call budget spent")
        self.calls += 1
        url = (
            f"https://{domain}/api/3/action/package_search?"
            f"{urlencode({'q': query, 'rows': self.rows})}"
        )
        fetcher = self.fetch_json
        trace_before = len(getattr(fetcher, "trace", []))
        jobs_before = len(getattr(fetcher, "jobs", []))
        try:
            payload = fetcher(url, domain)
        finally:
            self.trace.extend(getattr(fetcher, "trace", [])[trace_before:])
            self.jobs.extend(getattr(fetcher, "jobs", [])[jobs_before:])
        if not isinstance(payload, Mapping):
            raise TypeError("sandbox fetch_json did not return a JSON object")
        payload = dict(payload)
        self.cache.put("ckan", key, payload)
        return payload

    def leads(self, context: LeadContext) -> list[Lead]:
        queries = tuple(dict.fromkeys(context.queries))
        if self.fetch_json is None:
            for query in queries:
                self._attempt(
                    query.text,
                    "unavailable: sandbox fetch_json callback is not configured",
                    0,
                )
            return []
        domains = [
            domain
            for domain in self.portal_domains(context.policy, context.open_data_portals)
            if domain not in self._unsupported
        ]
        found: list[Lead] = []
        for domain_index, domain in enumerate(domains):
            for query_index, query in enumerate(queries):
                try:
                    payload = self._search(domain, query.text)
                except ProviderUnavailable:
                    pending = (len(domains) - domain_index - 1) * len(queries) + (
                        len(queries) - query_index
                    )
                    self._attempt(
                        query.text,
                        "cap_reached",
                        0,
                        domain=domain,
                        max_calls=self.max_calls,
                        calls=self.calls,
                        pending_queries=pending,
                    )
                    return found
                except PROVIDER_ERRORS as exc:
                    self._unsupported.add(domain)
                    self._attempt(query.text, f"error: {type(exc).__name__}", 0, domain=domain)
                    break
                leads = parse_ckan(payload, domain, query)
                found.extend(leads)
                self._attempt(query.text, "ok" if leads else "empty", len(leads), domain=domain)
        return found


def parse_ckan(payload: Mapping, domain: str, query: LeadQuery) -> list[Lead]:
    if payload.get("success") is not True:
        return []
    found = []
    for package in payload.get("result", {}).get("results", []):
        name = package.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            continue
        organization = (package.get("organization") or {}).get("title") or ""
        notes = " ".join(str(package.get("notes") or "").split())[:400]
        formats = sorted(
            {
                str(resource.get("format")).lower()
                for resource in package.get("resources", [])
                if resource.get("format")
            }
        )
        found.append(
            Lead(
                url=f"https://{domain}/dataset/{quote(name)}",
                title=str(package.get("title") or name),
                snippet=" ".join(part for part in (organization, notes, " ".join(formats)) if part),
                discovered_by="ckan",
                query=query.text,
                property_ids=(query.property_id,),
                publisher=organization or None,
            )
        )
    return found


class TavilyLeadProvider(LeadProvider):
    """Keyed web search (basic depth) steered by the approved authority policy.

    ``TAVILY_API_KEY`` is read from the environment and never stored in results,
    attempts or errors. Responses are cached by request fingerprint.
    """

    name = "tavily"
    URL = "https://api.tavily.com/search"

    def __init__(
        self,
        *,
        http: httpx.Client | None = None,
        cache: JsonCache | None = None,
        max_credits: int = 10,
        max_results: int = 5,
        country_resolver: Callable[[str], str | None] | None = None,
        api_key: str | None = None,
    ) -> None:
        super().__init__()
        self.http = http or httpx.Client(timeout=30)
        self.cache = cache or JsonCache(None)
        self.max_credits = max_credits
        self.max_results = max_results
        self.country_resolver = country_resolver
        self._key = api_key if api_key is not None else os.environ.get("TAVILY_API_KEY", "")
        self.credits = 0
        self.cache_hits = 0
        self._country: dict[str, str | None] = {}

    @property
    def available(self) -> bool:
        return bool(self._key)

    def request_body(self, query: LeadQuery, context: LeadContext) -> dict:
        primary, other = policy_domains(context.policy)
        site_domain = next(
            (
                match.group(1).lower().rstrip(".")
                for match in re.finditer(r"(?:^|\s)site:([A-Za-z0-9.-]+)", query.text)
            ),
            None,
        )
        body: dict = {
            "query": query.text,
            "search_depth": "basic",
            "topic": "general",
            "max_results": self.max_results,
            "include_usage": True,
        }
        if site_domain:
            body["include_domains"] = [site_domain]
            body["include_domains_mode"] = "restrict"
        elif primary and context.iteration == 1:
            body["include_domains"] = primary[:300]
            body["include_domains_mode"] = "restrict"
        elif primary or other:
            body["include_domains"] = (primary + other)[:300]
            body["include_domains_mode"] = "prefer"
        jurisdiction = str(context.policy.get("jurisdiction") or "")
        if jurisdiction and self.country_resolver is not None:
            if jurisdiction not in self._country:
                self._country[jurisdiction] = self.country_resolver(jurisdiction)
            if self._country[jurisdiction]:
                body["country"] = self._country[jurisdiction]
        return body

    def _post(self, body: dict) -> dict:
        key = JsonCache.fingerprint("tavily", body)
        cached = self.cache.get("tavily", key)
        if cached is not None:
            self.cache_hits += 1
            cached["_cached"] = True
            return cached
        if not self.available:
            raise ProviderUnavailable("TAVILY_API_KEY is not set")
        cost = 2 if body.get("search_depth") == "advanced" else 1
        if self.credits + cost > self.max_credits:
            raise ProviderUnavailable("Tavily credit budget spent")
        response = self.http.post(
            self.URL,
            json=body,
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
        )
        if response.status_code != 200:
            # Status only: never echo the request or anything that could carry the key.
            raise ProviderUnavailable(f"tavily http_{response.status_code}")
        payload = response.json()
        used = payload.get("usage", {}).get("credits")
        self.credits += used if isinstance(used, int) and used >= 0 else cost
        self.cache.put("tavily", key, payload)
        return payload

    def leads(self, context: LeadContext) -> list[Lead]:
        found: list[Lead] = []
        for query in context.queries:
            body = self.request_body(query, context)
            credits_before = self.credits
            try:
                payload = self._post(body)
            except ProviderUnavailable as exc:
                self._attempt(
                    query.text,
                    str(exc),
                    0,
                    credits=self.credits - credits_before,
                    credits_used=self.credits,
                    credit_cap=self.max_credits,
                )
                continue
            except (httpx.HTTPError, ValueError) as exc:
                self._attempt(
                    query.text,
                    f"error: {type(exc).__name__}",
                    0,
                    credits=self.credits - credits_before,
                    credits_used=self.credits,
                    credit_cap=self.max_credits,
                )
                continue
            leads = parse_tavily(payload, query)
            found.extend(leads)
            self._attempt(
                query.text,
                "ok" if leads else "empty",
                len(leads),
                credits=self.credits - credits_before,
                credits_used=self.credits,
                credit_cap=self.max_credits,
                cached=bool(payload.get("_cached")),
                include_domains_mode=body.get("include_domains_mode"),
                country=body.get("country"),
            )
        return found


def parse_tavily(payload: Mapping, query: LeadQuery) -> list[Lead]:
    found = []
    for item in payload.get("results", []):
        url = str(item.get("url") or "")
        if not public_url(url):
            continue
        score = item.get("score")
        found.append(
            Lead(
                url=url,
                title=str(item.get("title") or url)[:300],
                snippet=" ".join(str(item.get("content") or "").split())[:600],
                discovered_by="tavily",
                query=query.text,
                property_ids=(query.property_id,),
                score=float(score) if isinstance(score, (int, float)) else 0.0,
            )
        )
    return found


class SearchClientLeadProvider(LeadProvider):
    """Adapt an existing captured-result search client; its results are leads too."""

    def __init__(self, client: object) -> None:
        super().__init__()
        self.client = client
        self.name = getattr(client, "name", None) or "web_search"
        self.trace: list[dict] = []
        self.jobs: list[dict] = []

    def leads(self, context: LeadContext) -> list[Lead]:
        found: list[Lead] = []
        for query in context.queries:
            trace_before = len(getattr(self.client, "trace", []))
            jobs_before = len(getattr(self.client, "jobs", []))
            try:
                results = tuple(self.client.search(query.text))
                outcome = "ok" if results else "empty"
            except PROVIDER_ERRORS as exc:  # blocked or failed search engines fall through
                results, outcome = (), f"error: {type(exc).__name__}"
            self.trace.extend(getattr(self.client, "trace", [])[trace_before:])
            self.jobs.extend(getattr(self.client, "jobs", [])[jobs_before:])
            metadata = getattr(self.client, "result_metadata", {})
            for result in results:
                if not public_url(result.url):
                    continue
                provider = metadata.get(result.url, {}).get("provider", self.name)
                found.append(
                    Lead(
                        url=result.url,
                        title=result.title,
                        snippet=result.snippet,
                        discovered_by=provider,
                        query=query.text,
                        property_ids=(query.property_id,),
                    )
                )
            self._attempt(query.text, outcome, len(results))
        return found


def default_lead_providers(
    decision: DecisionClient,
    *,
    cache_root: Path | None = DEFAULT_CACHE,
    tavily_max_credits: int | None = None,
    remaining_budget_usd: float | None = None,
    search_client: object | None = None,
    fetch_json: Callable[[str, str], Mapping] | None = None,
) -> list[LeadProvider]:
    """Live providers in the order the loop consults them."""
    cache = JsonCache(cache_root)
    wikidata = WikidataClient(cache=cache)
    providers: list[LeadProvider] = []
    if decision.backend == "vultr":
        providers.append(ModelLeadProvider(decision))
    providers.append(WikidataLeadProvider(wikidata))
    providers.append(CkanLeadProvider(fetch_json=fetch_json, cache=cache))
    credits = tavily_max_credits
    if credits is None:
        override = os.environ.get("ONTOFILL_TAVILY_MAX_CREDITS", "").strip()
        credits = int(override) if override else tavily_credit_cap_for_budget(remaining_budget_usd)
    tavily = TavilyLeadProvider(
        cache=cache, max_credits=credits, country_resolver=wikidata.country_name
    )
    if tavily.available and decision.backend == "vultr":  # mock runs never spend credits
        providers.append(tavily)
    if search_client is not None:
        providers.append(SearchClientLeadProvider(search_client))
    return providers
