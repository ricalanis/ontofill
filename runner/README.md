# ontofill-runner

Self-sustained runs (GAPS R18). A small service on the control plane that watches every case registered in the
Ontofill Console. When a case's latest run is **paused at a checkpoint** and a decision for that checkpoint appears
(`APPROVED`, approve or deny, written by the console), it resumes **the same run**
(`ontofill run <case> --run-id <that run>`; the engine verifies the artifact digest and either continues to the next
checkpoint or regenerates on a deny). It starts a **new** run only when an approver asks for one in the console. The
engine owns the work, including its outer gap loop (`--to-phase 5`); the runner only decides when to call it again.

```bash
uv sync --extra s3          # boto3 only for S3 lakes
uv run ontofill-runner serve          # the service loop (systemd: deploy/ontofill-runner.service)
uv run ontofill-runner serve --once   # one poll
uv run ontofill-runner status         # JSON: kill switch + each case's state
uv run pytest -q                      # uses a fake engine; no network
```

## Guardrails

- **One engine per case:** a `flock` on `<state>/cases/<id>/lock` while the child runs; a lake status `running`
  (someone else's CLI run) is left alone.
- **Budgets:** per case (`ONTOFILL_RUNNER_BUDGETS`, default `ONTOFILL_RUNNER_DEFAULT_CASE_USD`) measured from the
  est_usd in the case's run traces; global (`ONTOFILL_RUNNER_GLOBAL_USD`) from the inference gateway's call log. At or
  over a cap: no launch (`budget_stop`), and a running engine that crosses it gets SIGTERM, then SIGKILL after the
  grace period. The engine also receives the remaining case budget as `--budget-usd`.
- **Kill switch:** `<state>/KILL` (the console toggles it). While present nothing launches and running engines are
  stopped; removing it resumes an interrupted run.
- **No retry loops:** a decision is acted on once (`last_trigger` = run + checkpoint + sha256 of `APPROVED`). A failed
  run stays failed until a new decision or a console "Resume" (which re-arms one relaunch).
- **Secrets:** the engine's env file (`ONTOFILL_RUNNER_ENGINE_ENV`) is loaded into the child only; the runner never
  logs env values. A failure's last 20 output lines go into the event, redacted (key/token/secret/password/bearer lines
  and long token-like strings).

## Environment

| Variable | Meaning |
|---|---|
| `ONTOFILL_CONSOLE_CASES` | the console's registry: `id=/case[:/local/lake],…` (lake from `<case parent>/lake.yaml` otherwise) |
| `ONTOFILL_RUNNER_STATE` | state dir shared with the console (default `/var/lib/ontofill-runner`) |
| `ONTOFILL_RUNNER_POLL_S` | poll interval, default 10 |
| `ONTOFILL_RUNNER_ENGINE_CMD` | default `uv run --no-sync ontofill run {case_dir} --to-phase {to_phase} --run-id {run_id} --budget-usd {budget}` |
| `ONTOFILL_RUNNER_ENGINE_DIR` | the engine checkout (the child's cwd) |
| `ONTOFILL_RUNNER_ENGINE_ENV` | env file for the engine child (gateway URL + the engine's gateway token, lake credentials) |
| `ONTOFILL_RUNNER_TO_PHASE` | default 5 |
| `ONTOFILL_RUNNER_BUDGETS`, `ONTOFILL_RUNNER_DEFAULT_CASE_USD`, `ONTOFILL_RUNNER_GLOBAL_USD` | `id=usd,…`; default 2.0; global cap |
| `ONTOFILL_RUNNER_GATEWAY_LOG` | the inference gateway's JSONL call log (global spend) |
| `ONTOFILL_RUNNER_KILL_GRACE_S` | SIGTERM → SIGKILL grace, default 30 |

## State directory (shared with the console)

| Path | Writer | Content |
|---|---|---|
| `KILL` | console | present = kill switch on |
| `console-decisions.jsonl` | console | kill-switch toggles (who, when) |
| `cases/<id>/control.json` | console | `{"paused": bool, "start_requested": {"by","at","to_phase"} \| null, "retry_at"?}` |
| `cases/<id>/status.json` | runner | `{state: idle\|running\|waiting_approval\|paused\|killed\|budget_stop\|failed\|done, run_id, checkpoint, running_since, last_started_at, last_resumed_at, reason, spent_usd_case, spent_usd_global, pid, last_trigger, handled_start_at, handled_retry_at, updated_at}` |
| `events.jsonl` | runner | `{ts, case_id, kind: started\|resumed\|paused_at_checkpoint\|done\|failed\|budget_stop\|killed\|start_requested\|paused\|unpaused, detail, run_id}` (the console inbox shows failed / budget_stop / killed) |
| `cases/<id>/lock`, `cases/<id>/engine-<run>.log` | runner | the per-case lock; the engine child's output (0600) |

The console (uid 10001 in its container) needs write access to `KILL`, `console-decisions.jsonl` and
`cases/*/control.json`: give it the state dir with an ACL (`setfacl -R -m u:10001:rwX -m d:u:10001:rwX`).
