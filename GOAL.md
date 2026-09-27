# Current goal: R44 per-entity source granularity

P4 independently refuses aggregate, unknown or missing primary-class DoD granularity before inference or TDD cache reuse. Its model prompt and validation rule specify one row/page per entity. A stale local TDD is invalidated when cited granularity evidence changes.

The deprecated injected SearchClient path used by recorded fixtures has no P3 capture metadata. It may preview a mock run without access paths, while any live backend with missing paths fails closed and explicit aggregate/unknown paths fail on every backend. Mock output cannot satisfy checkpoints or the DoD.

P3 confirms a source for primary-class DoD properties only when captured evidence supports a route to records at one row or page per primary entity; aggregate statistics do not count. Search asks for entity-level lists, and P4 independently checks objectives before TDD.

DONE: `uv run pytest -q && uv run ruff check src tests packages infra sandbox && uv run ruff format --check src tests packages infra sandbox && uv run python -m json.tool schemas/objectives.schema.json >/dev/null && uv run python -m json.tool schemas/source-candidate.schema.json >/dev/null && git diff --check` (managed composed successor passed 578 tests, 5 skips, Ruff, schemas and diff checks).

Constraints: synthetic tests; no case artifacts, approvals, credentials, VM deploy or PA-owned code. Public delivery only after the composed gate passes.

# Prior goal: R33b never offer a thin redirect source review

Only a one-shot sandbox preview with a capture, screenshot, landing URL, source class,
and critic-supported DoD capability may produce a new redirect source review packet.
On TLS, a further redirect, parser failure, or critic failure, record the reason in
the trace and leave the target unconfirmed without a human checkpoint.
Preserve digest-bound legacy approval packets unchanged.

DONE: The focused gate passed 6 tests after its red baseline. The first composed R33b+R39 integration gate failed on R39's capability-free synthetic page; after a factual fixture correction, its managed retry passed 566 tests, 5 skips, Ruff lint/format and diff check. The earlier exact R33b full check stopped after two distinct failures; no third run of that check was made.

Constraints: no real case or APPROVED edits, no VM deploy while a run is active, no
PA-owned service changes, no credentials or internal identifiers in public commits.

# Prior goal: R38 robots unavailable policy

Treat robots.txt HTTP 4xx except 429 as unavailable and allow bounded same-domain reads, while stopping conservatively on 429, 5xx, timeouts, and unreachable responses; persist the robots status in each site graph record.

DONE: worker focused gate passed 31 tests; root composed gate passed 31 tests; full engine gate passed 559 tests, 5 skips, Ruff lint, engine-owned format, schema and diff checks. Live SAT crawl awaits a no-engine deploy window.

Constraints: own only the sandbox spider policy, P3 site-graph serialization/schema, synthetic tests, and GOAL/TODO/NOTES. No case or APPROVED edits, console or browser-agent edits, credentials, live run, push, or deploy. Deployment waits for an orchestrator-confirmed no-engine VM window.

# Prior goal: R32 P2 structural guards and DoD share

Require per-entity completeness with approved target 0.8 to measure the share of primary entities linked by the intended relation that meet `min_ratio`, not a raw count. New queries name that relation explicitly; legacy approved queries derive it from their unique matching relation-count query. Keep the core-field class, relation-count class/direction, and rule-label guards, and preserve digest-approved artifact reuse.

DONE (passed on integration): managed `r32-ship/r32-composed` passed 27 focused tests, and `r32-ship/r32-full` passed 549 tests, 5 skips, Ruff lint, engine-owned format, schema and diff checks. The previous worker-only `share-focused` check stopped after two attempts; the root integration fixed its fixture and a static Ruff annotation before these gates.

Constraints: own only P2, refiner/export, DoD query schema, synthetic tests and task notes. Do not modify case artifacts or `APPROVED`. Deploy only in a no-engine window. Preserve digest-approved ontology/query reuse and the existing structural guard feedback.


# Prior goal: R40/R41 reviewed document downloads

Pause P5 on off-domain document links from a trusted page; bind each source decision to its exact candidate digest; fetch only an approved exact host; parse legacy `.xls` BIFF from bronze inside the networkless, resource-capped runsc parser pod. Keep same-host GET behavior and six-checkpoint job proof.

DONE (passed locally): the managed `r40-composed/r40-full` gate passed 541 tests, 5 skips, Ruff lint, engine-owned format, lock and diff checks. The global format check still flags 24 unchanged PA/reference/runner files present on the base.

