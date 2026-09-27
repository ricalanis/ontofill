# R44 P3 granularity

The first composed gate passed 570 tests/5 skips and failed seven recorded legacy SearchClient fixtures that carry no P3 access-path metadata. The live P4 invariant remains strict. A narrowly scoped recorded-only compatibility path now allows missing metadata solely in mock previews; explicit aggregate/unknown mock paths still fail. A new live missing-path regression and the seven formerly failing cases passed together (26 focused tests, Ruff clean). The managed composed successor gate passed **578 tests, 5 skips**, Ruff lint/format, both JSON schema syntax checks, and diff checks.

- [x] FILES: `src/ontofill/phases/p4_local_scoping/phase.py`, `tests/test_local_scope.py`. TASK: independently reject aggregate/unknown/legacy objectives before model call/cache, and scope an entity-level TDD. DONE: worker managed `p4-granularity-workflow-rejection` passed 12 P4 tests and Ruff lint/format/diff checks at `59d32af`; root composed full gate remains. FORMAT: Ruff check/format.

The first full gate passed 565 tests/5 skips and failed three older synthetic fixtures that claimed per-library capability from an identity-free generic search form/table. Those fixtures now include a captured `name` field while preserving redirect and prompt-screening assertions. The second full gate passed 568 tests/5 skips and Ruff lint, then stopped on one deterministic format line; Ruff formatted it. A focused cache regression now proves old capability claims are recaptured before reuse.

- [x] FILES: `src/ontofill/phases/p3_fanout/discovery_loop.py`, `schemas/objectives.schema.json`, `schemas/source-candidate.schema.json`, `tests/test_r44_granularity.py`. TASK: reject aggregate statistics as providers of primary-class DoD properties, accept grounded entity records, and ask for per-entity datasets. DONE: `uv run pytest -q tests/test_r44_granularity.py tests/test_r35_p3_capability.py` passed 6 tests after red baseline. FORMAT: focused Ruff check/format passed.
- [x] FILES: P3 cache and `tests/test_r44_granularity.py`. TASK: invalidate old objectives and candidate skip keys without record granularity. DONE: focused 4 tests passed, including a full second discovery after stripping legacy fields. FORMAT: Ruff check/format passed.
- [x] FILES: same plus P4 worker's scoped files. TASK: compose R44 P3/P4 and preserve synthetic workflow behavior. DONE: managed full successor passed 578 tests/5 skips, Ruff lint/format, schema JSON and diff checks. FORMAT: Ruff check and format over engine-owned paths.
- [ ] FILES: `coord/status/codex-ontofill.md`, `coord/GAPS.md`. TASK: record green SHA and gate, then pre-push scan and push only ontofill main. DONE: origin/main contains the verified commit; VM deployment waits for no-engine window. FORMAT: markdown.

# R33b thin candidate follow-up

FILES: `src/ontofill/phases/p3_fanout/discovery_loop.py`, `tests/test_r33b_redirect_preview.py`, task records.
TASK: Require a captured, screenshot-backed, critic-supported redirect preview before creating a new source review packet.
DONE: The focused managed check in `GOAL.md` and full engine gate pass; the red baseline rejects old thin-packet behavior.
FORMAT: Ruff lint and format for edited Python files.

1. [x] Record failing further-redirect and TLS-preview regressions.
2. [x] Gate new source review packet creation on complete preview and record failure reason.
3. [x] The focused gate passed 6 tests and Ruff. Two full-gate attempts exposed a trace-null defect and then an old fixture whose trusted root wrongly became capable; that exact check stopped under the two-attempt guard. The R33b+R39 combined gate passed 566 tests, 5 skips, Ruff lint/format and diff check. Hold VM deploy until a no-engine window.

# Prior goal: R38 robots unavailable policy

1. [x] Add synthetic status regressions: ordinary robots 4xx allows pages under the page cap; 429, 5xx, timeout and unreachable keep the conservative stop. Baseline failed at the expected policy assertions.
2. [x] Record normalized robots status through sandbox output and the P3 site-graph schema without changing the existing graph envelope version.
3. [x] Run the focused managed pytest/Ruff/schema/diff DONE gate and commit the isolated branch; the root full gate passed 559 tests/5 skips. Public push and live SAT proof are separate.
FILES: `sandbox/agent-pod/spider_policy.py`, `src/ontofill/phases/p3_fanout/site_graph.py`, `schemas/site-graph.schema.json`, `tests/test_spider_policy.py`, `tests/test_site_graph.py`, `GOAL.md`, `TODO.md`, `NOTES.md`.
TASK: Apply RFC 9309's unavailable versus unreachable distinction while honoring R38's stricter 429 stop, and preserve each robots outcome in the graph.
DONE: the managed focused gate in `GOAL.md` passes with branch-specific synthetic coverage and schema validation.

