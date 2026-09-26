# AGENTS.md (notes for coding agents; mirrors CLAUDE.md)

- Read `docs/planning/01-engine-definition.md` (what) and `docs/planning/ontofill-plan.md` (how/when) first.
- Engine repo is **code only**. Case content lives in the application repo; data lives in the external lake.
- All inference goes to Vultr Serverless Inference (OpenAI-compatible, tool calling).
  Model list: https://api.vultrinference.com/v1/models.
- Browsers and spiders run in sandboxes (containers/throwaway instances), never in-process.
- Data completion is read-only: no writes, SAFE/LOW edges only, captcha or login wall = stop.
- Every step logs observed / requested / executed / evaluated. `emit.observation` is the only output channel.
- Build the walking skeleton end to end first; keep each phase thin until the field-complete gate.
- No secrets in git. Config comes from env vars (`.env.example` lists names only).

## Public-repo scope rules (clean-room)
- This is a public research repo. Do not use, cite, paraphrase or design by analogy from any company's
  internal or proprietary technology, data, evals or unreleased work. Derive ideas from public literature
  or first principles.
- Ground claims in publicly citable sources (papers, open benchmarks, open-source repos); flag uncertainty.
- No internal identifiers: no customer names, private endpoints, credentials, internal repo paths or metrics.

## Coordination
This repo is one of two built in parallel. The cross-repo interface (CLI, lake layout, gold export,
metrics) is in `../coord/CONTRACT.md`; report progress in `../coord/status/codex-ontofill.md`.

## Commands

From the `ontofill/` repository root, copy `.env.example` to the ignored `.env`
and fill the names in it. Local service URLs are
`postgresql://<POSTGRES_USER>:<POSTGRES_PASSWORD>@127.0.0.1:5432/<POSTGRES_DB>`
and `http://127.0.0.1:7878` (Oxigraph). Set a top-level `case_id`,
`bronze.kind: file`, and `bronze.root` in the case's `lake.yaml`, or override the
root with `LAKE_ROOT` pointing outside the checkout. `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY` are for Vultr Object Storage; its endpoint and bucket
live in `lake.yaml`.

```sh
docker compose --env-file .env -f infra/compose/compose.yaml up -d --wait
uv sync
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run ontofill run ../proveedor-abierto/case
uv run ontofill refine ../proveedor-abierto/case
uv run ontofill export ../proveedor-abierto/case
docker compose --env-file .env -f infra/compose/compose.yaml down
```

`oxigraph-health` probes Oxigraph HTTP readiness from outside its distroless
image. `docker compose ... ps` reports the stack state. Keep `.env` and lake
data out of Git.