Scope: P5 source review and workflow pause, source candidate schema/checkpoints, the parse pod, sandbox adapter, synthetic tests and task notes. Do not touch PA-owned console/browser-agent, real case/APPROVED or secrets.

# Prior goal: R35/R36 source capability and capture diagnostics

Integrate authority-checked P3 access-path capability with sandbox document capture and bounded navigation diagnostics. A captured registry search form can become a source; a blog cannot. Binary downloads are captured as bronze documents through the same gVisor proxy, and navigation/DNS/TLS failures remain distinguishable in job and trace receipts.

DONE (currently failing on this base): `agent-progress run --task r35-r36-integration --paths src/ontofill/phases/p3_fanout,src/ontofill/sandbox,sandbox/agent-pod,sandbox/egress,schemas,tests --check r35-r36-full --strategy integrate-reviewed-worker-patches --hypothesis 'An authority-checked registry capability is accepted and a blog refused; binary and failed navigation jobs retain bounded receipts without weakening the proxy' -- sh -c 'uv run pytest -q && uv run ruff check src tests packages infra sandbox && uv run ruff format --check src tests packages infra sandbox && git diff --check'` passes on the reviewed combined tree. Before delivery, scan outgoing commits for secrets/internal data, push only main, and fast-forward the control VM only with no `ontofill run` process alive.

Scope: engine P3, sandbox capture/proxy/pod, additive schemas and recorded tests. No case or APPROVED edits, no PA-owned console or browser-agent edits, no runner restart, no public port changes.

# Prior goal: R30 P2 factor grounding

Every factor labeled grounded cites an existing PRD record by record ID and exact quote; unsupported factors are retried and then recast conceptual without fabricated evidence.

DONE: `agent-progress run --task r30-factor-evidence --paths src/ontofill/phases/p2_ontology/phase.py,schemas/factors.schema.json,tests/test_r30_factors.py --check prd-grounded-evidence-focused --strategy plain-language-record-correction-with-conceptual-fallback --hypothesis 'Retry feedback should state the exact PRD path, record ID, and exact quote in plain wording; after bounded retries preserve supported citations and recast unsupported factors conceptual.' -- sh -c 'uv run pytest -q tests/test_r30_factors.py tests/test_r22_p2.py tests/test_definition_phases.py tests/test_schemas.py && uv run ruff check src/ontofill/phases/p2_ontology/phase.py tests/test_r30_factors.py && uv run ruff format --check src/ontofill/phases/p2_ontology/phase.py tests/test_r30_factors.py && python3 -m json.tool schemas/factors.schema.json >/dev/null && git diff --check'` passes with the recorded P2 regression, focused compatibility tests, Ruff, schema JSON parsing, and whitespace checks.

Integration DONE: the cache-reuse regression passes, and the managed full engine gate passes 470 tests with five skips, Ruff lint/format clean, and whitespace clean on the rebased branch.

Constraints: own only P2 factor grounding, the factor schema, synthetic recorded tests, and these task notes; do not edit P1, console, browser-agent, coordination files, the real case, approvals, push, or deploy.

## Prior R26d goal

When P1 revises a PRD, persist only open issues from the current draft's critic and code checks; remove issues inherited from an earlier draft when the current review resolves them.

DONE: `agent-progress run --task r26d-open-issues --paths src/ontofill/phases/p1_scope/phase.py,tests/test_r26d_open_issues.py --check prd-open-issues-focused --strategy current-result-wins --hypothesis 'The narrow PRD patch inherits old issues; make the current loop result authoritative at persistence.' -- sh -c 'uv run pytest -q tests/test_r26d_open_issues.py tests/test_r26c_prd_patch.py tests/test_r22_p1.py tests/test_scope_thresholds.py && uv run ruff check src/ontofill/phases/p1_scope/phase.py tests/test_r26d_open_issues.py && uv run ruff format --check src/ontofill/phases/p1_scope/phase.py tests/test_r26d_open_issues.py'` passes; changes are committed and the SHA is reported to root.

Constraints: own only P1 PRD open-issue handling, synthetic tests, and these task notes; no P2, console, browser-agent, coordination, real case, approvals, push, or deploy.

## Prior R31 goal

Give phase 2 actionable per-rule and per-relation validation feedback, and after three semantically invalid rule/relation proposals save a schema-valid ontology plus unresolved recommendations.

