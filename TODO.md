# Work list

## C1: tooling and contract
- [x] Initialize Git, install full Apache-2.0 license, commit scaffold.
- [x] Build uv workspace, CLI skeleton, schemas, local Postgres/Oxigraph compose, tests and dev commands.
- [x] Check: `uv run pytest -q` → 41 passed; `docker compose --env-file .env -f infra/compose/compose.yaml up -d --wait` → 3 services started, Postgres and Oxigraph probe healthy; `uv run ruff check .` and `uv run ruff format --check .` pass.
FILES: `LICENSE`, `pyproject.toml`, `packages/ontofill-scrape/`, `src/ontofill/cli/`, `src/ontofill/lake/`, `src/ontofill/inference/README.md`, `docs/planning/01-engine-definition.md`, `schemas/`, `tests/`, `infra/compose/`, `.env.example`, `.gitignore`, `CLAUDE.md`, `AGENTS.md`, `README.md`, `GOAL.md`, `TODO.md`, `NOTES.md`.
TASK: Make the engine installable with contract-valid documents, file:// local lake, and healthy local services.
DONE: `uv run pytest -q && docker compose --env-file .env -f infra/compose/compose.yaml up -d --wait`.
FORMAT: `uv run ruff check . && uv run ruff format --check .`.

## C2: sandbox gate
- [ ] Docker browser pod, TDD domain allowlist, file:// bronze captures and step logs.
- [ ] Check: allowed capture yields HTML, accessibility tree and screenshot; disallowed URL fails; trace exists.
FILES: `sandbox/`, `src/ontofill/sandbox/`, `src/ontofill/lake/`, `tests/`.
TASK: Capture only allowlisted public pages in a contained browser, recording evidence and trace.
DONE: `uv run pytest -q tests/test_sandbox.py`.
FORMAT: `uv run ruff check . && uv run ruff format --check .`.

## C3: five-phase walking skeleton
- [ ] Brief to PRD, ontology, discovered source, local TDD, S1 extraction, silver, SHACL, gold and export.
- [ ] Check: case run yields one evidenced supplier and valid export metrics.
FILES: `src/ontofill/`, `packages/ontofill-scrape/`, `schemas/`, `tests/`, `.env.example`, `README.md`.
TASK: Run one source end to end using discovered sources and evidence-backed gold values.
DONE: `uv run pytest -q && uv run ontofill run ../proveedor-abierto/case --run-id c3-check && uv run ontofill export ../proveedor-abierto/case --run-id c3-check`.
FORMAT: `uv run ruff check . && uv run ruff format --check .`.
