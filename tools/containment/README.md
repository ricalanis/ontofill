# ontofill-containment (R8)

Run the engine's **existing** containment fixtures against the production sandbox
substrate and append their proof to a real run's feed, so the Ontofill Console's
run view shows them like any other step. It authors no new hostile content: it
serves `sandbox/fixtures/hostile.html` and runs
`sandbox/fixtures/destructive_loop.py`.

```sh
ontofill-containment --case <case_dir> --run-id <id>
```

* `--case` — the case directory the run belongs to (its `lake.yaml`/`LAKE_ROOT`
  is resolved exactly like `ontofill run`).
* `--run-id` — defaults to `containment-demo-<yyyymmddhhmm>` (UTC).

It **appends** to `runs/<case_id>/<run_id>/trace.live.jsonl` and `jobs.jsonl` and
never rewrites an existing line; every step id is unique.

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
# on the control VM, from the engine checkout
set -a; . /opt/ontofill/engine.env; set +a
set -a; . /opt/ontofill/browser-agent.env; set +a   # ONTOFILL_SANDBOX_DOCKER_HOST, DOCKER_SSH_COMMAND
export LAKE_ROOT=/srv/demo-library/lake
tools/containment/bin/ontofill-containment --case /srv/demo-library/case --run-id containment-demo-<ts>
```

## Tests

```sh
uv run pytest tools/containment/tests -q
uv run ruff check tools/containment
```
