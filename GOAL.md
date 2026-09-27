# Goal
Deliver a generic Ontofill engine for any open brief, with evidence-backed export, live Vultr decisions, and real zero-inbound Vultr/NetBird infrastructure.

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

## R11 bronze replay seam
Implement an offline helper that rebuilds typed observations for current ontology properties from file bytes referenced by a run's phase-5 trace, and returns lineage trace steps so refine can export newly added fields without browsing.

DONE: `uv run pytest -q tests/test_bronze_replay.py tests/test_cli.py` proves the replayed property and its evidence/value trace pass the existing gold export lineage gate and the CLI documents the trace-bounded behavior.

Status: the helper and gold-lineage tests passed, but the combined managed focused check failed twice on test assertions. The final whitespace-tolerant CLI assertion is unverified under the two-attempt guard; root owns integration and the broader acceptance gate.

Constraints: replay only keys referenced by completed file-fetch trace steps; no network access or URL fetch; keep workflow.py, refiner/core.py, refiner/export.py, P5, console, browser-agent, case, and coordination files unchanged.

Constraints: public pushes only after slice checks and a pre-push scan; no committed data or secrets; source discovery without hard-coded source URLs; all captures in a sandbox; local bronze uses file:// and Vultr bronze uses S3; approvals use the contract protocol.

## R2 P5 real-path closure
Run every selected source objective in each P5 pass, count gap iterations independently, and derive generic list membership only from a complete capture keyed by the ontology identifier; normalize identifiers and flatten generic JSON/OCDS arrays with bronze evidence.

Status: implementation and most recorded checks are complete. The corrected workflow scheduling seam is unverified because progress-guard escalation barred another focused run; root will run the broader main integration gate after merge. See `NOTES.md` for the two failed hypotheses and exact remaining check. Do not claim the new workflow seam passed.

Constraints: only this worktree/branch; no controller, app, console, case, main, or coordination-file edits; keep R3/R7 work modular; preserve genericity; use the announced membership TDD contract; do not push or merge.
