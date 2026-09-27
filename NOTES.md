# R31 P2 schema salvage plan (2026-09-27)

The first root integration gate on current public main passed 463 tests but failed two pre-existing R22 assertions. One required the old exact generic rule error, while R31 deliberately names the offending rule and allowed forms; the other required three cross-class rules to pause without artifacts, which is the R31 defect being fixed. The recorded R22 test now checks the precise feedback and uses a separate invalid core class property to preserve the fail-closed invariant. This is a changed-spec test correction, not a production change. Re-run the full gate once under the standing distinct-cause authorization.

The corrected R22 focused check passed 10 tests and Ruff. The authorized full integration retry passed **465 tests, 5 skipped**, Ruff lint/format clean on 168 files, recommendation JSON syntax valid, and committed plus working diffs whitespace-clean. The R31 production source did not change after the worker's 107-test gate.

Scope is the P2 ontology draft/validator, its new unresolved-recommendations schema documentation, and generic recorded tests. The code must not edit case data, approvals, console, browser-agent, coordination files, or deployment targets.

Selected strategy: keep the existing three-attempt model retry. Improve semantic validation to aggregate named relation/rule diagnostics and give concrete accepted grammar forms. Retain only the last structurally valid candidate. After bounded exhaustion, remove only rule/relation proposals that have semantic diagnostics; revalidate the complete remaining ontology and compile/validate DoD queries before writing any artifact. Missing classes, properties, malformed objects, and other schema errors continue to fail without an ontology checkpoint.

Set-aside artifact contract requested by the orchestrator: `02-ontology/recommendations/unresolved.json`, shaped as `{schema_version: "1", ontology_path: "02-ontology/ontology.json", generated_by, unresolved: [{kind: "rule"|"relation", id, reason, proposal}]}`. `proposal` preserves the original structurally valid full schema object, and code verifies `id == proposal.id`. The JSON schema is `ontology-recommendations.schema.json`. No relation-path grammar is being introduced; executable predicates remain limited to same-class `same_value`, `equals`, and `date_before` terms, while relation matching is one typed property per declared endpoint class.

Regression data uses synthetic IDs only. Test attempt sequence captures cross-class rules ×3, with valid classes/properties and a separate valid rule retained. A companion case exercises a bad relation match and endpoint-specific guidance. No live case or VM artifact is read or edited.

Managed focused attempt 1 passed the cross-class salvage, stale-recommendation clearing, and ontology checkpoint cases, then failed one relation-feedback assertion: the validator named both endpoint properties/classes but did not include the test's requested phrase `endpoint classes`. Tighten the relation guidance heading to state the endpoint-class constraint explicitly; this is a wording coverage gap, not a salvage failure.

The initial standalone schema syntax command used `python -m json.tool`, but this host has no `python` executable (exit 127). Use `python3 -m json.tool` in the managed final gate; no schema validation ran in that failed command.

Managed final-focused attempt 1 passed all 107 selected tests, then Ruff stopped the gate on three deterministic style issues: the new `copy` import was out of order and two nested datatype checks triggered SIM102. Apply Ruff's fixes and rerun the same gate once.

The automatic Ruff-fix follow-up sorted the import but left both SIM102 checks unchanged (exit 1). Dead hypothesis: Ruff auto-fix would flatten these nested conditions. Manually combine each presence and datatype predicate, then use the successor managed gate with its distinct schema-recovery strategy; do not repeat the same auto-fix path.

The explicit-condition successor passed the focused behavior, Ruff lint/format, schema JSON parse, and diff checks (107 tests). A parent review then requested positive preservation coverage for valid typed relations, so the three-attempt rule fixture now carries a valid generic `record`→`event` relation and asserts it survives alongside the valid rule. The new retained-relation successor gate passed the same 107 tests and all checks. The workflow fixture reaches the regular ontology `APPROVAL_PENDING.md` with the ontology artifact present; no case-specific data or relation-count claim is involved.

## Prior strategy and assumptions

