# Work list

## R26c current slice
- [x] Reproduce false secondary subjects, full redraft and optional tier with the recorded v4 plus fourth denial.
- [x] Require tier in new live PRD tool output, preserving legacy artifact validation.
- [x] Patch only the denied publisher domains from the digest-reviewed PRD; retain all other fields.
- [x] Pass focused and full managed checks: 445 passed, 5 skipped; Ruff clean.
- [ ] Pre-push scan, push main, fast-forward control VM, and hand the next console run to the orchestrator.

## R26b live PRD authority clauses
- [x] Integrate the separately owned exact-text recorded regression.
- [x] Preserve abbreviations in secondary clauses and merge marker-only fragments with their subject.
- [x] Accept a specific-domain secondary publisher whose kind matches the clause subject; report the missing subject precisely.
- [ ] Run the full engine gate and Ruff, pre-push scan, push main, fast-forward the control VM, then tell the orchestrator to resume.
FILES: `src/ontofill/phases/p1_scope/phase.py`, R26b tests and task records. The worker owns tests only; this lane owns the P1 source.
TASK: make the real PRD authority objection satisfiable without hardcoding a case publisher/domain or changing the live case.
DONE: recorded exact revisions pass with valid secondary publishers and fail with actionable subject-specific feedback when missing.
FORMAT: Ruff check and format on edited Python.

## R26 output budget and diagnostics
- [x] Reproduce a truncated PRD section and ontology response; assert the retry raises the output cap before success.
- [x] Preserve bounded validator errors for failed typed calls, and show them in P1 pause status, CLI and trace.
- [x] Verify the reported live trace key exists in the case's S3 lake without editing the case.
- [ ] Integrate the separately owned approval-cache fix for PRD, factors and ontology.
- [ ] Run the full managed gate, pre-push scan, push main and fast-forward the control VM.
FILES: `src/ontofill/inference/decision.py`, `src/ontofill/workflow.py`, focused tests and task records; approval sublane owns P1/P2 phase modules.
TASK: make R26 pauses recoverable and explainable without changing the real case or its approvals.
DONE: truncated first answer succeeds at a larger cap; exhausted validation leaves exact bounded errors in status and trace; approved artifacts survive cache-key drift.
FORMAT: Ruff check and format on edited Python.

## R24 PRD authority checkpoint
- [x] Reproduce policy failure before approval and prove a corrected answer receives validator feedback.
- [x] Preserve an existing approval and artifact bytes if an older approved PRD now fails the policy.
- [x] Guard the final PRD after the phase loop, before any artifact or approval write.
- [x] Run focused and full managed gates; finish the pre-push scan, main push, and VM deployment.
FILES: `src/ontofill/phases/p1_scope/phase.py`, `tests/test_r24_prd_policy.py`, and these task records.
TASK: break the PRD approve/rerun loop without invalidating a valid approval.
DONE: a policy-failing candidate is corrected before the checkpoint; approved valid PRD rerun has no model call and no stale marker; invalid legacy approval is preserved while paused.
FORMAT: Ruff check and format on edited Python.

## R21 generic schemas and judges pointer
- [x] Remove legacy case-domain schemas and identifiers from `schemas/`.
- [x] Make the genericity guard scan schema filenames and contents.
- [x] Keep legacy app tests on app-owned historical schemas in a separate handoff branch.
- [x] Link the read-only judges console without copying its password.
- [x] Run the full engine gate; finish the pre-push scan, main push, and control VM update.
FILES: `schemas/`, `tests/test_no_case_vocabulary.py`, `tests/test_schemas.py`, `README.md`.
TASK: finish R21 without bringing case vocabulary back into the engine.
DONE: full engine test and Ruff gate plus clean public pre-push scan.
FORMAT: Ruff check and format on engine Python.

## R22 model validation retries (priority before R21)
- [x] Add bounded validator-error feedback with one trace record per attempt in the typed decision path.
- [x] Make P2 prerequisites pause on exhausted validation without changing approved artifacts.
- [x] Make P4/P5 validation failures local to the source and continue the other sources.
- [x] Verify recorded two-attempt recovery and three-attempt source isolation; run full pytest and Ruff.
FILES: `src/ontofill/inference/`, phase modules, `src/ontofill/workflow.py`, focused tests.
TASK: prevent one malformed typed model answer from failing the whole run.
DONE: R22 recorded proof plus full gate and safe deployment.
FORMAT: Ruff check and format on edited Python files.

## R22: bounded retries for semantic inference outputs outside P2/P4/P5
- [x] Add focused synthetic regressions for P3 discovery/site-graph validation retries, legacy injected-search exhaustion, refiner classification exhaustion, and repair generation/patch exhaustion.
- [x] Route owned model outputs through `complete_validated`, keeping semantic checks inside validators and preserving source/step-scoped failure behavior.
- [x] Commit the verified slice without pushing and report the SHA.
FILES: `src/ontofill/phases/p3_fanout/{phase.py,leads.py,discovery_loop.py,site_graph.py}`, `src/ontofill/refiner/core.py`, `src/ontofill/repair/pattern_a.py`, focused tests.
TASK: Retry schema and semantic model-output failures at most three times with exact feedback and call-log status; do not retry safety, sandbox, capture, or approval failures.
DONE: the managed gate in `GOAL.md` passes, exhausted refiner classification stays unclassified, and P3/repair failures remain source/step scoped.
FORMAT: Ruff check/format for owned modules and focused tests.
CHECK: `r22-other-semantic-retries-lint-successor` / `focused-pytest-ruff-lint-successor` passed 63 tests plus Ruff check/format. The two earlier distinct test/ruff causes and the authorized successor history are recorded in `NOTES.md`.