DONE: `agent-progress run --task r31-p2-valid-relation --paths src/ontofill/phases/p2_ontology/phase.py,src/ontofill/phases/p2_ontology/README.md,schemas/ontology-recommendations.schema.json,schemas/README.md,tests/test_r31_ontology_salvage.py,tests/test_r6_signals.py,tests/test_r10_taxonomy.py,tests/test_definition_phases.py,tests/test_r22_validation.py,tests/test_schemas.py --check r31-p2-retained-valid-relation --strategy valid-relation-preservation-and-compact-feedback --hypothesis "After bounded retries, set-aside invalid proposals retain all valid typed relations and rules, compact feedback still names every offender, and the standard ontology checkpoint contains validated artifacts." -- sh -c 'uv run pytest -q tests/test_r31_ontology_salvage.py tests/test_r6_signals.py tests/test_r10_taxonomy.py tests/test_definition_phases.py tests/test_r22_validation.py tests/test_schemas.py && uv run ruff check src/ontofill/phases/p2_ontology/phase.py tests/test_r31_ontology_salvage.py && uv run ruff format --check src/ontofill/phases/p2_ontology/phase.py tests/test_r31_ontology_salvage.py && python3 -m json.tool schemas/ontology-recommendations.schema.json >/dev/null && git diff --check'` passes: 107 selected tests, Ruff lint/format, JSON schema parse, and diff check.

## Prior R29 goal

Attribute every gateway inference request to its engine run and trace step; separate sandbox task outcomes from proof integrity; show source labels/hosts and a stop reason in live run status. Existing artifacts remain valid.

DONE: synthetic HTTP and workflow tests join X-Run-Id/X-BA-Step-Id to trace; job tests show HTTP refusal with sound proof; P3/status tests show source identity and a nonempty stop reason. Full engine pytest and Ruff, runner as relevant, pre-push scan, public main push, and control VM fast-forward pass. A live run is then checked by the orchestrator for gateway attribution.

## Prior R26c goal

Preserve a reviewed PRD while correcting only the field named by a new digest-verified denial; keep the authority policy valid under all human revisions.

DONE: the recorded v4 plus fourth denial demonstrates a bounded structured domain patch that changes only the named publisher domains, new full-draft tool schemas require tier, and the full engine pytest and Ruff gate passes. Public main and the control VM fast-forward; the orchestrator alone starts the next live console run. This lane never writes the real case.

### P3 source/status subgoal
R29(c,d): P3 discovery trace steps and live status identify confirmed sources by their captured public label and hostname, and every paused, done, or failed status records a non-empty stop reason.
DONE: `agent-progress run --task r29-source-status --paths src/ontofill/phases/p3_fanout/authority.py,src/ontofill/phases/p3_fanout/discovery_loop.py,src/ontofill/runfeed.py,src/ontofill/workflow.py,schemas/run-status.schema.json,schemas/trace-step.schema.json,tests/test_discovery_loop.py,tests/test_runfeed.py,tests/test_r29_source_status.py --check r29-source-status-focused --strategy captured-artifact-source-identity-and-terminal-reason -- sh -c 'uv run pytest -q tests/test_r29_source_status.py tests/test_runfeed.py tests/test_discovery_loop.py && uv run ruff check src/ontofill/phases/p3_fanout/authority.py src/ontofill/phases/p3_fanout/discovery_loop.py src/ontofill/runfeed.py src/ontofill/workflow.py tests/test_r29_source_status.py tests/test_runfeed.py tests/test_discovery_loop.py && uv run ruff format --check src/ontofill/phases/p3_fanout/authority.py src/ontofill/phases/p3_fanout/discovery_loop.py src/ontofill/runfeed.py src/ontofill/workflow.py tests/test_r29_source_status.py tests/test_runfeed.py tests/test_discovery_loop.py'` passes with named source identity visible in recorded P3 trace/status and a non-empty failed status reason.
Constraints: own P3 fanout, runfeed, source/status/reason workflow wiring, schemas, focused tests and these task notes; no inference, jobs, console, browser-agent, coordination, live-case, push, or deploy edits.

## Prior R26b goal
R26b: a human secondary-source clause remains intact across common abbreviations, and a PRD with a matching secondary publisher kind plus a specific domain passes the authority check. Missing publishers receive actionable, subject-specific feedback before approval.
DONE: the exact archived ES and EN denial reasons pass a recorded PRD check with domain-bearing secondary publishers; a missing publisher names the unmatched subject; `uv run pytest -q`, Ruff lint/format, pre-push scan and VM fast-forward pass. The live case is resumed only by the orchestrator through the console.

