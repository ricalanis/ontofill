# Strategy and assumptions

## R16 selected strategy (2026-09-26)
- Both CDP forwarding layers currently return when `select.select(..., 30)` has no readable socket. An idle browser WebSocket therefore closes even while Chromium and the cell container remain alive. Fix both forwarders to continue waiting; add an idle-poll regression test.
- The substrate currently checks only Docker `State.Running`; it does not test the CDP page target. It also copies `peak_memory_mb` from preflight once and never updates it. Add a target liveness probe before reporting `ready` or accepting a new step, and gather live/peak memory before teardown.
- The PA-owned HTTP provider uses a 90-second substrate default when the caller supplies no limits, while its controller session default is 900 seconds. That exact mismatch could explain the 1.5-minute symptom in direct console sessions. Tell PA/orchestrator; do not change PA-owned code here.
- The latest real-case S3 run has no `jobs.jsonl`, and no local jobs feed was found on the control VM; the demo's `peak_memory_mb` is not yet available from the engine lake. A fresh live R16 scratch job will record it. No container was left running after the demo.
- Managed regression attempt 1 intentionally failed before implementation (idle relay closed; status returned ready). Attempt 2 passed relay and 17 tests but failed two liveness tests because `jobs.schema.json` only permits the four limit reasons. The dead hypothesis was that the existing closed enum already allowed browser/relay deaths. The next strategy is schema-aligned liveness: add the two announced reason values, then re-run the same focused tests and formatting. A third identical `baseline-failing-tests` attempt is forbidden by progress-guard.
- The first broader focused-suite invocation named a nonexistent `tests/test_cell_api.py`; cell API tests are already in `tests/test_cells.py`. It exited 4 before running tests. Corrected the DONE command and scope; no engine failure was observed from that invocation.

## R3 controller strategy

- Read the engine definition/plan, brief 14 and R3 gap check before editing. R3 adds no contract schema fields.
- Keep browser MCP orchestration in a new P5 module; change the existing P5 path only at its controller dispatch seam so the R2 branch can be rebased cleanly.
- Start the controller for a TDD whose first step starts at S1, or after P5 finds no allowed downloadable table. Keep the file download route for the deterministic case.
- Use the current RunFeed and BrowserTraceBridge directly. Accept only controller extracted values that exactly match a target ontology property and carry a safe screen, allowed HTTP(S) URL, selector, and mirrored bronze screenshot.
- Do not include page excerpts, live-view URLs, absolute controller paths, or case paths in public trace fields. Close every opened session in `finally`, then drain its final trace rows.
- The synthetic fake-controller test is required; the live synthetic-page run depends on the controller/gateway being configured and reachable without exposing credentials.

C1 establishes contract schemas and local infrastructure. The orchestrator changed local bronze to a file:// adapter; the S3 adapter remains for Vultr. C2 isolates browser capture in a disposable Docker service with an explicit per-task allowlist. C3 keeps each phase thin and persists phase outputs in the case package while evidence remains in bronze. A recorded inference double supports tests until Vultr credentials arrive. Runtime case writes are required by the contract and are owned by the case runner; engine source changes stay in this repo.

The local Docker daemon was unavailable at task start and was started with `orb start`. A manual public register lookup checked feasibility; its URL and content must never enter engine code, tests, prompts, or fixtures. `source.discover` will search from the brief at runtime. All agent inference uses Vultr Serverless Inference; no Jev backend.
# C4 strategy (2026-09-26)

- Reviewed `.results/delegation/c4-plan/plan.md` and bound the acceptance verdict in its receipt. C4a and C4b share discovery, objective lineage and run state, so root integrates them sequentially. C4c owns infra and remote-safe sandbox dispatch in its worktree.
- Search service URLs are provider infrastructure. A candidate source URL must come from a sandbox capture in that run; no recorded source choices or source fixtures know a real site.
- Authority policy accepts a government domain or a recognized publisher tied to the catalog provider; other candidates get a fingerprinted source checkpoint before TDD/execution. This app-visible enum addition was announced in coord/status before landing.
- A blocked/captcha provider stops at that endpoint and records failure evidence. A distinct configured provider may be tried next. Gap queries include the missing fields explicitly, and recorded plans derive from those gaps.
- Mock output remains scratch-only and exit 3. The C4 gate is a synthetic 3-source-type run with gap reopening; no mock metric counts as live DoD.
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

# Brief 10d progress guard, 2026-09-26

The first full managed check passed the P1-focused tests but failed one recorded
preview test: the mock has no extra decision response when authority remains
an open issue. The second full managed check passed 241 tests with 3 skips,
then Ruff found RUF012 in the new failure-preservation test fixture. Both full
strategies were stopped under the two-attempt rule. The user then authorized a
combined R1+R5 full managed check. With the fixture fix and digest-bound
approvals in place, the final delivery check passed: 246 tests, 3 skips, Ruff lint and format
clean. No live case files were touched.

