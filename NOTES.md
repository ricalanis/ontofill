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
# Pattern A repair runner lint note

The first Ruff pass flagged the candidate exception handler, and replacing `BaseException` with
`Exception` still triggered BLE001. The repair runner must record arbitrary candidate failures;
the narrow `# noqa: BLE001` on that handler documents this intentional boundary.

# Brief 10 live PRD probe, 2026-09-26

Two synthetic one-shot PRD calls on glm-5.3 hit `finish_reason=length` at the 16,384-token cap.
The first used low reasoning, the second minimal; changing reasoning alone was a dead strategy.
Three smaller typed section calls then completed a synthetic live PRD and independent critic pass.
The brief 10b scratch denial probe passed after the grounding and pause fixes: two numeric clauses
stayed human-grounded, the secondary cross-check reached the policy, and a critic objection was
persisted as an open issue. Eight model calls cost an estimated $0.01958. The probe script and
artifacts stayed under ignored `.cache/` and a temporary directory.
# Brief 10c progress guard, 2026-09-26

The first focused P1 check failed because the new authority invariant sent a second
recorded response request and existing live fixtures lacked a local primary publisher.
After fixing that flow, the second check exposed two legacy assertions that required
the old broad publisher demotion and empty-domain human placeholder. That strategy is
retired: only the named cross-check is demoted, unrelated primary publishers remain,
and unresolved human requests stay visible in revisions/open issues. The next check
uses the invariant-specific assertions through `agent-progress run`.

The later managed `live-critic-flip-and-cache-regressions` strategy failed once: the
critic-flip test saw two draft digests because a regenerated draft's volatile
`generated_by.at` timestamp was included in the hash. The code now hashes draft
content without provenance timestamps. That correction is unverified: the guard
blocked a repeat after two distinct failed strategies, so verification awaits
explicit direction for a genuinely new managed strategy.

Read-only review before that check found three more failure cases: a tier
objection can clear on `secondary→review` while the rationale still claims
primary; a short keyword overlap can demote an unrelated publisher; and the
cache can return an `open_issues` draft whose authority invariant still fails.
The next strategy needs focused regressions for all three before any live rerun.

The user-authorized successor managed check on commit `4b54b77` passed the full
test suite (232 passed, 3 skipped) but failed Ruff B023 on the nested `reviewed`
closure in `phase_loop.py` because it captures loop variables without binding
them. The chained formatting check did not run. This strategy is stopped after
one attempt as the user instructed; no source fix or retry followed.

The user then authorized a B023 closure-binding edit plus one further full
managed check. Commit `e806618` binds the draft, gathered context and iteration
as arguments of the nested review function. That one check passed: 232 tests,
3 opt-in skips, Ruff check clean, and Ruff format check clean.