## R8b attribution merge
- [x] Merge the reviewed containment gateway attribution fix without changing other demo behavior.
- [x] Verify the emitted quarantine step ID matches the gateway request and no existing investigation run can be targeted.
FILES: `tools/containment/src/ontofill_containment/main.py`, `tools/containment/tests/test_containment.py`.
TASK: Attribute the containment gateway call to its persisted quarantine step.
DONE: `uv run pytest -q tools/containment/tests/test_containment.py` plus the exact gateway-step assertion.
FORMAT: `uv run ruff check tools/containment/src/ontofill_containment/main.py tools/containment/tests/test_containment.py && uv run ruff format --check tools/containment/src/ontofill_containment/main.py tools/containment/tests/test_containment.py`.
CHECK: managed focused gate passed 20 tests and Ruff lint/format after adding the workspace-excluded package to `PYTHONPATH`; first collection-only attempt lacked that path.

## R10 workflow handoff
- [x] Pass a live decision client into P5 refinement and both exports; publish classifier call usage before export.
- [x] On `refine_case`, require the screened gateway for live provenance and append classification calls to the existing run trace.
- [x] Add focused workflow tests and run the acceptance gate after the R10 branch is integrated.
FILES: `src/ontofill/workflow.py`, `tests/test_r10_workflow.py`.
TASK: Make separate-critic taxonomy classification visible and honest in production run/refine paths.
DONE: `uv run pytest -q tests/test_r10_workflow.py tests/test_r10_taxonomy.py tests/test_workflow.py`.
FORMAT: `uv run ruff check src/ontofill/workflow.py tests/test_r10_workflow.py && uv run ruff format --check src/ontofill/workflow.py tests/test_r10_workflow.py`.

## R9b: screened page content in engine prompts
- [x] Add a synthetic recorded test for P3 selection and captured-page prompt spans; wrap the unwrapped P3 candidate title/snippet.
- [x] Assert P5 extracted-page spans and hostile page-derived repair stderr remain inside escaped `<page_content>` boundaries.
- [x] Keep the new gateway token preferred and prove fallback to the legacy environment name with synthetic values.
- [x] Run the focused managed test and Ruff checks, then commit the isolated branch.
FILES: `src/ontofill/inference/page_content.py`, `src/ontofill/inference/decision.py`, `src/ontofill/phases/p3_fanout/`, `src/ontofill/phases/p5_execute/`, `src/ontofill/repair/`, focused tests.
TASK: Keep every page-derived string sent by the engine inside a gateway-screenable content span.
DONE: `uv run pytest -q tests/test_r9b_prompt_screening.py tests/test_pattern_a.py tests/test_inference.py` and Ruff check/format for the edited engine and test files.
FORMAT: Ruff check and format on changed Python files.

## R16: cell browser liveness and idle CDP
- [x] Reproduce the relay idle close with a failing local test and seek the demo cell's memory/timeout evidence; no demo job feed was available.
- [x] Keep the in-pod CDP bridge and sidecar relay open across idle polls; detect browser/target death in status and action counting, with an honest failed job record and measured memory (synthetic gate).
- [ ] Run focused managed pytest/Ruff; merge and push after the pre-push scan, then update the control VM and run the five-minute live gVisor proof plus forced renderer-death proof.
FILES: `sandbox/agent-pod/cdp.py`, `sandbox/egress/relay.py`, `src/ontofill/sandbox/cells.py`, `schemas/jobs.schema.json`, `tests/test_cell_relay.py`, `tests/test_cells.py`.
TASK: Survive idle CDP gaps and stop/report a dead browser target in the substrate.
DONE: `uv run pytest -q tests/test_cell_relay.py tests/test_cells.py` and the two live R16 proofs above.
FORMAT: `uv run ruff check src/ontofill/sandbox sandbox/agent-pod sandbox/egress tests/test_cell_relay.py tests/test_cells.py && uv run ruff format --check src/ontofill/sandbox sandbox/agent-pod sandbox/egress tests/test_cell_relay.py tests/test_cells.py`.

