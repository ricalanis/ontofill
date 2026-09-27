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
down, the Vultr content-safety model gives a second opinion. A chunk is flagged if Jev says injection with
confidence ≥ 0.8, if the safety model says `unsafe`, or if Jev was unsure (< 0.8, either answer) or down and the
safety model did not explicitly say `safe` (`reason: unscreened`). Long untagged agent prompts (Skyvern's, which
include the agent's own instructions) typically get a low-confidence "injection" from Jev; the safety model decides
those. A "benign" verdict never
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

## Cells

Each session leases one **cell** (CONTRACT §13a) when `BA_CELL_PROVIDER` is set; the native backend then drives
that cell's Chromium over its `cdp_url` instead of a browser on the control plane.

- Provider interface (`controller/cells.py`), the shape of the engine's substrate:
  `create(backend: native|skyvern, allowed_domains, limits, placement: sandbox_vm|throwaway_vx1)` →
  `{cell_id, cdp_url, brain_url?, live_view_port}`, `destroy(cell_id)`, `status(cell_id)`.
- `BA_CELL_PROVIDER`: `none` (default; the backend launches or connects a browser itself), `docker-stub`,
  `ontofill-http` (**the engine's gVisor cell substrate** over its loopback API `serve_cells`: `BA_CELLS_URL`,
  default `http://127.0.0.1:8766`, and `BA_CELLS_TOKEN`), or `ontofill` (an importable module with the same three
  functions). With `ontofill-http` the controller counts each browser action with the substrate (`POST
  /cells/{id}/steps`; it stops the cell at `max_steps`), reports the session's real outcome at close (`task-result`,
  never inferred), and returns the teardown's six-checkpoint `job_record` in `session.close` → `cell.teardown`. The
  substrate requires gVisor (`runsc`) and fails closed without it; its cells age from creation, so the warm pool
  defaults to K=0 there. End-to-end check: `BA_CELLS_TOKEN=… uv run pytest -m docker tests/test_cells_engine_e2e.py`
  where the substrate runs (control plane → sandbox host). With a provider set, a failed lease fails
  `session.open`; there is no fallback to a host browser.
- Warm pool (`BA_CELL_K_NATIVE`, default 1; `BA_CELL_K_SKYVERN`, default 0). A cell's allowed domains and caps are
  fixed at creation, so warm cells are made for the most recent (domains, caps) per backend and a lease that does not
  match creates a fresh cell. Release destroys the cell and a replacement is created in the background: **a cell is
  never reused across sessions**. `session.open` returns `cell_id` + `isolation`; the first step's `executed.cell`
  carries them with `create_ms` / `lease_ms` / `warm`; `session.close` returns `destroy_ms`.
- **`docker-stub` is a local stand-in, not a sandbox:** runc (tier 2), and the egress allowlist is enforced only by
  the controller's per-request route abort, not at the network layer; `isolation` says so. One hands container per
  cell (upstream Skyvern image as a Chromium carrier, unmodified, `cells/hands.sh` mounted read-only) on its own
  Docker network, `--memory/--cpus/--pids-limit`, CDP published on 127.0.0.1 only, no environment passed at all.
  A skyvern cell gets hands only (`brain_url` null). Measured on a laptop (OrbStack, image already pulled): cold
  create 0.42 s, warm lease ~0 ms, destroy 0.32 s, hands memory 149 MiB idle / 163 MiB during a task.
- `uv run pytest -q -m docker` runs a real stub cell end to end and checks that nothing is left behind.

## Live view

Each session gets a read-only live view of its browser (architecture §8: per-cell live-view URLs that die with the
cell). `session.open` returns `live_view_url` = `http://127.0.0.1:<BA_LIVEVIEW_PORT>/live/<session_id>?t=<view
token>` (or `BA_LIVEVIEW_PUBLIC_BASE/live/...`, e.g. the `netbird expose` URL of the control plane). Routes: the page
itself (a minimal viewer), `/stream` (MJPEG, `multipart/x-mixed-replace`) and `/frame.jpg` (latest frame).