# R32 P2 structural DoD guards

- `FILES`: `src/ontofill/phases/p2_ontology/phase.py`, `src/ontofill/refiner/export.py`, `schemas/dod-queries.schema.json`, `tests/test_r31_ontology_salvage.py`, `tests/test_r32_p2_guards.py`, `tests/test_refiner.py`, and task records.
- `TASK`: measure fractional completeness targets as the share of primary entities linked by the explicit relation ID (or the unique matching relation-count query in a legacy approved artifact) that meet `min_ratio`; retain count behavior and the R32 structural checks.
- `DONE`: root composed gate passed 27 tests; full engine gate passed 549 tests/5 skips with Ruff, schema and diff checks. Public push and guarded VM deploy are separate.
- `FORMAT`: Ruff lint/format for edited engine and test files; parse the DoD query schema.

1. [x] Inspect commits `e1d3633` and `a236b21` plus the dirty approved-cache fixture; preserve the fixture's two schema drafts.
2. [x] Add red tests for explicit share query structure, legacy approved-query validation, one complete entity among ten linked entities, an unrelated-relation-only entity, and a zero denominator.
   - `DONE`: the first managed baseline reproduced missing P2 share validation and raw-count evaluation.
3. [x] Add `measure: share` to the existing completeness aggregate, enforce its primary-domain relation, and preserve legacy approved artifacts without rewriting their bytes.
4. [x] Commit the local patch and report focused-check causes. The focused gate reached its two-attempt limit; root will run a distinct composed integration gate after R40/R41. Do not push or deploy.


# R33b redirect review preview

Integrated on current public main plus the exact-host sandbox guard: 524 tests passed, 5 skipped, Ruff lint/format, schema parse and diff checks clean. The first combined run passed pytest but found three unused/import-order issues in the new synthetic test; the second managed run passed after that static cleanup. Public push and VM deploy remain separate, with deploy only in a no-engine window.

- [ ] Add a recorded synthetic redirect fixture proving one bounded preview uses only the target host, and that preview-derived source fields and critic-supported access paths appear before the review checkpoint.
- [ ] Keep follow-on redirects unapproved; prove preview denial skips the target and digest-approved bytes remain unchanged.
- [ ] Add only any additive candidate fields required by R33b/R35, preserving thin legacy packets.
- [x] Run the composed managed full test/Ruff/schema/diff DONE gate and record each attempt.
- [ ] Commit the isolated worktree and report the SHA to root; do not push or deploy.

FILES: `src/ontofill/phases/p3_fanout/discovery_loop.py`, optional `schemas/source-candidate.schema.json`, focused synthetic tests, `GOAL.md`, `TODO.md`, `NOTES.md`.
TASK: Capture each new untrusted redirect target once in a bounded sandbox job with an exact-host allowlist before its source-review request; bind observed landing URL, title, screenshot, type, and validated capability to the review packet without granting source authority.
DONE: the managed gate in `GOAL.md` proves preview capture/evidence, no further-host follow, denial skip, approved-byte preservation, schema compatibility, Ruff and diff checks.
FORMAT: Ruff format for edited Python files; JSON-tool parse for edited schemas.

Implementation notes: the preview supplies `exact_hosts=[target_host]`; the sandbox/proxy enforcement is in the parent-owned companion commit `f960ca0` and must be included when composing. A tier suggestion requires the full normalized trusted publisher-kind phrase in bounded parse-pod page text; authority remains `review`, and an absent or ambiguous phrase leaves the tier `unknown`.

# R35/R36 integration