## R3: P5 to browser controller S1
- [x] Add an isolated P5 controller helper and route S1-start/no-download TDDs through the existing session MCP API.
- [x] Convert only target-property fields with a safe screen, allowed URL, selector, and mirrored screenshot evidence.
- [x] Drain controller verify/action_gate/quarantine steps into the run feed and always close the session.
- [x] Add recorded fake-controller tests; checked for live configuration, which was unavailable.
FILES: `src/ontofill/phases/p5_execute/`, the P5 dispatch call in `src/ontofill/workflow.py`, `tests/test_p5_controller.py`, `.env.example`.
TASK: Connect P5's S1 path to BrowserAgentClient while preserving evidence and controller safety decisions.
DONE: `uv run pytest -q tests/test_p5_controller.py tests/genericity && uv run ruff check src/ontofill/phases/p5_execute src/ontofill/browser_agent.py tests/test_p5_controller.py && uv run ruff format --check src/ontofill/phases/p5_execute src/ontofill/browser_agent.py tests/test_p5_controller.py`.
FORMAT: Ruff check and format across edited engine/test files.

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

## R11: bronze re-refine seam
- [x] Add a falsifying synthetic export test where an approved current ontology contains a property absent from the old column macro, but its literal column exists in a trace-referenced bronze file; replay must fill it without any browser/fetch call.
- [x] Implement a pure replay helper that returns observations and deterministic lineage trace steps, reads only completed trace-referenced bronze keys, and preserves source, selector, screenshot, capture time, and original-step parentage.
- [x] Keep the CLI surface accurate and expose the helper contract for root's `refine_case` integration; do not add a second persistence/refine execution path.
- [x] Run the managed helper/CLI gate after the whitespace-tolerant assertion fix: 11 passed under the one explicitly authorized successor attempt.
- [x] Wire `refine_case` to combine replay observations with silver, persist de-duplicated parented replay steps before its single export, and restore the previous live trace if export fails.
FILES: `src/ontofill/workflow.py`, `tests/test_bronze_replay.py`, `GOAL.md`, `TODO.md`, `NOTES.md`.
TASK: Re-refine against the current ontology from successful bronze captures already referenced by this run's trace, without browsing or duplicating export.
DONE: `uv run pytest -q tests/test_bronze_replay.py tests/test_cli.py` proves a new property reaches gold through `refine_case`, trace lineage persists once, export runs once, and export failure restores the original live trace.
FORMAT: `uv run ruff check src/ontofill/workflow.py tests/test_bronze_replay.py && uv run ruff format --check src/ontofill/workflow.py tests/test_bronze_replay.py`.
CHECK: `r11-refine-case-replay` / `r11-refine-case-replay-export-lineage`, exact command `uv run pytest -q tests/test_bronze_replay.py tests/test_cli.py`. Initial task attempts stopped after two fixture failures; following the explicit R11 successor authorization, task `r11-bronze-refine-authorized-successor` with strategy `shacl-fixture-provides-ontology-shapes-path` passed 13 tests. It covers the new-property export through `refine_case`, repeated-call trace idempotence, and rollback on export failure.

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

## R2 P5 real-path closure (worker)
- [x] Add falsifying recorded tests for multi-objective passes, per-gap iteration accounting, full-list membership evidence, identifier normalization, and JSON/OCDS flattening.
  - FILES: `tests/test_r2_p5.py`, `tests/genericity/fixtures/`.
  - TASK: Build a synthetic multi-source fixture with at least four distinct source classes; include one gap reopened twice while a different gap first appears and starts at iteration 1; require a false membership value with the complete list's bronze key.
  - DONE: recorded multi-source fixture and focused tests cover the missing R2 behaviors; the newly added workflow scheduling seam remains unverified as recorded in `NOTES.md`.
  - FORMAT: Ruff format on the new test/fixture files.
- [x] Implement objective scheduling and per-gap iteration accounting in the P5 run loop.
  - FILES: `src/ontofill/workflow.py`, `src/ontofill/outer_gap.py`.
  - TASK: Draft/execute every selected objective in a pass, aggregate observations and source status, and associate reopen iterations with each criterion ID and its ontology properties.
  - DONE: workflow implementation drafts and schedules all selected objectives in one pass and maintains criterion-keyed iteration counts; the final gate remains pending.
  - FORMAT: Ruff check/format on the changed modules.
- [x] Implement membership, safe identifier normalization, and generic JSON/OCDS flattening.
  - FILES: `schemas/tdd.schema.json`, `src/ontofill/phases/p4_local_scoping/`, `src/ontofill/phases/p5_execute/`, `packages/ontofill-scrape/src/ontofill_scrape/`, relevant schema/toolkit tests.
  - TASK: Accept the announced optional membership declaration; require the target to be boolean and the identifier id to match the selected class. Emit true/false only for a complete untruncated list and cite its actual bronze key in both cases. Screen page-derived prompt content.
  - DONE: implementation and corresponding recorded tests are present; positive/negative membership, truncation refusal, JSON arrays/OCDS flattening, and identifier-equivalence passed before the workflow seam was added.
  - FORMAT: Ruff check/format on owned Python paths.
- [ ] Run the focused, genericity, Ruff and format gates; commit `r2-p5` without pushing or merging.
  - FILES: R2-owned engine modules, schema, test/fixture files, and task notes.
  - TASK: Preserve R3/R7 files and confirm no protected paths changed.
  - DONE: the R2 gate in `GOAL.md` passes, `git diff --check` is clean, and the branch commit SHA is reported to root.
  - FORMAT: same gate as `GOAL.md`.

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
