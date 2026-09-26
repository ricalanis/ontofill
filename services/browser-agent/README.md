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

Errors: `401` unknown/expired/revoked token, `402` session budget exhausted, `502` upstream failure.

Admin calls (`Authorization: Bearer $BA_GATEWAY_ADMIN_TOKEN`, controller only):

- `POST /admin/sessions` `{session_id, ttl_s, budget_usd, run_id?}` → `{session_id, token, expires_at}`
- `GET /admin/sessions/{id}` → `{session_id, spent_usd, budget_usd, calls, expires_at, revoked, flagged}`
- `POST /admin/sessions/{id}/revoke` → `{revoked: true}`

Call log (`$BA_GATEWAY_LOG`, JSONL, one line per upstream call; never the key, never full prompt text):
`{ts, session_id, run_id, step_id, upstream: vultr|jev, model, status, input_tokens, output_tokens, est_usd,
gate: {checked, flagged, jev: {...}, safety: {...}}, latency_ms}`.

## Environment

See `shared/config.py`. Only the gateway reads `VULTR_INFERENCE_API_KEY` and `JEV_API_KEY`.

## Development

```bash
cd services/browser-agent
uv sync
uv run pytest -q            # offline: upstreams are mocked
uv run pytest -q -m live    # real Vultr/Jev calls through the gateway (needs keys in the environment)
```
