# Project 1: the engine (working name "Ontofill")

**One line:** an open-source engine that turns a vague problem into a defined case, then completes the data for that case by sending sandboxed computer-use agents to public websites.

Status: definition only. The architecture and build schedule are in `ontofill-plan.md`.

## Problem
Most data-completion work fails before any scraping starts. Nobody wrote down who the data is for, what "complete" means, or what shape the data should take. Teams then scrape everything, store it inconsistently and never know when they're done. The engine makes the definition step explicit, traceable and reusable. Execution is then driven by that definition.

## Who uses the engine
- **Data and civic-tech teams** (like Codeando México) who start new open-data projects often.
- **Researchers and journalists** who need a dataset that doesn't exist yet.
- **Developers** who build applications on a domain. They write a **case** in their own repo and run the engine on it, instead of writing a scraper.

## Three homes: engine repo, application repo, data lake
| Where | What lives there |
|-------|------------------|
| **Repo 1: the engine** (this project) | Code only. It takes a case, runs the five phases and executes. It holds **no data and no data lake**. It ships the phase agents, sandbox and executor runtime, and the adapters that write to whatever lake a case points to. |
| **Repo 2: the application** (for example, anti-corruption) | The **case package** (defined below), the application layer, and the configuration that points to its data lake. Running repo 1 on this case is what "the application" means. |
| **The data lake** (outside both repos) | The working store for everything the execution finds: bronze (raw captures and site-graph snapshots), silver (typed observations with provenance) and gold (refined toward the definition of done). It runs as a service on Vultr (Object Storage plus a database). Repo 2 references it and never commits data. |

## The case package (lives in repo 2)
The case package is **the versioned definition of one case**: everything the engine decides *before and around* data, as text files in git. Each phase adds to it:

| Part | Produced by | Contents |
|------|-------------|----------|
| Brief and research ledger | Phase 1 | The open brief, then sources, claims and evidence behind personas and jobs to be done (as links to bronze in the lake) |
| Global PRD | Phase 1 | Personas, jobs to be done, journeys, requirements traced to journey steps, non-goals, constraints, definition of done as testable criteria |
| Ontology | Phase 2 | Taxonomies of the factors of variation (Simula), classes, properties, relations, SHACL shapes, alignment to schema.org and Wikidata, and definition-of-done criteria compiled to queries |
| Surface map | Phase 3 | Mapping of site graphs onto the ontology and coverage estimates (the raw site graphs are in the lake) |
| Objectives | Phase 3 | (source, objective) pairs, ranked by expected contribution to the global definition of done |
| Local PRDs + technical definition documents | Phase 4 | One per source and objective: the re-scoped problem for that source only, the desired extraction, and the negotiation record |
| Crystallized macros | Phase 5 | Deterministic RPA scripts learned from agentic runs, versioned like code |
| Lake pointer and run log | Phase 5 | Which lake, which run, metric snapshots, and the case journal index |

It's the thing people fork. Another team can copy repo 2's case package, change one persona, point it at their own lake and re-run the engine from repo 1.

## The five phases (summary)
1. **Scope:** draft personas, exploratory deep research into real-world jobs to be done, refined personas, journeys, then a global PRD with a definition of done.
2. **Ontology:** built from the PRD by replicating Simula's taxonomy construction (see "Phase 2 method" below). The steps are: factors of variation, then breadth-first taxonomies with Best-of-N proposals, a critic and level plans, then evaluation and sampling strategies. The schema comes second. It's validated by checking that every definition-of-done criterion can be expressed as a query.
3. **Fan out:** spiders map sites into graphs, those graphs are mapped onto the ontology, and the engine produces **(source, objective)** pairs.
4. **Local scoping (one per source and objective).** Scope is re-defined *only* for that one source and objective, and the global PRD is not reopened.
   - **Local PRD:** which global requirements this source serves for this objective, and its local definition of done.
   - **Technical definition document:** the desired extraction (the ontology subset and target fields, entities and volume), how to reach it (path through the site graph, search forms, pagination, files or APIs), the extraction method (DOM, vision, PDF or download), validation rules, rate limits, budget and allowed domains.
   - **Back and forth:** a *requester* agent holding the global PRD negotiates with a *source engineer* agent. The source engineer probes the real site in a sandbox (small test extractions) and pushes back: "this field isn't published here", "only 2019 onwards", "this needs 400 paginated pages, so I propose using the bulk download instead". They iterate until the document is feasible and agreed. A human can approve or step in. The negotiation log is kept.
   - A source can also be rejected here, which sends the objective back to Phase 3 to find another source.
   - **Focused, not expanding (decided):** a local PRD asks only *"what does this site have that completes the current ontology?"* It never changes the ontology itself. When a site shows something the ontology lacks (a new field, category or relation), the local PRD records an **ontology recommendation**, with evidence, and sends it back to Phase 2. The raw capture is already in bronze, so nothing is lost. If the recommendation is accepted, the ontology gets a new version and gold is re-refined from bronze without browsing again. This keeps the coverage metric comparable across sources, keeps agents writing only typed values into a fixed schema, and makes every ontology change a reviewed decision.