- [ ] Review R35 capability evidence: exact captured access path, authority verdict, bounded form metadata, persistence in source/objective; registry accepted, blog refused.
- [ ] Review R36 navigation/document/DNS core: no egress bypass, no unbounded payload/error, bronze lineage and six-checkpoint job, additive schemas. TLS/AIA is a later slice.
- [x] Add a recorded direct-document P5 regression: a sandbox-captured CSV source URL parses its `document_key` in the networkless parse pod and emits literal cells without a second fetch or HTML landing; PDF text remains bounded and never invents values. Managed red test failed on the old S1 fallback, then the focused second check passed 2 tests plus Ruff lint/format and diff.
- [ ] Integrate workers on latest origin/main and run the managed full DONE gate.
- [ ] Scan outgoing commits, push only main, wait for a no-engine VM window, fast-forward and sync; mark R35/R36 READY FOR VERIFY and tell orchestrator to relaunch.

# Prior task: R30 P2 factor grounding

- `FILES`: `src/ontofill/phases/p2_ontology/phase.py`, `schemas/factors.schema.json`, `tests/test_r30_factors.py`, `GOAL.md`, `TODO.md`, `NOTES.md`.
- `TASK`: validate grounded factors against published PRD records, give exact retry feedback, and recast unsupported factors conceptual only after bounded retries.
- `DONE`: the managed gate defined in `GOAL.md` passes with recorded grounding regressions, compatibility coverage, Ruff, schema parsing, and `git diff --check`.
- `FORMAT`: `uv run ruff format` for changed Python files; `python3 -m json.tool schemas/factors.schema.json`.

1. [x] Add recorded tests for a grounded factor with no evidence and for an exact PRD citation; prove invalid output is retried with factor-specific feedback.
   - `DONE`: managed red baseline attempt 1 failed both new tests on current P2 behavior and passed 11 compatibility tests; empty evidence was accepted and the citation shape was rejected by the old schema.
2. [x] Add schema support for PRD record citations, strict factor-level evidence validation, and a bounded conceptual fallback that drops unsupported evidence.
   - `DONE`: recorded retry keeps an exact PRD citation grounded; after three unsupported proposals, only the unsupported factor becomes conceptual and its invented URL is removed.
3. [x] Run the focused managed compatibility/Ruff/schema gate; record the result and commit the isolated branch.
   - `DONE`: 94 selected tests, Ruff lint/format, schema parsing, and diff checks passed; the isolated commit contains only assigned paths.
4. [x] Review the cache path against the R30 invariant and run the full integration gate.
   - `DONE`: a cached grounded factor without a supported citation is redrafted; the managed full engine gate passed 470 tests, five skipped, with Ruff clean.

# R31 P2 schema salvage

- `FILES`: `src/ontofill/phases/p2_ontology/phase.py`, `src/ontofill/phases/p2_ontology/README.md`, `schemas/ontology-recommendations.schema.json`, `schemas/README.md`, `tests/test_r31_ontology_salvage.py`, `GOAL.md`, `TODO.md`, `NOTES.md`.
- `TASK`: name all semantically invalid schema proposals in repair feedback; after retry exhaustion set aside only invalid rules/relations, then revalidate and write the valid ontology and unresolved recommendation artifact.
- `DONE`: the retained-relation managed gate in `GOAL.md` passed with 107 selected tests, Ruff lint/format, JSON schema parse, and `git diff --check`.
- `FORMAT`: `uv run ruff format` for changed Python files; `python3 -m json.tool` for the added schema.

1. [x] Add a recorded failing test using generic class/property IDs for cross-class rules ×3, precise prompt feedback, valid-schema salvage, and set-aside provenance.
   - `DONE`: the managed red baseline failed both new regressions with current P2 exhausting after attempt 3; no ontology artifact existed.
2. [x] Add stable diagnostics and narrow retry-exhaustion salvage for invalid rule/relation proposals; write schema-validated unresolved recommendations only after ontology and DoD queries validate.
   - `DONE`: recorded tests prove invalid rules/relations are set aside, valid rules and typed relations survive, and an unrelated class/property defect still raises without artifacts.
3. [x] Validate the focused P2 contract, formatting, and schemas; record results here and commit this isolated branch.
   - `DONE`: managed focused pytest, Ruff lint/format, JSON schema checks, and `git diff --check` passed; branch commit `6248563` is integrated for the full gate.

# Prior task work

