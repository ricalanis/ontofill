# Work list

## Active slice: R15 spiders and site graphs (brief 13)
- [ ] Preserve the bounded gVisor spider pod and enforce same-registrable-domain GETs, robots, politeness, depth 2, configurable 30–50 page cap, and captcha/login hard stops.
- [ ] Persist one bronze page capture and trace step per fetched page, plus one six-checkpoint job per crawl.
- [ ] Build a schema-valid bronze/site-map graph with URL-template + DOM-skeleton page types, typed ontology labels, sample property hints, and property coverage.
- [ ] Rank P3 objectives by graph coverage and pass graph paths into P4; send outer-gap spider-reopen integration proposal to root for post-R2 integration.
- [ ] Add focused synthetic listing → detail → download, robots, POST-never-submitted, and depth/cap tests.
FILES: `sandbox/agent-pod/capture.py`, `sandbox/agent-pod/spider_policy.py`, `src/ontofill/sandbox/capture.py`, `src/ontofill/sandbox/jobs.py`, `src/ontofill/phases/p3_fanout/site_graph.py`, `src/ontofill/phases/p3_fanout/discovery_loop.py`, `src/ontofill/phases/p4_local_scoping/phase.py`, `schemas/site-graph.schema.json`, focused spider/site-graph tests.
TASK: Add a bounded, auditable generic site spider and feed its ontology coverage to P3 and P4.
DONE: `uv run pytest -q tests/test_spider_policy.py tests/test_site_graph.py tests/test_sandbox_spider.py tests/test_no_case_vocabulary.py`
FORMAT: `uv run ruff check sandbox/agent-pod src/ontofill/sandbox/capture.py src/ontofill/phases/p3_fanout src/ontofill/phases/p4_local_scoping tests/test_spider_policy.py tests/test_site_graph.py tests/test_sandbox_spider.py && uv run ruff format --check sandbox/agent-pod src/ontofill/sandbox/capture.py src/ontofill/phases/p3_fanout src/ontofill/phases/p4_local_scoping tests/test_spider_policy.py tests/test_site_graph.py tests/test_sandbox_spider.py`
CHECK: focused managed `agent-progress run` passed 20 tests with strategy `robots-fixture-correction`.

## Critical path: brief 10d and CONTRACT v0.9.7
- [x] Make every live P1 revision consume all critic objections and prior failed checks, with deterministic human percent/tier grounding and specific-domain review.
- [x] Generate and validate a replacement PRD before archiving a denied draft; keep current artifacts and approval intact on inference failure.
- [x] Require reviewed artifact digests and identity_source on PRD/factors/ontology/action approvals; refuse mismatches without changing case files.
- [ ] Run a managed full pytest + Ruff gate, pre-push scan, then push main and coordinate the control-VM update with Claude PA.
FILES: `src/ontofill/phases/p1_scope/`, `src/ontofill/case/`, `src/ontofill/inference/decision.py`, `schemas/approved.schema.json`, relevant tests.
TASK: Unblock the human PRD review while binding every approval to the exact reviewed artifact.
DONE: `uv run pytest -q && uv run ruff check src tests packages infra sandbox && uv run ruff format --check src tests packages infra sandbox`; mismatch tests must show byte-for-byte unchanged case files.
FORMAT: Ruff check and format across edited files.
CHECK: user-authorized `agent-progress` R1+R5 full gate after the run-status regression → 246 passed, 3 skipped; Ruff check/format clean.

## Gap closure order
- [x] R1+R5: pushed `89c5fff`, control VM fast-forwarded and `uv sync --frozen` passed; awaiting orchestrator verification.
- [ ] R7: merge orch/p3-discovery with brief 12 fixes; default providers exclude Bing/DDG.
- [ ] In parallel after R1: R2 P5 real path, R3 controller S1 bridge, R6 signals/relationships, R15 spiders/site graphs.
- [ ] After R2: R4 in-run repair macros, R10 taxonomy honesty, R11 bronze re-refine.
- [ ] R8 repair-side containment command; R9 gateway routing was verified by the orchestrator.
- [ ] R9b: gateway-token alias is locally committed; finish screened page-content spans in P3/P5/repair prompts.
P4 negotiation and P1 research are cut by the user. Mark each shipped row READY FOR VERIFY <sha> in the gap tracker.

