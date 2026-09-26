# Tavily: search API reference (discovery provider)

Tavily is a keyed, LLM-oriented search API. In Ontofill it becomes a pluggable **discovery provider**
for Phase 3 fan-out: it proposes candidate publishers for an ontology gap (`discovered_by: "tavily"`),
but it never produces evidence — every candidate still needs a sandboxed-browser capture and an
authority check before it counts. Tavily's own servers fetch pages on your behalf (for Extract, Crawl,
Map, and for `include_raw_content`/`include_answer` on Search); none of that traffic goes through our
sandbox's egress allowlist or proof checkpoints, so treat everything Tavily returns as a **lead**, never
as a capture.

Key: `TAVILY_API_KEY` (env, also the Python SDK's default). Research date: 2026-09-26. SDK: `tavily-python`
0.8.4 (PyPI, checked 2026-09-26), repo `github.com/tavily-ai/tavily-python` (MIT). Three tiny live calls
against the real key are cited inline as **(live-verified 2026-09-26)**; everything else is from the pages
below.

**Sources** (all public; fetched 2026-09-26).

| Tag | URL |
|---|---|
| [AUTH] | https://docs.tavily.com/documentation/api-reference/introduction |
| [SEARCH] | https://docs.tavily.com/documentation/api-reference/endpoint/search |
| [EXTRACT] | https://docs.tavily.com/documentation/api-reference/endpoint/extract |
| [CRAWL] | https://docs.tavily.com/documentation/api-reference/endpoint/crawl |
| [MAP] | https://docs.tavily.com/documentation/api-reference/endpoint/map |
| [USAGE] | https://docs.tavily.com/documentation/api-reference/endpoint/usage |
| [CREDITS] | https://docs.tavily.com/documentation/api-credits |
| [RATELIMITS] | https://docs.tavily.com/documentation/rate-limits |
| [BP-SEARCH] | https://docs.tavily.com/documentation/best-practices/best-practices-search |
| [BP-EXTRACT] | https://docs.tavily.com/documentation/best-practices/best-practices-extract |
| [BP-CRAWL] | https://docs.tavily.com/documentation/best-practices/best-practices-crawl |
| [PRIVACY] | https://docs.tavily.com/documentation/privacy, https://www.tavily.com/privacy |
| [TERMS] | https://www.tavily.com/terms |
| [PYPI] | https://pypi.org/project/tavily-python/ |
| [GH] | https://github.com/tavily-ai/tavily-python — README and `tavily/tavily.py`, `tavily/errors.py` source, fetched via `raw.githubusercontent.com` |

---

## 0. Gotchas (read first)

| # | Gotcha | What to do |
|---|---|---|
| G1 | **`search_depth` has four values, not two.** `basic`, `fast`, `ultra-fast` (all 1 credit) and `advanced` (2 credits, "highest relevance, increased latency"). [SEARCH] Older blog posts and SDK examples from before this only mention basic/advanced. | Default to `basic`. Reach for `advanced` only when a gap resists — don't reach for `fast`/`ultra-fast` for discovery; they trade relevance for latency we don't need. |
| G2 | **The SDK auto-reads `TAVILY_API_KEY`.** `TavilyClient()` with no args falls back to the env var, and with *no key and no session* it runs in **keyless mode** (rate-limited, `search`/`extract` only — `crawl`/`map`/`research` raise `KeylessUnsupportedEndpointError`). [GH] | Fine to instantiate `TavilyClient()` bare in code that also runs keyless-degraded; but for Crawl/Map you must pass a real key. |
| G3 | **The SDK does not auto-retry.** Every method (`search`, `extract`, `crawl`, `map`) takes only a `timeout` (capped at 120s for search); there is no `max_retries` anywhere in `tavily/tavily.py`. 429 raises `UsageLimitExceededError`; 401 → `InvalidAPIKeyError`; 400 → `BadRequestError`; **403, 432 and 433 all raise the same `ForbiddenError`** (plan-limit and PAYGO-limit are indistinguishable by exception type — inspect `str(e)`/status). [GH] [SEARCH] | Wrap every call in your own retry/backoff (§6). Don't rely on catching a specific 432-vs-433 exception class. |
| G4 | **`chunks_per_source` isn't a named `search()` kwarg in the SDK**, only in `extract`/`crawl`/`map`. [GH] It **is** a documented raw Search API field (1–3, default 3). [SEARCH] | Pass it as a kwarg anyway — the SDK forwards unknown kwargs straight into the request body (`**kwargs` → `data.update(kwargs)`). Or just call the REST endpoint directly. |
| G5 | **`country` takes a full lowercased country name, not an ISO code** (e.g. `"mexico"`, not `"mx"`), and only applies when `topic="general"`. [SEARCH] Live-verified: `{"country": "mexico", "topic": "general"}` returned 200. | For procurement discovery, leave `topic` at its `general` default so `country` stays usable. |
| G6 | **`include_domains` is a substring/suffix match, not a regex, and it doesn't guarantee authority.** Live-verified: `include_domains: ["gob.mx"], include_domains_mode: "restrict"` returned real subdomains (`www.spr.gob.mx`, `dsiappsdev.semarnat.gob.mx`, `directoriosancionados.buengobierno.gob.mx`) — i.e. a bare eTLD+1-style suffix does steer toward a whole government zone. But a domain-free query on the same topic also surfaced `facebook.com` in position 3 with a similar relevance `score` — **`score` is a relevance signal, not a trust signal.** [SEARCH] | Always run the authority check in code after Tavily, even inside `.gob.mx`; never treat a high `score` as a pass. |
| G7 | **Crawl's own SDK README example says "currently available on an invite-only basis"** and points at `crawl.tavily.com`, but the current `[CRAWL]` API reference page has no such restriction and describes it as a normal keyed endpoint. [GH] [CRAWL] | Treat Crawl as generally available but budget one live call to confirm your key isn't gated before relying on it — **UNVERIFIED** which statement is current. |
| G8 | **Map/Crawl can return zero results on JS-rendered government sites, and cost 0 credits when they do.** Live-verified: `POST /map` on a `.gob.mx` SPA-style directory returned `{"results": [], "usage": {"credits": 0}}` after an 8.9s round trip. [MAP] ("The value may be 0 if the total successful pages mapped has not yet reached 10 [pages].") | Don't use an empty Map/Crawl result as a negative signal about the source's authority — it may just mean the crawler couldn't render the page. Zero cost means you can try cheaply, though. |
| G9 | **`include_domains` (Search) caps at 300 entries, `exclude_domains` at 150** [SEARCH]; **Map/Crawl's `select_domains`/`exclude_domains` are regex patterns**, a different mechanism from Search's plain-string `include_domains`. [MAP] [CRAWL] | Don't copy a Search `include_domains` list straight into a Map/Crawl call expecting the same matching semantics — Map/Crawl domain filters need regex syntax (e.g. `.*\.gob\.mx$`). |
| G10 | **Crawl's cost is Map-cost + Extract-cost added together**, not one flat rate: "crawling 10 pages with basic extraction costs 3 total credits (1 for mapping + 2 for extraction)." [CREDITS] | Budget Crawl calls as `ceil(pages/10) + ceil(pages/5)*[1 or 2]`, not a single per-page number. |
| G11 | **429/432/433 all carry a `Retry-After` header**, but the docs explicitly warn the *message text* can vary — match on status + header, not on string content. [RATELIMITS] | Branch on `response.status_code`, never on the error body's prose. |

---

## 1. Auth, base URL, and endpoints

| Item | Value |
|---|---|
| Base URL | `https://api.tavily.com` [AUTH] |
| Auth header | `Authorization: Bearer tvly-YOUR_API_KEY` [AUTH] |
| Key format | Prefixed `tvly-...`, issued free (no card) from https://app.tavily.com [AUTH] [CREDITS] |
| Endpoints | `POST /search`, `POST /extract`, `POST /crawl`, `POST /map`, `GET /usage`, `POST /research` (async report generation), `POST /feedback` — this doc covers Search/Extract/Crawl/Map/Usage in depth; Research and Feedback only in passing (out of scope for discovery). [SEARCH] [EXTRACT] [CRAWL] [MAP] [USAGE] [GH] |
| Keyless mode | `TavilyClient()` with no key/session works for `search`/`extract` only, rate-limited; raises `TavilyKeylessLimitError` (carries `.retry_after_seconds`, `.next_actions`) when capped. [GH] |

## 2. `POST /search` — full parameter reference [SEARCH]

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — | Required. Keep under ~1500 chars; phrase like a web query, not a prompt. [BP-SEARCH] |
| `search_depth` | `basic`\|`fast`\|`ultra-fast`\|`advanced` | `basic` | Latency/relevance/cost tradeoff (G1). |
| `topic` | `general`\|`news`\|`finance` | `general` | `country` only works with `general`. |
| `max_results` | int | 10 | Range 0–20. |
| `chunks_per_source` | int | 3 | 1–3; only affects `advanced`/`basic`/`fast` content generation (G4). |
| `include_raw_content` | bool \| `"markdown"`\|`"text"` | `false` | Adds cleaned full-page content per result. |
| `include_answer` | bool \| `"basic"`\|`"advanced"` | `false` | LLM-generated summary answer. |
| `include_images`, `include_image_descriptions` | bool | `false` | |
| `include_favicon` | bool | `false` | |
| `include_published_date` | bool | `false` | Adds `published_date` per result — useful for pre-ranking (§4). |
| `filter_by_published_date` | bool | `false` | Hard filter vs. soft boost. |
| `time_range` | `day`\|`week`\|`month`\|`year` (or `d`/`w`/`m`/`y`) | none | |
| `start_date`, `end_date` | `YYYY-MM-DD` | none | |
| `include_domains` | array (≤300) | `[]` | See G6, G9. |
| `exclude_domains` | array (≤150) | `[]` | |
| `include_domains_mode` | `restrict`\|`prefer` | none (behaves as `restrict` once `include_domains` is set) | `prefer` still searches the rest of the web so results outside the list can surface; requires `include_domains` or 400. |
| `country` | string (full name, lowercased) | none | `general` topic only (G5); ~160 countries supported per community docs, exact list **UNVERIFIED** in the fetched pages. |
| `language` | ISO 639-1 code or English name | none | "search ranking is optimized when the query and target language match" — i.e. also write the query itself in Spanish. [BP-SEARCH] |
| `filter_by_language` | bool | `false` | Strict filter vs. boost; strict can shrink coverage. |
| `exact_match` | bool | `false` | Only return results containing the exact quoted phrase. |
| `auto_parameters` | bool | `false` | Tavily auto-picks depth/etc.; costs 2 credits if it picks `advanced`. |
| `safe_search` | bool | `false` | Not available on `fast`/`ultra-fast`. |
| `include_usage` | bool | `false` | Adds a `usage.credits` field to the response — use it, it's free to ask for. |

**Response shape** (top level): `query`, `answer`, `images[]`, `results[]`, `response_time`, `usage`, `request_id`,
`auto_parameters`. Each `results[]` item: `title`, `url`, `content` (snippet), `score` (float, relevance — not
authority, G6), `id`, plus optional `raw_content`, `published_date`, `favicon`, `images[]`. [SEARCH]

Live-verified request/response (Search, basic depth, `country: "mexico"`, `language: "es"`, `max_results: 3`):
one credit charged, three results returned including one government domain match and one low-authority
`facebook.com` result at a similar score — see G6.

## 3. `POST /extract`, `POST /crawl`, `POST /map`, `GET /usage`

| | Extract [EXTRACT] | Crawl [CRAWL] | Map [MAP] | Usage [USAGE] |
|---|---|---|---|---|
| Required | `urls` (1–20) | `url` (root) | `url` (root) | — |
| Depth/quality knob | `extract_depth`: `basic`\|`advanced` | `extract_depth`: `basic`\|`advanced` | — (no content extraction) | — |
| Steering | `query` (reranks chunks), `chunks_per_source` (1–5) | `instructions` (NL guidance), `select_paths`/`select_domains`/`exclude_paths`/`exclude_domains` (**regex**, G9), `max_depth` (≤5), `max_breadth` (1–500), `limit`, `allow_external` | same steering params as Crawl, minus extraction | `X-Project-ID` header scopes to a project |
| Output format | `format`: `markdown`\|`text` | `format`: `markdown`\|`text` | — (URLs only) | — |
| Timeout | 1.0–60.0s | up to 150s | 10–150s (default 150) | — |
| Response | `results[]{url, raw_content, images[], favicon}`, `failed_results[]`, `usage`, `request_id` | `base_url`, `results[]{url, raw_content, favicon}`, `usage`, `request_id` | `base_url`, `results[]` (URL list), `usage`, `request_id` | key-level: total credits used/limit + per-endpoint breakdown; account-level: plan name, plan usage/limit, PAYGO usage/limit |

Live-verified: `POST /map` on a confirmed `.gob.mx` sanctions-directory page (`max_depth:1, limit:10`) returned
`results: []` and `usage.credits: 0` after 8.9s — see G8.

## 4. Credits, pricing, and rate limits

| Endpoint | Cost | Source |
|---|---|---|
| Search — `basic`/`fast`/`ultra-fast` | 1 credit/request | [CREDITS] [SEARCH] |
| Search — `advanced` (or `auto_parameters` picking advanced) | 2 credits/request | [CREDITS] |
| Extract — `basic` | 1 credit per 5 successful URLs | [CREDITS] [EXTRACT] |
| Extract — `advanced` | 2 credits per 5 successful URLs | [CREDITS] |
| Map — no `instructions` | 1 credit per 10 successful pages | [CREDITS] [MAP] |
| Map — with `instructions` | 2 credits per 10 successful pages | [CREDITS] [MAP] |
| Crawl | Map-cost + Extract-cost, added (G10); e.g. 10 pages, basic extract, no instructions ≈ 3 credits | [CREDITS] |
| Research | `model=pro`: 15–250 credits; `model=mini`: 4–110 credits, dynamic | [CREDITS] (out of scope here) |
| Failed extractions/crawls/maps | Not charged | [EXTRACT] [MAP] |

| Plan | Credits | Notes |
|---|---|---|
| Free ("Researcher") | 1,000 credits/month, no card required | [CREDITS] [AUTH] |
| Paid tiers | $0.005–$0.008/credit depending on plan; e.g. Growth $500/mo for 100,000 credits | [CREDITS] |
| Enterprise | Custom pricing | [CREDITS] |

A hackathon key is almost certainly the free 1,000-credit tier unless someone explicitly upgraded it —
confirm with `GET /usage` rather than assuming (cheap: 1 call per ~10 minutes, see rate limit below).
**Whether "hackathon key" implies anything beyond the standard free tier is UNVERIFIED.**

**Rate limits** [RATELIMITS]:

| Scope | Development | Production |
|---|---|---|
| Standard endpoints (Search, Extract) | 100 req/min | 1,000 req/min |
| Crawl | 100 req/min (both tiers) | |
| Research | 20 req/min (both tiers) | |
| Usage | 10 req / 10 min (both tiers) | |

429/432/433 all carry `Retry-After` (seconds); "Production access requires either an active Paid Plan or
PAYGO enabled." [RATELIMITS] No documented concurrency (parallel in-flight request) cap — **UNVERIFIED**.

## 5. Steering discovery toward official publishers

| Goal | How | Source |
|---|---|---|
| Bias toward one country's official web | `country: "<lowercased name>"` + `topic: "general"` (G5) | [SEARCH] |
| Hard-restrict to a government zone | `include_domains: ["gob.mx"]` (or similar apex/suffix), `include_domains_mode: "restrict"` — matches the whole zone, not just the exact domain (G6, live-verified) | [SEARCH] |
| Prefer official domains but don't starve results | `include_domains_mode: "prefer"` — same list, but non-listed results can still surface if the zone is thin | [SEARCH] |
| Spanish-language results | Write the **query itself in Spanish** (ranking is tuned to query-language match) and set `language: "es"`; add `filter_by_language: true` only if leakage of English results is a real problem, since it can shrink coverage | [BP-SEARCH] [SEARCH] |
| Keep cost low | Default `search_depth: "basic"` (1 credit); reserve `advanced` (2 credits) for gaps that come back empty at `basic`; ask for `include_usage: true` (free) to track spend per call; skip `include_raw_content`/`include_answer` for a discovery pass — you only need `title`/`url`/`content`/`score` to build a candidate list | [BP-SEARCH] [CREDITS] |
| Pre-rank candidates without another call | `score` (relevance, not authority — G6), `content` (snippet, enough for a keyword/authority-policy check), `published_date` (only if `include_published_date: true` is set — off by default) | [SEARCH] |

**Query phrasing for "who publishes X for jurisdiction Y":** best-practices docs recommend short, web-style
queries over long natural-language prompts. [BP-SEARCH] A generic template like
`"<property> official publisher <jurisdiction>"` or, in Spanish, `"quién publica <property> en <jurisdiction>"`
worked in the live test and surfaced the right government domain in position 1 (`score` 0.87) — see §2.

## 6. Extract / Crawl / Map vs. our sandboxed-evidence rule

Our rule: every **evidence** capture happens inside our sandboxed browser (egress allowlist, proof
checkpoints). Tavily's Extract/Crawl/Map endpoints fetch pages from **Tavily's own infrastructure**, not
ours — so anything they return is outside that boundary by construction, no matter how "raw" the content
looks. [EXTRACT] [CRAWL] [MAP] describe server-side fetching; none mention routing through a caller-supplied
proxy or browser.

| Endpoint | Can it help pre-sandbox? | Why / why not |
|---|---|---|
| **Map** | Yes, for site-graph seeding. Returns a URL list cheaply (1 credit/10 pages, 0 if it finds nothing — G8) — good for widening the site graph on a *confirmed* source before a sandbox crawl decides what to actually capture. | Never treat its URL list as evidence that a page exists or is reachable in our sandbox; the sandbox still has to visit each URL itself. Also fails silently (empty, still cheap) on JS-rendered directories (G8), so it's a hint generator, not a completeness guarantee. |
| **Extract** | Yes, for cheap pre-screening of *candidates*, not confirmed sources — e.g. deciding which 3 of 20 Search hits are worth a sandbox visit at all, using `extract_depth: "basic"` (1 credit/5 URLs). [BP-EXTRACT] | The extracted text can inform triage/ranking but must never be cited as the evidence itself — it never touched our sandbox, so it can't satisfy a proof checkpoint. |
| **Crawl** | Marginal for us. It duplicates what a sandboxed site-walk already does, at Map+Extract combined cost (G10), and its content still isn't sandbox evidence. | Prefer Map (cheap structure) + our own sandbox crawl (evidence) over paying for Tavily's Crawl to do both at once outside the sandbox. |
| **Search** | This is the actual discovery step — a candidate list, nothing more. | Always the entry point; never terminal. |

Net: **Map earns its keep** (cheap site-graph input on a source that's already passed the authority check).
**Extract earns its keep only as pre-screening**, filtering candidates before they cost a sandbox visit —
never as a substitute for one. **Crawl is the weakest fit** given our architecture; skip it unless a specific
gap needs Tavily's own semantic `instructions`-guided traversal and the team accepts that cost is roughly
map-cost-plus-extract-cost for content that still isn't evidence.

## 7. Python examples

All three use `httpx`, read `TAVILY_API_KEY` from env, apply a timeout, retry on 429/5xx with backoff, and
fall back to `None` (letting the caller move to the next discovery provider) on exhaustion — following the
same fallback shape as `docs/reference/jev.md` §6.

```python
import os, time, random, httpx

TAVILY_URL = "https://api.tavily.com"
_http = httpx.Client(base_url=TAVILY_URL, timeout=httpx.Timeout(10.0, connect=3.0))

class TavilyUnavailable(Exception): ...

def _tavily_post(path: str, body: dict, max_retries: int = 3) -> dict:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise TavilyUnavailable("TAVILY_API_KEY not set")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for attempt in range(max_retries + 1):
        try:
            r = _http.post(path, headers=headers, json=body)
        except httpx.HTTPError as e:
            if attempt == max_retries:
                raise TavilyUnavailable(repr(e)) from e
            time.sleep(min(2 ** attempt + random.random(), 20)); continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
            wait = float(r.headers.get("Retry-After", 2 ** attempt))  # G11: status/header, not message text
            time.sleep(min(wait, 30)); continue
        raise TavilyUnavailable(f"HTTP {r.status_code}: {r.text[:300]}")
    raise TavilyUnavailable("retries exhausted")
```

**(a) Discovery query for an ontology gap → ranked candidate publishers**

```python
def discover_publishers(gap: dict, max_results: int = 5) -> list[dict]:
    """gap = {'class': ..., 'property': ..., 'jurisdiction': ..., 'language': 'es'}"""
    query = f"quién publica {gap['property']} de {gap['class']} en {gap['jurisdiction']}" \
            if gap.get("language", "en").startswith("es") else \
            f"official publisher of {gap['property']} for {gap['class']} in {gap['jurisdiction']}"
    body = {"query": query, "search_depth": "basic", "topic": "general",
            "max_results": max_results, "include_usage": True,
            "language": gap.get("language"), "include_published_date": True}
    data = _tavily_post("/search", {k: v for k, v in body.items() if v is not None})
    return [{"url": r["url"], "title": r["title"], "snippet": r["content"],
              "score": r["score"], "published_date": r.get("published_date"),
              "discovered_by": "tavily", "query": query} for r in data["results"]]
```

**(b) Domain-restricted query**

```python
def discover_in_zone(gap: dict, zones: list[str], max_results: int = 5) -> list[dict]:
    """zones: e.g. ['gob.mx'] — apex/suffix strings, matched as substrings by Tavily (G6)."""
    query = f"{gap['property']} {gap['class']} {gap['jurisdiction']}"
    body = {"query": query, "search_depth": "basic", "max_results": max_results,
            "include_domains": zones, "include_domains_mode": "restrict", "include_usage": True}
    data = _tavily_post("/search", body)
    return [{"url": r["url"], "domain": r["url"].split("/")[2], "score": r["score"],
              "discovered_by": "tavily", "query": query} for r in data["results"]]
```

**(c) Map call on a confirmed source**

```python
def map_confirmed_source(root_url: str, max_depth: int = 1, limit: int = 30) -> list[str]:
    """Only call this on a URL that already passed the authority check — Map results feed the
    site graph, they are not themselves evidence (see §6)."""
    body = {"url": root_url, "max_depth": max_depth, "limit": limit, "include_usage": True}
    data = _tavily_post("/map", body)
    return [r["url"] if isinstance(r, str) else r.get("url") for r in data.get("results", [])]
```

Equivalent SDK form (auto-reads `TAVILY_API_KEY`, no built-in retry — G3, so still wrap it):

```python
from tavily import TavilyClient, UsageLimitExceededError, ForbiddenError, InvalidAPIKeyError

client = TavilyClient()  # reads TAVILY_API_KEY from env; keyless if unset (G2)

def discover_publishers_sdk(gap: dict, max_results: int = 5) -> list[dict] | None:
    try:
        resp = client.search(query=f"{gap['property']} {gap['class']} {gap['jurisdiction']}",
                              search_depth="basic", language=gap.get("language"),
                              max_results=max_results, include_usage=True)
    except (UsageLimitExceededError, ForbiddenError, InvalidAPIKeyError, Exception):
        return None  # caller falls back to Wikidata / model-proposed / open-data catalog
    return resp["results"]
```

## 8. Provider adapter sketch

No property/jurisdiction vocabulary is hard-coded; the caller's `gap` and `budget` carry every
domain-specific detail (query phrasing inputs, target zones, language, depth/cost ceiling).

```python
from dataclasses import dataclass

@dataclass
class Candidate:
    url: str
    domain: str
    title: str
    snippet: str
    score: float
    discovered_by: str
    query: str

class TavilyProvider:
    """Discovery-only. Never returns evidence; every candidate still needs sandbox confirmation
    and the authority-policy check (§ Discovery, docs/planning/03-technical-architecture.md §2)."""
    name = "tavily"

    def __init__(self, api_key: str | None = None, timeout: float = 10.0, max_retries: int = 3):
        self._key = api_key or __import__("os").environ.get("TAVILY_API_KEY")
        self._timeout, self._max_retries = timeout, max_retries

    @property
    def available(self) -> bool:
        return bool(self._key)

    def discover(self, gap: dict, budget: dict) -> list[Candidate]:
        """gap: {'class', 'property', 'jurisdiction', 'language'} — no assumptions about their values.
        budget: {'max_results', 'search_depth', 'include_domains', 'include_domains_mode',
                 'country', 'max_credits'} — all optional, all caller-supplied."""
        if not self.available:
            return []
        query = self._build_query(gap)
        body = {
            "query": query,
            "search_depth": budget.get("search_depth", "basic"),
            "max_results": min(budget.get("max_results", 5), 20),
            "language": gap.get("language"),
            "country": budget.get("country"),
            "include_domains": budget.get("include_domains"),
            "include_domains_mode": budget.get("include_domains_mode"),
            "include_usage": True,
        }
        body = {k: v for k, v in body.items() if v is not None}
        try:
            data = _tavily_post("/search", body, max_retries=self._max_retries)
        except TavilyUnavailable:
            return []
        return [
            Candidate(url=r["url"], domain=r["url"].split("/")[2] if "://" in r["url"] else r["url"],
                       title=r.get("title", ""), snippet=r.get("content", ""), score=r.get("score", 0.0),
                       discovered_by=self.name, query=query)
            for r in data.get("results", [])
        ]

    @staticmethod
    def _build_query(gap: dict) -> str:
        # A generic slot-fill template — swap languages/phrasing per gap['language'], never per domain.
        parts = [p for p in (gap.get("property"), gap.get("class"), gap.get("jurisdiction")) if p]
        return " ".join(parts) if parts else gap.get("class", "")
```

## 9. Data retention, privacy, and terms

| Item | Documented | Source |
|---|---|---|
| Use of query content | Queries retrieve web content on your behalf; legal basis under EU/UK data-protection law is "performance of a contract." Tavily "may use certain portions of query data to improve responses to future queries" unless a contract says otherwise. | [PRIVACY] |
| Retention | Deletes/anonymizes personal information "when it is no longer reasonably necessary" for disclosed purposes, considering statute, litigation holds, limitation periods and industry practice; **no fixed number of days is stated for ordinary API request bodies.** Zero-data-retention is mentioned as an enterprise/contractual option, not a default. | [PRIVACY] |
| Certifications | SOC 2 (Type II referenced) and ISO/IEC 27001:2022 mentioned; a Trust Center exists for compliance docs. | [PRIVACY] |
| Terms | Platform Terms of Service incorporate the Privacy Policy by reference. | [TERMS] |
| Our posture | Queries here are public-interest research terms (which government or open-data body publishes X) — no personal or customer data leaves in the query text. Fine to send under this policy; **do not** put case-specific personal data (a named individual under investigation, etc.) into a Tavily query, since retention/training language is not a hard zero. | — |

## UNVERIFIED summary

1. Whether the current Crawl endpoint is fully generally available or still gated — the SDK README says
   "invite-only," the live API-reference page for the same endpoint does not (G7).
2. The exact number and list of ~160 `country` values, and finance/news-topic geographic filtering — only
   confirmed that `country` requires `topic: general`.
3. Any documented concurrency (parallel in-flight requests) limit — only RPM figures are documented.
4. Whether a hackathon-issued key differs from the standard 1,000-credit free tier (worth one `GET /usage`
   call to confirm rather than assuming).
5. A fixed retention period for ordinary (non-enterprise-ZDR) API request/response bodies.
6. Whether `include_domains`/`exclude_domains` on Search support true TLD-suffix wildcards (e.g. `*.gob.mx`)
   versus the plain substring match observed live with a bare `"gob.mx"` entry — behavior for a leading-dot
   or wildcard form specifically was not tested.
