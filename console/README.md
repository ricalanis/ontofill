# Ontofill Console

The engine's operator and approver UI (CONTRACT §14): generic and multi-case. For every registered case package it
shows the runs and live feed (steps, execution modes, loop threads, a cell's live-view link, the six-checkpoint
sandbox proof, the DoD panel), the approval checkpoints, spend and track evidence. No domain words live here: labels
come from each case's ontology. Apache-2.0, like the rest of Ontofill; ported from the control pages of the
Proveedor Abierto app (same author, same license), which keeps only its consumer-facing screens.

## Approvals (CONTRACT v0.9.7)

- **Identity from the sign-in, not a form field.** `ONTOFILL_CONSOLE_IDENTITY=sso` (default): the approver is the
  value of the first non-empty header in `ONTOFILL_CONSOLE_IDENTITY_HEADER` (default `X-NetBird-User`; a
  comma-separated list is accepted). No header → 403, nothing written. `local` (typed name) is for development only
  and must be set explicitly.
- **Bound to the exact artifact.** The review page embeds the sha256 of every artifact the checkpoint lists; the POST
  recomputes them and answers 409 if anything changed or is missing. `APPROVED` records them:
  `{approver, date, checkpoint[, decisions][, decision, reason], identity_source, artifact_sha256: {path: hex}[, run_id]}`
  (paths relative to the case, digests over the raw file bytes). The engine must refuse an `APPROVED` whose digests do
  not match the files it is about to act on.
- **Append-only log.** Each decision also appends one line to `<case>/decisions.jsonl`:
  `{ts, case_id, checkpoint, phase_dir, decision, reason?, approver, identity_source, artifact_sha256, run_id?}`.
  The marker (atomic rename) and the log line (O_APPEND + fsync) land together or not at all; a second decision on
  the same checkpoint is 409. Cross-origin POSTs are 403.

## Identity

What the sign-in proxy actually proves, and what it does not:

- **NetBird Cloud forwards group membership, not the user.** Behind a NetBird Cloud reverse-proxy service with SSO,
  the console receives `X-NetBird-Groups` (comma-separated group names, client copies stripped by the proxy) and no
  user or email header (`X-NetBird-User` is only stamped for NetBird-Only private services). So the deployed mode is
  `ONTOFILL_CONSOLE_IDENTITY=sso-group`: a decision is allowed only when that header contains
  `ONTOFILL_CONSOLE_APPROVER_GROUP` (default `approvers`).
- **The name is self-declared.** The approver types a display name. APPROVED and `decisions.jsonl` record
  `approver: "group:<group>"`, `unverified_name: "<name>"`, `identity_source: "sso-group"` and
  `verified: {"group": "<group>", "via": "NetBird SSO (x-netbird-groups)"}`; the pages label the name "self-declared".
- **Direct mesh access cannot decide.** The proxy strips client-supplied identity headers only on requests it
  proxies; a peer on our own mesh could reach the console's NetBird IP directly and send its own header. Requests whose
  TCP peer address is in `ONTOFILL_CONSOLE_DIRECT_DENY` (our peers' IPs or CIDRs; forwarded-for headers are never
  trusted) get 403 "decisions must come through the NetBird proxy".
- `sso` mode (a per-user header) remains for proxies that forward one; `local` (typed name) is development only.
- **`readonly` is a public viewing instance** (e.g. a second NetBird service with a shared password): every page
  renders, and every non-GET request is refused with 403 before any route runs, whatever headers arrive, so no
  forwarded or forged header can make a viewer an approver. Deploy it as a separate container with
  `CONSOLE_WRITE_MODE=ro`, so its case, registry and runner mounts are read-only as well.
- `/whoami` shows header names, whether the groups header is present and contains the approver group, and whether the
  request came from a denied direct address; never a header value or an address.

Env: `ONTOFILL_CONSOLE_IDENTITY` (`sso-group` | `sso` | `readonly` | `local`), `ONTOFILL_CONSOLE_GROUPS_HEADER` (default
`X-NetBird-Groups`), `ONTOFILL_CONSOLE_APPROVER_GROUP` (default `approvers`), `ONTOFILL_CONSOLE_DIRECT_DENY`,
`ONTOFILL_CONSOLE_IDENTITY_HEADER` (sso mode).