5. **Execute (reactive and flexible):** each approved technical definition document is carried out into the case's external data lake, using the execution spectrum below. Gold is refined toward the definition of done. Metrics feed back: a local shortfall reopens that source's Phase 4, and a global gap reopens Phase 3.

## Execution spectrum: from deterministic to stochastic
Execution isn't one mode. Each step of a technical definition document runs somewhere on a spectrum, and inference decides where, step by step, reacting to what happens:

| Mode | What it is | When it fits |
|------|------------|--------------|
| **D0 Deterministic** | Bulk download, API call, or a recorded RPA script with fixed selectors. No LLM. | Stable, structured sources. The cheapest and most reproducible mode. |
| **D1 Inference-assisted RPA** | A fixed flow, with the LLM used only at defined points: mapping fields to the ontology, parsing messy text or PDFs, repairing a broken selector. | Known layouts with messy content |
| **S1 Agentic loop** | Plan, act, observe over the DOM and accessibility tree, with tools. The agent chooses the path within the technical definition's allowed domains. | Search forms, varied pages, multi-step navigation |
| **S2 Full computer use** | Screenshots and the vision model, free navigation within the sandbox. | Unknown or hostile layouts, canvas, and scanned documents |

**How it reacts:**
- The technical definition document sets each step's **starting mode and allowed range** (for example, "start at D0, may escalate to S1, never S2").
- **Escalate** on signals: a selector misses, a value fails schema validation, the layout drifts, an unexpected page appears, or the yield falls below the document's expectation. A captcha or login wall means **stop**, never bypass.
- **De-escalate (crystallize):** a successful agentic trace is compiled into a deterministic RPA script for the next entity or run. The system gets cheaper and more reproducible as it learns a source.
- **Event-driven:** the executor reacts to events (a new lead from silver, a site change detected by a spider, a metric dropping) rather than following a fixed batch plan.
- **Every step logs its mode.** The metrics show success, cost and yield per mode, and the case journal shows where the engine had to "think" and where it ran on rails.

This matters for the pitch: the engine spends inference only where the web is unpredictable, and turns what it learns back into cheap, reproducible automation.

## Agent toolkit: exploration, extraction, code and resilience
Agents are only as good as their tools. Every tool is **read-only** (the data-completion archetype has no writes), runs **inside the sandbox**, is logged with the four observability fields, and is **granted per phase and per technical definition document**. The document lists the tools a job may use, the same way it lists allowed domains.

### 1. Site exploration: technical tools (what's on this page and site?)
| Tool | What it gives the agent |
|------|-------------------------|
| `page.snapshot` | Rendered page as an actionability-filtered accessibility tree, plus a DOM skeleton hash and URL template |
| `page.screenshot` | A screenshot with set-of-marks overlays, for the vision fallback |
| `page.query` | CSS or XPath query, returning matching elements with role, name and structural path |
| `page.forms` / `page.links` / `page.pagination` | List forms (fields and options), links and pagination controls, each with a risk tier |
| `net.observed` | The XHR and fetch calls the page made, which reveal hidden JSON endpoints for deterministic reads |
| `site.meta` | robots.txt, sitemaps, and open-data or API links found on the site |
| `file.fetch` / `file.parse` | Download a CSV, XLSX or PDF; extract text and tables; OCR for scanned PDFs |
| `page.diff` | Before and after comparison around an action (state estimation: did anything change?) |

