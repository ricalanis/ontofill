# AGENTS.md (notes for coding agents; mirrors CLAUDE.md)

- Read `docs/planning/01-engine-definition.md` (what) and `docs/planning/ontofill-plan.md` (how/when) first.
- Engine repo is **code only**. Case content lives in the application repo; data lives in the external lake.
- All main inference goes to Vultr Serverless Inference (OpenAI-compatible, tool calling).
  Model list: https://api.vultrinference.com/v1/models. Jev is an optional helper behind the decision interface only.
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
