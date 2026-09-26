# Event decks: Vultr "Blast Radius Zero" and NetBird (Agent Arena, Sep 26–27 2026)

Condensed notes from the two partner presentations. They are the organizers' own framing of what judges look for,
so the design and demo should answer them directly. Sources: the Vultr track deck (PDF shared with participants),
and the NetBird deck at https://techhut.tv/decks/agentarena26. Paraphrased; commands quoted verbatim.

---

## 1. Vultr — "Blast Radius Zero: Safe Agent Execution on Vultr"

**Opening case:** "April 2026 — a coding agent, mid-task, deletes a customer's production database records."

### The brief, decoded (what the judge is asking)
| The track says | The judge asks | Our answer (where it shows) |
|---|---|---|
| VM-based backend on Vultr | "Show me the instance." | Control plane + sandbox host on VX1; proof checkpoint 1 in the run view (CONTRACT §8) |
| LLM calls via Vultr Serverless Inference | "Is the model yours, or a borrowed key?" | Our own inference subscription; `generated_by.backend = vultr` on every artifact; spend by mode in the run view |
| Vultr is the center of orchestration | "Is Vultr planning and dispatching, or serving a static page?" | Engine phases plan on Vultr models and dispatch sandbox jobs to VM #2; throwaway instances via the Vultr API |
| Sandboxes never inside your app process | "If I paste `rm -rf /`, what dies?" | Only the pod: gVisor `runsc`, internal network, egress proxy; isolation probes shown as BLOCKED |

> "An agent that only chats is a demo. An agent that executes safely is a product."

### Isolation ladder: "A container is not a sandbox"
| Tier | What | What dies |
|---|---|---|
| 01 In-process | `eval()` / `subprocess` in your worker | your app, your DB creds, your cloud keys |
| 02 Container (runc) | shared kernel | one kernel bug from tier 1 |
| 03 User-space kernel (gVisor) | syscalls intercepted | the sandbox process |
| 04 microVM (Firecracker / libkrun / Kata) | own kernel | the VM — throw it away |

`$ ls -l /dev/kvm` — VX1 exposes KVM, so every tier runs on one instance. **We target tier 03 (gVisor) for
pods on VM #2**, with tier 04 (throwaway instance) as the escalation for anything riskier.

### Pick your sandbox
| | gVisor | OpenSandbox | E2B |
|---|---|---|---|
| It is | a Docker runtime (`--runtime=runsc`) | a sandbox platform: API, SDKs, `osb` CLI, MCP | the SDK most agent frameworks speak |
| Time to first sandbox on VX1 | ~10 min | ~30–45 min | SDK: minutes · self-host: check docs |
| Pick it if | you think in Docker and mostly run code | you want egress rules + secrets handled, or need a browser | your framework already integrates E2B |
| Know that | orchestration and egress are yours to write | runs on gVisor / Kata / Firecracker underneath | read the self-hosting docs early |

"Runtime = how strong is the wall. Platform = how your agent talks to it. Best combo: OpenSandbox on gVisor."
**Our choice:** gVisor directly, with our own orchestration and egress proxy (already built and tested locally).

### Architecture: "Two instances. One boundary."
Browser (web app) → **VX1 #1 control plane** (FastAPI / Node, planner) → `dispatch(task)` over a **private network**
→ **VX1 #2 sandbox host**: sandbox-01 (gVisor / microVM, code task), sandbox-02 (Playwright, browser task),
sandbox-N (destroyed on finish). Vultr Serverless Inference at `api.vultrinference.com/v1`.
**Verifiable output:** stdout · exit code · files · screenshot · hostname/uname proof.

### Demo output: "Five checkpoints"
1. host check — CPU virt ✓, KVM device `/dev/kvm` ✓, KVM access read/write ✓
2. agent → `sandbox_run` — a real result
3. proof — sandbox hostname + `uname -a` (e.g. `Linux py-demo 6.12.99 … x86_64 GNU/Linux`)
4. isolation probe — **BLOCKED**
5. teardown — (no sandboxes)
→ Adopted as CONTRACT §8 / §8a (`jobs.jsonl`) and rendered in the app's run view.