## R29 integration (2026-09-27)
- Independent inference and sandbox outcome branches were reviewed and cherry-picked on current main. Workflow now assigns a run ID before the gateway model catalog call and binds each decision call's gateway step ID to the same trace step.
- Review found standalone `refine_case` still created its live gateway client without the existing run ID. A recorded test now intercepts its catalog factory after a digest-bound ontology approval and proves the existing run ID is supplied.
- The new refine attribution focused check passed four tests, then failed Ruff import ordering in its test module. The second check passed four tests and Ruff lint, then failed Ruff format on deterministic test-only line wraps. Both causes differ and neither was a behavior failure. Ruff formatted the test; use the full integration gate as the successor rather than rerunning the exhausted focused check.
- The first full gate after refine attribution passed 458 tests and failed one pre-existing R10 fixture: its `from_env` classmethod fake took no `run_id`, so it rejected the newly required keyword before any inference. The fixture now accepts and asserts the existing run ID; production behavior is unchanged by this test correction.
- Initial direct focused check passed 60 tests with 2 opt-in skips, then Ruff flagged a mutable class test fixture (`RUF012`) in the new workflow attribution test. The fixture is moved into `__init__`; the next managed focused gate checks it. This is a style-only failure, not a production behavior failure.
- The P3/status worker passed its module-focused gate (34 tests and Ruff). Its workflow integration fixture then failed twice for distinct harness assumptions: expected exit 0 in a recorded preview that correctly pauses at PRD, then seeded a fake source trace before workflow's `trace_before` snapshot. It corrected the fixture to append after the snapshot and stopped under progress guard. I own the independent integrated check of that final seam.

## R26c narrow domain denial (2026-09-27)
- Red baseline: all three new recorded regressions failed as intended: the fourth human denial created false SECONDARY subjects, the engine redrafted the whole PRD, and the model schema left publisher tier optional.
- First focused post-change check: 14 passed, 1 failed. The remaining failure was a test fixture mismatch: the synthetic v4 declared two prior revisions inside `prd.json` but did not create their archived denial markers, so `checkpoint_revisions` correctly assigned the active denial `n=1` rather than the real case's `n=3`. The fixture now creates synthetic archived markers; no production change is needed for that failure.
- The successor focused test passed all 15 cases; Ruff then rejected five new `re.I` aliases and import order in the new test. This is a distinct style-only cause. Apply Ruff's deterministic fixes and formatter before the next managed check.
- The first full integration gate passed 444 tests, failed one, and skipped five. A pre-existing live fake transport produced a valid secondary publisher without `jurisdiction`; R26c required only `tier`, but my initial model-tool schema also required jurisdiction. That extra requirement caused three bounded schema retries and the test failure. Removed only the extra jurisdiction requirement; the deterministic authority check still requires an in-scope primary publisher. This is distinct from the fixture and style issues above.
- The final reviewed gate passed **445 tests, 5 skipped**, Ruff lint and format clean on 165 files. It includes invalid-patch rollback: a refused replacement leaves the reviewed PRD, sidecar, Markdown and active denial byte-for-byte unchanged. A schema-invalid legacy denied draft falls back to the existing full bounded redraft rather than raising from patch-target selection.