## After 10d and P3: brief 13 spiders and site graphs
- [ ] Start a Luna worker in `.worktrees/brief13-spiders` after P3 lands; keep console and browser-agent files PA-owned.
- [ ] Crawl confirmed sources only inside bounded gVisor cells, with robots, depth/page/polite limits, GET-only edges and bronze/trace proof.
- [ ] Cluster page types by URL template plus DOM skeleton, label them against the inferred ontology, and publish the CONTRACT v1.0.3 site graph.
- [ ] Feed graph coverage and paths into P3 ranking, P4 TDDs and outer-gap reopen; prove synthetic fixture and one live generic site.
FILES: `src/ontofill/phases/p3_fanout/`, sandbox/cell substrate, `schemas/`, P4/outer integration and synthetic tests.
TASK: Restore the original spider/site-graph design without domain assumptions or in-process browsing.
DONE: Synthetic listing→detail→download, robots, POST-never-submitted and cap/depth tests, genericity guard, full pytest/Ruff, and a live gVisor generic-site proof.
FORMAT: Ruff check and format across edited files.

## Current delivery gate: live cells and brief 10b PRD steering
- [x] Push the runsc cell relay, preflight diagnostics, control-VM SSH bootstrap and repair-pod stdin transport; deploy the cell service on the control VM.
- [x] Prove the destructive loop stays inside a capped runsc pod and that the repair path emits a limit stop.
- [x] Complete native cell create → CDP browser action → blocked navigation → six checkpoints → destroy on the live sandbox VM.
- [x] Close three independent-review PRD regressions: human secondary publisher cannot auto-trust, revised policy invalidates old auto authority, and denied/percent numbers cannot become human-grounded counts.
- [x] Rerun brief 10b combined tests and scratch live denial, push the PRD fix, and fast-forward the control VM for the user's rerun.
- [x] Fix brief 10c: each accepted P1 revision must change the relevant draft and carry auditable loop digests; authority policy needs at least one in-jurisdiction primary publisher with domains, no empty trusted domain lists or duplicates.
- [x] Prove the narrow brain-to-gateway rule live: gateway ALLOWED, control SSH BLOCKED, hands-to-gateway BLOCKED; clean up disposable containers/networks.
- [x] Add the bounded brief 11 outer DoD gap loop after P5, with typed P2/P3/P4 reopen decisions and a run budget.
- [ ] Complete brief 11 P3/P2/P4 phase loops after P1 and the outer gap loop.
FILES: `src/ontofill/sandbox/`, `sandbox/`, `infra/vultr/`, `src/ontofill/phases/p1_scope/`, `src/ontofill/phases/p3_fanout/`, `src/ontofill/workflow.py`, `schemas/`, `tests/`.
TASK: Make the live browser substrate and user-steered PRD safe and reviewable before widening phase loops.
DONE: `uv run pytest -q` and Ruff gates pass, a scratch Vultr denial writes an approvable revised PRD with human-grounded DoD, and the live native cell records its six checkpoints and teardown.
FORMAT: `uv run ruff check src tests packages infra sandbox && uv run ruff format --check src tests packages infra sandbox`.

## G1/G2: generic brief-to-export engine (brief 04, active priority)
- [x] Infer case authority policy in PRD and classes/properties/relations/rules/DoD queries in ontology; no fixed domain vocabulary.
- [x] Derive source classes and target properties from approved artifacts; infer and crystallize source-column mappings, then emit observed values generically.
- [x] Refine and export entities.jsonl plus ontology.json and generic metrics.dod[] with full evidence/trace lineage.
- [x] Add a failing case-vocabulary guard and a second unrelated brief-only test under tests/genericity that reaches P5 in recorded scratch.
- [x] Apply brief 05 model routing and put per-call token usage/est_usd on inference trace steps.
FILES: `src/ontofill/`, `packages/ontofill-scrape/src/`, `schemas/`, `tests/genericity/`, `tests/test_no_case_vocabulary.py`, `tests/test_inference.py`, `GOAL.md`, `TODO.md`.
TASK: Make phases and gold export driven by each case's approved PRD/ontology rather than embedded domain assumptions.
DONE: `uv run pytest -q tests/genericity tests/test_no_case_vocabulary.py` and full suite with generic entity export and no banned source terms.
FORMAT: `uv run ruff check src tests packages/ontofill-scrape/src && uv run ruff format --check src tests packages/ontofill-scrape/src`.

## Track acceptance (brief 06; immediately after G1/G2, before 03b/C4b)
- [ ] Probe zero pod secrets, blocked metadata/mesh access, and publish proof in jobs.jsonl.
- [ ] Enforce memory, CPU, pids, wall-clock and step limits on every pod; record limits and hard stops.
- [ ] Verify each browser action's screenshot with Vultr vision inference, and gate non-SAFE/LOW actions before submit.
- [ ] Show bounded sandbox code write/test/repair attempts; script hostile-page and destructive-loop containment proofs.
FILES: `sandbox/`, `src/ontofill/sandbox/`, `src/ontofill/workflow.py`, `schemas/`, `tests/`, `docs/reference/track-blast-radius-zero.md`.
TASK: Satisfy the official track requirement table with inspectable runtime proof.
DONE: targeted sandbox tests plus on-demand containment scenarios prove blocked egress, zero secrets and enforced limits.
FORMAT: Ruff check/format across edited engine and sandbox files.