## R26d current slice
- [x] Add a recorded PRD loop regression: a fixed earlier critic objection disappears, while a current unresolved objection remains.
- [x] Recompute persisted `open_issues` from the final loop result, dropping inherited entries after a successful current review.
- [x] Run the focused managed pytest/Ruff gate, commit the isolated branch, and report its SHA to root.
FILES: `src/ontofill/phases/p1_scope/phase.py`, `tests/test_r26d_open_issues.py`, `GOAL.md`, `TODO.md`, `NOTES.md`.
TASK: Keep PRD `open_issues` aligned with the current draft's checks and critic.
DONE: the recorded resolved/unresolved objections pass through the P1 persistence path, and the managed focused tests plus Ruff check/format pass.
FORMAT: `uv run ruff check src/ontofill/phases/p1_scope/phase.py tests/test_r26d_open_issues.py && uv run ruff format --check src/ontofill/phases/p1_scope/phase.py tests/test_r26d_open_issues.py`.

## R29 current slice
- [x] Review and integrate independent inference-header and sandbox-outcome lanes.
- [x] Wire workflow run_id before model catalog request and reuse each inference call's step_id in its trace row, including standalone refine.
- [x] Integrate P3 source labels/hosts and stop reasons; verify its corrected workflow fixture in the root integration gate.
- [x] Run full managed gate, pre-push scan, push only main, fast-forward VM, and hand live attribution check to orchestrator.

## R26c current slice
- [x] Reproduce false secondary subjects, full redraft and optional tier with the recorded v4 plus fourth denial.
- [x] Require tier in new live PRD tool output, preserving legacy artifact validation.
- [x] Patch only the denied publisher domains from the digest-reviewed PRD; retain all other fields.
- [x] Pass focused and full managed checks: 445 passed, 5 skipped; Ruff clean.
- [ ] Pre-push scan, push main, fast-forward control VM, and hand the next console run to the orchestrator.

## R29(c,d): P3 source identity and status stop reasons
- [x] Add optional `source_label` and `source_host` to trace/status schemas, preserving older artifact validity.
- [x] Derive source label/host from captured P3 candidate artifacts; include them on source-linked P3 trace steps and `status.sources[]`.
- [x] Ensure paused, done, and failed status snapshots always have a non-empty reason, preserving explicit causes and using a state/checkpoint fallback only when absent.
- [x] Add recorded synthetic assertions for the named source in discovery trace/status and for non-null failure reason.
- [ ] Run the managed focused tests plus Ruff on the final workflow fixture; the two-strategy progress guard escalated this seam to root for an independent integration gate after merge.
FILES: `src/ontofill/phases/p3_fanout/`, `src/ontofill/runfeed.py`, limited source/status/reason wiring in `src/ontofill/workflow.py`, `schemas/`, focused tests and task notes.
TASK: Make P3 source records legible in the live run feed and keep terminal status explanations available.
DONE: recorded synthetic discovery proves a named source label/host appears in its trace and `status.json`, and failed status has a non-empty reason; the managed focused tests and Ruff check/format gate pass.
FORMAT: Ruff check and format on edited engine and test paths.

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

## R40: P5 off-domain document review
- [x] Add a failing synthetic test for an off-domain download link: persist parent/page/link bronze evidence and a digest-bound source checkpoint, with no GET before approval.
  - CHECK: `uv run pytest -q tests/test_r40_p5_download_review.py tests/test_source_candidate_schema.py` fails on the missing review behavior.
- [x] Implement additive candidate provenance, exact digest/fingerprint approval validation, deny/stale refusal, exact-host sandbox GET, and networkless D0 parsing.
  - CHECK: `uv run pytest -q tests/test_r40_p5_download_review.py` proves each approval branch and parse lineage.
- [ ] Compose the parent-owned workflow pause bridge and parser-owned capped `.xls` pod implementation; preserve allowed-domain GET behavior and the browser-agent guard.
  - CHECK: focused synthetic workflow/controller assertions show pending source review pauses before export and same-domain GET remains allowed.
- [x] Run the managed focused P5, candidate-schema, Ruff, and format gates; commit locally without pushing.
  - CHECK: R40 DONE command, Ruff, `git diff --check`, and protected-path scan pass; report the commit SHA.
FILES: `src/ontofill/phases/p5_execute/`, `src/ontofill/workflow.py` only for a P5 source-review pause seam, `src/ontofill/case/checkpoints.py`, `schemas/source-candidate.schema.json`, `schemas/approved.schema.json`, R40 synthetic tests, `GOAL.md`, `TODO.md`, `NOTES.md`.
TASK: Treat off-domain document links published by a trusted captured source as review leads, then fetch and parse only after an approval bound to exact candidate bytes.
DONE (P5-owned slice): candidate evidence names the parent source/page, bronze capture, screenshot, link index/text and URL; missing/stale digests refuse, deny skips without re-requesting, and an approved exact-host D0 capture is parsed networklessly before evidence emission. Parent workflow and parser commits are still needed for a full R40 claim.
FORMAT: Ruff check and format on all edited engine/schema/test files.

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
# R40 legacy XLS parser

