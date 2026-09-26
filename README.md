# Ontofill (working name)

Generic data-completion engine: takes a **case** (from an application repo), runs five phases, and
writes what it finds into the lake the case points to. "Simula for the real web."

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

## Run a case

```sh
uv sync
uv run ontofill run ../proveedor-abierto/case
uv run ontofill export ../proveedor-abierto/case --run-id mock-<run-id>
```

With no Vultr inference credentials, `run` uses a clearly labeled recorded decision double.
It generates a `mock-` run in `.cache/case-mock/`, returns exit code 3 for outstanding human
checkpoints, and never advances the case's latest pointers. Its source URL is discovered at
runtime from the case brief; supplier values are emitted only from observed public cells.

When `VULTR_INFERENCE_API_KEY` and `VULTR_INFERENCE_MODEL` are set in the ignored `.env`,
the engine regenerates recorded artifacts with live calls, pauses for PRD, factors, and
ontology approvals, and resumes on the next `run`. The application owns the live case
directory and approval markers. `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` enable
Vultr Object Storage through the S3 lake adapter.
