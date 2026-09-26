# Work list

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