## R29(c,d) P3 source status strategy (2026-09-27)
- Read `ontofill/AGENTS.md`, the engine definition/build plan, and the R29 row plus current run-feed contract. Root approved optional `source_label`/`source_host` on trace rows and `status.sources[]`; older artifacts remain valid. Terminal `reason` stays optional in the schema for compatibility, while new paused/done/failed snapshots must write a non-empty reason.
- Derive a public label from the captured candidate title, falling back to its parsed host; derive the host from the captured landing URL and lowercase it. Never use case-specific constants. Attach identity only to trace steps bound to that source ID.
- Keep explicit pause/failure reasons as-is. If absent, use a deterministic checkpoint/state fallback. Running snapshots clear stale stop reasons. Status source labels are copied from the candidate manifest and should be written immediately when the source list changes.
- Acceptance is recorded synthetic discovery whose named source appears in P3 trace and `status.json`, plus a failed `RunFeed` status with non-empty reason. Use one managed focused run with tests then Ruff, and record any check failure cause before retrying.
- Managed focused attempt 1: 34 tests passed and Ruff lint passed; format check requested one deterministic wrap in `runfeed.py` and one in `test_r29_source_status.py`. This is formatter-only, not a behavioral failure. Format those touched lines, then use the authorized successor strategy for the same focused gate.
- Managed workflow-integration attempt 1: 34 tests passed; the new recorded `run_case(to_phase=3)` fixture exercised P3 and persisted the source data, then returned 3 because the PRD checkpoint remained pending in preview mode. The failed assertion expected exit 0, so the fixture's result expectation was wrong. Keep the checkpoint behavior and assert the paused state/reason with the source fields.
- A successor invocation without `--hypothesis` was blocked before checks; `agent-progress run --help` confirmed the required option. This was a command-shape block, not a test result.
- Managed workflow-integration attempt 2: 34 tests passed and the new workflow test failed with `StopIteration` when it searched live trace for `source-synthetic`. The fake `SearchTrace` was constructed with a pre-seeded row before `run_case` measured `trace_before`, so workflow correctly treated that row as historical and published no fresh P3 row. The fixture now starts empty and appends the source row inside the monkeypatched discovery call, after the snapshot.
- Progress-guard escalation after two distinct failed workflow strategies: root authorized this fixture-only correction and instructed no third check under this guarded task; root will run an independent integration gate after merge. The final corrected workflow seam is unverified locally.

## R28 runner integration and formatter baseline (2026-09-27)
- The first integrated runner check passed 22 tests and Ruff lint, then failed `ruff format --check` across the runner tree. Six runner files already have formatting drift on origin/main; `git show origin/main:runner/ontofill_runner/runner.py | ruff format --check --stdin-filename ... -` also exits 1.
- A second, narrower runner check passed the same 22 tests and lint, then failed `ruff format --check runner/ontofill_runner/state.py`. Its docstring indentation and pre-existing `event` line wrap also fail on origin/main. The hypothesis that only unrelated runner files caused the failure was wrong.
- New strategy: verify the exact R28 merge with the full engine suite and formatter, full runner tests and lint, and `git diff --check`; do not reformat unrelated runner baseline solely to satisfy a historical style gate. The rebase conflict retained the pre-existing stop reason and checkpoint reporting while adding the needs-human exit and engine-stop record.

## R24 authority checkpoint (2026-09-27)
- Baseline managed check: 1 passed, 2 failed. The real regression reproduced: an older, approved PRD with a policy-invalid tier was redrafted and its approval staled. The final-boundary test failed earlier than intended because its injected loop artifact omitted required `generated_by`; that is a fixture setup error, not a policy result. The fixture now supplies valid provenance so the final-boundary invariant is exercised.
- Strategy: keep R22's three-attempt model validator as the authority-policy repair path, add a final policy gate before staging an artifact, and preserve any existing approval if the approved cached artifact fails the current policy. Do not act on an invalid approval or rewrite reviewed bytes.
- Focused check attempt 1 after the code change: all 28 P1 tests and Ruff lint passed. Ruff format alone requested two deterministic line wraps in the new guard; no behavior failure. Apply exactly those wraps, then retry the same focused gate under the standing authorization.
- First full integration gate: 417 passed, 1 failed, 5 skipped. The failure was the recorded mock preview's intentionally untrusted `APPROVED` marker: the new legacy-approval preservation branch treated it like a live approval and paused with a draft-unavailable reason. Recorded output never satisfies a checkpoint. Restrict the preservation guard to strict live Vultr approvals; keep mock-preview behavior unchanged, then retry the full gate.
- Full integration retry with that live-only guard passed: 418 tests, 5 skipped; Ruff lint and format clean on 160 files.