### 2. Graph exploration: expansion tools (where should we go next?)
| Tool | What it gives the agent |
|------|-------------------------|
| `graph.frontier` | Unexplored nodes and edges in the site graph, ranked by expected ontology value, with HIGH-risk edges filtered out |
| `graph.expand` | Crawl a frontier node with a budget and a learning-progress stopping rule, adding instances and types |
| `graph.cluster` | Group instances into page types (URL template plus skeleton hash) |
| `graph.map` | Propose which ontology classes and properties a page type exposes, with a confidence score |
| `ontology.gaps` | Coverage query: which taxonomy cells and schema fields are still empty, and which known sources could fill them |
| `source.discover` | Web search for new candidate sources for a gap, followed by an authority or trust check |
| `ontology.recommend` | File an ontology change recommendation with evidence. This is the focused local PRD's only way to touch the ontology. |

### 3. Extraction tools (getting values out, decisively)
| Tool | What it does |
|------|--------------|
| `extract.selector` | Deterministic extraction by selector or template |
| `extract.table` | HTML or PDF table extraction into rows |
| `extract.llm` | Schema-guided extraction from a page chunk with a Vultr model, with output constrained to the ontology schema |
| `entity.lookup` | "Have we seen this supplier (RFC or name)?" for resolution before writing |
| `emit.observation` | **The only output channel.** Writes a typed triple plus evidence (URL, selector, screenshot crop) to silver. Validated against SHACL on write. |

### 4. Code tools (technical flexibility)
Agents can **write, test and repair extraction code**, so a site is learned once and then runs cheaply:
- `code.write`: the agent writes a Python or Playwright extractor for a page type (code-as-action), run in the sandbox only.
- `code.test`: runs the extractor on N sample pages and compares its output against LLM-extracted reference values on the same pages. It scores precision and coverage per field.
- `code.promote`: a script that passes becomes a **crystallized macro** (a D1 option in the site graph), versioned in the case package in repo 2.
- `code.repair`: when a macro's termination predicate fails (layout drift or a stale selector), inference diagnoses the failure from the page diff and patches the script. It re-runs `code.test` and bumps the version. If the repair fails twice, the step escalates to the agentic loop.

### 5. Resilience built into the tools
- **A strategy ladder for each field:** public API or bulk file, then an observed JSON endpoint (read-only), then a selector macro, then LLM extraction from the DOM, then vision. Fall back one rung at a time on failure.
- **Retries with backoff, polite rate limits** and checkpointing, so a job resumes where it stopped. Writes to the lake are idempotent, keyed by content hash.
- **Yield monitoring:** if a job's yield falls below its document's expectation, that triggers repair or escalation, not silent continuation.
- **Hard stops:** captcha, login wall or a HIGH-risk action ends the job and flags it. Nothing is bypassed.
- **Inference-based diagnosis:** on any failure, a Vultr model reads the step log (observed, requested, executed, evaluated) and picks one of retry, repair, escalate, or reject the source.

**Hackathon minimum:** `page.snapshot`, `page.query`, `page.forms`/`links`/`pagination`, `file.fetch`/`parse`, `ontology.gaps`, `source.discover`, `extract.selector`/`llm`, `entity.lookup`, `emit.observation`, and `code.write`/`test`/`promote`. The rest (`net.observed`, `graph.cluster`, `code.repair`, vision) are added as time allows.

## The resilient scraping toolkit (a formal package)
The toolkit above isn't glue code. It's a **first-class, separately usable package inside repo 1 (decided)** (working name `ontofill-scrape`). It isn't a general "scrape anything" crawler; it's a **resilience-first extraction toolkit** that the engine drives and others can reuse. It's formalized as follows:

**1. Tool contract.** Every tool declares typed inputs and outputs, an action-ladder rung, a risk tier (read-only: SAFE or LOW), idempotency, a **termination predicate** (how you know it worked), and which failure classes it can raise. Tools are exposed as plain Python and as MCP tools, so any agent can call them.

**2. Failure taxonomy.** Every failure is classified, never just logged: `network`, `rate_limited`, `blocked` (captcha or login: hard stop), `layout_drift`, `stale_binding`, `empty_yield`, `validation_failed` (SHACL), `conflict` (disagrees with another source), and `injection_detected`.

**3. Resilience policies**, one per failure class and declared in the technical definition document:
- retry with backoff (network, rate-limited)
- repair the binding by inference (layout drift, stale binding)
- step down the strategy ladder: API or file, then observed endpoint, then selector macro, then LLM extraction, then vision or the peripheral agent (empty yield, repeated drift)
- corroborate with another source (conflict)
- stop and flag (blocked, injection)