## Cases: a data-driven registry (CONTRACT v1.0.6)

`ONTOFILL_CASES_ROOT` points at the cases root, shared with the runner and deploy. `cases.json` lists every case:
`{id, title, path, lake, lake_kind, budget_usd, to_phase, created_by, created_at, archived, archived_at, archived_by,
version, supersedes, superseded_by, brief_sha256}`. Paths are relative to the root for cases created here, or absolute
for migrated ones (mounted at identical paths). It is written atomically under a lock on `cases.json.lock`, and the
console picks up changes on the next request. Create a case at `/cases/new`: the question becomes `brief.md` exactly as
typed. The lake is `lake.template.yaml` with `case_id` set, or a scratch folder. "Start now" hands the case to the
runner. The brief and budget are editable only until a run exists (or a start is requested); after that, "Revise the
question" makes a new version and archives the old one. Archive/restore never deletes files. Every write needs the
approver identity and is logged in the case's `decisions.jsonl`. `ONTOFILL_CONSOLE_CASES` still works as a read-only
fallback, merged under the registry. `ONTOFILL_CASES_MAX_BUDGET_USD` caps the per-case budget (default 10).

## Run it

```bash
uv sync
uv run ontofill-console serve --fixtures .cache/console-fixtures --identity local   # two synthetic cases, dev mode
ONTOFILL_CONSOLE_CASES="main=/srv/case-a/case,demo=/srv/case-b/case:/srv/case-b/lake" uv run ontofill-console serve
uv run ontofill-console replay /path/to/local/lake --scratch /tmp/replay   # never writes the source lake
uv run pytest -q && uv run ruff check .
```

`ONTOFILL_CONSOLE_CASES`: `id=/case/dir[:/local/lake]` pairs; ids are `a-z0-9-`, at most 40 characters. Without
`:lake`, the case's lake is `<case parent>/lake.yaml` (the engine's pointer: `bronze.kind` file or s3; S3 needs
`AWS_*` and the `s3` extra). A case without a reachable lake still registers, so its approvals keep working.
Other settings: `ONTOFILL_CONSOLE_SPEND_HISTORY` (the spend tracker's history file), `ONTOFILL_CONSOLE_EVIDENCE_DIR`
(`verify-remote.txt`, `live-view-check.txt`, optional `track-checklist.md`), `ONTOFILL_CONSOLE_DEADLINE` (ISO time
for the spend projection). Both pages read saved files only.

Container: `docker build -t ontofill-console:local .`, then `deploy/compose.yaml` (read-only root filesystem, no
capabilities, non-root uid 10001; case directories mounted read-write because approvals write there, lakes and
evidence read-only; bind it to loopback or the host's NetBird IP and publish it through NetBird with SSO). Create the
default empty mount sources first (`mkdir -p ../.cache/empty-case ../.cache/empty-lake ../.cache/empty-evidence;
touch ../.cache/empty-lake.yaml`) or set the `CONSOLE_*` paths in `deploy/.env`.

## Runner controls (R18)

The console shares a state directory with the `ontofill-runner` service (`ONTOFILL_RUNNER_STATE`, default
`/var/lib/ontofill-runner`; see `runner/README.md`). A decided checkpoint resumes by itself; the case overview and the
approvals page show the runner's state ("Waiting for the prd decision", "Resumed automatically at HH:MM", "Stopped:
budget", "Runner off (kill switch)"). Approvers (same identity rules as approvals) can:

- `POST /cases/<id>/runner` `action=start|pause|resume` (`to_phase` 1–5 for start): writes `control.json`; every action
  is appended to the case's `decisions.jsonl` as `{checkpoint: "runner", decision: <action>, approver, identity_source,
  unverified_name?, verified?}`. Resume also re-arms one relaunch after a failed run.
- `POST /runner/kill` `state=on|off`: the global kill switch (`KILL`), logged in `console-decisions.jsonl`.

Runner failures, budget stops and kills appear in the inbox.
