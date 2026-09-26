# browser-agent

The browser layer of Ontofill (CONTRACT §13 / brief 07 v2). Three parts, all on the control plane:

| Part | Directory | Job |
|------|-----------|-----|
| Inference gateway | `gateway/` | OpenAI-compatible proxy to Vultr Serverless Inference and a Jev proxy. Holds the **only** real keys. Issues per-session tokens (TTL, $ budget, revocation), logs every call with session/step usage, and screens page content inside prompts (Jev injection gate + Vultr content safety) before forwarding. |
| Controller | `controller/` | MCP façade `session.open/act/observe/close`, the step loop (plan → guard → act → verify), §12 step emission, approve-before-submit gate. |
| Backends | `backends/native/`, `backends/skyvern/` | Native: our Playwright loop over CDP. Skyvern: cells (one unmodified Skyvern brain + one gVisor browser pod), pointed at the gateway with a session token. |

**No long-lived credential in any sandbox.** Pods hold no keys. Brains (Skyvern) and the controller hold only a
session token that expires, has a budget and can be revoked.

## Gateway HTTP API (pinned)

Session-token calls (`Authorization: Bearer <session token>`, optional `X-BA-Step-Id: <step id>`):

- `POST /v1/chat/completions`: OpenAI chat format, forwarded to Vultr. Page content in a user message should be
  wrapped as `<page_content>…</page_content>`; the gateway also screens any other user/tool text over 2,000 chars
  (Skyvern prompts are not marked). A flagged chunk is **quarantined, not removed**: it is wrapped in a notice
  telling the model to treat it as untrusted data, and the call is annotated in the log. Response: Vultr's body,
  plus header `X-BA-Gate: clean|flagged`.
- `GET /v1/models`: Vultr's model list (for OpenAI-compatible clients that probe it).
- `POST /v1/jev/systemone`: `{state, questions}` → Jev's body. The gateway sets the model.

- `GET /healthz`: liveness, and whether each upstream key is present (never the value).

Errors: `400` malformed body, `401` unknown/expired/revoked token, `402` session budget exhausted, `502` upstream
failure.

Screening is one-way and fails closed: Jev asks first; if Jev says injection, is below 0.8 confidence or is
down, the Vultr content-safety model gives a second opinion. A chunk is flagged if either says so, or if Jev was
unsure/down and the safety model did not explicitly say `safe` (`reason: unscreened`). A "benign" verdict never
removes another defense. Screening calls are charged to the same session and logged with `purpose: screen`;
results are cached per chunk hash (errors are not cached).

Admin calls (`Authorization: Bearer $BA_GATEWAY_ADMIN_TOKEN`, controller only; `503` if the gateway has no admin
token configured):

- `POST /admin/sessions` `{session_id, ttl_s, budget_usd, run_id?}` → `{session_id, token, expires_at}`.
  Reopening an id issues a new token and invalidates the old one.
- `GET /admin/sessions/{id}` → `{session_id, run_id, spent_usd, budget_usd, calls, expires_at, revoked, flagged}`
  (`404` if unknown)
- `POST /admin/sessions/{id}/revoke` → `{revoked: true}`

Call log (`$BA_GATEWAY_LOG`, JSONL, one line per upstream call; never the key, never full prompt text):
`{ts, session_id, run_id, step_id, upstream: vultr|jev, purpose: chat|decision|screen, model, status, input_tokens,
output_tokens, est_usd, prompt_chars, gate: {checked, flagged, chunks: [{chars, flagged, jev, safety}]},
latency_ms}` (+ `est_tokens: true` when a stream sent no usage and tokens were estimated from length).

## Controller (MCP)

`uv run ba-controller [--transport stdio|streamable-http]`. Tools (CONTRACT §13):

- `session.open(tdd, allowed_domains, limits?)` → `{session_id, live_view_url, url, steps_path}`. Mints a gateway
  session token (budget/TTL from `limits`: `max_steps` 30, `timeout_s` 900, `max_attempts` 3, `approval_timeout_s`
  300, `budget_usd` 0.50, `ttl_s` 900). Optional `tdd` keys: `run_id`, `source_id`, `objective_id`, `tdd_path`,
  `job_id`, `case_dir`, `cdp_url`, `start_url`, `vision_grounding`.
- `session.act(session_id, goal?, action?)`: exactly one. A goal runs the loop; an explicit action goes through the
  same guard. → `{status, summary, url, extracted, metrics}`.
- `session.observe(session_id)` → `{observation, extracted, metrics}`.
- `session.close(session_id)` → `{closed, metrics, token_revoked}`.

The loop per turn: observe (capture to bronze) → injection screen (controller Jev pre-screen + the gateway's screen)
→ plan (Vultr tool call, page text only inside `<page_content>`) → action guard (deterministic code floor, then Jev,
which can only raise the tier; HIGH writes `05-actions/<id>/APPROVAL_PENDING.md` and waits; a timeout is a deny;
off-allowlist targets are denied outright) → act → step check (Jev; a confident "progress" skips the vision call)
→ Vultr vision verify otherwise. Steps (§12/§12a) go to `$BA_STEPS_DIR/<session_id>.jsonl`: `quarantine` (page
withheld from planning, kept in bronze), `action_gate`, `verify`, `limit_kill`, `hard_stop` (gateway 401/402).
Metrics: `jev_observations_screened`, `jev_flagged`, `vision_calls_avoided`, `vision_calls_made`, `estimated_usd`,
`estimated_usd_saved`, `backend_breakdown`, `blocked_hosts`.

The native backend drives Chromium over CDP (`cdp_url`, the pod) or launches it locally for development; every
request outside `allowed_domains` is aborted and recorded (the pod's egress proxy enforces the same list below it,
including WebSockets, which page routing does not see).

## Environment

See `shared/config.py`. Only the gateway reads `VULTR_INFERENCE_API_KEY` and `JEV_API_KEY`. The gateway listens on
`127.0.0.1:8700` (`BA_GATEWAY_HOST`, `BA_GATEWAY_PORT`); run it with `uv run ba-gateway`.

## Development

```bash
cd services/browser-agent
uv sync
uv run pytest -q            # offline: upstreams are mocked
uv run pytest -q -m live    # real Vultr/Jev calls through the gateway (needs keys in the environment)
```