## R22 typed decision strategy (2026-09-27)
- First focused managed check: 13 passed, 2 failed. Both existing inference tests expected `TypeError` on exhausted malformed model output, while the new typed `ModelValidationExhausted` initially inherited `ValueError`. The output still failed closed; this was an exception-compatibility regression, not a retry or safety failure.
- Revised strategy: make the typed exhaustion a `TypeError` subtype, preserving existing callers while exposing purpose, exact bounded reason, and attempt count for phase-specific pause/source isolation. Keep schema/semantic retries bounded and leave network/approval failures outside the retry class.
- P4 regression: 18 passed, 3 failed because existing tests expected the immediate raw `ValueError` from one malformed model answer. The changed contract retries three times and raises typed exhaustion, so the tests now assert that type, three attempts, and no artifact write; a new test proves invalid-then-valid correction.
- Multi-source/outer regression: 32 passed, 3 failed because adding `call_log` to the recorded client exposed it to the outer budget accounting, and its recorded calls had no `usage`. The outer decision correctly refused an unpriced call, but that broke recorded loop previews. Recorded test calls now carry explicit zero-token, zero-cost usage, so preview budget accounting remains deterministic and every attempt can appear in trace.
- P5 workflow status test: 6 passed, 1 failed because its fake `execute_objectives` returned results in input order, while the real executor and workflow both run ordinary objectives before complete-list membership objectives. The failed-result fixture was assigned to the wrong source by `zip`, so the health assertion was wrong. The fake now returns the same membership-last order as production; the three source-health variants pass.
- First R22 core+P2+P5 full gate: 403 passed, 1 failed, 5 skipped. The one failure was a pre-existing CLI-output assertion for zero-budget PRD pauses: the new combined exception handler changed the printed reason from `budget exhausted` to `draft unavailable`, while status and artifact safety still passed. The handler now preserves the exact budget phrase for `PrdDraftUnavailable` and uses `model validation exhausted` only for the new typed case.
- Final R22 integration gate attempt 1: 415 passed, 4 failed, 5 skipped. All four failures were older R10 classifier tests that queued one invalid answer and expected an immediate `ValueError`; the new contract retries up to three times and emits `ModelValidationExhausted`. They now queue three invalid answers and assert the exact objection reaches answer two. The standalone refiner tests already covered invalid-then-valid and fail-closed unclassified behavior; no production classifier logic changed for this correction.

## R17 integrated gate strategy (2026-09-27)
- Attempt 1, `r17-integration-main`: 77 passed, 7 failed, 3 skipped. Four replay fixtures omitted the synthetic parser; three assertions assumed raw HTML or capture-only trace. Static fixture corrections preceded the authorized retry.
- Attempt 2, `r17-integration-authorized-fixture-fix-retry`: 82 passed, 2 failed, 3 skipped. Dead hypothesis: a repeated real parser run could preserve byte-identical live trace, and export failure should erase proof that the parser pod ran. The parser generates a new six-checkpoint job each time; `refine_case` retains that proof and rolls back only unexported replay value steps.
- New strategy: assert one stable replay value lineage and one new six-checkpoint proof per actual parser run; on failed export, assert parser proof persists but unexported replay steps do not. Broaden the architecture guard for P5 bronze reads, socket connections and direct network subprocesses. The two test files were edited and formatted statically; no third integrated check is authorized yet. Use a transparently named successor with this hypothesis only after the orchestrator decides under `coord/briefs/codex-authorizations.md`.

