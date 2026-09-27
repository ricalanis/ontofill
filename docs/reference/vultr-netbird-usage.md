# How Ontofill uses Vultr and NetBird

Everything runs on Vultr, and NetBird is the only way in or through. This describes the live deployment (Sat
2026-09-26). Resource IDs, keys and personal device names are deliberately left out.

## Vultr: where it runs and what it thinks with
| Piece | What it does |
|---|---|
| **Control VM** (VX1, ewr) | Engine, inference gateway, browser controller (MCP), cells service, Ontofill Console, Proveedor Abierto product, Postgres (silver) |
| **Sandbox VM** (VX1 4 vCPU, ewr) | The disposable gVisor (`runsc`) cells: Chromium "hands" + egress allowlist proxy (+ optional Skyvern "brain"); reached only from the control VM, over Docker-over-SSH through NetBird |
| **Serverless Inference** | Every model call, always through the gateway (the only holder of the real key; the engine and cells get budget-capped tokens). Roles: `glm-5.3` plans and drafts (PRD, ontology), `minimax-m3` critiques (another model family), `qwen3.8-flash-next` extracts and picks the next action, `qwen3.8-27b` verifies screenshots (vision), `nemotron-3.5-content-safety` screens page text |
| **Object Storage** | The lake's bronze layer: screenshots, HTML, files and site graphs, content-addressed (`bronze/sha256/<hex>` + a metadata sidecar) |
| **Vultr API** | Provisioning (VMs, VPC, firewall, object storage) and the project-wide spend tracker (budget alerts at 50/75/90% of the $200 credit) |
| **Private VPC** | The control ↔ sandbox path (NetBird P2P over it, ~0.5 ms) |

## NetBird: the network and the lock
| Piece | What it does |
|---|---|
| **Mesh (WireGuard)** | Joins the admin workstation and both VMs; the VMs connect peer-to-peer |
| **Zero public ports** | Neither VM exposes any port, SSH included (verified from outside); all administration goes over the mesh |
| **One-way policies** ("fence in your agents"; the default all-to-all policy is OFF) | admins → VMs; control → sandbox TCP 22 only; sandbox → control TCP 8700 only (the gateway, for Skyvern brains). The sandbox can't reach the admin workstation or control's SSH |
| **Reverse proxy** (Cloud, `*.eu1.netbird.services`, automatic TLS) | The only two public entry points: the **Ontofill Console** (SSO, group `approvers`) and the **Proveedor Abierto** product (password) |
| **Identity** | Console approvals are allowed only when the proxy's `x-netbird-groups` header proves the `approvers` group. Requests that don't come through the proxy are refused. NetBird Cloud's public services forward the group but not the user, so the approver's name is self-declared and labeled as such |
| **`netbird expose` per session** | Each browser session gets its own public live-view URL (read-only JPEG frames, token-gated). The process is killed at session close, so the URL itself disappears with the cell |

## What stays outside Vultr (and how it's fenced)
- **Jev** (typesafe.ai): quick typed checks, called by the gateway only.
- **Tavily**: lead-only web search, called by the engine on the control VM, restricted to the authority policy's
  domains and country.
- **Wikidata / CKAN catalogs**: public APIs used as lead providers. CKAN moves into the sandbox (gap R17).
- **GitHub**: the two public repos (code only; no data, no secrets).
None of these is ever called from a cell, and no cell holds a key.
