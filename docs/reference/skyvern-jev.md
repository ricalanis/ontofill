# Skyvern + Jev as the computer-use / web-exploration layer

Research date: 2026-09-26. Public sources only (GitHub repo, docs.skyvern.com — which 308-redirects to
`www.skyvern.com/docs/...` — LICENSE, releases, issues, plus `docs.browser-use.com` and
`github.com/microsoft/playwright-mcp`). Nothing here was verified by installing or running Skyvern, calling
Vultr, or calling Jev; that's out of scope for this document. **UNVERIFIED** marks anything no fetched public
source confirmed. Read against `docs/reference/track-blast-radius-zero.md` (the official challenge text) and
`docs/planning/01-engine-definition.md`.

This file answers two questions first, because they decide everything else: **(1) can Skyvern run entirely on
our Vultr Serverless Inference subscription?** Yes, with caveats — §1. **(2) Can one Skyvern instance act as a
multi-session "browser-agent MCP microservice" driving many gVisor pods concurrently over CDP, as a natural
architecture for the engine?** **No — verified against Skyvern's own Browser Session API and a documented
self-hosted concurrency limitation (§9).** That finding reshapes the recommended architecture: a thin MCP façade
we write ourselves, fronting a native Playwright backend (which *does* support N concurrent pods) as the primary
path, with Skyvern usable only as a single-concurrency fallback backend.

---

## TL;DR recommendation

**Build a thin MCP façade ourselves — `session.open` / `session.act` / `session.observe` / `session.close` —
with a native Playwright-driven backend as the primary, multi-session executor, and an optional Skyvern backend
wired for exactly one concurrent session at a time as the S2 fallback.** Do not deploy Skyvern today; keep it
config-ready. Reasoning, tightened against the official track text:

- The track's **Pattern B** loop is explicit: plan → Playwright in a container acts → screenshots return → **a
  vision model on Serverless Inference verifies the result** → repeat, with a human approving anything final.
  Our own loop matches that literally, with a genuine two-stage plan/verify step boundary. Skyvern's per-step
  loop bundles planning, screenshot grounding and implicit verification into **one** LLM call — a real but
  partial fit (§0).
- The track's **containment moment** (a hostile page absorbed safely, on screen) needs a pre-model text screen.
  Our loop already captures page text via `page.snapshot` before any LLM sees it, so Jev's injection gate sits
  naturally there. Skyvern has no documented pre-model injection screen; a hostile page's text goes straight
  into its own internal planning call unless we intercept it ourselves first — which makes Skyvern's own
  navigation redundant for that step (§0, §6b.1).
- The track's **secret-hygiene** constraint (no keys in the sandbox pod) is satisfiable by either brain: both
  Skyvern's `BROWSER_TYPE=cdp-connect` and our own loop let the key-holding process sit on the control plane and
  drive a key-less pod browser remotely. But **Skyvern's own Browser Session API turns out to assume Skyvern
  launches and owns the browser** (`browser_type: msedge|chrome|stealth-chromium`, no per-session external-CDP
  input) — the `cdp-connect` path is a separate, **global, one-process-per-browser** self-hosted config, not a
  per-session parameter. One self-hosted Skyvern process can drive exactly one remote pod at a time (§9). A
  "browser-agent microservice" that multiplexes many concurrent pods through one Skyvern instance **is not
  architecturally supported** — confirmed against a closed GitHub issue describing exactly this limitation
  (shared VNC display, no per-session isolation) for concurrent self-hosted sessions.
- Jev has a real, designed place in this service, not an optional add-on — six concrete wiring points are
  specified in §6b, each with an exact question type, threshold, and a cost/volume estimate, feeding a run-view
  metric ("Jev screened N observations; M vision calls avoided").
- Given the hackathon ends Sun 12:00 PT (~21h remaining at research time), building the native backend first is
  the lower-risk sequencing: it is a strict subset of the hybrid, fully satisfies every "must add" row in the
  track table on its own, and doesn't depend on Skyvern's Compose stack, Postgres, or its unverified
  cdp-connect-under-load behavior at all. See §7-§8 for the numbers and §11 for the slice-by-slice brief.

---

## 0. Alignment to the official track (Blast Radius Zero)

The track table (`track-blast-radius-zero.md`) marks five requirement rows **"must add"** or **"must add +
show"**, and all five depend on this research. Scored below for Skyvern, our own Playwright-driven loop, and the
hybrid (native primary, Skyvern S2 fallback).

| Track hard constraint | Skyvern | Our Playwright-driven loop | Hybrid |
|---|---|---|---|
| **1. Secret hygiene** — no keys in the sandbox pod; whatever holds the Vultr key drives the pod's browser remotely over CDP | ✅ with a caveat. `BROWSER_TYPE=cdp-connect` + `BROWSER_REMOTE_DEBUGGING_URL` (§3) lets the Skyvern *process* (holding `OPENAI_COMPATIBLE_API_KEY`) run on the control plane and drive a remote, key-less pod browser. **Caveat (§9):** this is global and process-wide — one Skyvern process binds to one remote CDP target, not many sessions at once. | ✅ native fit — our loop already separates "brain" (control plane, holds `VULTR_INFERENCE_API_KEY`) from "hands" (pod browser reached over CDP, no secrets), the same shape the one-shot capture already uses. | ✅ same as native for the primary path; the Skyvern fallback inherits the same 1:1 constraint. |
| **2. Pattern B loop** (plan → act → screenshot → **separate vision-model verification** → repeat → human approves final) | ⚠️ partial — one bundled LLM call does planning + grounding + implicit verification per step (§2, §11). An explicit external verify step means calling Skyvern with `max_steps=1` repeatedly and polling artifacts between calls (no per-step webhook, §5) — workable, but duplicates an LLM call Skyvern already made internally. | ✅ exact match — planner call → `page.diff` → a **separate** `qwen3.8-27b` call verifies the post-action screenshot against the step goal → repeat, with Jev's fast step check (§6b.3) as the cheap first pass ahead of that verifier. | ✅ inherits the native loop's exact match on the primary path; Skyvern only handles isolated hard-navigation sub-goals off the critical demo path. |
| **3. Resource limits & lifecycle** (time/memory caps every run; destroy the pod after each task) | ➖ orthogonal — neither framework manages `--memory`/`--cpus`/`--pids-limit`/wall-clock timeouts or teardown; that's our supervisor script regardless of which brain drives the browser (§8). Standing up Skyvern's 3-service Compose stack per escalation is heavier than the native path, which favors keeping it single-flight. | ➖ same orthogonality, lighter per-pod footprint since there's no Compose stack in the loop. | ➖ same; the heavier Skyvern footprint is paid only when actually escalated. |
| **4. Approve-before-submit gate** on irreversible/HIGH-risk actions | ⚠️ partial, coarse — the **Human Interaction** workflow block (§4, §6) is a genuine native hook, but it's a checkpoint we author into one workflow position, not a per-action interceptor consulted automatically before every click. | ✅ fine-grained — our loop chooses each tool call before executing it, so the gate is a property of the loop: anything Jev/code classifies above SAFE/LOW pauses for approval before that specific call (Jev role 2, §6b.2). | ✅ inherits the native loop's fine-grained gate on the primary path. |
| **5. Containment moment** (hostile/prompt-injection page absorbed safely, on screen) | ❌ weak fit — no documented pre-model injection screen (§4, §6); a hostile page's text reaches Skyvern's own planning call directly unless we pre-fetch and screen it ourselves, which makes Skyvern's own navigation redundant for that step. | ✅ strong fit — `page.snapshot` already captures page text before any LLM sees it, so Jev's observation gate (§6b.1) sits exactly there: flag → quarantine → still contained, visible in the run view as the step proceeds safely. | ✅ native loop's strength carries the demo beat; Skyvern never touches a hostile page live on screen. |