- Frames come from a second CDP client on the session's own browser (the cell's `cdp_url`; for a locally launched
  development browser, a loopback DevTools port opened only when the live view is on): `Page.startScreencast`
  (JPEG, quality 60, max width 1280) on the newest page with content, each frame acked, only the latest kept. The
  viewer receives images only: no input is forwarded and no CDP endpoint is exposed. With no CDP endpoint the view
  shows the session's latest screenshot capture and says "latest capture, not live".
- Every route needs the session's random view token (constant-time compare); a wrong or missing token, an unknown
  session or a closed one is a `404`. Responses are `Cache-Control: no-store`, `Referrer-Policy: no-referrer`; framing
  is allowed so the investigation app can embed the view.
- `session.close` stops the screencast, disconnects the viewer's CDP client and ends open streams within about a
  second. The URL and its token are returned to the caller only; they never go into trace steps.
- `BA_LIVEVIEW_PORT` (default 8702; `0` disables it). The server binds loopback only and is shared by all sessions in
  the process; if the port is taken, sessions run without a live view.
- Screencast frames are sent only when the page changes, so an idle page shows its last frame. Measured on a local
  docker-stub cell: first frame immediately (a seed screenshot), about 26 ms from a DOM change to its frame.

**Per-session `netbird expose` (production; NetBird approach 4, a URL that dies with the cell).** With
`BA_LIVEVIEW_EXPOSE=netbird`, `session.open` starts a listener for that session only (on `BA_LIVEVIEW_HOST`, the
control NetBird IP in production, port from `BA_LIVEVIEW_PORTS`, default `8710-8759`) and spawns
`netbird expose <port> --with-name-prefix pa-live` for it, reading the `URL:` line from the child's merged
stdout/stderr (the success block goes to stderr) within `BA_LIVEVIEW_EXPOSE_TIMEOUT_S` (default 20).
`live_view_url` is then `<exposed URL>/live/<session_id>?t=<view token>`: the token stays as defense in depth.
`session.close` stops the view, sends SIGTERM to the expose's process group (NetBird removes the service at once;
SIGKILL after 5 s) and closes the listener, so the URL itself stops working. `close_all` and interpreter exit do
the same for any session left open. If the expose cannot start or prints no URL in time, the session still opens,
with `live_view_url: null` and `live_view: {"error": ...}`. The child runs with a minimal environment (PATH, HOME,
LANG) and no NetBird auth flags; `BA_LIVEVIEW_EXPOSE_ARGS` (shlex-split) adds extra flags, e.g.
`--with-user-groups approvers`. `BA_LIVEVIEW_EXPOSE_BIN` overrides the binary (default `netbird`). Logs carry the
exposed host only, never the token.

## Deployment (CONTRACT v0.9.4)

On the control VM the gateway binds the control plane's NetBird IP so Skyvern brains on the sandbox host can reach
it (and nothing else; the sandbox's narrow brain → gateway rule is the substrate's): `BA_GATEWAY_HOST=<control NetBird
IP>`, `BA_GATEWAY_PORT=8700`; brains get `OPENAI_COMPATIBLE_API_BASE=http://<control NetBird IP>:8700/v1`. The engine's
cell API (`serve_cells`) stays on loopback. Publish live views per session: `BA_LIVEVIEW_EXPOSE=netbird`,
`BA_LIVEVIEW_HOST=<control NetBird IP>` (the proxy dials the peer IP, never loopback), `BA_LIVEVIEW_PORTS=8710-8759`,
Peer Expose enabled for the control plane's group, and the controller running as a user that can reach the NetBird
daemon. No always-on expose unit and no `BA_LIVEVIEW_PUBLIC_BASE` are needed then (that shared-hub mode remains for
development). `BA_CELLS_TIMEOUT_S` (default 600) bounds a cell create, which builds images on a fresh host.

## Service principals (the engine)

The engine is a gateway client like any session: `BA_GATEWAY_SERVICE_TOKENS=engine:<sha256 of its token>:<budget_usd>`
registers a long-lived, budget-capped, revocable principal (no TTL). The gateway stores only the hash; its spend is
restored from the call log on restart, so the cap survives restarts. The engine then points its decision client at the
gateway (`VULTR_INFERENCE_BASE_URL=http://<control NetBird IP>:8700/v1`, with its service token as the bearer), holds no
Vultr or Jev key, and its prompts are screened like any other. Every call is logged under `session_id: engine`.

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
