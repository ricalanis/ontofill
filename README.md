# Ontofill (working name)

Generic data-completion engine: takes a **case** (from an application repo), runs five phases, and
writes what it finds into the lake the case points to. "Simula for the real web."

Code only. No data, no lake, no case content lives here.

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
