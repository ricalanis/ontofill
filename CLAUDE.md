# Notes for coding agents

- Read `docs/planning/01-engine-definition.md` (what) and `docs/planning/ontofill-plan.md` (how/when) first.
- Engine repo is **code only**. Case content lives in the application repo; data lives in the external lake.
- All inference goes to Vultr Serverless Inference (OpenAI-compatible, tool calling).
  Model list: https://api.vultrinference.com/v1/models.
- Browsers and spiders run in sandboxes (containers/throwaway instances), never in-process.
- Data completion is read-only: no writes, SAFE/LOW edges only, captcha or login wall = stop.
- Every step logs observed / requested / executed / evaluated. `emit.observation` is the only output channel.
- Build the walking skeleton end to end first; keep each phase thin until the field-complete gate.
- No secrets in git. Config comes from env vars (`.env.example` lists names only).

## Commands

From the `ontofill/` repository root:

```sh
cp .env.example .env
# Fill the names in .env. For local compose, set SILVER_DATABASE_URL to
# postgresql://<POSTGRES_USER>:<POSTGRES_PASSWORD>@127.0.0.1:5432/<POSTGRES_DB>,
# OXIGRAPH_URL to http://127.0.0.1:7878, and LAKE_ROOT to a directory outside
# the checkout (for example, /tmp/ontofill-lake). S3 names are for Vultr.
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

The compose endpoints bind to localhost. `docker compose ... ps` shows service
readiness. The Oxigraph readiness probe is `oxigraph-health` because the
database image has no shell.
