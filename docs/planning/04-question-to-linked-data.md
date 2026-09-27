# From an open question to linked data (Ontofill's process)

A plain account of what the engine does, stage by stage, and what each stage leaves behind as evidence. The canonical
architecture is `03-technical-architecture.md`. Each stage below states its status honestly.

```
question → PRD → ontology → search → spiders → extraction → gold with receipts (linked data)
```

The ontology comes BEFORE the search: the data standard decides what to look for. That ordering is what makes the
engine generic: swap the question, and the PRD, standard, sources and extraction all change with zero code changes.

## 1. Question → PRD (a contract)
- **Input:** `case/brief.md`, the only human-written input (one open question).
- **The engine:** it drafts a PRD with personas, jobs to be done, requirements, non-goals, testable definition-of-done
  criteria and an authority policy (which publishers count as official, and in which tier). The draft runs in a
  bounded loop: the planning model drafts, a critic from another model family objects (invented targets,
  feasibility, coverage), the draft is revised, and code checks it.
- **Human gate:** the approver approves, or denies with a reason. A denial regenerates the PRD with that reason as a
  human revision, and each target records its basis (brief / human / proposed) with the exact quote it came from.
- **Evidence left:** `01-scope/prd.json`, `revisions/<n>/`, `decisions.jsonl` (digest-bound, SSO-group-verified), and
  loop trace steps. **Status:** live on Vultr; the user denied two drafts and each was regenerated.

## 2. PRD → ontology (the data standard)
- **The engine:** it identifies factors of variation, then taxonomies, then classes, properties, relations and a
  primary class, SHACL shapes, rules, alignment to open vocabularies (schema.org / Wikidata / a domain standard), and
  the definition of done compiled into declarative queries.
- **Human gate:** the approver accepts or rejects each factor, then the ontology.
- **Evidence left:** `02-ontology/*` (ontology.json, shapes, taxonomies, dod queries). **Status:** built (Simula at
  depth 1 with a critic; depth 2 is cut). Grounding against sampled pages is in progress.

## 3. Ontology → search (discovery)
- **The engine:** each ontology property still missing becomes targeted queries. Lead-only providers: the Vultr model
  proposes publishers, Wikidata supplies official websites, CKAN searches catalogs, and Tavily searches the web
  restricted to the policy's domains and country. **A lead is never evidence:** a sandbox capture must confirm it,
  then a critic must accept it, then the authority policy must pass it; unknown publishers go to human review.
- **Evidence left:** `03-fanout/objectives.yaml` (each source with `discovered_by`, authority tier and the confirming
  bronze capture), `surface-map/leads.json`. **Status:** built and verified (gap R7).

## 4. Search → spiders (site graphs)
- **The engine:** it crawls each confirmed source inside a gVisor cell (same domain, depth 2, page cap, robots.txt,
  read-only GETs, never a form). Pages are grouped into types (URL template + DOM skeleton), each type is labeled
  against the ontology (listing, detail of a class, search, download), and each carries property hints.
- **Evidence left:** `03-fanout/surface-map/<source>/site-graph.json` (types, instances with bronze captures, edges,
  coverage by property, crawl limits). It ranks sources and seeds each per-source plan. **Status:** in progress (R15).

## 5. Spiders → extraction (per-source plans and execution)
- **The engine:** a technical plan per (source, objective) sets target properties, path, method, allowed domains,
  budget and the execution-mode range. Execution climbs a ladder:
  - **D0:** plain download and parse (no model);
  - **D1:** a crystallized script where the model only maps fields;
  - **S1:** an agentic browser loop (look → Jev check → Vultr plan → action guard → act → Vultr vision verify);
  - **S2:** Skyvern.
  Failing extractors repair themselves (write → test in a sandbox → read stderr → patch; Pattern A) and get promoted
  to macros, so the next run is cheaper. Complete official lists yield evidence-backed "absent from the list" values.
- **Every action runs in a disposable gVisor cell** with six proof checkpoints (host, task, where, isolation BLOCKED,
  teardown, secrets 0). The live view is a per-session `netbird expose` URL that dies with the cell.
- **Evidence left:** bronze captures (screenshot, HTML, files; content-addressed), `trace.live.jsonl` (observed /
  requested / executed / evaluated per step, mode, verdict, cost), `jobs.jsonl`. **Status:** D0 multi-source +
  membership (R2) and S1 on the engine path (R3) are verified; Pattern A in runs (R4), sandbox-only parsing (R17) and
  browser-death handling (R16) are in progress.

## 6. Extraction → gold with receipts (linked data)
- **Refine:** silver keeps every typed observation with its provenance, including conflicts (Postgres). Gold holds the
  reconciled, SHACL-valid values; entity resolution compares identifiers in code, and rule-derived signals and
  relationships come from the ontology's rules (R6, in progress). The outer loop compares gold to the definition of
  done and reopens discovery or planning for the gaps.
- **Linked data out:** `entities.jsonl` + `ontology.json` (classes, properties and relations aligned to open
  vocabularies) + `trace.jsonl` + `jobs.jsonl` + `metrics.json` (per-criterion DoD), plus open exports (RDF, CSV,
  OCDS for the procurement case). Every value carries its receipt: URL, selector, bronze screenshot, capture time
  and the step that produced it. Lineage runs value → evidence → step → plan → objective → ontology → PRD → brief.
- **Consumers:** the **Ontofill Console** (B2B; any case: runs, approvals, proofs, graphs) and case products such as
  **Proveedor Abierto** (B2C; one case's published gold, each value with its receipt).

## Why it holds up
- Nothing is hard-coded: sources are discovered, and the standard is derived from the question.
- Humans decide at the contract points (PRD, factors, ontology, risky actions), and every decision is digest-bound
  and logged.
- Models are spent only where the web is unpredictable (D0 → S2), and every call is attributed and costed.
- It's safe by construction: no browser or untrusted bytes in the app process, no long-lived key in any sandbox, and
  zero public ports.