**4. Source health.** Each source gets a rolling health record: success rate, yield against expected, drift events, repair count, and latency. It feeds Phase 3 source ranking, and it's what "resilient" is measured by.

**5. Regression harness.** Because bronze stores raw captures, every crystallized macro is **tested by replaying stored pages**, with no live traffic. A layout change shows up as a failing replay before it corrupts gold, and a repaired macro must pass both old and new captures.

**6. Versioned macros.** Crystallized extractors are versioned with their test results and source health at promotion. They live in the case package in repo 2; the toolkit only defines the format.

### Skyvern on the periphery
Skyvern (open source, AGPL-3.0) is an LLM- and vision-driven browser agent. It has workflows, JSON-schema data extraction, Docker self-hosting, and support for any OpenAI-compatible endpoint, which means it can run on **Vultr Serverless Inference**. The engine uses it **only as a peripheral adapter** at the top of the strategy ladder:
- **Role:** an optional S2 executor for hard navigation, such as multi-step search forms or odd layouts, when the engine's own agentic loop has failed or been judged too costly.
- **Boundary:** it runs as a **separate service in the sandbox, called over its API**, and is never imported as a library. That keeps the engine's Apache-2.0 code clear of AGPL coupling, and it's what "periphery" means here.
- **Same rules:** its output comes back as *candidate observations* through `emit.observation`, with the same evidence, SHACL validation and critics. Its login, 2FA and password-manager features are not used, and its captcha solving is cloud-only and out of scope anyway.
- **Optional:** the engine and toolkit run fully without it. It's a fallback the adapter interface allows, not a dependency.

## Inference: Vultr first, Jev as a supporting resource
**All main inference runs on Vultr Serverless Inference.** That covers planning, the agentic loop's next-action choices, extraction, critics (including the double critic), classification, the Simula taxonomy work and the negotiation agents. Vultr's models (glm-5.3, qwen3.8, deepseek-v4.1-flash, nemotron omni for vision, nemotron content-safety, the retriever and reranker) cover all of it. The engine exposes one **decision interface** for typed judgments (choice, score, yes/no), backed by a Vultr model with constrained output.

**Jev (TypeSafe AI) is an optional supporting backend** behind that same interface. It's a decision-only model (no text output) with fast, cheap typed answers. Use it only as a helper for a few high-volume, low-stakes pre-checks, such as:
- pre-filtering pages for prompt-injection text before a Vultr model reads them
- a first-pass "same supplier?" screen before the Vultr model confirms matches

It is never the final word on a gold value, it's never in the main agent loop, and the engine runs fully without it. Every call is logged like any other decision. Its known weak spots (dates, counting, arithmetic, multi-hop reasoning) stay with code or Vultr models.

## Phase 2 method: Simula's ontology construction, replicated
Reference: Davidson et al., *Reasoning-Driven Synthetic Data Generation and Evaluation* (Simula), TMLR 03/2026, openreview NALsdGEPhB, arXiv 2603.29791. The file is at `/mnt/project-files/reference/simula-2603.29791v1.pdf`. Section numbers below refer to the paper.

**Core idea:** we replicate Simula's taxonomy-first definition of a domain as faithfully as possible. Simula uses it to decide what coverage a synthetic dataset needs, then generates data. Ontofill uses the identical construction to decide what coverage the **real** data needs, then sends agents to find it. That makes the engine "Simula for the real web". The engine is **taxonomy first, schema second**.

