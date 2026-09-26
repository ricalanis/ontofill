# Vultr reference: provisioning and Serverless Inference

Snapshot: 2026-09-26. This is written for the two coding agents (engine in Python, FastAPI app) and
for the humans who hold the credentials. Everything below comes from public sources, cited inline.
**UNVERIFIED** means no fetched public page confirmed the claim; test it before relying on it.
Secrets always come from env vars: `VULTR_API_KEY` (account key), `VULTR_INFERENCE_API_KEY`
(inference subscription key), `S3_ACCESS_KEY` / `S3_SECRET_KEY`, `NETBIRD_SETUP_KEY`.

For the organizers' framing (isolation ladder, five proof checkpoints, deck commands, NetBird Cloud vs
Marketplace), read [event-decks.md](event-decks.md). This file doesn't repeat it. §0b says where the public
docs confirm or contradict the decks.

Main source files:

- [govultr] is the official Go client, and its source is the most reliable map of the account API: https://github.com/vultr/govultr (checked at commit f0f2cd9, 2026-09-01).
- [INF] is the inference OpenAPI spec (v1.1.3), embedded in the Redoc page at https://api.vultrinference.com/.
- [MODELS] is `GET https://api.vultrinference.com/v1/models`. It is public and was fetched 2026-09-26.
- `www.vultr.com/api/` returns 403 to scripted fetchers, so account-API claims cite docs.vultr.com or govultr instead.

---

## 0. Gotchas we hit (verified by the team, 2026-09-26)

| # | Finding | Consequence |
|---|---------|-------------|
| G1 | `GET https://api.vultrinference.com/v1/models` → 200 with no auth. `/v1/chat/models` (printed in the event guide) → **404**. | Use `/v1/models`. [INF] says: "The public `/v1/models` route does not [require a token]". |
| G2 | Our key is an **account API key**, not an inference key. `POST /v1/chat/completions` with it → **422 "Invalid API key"**. | You need a separate key from a Serverless Inference *subscription* (§2.1). **Resolved (verified):** we created one with `POST /v2/inference` and `{"label": ...}`. The response was `{"subscription": {"id", "label", "api_key", "status": "active", ...}}`, and a live chat completion with that `api_key` succeeded. |
| G3 | `GET https://api.vultr.com/v2/account` with the account key → **401 "Unauthorized IP address"**. | The account key has an IP allowlist. Add the caller's public IP first (§1). **Resolved (verified):** the account key works once the allowlist is opened. |
| G4 | The track deck says `OPENAI_BASE_URL=https://api.vultrinference.com/v1`, `OPENAI_API_KEY=$VULTR_INFERENCE_API_KEY`, example model **Kimi-K2.6**. | The base URL and SDK approach are right. **Kimi-K2.6 is not in today's catalog** of 19 models. Pick from §2.3. |
| G5 | Every model is served from datacenter `atl` (Atlanta) per [MODELS]. | West-coast VMs add cross-country RTT to each LLM call. That is fine, but don't be surprised. |
| G6 | Per the VX1 docs, plans **without** the `-###s` suffix must boot from block storage (needs `block_devices`). https://docs.vultr.com/vultr-vx1-cloud-compute | Use `vx1-g-4c-16g-240s`-style plans (local NVMe) for the simplest `POST /v2/instances`. |
| G7 | The VX1 docs page (updated 2026-04-15) says VX1 is "Currently available in New Jersey (ewr)". A third-party catalog lists more regions. | Check VX1 availability in `sjc`/`lax`/`sea` before choosing a region (§3.1). |
| G8 | The inference gateway treats a **final `assistant` message as a prefill to continue** [INF]. | Don't end `messages` with an assistant turn unless you want continuation. This matters for the content-safety model (§2.9). |

## 0b. Deck claims vs public docs