## R9b prompt screening strategy (2026-09-26)
- Preserve the shared `screened_page_content` encoder: it creates one outer page span and escapes page-supplied opening/closing delimiters.
- Audit every P3/P5 engine inference prompt that receives captured page data, including legacy P3 source selection, P3 site-graph classification, P5 table/link mapping, and Pattern A repair stderr.
- Use only synthetic page strings, static recorded/fake decisions, and fake HTTP transports; no live gateway calls or credential values.
- `ONTOFILL_GATEWAY_TOKEN` must win when both names are set; `VULTR_INFERENCE_API_KEY` remains a compatibility fallback.
- DONE: `uv run pytest -q tests/test_r9b_prompt_screening.py tests/test_pattern_a.py tests/test_inference.py` plus Ruff checks on edited Python files.
- Managed check attempt 1: 44 focused tests passed; Ruff reported import order in the new test module. Corrected the import grouping for the one allowed follow-up.
- Managed check attempt 2: 44 focused tests and Ruff lint passed; Ruff format identified two deterministic wraps in `tests/test_execute_phase.py`. The formatting fix is isolated for its own narrow managed check.
- The first narrow format check found one remaining wrapped map-sample assertion; corrected before the final attempt for that path/check.
- The second narrow format attempt showed Ruff requires the long map-sample condition wrapped in a parenthesized assertion. Applied that exact output and stopped this path/check after its two allowed attempts; no semantic code changed after the passing test/lint run.

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
# R11 bronze replay strategy, 2026-09-27

Read-only review found `workflow.refine_case` currently reads existing silver observations and exports them with the persisted run trace. It does not reconstruct typed values from bronze. Root owns adding replay observations and replay trace steps to that orchestration after this isolated helper lands.

Selected seam: `replay_bronze_observations(case_dir, lake, run_id, trace, ontology, provenance)` returns an immutable result containing observations and deterministic replay trace steps. A replay candidate must be a completed phase-5 `fetch: bytes` trace step with a validated `sha256:` key. The helper reads local/object storage only, verifies the payload hash, reuses the trace-linked column macro for entity class and existing mappings, and adds only unambiguous current-property matches by normalized ontology ID/label versus literal header. It never resolves or fetches a URL.

Trace/evidence assumptions: source/objective/TDD metadata comes from the capture trace and approved objective/TDD case artifacts; screenshot evidence comes from the nearest prior capture step for that same objective; each emitted observation points to the deterministic replay trace step, whose `parent_step_id` is the original file-capture step. Export still requires the current objective and TDD artifacts to authorize every property being emitted.

Unsupported formats, truncated parses, missing/mismatched bronze objects, ambiguous header matches, absent identity columns, or absent screenshot lineage produce no replay observations for that capture. The focused export fixture will update its current ontology/objective/TDD artifacts to include the approved new property while its historical macro remains unchanged.

The first managed focused attempt passed replay derivation and `export_run` lineage validation, then failed two test assertions: the fixture read the single-record `entities.jsonl` object as a list, and the CLI test looked for subcommand help text in `ontofill refine --help` although argparse displays that description in `ontofill --help`. Correct both assertions before the single remaining attempt; the engine hypothesis remains alive.

Managed gate details: `agent-progress run --task r11-bronze-replay-focused --paths src/ontofill/refiner/bronze_replay.py,src/ontofill/cli/main.py,tests/test_bronze_replay.py,tests/test_cli.py --check r11-new-property-export --strategy trace-linked-offline-replay --hypothesis "Completed file-fetch steps linked to existing macros can be reparsed locally with exact unambiguous current property matching, preserving new trace evidence through the existing export lineage gate." -- uv run pytest -q tests/test_bronze_replay.py tests/test_cli.py`.

Attempt 1 exited 1 with 9 passed and 2 test assertion failures: the synthetic JSONL reader treated one entity as a list, and the CLI test queried subcommand help rather than the root command listing. Attempt 2 exited 1 with 10 passed and only the CLI help assertion failing because argparse can wrap the description. After the whitespace-folding assertion fix, the parent authorized one distinct successor strategy; it passed all 11 tests. The helper/CLI managed gate is complete. The refine-case wiring now has a separate managed gate.

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

# R11 refine_case integration, 2026-09-27

Read-only inspection confirmed `refine_case` currently obtains the latest ontology, reads existing silver (or the silver cache), and calls `export_run` once with `trace.live.jsonl`. The no-silver refusal must move after offline replay so bronze-only runs can still refine. No browser, fetch, or inference call belongs on this path.