### 2a. Factor disentanglement (§2.1; Alg. 1, phase 1)
- `ProposeFactors(y, S)`: the model proposes the prime **factors of variation** of the target dataset. *y* is the global PRD (Simula's "user instructions"). *S* is an optional sample, which here means the Phase 1 evidence and any known examples.
- A human (or a model) **accepts or rejects** each factor. This is the Phase 2 human checkpoint.
- Example for the anti-corruption case: procedure type, contracting sector, buying-unit level, region, contract-amount band, supplier profile, red-flag type.

### 2b. Breadth-first taxonomic expansion (§2.1; Alg. 1, phase 2, App. B.4)
Each accepted factor *f<sub>i</sub>* becomes a taxonomy *T<sub>i</sub>* with root `Node(y, f_i)`, expanded level by level to a target depth *D<sub>i</sub>*. For every node *n* at the current level:
1. **Context** *C* = {*y*, ancestors(*n*), siblings(*n*)}.
2. **Proposal (Best-of-N):** query the model *N* times with *C* and the current level plan *P*, then take the union of the raw children.
3. **Critic refinement:** a separate call adds, removes, merges or edits children to improve **completeness, soundness and specificity** (using the gap between generator and critic).
4. **Level plan:** after all nodes of a level are expanded, and if *d < D*, the model writes a plan *P* for the next level from all the new nodes. This keeps granularity consistent across parallel expansions. *P* starts as "Expand based on *y* and *f<sub>i</sub>*".

Parameters are set per case in the PRD: depth *D<sub>i</sub>* per factor and *N*. Our default is *N* = 5; the paper doesn't fix *N* for taxonomies. Over-coverage is acceptable (§4.1: "we can always reduce the depth"), so prune later instead of under-expanding.

### 2c. Taxonomy evaluation (App. B.1–B.3)
- A **critic model** labels every node as *Good and Overlapping*, *Good and Exclusive*, *Redundant*, or *Bad*, measured against a reference taxonomy where one exists.
- Metrics: **Completeness** = Good-Overlapping ÷ Total-Good(reference). **Soundness** = Total-Good ÷ Total-Nodes(generated). **Novelty** = Good-Exclusive(generated) ÷ Total-Good(reference). **Coverage** = Completeness + Novelty. Coverage above 1.0 means the generated taxonomy has more sound items than the reference.
- **Grounded versus conceptual** (App. B.1): for grounded factors, use or evaluate against the official classification. Examples for Mexico: INEGI geography for region, SCIAN for sector, and procurement-law procedure types. Conceptual factors, such as red-flag types, are generated, and novelty counts as a strength.
- **Different model families for generator and critic,** to address the paper's preference-bias limitation (B.3). On Vultr, for example, glm-5.3 generates and qwen3.8-27b or deepseek-v4.1-flash critiques.
- Soundness and coverage go on the Phase 2 review screen, so the human approves numbers, not vibes.

### 2d. Sampling strategies (§2.2; App. C)
The model writes **strategies**: which taxonomies are combined, with what weights, and which combinations are illogical and excluded. A strategy's sampled node set (a **mix**) is a coverage cell. With *V* unique mixes and budget *N*, the global coverage ratio is *N/V* (§2.2). The PRD chooses wide (more mixes) or deep (more instances per mix). A **complexification** share *c* reserves budget for hard or rare cells.

### 2e. Schema and alignment (Ontofill's addition, not in the paper)
Once the taxonomies define *which varieties* of entities must be found, the schema defines *what each entity needs*: classes, properties, relations, and SHACL shapes. Each entity also gets a "classified-as" link to taxonomy leaves. The schema is aligned to schema.org, Wikidata or a domain standard such as OCDS, and every definition-of-done criterion must be expressible as a query.

### How the rest of the engine uses it
| Simula piece | Ontofill use |
|--------------|--------------|
| Mixes from strategies (§2.2) | **Phase 3 objectives:** a mix plus the schema properties it needs, sent to sources that can supply it |
| Meta prompts | **Phase 4 local PRD and technical definition document:** the mix turned into a concrete extraction instruction for one source |
| Complexification | Deliberately target hard or rare cells (scanned PDFs, name variants, small municipalities) |
| Point-wise critic and **double critic** (§2.2, §3.1) | Silver-to-gold refinement. Check each value against its evidence and cell requirements. On high-stakes fields such as tax-list status and sanctions, ask separately "is it correct?" and "is it incorrect?" to reduce sycophancy. |
| **Taxonomy assignment and Level Ratio Coverage** (§2.3) | Assign every gold record to taxonomy nodes, then compute the share of unique nodes covered **per level**. This is the ontology-completeness metric (Ric's third axis), and uncovered nodes drive the next fan-out. |
| Calibrated attribute scoring with Elo (§2.3, App. E.3) | Score the difficulty of sources and objectives. This sets the starting execution mode in the technical definition document. |

## Grounding in the computer-use ontology
Reference: `/mnt/project-files/reference/computer-use-ontology.md` (Ric's computer-use taxonomy). The engine uses its vocabulary as the standard terms for describing and logging execution.

**The execution spectrum mapped onto the action ladder (Axis B) and control loop (Axis C):**
| Mode | Rungs (Axis B) | Observation (Axis A) | Control loop (Axis C) |
|------|----------------|----------------------|------------------------|
| D0 Deterministic | B7 public API or bulk file; B6 **reads only** on observed endpoints | Programmatic | None (script) |
| D1 Inference-assisted RPA | B5 macros (site-graph options) that expand into B2 steps | Text structure | Macro mode with a per-step verifier; the LLM only maps, parses or repairs a binding |
| S1 Agentic loop | B2 element-targeted over an actionability-filtered accessibility-tree candidate set | Text structure, with set-of-marks hybrid when needed | Planner/executor with before/after change observation; one bounded reflection on failure |
| S2 Full computer use | B1 browser input events (coordinates via CDP), B0 only inside the sandbox desktop | Pixels or set-of-marks | ReAct with a vision grounder |

**Escalation and crystallization in these terms:**
- Escalation is triggered when a **termination predicate** fails, a **binding** goes stale, a value fails SHACL validation, or state estimation is uncertain. Each move goes down the ladder toward generality.
- **Crystallization** is memory-augmented control: a successful S1 or S2 trace becomes a **rung-5 option** (initiation set, policy, termination predicate) stored as an edge in the site graph. Next time, it runs at D1.

**Phase 3 spiders build the ontology's site graph:**
- A **type/instance** two-layer graph. Node key = URL template plus DOM-skeleton hash with volatile content stripped. Embeddings are only a merge signal.
- **Edges are options** with a risk tier. Hidden state (filters, pagination, open modal) goes in the episode context vector as preconditions.
- This is **build-time exploration**. **HIGH-risk edges are never explored.** Spiders stop on **learning progress** (the drop in prediction error falls below ε), not on novelty, to avoid the noisy-TV trap on ads, timestamps and tokens.
- Types are named by the LLM for display only. The mapping from site graph to domain ontology (Phase 3) connects page types and affordances to the domain classes and properties they expose.

**Phase 4 technical definition documents are specified on the three axes.** For each step, the document declares the observation channel, the allowed rung range, and the control-loop family, plus the termination predicates that verify each step.

**Task archetype and safety posture:** data completion is the **read-only information-seeking** archetype. It's heavy on navigation, cheap to retry, and the best fit for graph memory. The engine enforces it: **no writes, only SAFE and LOW edges, no form submission beyond search and filter**, and no B6 writes. That's the Blast Radius Zero story in the taxonomy's own terms.

**The five sub-problems as metrics:** grounding (correct-target rate), state estimation (repeated or missed transitions), verification (termination-predicate pass rate, validator against gold), memory and transfer (option reuse rate, stale-binding rate, amortization crossover per source), and safety (blocked injections, zero unauthorized writes). These sit alongside Ric's three extraction axes.

**Observability rule, applied to bronze:** every execution step records the four fields: **observed** (Axis A channel), **requested** (rung, agent space), **executed** (executor space, including macro expansion), and **evaluated** (an independent check against SHACL, cross-source agreement, or held-out ground truth, not the live UI).

## Principles
- **Open by default:** Apache-2.0 code in both repos. Case packages published with open licenses (CC-BY for PRDs and ontologies). Gold data published from the lake under a license set per case.
- **Public data only:** no logins, no paywalls, and nothing past a captcha. Each case declares its allowed domains.
- **Contained execution:** every browser runs in a disposable Vultr sandbox that can only reach its technical definition document's domains.
- **Provenance or it didn't happen:** every value in gold traces back to a capture in bronze.
- **Humans approve the contract:** people sign off on the personas, the PRD and the ontology. Agents do the labor.

## Definition of done for the engine (hackathon)
- One case runs end to end through all five phases, with every part of the case package produced and viewable.
- Metrics on all three axes are visible, and gold reaches the case's definition-of-done target.
- Editing the PRD refreshes gold from bronze without browsing again.
- The anti-corruption case (Project 2) is the reference case.

## Non-goals
- Logged-in, paywalled or private sources, and bypassing captchas or anti-bot measures.
- A scraper that runs without a case. The toolkit is general-purpose code, but every run is scoped by a technical definition document.
- Deciding truth on its own. The engine reports confidence and conflicts, and humans judge.

## Open questions

- Minor: whether Jev, as an optional helper outside Vultr, is acceptable under the Track 1 inference rule. The engine doesn't depend on it.
- Lake layout: my default is bronze in Vultr Object Storage and silver and gold in Postgres plus an RDF store, with gold exported as Parquet, CSV and RDF for publishing.