### Pattern A repair runner slice
FILES: `src/ontofill/repair/`, `sandbox/code-repair/`, `tests/test_code_repair.py`, `TODO.md`.
TASK: Execute bounded code.write → code.test repair attempts against stored captures in disposable gVisor pods, with bronze artifacts and visible repair traces.
DONE: `uv run pytest -q tests/test_code_repair.py` exercises a failing extractor, a passing patch, and a contained limit stop; opt-in runsc test exercises the real pod when available.
FORMAT: `uv run ruff check src/ontofill/repair sandbox/code-repair tests/test_code_repair.py && uv run ruff format --check src/ontofill/repair sandbox/code-repair tests/test_code_repair.py`.
CHECK: `uv run pytest -q tests/test_code_repair.py tests/test_no_case_vocabulary.py` → 6 passed, 1 opt-in runsc skip; `uv run pytest -q` → 182 passed, 3 skips; Ruff check and format check pass. Local Docker has runc only, so the gVisor containment probe remains for the sandbox VM.

## Cell substrate (brief 07 v2; alongside brief 06)
- [ ] Expose create/status/destroy for a per-cell isolated network with gVisor Chromium hands, control-plane-only CDP, domain allowlist, caps and zero hands secrets.
- [ ] Support native and upstream Skyvern brain cells; restrict a brain to its own hands and Claude PA's inference gateway using only a session-scoped token.
- [ ] Publish six proof checkpoints and limits/usage to jobs.jsonl; prove allowlisted and blocked navigation from the control plane.
- [ ] Connect engine S1/S2 steps to Claude PA's controller MCP client without editing `services/browser-agent/`.
- [ ] Stretch: tagged throwaway-VX1 placement, destroyed with the cell.
FILES: `src/ontofill/sandbox/`, `sandbox/`, `infra/vultr/` for placement, engine-side MCP client, relevant schemas/tests. Claude PA owns `services/browser-agent/` and the inference gateway.
TASK: Supply safe cell lifecycle and the engine client interface for the controller service.
DONE: local integration creates a native cell, connects via CDP, blocks disallowed egress, destroys it, and records six checkpoints; a Skyvern cell reaches only its gateway and hands. Remote gVisor check follows L2 VM availability.
FORMAT: Ruff check/format across edited files.

## C1: tooling and contract
- [x] Initialize Git, install full Apache-2.0 license, commit scaffold.
- [x] Build uv workspace, CLI skeleton, schemas, local Postgres/Oxigraph compose, tests and dev commands.
- [x] Check: `uv run pytest -q` → 41 passed; `docker compose --env-file .env -f infra/compose/compose.yaml up -d --wait` → 3 services started, Postgres and Oxigraph probe healthy; `uv run ruff check .` and `uv run ruff format --check .` pass.
FILES: `LICENSE`, `pyproject.toml`, `packages/ontofill-scrape/`, `src/ontofill/cli/`, `src/ontofill/lake/`, `src/ontofill/inference/README.md`, `docs/planning/01-engine-definition.md`, `schemas/`, `tests/`, `infra/compose/`, `.env.example`, `.gitignore`, `CLAUDE.md`, `AGENTS.md`, `README.md`, `GOAL.md`, `TODO.md`, `NOTES.md`.
TASK: Make the engine installable with contract-valid documents, file:// local lake, and healthy local services.
DONE: `uv run pytest -q && docker compose --env-file .env -f infra/compose/compose.yaml up -d --wait`.
FORMAT: `uv run ruff check . && uv run ruff format --check .`.

## C4a: provider-pluggable discovery and authority review
- [ ] Add catalog plus agent-driven general web-search providers through sandbox egress; query from ontology gaps; stop on captcha/block and try next provider.
- [ ] Gate unrecognized sources behind an explicit human source approval; derive recorded selections only from captured candidates.
FILES: `src/ontofill/phases/p3_fanout/`, `src/ontofill/workflow.py`, `packages/ontofill-scrape/src/ontofill_scrape/discovery.py`, `tests/test_discovery_providers.py`, `schemas/` only if interface needs it.
TASK: Discover public sources across source types from the brief and current gaps with auditable authority decisions.
DONE: `uv run pytest -q tests/test_discovery_providers.py tests/test_discovery_phase.py` including synthetic captcha/fallback and source-review checks.
FORMAT: `uv run ruff check src/ontofill/phases/p3_fanout tests/test_discovery_providers.py && uv run ruff format --check src/ontofill/phases/p3_fanout tests/test_discovery_providers.py`.