Selected integration: load the live trace bytes once, pass its parsed rows and the current ontology to `replay_bronze_observations`, and combine returned observations with silver before refinement. Merge replay steps by deterministic `step_id`; compare an existing row to the regenerated one ignoring only its volatile timestamp, and reject a same-ID content mismatch. If new steps are needed, replace the one live trace object atomically before export so the export lineage already exists. If export raises, restore the exact previous trace bytes and re-raise. This keeps gold export to one call and makes repeated refine idempotent for trace rows.

Focused integration fixtures are intended to exercise the new property through `refine_case`, observe persisted parentage at export, run refine twice to check no duplicate replay row, and inject export failure to prove exact trace restoration. Managed check: `r11-refine-case-replay` / `r11-refine-case-replay-export-lineage`; exact command and results are in `TODO.md`.

Attempt 1 exited 1 with 11 passed and 2 failed. The existing `refine_case` refused the bronze-only fixture before loading the trace, and the rollback test was missing `pytest`. Attempt 2 exited 1 with 11 passed and 2 failed: both integration tests reached `refine_observations` but failed on missing fixture artifact `case/02-ontology/book-shape.ttl`, before the export spy ran. The workflow behavior and rollback therefore remain unverified. Static Ruff check, Ruff format check, and `git diff --check` passed before the second run. This task's two attempts are exhausted; per the parent instruction and progress guard, stop here without renaming or retrying the managed check.

## R11 authorized successor check, 2026-09-27

The parent authorized one managed successor after the distinct SHACL-fixture failure and directed use of the transparent task name `r11-bronze-refine-authorized-successor`. The only correction for this attempt is to create the empty file named by the test ontology's `shacl_path`, allowing `refine_case` to reach export and rollback assertions. The exact gate is `uv run pytest -q tests/test_bronze_replay.py tests/test_cli.py`; run it once under the new task and stop if it fails.

The authorized successor gate passed: 13 tests in 0.44 seconds. This verifies the fixture reaches the export spy, new-property gold export succeeds, repeated refine does not duplicate the replay trace row, and the original trace bytes are restored after an injected export failure. No live or network run was performed.

## R22 P2 validation retries

The focused recorded tests passed on the first managed attempt. The initial Ruff check then failed because `Callable` was imported from `typing` instead of `collections.abc` and the test file had an unused import; the format check identified line wrapping and blank-line normalization in the P2 phase and new test. These were style issues and are fixed. The compile smoke command also used `python`, which is unavailable in this environment; the project uses `uv run` for Python commands. The managed Ruff check and format check passed after these corrections.

The first combined R22 P2 regression check passed 22 tests but failed the older duplicate-taxonomy-label fixture: it queued one invalid critic response while the new contract correctly requests up to three. The fixture now queues three invalid responses and asserts typed step exhaustion. The corrected managed regression set passed all 23 P2 focused and existing tests.

The final managed integration check passed 30 tests, including the shared inference retry tests and P2 schema, semantic, exhaustion, and checkpoint-preservation cases. Ruff lint and format checks passed. Bounded validator feedback is screened before it is added to a repair prompt.

## R22 P1 schema and semantic retry

P1 now validates the normalized PRD after each model draft and retries schema or local authority-policy failures up to three times with screened validator feedback. Exhaustion raises `PrdDraftUnavailable` before checkpoint writes, approval archival, or critic calls. Vultr's three section requests are adapted behind the same complete-PRD validation; the shared inference client retains per-request call-log records.

The first managed focused run passed 22 tests and failed one legacy expectation: `PhaseLoop` records its initial `gather` trace before the invalid draft raises. The test now asserts that this is the only trace role emitted before pause. The managed focused run then passed all 23 tests. The first Ruff check found RUF012 on the synthetic exhaustion fixture's mutable class-level call log; the fixture now initializes that list per instance. The managed Ruff and format gate passed, with all three P1-owned code/test files clean.