### R15 implementation strategy

The capture branch keeps spider policy inside the agent pod and delegates one crawl to one `runsc` job. A P3 site-graph helper will classify captured page types against the active ontology, validate bronze and case artifacts against the additive v1.0.3 schema, and rank existing source objectives from recorded graph coverage. P4 will use the graph as planning context without changing TDD's existing schema. Workflow and `outer_gap.py` are R2-owned integration surfaces, so this branch exposes a bounded consumer API and sends root the exact post-R2 wiring proposal rather than editing them. No case data or source URLs are added.

The first managed R15 run found three targeted defects: the pure policy default exceeded its new response-byte ceiling, the graph producer returned a private trace field in the strict schema envelope, and the synthetic proof/job fixture did not match the job schema or preserve the crawl job ID. After those fixes, the second run passed 19 tests and failed only because the robots fixture expected an `allow` decision even though it disallows `/private`; `RobotFileParser` records `disallow` when that rule is evaluated. Root authorized one additional distinct-strategy run after correcting that fixture expectation. It passed all 20 focused tests. No live cell was started while R16 proof is active.

The authorized R15 attempt 6 gave the synthetic provider a real lead and passed 39 focused tests. At integration, 48 tests passed twice. The combined Ruff gate then failed once on an import-order error and once on three formatting differences. The manual formatting hypothesis is exhausted; the formatter has now normalized those files. No third combined check or public push occurs without a fresh authorization under the progress guard.

<!-- agent-session-state:begin -->
Last session end: 2026-09-27T00:52:14.923428+00:00
Changed paths:
 M NOTES.md
 M TODO.md
 M docs/planning/03-technical-architecture.md
 M src/ontofill/case/checkpoints.py
 M src/ontofill/inference/decision.py
 M src/ontofill/phases/p1_scope/phase.py
 M tests/test_inference.py
 M tests/test_scope_thresholds.py
<!-- agent-session-state:end -->

# R3 integration gate, 2026-09-26

The first full gate after merging the controller seam failed in three recorded
outer-loop fixtures. Their synthetic page now reaches the P5 file path, but one
fixture omitted its fake fetch, and reopened passes exhausted the single
recorded download-choice response. The page URL was therefore sent to the
contained fetch pod or lacked a recorded choice. The dead hypothesis was that
the existing loop fixtures already covered every P5 pass. The fixtures now
provide the fake fetch and one choice per expected pass. The next full gate
must verify this correction and the R3 code together; if it fails, stop under
the two-attempt rule.

## R2 P5 strategy (2026-09-27)
- Execute all P3-selected objectives within one P5 pass, draft/cache one TDD per objective, then refine/export once from aggregated observations.
- Count iterations by stable DoD criterion ID, carrying the associated ontology property IDs in gap reports, while retaining the current global pass ceiling. Route a gap report only to objectives whose target fields overlap it.
- The announced optional TDD membership shape is `{property_id, identifier_property_id, complete: true}`. The target must be a boolean ontology property in `target_fields`; the identifier must equal the selected class's `identifier_property`. `complete: true` is only a source assertion: runtime must prove a full, untruncated capture before deriving either result.
- Normalize identifiers deterministically and conservatively before matching; detect normalization collisions rather than turning ambiguous matches into false results. Keep raw captured values and bronze keys as evidence.
- Flatten arrays of JSON objects generically, including OCDS-shaped nested objects, without baking case vocabulary into `src/`. Prompt data derived from captures must pass through `screened_page_content`.
- Gate: a recorded synthetic fixture has at least four distinct source classes and proves all objectives execute in one pass, per-gap counts (one gap reopens twice while another first appears at iteration 1), identifier-equivalent membership, an evidence-backed false, and JSON-array/OCDS row extraction.
- Managed focused attempt 1 failed on the fixture's expected membership map: JSON and OCDS objectives intentionally shared the same ontology class as the index objective, so the complete list correctly emitted false for those absent records too. Correct the expected map to include every entity of that class; do not change the engine behavior.
- Managed Ruff check attempt 1 found an unused `extension` destructuring in the synthetic fetch callback; mark the unused tuple member explicitly and rerun the same source gate.
- Dead hypothesis 1: the workflow scheduling check's expected false-membership map omitted JSON/OCDS entities because its expectation treated each source objective as a separate class. They share the same ontology class; the engine correctly emitted false for those absent identifiers, and the expected map was corrected.
- Dead hypothesis 2: the newly added workflow scheduling seam fixture could omit `discovered_by` because its stub objectives bypassed discovery validation. The run-status schema requires that metadata, so the fixture now supplies valid synthetic provider and timestamp fields.
- Remaining check: the corrected workflow scheduling seam has not been rerun because progress-guard escalation barred another `r2-p5-focused` attempt. Root will run the materially broader main integration gate after merge; do not claim this new seam has passed.