## C4b: multi-source completion loop
- [ ] Execute 3–4 discovered source objectives, resolve entities, reconcile conflicts with double critic for high-stakes statuses, and reopen fan-out on remaining gaps.
- [ ] Keep all observations evidenced and recorded mock outputs scratch-only; report metrics honestly.
FILES: `src/ontofill/workflow.py`, `src/ontofill/phases/p5_execute/`, `src/ontofill/refiner/`, `packages/ontofill-scrape/src/ontofill_scrape/extract.py`, `tests/test_multisource_workflow.py`.
TASK: Make the C3 one-source run expand through source types until DoD or budget.
DONE: `uv run pytest -q tests/test_multisource_workflow.py` shows 3 distinct synthetic source types and traced gap-triggered fan-out.
FORMAT: `uv run ruff check src/ontofill/workflow.py src/ontofill/phases/p5_execute src/ontofill/refiner tests/test_multisource_workflow.py && uv run ruff format --check src/ontofill/workflow.py src/ontofill/phases/p5_execute src/ontofill/refiner tests/test_multisource_workflow.py`.

## L1: live inference and provenance
- [ ] Fetch available model IDs from `/v1/models`; force a single typed tool call for Vultr JSON decisions; assign distinct model families to generation and critique.
- [ ] Regenerate recorded phase 1–2 artifacts in a scratch live case; add Jev only if its public API is clear and keep Vultr as the confirming decision maker.
FILES: `src/ontofill/inference/`, `src/ontofill/phases/p1_scope/`, `src/ontofill/phases/p2_ontology/`, `schemas/common.schema.json`, `schemas/metrics.schema.json`, `tests/test_inference.py`, `tests/test_workflow.py`.
TASK: Make live phase decisions contract-valid and auditable using the active Vultr subscription.
DONE: `uv run pytest -q tests/test_inference.py tests/test_workflow.py` and a scratch phase 1–2 live run whose PRD/factors provenance is `vultr`.
FORMAT: `uv run ruff check src/ontofill/inference src/ontofill/phases/p1_scope src/ontofill/phases/p2_ontology tests/test_inference.py && uv run ruff format --check src/ontofill/inference src/ontofill/phases/p1_scope src/ontofill/phases/p2_ontology tests/test_inference.py`.

## C4c: Vultr dry-run infrastructure (parallel worker)
- [ ] Plan two Ubuntu 24.04 VMs, private network, gVisor sandbox, Object Storage, NetBird peers, and private dispatch; dry-run without credentials.
FILES: `infra/vultr/`, `tests/test_vultr_plan.py`, `.env.example` (worker ownership).
TASK: Make cloud provisioning idempotent and reviewable before credentials arrive.
DONE: `uv run pytest -q tests/test_vultr_plan.py && uv run python infra/vultr/provision.py --dry-run` (or worker's documented CLI).
FORMAT: `uv run ruff check infra/vultr tests/test_vultr_plan.py && uv run ruff format --check infra/vultr tests/test_vultr_plan.py`.

## C2: sandbox gate
- [x] Docker browser pod, TDD domain allowlist, file:// bronze captures and step logs.
- [x] Check: `uv run pytest -q tests/test_sandbox.py -s` → 3 passed, including live Docker allowed capture, disallowed subrequest blocked by proxy, and step trace fields.
FILES: `sandbox/`, `src/ontofill/sandbox/`, `src/ontofill/lake/`, `tests/`.
TASK: Capture only allowlisted public pages in a contained browser, recording evidence and trace.
DONE: `uv run pytest -q tests/test_sandbox.py`.
FORMAT: `uv run ruff check . && uv run ruff format --check .`.

## C3: five-phase walking skeleton
- [x] Brief to PRD, ontology, discovered source, local TDD, S1 extraction, silver, SHACL, gold and export in a labeled recorded scratch run.
- [x] Local check: 107 tests; real-brief `mock-` run yielded one evidenced supplier with 3 of 6 core fields, valid metrics, jobs proof and replayable export; remained paused and never advanced latest.
- [ ] Live C3 check: Vultr inference/S3 credentials, three human approvals, and app-owned real export with the required core-field coverage.
FILES: `src/ontofill/`, `packages/ontofill-scrape/`, `pyproject.toml`, `uv.lock`, `schemas/`, `tests/`, `.env.example`, `.gitignore`, `README.md`.
TASK: Run one source end to end using discovered sources and evidence-backed gold values.
DONE: local synthetic and real-brief mock check above; live check remains pending credentials and approvals and cannot be replaced by recorded metrics.
FORMAT: `uv run ruff check . && uv run ruff format --check .`.