## Prior R26 goal
R26: truncated PRD and ontology model decisions retry with a larger output budget; a paused P1 run records bounded validator objections in its CLI output, status reason, and S3 live trace. The case's actual trace location is verified without editing the case.
DONE: `agent-progress run --task r26-output-diagnostics --paths src/ontofill/inference/decision.py,src/ontofill/workflow.py,tests/test_inference.py,tests/test_r26_diagnostics.py --check full-engine-gate --strategy adaptive-document-budget -- sh -c 'uv run pytest -q && uv run ruff check src tests packages infra sandbox && uv run ruff format --check src tests packages infra sandbox'` passes, followed by a clean pre-push scan and control VM fast-forward.

## Prior R24 goal
R24: a PRD offered for approval must pass the code-owned authority policy, and an approved cached PRD must be reused byte-for-byte on rerun. If a previously approved PRD fails the current policy, pause without changing its approval or artifacts.
DONE: `agent-progress run --task r24-prd-policy --paths src/ontofill/phases/p1_scope/phase.py,tests/test_r24_prd_policy.py --check focused-prd-gate --strategy final-policy-checkpoint -- sh -c 'uv run pytest -q tests/test_r24_prd_policy.py tests/test_r22_p1.py tests/test_scope_thresholds.py && uv run ruff check src/ontofill/phases/p1_scope/phase.py tests/test_r24_prd_policy.py && uv run ruff format --check src/ontofill/phases/p1_scope/phase.py tests/test_r24_prd_policy.py'` passes, followed by the full engine gate, clean pre-push scan, and control VM fast-forward.

## Prior R21 goal
R21: remove case-specific legacy schemas from the generic engine while preserving generic artifact validation and rejecting future domain-named schemas. Add the judges' read-only console pointer.
DONE: `agent-progress run --task r21-generic-schemas --paths schemas,tests/test_no_case_vocabulary.py,tests/test_schemas.py,README.md --check full-engine-gate --strategy generic-schema-cleanup -- sh -c 'uv run pytest -q && uv run ruff check src tests packages infra sandbox && uv run ruff format --check src tests packages infra sandbox'` passes, and the public pre-push scan finds no tracked env, credential values, private markers, or app-owned paths.

## Prior R22 goal
Retry invalid model outputs with bounded, logged validation feedback while keeping P3, refiner, and repair failures scoped to their source or step.
DONE: `agent-progress run --task r22-other-semantic-retries-lint-successor --paths src/ontofill/phases/p3_fanout/phase.py,src/ontofill/phases/p3_fanout/leads.py,src/ontofill/phases/p3_fanout/discovery_loop.py,src/ontofill/phases/p3_fanout/site_graph.py,src/ontofill/refiner/core.py,src/ontofill/repair/pattern_a.py,tests/test_discovery_phase.py,tests/test_discovery_providers.py,tests/test_discovery_loop.py,tests/test_site_graph.py,tests/test_refiner.py,tests/test_pattern_a.py --check focused-pytest-ruff-lint-successor --strategy direct-trace-attribute-assignment -- sh -c 'uv run pytest -q tests/test_r22_validation.py tests/test_discovery_phase.py tests/test_discovery_providers.py tests/test_discovery_loop.py tests/test_site_graph.py tests/test_refiner.py tests/test_pattern_a.py && uv run ruff check src/ontofill/phases/p3_fanout/phase.py src/ontofill/phases/p3_fanout/leads.py src/ontofill/phases/p3_fanout/discovery_loop.py src/ontofill/phases/p3_fanout/site_graph.py src/ontofill/refiner/core.py src/ontofill/repair/pattern_a.py tests/test_discovery_phase.py tests/test_discovery_providers.py tests/test_discovery_loop.py tests/test_site_graph.py tests/test_refiner.py tests/test_pattern_a.py && uv run ruff format --check src/ontofill/phases/p3_fanout/phase.py src/ontofill/phases/p3_fanout/leads.py src/ontofill/phases/p3_fanout/discovery_loop.py src/ontofill/phases/p3_fanout/site_graph.py src/ontofill/refiner/core.py src/ontofill/repair/pattern_a.py tests/test_discovery_phase.py tests/test_discovery_providers.py tests/test_discovery_loop.py tests/test_site_graph.py tests/test_refiner.py tests/test_pattern_a.py'` passed: 63 tests; Ruff check and format clean.
Result covers source-scoped site-graph exhaustion, unclassified refiner coverage, step-scoped repair exhaustion, sandbox parse error propagation, and legacy injected-search fallbacks.
Constraints: own only P3 fanout, refiner core, repair Pattern A, and focused tests; no P2/P4/P5/workflow edits, live capture, push, or merge.
Branch/worktree: `r22-other` from `origin/main` b4af713 plus the root-owned helper dependency; commit and report without pushing.

