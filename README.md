# Ontofill: any open question → an evidence-backed dataset, run safely on Vultr

Ontofill turns an open brief into a reviewed PRD and definition of done, an ontology,
source objectives, scoped extraction plans, and a dataset whose values link back to
captured public evidence. Vultr inference makes the typed decisions; isolated sandbox
jobs capture sources; the lake keeps raw evidence, observations, and validated exports.
The engine accepts a **case** from an application repo and runs the same five phases for
different subjects. [Proveedor Abierto](https://github.com/ricalanis/proveedor-abierto/tree/main/case)
is the reference case used to exercise the engine.

Tracked files contain code and documentation only. Recorded previews use the ignored `.cache/`
directory for a scratch case, local bronze objects, and silver observations.

| Folder | What goes there |
|--------|-----------------|
| `src/ontofill/` | The engine package: phases, executor, refiner, inference, lake adapters, sandbox control |
| `packages/ontofill-scrape/` | Resilience-first scraping toolkit, separately usable (tool contract, failures, policies) |
| `sandbox/` | Browser/spider pod images (Docker + gVisor), egress policy |
| `infra/` | Vultr VMs, Object Storage, Postgres, Oxigraph, NetBird (zero open ports) |
| `schemas/` | JSON Schemas for the case-package file formats the engine reads/writes |
| `tests/` | Unit tests and replay tests on stored captures |
| `docs/` | Definition docs (copied from planning) |

License: Apache-2.0. See `docs/planning/01-engine-definition.md`.

## Built during the event

Before the event, this repository contained the definition documents and empty package scaffold.
During the event, the engine implementation, runnable services, schemas, tests, and a recorded
five-phase run were added in dated local commits. The Git history records each build slice.

## Try a second brief through the ontology checkpoint

This uses the unrelated public-library brief in `tests/genericity`. Run these commands
from the Ontofill repo root with a live `VULTR_INFERENCE_API_KEY` in the ignored `.env`.
The explicit `run-` ID rejects a recorded fallback. All case files and lake objects go
under a new ignored `.cache/` directory; this does not touch the reference case.

```sh
mkdir -p .cache
demo_root="$(mktemp -d "$PWD/.cache/library.XXXXXX")"
mkdir -p "$demo_root/case"
cp tests/genericity/cases/libraries/brief.md "$demo_root/case/brief.md"
demo_run="run-library-$(date +%s)"
run_library() {
  LAKE_ROOT="$demo_root/lake" SILVER_DATABASE_URL= OXIGRAPH_URL= \
    uv run ontofill run "$demo_root/case" --to-phase 2 \
      --run-id "$demo_run" --budget-usd 1.00
}
run_library
```

The first call stops at `01-scope/APPROVAL_PENDING.md` with exit code 3. A human reviews
`01-scope/prd.json`, including each DoD criterion's basis, then writes `01-scope/APPROVED`
only if they accept it. Re-run `run_library`; review and approve
`02-ontology/factors/APPROVAL_PENDING.md`, then run `run_library` once more. It stops at
`02-ontology/APPROVAL_PENDING.md` with the live `ontology.json` ready for human review.
Use the same `demo_root` and `demo_run` values for every call. A denial with a reason
regenerates the rejected artifact on the next call; it never skips the checkpoint.
An accepted marker is JSON with `approver`, `date` (YYYY-MM-DD), and `checkpoint`
(`prd` or `factors`); see [the approval schema](schemas/approved.schema.json).

## Run the reference case

```sh
uv sync
uv run ontofill run ../proveedor-abierto/case
```

With no Vultr inference credentials, `run` uses a clearly labeled recorded decision double.
It generates a `mock-` run in `.cache/case-mock/`, returns exit code 3 for outstanding human
checkpoints, and never advances the case's latest pointers. Source URLs are discovered at
runtime from the case brief; values are emitted only from observed public cells.

With `VULTR_INFERENCE_API_KEY` set in the ignored `.env`, the engine selects a live Vultr
model, regenerates recorded artifacts, pauses for PRD, factors, and ontology approvals,
and resumes on the next `run`. The application owns its case directory and approval
markers. `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` enable Vultr Object Storage
through the S3 lake adapter.
