# Goal
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