**Net:** the track's own Pattern B and its containment-moment beat are best served by our own loop, not by
Skyvern's bundled per-step call. This sharpens, without reversing, the periphery framing already committed in
`01-engine-definition.md`: build the native loop first; keep Skyvern config-ready as a single-concurrency S2
backend behind the same façade (§9).

---

## 1. Headline: can Skyvern run entirely on our Vultr Serverless Inference endpoint?

**Yes for a single model acting as both planner and action-chooser, with the vision role needing its own
config slot whose exact multi-model wiring is UNVERIFIED.** Skyvern's LLM layer is built on **LiteLLM**
underneath a config-registry abstraction: named `LLM_KEY` values (e.g. `ANTHROPIC_CLAUDE4_SONNET`) resolve to a
LiteLLM `model=` string plus metadata flags like `supports_vision=True`. Confirmed by a bug report where a
broken config-registry lookup let the raw `LLM_KEY` string leak straight into LiteLLM's `model=` parameter,
producing "You passed model=ANTHROPIC_CLAUDE4_SONNET"
([issue #2603](https://github.com/Skyvern-AI/skyvern/issues/2603)).

### 1.1 The exact env vars

Documented on the self-hosted LLM configuration page
(`https://www.skyvern.com/docs/developers/self-hosted/llm-configuration`, reached via the `docs.skyvern.com`
redirect), for "services implementing OpenAI's API format (LiteLLM, LocalAI, vLLM)" — precisely the shape of
`api.vultrinference.com/v1`:

```bash
ENABLE_OPENAI_COMPATIBLE=true
OPENAI_COMPATIBLE_MODEL_NAME=<vultr-model-id>       # e.g. qwen3.8-flash-next
OPENAI_COMPATIBLE_API_KEY=<VULTR_INFERENCE_API_KEY>
OPENAI_COMPATIBLE_API_BASE=https://api.vultrinference.com/v1
LLM_KEY=OPENAI_COMPATIBLE
```

Other roles, all documented on the same page:

| Var | Role | Default |
|---|---|---|
| `LLM_KEY` | Primary model: screenshot analysis + action planning | — |
| `SECONDARY_LLM_KEY` | Lighter/cheaper model for simpler sub-tasks; the docs say some SVG-related operations *mandate* it | falls back to `LLM_KEY` |
| `EXTRACTION_LLM_KEY` | Overrides the model used for data extraction | defaults to `LLM_KEY` |
| `SCRIPT_GENERATION_LLM_KEY` | Overrides the model used for code generation | defaults to `LLM_KEY` |

**The caveat:** only one `OPENAI_COMPATIBLE_*` block is documented — a single named "OPENAI_COMPATIBLE" provider
slot, not independently configurable primary/vision/secondary compatible endpoints. Whether `SECONDARY_LLM_KEY`
or `EXTRACTION_LLM_KEY` can point at a **second, different** Vultr model while `LLM_KEY` uses a first is
**UNVERIFIED** — it may require a second custom-provider registration, or a small LiteLLM proxy in front of
Vultr that dispatches by model name inside one logical endpoint. Test this in the smoke test (§1.5).

### 1.2 Does Skyvern require vision?

**No — it degrades to DOM-only, not hard failure.** The same config page: "Without vision, Skyvern relies on
DOM analysis instead of screenshot analysis, which may reduce accuracy on complex pages." A text-only Vultr
model can drive Skyvern in DOM/accessibility-tree mode; `supports_vision=True` is only needed for Skyvern's
screenshot-grounded mode.

### 1.3 Does Skyvern require JSON mode or `response_format`?

**UNVERIFIED** — the one open question we couldn't close from outside the source. No fetched page states
Skyvern's LLM calls hard-require `response_format`/JSON-schema mode. Routed through LiteLLM with per-model
capability flags, it's plausible Skyvern uses native structured output, tool-calling, or prompted JSON-in-text
parsing depending on what the LiteLLM model config declares — we could not read the literal prompting/parsing
source through public fetches to confirm which.

**Against Vultr specifically** (per `docs/reference/vultr.md` §2.10, already verified by our own team):
forced tool/function calls work on 6/6 tested Vultr models; `response_format` with `json_schema` (strict) is
confirmed only on `glm-5.3-flash` and `qwen3.8-flash-next`; `qwen3.8-27b` has verified real vision (`glm-5.3`'s
vision is a captioning proxy); reasoning can eat a small `max_completion_tokens` budget on its own
(`finish_reason: "length"`, no content); GLM leaks chain-of-thought into `content` when reasoning is disabled
the wrong way (use `reasoning_effort: "minimal"` on GLM instead).

**Net model recommendation**, until the JSON-mode question is settled by testing:

| Skyvern role | Vultr model | Why |
|---|---|---|
| `LLM_KEY` (primary planning) | `qwen3.8-flash-next` | Both tool-calling *and* `response_format json_schema` confirmed — covers Skyvern regardless of which mechanism it uses. Cheap ($0.10/$0.20). |
| `SECONDARY_LLM_KEY` (if a second compatible slot works) | `glm-5.3-flash` | Same dual confirmation, different model family. |
| Vision-capable config (if a separate `supports_vision=True` entry is needed) | `qwen3.8-27b` | Only Vultr model with verified real vision. `response_format` support unverified — test this pairing specifically if Skyvern's vision path also needs strict JSON. |
| Not recommended for vision | `glm-5.3` | Captioning-proxy vision per our own live test — wrong for exact transcription. |

### 1.4 Known issues with non-OpenAI / self-hosted models

- **[#2603](https://github.com/Skyvern-AI/skyvern/issues/2603)** — Claude Sonnet 4 self-hosted config-registry
  resolution bug (above); direct evidence the LiteLLM-config layer has had real self-hosted breakage.
- **[#2923](https://github.com/Skyvern-AI/skyvern/issues/2923)** — self-hosted Compose + OpenRouter returned
  HTTP 500, root-caused to the **container failing DNS resolution** to the external LLM host, i.e. a Docker
  networking/egress problem, closed "not planned." **This is the most relevant risk for us**: if Skyvern's
  container can't resolve/reach `api.vultrinference.com` through our egress-allowlist proxy, it fails the same
  way. **UNVERIFIED** whether Skyvern honors `HTTP_PROXY`/`HTTPS_PROXY` for its own outbound LLM calls (its
  documented `ENABLE_PROXY`/`HOSTED_PROXY_POOL` system is for the *browser's* traffic, a different concern).
- Two more self-hosted install issues surfaced by title only, not independently verified:
  [#2260](https://github.com/Skyvern-AI/skyvern/issues/2260), [#3837](https://github.com/Skyvern-AI/skyvern/issues/3837).

### 1.5 15-minute smoke test

```bash
git clone https://github.com/Skyvern-AI/skyvern && cd skyvern
cat >> .env <<'EOF'
ENABLE_OPENAI_COMPATIBLE=true
OPENAI_COMPATIBLE_MODEL_NAME=qwen3.8-flash-next
OPENAI_COMPATIBLE_API_KEY=$VULTR_INFERENCE_API_KEY
OPENAI_COMPATIBLE_API_BASE=https://api.vultrinference.com/v1
LLM_KEY=OPENAI_COMPATIBLE
EOF
docker compose up -d
docker compose logs -f skyvern    # watch the OpenAI-compatible provider register without error

curl -sS -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" -H "x-api-key: $SKYVERN_API_KEY" \
  -d '{"url":"https://example.com",
       "navigation_goal":"Read the page and report the exact text of the main heading. Do not click any links, do not submit any forms.",
       "data_extraction_goal":"Extract the page heading","extracted_information_schema":{"type":"object","properties":{"heading":{"type":"string"}},"required":["heading"]},
       "max_steps":3}'

curl -sS http://localhost:8000/api/v1/tasks/<task_id> -H "x-api-key: $SKYVERN_API_KEY" | jq '.status, .extracted_information'
```

**Falsifiable pass/fail:** pass = task reaches `completed` with a non-null `heading`, no LiteLLM
"unrecognized model" error, no `finish_reason: length` truncation. Fail signals: a DNS/connect error (→ #2923's
failure mode, fix egress first), a model-resolution error (→ re-check `OPENAI_COMPATIBLE_*` names against the
current compose file), or garbled `content` (→ suspect the reasoning/`max_tokens` interaction, try
`reasoning_effort: "minimal"` or raise `max_completion_tokens`).

---

## 2. Skyvern today: architecture, deployment, license, activity

**What it is.** An open-source browser-automation platform: "a swarm of agents to comprehend a website, and
plan and execute its actions" instead of brittle fixed selectors, exposed as a Playwright-compatible SDK plus a
no-code workflow builder ([github.com/Skyvern-AI/skyvern](https://github.com/Skyvern-AI/skyvern)). Primitives:
**Tasks** (single-request automation units), **Workflows** (multi-step chains of typed blocks — release notes
reference a "Task V3" iteration:
[newreleases.io/.../v1.0.52](https://newreleases.io/project/github/Skyvern-AI/skyvern/release/v1.0.52)),
**Browser Sessions** (persistent, reusable across tasks — see §9), browser livestreaming for debugging, form
filling, data extraction, file downloading, and auth features (2FA/TOTP, password-manager vaults).

**Deployment.** Confirmed from `docker-compose.yml` on `main`
([github.com/Skyvern-AI/skyvern/blob/main/docker-compose.yml](https://github.com/Skyvern-AI/skyvern/blob/main/docker-compose.yml)):
three services — `postgres` (`postgres:14-alpine`), `skyvern` (image `public.ecr.aws/skyvern/skyvern:latest`,
API on 8000, VNC/livestream on 6080, waits on Postgres health), `skyvern-ui` (image
`public.ecr.aws/skyvern/skyvern-ui:latest`, UI on 8080 + artifact API on 9090, waits on `skyvern` health).
Optional commented-out `vaultwarden`/`bitwarden-cli` services back the credential-vault feature. A `pip install`
path also exists, defaulting to SQLite unless Postgres is configured. **CPU/RAM sizing guidance: UNVERIFIED.**
Migrations run on first boot, documented as "1-2 minutes"
([Docker Setup](https://www.skyvern.com/docs/developers/self-hosted/docker)).

**License.** Confirmed **AGPL-3.0**
([LICENSE](https://raw.githubusercontent.com/Skyvern-AI/skyvern/main/LICENSE), stock GNU AGPLv3), with the
maintainers' own carve-out: "All of the core logic powering Skyvern is available in this open source repository
licensed under the AGPL-3.0 License, with the exception of anti-bot measures available in our managed cloud
offering" (repo README). **AGPL §13 ("Remote Network Interaction")** is the clause beyond plain GPL: if you
**modify** the covered program and make that modified version interactively available to users over a network,
you must offer those users the corresponding source — even without a literal binary "distribution." Two
scenarios:

- **(a) Run Skyvern unmodified as a separate service, called only over its HTTP/MCP API, never imported as a
  library.** §13 isn't triggered — we're a network client of an unmodified AGPL service. Exactly the boundary
  `01-engine-definition.md` already commits to ("Skyvern on the periphery": "it runs as a separate service in
  the sandbox, called over its API, and is never imported as a library"). Holds as long as no Skyvern source is
  copied/linked into the Apache-2.0 codebase.
- **(b) Modify Skyvern's own source and expose that modified instance over a network** (even just to our own
  sandbox pod) — §13 requires offering the corresponding source of the modified version to whoever's traffic
  reaches it. A materially bigger obligation than plain GPL. **Summary of license text, not legal advice** — and
  avoid path (b) regardless, since guardrails belong in the orchestrator (§4, §6b), not a Skyvern fork.

**Activity.** Latest tag per the GitHub Atom feed: **v1.0.54, published 2026-09-22T22:57:33Z**, bundling ~100+
merged PRs in that release ([releases.atom](https://github.com/Skyvern-AI/skyvern/releases.atom)). Repo stats:
**23.1k stars, 2.2k forks, 42 open issues.** Actively maintained (weekly-ish release cadence, high PR density).

---

## 3. Sandboxing: can Skyvern run *as the brain only*, inside our pod?

**Yes for the browser-launch question — `BROWSER_TYPE=cdp-connect` is the intended mechanism — but it is a
global, process-wide setting, not a per-session one. See §9 for why that rules out a multi-session Skyvern
microservice.** Confirmed on the self-hosted browser-configuration page
([www.skyvern.com/docs/developers/self-hosted/browser](https://www.skyvern.com/docs/developers/self-hosted/browser)):

| `BROWSER_TYPE` | Behavior |
|---|---|
| `chromium-headful` (default) | Skyvern launches its own browser; needs Xvfb in its container |
| `chromium-headless` | Same, headless |
| `cdp-connect` | Attaches to an **already-running** Chrome/Chromium via `BROWSER_REMOTE_DEBUGGING_URL` (e.g. `http://host.docker.internal:9222/`), plus `BROWSER_CDP_CONNECT_TIMEOUT_MS` |

Our gVisor pod can own and launch the real browser — wired to our egress-allowlist proxy — and Skyvern's own
container just drives it remotely over CDP; no browser process needs to run inside Skyvern's container. Docs
warn an exposed CDP endpoint gives whoever holds it full control of the browser and should stay on a trusted
network — consistent with keeping it reachable only inside our sandbox's private network. One rough edge:
**[issue #3476](https://github.com/Skyvern-AI/skyvern/issues/3476)** reports `CDP_CONNECTION_URL` being ignored
on Windows specifically — not expected on Linux/VX1, but worth a quick check against v1.0.54.

**Proxying Skyvern's own egress (LLM calls, not the browser's traffic): UNVERIFIED.** No fetched page documents
a standard `HTTP_PROXY`/`HTTPS_PROXY` var for Skyvern's own outbound calls. Skyvern's `ENABLE_PROXY`/
`HOSTED_PROXY_POOL` system routes the **browser's** traffic for anti-detection/geo purposes — a different
concern. Using CDP-connect sidesteps needing Skyvern to honor our proxy for browsing at all, since our own
browser process talks to the allowlist proxy; Skyvern's container still needs its own path to
`api.vultrinference.com`, which must be allowlisted regardless (exactly what issue #2923 warns can silently
break).

**Metadata IP blocking / restricted internet:** not documented by Skyvern — expected, since it's a generic
container-hardening concern. The same DOCKER-USER iptables rules used elsewhere apply unchanged to a Skyvern
container. **UNVERIFIED**, worth a no-egress-except-allowlist smoke test on the Skyvern container specifically.

---

## 4. Read-only guardrails

**Goal and termination fields exist, but they're prompt-level, not a hard action gate.** `navigation_goal` is
free text ("tell Skyvern when your goal is achieved... 'when you see xxxxx in the page, consider the goal is
achieved'" — [Running Tasks](https://docs.skyvern.com/running-tasks/run-tasks)); `max_steps` (default 50) and a
custom `error_code_mapping` are run-task parameters
([Error Handling](https://www.skyvern.com/docs/developers/going-to-production/error-handling)); workflow
**Validation** blocks support a `terminate_criterion` to stop early on a detected error condition.

**No documented domain allowlist inside Skyvern itself.** A surfaced `--allowed-domains`-style flag traced to an
unrelated product, not Skyvern; Skyvern's own security write-up
([Best Practices](https://www.skyvern.com/blog/browser-automation-security-best-practices)) doesn't mention
domain allowlisting. **Domain restriction must be enforced entirely by our egress-allowlist proxy.**

**Restricting Skyvern to search/filter/paginate only: control by omission, not a hard gate.** No fine-grained
"disable this action type" switch is documented. Practical controls: the `navigation_goal` prompt stating the
allowed action set, never configuring a **Login** block or credential vault, and our own post-hoc audit of the
action-log artifact (§5) against the technical definition document's allowed action set — enforced at the
**orchestrator** level, the same way the toolkit already grants tools per phase and per technical definition
document.

**Login / credential vaults / 2FA are opt-in features we simply never invoke.** Skyvern has a distinct **Login**
block and Bitwarden/1Password/Azure Key Vault integrations, plus TOTP/2FA auto-fill
([Authentication](https://www.skyvern.com/blog/how-skyvern-handles-authentication/)). None activates unless we
add a Login block or supply vault credentials.

**CAPTCHA solving is off by construction for self-hosted — favorable for us.** "Automatic CAPTCHA solving is
not available for self-hosted deployments"
([CAPTCHA & Bot Bypass](https://www.skyvern.com/docs/developers/features/captcha-and-bot-bypass)). Self-hosted,
hitting a captcha pauses ~30s for manual solving, then continues if nobody solves it — it **stalls, not
bypasses**, satisfying "captcha is a hard stop" automatically as long as we stay self-hosted.

---

## 5. Data out: extraction, artifacts, and the evidence contract

- **`data_extraction_goal`** + **`extracted_information_schema`** (JSON Schema) are passed at task creation to force structured output. **Verified on v1.0.54 by the team:** `data_extraction_schema` is silently ignored.
- **Artifacts per run** ([Using Artifacts](https://www.skyvern.com/docs/developers/debugging/using-artifacts)):
  WebM recording, screenshots (`screenshot_final`, `screenshot_action`, `screenshot_llm`), LLM prompts/responses,
  parsed-action logs, DOM trees (JSON + text), element/selector maps, HTML dumps, execution logs, browser
  console output, full HAR files, Playwright traces. Local filesystem for self-hosted (`signed_url` is `null`).
  Maps cleanly onto our four-field trace: **observed** = DOM tree/screenshot/HAR, **requested** =
  `navigation_goal`/`data_extraction_goal`+`extracted_information_schema`, **executed** = the parsed action log, **evaluated** = our own
  SHACL/critic pass.
- **Webhooks fire only at run completion**, not per step — payload includes `run_id`, `status`, `output`,
  `recording_url`, `screenshot_urls`, `failure_reason`, `step_count`
  ([Webhooks](https://www.skyvern.com/docs/developers/going-to-production/webhooks)). **No native per-step
  hook** — mid-task visibility needs polling or reading artifacts live.
- **Live view is documented on the Cloud product**, with a "Take Control" mode
  ([Monitor a Run](https://www.skyvern.com/docs/cloud/getting-started/monitor-a-run)). Self-hosted live view has
  a confirmed limitation (§9): a shared X display across sessions, no per-session isolation
  ([issue #4392](https://github.com/Skyvern-AI/skyvern/issues/4392)). **Budget for our own noVNC screencast of
  the pod's CDP-connected browser rather than Skyvern's UI** for the live-view demo story.

---

## 6. Extension points for Jev (per-role, inside Skyvern vs. our orchestrator)

Confirmed workflow block types
([Workflow Blocks](https://www.skyvern.com/docs/workflows/workflow-blocks-details)): Browser Task, Browser
Action, Extraction, Login, Go to URL, Print Page, Web Search, Text Prompt, File Parser, Loop, While Loop,
Conditional, **AI Validation**, Code, Wait, File Download, Cloud Storage Upload, Send Email, HTTP Request,
**Human Interaction** (pause for human review, approve/reject, timeout, email notify).

| # | Jev role | Extension point in Skyvern | Extension point in our own loop |
|---|---|---|---|
| 1 | Prompt-injection pre-filter | None mid-task. Must run **before** the goal is handed to Skyvern, or as a `Text Prompt`/`Code` block upstream of a `Browser Task`. | Native — reads page text before the planning LLM, a genuine pre-model hook. |
| 2 | Termination predicate ("did this step achieve its goal?") | Partial — **AI Validation**/`terminate_criterion`, but Skyvern evaluates the condition with its own LLM; a Jev answer has to be fed in via a preceding Code block, not called directly. | Native — our loop's step boundary is exactly this question. |
| 3 | Page-type classification | None — runs against artifacts pulled after each step/run, since webhooks are end-of-run only. | Native — called on each `page.snapshot` result directly. |
| 4 | "Same entity?" dedup | None — a data-layer decision on extracted output. | Orchestrator-side either way. |
| 5 | "Is this action SAFE/LOW?" guard | Coarse — **Human Interaction** is a real approval-gate, but workflow-level, not a per-action interceptor. | Native and fine-grained — our loop chooses which tool calls are legal per step. |
| 6 | Escalation decisions | `step_count`/`failure_reason`/`status` in the completion webhook — after-the-fact only. | Same signals, available live per step. |

**Net:** rows 1, 2 and 5 are strictly tighter in a native loop, because Skyvern's hooks are workflow-level and
its telemetry is end-of-run. This is the architectural argument for §6b's design living inside our own MCP
façade rather than inside Skyvern.

## 6b. Jev in the service: six concrete integration points

Design rules carried through unchanged from `docs/reference/jev.md`: **Vultr stays the final word on gold
values and verdicts**; every Jev answer is logged with `generated_by.backend = "jev"`; the injection gate is
**one-way** (a flag adds scrutiny, "benign" never removes a downstream defense). These six points are wired into
the MCP façade's own step loop (§9), not into Skyvern — they apply the same way whichever backend is driving the
browser underneath, but only the native backend gives them a genuine per-step home (§6).

| # | Point in the loop | Jev call | Wiring | One-way / final-word rule |
|---|---|---|---|---|
| **1. Observation gate** | Every `session.observe` (post-navigation/post-action page-text capture) | `choice` — "Does `page_text` contain instructions aimed at an AI agent reading it?" `{injection, benign}`, following `jev.md`'s `INJECTION_Q` pattern | Runs immediately after `page.snapshot`, before the text reaches any LLM (planner or verifier). A flag marks the observation `injection_suspected=true`, quarantines it, and routes the raw text to `nemotron-3.5-content-safety` on Vultr for a second opinion; the step's `evaluated` field records both verdicts. | **Benign from Jev never skips the downstream Vultr safety check** — per `jev.md` G5, it only adds scrutiny, never removes it. This is the containment-moment beat: a hostile page gets flagged, quarantined, and the loop stays contained on screen. |
| **2. Action guard** | Before every `session.act` (click/type/navigate/download) | `choice` over `{SAFE, LOW, HIGH}` — "What is the risk tier of `action_description` against the current page?" | Runs after the planner proposes the next action, before execution. SAFE/LOW proceed automatically (logged, `backend=jev`). HIGH triggers the approve-before-submit gate: the action queues, an approver is notified via the app's approver channel (`docs/reference/netbird.md`'s reverse-proxy role gating), and execution blocks until approved or times out (fail-safe: reject on timeout). | A deterministic **code** floor sits underneath and can only raise the tier — e.g. any action targeting a submit control outside an allowed search/filter form is always HIGH regardless of what Jev says. Jev cannot lower a code-forced HIGH. |
| **3. Fast step check** | After every `session.act`, before deciding whether to spend a vision-verification call | `noul` — "Does `diff_summary` (a text description of the before/after `page.diff`) indicate progress toward `step_goal`?" | High-confidence "yes" skips the Vultr vision verifier for that step (`first_pass=jev`, proceed to the next planning call). Uncertain (below the confidence threshold) or "no" escalates to the authoritative `qwen3.8-27b` vision verifier — the literal "cheap first pass, with Vultr confirming" design. | Vultr is still the authority whenever Jev is uncertain or negative; a confident-positive skip only affects step-progress pacing, never a gold value — a wrong skip surfaces later as a failed termination check or a SHACL validation failure downstream, not as corrupted gold. |
| **4. Page-type classifier** | On arrival at a new (URL-template, DOM-skeleton-hash) node not yet seen this run | `choice` over the ontology-derived page-type list (built from `graph.cluster`'s known types plus an "unknown/new" option) | Feeds `graph.map`'s confidence-scored proposal. Low-confidence or "unknown" escalates to a Vultr call for a fresh type proposal, which can become an `ontology.recommend` if genuinely new. | Vultr/human confirms any "unknown" or ontology-changing result; Jev only pre-sorts the common, already-known-type case. |
| **5. Escalation policy** | Computed from step-log features (yield so far, repeated `layout_drift`/`stale_binding`, current mode) | `choice` over `{stay_D1, escalate_S1, escalate_S2, stop}` | Logged as `generated_by.backend=jev`; pre-sorts the common `stay_D1`/`escalate_S1` cases cheaply, ahead of the toolkit's existing rule that "on any failure, a Vultr model reads the step log... and picks one of retry, repair, escalate, or reject." | Jev's answer is **never allowed to choose `stop` unilaterally** — a hard stop is always Vultr- or code-confirmed. |
| **6. Entity screen** | During dedup, before writing an `entity.lookup` resolution | `choice` `{same, different}` + `noul` diagnostics for name/address match, exactly `jev.md` §6b's worked example | Tax IDs are compared in code and passed in as a named bucket, never asked of Jev (its documented weakness at exact-ID comparison). | Vultr always confirms afterward — unchanged from the existing documented pattern; this just places it inside the MCP façade's dedup path. |

**Expected call volume and cost per session (illustrative estimate, not measured — mark UNVERIFIED for real
tokens-per-call):** a typical S1 session of 5-10 steps generates roughly: 5-10 observation-gate calls, 5-10
action-guard calls, 5-10 fast-step-check calls, 1-3 page-type-classifier calls (only on new nodes), 0-2
escalation-policy calls, 0-5 entity-screen calls — **≈20-35 Jev calls/session**. At Jev's documented $0.042/1M
input tokens and roughly 100-300 input tokens per call (`jev.md` §8, §6), 30 calls/session ≈ 9,000 input tokens
≈ **$0.0004/session** — trivial next to a single Vultr vision call. The savings case is the fast step check
(role 3): every step it resolves at high confidence is one fewer `qwen3.8-27b` vision call (image tokens make
vision calls markedly more expensive than a Jev text call, though the exact per-image token cost on Vultr is
**UNVERIFIED**).

**Run-view metric** (feeds the track table's "Dashboards: execution loop, retries, screenshot trails — add
vision verdicts + retries" row): log per session, alongside the existing `jobs.jsonl` mode/retry/escalation
fields —

```json
{"jev_observations_screened": 8, "jev_flagged": 1, "vision_calls_avoided": 5, "vision_calls_made": 3,
 "estimated_usd_saved": 0.0031, "backend_breakdown": {"jev": 24, "vultr": 9}}
```

surfaced on the same `/run` view already planned for step streams, modes, escalations and repairs.

---

## 7. Alternatives: Playwright MCP and browser-use

| | Skyvern | Playwright MCP | browser-use |
|---|---|---|---|
| **What it is** | Full agent platform: API + workers + UI + Postgres | Pure tool server — browser actions as MCP tools; no LLM awareness of its own | Python agent library wrapping Playwright/Patchright |
| **License** | AGPL-3.0 (cloud-only anti-bot carve-out) | **Apache-2.0** ([LICENSE](https://github.com/microsoft/playwright-mcp/blob/main/LICENSE)) | **MIT** ([LICENSE](https://github.com/browser-use/browser-use/blob/main/LICENSE)); no license-change controversy found |
| **Setup time on a fresh VX1** | 1.5-3h (Compose bring-up, migrations, first-task debugging) before any gVisor/CDP work | 15-30 min bring-up; the real cost is our own loop | 30-45 min (`pip install` + Playwright browser install) |
| **Vultr/OpenAI-compatible fit** | Documented `ENABLE_OPENAI_COMPATIBLE` block (§1), with the caveats there | N/A to itself — no LLM of its own, so our already-verified Vultr tool-calling loop drives it directly. Cleanest compatibility story, no second config layer. | Documents `ChatOpenAI(model=..., base_url=...)` "for any provider with an OpenAI-compatible endpoint" ([Supported Models](https://docs.browser-use.com/supported-models)) |
| **Sandbox fit / concurrency** | `BROWSER_TYPE=cdp-connect` is documented (§3), but **global and single-target per process** (§9) — one self-hosted Skyvern instance drives exactly one remote browser at a time. | `--cdp-endpoint=<url>` is the **primary** supported path ([issue #1319](https://github.com/microsoft/playwright-mcp/issues/1319)), and since it's just a tool server, **N of our own loop instances each talk to their own pod** with no shared-state ceiling — this is what makes it the only backend that supports concurrent multi-pod sessions. | CDP attach is discussed as a community pattern ([discussion #2798](https://github.com/browser-use/browser-use/discussions/2798)), not a first-class documented flag — **UNVERIFIED** how cleanly it multiplexes. |
| **Read-only control** | Prompt-level only (§4) | Tightest — our loop decides which MCP tool calls are legal per step | Similarly prompt/goal-driven |
| **Evidence quality** | Richest built-in artifact set (§5), but end-of-run only | Accessibility-tree snapshots as the primary output, plus our own `page.screenshot()` — closest structural match to `emit.observation`'s (URL, selector, screenshot crop) shape | Action histories + screenshots, less first-class artifact tooling documented |
| **Demo value** | Built-in VNC/UI, but self-hosted live view has a documented shared-display limitation (§5, §9) | We build the visualization ourselves via `netbird expose` on the pod's CDP/VNC port | Similar to Playwright MCP; less documented self-hosted live-view polish |

**Conclusion:** Playwright MCP (or our own direct Playwright driving, which is what the current one-shot capture
already uses) is the only one of the three that cleanly supports **N concurrent, independently live-viewable
pods** — which the "browser-agent MCP service" design in §9 needs for its primary path. Skyvern is real,
license-clean when called over its API, and a legitimate single-concurrency S2 backend, but not a multi-session
microservice on its own.

---

## 8. Feasibility for this hackathon (~21 hours remaining at research time)

| Path | Est. hours | Key blockers |
|---|---|---|
| **(a) Skyvern self-hosted, wired to Vultr, as a single-flight S2 backend** | **4-7h** | Compose bring-up + migrations (1-2h); testing `OPENAI_COMPATIBLE_*` wiring, including whether a second model slot works for vision (0-2h, §1.1 genuinely unverified until tested); CDP-connect wiring into one pod (1-2h); first successful read-only task + evidence mapping (1h). |
| **(b) MCP façade + native Playwright backend (primary, multi-session)** | **3-5h** | MCP server/tool-schema bring-up (~0.5h, can reuse the existing one-shot capture's browser session); a minimal plan/act/observe loop translating tool calls into Vultr `tools=[...]` (2-3h — verified tool-calling, not a cold start); read-only tool allowlisting is ours to write, not discover (~0.5-1h). |
| **(c) Hybrid: façade with both backends, native primary + Skyvern S2 fallback** | **6-9h** | = (b) fully working (3-5h) + a bounded, single-instance Skyvern deployment wired only for the fallback path (+3-4h on top of (a)'s harder parts, since the fallback trigger/interface itself is cheap once (b) exists). Highest total hours, lowest risk concentration: if Skyvern stalls, (b) alone is already a complete, demoable, track-compliant executor (§0). |

**Top risks, in order:** (1) vision model quality — only `qwen3.8-27b` is verified real vision, constraining
every screenshot-grounded path (Skyvern's default mode, browser-use's vision mode, and our own S2 verifier) to
that one model; (2) Skyvern's per-step prompt size/cost — screenshot + DOM/accessibility tree + goal every step
by default, with an "economy accessibility tree" no-screenshot mode as a documented cost-cutting alternative
([HTML vs JSON cost](https://www.skyvern.com/blog/html-vs-json-llm-tokens-cost-reduction-success-rate/)); (3)
gVisor + Chromium compatibility — no Skyvern-specific reports found, general risk mitigated because our setup is
not rootless Docker, but Chromium-under-gVisor performance remains **UNVERIFIED** regardless of which framework
drives it (`vultr.md` §7); (4) AGPL exposure — mitigated only by calling Skyvern's unmodified API from a
separate service, never importing it as a library.

---

## 9. Architecture: the browser-agent MCP service

### 9.1 The design as proposed, and what verification found

The proposed design: a thin MCP façade the engine talks to (`session.open(tdd, allowed_domains, limits) →
session_id + live_view_url`, `session.act`/`goal`, `session.observe`, `session.close`), backed by Skyvern
running unmodified on the control plane (holding the Vultr key), with each session mapped to one gVisor browser
pod that Skyvern drives remotely over CDP — and the same MCP interface optionally backed by our own Playwright
loop instead, as pluggable backends.

**Verified against Skyvern's actual APIs, this holds only as a 1:1 pairing, not a multiplexed microservice:**

- **(a) Native MCP server: real, but thin.** Skyvern ships an official MCP integration at `integrations/mcp/`
  ([README](https://github.com/Skyvern-AI/skyvern/blob/main/integrations/mcp/README.md),
  [announcement](https://www.skyvern.com/blog/skyvern-mcp-server-let-agents-control-your-browser/)), 35 tools
  across 6 categories, configured via `SKYVERN_BASE_URL` + `SKYVERN_API_KEY`. The remote `/mcp` endpoint is
  **stateless**: call `skyvern_browser_session_create` first, then pass `browser_session_id` on every browser
  tool call — i.e. the MCP tools are thin wrappers over the REST Browser Sessions API below, not a separate
  mechanism.
- **(b) Browser Session API: real, but the CDP direction is backwards from what this design needs.** Confirmed
  via [Browser Sessions intro](https://www.skyvern.com/docs/browser-sessions/introduction) and the
  [create-session API reference](https://www.skyvern.com/docs/api-reference/browser-sessions/create-a-session):
  sessions are first-class and persistent (`browser_session_id` starting `pbs_`, reusable via
  `browser_profile_id`, timeout 5-240 min). `CreateBrowserSessionRequest.browser_type` is an enum — `msedge` |
  `chrome` | `stealth-chromium` — **with no `cdp-connect` option**, and the response's `browser_address` (a CDP
  URL) is an **output**, not an input. **Skyvern launches and owns the browser per session and hands you *its*
  CDP endpoint** — the opposite of "give Skyvern our pod's CDP URL to drive." The self-hosted global
  `BROWSER_TYPE=cdp-connect`/`BROWSER_REMOTE_DEBUGGING_URL` env vars (§3) are a **separate, process-wide**
  config path, not exposed anywhere in `CreateBrowserSessionRequest` — i.e. per-process, not per-session.
- **(c) Concurrency: ruled out by a closed, documented issue.** No worker-pool/concurrency-limit docs exist for
  the Browser Sessions API itself, but
  **[issue #4392](https://github.com/Skyvern-AI/skyvern/issues/4392)** ("there's no way to view individual
  browser sessions" running multiple workflows in parallel) documents that self-hosted sessions **share a
  single display (`:99`)** and a single VNC bind-mount between the API/UI containers — closed "not planned."
  Combined with (b), **"one Skyvern service, many concurrent sessions, each bound to a different remote pod" is
  not architecturally supported.** The only way to get N concurrent, independently-driven, independently
  live-viewable pods with Skyvern as the brain is **N separate self-hosted Skyvern processes**, each with its
  own global `cdp-connect` config pointed at one pod — a materially heavier resource story than the design
  assumes, and not worth it for a single hackathon fallback path.
- **(d) CDP over a network link, and downloads: UNVERIFIED, real risk.** No documentation discusses running
  Skyvern's CDP connection across a WireGuard/VPC boundary (latency, reconnection behavior). Nothing documents
  how downloaded files are retrieved when the driven browser isn't co-located with the Skyvern process — assume
  Skyvern's download-artifact channel **does not** surface files reliably in `cdp-connect` mode; plan to pull
  files directly from the pod via our own `file.fetch` instead.
- **(e) Live view: Skyvern's own VNC doesn't apply in `cdp-connect` mode.** The self-hosted VNC bridge streams
  Skyvern's *own launched* browser's shared X display (per (c)); `stream_transport` in the session response can
  be `vnc` or `cdp`, suggesting a CDP-screencast-based stream is architecturally possible, but this isn't
  documented for a session bound to a caller-supplied CDP target (which, per (b), isn't a supported input
  anyway). **Don't rely on Skyvern's UI/VNC for live view in this mode** — substitute our own noVNC layer on the
  pod's browser (already the plan per §5 and `netbird.md`).

### 9.2 Recommended architecture

A thin MCP façade **we write**, with two pluggable backends behind one interface
(`session.open`/`act`/`observe`/`close`):

- **`NativeBackend` (primary, multi-session).** Our own Playwright driver, one instance per gVisor pod on VM #2.
  Each session is just our own process talking to its own pod — no shared state, so it supports N concurrent
  sessions (bounded by sandbox VM CPU/RAM and NetBird's 10-active-expose-sessions-per-peer cap, which
  conveniently matches the pod port range `6080-6089` already reserved in `netbird.md`). Live view: our own
  noVNC on the pod, exposed per pod via `netbird expose` (`netbird.md` §6.4's pattern, one PIN per pod).
- **`SkyvernBackend` (fallback, single-concurrency only).** One dedicated self-hosted Skyvern deployment
  (§1.5's Compose stack), configured with the **global** `BROWSER_TYPE=cdp-connect` +
  `BROWSER_REMOTE_DEBUGGING_URL` env vars (§3, §10) pointed at exactly one pod's CDP port at a time — **not**
  Skyvern's own Browser Session API/MCP tools, which assume Skyvern owns the browser (§9.1(b)) and would be the
  wrong direction for "Skyvern drives our pod." The façade's `session.open` for this backend queues/blocks if a
  Skyvern-backed session is already in flight. Live view for this backend reuses the same noVNC-on-the-pod
  mechanism as `NativeBackend` (§9.1(e)) — it's the pod's browser being viewed either way, not Skyvern's UI.

```
                         ┌───────────────────────────────────────────────────┐
                         │              VX1 #1 — control plane                │
                         │  FastAPI planner · MCP façade                      │
                         │    session.open/act/observe/close                  │
                         │  ┌───────────────┐   ┌─────────────────────────┐  │
                         │  │ NativeBackend  │   │ SkyvernBackend (1 slot   │  │
                         │  │ (our loop, N   │   │  in flight; Compose      │  │
                         │  │  sessions)     │   │  stack + Postgres)       │  │
                         │  └───────┬────────┘   └────────────┬────────────┘  │
                         │  DecisionInterface: Jev → Vultr chain (§6b)         │
                         └──────────┼──────────────────────────┼──────────────┘
                                    │ CDP (pod-internal net)    │ CDP (single pod at a time)
                                    ▼                           ▼
                         ┌───────────────────────────────────────────────────┐
                         │      VX1 #2 — sandbox host (gVisor `runsc`)         │
                         │  pod-1 .. pod-N: real browser, zero secrets,       │
                         │  memory/cpu/pids/timeout caps, noVNC + netbird     │
                         │  expose, destroyed on session.close                │
                         │  egress-allowlist proxy → allowlisted sites only   │
                         └───────────────────────────────────────────────────┘
                                    │                           │
                                    ▼                           ▼
                    api.vultrinference.com/v1          api.typesafe.ai/v1/systemone
                    (planner, verifier, critics)        (Jev — §6b's six points,
                                                          orchestrator-side only)
```

Every `emit.observation` call, from either backend, carries the same evidence shape (URL, selector, screenshot
crop) and is SHACL-validated before reaching silver — Skyvern's richer end-of-run artifacts become additional
evidence attachments when that backend is used, not a different pipeline.

---

## 10. Config snippet: Skyvern on Vultr (env var names only, no secrets)

For the `SkyvernBackend` specifically (§9.2) — a single dedicated instance, not a shared multi-session service:

```bash
# --- Skyvern LLM configuration: point at Vultr Serverless Inference ---
ENABLE_OPENAI_COMPATIBLE=true
OPENAI_COMPATIBLE_MODEL_NAME=qwen3.8-flash-next        # confirmed: tool calling + response_format json_schema
OPENAI_COMPATIBLE_API_KEY=${VULTR_INFERENCE_API_KEY}
OPENAI_COMPATIBLE_API_BASE=https://api.vultrinference.com/v1
LLM_KEY=OPENAI_COMPATIBLE

# Secondary/extraction/script-gen roles: confirm in the smoke test (§1.5) whether these can point at a
# DIFFERENT Vultr model (e.g. glm-5.3-flash or qwen3.8-27b for vision) via a second slot, or must share LLM_KEY's.
SECONDARY_LLM_KEY=OPENAI_COMPATIBLE          # UNVERIFIED: same model as LLM_KEY unless a second slot exists
EXTRACTION_LLM_KEY=OPENAI_COMPATIBLE         # defaults to LLM_KEY if unset
SCRIPT_GENERATION_LLM_KEY=OPENAI_COMPATIBLE  # defaults to LLM_KEY if unset

# --- Browser: attach to our own CDP-exposed Chrome inside ONE sandbox pod (global, one process = one target) ---
BROWSER_TYPE=cdp-connect
BROWSER_REMOTE_DEBUGGING_URL=http://<pod-internal-host>:9222/
BROWSER_CDP_CONNECT_TIMEOUT_MS=10000

# --- Explicitly NOT configured (keeps login/2FA/captcha-bypass features dormant) ---
# no Bitwarden/1Password/Azure Key Vault credential vars
# no TOTP secret keys
# no ENABLE_PROXY / HOSTED_PROXY_POOL (Skyvern's anti-detection proxy, not our egress allowlist)
```

---

## 11. Proposed Codex brief

Written for our engine agent. Fits **after** the in-flight "genericity" refactor. Slices are for the MCP façade
+ pluggable-backends design (§9.2), native backend first (the only one that supports concurrent sessions, and
the one the track's Pattern B and containment-moment beats favor per §0). Stopping after any slice leaves a
working, demoable state; each slice has one falsifiable check.

**Slice 0 — Smoke-test Skyvern-on-Vultr in isolation (optional, 30 min, only if hours remain after Slice 4).**
*Do:* §1.5's smoke test on a scratch box.
*Check:* task reaches `completed` with a non-null extracted field, no LiteLLM resolution error, no truncation.
Fail inside 30 min → stop and don't revisit Skyvern until after Slice 4 ships.

**Slice 1 — MCP façade skeleton + `NativeBackend`, hardcoded script, no LLM yet.**
*Do:* define the façade interface (`session.open`/`act`/`observe`/`close`); implement `NativeBackend` over our
existing one-shot Playwright capture's browser session (or a fresh `@playwright/mcp` bring-up); run a hardcoded
3-step script (navigate, snapshot, extract).
*Check:* the loop completes the hardcoded run against a real public page and produces a `page.snapshot` +
screenshot pair per step, with no LLM call yet.

**Slice 2 — Vultr tool-calling drives `session.act`, with the action guard (Jev role 2).**
*Do:* translate the tool schema into Vultr `tools=[...]`; drive next-action choice through `qwen3.8-flash-next`
with `tool_choice: "auto"` and a small allowed-tool set per step (navigate/search/filter/paginate only). Wire
Jev's action guard (§6b.2) before every `session.act` call, with the code-level HIGH-risk floor underneath it.
*Check:* given an open-ended goal, the loop reaches a correct answer within `max_steps`, using only allowlisted
tools; a scripted HIGH-risk action (e.g. a disguised submit button) pauses for approval instead of executing.

**Slice 3 — Observation gate + fast step check (Jev roles 1 and 3) + evidence into `emit.observation`.**
*Do:* wire the injection pre-filter (§6b.1, one-way per `jev.md` G5) on every `session.observe`, and the fast
step check (§6b.3) between each action and the `qwen3.8-27b` vision verifier. Emit each step through
`emit.observation` with full evidence and a mode tag (`S1`).
*Check:* the case journal shows each step's four fields (observed, requested, executed, evaluated) plus mode;
feeding one adversarial page fixture flags it, quarantines it, and still lets the run-view metric (§6b) show
"1 flagged / N screened" without the run crashing — the containment-moment check.

**Slice 4 — Sandbox hardening: gVisor pod + egress proxy + resource caps + lifecycle.**
*Do:* move the browser session from Slice 1 into a gVisor `runsc` pod on VM #2, behind the egress-allowlist
proxy, with `--memory`/`--cpus`/`--pids-limit` and a wall-clock timeout on the pod's supervisor script; destroy
the pod on `session.close`.
*Check:* the isolation probe shows BLOCKED for a non-allowlisted domain reached from inside the pod; a scripted
runaway action is killed by the timeout/memory cap with the host untouched (the track's "containment moment"
for Pattern A's equivalent); `jobs.jsonl` records the caps and the teardown event.

**Slice 5 — `SkyvernBackend` (config + code path, deploy optional, single-concurrency).**
*Do:* implement `SkyvernBackend` per §9.2/§10 — one Skyvern deployment, `BROWSER_TYPE=cdp-connect` pointed at
the *same* pod browser from Slice 4, called over its Task API (not its own Browser Session API/MCP tools, which
assume Skyvern owns the browser). Wire the escalation policy (Jev role 5, §6b.5) to trigger this backend when
`NativeBackend`'s termination predicate fails twice or a technical definition document pre-flags "hard
navigation."
*Check:* with `SkyvernBackend` **not deployed**, escalation degrades gracefully ("S2 unavailable, staying at S1
with a flagged failure") rather than crashing. If Slice 0 passed and hours remain, deploy it and check one real
escalation produces a candidate observation through the same evidence contract as Slice 3.

**Slice 6 (stretch) — Live-view URL and the demo's approve-before-submit gate in the app.**
*Do:* `netbird expose` each pod's noVNC port per `netbird.md` §6, tied to the pod's lifetime, surfaced in
`status.json.live_view_url`; wire Jev role 2's HIGH-risk pause (Slice 2) to the app's approver channel so a
judge can watch a real approve/reject happen live.
*Check:* opening the URL mid-run shows the live browser; the URL stops resolving within seconds of teardown,
with a "Peer unexposed service" audit event; a HIGH-risk action pauses in the run view and only proceeds after
an explicit approve click, visible on screen.

Each slice's check is something a judge can run and get a pass/fail answer from without reading source —
matching the track's own framing ("show me the instance," "a real result," "BLOCKED").