- [x] Add a synthetic BIFF workbook and failing bronze-to-parse-pod tests for explicit `xls`, auto-detection, distinct XLSX handling, six-checkpoint output, row limits, and missing-runsc refusal.
  - `DONE`: managed red attempt 1 failed exactly where expected: explicit `xls` was rejected by adapter validation; auto detection classified OLE BIFF as unknown; and the `.xls` no-runsc case was rejected before Docker preflight for the same adapter reason. The other 21 parser tests passed and 2 containment tests were skipped.
- [x] Add pinned `xlrd` to the networkless parse-pod image and dev test group; parse BIFF in the pod and add adapter format validation without an in-process legacy parser.
- [x] Run managed attempt 2 for the focused DONE gate; record the exact failure and stop under the two-attempt rule.
  - `DONE`: 25 parser tests passed and 2 containment tests were skipped; Ruff then reported BLE001 on the three fail-closed malformed BIFF/OLE catches. Root authorized narrow suppressions and will verify the composed branch; this worktree has no green managed DONE result.
- [x] Commit the isolated parser patch and report SHA/checks/risks to root; the parent composed gate passed.

FILES: `sandbox/parse-pod/runner.py`, `sandbox/parse-pod/Dockerfile`, `src/ontofill/sandbox/parse.py`, `pyproject.toml`, `uv.lock`, `tests/test_sandbox_parse.py`, synthetic BIFF fixture, `GOAL.md`, `TODO.md`, `NOTES.md`.
TASK: Add bounded legacy `.xls` BIFF parsing from bronze inside the runsc pod only, with explicit and auto detection, preserving six-checkpoint proof and default runsc refusal.
DONE: parent composed gate passed 52 tests/2 skips; full engine gate passed 541 tests/5 skips, Ruff lint, engine-owned format, lock and diff checks. Attempt 1 was the expected parser red baseline; attempt 2 needed a narrow Ruff BLE001 annotation, verified in the composed gate.
FORMAT: Ruff format for changed Python files.


## R40 malformed XLS parse diagnostics

1. [x] Add a synthetic malformed-BIFF regression that exercises the pod runner and checks code, diagnostic bounds, secret/raw-byte absence, and all six receipt checkpoints. The red baseline failed at the expected missing `message` field.
2. [x] Emit stage-specific safe diagnostic text from the pod and validate/propagate only the bounded format through the adapter into task outcome and trace. The stable task reason remains `invalid_xls`; the job outcome includes the code prefix plus safe detail.
3. [x] Run the managed focused test/Ruff/format/diff gate, inspect the owned diff, and commit only the allowed paths and task notes. Implementation committed locally as `fd38f2a`; no push or deploy.

Managed check: `agent-progress run --task r40-parse-error --paths sandbox/parse-pod/runner.py,src/ontofill/sandbox/parse.py,tests/test_sandbox_parse.py --check malformed-biff-diagnostic --strategy bounded-stage-diagnostic-without-payload-text --hypothesis 'A malformed synthetic BIFF workbook keeps invalid_xls as its stable task reason and emits bounded safe diagnostic text in its six-checkpoint job and task trace, without leaking raw bytes or secret-like text.' -- sh -c 'uv run pytest -q tests/test_sandbox_parse.py && uv run ruff check sandbox/parse-pod/runner.py src/ontofill/sandbox/parse.py tests/test_sandbox_parse.py && uv run ruff format --check sandbox/parse-pod/runner.py src/ontofill/sandbox/parse.py tests/test_sandbox_parse.py && git diff --check'`
Result: attempt 1 was the intended red baseline (`KeyError: message`); attempt 2 passed 26 tests, skipped 2 containment tests, and passed Ruff lint, format, and `git diff --check`.

## R47 source-contained D0 fetch

1. [x] Reproduce one fetch CaptureError followed by a successful objective.
2. [x] Contain D0 transport CaptureError, preserving the prior page trace and leaving safety errors fatal.
3. [ ] Integrate the proxy pod fix and its focused test.
4. [ ] Run the full gate and pre-push scan, push, and deploy in the authorized zero-engine window.