| Deck claim ([event-decks.md](event-decks.md)) | Verdict |
|---|---|
| Model "Kimi-K2.6", OpenAI-compatible, tool calling | **Contradicted for the model.** It isn't in the 19-model `/v1/models` list (§2.3). The only Kimi mention in Vultr's release notes is `kimi-k2-instruct`, the first tool-call model, in 2025 (https://docs.vultr.com/platform/release-notes). **Confirmed:** OpenAI compatibility and tool calling ([INF]; §2.4 and §2.5). |
| "VX1 exposes KVM", `ls -l /dev/kvm` | **Consistent, not confirmed.** Vultr's VX1 page says only "support for virtualization", and a third-party cpuinfo shows `svm` (§3.3). No Vultr page mentions `/dev/kvm`. gVisor's docs still recommend systrap over KVM on VMs (§7). Run `ls -l /dev/kvm` first. |
| `netbird expose` needs an admin to enable **Peer Expose**; max 10 sessions per peer; `--with-*` flags | **Confirmed** (https://docs.netbird.io/manage/reverse-proxy/expose-from-cli). The docs add two prerequisites the deck omits: run `netbird up` first, and the command **fails if "Block Inbound Connections" is enabled** on the peer. That is a NetBird client setting, not a host port. The v0.66 blog used older flag names (`--pin`, `--password`). |
| Reverse proxy is beta, with SSO/password/PIN/header/peers-only | **Confirmed** (§8). |

---

## 1. Account API key access control (IP allowlist)

- **Portal, account key.** Go to Account → API (under OTHER) → **Access Control**, enter the IP or subnet, click **Add**. Remove entries the same way. https://docs.vultr.com/platform/other/api/manage-api-access-control (updated 2025-09-12). Since April 2026 the portal lives at `console.vultr.com`, so menu names may differ slightly (release notes, April 2026: https://docs.vultr.com/platform/release-notes).
- **Portal, organization users and service accounts.** Go to Organization → Manage Organization → Users → *user* → API Access → Access Control List → **Add IP to Allowlist** (subnet address + prefix). Every user starts with two default entries, **Any IPv4 (0.0.0.0/0)** and **Any IPv6**. You can toggle each one off. "Disabling both default entries without adding a replacement subnet blocks all API access for the user." https://docs.vultr.com/platform/other/users/manage-users/api-access/manage-api-access-control-for-users (updated 2026-08-18)
- **API for per-user allowlists.** Needs the root user or the Manage Users permission. Source: same page as above, API tab, plus https://docs.vultr.com/platform/other/users/manage-users/api-access/ip-address-whitelisting/add-ip-address

  ```bash
  curl -sS "https://api.vultr.com/v2/users" -H "Authorization: Bearer $VULTR_API_KEY"   # find user id
  curl -sS -X POST "https://api.vultr.com/v2/users/$USER_ID/ip-whitelist" \
    -H "Authorization: Bearer $VULTR_API_KEY" -H "Content-Type: application/json" \
    -d '{"subnet": "203.0.113.0", "subnet_size": 24}'
  curl -sS "https://api.vultr.com/v2/users/$USER_ID/ip-whitelist" -H "Authorization: Bearer $VULTR_API_KEY"
  curl -sS -X DELETE "https://api.vultr.com/v2/users/$USER_ID/ip-whitelist" \
    -H "Authorization: Bearer $VULTR_API_KEY" -H "Content-Type: application/json" \
    -d '{"subnet": "203.0.113.0", "subnet_size": 24}'
  ```

  - Only public IPs are accepted: /8 to /32 for IPv4, /20 to /128 for IPv6. Adding the same entry twice is idempotent (search snippet of the add-ip-address page; **UNVERIFIED** on the rendered page).
  - The "Any IPv4/IPv6" defaults "are not returned as individual entries by the Vultr API".
  - The API cannot bootstrap itself. A key that is blocked from your IP can't add your IP, so the **first entry must go in through the portal**.
  - **UNVERIFIED:** whether `/v2/users/{id}/ip-whitelist` also governs the account owner's own "Account → API" key. govultr has no client for this endpoint.

**Why the venue network matters:**

- Venue Wi-Fi often egresses through **several NAT IPs**, and consecutive requests can leave from different addresses. One allowlisted /32 then fails intermittently. It can also leave over **IPv6**: curl prefers IPv6 when it is available, so an IPv4-only allowlist fails.
- Check what the API sees with `curl -4 https://api.ipify.org` and `curl -6 https://api.ipify.org`. Run each a few times.
- Options, from safest to most open:
  1. Allowlist the venue's egress /24 (or the /64 for IPv6).
  2. Call the API from one of our VMs, which has a stable IP.
  3. Temporarily leave Any IPv4 and Any IPv6 enabled, then tighten after the hackathon.
- **VMs are callers too.** The control-plane VX1 that creates burst instances must have its own public IPv4 (and IPv6, if used) on the allowlist. Better: give it a dedicated org user or service account, whose allowlist holds only that VM's IP (IAM service accounts: release notes, April 2026).
- **Rate limit.** The account API allows 30 requests/s per originating IP, then returns HTTP 429. Back off on 429. https://docs.vultr.com/support/platform/api/what-rate-limits-apply-to-the-vultr-api
- **UNVERIFIED:** whether inference subscription keys have any IP restriction. Nothing in [INF] mentions one.

---

## 2. Serverless Inference

### 2.1 Get an inference API key

**Portal:**

- **Create:** Products → Serverless → Inference → **Add Serverless Inference**, enter a label, then acknowledge the models and charges note. https://docs.vultr.com/products/serverless/inference/provisioning
- **Copy the key:** open the subscription; the key is on the Overview page. https://docs.vultr.com/products/compute/serverless-inference/management/connection
- **Regenerate the key** (Console only): this "immediately invalidates the previous key". https://docs.vultr.com/support/products/serverless/how-do-i-regenerate-my-vultr-serverless-inference-api-key

**Account API** (needs `VULTR_API_KEY` and an allowlisted IP). Paths and fields come from `inference.go` in [govultr]; the curl commands come from the provisioning page above.

| Method | Path | Body | Response |
|--------|------|------|----------|
| GET | `/v2/inference` | | `{"subscriptions":[{id,date_created,label,api_key}]}` |
| POST | `/v2/inference` | `{"label":"..."}` | `{"subscription":{id,label,api_key,status:"active",...}}` (**verified by the team**) |
| GET | `/v2/inference/{id}` | | `{"subscription":{...,"api_key"}}` |
| PATCH | `/v2/inference/{id}` | `{"label":"..."}` | `{"subscription":{...}}` |
| DELETE | `/v2/inference/{id}` | | 204 |
| GET | `/v2/inference/{id}/usage` | | `{"usage":{chat_by_model:{current_month,previous_month},audio,image,...}}` |

```bash
# Create once, capture the key into env (never commit it)
RESP=$(curl -sS -X POST "https://api.vultr.com/v2/inference" \
  -H "Authorization: Bearer $VULTR_API_KEY" -H "Content-Type: application/json" \
  --data '{"label":"blast-radius-zero"}')
export VULTR_INFERENCE_ID=$(echo "$RESP" | jq -r '.subscription.id')
export VULTR_INFERENCE_API_KEY=$(echo "$RESP" | jq -r '.subscription.api_key')
# Later, re-read the key (the docs use GET /v2/inference/{id} for exactly this):
curl -sS "https://api.vultr.com/v2/inference/$VULTR_INFERENCE_ID" -H "Authorization: Bearer $VULTR_API_KEY" | jq -r .subscription.api_key
```

- **Verified by the team (2026-09-26):** the POST response contains `api_key` and `status: "active"`, and a live chat completion with that key succeeded.
- Reading the key from GET `/v2/inference/{id}` is documented at https://docs.vultr.com/products/serverless/inference/vector-store/create-collections.
- The usage endpoint is useful for a cost check during the demo.

### 2.2 Endpoints and auth

- **Base URL:** `https://api.vultrinference.com/v1`
- **Header:** `Authorization: Bearer $VULTR_INFERENCE_API_KEY` ([INF] `securitySchemes.APIKey`: http bearer).
- **OpenAI compatibility:** the OpenAI SDK works with `base_url` set to the URL above (https://docs.vultr.com/how-to-use-vultr-cloud-inference-in-python).

| Path | What it does |
|---|---|
| `POST /chat/completions` | OpenAI-style chat, with streaming (SSE) and tools. |
| `POST /chat/completions/RAG` | Same fields as chat, plus required `collection` (a vector store id). |
| `POST /responses` | OpenAI Responses-style: `input`, `max_output_tokens`, `reasoning`, `stream`. |
| `POST /messages` | Anthropic Messages format. Tools use `input_schema`. |
| `POST /rerank` | Rerank models. |
| `POST /images/generations` | Image generation (`z-image-turbo`). |
| `POST /audio/speech`, `GET /audio/voices` | Text to speech. |
| `/vector_store`, `/vector_store/{id}/items`, `/vector_store/{id}/files`, `/vector_store/{id}/search` | Managed RAG store. |
| `GET /models` | Public catalog. Other `/models/*` routes need auth. |
| `GET /usage`, `GET /health` | Usage and health. |

**Not in [INF]:** `/embeddings`, `/completions` and `/audio/transcriptions`.

### 2.3 Model catalog (from [MODELS], 2026-09-26)

How to read the columns:

- **Ctx** is `input_modalities[text].max_context_length`.
- **Tools** means `tools` is listed in `supported_parameters`.
- **Vision** means an `image` input modality. The catalog marked † (see below) disagrees for some models.
- **JSON out:** no model advertises `response_format` or JSON-schema output. See §2.5.
- **Price** is USD per 1M tokens, input / output.

| id | Purpose | Ctx | Tools | Vision / other input | Price in / out |
|---|---|---|---|---|---|
| `deepseek-v4-flash-0731` | agentic chat, MoE 304B/13B active | 1,048,576 | yes | image† | 0.10 / 0.25 |
| `deepseek-v4.1-flash` | chat, reasoning, tools | 1,048,576 | yes | image | 0.15 / 0.60 |
| `glm-5.2` | long-horizon agentic, coding | 1,048,576 | yes | image† | 0.75 / 3.00 |
| `glm-5.3` | stronger coding and agentic (GLM-5.2 base) | 1,048,576 | yes | image† | 0.75 / 3.00 |
| `glm-5.3-flash` | cheap multimodal agentic | 1,048,576 | yes | image, video | 0.10 / 0.35 |
| `glm-5.x-menthol` | GLM-5 744B, agentic and coding | 202,752 | yes | image† | 0.40 / 1.75 |
| `laguna-s-2.1` | agentic coding, 118B/8B active | 1,048,576 | yes | image† | 0.09 / 0.18 |
| `mimo-v2.6-flash-rl` | reasoning and tools | 1,048,576 | yes | image, audio, video | 0.10 / 0.25 |
| `mimo-v2.6-pro-rl` | reasoning and tools, 1.02T/42B | 1,048,576 | yes | image, audio, video | 0.40 / 0.80 |
| `minimax-m3` | long-horizon coding and agentic | 524,288 | yes | image, video | 0.20 / 0.90 |
| `muse-glimmer-30b` | dense multimodal, reasoning and tools | 131,072 | yes | image, video | 0.25 / 1.00 |
| `nemotron-3-nano-omni-30b-a3b-reasoning` | **omni**: document, video and audio Q&A | 262,144 | yes | image, audio, video | 0.10 / 0.25 |
| `nemotron-3.5-content-safety` | moderation classifier | 131,072 | **no** | image | 0.05 / 0.15 |
| `qwen3.8-27b` | compact dense vision-language model | 262,144 | yes | image, video | 0.15 / 1.00 |
| `qwen3.8-flash-next` | cheap, reasoning and tools | 262,144 | yes | image, video | 0.10 / 0.20 |
| `bge-reranker-v2-m3` | rerank (multilingual) | 8,192 | n/a | text | 0.05 (per 1M tokens) |
| `vultron-retriever-core-qwen3.5-4.5b` | rerank, accuracy | 262,144 | n/a | text | 0.10 |
| `vultron-retriever-flash-qwen3.5-0.8b` | rerank, low latency | 262,144 | n/a | text | 0.05 |
| `z-image-turbo` | text to image | n/a | n/a | text | $0.02 per megapixel |

- † The Anthropic-format catalog (`GET /v1/models` with header `anthropic-version: 2023-06-01`) reports `image_input.supported: false` for `deepseek-v4-flash-0731`, `glm-5.2`, `glm-5.3`, `glm-5.x-menthol` and `laguna-s-2.1`. Treat vision on those as **UNVERIFIED**.
- **Reasoning effort.** Every chat model accepts `reasoning_effort` in `ultra|max|xhigh|high|medium|low|minimal`, except the two Nemotron models, whose `supported_efforts` is `null`. Don't send the parameter to those; [INF] says "unsupported levels return 422".
- **Starting picks:** `qwen3.8-flash-next` or `glm-5.3-flash` for cheap tool loops; `glm-5.3` or `minimax-m3` for hard planning; the omni model for screenshots, PDFs and video; `vultron-retriever-flash-*` for reranking.

Refresh the table with:

```bash
curl -s https://api.vultrinference.com/v1/models | jq -r '.data[] | [.id, (.input_modalities[]|select(.type=="text")|.supported_inputs.max_context_length.value), ((.output_modalities[0].supported_parameters.tools)!=null)] | @tsv'
```

### 2.4 Chat, streaming: curl and Python

```bash
curl -sS https://api.vultrinference.com/v1/chat/completions \
  -H "Authorization: Bearer $VULTR_INFERENCE_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"qwen3.8-flash-next","messages":[{"role":"user","content":"Say hi in 3 words"}],
       "max_completion_tokens":256,"reasoning_effort":"low"}' | jq -r '.choices[0].message.content'
# streaming (SSE): add "stream": true and use curl -N
```

```python
import os
from openai import OpenAI

client = OpenAI(base_url="https://api.vultrinference.com/v1",
                api_key=os.environ["VULTR_INFERENCE_API_KEY"])

r = client.chat.completions.create(
    model="qwen3.8-flash-next",
    messages=[{"role": "system", "content": "Be terse."},
              {"role": "user", "content": "Capital of Jalisco?"}],
    max_completion_tokens=512,
    extra_body={"reasoning_effort": "low"},   # passes through regardless of SDK version
)
print(r.choices[0].message.content, r.usage)

for chunk in client.chat.completions.create(model="qwen3.8-flash-next", stream=True,
        messages=[{"role": "user", "content": "Count to 5"}]):
    delta = chunk.choices[0].delta if chunk.choices else None
    if delta and delta.content:
        print(delta.content, end="", flush=True)
```

- **Chat parameters** ([INF]): `model`, `messages` (roles `system|user|assistant|tool|developer`), `stream`, `temperature`, `top_p`, `frequency_penalty`, `presence_penalty`, `seed`, `stop`, `n`, `logprobs`, `top_logprobs`, `tools`, `tool_choice`, `continue_final_message`.
  - `max_completion_tokens` defaults to **32768** and *includes* reasoning tokens. `max_tokens` is the deprecated name.
  - `reasoning_effort`, or `reasoning {effort, enabled, max_tokens}`. `max_tokens` there is enforced only if the model's `reasoning.supports_max_tokens` is true.
- **Model-name suffixes:**
  - `-normalize` (e.g. `glm-5.3-normalize`) rewrites `reasoning_content` to `reasoning`, fixes tool-call ids, and turns `content=None` with tool calls into `""`. Use it if the OpenAI SDK or your parser chokes on tool-call responses.
  - `:express` derives a thinking budget automatically.

### 2.5 Tool calling and structured output

- **Tools** use the OpenAI function-tool shape ([INF]): `{"type":"function","function":{"name","description","parameters": <JSON Schema>, "strict": bool}}`.
  - `strict: true` enables "strict schema adherence … Only a subset of JSON Schema is supported".
  - `tool_choice` accepts `"none" | "auto" | "required"`, `{"type":"function","function":{"name":"..."}}`, or `{"type":"allowed_tools","mode":"auto|required","tools":[...]}`.
  - Tool calls come back in `choices[].message.tool_calls`. Send results as `{"role":"tool","tool_call_id":..., "content": "..."}`.
- **Structured / JSON output is not documented.** [INF] has no `response_format` for chat (only for images), no `json_schema` and no `guided_json`, and [MODELS] advertises none. **UNVERIFIED** whether `response_format` passes through to the engine. Reliable pattern: **force a single tool** and parse its arguments.

```python
schema = {"type": "object", "additionalProperties": False,
          "properties": {"supplier": {"type": "string"}, "rfc": {"type": "string"}},
          "required": ["supplier", "rfc"]}
r = client.chat.completions.create(
    model="glm-5.3-flash",
    messages=[{"role": "user", "content": "Extract supplier and RFC from: ..."}],
    tools=[{"type": "function", "function": {"name": "emit", "description": "Return the extraction",
                                              "parameters": schema, "strict": True}}],
    tool_choice={"type": "function", "function": {"name": "emit"}},
)
import json; data = json.loads(r.choices[0].message.tool_calls[0].function.arguments)  # validate with pydantic
```

### 2.6 Vision and omni input (`nemotron-3-nano-omni-30b-a3b-reasoning`)

- [MODELS]: the omni model accepts `image` (png/jpeg/webp/gif), `audio` (wav/mpeg) and `video` (mp4/webm), each from `url` or `base64`.
- [INF] documents `messages[].content` only as a string, so the content-part format is **UNVERIFIED** on Vultr. Below is the standard OpenAI shape, which NVIDIA's model cards also use (https://huggingface.co/nvidia/Nemotron-3.5-Content-Safety):

```python
import base64
b64 = base64.b64encode(open("shot.png", "rb").read()).decode()
r = client.chat.completions.create(
    model="nemotron-3-nano-omni-30b-a3b-reasoning",
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "List every table header visible."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]}])
```

Audio and video parts (**UNVERIFIED**, vLLM conventions): `{"type":"input_audio","input_audio":{"data": b64, "format":"wav"}}` and `{"type":"video_url","video_url":{"url": "https://..."}}`.

### 2.7 Embeddings, rerank, vector store

- **No embeddings endpoint and no embedding model** in [INF] or [MODELS]. Embeddings happen only inside the managed vector store (`POST /vector_store/{id}/items` with `content` and `auto_chunk`, then `POST /vector_store/{id}/search` with `{"input": "..."}`, or RAG chat with `collection`). If you need raw vectors, you have to generate them somewhere else.
- **Rerank.** The request is `{"model","query","documents":[str|object],"top_n","return_documents"}`. [INF] says the response is "The upstream rerank response, forwarded without reshaping". Its shape is undocumented; probably `results[{index, relevance_score, document?}]` (**UNVERIFIED**).

```bash
curl -sS https://api.vultrinference.com/v1/rerank -H "Authorization: Bearer $VULTR_INFERENCE_API_KEY" \
  -H "Content-Type: application/json" -d '{"model":"vultron-retriever-flash-qwen3.5-0.8b",
  "query":"licitación pública medicamentos","documents":["doc a","doc b"],"top_n":2,"return_documents":true}'
```

```python
import httpx, os
res = httpx.post("https://api.vultrinference.com/v1/rerank", timeout=60,
    headers={"Authorization": f"Bearer {os.environ['VULTR_INFERENCE_API_KEY']}"},
    json={"model": "bge-reranker-v2-m3", "query": q, "documents": docs, "top_n": 5}).json()
```

- **Vector store** ([INF]; https://docs.vultr.com/products/serverless/inference/vector-store/create-collections): `POST /vector_store {"name"}` creates a collection; `POST /vector_store/{id}/items {"content","description","auto_chunk"}` adds an item; `POST /vector_store/{id}/files` uploads multipart `file`; `POST /chat/completions/RAG {"collection","model","messages",...}` runs RAG chat.

### 2.8 Rate limits and pricing

- **Inference rate limits are not published.** [INF] only lists `429 "Rate limit exceeded"` on `/rerank`. Retry with backoff on 429.
- **Pricing.** Since April 2026 billing is "usage-based … per-model input and output token pricing" (release notes, April 2026). The per-model prices are the ones in §2.3.
- An older support page (updated 2026-03-10) quotes a flat $0.55 in / $2.75 out per 1M tokens: https://docs.vultr.com/support/products/serverless/how-do-i-monitor-the-usage-and-cost-of-my-vultr-serverless-inference-subscription. Treat it as legacy.
- Whether a subscription also carries a monthly base fee is **UNVERIFIED**. govultr's usage struct still has deprecated `monthly_allotment` and `overage` fields.

### 2.9 Content safety (`nemotron-3.5-content-safety`)

- The model card (https://huggingface.co/nvidia/Nemotron-3.5-Content-Safety) says output is plain-text lines: `User Safety: safe|unsafe`, `Response Safety: safe|unsafe`, `Safety Categories: a, b`.
- Its options go through `chat_template_kwargs` (`request_categories: "/categories"`, `enable_thinking`, `custom_policy`). **UNVERIFIED** whether Vultr forwards `chat_template_kwargs`.
- The card checks a response by sending it as a final `assistant` message. On Vultr that triggers **continuation** (G8), so embed the text to check inside the user turn instead.

```python
r = client.chat.completions.create(model="nemotron-3.5-content-safety", max_completion_tokens=64,
    messages=[{"role": "user", "content": untrusted_page_text[:20000]}],
    extra_body={"chat_template_kwargs": {"request_categories": "/categories"}})  # kwargs: UNVERIFIED
verdict = r.choices[0].message.content  # parse "User Safety: ..." lines
```

---

## 3. Compute (VX1)

### 3.1 Plans, OS image, regions

| Item | Value | Source |
|---|---|---|
| Plan families | `vx1-g-*` general purpose, `vx1-m-*` memory optimized. A `-###s` suffix means that much local NVMe in GB. No suffix means you must boot from block storage. | https://docs.vultr.com/vultr-vx1-cloud-compute |
| Example ids (third-party catalog) | `vx1-g-2c-8g-120s` (2 vCPU/8 GB/120 GB, ~$0.076/h), `vx1-g-4c-16g-240s` (4/16/240, ~$0.153/h), `vx1-g-8c-32g-480s` (8/32/480, ~$0.306/h), `vx1-m-4c-32g-240s` | https://sparecores.com/server/vultr/vx1-g-4c-16g-240s (**confirm with `GET /v2/plans`**) |
| Ubuntu 24.04 LTS x64 | `os_id: 2284` | https://docs.vultr.com/products/compute/instances/cloud-compute/provisioning (Terraform snippet) |
| Regions near SF | `sjc` Silicon Valley, `lax` Los Angeles, `sea` Seattle | Third-party catalog above: VX1 `-240s` listed in `sjc`; non-suffix VX1 listed in `sea`, not `sjc` |
| VX1 notes | Automated backups disabled; no Windows | VX1 docs |

Suggested sizes: `vx1-g-4c-16g-240s` for the control plane (VX1 #1), `vx1-g-8c-32g-480s` for the sandbox host that runs Chromium pods (VX1 #2).

Check before you pick (both reads need `VULTR_API_KEY` and an allowlisted IP):

```bash
curl -sS "https://api.vultr.com/v2/plans?per_page=500" -H "Authorization: Bearer $VULTR_API_KEY" \
  | jq -r '.plans[] | select(.id|startswith("vx1-g-")) | "\(.id)\t\(.vcpu_count)c\t\(.ram)MB\t\(.locations|join(","))"'
curl -sS "https://api.vultr.com/v2/regions/sjc/availability" -H "Authorization: Bearer $VULTR_API_KEY" | jq '.available_plans[]' | grep vx1
curl -sS "https://api.vultr.com/v2/os?per_page=500" -H "Authorization: Bearer $VULTR_API_KEY" | jq '.os[] | select(.name|test("Ubuntu 24.04"))'
```

**Both VMs must be in the same region**, because a VPC is regional (§4).

### 3.2 Create, poll, delete

Create request fields (from `InstanceCreateReq` in [govultr]):

- `region`, `plan`, `os_id`, `label`, `hostname`, `tags[]`
- `sshkey_id[]`, `script_id`, `user_data` (**base64**), `firewall_group_id`
- `attach_vpc[]`, `enable_vpc`, `vpc_only`, `enable_ipv6`, `disable_public_ipv4`
- `block_devices[]`

The account API needs `user_data` base64-encoded; the console and vultr-cli don't. https://docs.vultr.com/how-to-deploy-a-vultr-server-with-cloudinit-userdata

The full create call (SSH key, `user_data`, VPC, firewall, tags) is `mk()` in runbook step 6. Poll and delete:

```bash
V=https://api.vultr.com/v2; H=(-H "Authorization: Bearer $VULTR_API_KEY" -H "Content-Type: application/json")
# Poll: Terraform waits for status=active, then power_status=running
until [ "$(curl -sS $V/instances/$ID "${H[@]}" | jq -r '.instance.status+"/"+.instance.power_status')" = "active/running" ]; do sleep 5; done
curl -sS $V/instances/$ID "${H[@]}" | jq '.instance | {main_ip, v6_main_ip, server_status}'
# Throwaway teardown (one, or all with a tag)
curl -sS -X DELETE $V/instances/$ID "${H[@]}"
for i in $(curl -sS "$V/instances?tag=burst&per_page=500" "${H[@]}" | jq -r '.instances[].id'); do curl -sS -X DELETE $V/instances/$i "${H[@]}"; done
```

- The polling logic mirrors `resource_vultr_instance.go` in https://github.com/vultr/terraform-provider-vultr: it waits for `status` to move from pending/installing to `active`, then for `power_status` to reach `running`.
- `server_status` reaching `"ok"` means boot finished (**UNVERIFIED**).
- cloud-init keeps running after `active`. Wait for it on the box with `cloud-init status --wait`.
- The `tag` list filter is `ListOptions.Tag` in [govultr].
- Burst instances:
  - Use the same call with `tags:["brz","burst"]`, no `attach_vpc` unless they need it, and a short-lived NetBird setup key.
  - VX1 plans with local NVMe lose their data on delete (VX1 docs).

### 3.3 `/dev/kvm` on VX1

- The Vultr docs say VX1 "Supports additional CPU features including support for virtualization" (VX1 docs).
- A third-party catalog's `/proc/cpuinfo` dump for `vx1-g-*` shows the AMD `svm` flag inside the guest (https://sparecores.com/server/vultr/vx1-g-2c-8g). Nested KVM is therefore **likely but UNVERIFIED**.
- Check on the box:
  ```bash
  grep -c svm /proc/cpuinfo; ls -l /dev/kvm || sudo modprobe kvm_amd && ls -l /dev/kvm
  ```
- It doesn't matter much for us. gVisor recommends **systrap** (its default) on VMs, because nested KVM is slower (§7).

---

## 4. Private networking (VPC)

- **VPC 2.0 is retired.** govultr removed VPC2 ([govultr] CHANGELOG, "Remove VPC2 data and functions", PR 470), and Vultr publishes a migration guide: https://docs.vultr.com/how-to-migrate-from-vultr-vpc-20-network-to-a-vultr-vpc-network. Use plain **VPC** (`/v2/vpcs`).
- **Create:** `POST /v2/vpcs {"region","description","v4_subnet","v4_subnet_mask"}` returns `{"vpc":{id,...}}`. Omit the subnet fields to auto-assign. https://docs.vultr.com/products/network/vpc/provisioning and `vpc.go` in [govultr]
- **Attach at create time** with `attach_vpc: [VPC_ID]`, which is the easy path. After creation, use `POST /v2/instances/{id}/vpcs/attach {"vpc_id"}` (`instance.go` in [govultr]).
- **Addressing.** "If you attach your VPCs while deploying the server, Vultr automatically configures those network adapters" via cloud-init. If you attach later, you configure the adapter by hand. https://docs.vultr.com/how-to-configure-networking-on-vultr-cloud-servers
- **Find each VM's private IP:**
  - From the API: `GET /v2/instances/{id}/vpcs` returns `vpcs[{id, mac_address, ip_address}]`.
  - On the box: `ip -4 addr`.
  - Put the peer's private IP into the other VM's env (e.g. `CONTROL_PLANE_URL=http://10.x.x.x:8000`).
- **Use the VPC IP between the VMs** for Postgres/API traffic, or run NetBird on both and use the `100.x` NetBird addresses so access policies apply (§8). The commands are in runbook steps 4 and 6.

---

## 5. Firewall group (zero inbound)

- **Default deny inbound.** "Vultr Firewall Groups use a default-deny policy. If a specific port or protocol is not explicitly allowed in the inbound rules, all traffic on that port will be blocked." https://docs.vultr.com/support/products/compute/how-do-i-debug-a-firewall-causing-connection-problems-with-my-vultr-compute-instance
- **Outbound is not filtered.** "Vultr Firewall does not filter outgoing network traffic". https://docs.vultr.com/products/network/firewall-groups/faq
- So a group **with no rules** means zero inbound ports on both VMs.
- **API calls** (`firewall_group.go` and `firewall_rule.go` in [govultr]; https://docs.vultr.com/products/network/firewall/provisioning):
  - `POST /v2/firewalls {"description"}` returns `{"firewall_group":{id}}`.
  - Rules: `POST /v2/firewalls/{id}/rules {"ip_type":"v4","protocol":"tcp","subnet":"203.0.113.7","subnet_size":32,"port":"22","notes":"break-glass"}`.
- **NetBird coexists** because peers need no inbound ports. "The NetBird client doesn't require any inbound port to be open". It only needs outbound TCP 443 (management, signal, relay) and UDP to STUN (3478 and others). https://docs.netbird.io/about-netbird/ports-and-firewalls
- **UNVERIFIED:** whether Vultr firewall groups are stateful for UDP (return traffic for hole punching) and whether they filter VPC traffic. If `netbird status -d` shows peers as `Relayed` instead of `P2P`, traffic still flows over the relay, only slower. Optionally pin `netbird up --wireguard-port 51820`.
- Without an SSH rule, break-glass access is SSH over NetBird (`ssh root@<netbird-ip>`) or the Vultr web console. The create command is in runbook step 4.

---

## 6. Object Storage (S3)

Source: https://docs.vultr.com/products/storage/object-storage/provisioning, `object_storage.go` in [govultr].

| Step | Call |
|---|---|
| Clusters (region to hostname) | `GET /v2/object-storage/clusters` returns `clusters[{id, region, hostname, deploy}]` |
| Tiers | `GET /v2/object-storage/tiers` or `GET /v2/object-storage/clusters/{cluster_id}/tiers` returns `tiers[{id, sales_name, slug, price, ...}]` |
| Create subscription | `POST /v2/object-storage {"cluster_id":N,"tier_id":M,"label":"brz-lake"}` returns `object_storage{id, status, s3_hostname, s3_access_key, s3_secret_key}` |
| Get keys later | `GET /v2/object-storage/{id}` (same fields). Rotate with `POST /v2/object-storage/{id}/regenerate-keys` ([credentials doc](https://docs.vultr.com/products/storage/object-storage/management/manage-credentials)) |
| Create bucket (API) | `POST /v2/object-storage/{id}/bucket {"name":"brz-bronze","enable_bucket_versioning":false,"enable_object_lock":false}`. List with `GET .../bucket`, delete with `DELETE .../bucket/{name}` |

- **Hostnames** follow `<region>1.vultrobjects.com`, e.g. `ewr1.vultrobjects.com`. Virtual-host style is `bucket.ewr1.vultrobjects.com`. Some clients need path-style. https://docs.vultr.com/platform/security-best-practices/vultr-object-storage
- `sjc1.vultrobjects.com` exists (third-party DNS index https://www.netify.ai/resources/hostnames/sjc1.vultrobjects.com). Always use the `hostname` returned by `/clusters`.
- **Tiers:** Standard, Premium, Performance (NVMe), Accelerated (NVMe) and Archive. Not every tier exists in every region, which is why the cluster-tier list call is there (provisioning doc; release notes, April 2026).
- Wait for `status` to become `active` before you use the keys (**UNVERIFIED** status string).

The create calls are in runbook step 7. To read the keys into a gitignored env file (never into git):

```bash
curl -sS $V/object-storage/$OBJ_ID "${H[@]}" | jq -r '.object_storage | "S3_ENDPOINT=https://\(.s3_hostname)\nS3_ACCESS_KEY=\(.s3_access_key)\nS3_SECRET_KEY=\(.s3_secret_key)"' >> .env.vultr
```

```python
import os, boto3
from botocore.config import Config
s3 = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"],          # e.g. https://sjc1.vultrobjects.com
                  aws_access_key_id=os.environ["S3_ACCESS_KEY"],
                  aws_secret_access_key=os.environ["S3_SECRET_KEY"],
                  region_name="us-east-1",                                  # placeholder; SigV4 needs a value
                  config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
s3.create_bucket(Bucket="brz-bronze")                                       # alternative to the API call
s3.put_object(Bucket="brz-bronze", Key="captures/sha256/ab/abcd.html", Body=b"...")
url = s3.generate_presigned_url("get_object", Params={"Bucket": "brz-bronze", "Key": "..."}, ExpiresIn=300)
```

- The `region_name` value Vultr expects for SigV4 is **UNVERIFIED**. Any value usually works with custom endpoints. If you get a signature error, try the cluster region code.

---

## 7. gVisor (`runsc`) on Ubuntu 24.04 with Docker

Sources: https://gvisor.dev/docs/user_guide/install/, https://gvisor.dev/docs/user_guide/quick_start/docker/, https://gvisor.dev/docs/user_guide/platforms/, https://gvisor.dev/docs/user_guide/networking/. The latest release is 20260921.0 (https://github.com/google/gvisor/releases).

```bash
sudo apt-get update && sudo apt-get install -y apt-transport-https ca-certificates curl gnupg
curl -fsSL https://gvisor.dev/archive.key | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
  | sudo tee /etc/apt/sources.list.d/gvisor.list > /dev/null
sudo apt-get update && sudo apt-get install -y runsc
sudo runsc install                 # adds "runsc" to /etc/docker/daemon.json
sudo systemctl restart docker
# verify
docker run --rm --runtime=runsc hello-world
docker run --rm --runtime=runsc ubuntu dmesg     # prints "Starting gVisor..." (docs: dmesg is spoofable, not a security check)
```

- **Docker itself** installs with `curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh get-docker.sh`, or via the apt repo. https://docs.docker.com/engine/install/ubuntu/
- **Platform:** systrap is the default. The docs say to "use the KVM platform on bare-metal machines only"; on VMs systrap usually performs better. ptrace is deprecated.
- **Networking:**
  - The default is netstack, a user-space network stack isolated from the host.
  - `--network=host` weakens isolation, so don't use it for sandboxes.
  - `-p` port publishing works.
- **Force gVisor on the sandbox host.** Set `"default-runtime": "runsc"` in `/etc/docker/daemon.json`; it's a dockerd option (https://docs.docker.com/reference/cli/dockerd/). Otherwise always pass `--runtime=runsc`.
- **Chromium/Playwright under gVisor: UNVERIFIED.** gVisor's docs don't cover browsers.
  - Playwright recommends `--ipc=host` plus `--init`, and for untrusted sites `--user pwuser` with a seccomp profile (https://playwright.dev/docs/docker).
  - Under runsc, try `--shm-size=1g` instead of `--ipc=host`.
  - Smoke test (image tag **UNVERIFIED**; check https://playwright.dev/python/docs/docker):
    ```bash
    docker run --rm --runtime=runsc --init --shm-size=1g --user pwuser mcr.microsoft.com/playwright/python:v1.63.0-noble \
      python -c "from playwright.sync_api import sync_playwright as p; b=p().start().chromium.launch(); pg=b.new_page(); pg.goto('https://example.com'); print(pg.title())"
    ```
- **Egress hygiene for sandbox pods.** These are generic Docker/iptables mechanics, not Vultr-specific.
  - Block the metadata service. It serves `user_data`, including any setup key you put in it, over unauthenticated HTTP at `169.254.169.254` (https://docs.cloud-init.io/en/latest/reference/datasources/vultr.html).
  - Block the VPC subnet as well, so a compromised page can't reach Postgres:
    ```bash
    sudo iptables -I DOCKER-USER -d 169.254.169.254/32 -j DROP
    sudo iptables -I DOCKER-USER -d 10.42.0.0/24 -j DROP
    ```
  - Enforce the per-pod domain allowlist with an egress proxy, or per-pod networks plus DOCKER-USER rules.

---

## 8. NetBird (short)

| Topic | Facts | Source |
|---|---|---|
| Client install | `curl -fsSL https://pkgs.netbird.io/install.sh \| sh`, then `sudo netbird up --setup-key "$NETBIRD_SETUP_KEY"` (self-hosted: add `--management-url https://netbird.example.com`, **UNVERIFIED** exact form). Check with `netbird status`. | https://docs.netbird.io/get-started/install/linux |
| Peer to peer | No inbound ports on peers. ICE (Pion) hole punching with relay fallback; the relay stays end-to-end WireGuard encrypted. v0.36+ falls back to WebSocket over TCP/443 when UDP is blocked. | https://docs.netbird.io/about-netbird/ports-and-firewalls, https://docs.netbird.io/about-netbird/how-netbird-works |
| Access policies | "Without policies, no peer can communicate with another peer." Groups plus policies (source, destination, protocol, ports, direction). Remove the default ALL-to-ALL. Posture checks are available. | https://docs.netbird.io/manage/access-control |
| Reverse proxy ("expose a service") | **Beta.** Serves a peer's port publicly with automatic TLS through the NetBird tunnel, with no open ports on the peer. Works on Cloud (free domains) and self-hosted (needs Traefik). UI: Reverse Proxy → Services → Add Service → HTTP → subdomain → target Peer + port → auth. | https://docs.netbird.io/manage/reverse-proxy |
| Proxy auth | SSO (OIDC, optional IdP groups), password, PIN, header (static value, stripped before forwarding), or NetBird-only. 24h sessions. Layer 4 services get no browser auth. | https://docs.netbird.io/manage/reverse-proxy/authentication |
| CLI expose | `netbird expose 8000 --with-password ...` (also `--with-pin`, `--with-user-groups`, `--protocol`). The URL is ephemeral and lives while the command runs. An admin must enable **Peer Expose** (Settings → Clients). Fails if the peer has "Block Inbound Connections" on, which is a client setting, not a firewall port. Flag names differ in the v0.66 blog; check `netbird expose --help`. | https://docs.netbird.io/manage/reverse-proxy/expose-from-cli |
| Self-hosted management | `curl -fsSL https://github.com/netbirdio/netbird/releases/latest/download/getting-started.sh \| bash` on a VM with ≥1 CPU and 2 GB, Docker Compose, and a DNS name. **Needs inbound TCP 80 and 443 and UDP 3478**, plus `*.netbird` wildcard DNS if you enable the proxy. Embedded Dex IdP. | https://docs.netbird.io/selfhosted/selfhosted-quickstart |

The deck also describes a **Vultr Marketplace NetBird app** as the self-hosted route ([event-decks.md](event-decks.md) §2). I didn't check it against the docs. It has the same inbound-port requirement as the quickstart script.

**Recommendation for "zero inbound":**

- Use **NetBird Cloud** for management and the reverse proxy. Both VX1s then keep a rule-less firewall group.
- Self-hosting puts inbound ports on whatever VM runs the management server, so it would need a third small VM with its own firewall group allowing 80/443/tcp and 3478/udp.
- For a stable demo URL, use a dashboard Service (persistent) rather than `netbird expose`.

**Setup keys:**

- Use a reusable key for the two long-lived VMs.
- Use one-off or ephemeral keys with short expiry for burst instances, because the key sits in `user_data` (§7).

---

## 9. Copy-paste runbook

Prereqs:

- Locally: `curl`, `jq`, an SSH key pair, and an env with `VULTR_API_KEY` and `NETBIRD_SETUP_KEY`.
- Set `REGION` after step 2's availability check (e.g. `sjc`, else `sea` or `lax`).

```bash
set -euo pipefail
V=https://api.vultr.com/v2; H=(-H "Authorization: Bearer $VULTR_API_KEY" -H "Content-Type: application/json")
: "${REGION:=sjc}"
```

**1. Allowlist our IP.** Portal: Account → API → Access Control (§1). Check what the API will see with:

```bash
curl -4 -s https://api.ipify.org; echo; curl -6 -s https://api.ipify.org; echo
curl -sS $V/account "${H[@]}" | jq .account.email       # must succeed (no 401) before continuing
```

**2. Check the VX1 plan in the region** (§3.1):

```bash
curl -sS "https://api.vultr.com/v2/regions/$REGION/availability" "${H[@]}" | jq -r '.available_plans[]' | grep '^vx1-g-.*s$'
```

**3. Create the inference subscription and capture its key** (§2.1). Then smoke-test it:

```bash
RESP=$(curl -sS -X POST $V/inference "${H[@]}" -d '{"label":"brz"}')
export VULTR_INFERENCE_ID=$(jq -r .subscription.id <<<"$RESP")
export VULTR_INFERENCE_API_KEY=$(jq -r '.subscription.api_key // empty' <<<"$RESP")
[ -n "$VULTR_INFERENCE_API_KEY" ] || export VULTR_INFERENCE_API_KEY=$(curl -sS $V/inference/$VULTR_INFERENCE_ID "${H[@]}" | jq -r .subscription.api_key)
curl -sS https://api.vultrinference.com/v1/chat/completions -H "Authorization: Bearer $VULTR_INFERENCE_API_KEY" \
  -H "Content-Type: application/json" -d '{"model":"qwen3.8-flash-next","messages":[{"role":"user","content":"ping"}],"max_completion_tokens":32}' | jq .choices[0].message
```

**4. Create the VPC and the firewall group:**

```bash
VPC_ID=$(curl -sS -X POST $V/vpcs "${H[@]}" -d "{\"region\":\"$REGION\",\"description\":\"brz\",\"v4_subnet\":\"10.42.0.0\",\"v4_subnet_mask\":24}" | jq -r .vpc.id)
FW_ID=$(curl -sS -X POST $V/firewalls "${H[@]}" -d '{"description":"brz-zero-inbound"}' | jq -r .firewall_group.id)
SSH_KEY_ID=$(curl -sS -X POST $V/ssh-keys "${H[@]}" -d "$(jq -n --arg k "$(cat ~/.ssh/id_ed25519.pub)" '{name:"brz",ssh_key:$k}')" | jq -r .ssh_key.id)
```

**5. Write the cloud-init file** (shared by both VMs; `ROLE` changes the sandbox extras). Save it as `cloud-init.tmpl.yaml`:

```yaml
#cloud-config
package_update: true
packages: [ca-certificates, curl, gnupg, jq, apt-transport-https]
runcmd:
  - [bash, -c, "curl -fsSL https://get.docker.com -o /tmp/get-docker.sh && sh /tmp/get-docker.sh"]
  - [bash, -c, "curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg"]
  - [bash, -c, "echo \"deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main\" > /etc/apt/sources.list.d/gvisor.list"]
  - [bash, -c, "apt-get update && apt-get install -y runsc && runsc install"]
  - [bash, -c, "if [ '__ROLE__' = sandbox ]; then jq '. + {\"default-runtime\":\"runsc\"}' /etc/docker/daemon.json > /tmp/d.json && mv /tmp/d.json /etc/docker/daemon.json; fi"]
  - [systemctl, restart, docker]
  - [bash, -c, "if [ '__ROLE__' = sandbox ]; then iptables -I DOCKER-USER -d 169.254.169.254/32 -j DROP; iptables -I DOCKER-USER -d 10.42.0.0/24 -j DROP; fi"]
  - [bash, -c, "curl -fsSL https://pkgs.netbird.io/install.sh | sh"]
  - [bash, -c, "netbird up --setup-key '__NB_SETUP_KEY__'"]
  - [bash, -c, "docker run --rm --runtime=runsc hello-world > /root/gvisor-check.txt 2>&1 || true"]
```

- The DOCKER-USER rules are not persistent across reboots. Re-apply them from a systemd unit if you reboot.

**6. Create both VX1 instances.** Use `jq -r .instance.id` on each response, then poll as in §3.2:

```bash
render() { sed -e "s|__ROLE__|$1|g" -e "s|__NB_SETUP_KEY__|$NETBIRD_SETUP_KEY|g" cloud-init.tmpl.yaml | base64 | tr -d '\n'; }
mk() { # $1=label $2=plan $3=role
  curl -sS -X POST $V/instances "${H[@]}" -d "$(jq -n --arg l "$1" --arg p "$2" --arg ud "$(render "$3")" \
    --arg r "$REGION" --arg s "$SSH_KEY_ID" --arg vpc "$VPC_ID" --arg fw "$FW_ID" \
    '{region:$r, plan:$p, os_id:2284, label:$l, hostname:$l, tags:["brz",$l], sshkey_id:[$s],
      user_data:$ud, attach_vpc:[$vpc], firewall_group_id:$fw, enable_ipv6:true}')" | jq -r .instance.id; }
CP_ID=$(mk brz-control vx1-g-4c-16g-240s control)
SB_ID=$(mk brz-sandbox vx1-g-8c-32g-480s sandbox)
for i in $CP_ID $SB_ID; do until [ "$(curl -sS $V/instances/$i "${H[@]}" | jq -r '.instance.status+"/"+.instance.power_status')" = "active/running" ]; do sleep 5; done; done
for i in $CP_ID $SB_ID; do curl -sS $V/instances/$i/vpcs "${H[@]}" | jq -r ".vpcs[0].ip_address"; done   # private IPs
```

- Allowlist the control plane's `main_ip` (§1) if it will create burst instances.
- Then, over NetBird: `ssh root@<netbird-ip> cloud-init status --wait && cat /root/gvisor-check.txt`.

**7. Create Object Storage and a bucket** (§6):

```bash
CL=$(curl -sS $V/object-storage/clusters "${H[@]}" | jq -r ".clusters[] | select(.region==\"$REGION\") | .id" | head -1)
curl -sS $V/object-storage/clusters/$CL/tiers "${H[@]}" | jq -r '.tiers[] | "\(.id)\t\(.slug)\t\(.price)"'   # pick TIER_ID
OBJ_ID=$(curl -sS -X POST $V/object-storage "${H[@]}" -d "{\"cluster_id\":$CL,\"tier_id\":$TIER_ID,\"label\":\"brz-lake\"}" | jq -r .object_storage.id)
until [ "$(curl -sS $V/object-storage/$OBJ_ID "${H[@]}" | jq -r .object_storage.status)" = "active" ]; do sleep 5; done
curl -sS -X POST $V/object-storage/$OBJ_ID/bucket "${H[@]}" -d '{"name":"brz-bronze"}'
```

**8. NetBird.** In the NetBird dashboard:

- Add a policy between the groups of `brz-control` and `brz-sandbox`, then remove ALL-to-ALL.
- Add a Reverse Proxy Service targeting `brz-control:8000` with SSO or password (§8).

**9. Burst instance** (from the control plane, whose IP must be allowlisted):

- Call `mk brz-burst-$RANDOM vx1-g-2c-8g-120s sandbox`, but with tag `burst` and an ephemeral NetBird key.
- Delete it after use.

**10. Teardown** (reverse order; local NVMe data is lost):

```bash
for i in $(curl -sS "$V/instances?tag=brz&per_page=500" "${H[@]}" | jq -r '.instances[].id'); do curl -sS -X DELETE $V/instances/$i "${H[@]}"; done
curl -sS -X DELETE $V/object-storage/$OBJ_ID/bucket/brz-bronze "${H[@]}"   # must be empty first (UNVERIFIED)
curl -sS -X DELETE $V/object-storage/$OBJ_ID "${H[@]}"
sleep 30; curl -sS -X DELETE $V/firewalls/$FW_ID "${H[@]}"; curl -sS -X DELETE $V/vpcs/$VPC_ID "${H[@]}"
curl -sS -X DELETE $V/ssh-keys/$SSH_KEY_ID "${H[@]}"
curl -sS -X DELETE $V/inference/$VULTR_INFERENCE_ID "${H[@]}"   # key stops working immediately
```

- `DELETE` paths follow the same resource paths in [govultr].
- Deleting a VPC or firewall group that is still attached probably fails. Wait until the instances are gone (**UNVERIFIED**).

---

## 10. UNVERIFIED items to check by hand

1. ~~`POST /v2/inference` returns `api_key` inline~~. Verified by the team on 2026-09-26.
2. VX1 availability in `sjc`/`lax`/`sea` and the exact plan ids. Run `GET /v2/regions/{r}/availability`.
3. Whether `/v2/users/{id}/ip-whitelist` covers the account owner's key, and whether inference keys have any IP restriction.
4. `response_format` / JSON-schema output on chat. The documented fallback is a forced tool call.
5. Image, audio and video content-part format on the omni model; the vision flags that conflict between the two catalog formats (†).
6. The rerank response shape; whether `chat_template_kwargs` reaches the content-safety model.
7. Inference rate limits, and whether there is any monthly subscription fee.
8. `/dev/kvm` on VX1 (it's likely, given the `svm` flag).
9. Chromium/Playwright under `runsc`; the Playwright Python image tag.
10. Whether Vultr firewall groups are stateful for UDP and filter VPC traffic; NetBird P2P vs relayed.
11. The NetBird `--management-url` form for self-hosting; the `netbird expose` flag names.
12. Object Storage `status` string and SigV4 `region_name`; deleting a non-empty bucket.
