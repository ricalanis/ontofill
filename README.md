# Ontofill: any open question → an evidence-backed dataset, run safely on Vultr

Ontofill turns an open brief into a reviewed PRD and definition of done, an ontology,
source objectives, scoped extraction plans, and a dataset whose values link back to
captured public evidence. Vultr inference makes the typed decisions; isolated sandbox
jobs capture sources; the lake keeps raw evidence, observations, and validated exports.
The engine accepts a **case** from an application repo and runs the same five phases for
different subjects. [Proveedor Abierto](https://github.com/ricalanis/proveedor-abierto/tree/main/case)
is the reference case used to exercise the engine.

**For judges:** [Live, read-only Ontofill Console](https://ontofill-console-judges.eu1.netbird.services).
The password is linked from [Proveedor Abierto's For judges section](https://github.com/ricalanis/proveedor-abierto#for-judges).

## How it works

The engine turns a question into a reviewed PRD, an inferred ontology, confirmed sources,
bounded collection, and linked data with evidence for each value. See
[From an open question to linked data](docs/planning/04-question-to-linked-data.md)
for the steps and the artifacts they produce.

## Runs on Vultr and NetBird

Vultr provides inference, the control and sandbox VMs, and the evidence lake. NetBird
connects the VMs and provides access without public inbound ports. See
[How Ontofill uses Vultr and NetBird](docs/reference/vultr-netbird-usage.md)
for the deployed layout and network boundaries.

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
from the Ontofill repo root with `ONTOFILL_GATEWAY_TOKEN` and the gateway's
`VULTR_INFERENCE_BASE_URL` in the ignored `.env`.
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

The first call stops at `01-scope/APPROVAL_PENDING.md` with exit code 3. After a human
reviews and accepts `prd.json` and its DoD basis, this scratch-only helper writes an
approval bound to the exact file bytes:

```sh
approve_reviewed() {
  uv run python - "$demo_root/case" "$1" "$2" "$demo_run" <<'PY'
import hashlib, json, sys
from datetime import UTC, datetime
from pathlib import Path
root, relative, checkpoint = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
artifact = root / relative
marker = {
    "approver": "Local reviewer", "date": datetime.now(UTC).date().isoformat(),
    "checkpoint": checkpoint, "identity_source": "local", "run_id": sys.argv[4],
    "artifact_sha256": {relative: hashlib.sha256(artifact.read_bytes()).hexdigest()},
}
(artifact.parent / "APPROVED").write_text(json.dumps(marker) + "\n", encoding="utf-8")
PY
}
approve_reviewed 01-scope/prd.json prd
run_library
# Review factors.json before continuing:
approve_reviewed 02-ontology/factors/factors.json factors
run_library
```

The final call stops at `02-ontology/APPROVAL_PENDING.md` with a live
`ontology.json` ready for review. Keep the same `demo_root` and `demo_run` values.
A denial with a reason regenerates the rejected artifact on the next call; it never
skips a checkpoint. See [the approval schema](schemas/approved.schema.json).

## Run the reference case

```sh
uv sync
uv run ontofill run ../proveedor-abierto/case
```

With no Vultr inference credentials, `run` uses a clearly labeled recorded decision double.
It generates a `mock-` run in `.cache/case-mock/`, returns exit code 3 for outstanding human
checkpoints, and never advances the case's latest pointers. Source URLs are discovered at
runtime from the case brief; values are emitted only from observed public cells.

With `ONTOFILL_GATEWAY_TOKEN` and `VULTR_INFERENCE_BASE_URL` set in the ignored
`.env`, the engine selects a live Vultr model through the screened gateway,
regenerates recorded artifacts, pauses for PRD, factors, and ontology approvals,
and resumes on the next `run`. The application owns its case directory and approval
markers. `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` enable Vultr Object Storage
through the S3 lake adapter.
