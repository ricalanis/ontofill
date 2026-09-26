# Ontofill: architecture and build plan (v5, lean build toward a field-complete run)

This file covers **how to build** the engine: data lake, architecture, schedule, demo and cuts. **What the engine is**, including the five phases, local scoping, the execution spectrum and the computer-use ontology mapping, is defined in `01-engine-definition.md`. The reference case is `02-anticorruption-definition.md`.

The five phases, briefly: (1) scope, meaning deep research, then personas and jobs to be done, then the global PRD; (2) ontology; (3) fan out, where spiders build site graphs and produce (source, objective) pairs; (4) local scoping per source and objective, producing a local PRD and a technical definition document through requester and source-engineer negotiation; (5) reactive execution across the deterministic-to-agentic spectrum.

> Phase 2 follows Simula (openreview NALsdGEPhB); see the "Phase 2 method" section in 01-engine-definition.md.

---

## The data lake: find and store, then refine
| Layer | What goes in | Where |
|-------|--------------|-------|
| **Bronze (raw)** | Everything captured, immutable and content-addressed: HTML, accessibility trees, screenshots, PDFs, HAR, and the spider site graphs | Vultr Object Storage (S3-compatible) |
| **Silver (observed)** | Typed candidate observations mapped to the ontology, each with provenance (bronze object, selector or crop). Duplicates and conflicts are kept. | Postgres + Oxigraph (named graph per source) |
| **Gold (refined)** | Reconciled, SHACL-valid values that pass the confidence bar. The Gold layer is what the global definition of done is measured on. | Oxigraph gold graph plus exported CSV |

Why this matters:
- When the ontology or a PRD changes, **re-refine from bronze without re-browsing**. That's cheap, fast, and a strong demo moment.
- Separation of concerns. Agents only have to *capture*. Refinement is deterministic plus LLM, and it can be audited.

**Ric's three metric axes, mapped onto the lake:**
1. **Extraction success:** job and site success rates and failure reasons, measured on bronze ingestion.
2. **Quantity and observation:** bronze objects and silver observations per site and per minute, how much is new versus duplicate, and budget burn.
3. **Quality and ontology completeness:** gold SHACL pass rate, cross-source agreement, and the percentage of each **local and global definition of done** met.

Loop: a local shortfall reopens that source's local scoping (Phase 4), and a global gold gap reopens fan-out (Phase 3). Spiders re-crawl when a site underperforms. New concepts found in silver become ontology proposals (Phase 2).

---

## Architecture
```
Browser ─> NetBird reverse proxy (PIN/SSO, zero inbound ports)
             ▼
VM #1 Control plane (Vultr)
  FastAPI · Postgres (PRDs, jobs, metrics) · Oxigraph (ontology, silver and gold graphs) · pySHACL
  P1 Researcher + PRD writer ─> P2 Ontologist ─> P3 Spider manager (site graphs, objectives)
  ─> P4 Local scoping (requester ⇄ source-engineer agents, probe pods) ─> P5 Reactive executor
  Executor: event bus, mode controller (D0/D1/S1/S2), option/macro store in the site graph
  Refiner (silver ─> gold, reconciliation, definition-of-done checks), with the feedback loops above
  LLMs ─> Vultr Serverless Inference: glm-5.3 / qwen3.8-27b, nemotron-omni (vision),
          vultron-retriever + bge-reranker (alignment/mapping), nemotron-3.5-content-safety
             ▼                                        ▼
VM #2 Sandbox host (Docker + gVisor)            Vultr Object Storage = bronze lake
  spider pods (fast, headless)                  (raw captures + site graphs)
  agent pods (Chromium + noVNC)
  egress limited per technical definition document; noVNC viewed through the app (no per-pod public URLs)
Burst: throwaway Vultr instances through the API for large technical definition documents
```

## Build goal: a field-complete execution
The hackathon target isn't the full paper. It's **one real run, from an open brief to completed fields**: every schema field for the target suppliers filled from public sources, with evidence, across all five phases. The definitions stay the north star; the build uses the thinnest version of each phase that still does real work.

**"Field complete" for the demo:** ≥ 50 suppliers with ≥ 80% of their schema fields in gold (identity, address, founding date, tax-list status, sanction status), each with a source link and screenshot. It's shown as per-field completeness bars plus per-node coverage of one or two taxonomies.

