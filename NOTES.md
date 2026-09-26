# Strategy and assumptions

C1 establishes contract schemas and local infrastructure. The orchestrator changed local bronze to a file:// adapter; the S3 adapter remains for Vultr. C2 isolates browser capture in a disposable Docker service with an explicit per-task allowlist. C3 keeps each phase thin and persists phase outputs in the case package while evidence remains in bronze. A recorded inference double supports tests until Vultr credentials arrive. Runtime case writes are required by the contract and are owned by the case runner; engine source changes stay in this repo.

The local Docker daemon was unavailable at task start and was started with `orb start`. A manual public register lookup checked feasibility; its URL and content must never enter engine code, tests, prompts, or fixtures. `source.discover` will search from the brief at runtime. All agent inference uses Vultr Serverless Inference; no Jev backend.
# C4 strategy (2026-09-26)

- Reviewed `.results/delegation/c4-plan/plan.md` and bound the acceptance verdict in its receipt. C4a and C4b share discovery, objective lineage and run state, so root integrates them sequentially. C4c owns infra and remote-safe sandbox dispatch in its worktree.
- Search service URLs are provider infrastructure. A candidate source URL must come from a sandbox capture in that run; no recorded source choices or source fixtures know a real site.
- Authority policy accepts a government domain or a recognized publisher tied to the catalog provider; other candidates get a fingerprinted source checkpoint before TDD/execution. This app-visible enum addition was announced in coord/status before landing.
- A blocked/captcha provider stops at that endpoint and records failure evidence. A distinct configured provider may be tried next. Gap queries include the missing fields explicitly, and recorded plans derive from those gaps.
- Mock output remains scratch-only and exit 3. The C4 gate is a synthetic 3-source-type run with gap reopening; no mock metric counts as live DoD.

<!-- agent-session-state:begin -->
Last session end: 2026-09-26T21:34:07.361310+00:00
Changed paths:
 M GOAL.md
 M NOTES.md
 M TODO.md
 M packages/ontofill-scrape/src/ontofill_scrape/discovery.py
 M schemas/approval-pending.schema.json
 M schemas/approved.schema.json
 M schemas/common.schema.json
 M schemas/metrics.schema.json
 M schemas/objectives.schema.json
 M schemas/run-status.schema.json
 M src/ontofill/case/checkpoints.py
 M src/ontofill/cli/main.py
 D src/ontofill/phases/p3_fanout/phase.py
 M src/ontofill/phases/p3_fanout/search.py
 M src/ontofill/phases/p5_execute/phase.py
 M src/ontofill/refiner/export.py
 M src/ontofill/runfeed.py
 M src/ontofill/workflow.py
 M tests/test_cli.py
 M tests/test_execute_phase.py
<!-- agent-session-state:end -->