## R22 semantic validation retry strategy (2026-09-27)
- The task branch starts at `origin/main` b4af713. The root-owned helper commit 96d6135 was cherry-picked as a dependency, yielding branch commit a2bf9f5; no helper source is owned here.
- Replace direct typed model calls in the owned P3, refiner, and repair paths with `complete_validated`. Put existing response-specific checks inside callbacks that raise `ValueError`, so the helper can feed back the exact validation reason and log each failed attempt.
- Retry only model schema/semantic failures. Preserve P3's source-specific site-graph failure step, make exhausted taxonomy classification return no assignments (unknown coverage), and turn generated/patch-code exhaustion into a repair failure result.
- Do not route capture, sandbox, safety, approval, parsing, filesystem, or other non-model failures through a retry validator. Keep model-produced repair code in the existing sandbox test loop.
- Gate is one managed focused pytest/Ruff run, with no live providers or captures.

### R22 managed gate continuation

The first managed `r22-other-semantic-retries` run exited after 61 tests passed
and one Pattern A assertion failed: parse trace rows have no `repair` field, so
the new assertion must filter for `event == "repair"`. The second run passed all
62 tests and Ruff format, then stopped at Ruff I001 because `classify_entities`
was imported ahead of the `ontofill.refiner` package imports. That import order
is corrected.

The user’s Sat 22:14 standing authorization in
`coord/briefs/codex-authorizations.md`
allows continued attempts until success while recording causes. The transparent
successor gate also covers a legacy P3 injected-search fix: exhausted typed
query planning records a failed phase-3 step and uses the deterministic query;
exhausted source selection records its failed step and falls back to the bounded
ranked candidates. The attempt remains isolated to synthetic responses.

The first transparent successor passed all 63 tests and Ruff format, then Ruff
reported B010 for assigning a constant-name `setattr` to the injected search
client. The helper now uses direct attribute assignment, still catching only
objects that cannot accept a trace attribute.

## R22 P1 recorded-preview follow-up

The root integration gate passed 406 tests and failed two workflow preview tests because `_preview_decision` intentionally supplies no trusted publisher. Strict PRD semantic retry consumed its single recorded response and paused before it could persist a preview with authority open issues. `draft_prd` now exposes `mock_preview=False`; only a recorded decision may enable it, and it skips only the authority rejection during retry validation. The final phase check still records authority objections as `open_issues`, and the preview fingerprint has a separate `mock-preview` marker so a valid preview draft cannot be reused by strict mode. The managed focused preview and strict-regression tests passed: 25 tests.

## R26 managed check causes

## R26b focused check cause

The first existing P1 gate passed 37 tests and failed one assertion that still searched for the old generic secondary-source objection text. The checker now names the unmatched human subject and the required SECONDARY publisher/domain. The test has been updated to assert that actionable reason while preserving its invariant: a shared generic word cannot demote unrelated publishers.

The first exact-revision integration gate passed 34 cases and failed three: the Spanish subject and English `registry` kind still failed under exact token matching across languages, and feedback said `specific publisher domain` rather than the test's actionable `specific domain`. The revised strategy compares bounded close cognates against publisher **kind** tokens only, requires a distinctive owner when the clause has no explicit matching jurisdiction alias, and uses the requested feedback wording. The next focused gate passed all 37 cases.

The full R26b engine suite passed 440 tests with 5 skipped; Ruff lint passed. Ruff format found only one line wrap in the new foreign-scope guard, which was formatted without logic changes. A recorded guard now proves the Spanish secondary clause cannot demote an in-scope primary registry despite the cognate match.

The baseline recorded check failed in the intended five places: PRD/P2 output budgets and retries, plus missing multi-error pause diagnostics. After the implementation, the first focused check exposed an existing extraction contract: a truncated extraction call must escalate from Qwen to GLM. A one-line follow-up accidentally altered the HTTP schema-mode branch instead of the length branch, so the second focused check failed for the same cause. The next strategy targets the length branch explicitly and retains extraction fallback while retrying PRD/P2 on their selected model.

The first full integration run passed 418 tests and failed three existing R22 assertions because my attempted status normalization changed recorded schema failures from `invalid_response` to `validation_failed`. Those are distinct trace statuses: the latter is reserved for semantic validation after a syntactically valid answer. The normalization was reverted, and the new R26 test now asserts the existing `invalid_response` status plus its visible validator reason.
