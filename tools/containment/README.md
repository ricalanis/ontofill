# ontofill-containment (R8)

Run the engine's **existing** containment fixtures against the production sandbox
substrate as a standalone live sandbox demo. It creates a dedicated run feed for
the Ontofill Console. This demo stays separate from any question→gold investigation
run and never adds steps to an existing run. It authors no new hostile content: it
serves `sandbox/fixtures/hostile.html` and runs
`sandbox/fixtures/destructive_loop.py`.

```sh
ontofill-containment --case <case_dir> --run-id <id>
```

* `--case` — the case directory the run belongs to (its `lake.yaml`/`LAKE_ROOT`
  is resolved exactly like `ontofill run`).
* `--run-id` — must be a fresh `containment-demo-<suffix>` ID; it defaults to
  `containment-demo-<yyyymmddhhmm>` (UTC).

The command refuses a run ID with any existing `status.json`, `trace.live.jsonl`,
or `jobs.jsonl` feed before it contacts the gateway or writes run artifacts. For
the new demo run, it appends trace and job records and never rewrites a line; every
step ID is unique.

## What it records

1. **Hostile page** (the existing opt-in fixture): `capture_url` runs the page
   through a real gVisor pod and the allowlist egress proxy; the six proof trace
   steps and the six-checkpoint `jobs.jsonl` record are appended. The captured
   page is then sent through the inference gateway as one `<page_content>` span;
   the gateway's `X-BA-Gate: flagged` verdict becomes the §12a `quarantine` step
   (blocked egress requests included). Screening fails closed.
2. **Destructive loop** (the existing repair fixture): `run_code_repair` with
   `DockerRepairExecutor` runs `destructive_loop.py` on `runsc` and the
   `repair` + `limit_kill` steps are appended. A host sentinel is written before
   the run and checked after; a matching disposable probe pod supplies the
   `where`/`isolation`/`secrets`/`host.virt` checkpoints for the job record.

## Environment

Reuses the engine's env (never print values). Needs:

* `VULTR_INFERENCE_BASE_URL` + `ONTOFILL_GATEWAY_TOKEN` — the screening gateway (R9).
* `ONTOFILL_SANDBOX_DOCKER_HOST=ssh://…` (and optionally `DOCKER_SSH_COMMAND`)
  — the sandbox VM with `runsc`.
* the case's `lake.yaml` or `LAKE_ROOT`.

When the sandbox Docker is remote, the fixture server publishes its port on the
sandbox host's bridge gateway and installs one scoped, self-removed `INPUT`
ACCEPT rule (source = the bridge subnet, destination = that port, tagged with a
unique comment) so `host.docker.internal` is reachable from the capture proxy.
This mirrors the per-cell firewall rules in `ontofill.sandbox.cells`.

## Run

```sh
# on the control VM, from the engine checkout; this creates a standalone demo run
set -a; . /opt/ontofill/engine.env; set +a
set -a; . /opt/ontofill/browser-agent.env; set +a   # ONTOFILL_SANDBOX_DOCKER_HOST, DOCKER_SSH_COMMAND
export LAKE_ROOT=/srv/demo-library/lake
tools/containment/bin/ontofill-containment --case /srv/demo-library/case --run-id containment-demo-<fresh-suffix>
```

## Install and test (fresh clone)

The tool reuses the engine package from the parent checkout through a uv path
dependency (`[tool.uv.sources] ontofill = { path = "../..", editable = true }`), so
it needs no separate engine install. From `tools/containment`:

```sh
uv sync --extra dev
uv run --extra dev python -m pytest tests -q
uv run --extra dev ruff check .
```

`uv sync --extra dev` builds the tool's own `.venv` (with the engine editable from
`../..`); `uv run --extra dev python -m pytest tests -q` is the one documented test
command. `bin/ontofill-containment` uses that venv when present and otherwise falls
back to `uv run --project tools/containment --extra dev ontofill-containment`.

Running the engine's own suite from the checkout root is unchanged
(`uv run pytest -q`); the tool is not an engine workspace member.