### First commands (verbatim, VX1 Ubuntu 24.04)
```
sudo usermod -aG kvm $USER && newgrp kvm && ls -l /dev/kvm
# gVisor
curl -fsSL https://gvisor.dev/archive.key | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
# add repo → apt install runsc → sudo runsc install → systemctl restart docker
docker run --rm --runtime=runsc python:3-slim python -c "import platform; print(platform.uname())"
# OpenSandbox: github.com/opensandbox-group/OpenSandbox → server quickstart, then:
osb create --image python:3-slim && osb list
# Vultr Serverless Inference (OpenAI-compatible, tool calling)
export OPENAI_BASE_URL=https://api.vultrinference.com/v1
export OPENAI_API_KEY=$VULTR_INFERENCE_API_KEY
# model: Kimi-K2.6
```
Note (verified 2026-09-26): the live model list at `GET https://api.vultrinference.com/v1/models` did not include
a Kimi model; use the list, not the deck, for model ids.

Resources named: Vultr VX1 docs, Serverless Inference chat + tool-calling guides, gVisor, OpenSandbox, E2B,
"Microsandbox on VX1" guide. Closing line: "Blast radius zero is the reason you can let your agent do anything."

---

## 2. NetBird — "Connect your stack. Expose what matters."

**Problem:** stacks are spread across laptops, Vultr VMs, a GPU box and isolated networks. The quick fixes (public
IPs, open ports, shared SSH keys) widen the attack surface for both agents and internet scanners.

**What NetBird is:** open-source networking on WireGuard. Install the client, sign in, and the machine joins a
private network with a 100.x address.
- Peer-to-peer: direct WireGuard tunnels, NAT traversal, encrypted relay fallback.
- Access by identity: SSO/MFA sign-in; groups and policies replace IP allowlists.
- Runs on Linux, macOS, Windows, iOS, Android, Docker, Kubernetes. NetBird Cloud or a self-hosted control plane.
- How peers connect: IdP for auth → Management (network map + policies) → Signal + STUN for discovery → Relay as
  encrypted fallback → point-to-point WireGuard. Private keys stay on the machines; traffic doesn't route through
  NetBird servers.

**Use cases the deck recommends (all apply to us):**
1. Share an MCP server: tools on a laptop, the agent on a Vultr VM calls them at a private NetBird address.
2. Keep data private: Postgres, Redis and vector stores avoid public IPs; only allowed peers connect.
3. **Fence in your agents:** give agents their own group and allow only the hosts and ports they need, which
   contains off-script behavior. → Our sandbox host VM goes in its own group, reaching only the control plane's
   lake/API ports.

### Reverse proxy (Cloud **beta** and self-hosted)
Visitor's browser → HTTPS → NetBird Proxy (TLS + auth at the edge, certificates auto-issued) → WireGuard → your
peer → your service (no public IP, no open ports).
Protection: SSO/OIDC · password · 6-digit PIN · API key header · NetBird peers only · IP and country rules ·
CrowdSec reputation. Routing: custom domain via CNAME, URL path routing, HTTP/TCP/UDP/TLS.

### `netbird expose` (v0.66+): one command, one public URL
```
$ netbird expose 3000 --with-name-prefix demo
→ https://demo-a1b2c3.proxy.example.com
```
Lock down with `--with-pin`, `--with-password`, `--with-user-groups`; `--protocol tcp|udp|tls`,
`--with-external-port`, `--with-custom-domain`. **"Gone on Ctrl+C"**: the URL lives only while the command runs.
Prerequisites: an admin must enable **Peer Expose** in account settings; up to **10 active sessions per peer**.
→ This is our bonus approach 4 (lifecycle-bound URLs): tie one `netbird expose <live-view port> --with-pin` to each
agent pod's lifetime and put the URL in `status.json.live_view_url`.

### Bonus challenge: Zero-Port Access (add-on to Challenge 1 or 2)
1. No open ports: the public demo URL goes through the NetBird reverse proxy, with no inbound app ports on the VM.
2. Gated access: SSO, password, PIN or header auth tied to a user role.
3. Peer-to-peer: machines reach each other directly over WireGuard.
(The official clarification adds 4, lifecycle-bound URLs; any one qualifies, combining strengthens, show it in the
demo or README.)

### Deployment options
- **NetBird Cloud (our choice):** free up to 5 users / 100 machines; P2P, social SSO + MFA, access policies, private
  DNS and routes, NetBird SSH. `app.netbird.io` → `curl -fsSL https://pkgs.netbird.io/install.sh | sh` → `netbird up`.
- **Self-hosted on Vultr (stretch):** Marketplace app (shared CPU, ≥ 2 GB RAM), email + domain, DNS A record
  `netbird → server IP` and CNAME `*.netbird → netbird.example.com`; the image bundles Management, Signal, Relay,
  dashboard with built-in IdP, NetBird Proxy, Traefik + Let's Encrypt, CrowdSec. Tip: reserve the IP first.

Resources: app.netbird.io · docs.netbird.io · github.com/netbirdio/netbird · vultr.com/marketplace (search NetBird).