## Project goal
Deliver a generic Ontofill engine for any open brief, with evidence-backed export, live Vultr decisions, and real zero-inbound Vultr/NetBird infrastructure.

R22 outcome: every typed model decision gets bounded feedback on schema or semantic validation errors, each attempt is traceable, and exhaustion pauses a prerequisite phase or fails only the affected source while the run continues.

DONE R22: recorded invalid-then-valid decisions produce two trace attempts and a usable artifact; three invalid P5 mappings fail one source while another proceeds; an exhausted P2 schema pauses without writing an invalid ontology; full pytest and Ruff gates pass.

R16 outcome: a live browser cell survives idle CDP gaps, reports a dead browser or target as stopped with a failed job proof, and completes a bounded five-minute gVisor session.

DONE R16: `uv run pytest -q tests/test_cell_relay.py tests/test_cells.py` plus Ruff check/format for touched substrate/tests; on the sandbox VM a five-minute allowed-page session with at least 30-second idle gaps succeeds, while a forced renderer death yields `state=stopped` and a failed jobs record.

Current milestone: close each engine row in the coordination gap tracker, have the orchestrator verify it, then run the real case. P4 negotiation and P1 research are cut by the user.

DONE R1+R5: a console-shaped approval marker binds raw artifact bytes for PRD, factors, ontology and actions; a stale digest pauses the live run with the exact reason and leaves case files unchanged. The user-authorized managed gate passed (246 tests, 3 skips, Ruff clean), public main reached `89c5fff`, and the control VM fast-forwarded plus `uv sync --frozen`.

DONE G1/G2: `uv run pytest -q tests/genericity tests/test_no_case_vocabulary.py` runs a second unrelated brief from brief-only input through P1→P5 with a non-case primary class, generic entities/ontology/metrics, and no case vocabulary under engine source. `uv run pytest -q` and Ruff checks pass. The v0.7 export change is announced in coord status before schema edits.