**Lean Simula (what's kept, what's cut):**
- **Keep:** factors of variation from the PRD with human accept or reject; taxonomy expansion to depth 2 with Best-of-3 proposals plus one critic pass; simple sampling strategies; Level Ratio Coverage as the completeness metric.
- **Cut for now:** per-level plans, the full node-evaluation metrics (completeness, soundness, novelty), Elo difficulty scoring, and complexification.

**Toolkit:** the engine ships the "hackathon minimum" tool set from the Agent toolkit section of `01-engine-definition.md`. Base page, file and extraction tools go in by 14:00. Graph, coverage and discovery tools go in by 17:00. The `code.write`, `code.test` and `code.promote` loop comes in the 22:00 execution-spectrum block.

**Repos:** repo 1 (engine) and repo 2 (anti-corruption case package plus app) are both public from hour 1. The lake (Object Storage plus Postgres) is set up on Vultr outside both.

## Hour-by-hour (Sat 11:30 to Sun 12:00 PT)
Strategy: get a **walking skeleton end to end first**, with every phase in its thinnest form on one source, then widen and deepen.

| Time | Milestone |
|------|-----------|
| 11:30–12:30 | Credits, 2 VMs, Object Storage bucket, Postgres, inference key. Create both public repos. Write the open brief in repo 2. |
| 12:30–14:00 | Sandbox host: gVisor browser pod with per-pod egress, writing captures to bronze and logging each step. **Gate: the engine drives a sandboxed browser and bronze fills.** |
| 14:00–17:00 | **Walking skeleton, one source, all five phases thin:** brief to PRD in one call; lean ontology (factors, one level, schema); fan-out that finds one source by search; one-shot technical definition document with no negotiation; an agentic loop that extracts into silver, then validation into gold. **Gate: one supplier's fields completed end to end with evidence.** |
| 17:00–20:00 | **Field completeness:** widen to 3 or 4 sources the agents found themselves. Entity resolution on RFC and name, reconciliation, per-field completeness, the loop back to fan-out for missing fields, stopping at the definition of done or the budget. **Gate: ≥ 50 suppliers at ≥ 80% of fields.** |
| 20:00–22:00 | Deepen the definition phases lightly: short Phase 1 research (personas and jobs to be done with sources), lean Simula (depth 2, Best-of-3 plus a critic), spider site graph for the chosen sources, Phase 4 with 1 or 2 negotiation rounds. |
| 22:00–00:30 | Execution spectrum: deterministic mode for file and bulk sources, crystallizing successful agentic traces into RPA macros, escalating on failed checks. Double critic on tax-list and sanction fields. |
| 00:30–03:00 | Repo 2 app layer: supplier dossier with evidence on click, red-flag explainer, case journal, field-completeness view. NetBird: gated URL and zero open ports. |
| 03:00–07:00 | Rest or harden. Cache one full run as a fallback. Poisoned-page scenario. |
| 07:00–10:00 | Rehearse, READMEs for both repos with a "built during the event" section, 1-minute video. Freeze at 10:00. |
| 10:00–12:00 | Buffer, then submit. |

**3-minute demo:** an open brief becomes a PRD with its definition of done (20 s). Factors and taxonomies appear, approved with one click (20 s). The agents find sources and a technical definition document is agreed for one of them (25 s). Sandboxes run while silver and gold fill and the per-field completeness bars climb (45 s). Open one supplier's dossier: every field has its evidence and a red flag explains itself (30 s). Replay that value in the case journal from the brief to the screenshot (20 s). A poisoned page is blocked and there are zero open ports (20 s).

**NetBird (decided: minimal):** one gated URL for the app (PIN or SSO) and zero inbound ports on the VMs, about 1 hour. Per-task expiring URLs are out of scope.

**Cut list if behind (in order):** Phase 4 negotiation (keep one-shot documents), then RPA crystallization (stay agentic), then spider site graphs (use search results directly), then the Phase 1 research loop (PRD from the brief only). **Never cut:** the end-to-end run, evidence per value, per-field completeness, and the dossier.

**Scope warning:** the definition phases (1 to 4) can eat the day. That's why they're thin until the field-complete gate at 20:00.