DONE local: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests` plus a `mock-` real-brief run (expected exit 3) followed by `ontofill export --run-id mock-...` (exit 0); no latest pointer moves.

DONE L1: forced-tool Vultr decisions use model IDs from live `/v1/models`, generator and critic use different families, and a scratch live phase 1–2 run regenerates recorded PRD/factors with Vultr provenance.

DONE L2: tagged two-VM Vultr deployment has zero inbound firewall rules, NetBird peers, remote `runsc` sandbox proof and Object Storage; verify account-side sandbox group policy and metadata/VPC egress protection.

DONE live case (app-owned approvals): `ontofill run ../proveedor-abierto/case` resumes through all three human checkpoints with Vultr inference, exports evidence-backed gold, and passes the app's DoD. Recorded output never counts toward this check.

DONE C4: `uv run pytest -q && uv run ruff check src tests packages infra && uv run ruff format --check src tests packages infra` plus `uv run pytest -q tests/test_discovery_providers.py tests/test_multisource_workflow.py tests/test_vultr_plan.py`; the multi-source check must show at least 3 distinct synthetic source types and a traced gap-triggered fan-out, while infra dry-run succeeds without credentials.

DONE R3: `uv run pytest -q tests/test_p5_controller.py tests/genericity && uv run ruff check src/ontofill/phases/p5_execute src/ontofill/browser_agent.py tests/test_p5_controller.py && uv run ruff format --check src/ontofill/phases/p5_execute src/ontofill/browser_agent.py tests/test_p5_controller.py`; a fake controller reaches P5 through S1, emits only evidence-validated target properties, and drains verify/action_gate/quarantine into `trace.jsonl`.

## R40 — P5 off-domain downloads
An off-domain document link on a trusted captured source page becomes a digest-bound source-review candidate; only an exact, verified approval permits sandboxed D0 GET and networkless parsing.
DONE (P5-owned slice): managed `agent-progress` check `r40-p5-download-review / p5-owned-final` passed 65 tests, Ruff lint/format, both approval-schema JSON checks, and `git diff --check`. The slice proves pending/deny/stale/approve behavior, stable candidate bytes across recapture, exact-host sandbox fetch, and same-domain allowlist behavior.
Integration remains open for the parent-owned workflow pause bridge and parse-pod `.xls` implementation before the full R40 gate is claimed.
Constraints: no automatic fetch; do not widen the current browser session allowlist; parent page and link evidence remain attached; never emit values without bronze-backed parse evidence.

## R11 bronze replay integration
Have `refine_case` rebuild current-ontology observations from successful trace-referenced bronze captures, persist parented replay steps before the one gold export, and restore the prior live trace if export fails.

DONE: `uv run pytest -q tests/test_bronze_replay.py tests/test_cli.py` proves a newly added property reaches gold through `refine_case`, replay lineage is persisted once without browsing, export runs once, and a failed export restores the original live trace.

Status: the authorized successor managed gate passed 13 tests. The fixture now supplies the SHACL path, and the tests exercise bronze-only gold export, repeat-call trace idempotence, and trace restoration when export fails. No live case or browser run was involved.

Constraints: replay only keys referenced by completed file-fetch trace steps; no network access or URL fetch; keep refiner/core.py, refiner/export.py, P5, console, browser-agent, case, and coordination files unchanged.

Constraints: public pushes only after slice checks and a pre-push scan; no committed data or secrets; source discovery without hard-coded source URLs; all captures in a sandbox; local bronze uses file:// and Vultr bronze uses S3; approvals use the contract protocol.

## R2 P5 real-path closure
Run every selected source objective in each P5 pass, count gap iterations independently, and derive generic list membership only from a complete capture keyed by the ontology identifier; normalize identifiers and flatten generic JSON/OCDS arrays with bronze evidence.

Status: implementation and most recorded checks are complete. The corrected workflow scheduling seam is unverified because progress-guard escalation barred another focused run; root will run the broader main integration gate after merge. See `NOTES.md` for the two failed hypotheses and exact remaining check. Do not claim the new workflow seam passed.

Constraints: only this worktree/branch; no controller, app, console, case, main, or coordination-file edits; keep R3/R7 work modular; preserve genericity; use the announced membership TDD contract; do not push or merge.

## R9b prompt screening closure
Wrap all captured page content sent to P3/P5 and page-derived repair stderr in safe `<page_content>` spans, with `ONTOFILL_GATEWAY_TOKEN` preferred and the legacy token accepted as fallback.

DONE R9b: `uv run pytest -q tests/test_r9b_prompt_screening.py tests/test_pattern_a.py tests/test_inference.py` and Ruff check/format for the edited engine and test files.

Constraints: synthetic-only prompts and tokens; no secrets, console/browser-agent edits, coordination status updates, pushes, merges, or deployment.


# Current task: R40 malformed XLS parse diagnostics

When malformed legacy BIFF reaches the isolated parser pod, preserve its stable failure code and add bounded, safe diagnostic text to the six-checkpoint task/job receipts and task trace.

DONE (passed): `agent-progress run --task r40-parse-error --paths sandbox/parse-pod/runner.py,src/ontofill/sandbox/parse.py,tests/test_sandbox_parse.py --check malformed-biff-diagnostic --strategy bounded-stage-diagnostic-without-payload-text --hypothesis 'A malformed synthetic BIFF workbook keeps invalid_xls as its stable task reason and emits bounded safe diagnostic text in its six-checkpoint job and task trace, without leaking raw bytes or secret-like text.' -- sh -c 'uv run pytest -q tests/test_sandbox_parse.py && uv run ruff check sandbox/parse-pod/runner.py src/ontofill/sandbox/parse.py tests/test_sandbox_parse.py && uv run ruff format --check sandbox/parse-pod/runner.py src/ontofill/sandbox/parse.py tests/test_sandbox_parse.py && git diff --check'` passed: 26 passed, 2 skipped; Ruff lint, format, and diff checks clean. Attempt 1 was the expected red baseline at the missing diagnostic field.

Constraints: own only the parse-pod runner, sandbox parse adapter, focused synthetic tests, and task notes. Do not touch P5/source review, R36 TLS, PA-owned files, real case/APPROVED, secrets, VM, or deployment. Commit locally; root integrates.

## Current task: R47 source-contained D0 fetch

Keep a failed D0 document fetch attached to its source and preserve sandbox receipts, so the next source can run. Containment failures remain hard stops.

DONE: a recorded failed fetch followed by a successful objective passes, the proxy route has a focused test, and the full pytest and Ruff gates pass before release.

## Current task: R46 batch linked-document review

Stage every eligible off-domain document link from one captured page in one bounded pending round, while keeping per-link digest decisions, exact-host fetch and denial skips.

DONE: the synthetic N-link batch, dedupe, approval/deny continuation and omission cap pass; the full engine gate passes after integration with R47.
