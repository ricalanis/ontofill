# Ontofill + Proveedor Abierto: technical architecture (v1, 2026-09-26)

This is the canonical architecture. It supersedes the architecture sketch in `ontofill-plan.md` where they differ.
`01-engine-definition.md` defines *what* the engine is; `02-anticorruption-definition.md` defines the reference case;
this document defines *how the system is built and runs*. The acceptance bar is
`../reference/track-blast-radius-zero.md`. Interfaces between the repos are pinned in the orchestration contract.

**One line:** a control plane that plans on Vultr models and dispatches disposable sandboxes, turning an open
question into a verified, evidence-backed dataset and an investigation app.

```
                 Judges / users ──HTTPS──▶ NetBird reverse proxy (PIN: investigator · SSO: approver)
                                                  │ WireGuard (zero open ports)
 ┌──────────── VX1 #1 · CONTROL PLANE ────────────▼─────────────────────────────────┐
 │ Proveedor Abierto app (reads gold export; dossier, run view, journal, approvals) │
 │ Ontofill engine: P1 scope → P2 ontology → P3 fan-out → P4 local scoping → P5 exec│
 │   ├─ decision interface ─▶ Inference gateway ─▶ Vultr Serverless Inference      │
 │   │                          (only real key · per-session tokens · Jev + safety) │
 │   ├─ Controller (MCP): sessions, cell pool, native loop / Skyvern backends       │
 │   ├─ Refiner: silver → SHACL → gold · metrics · DoD                             │
 │   └─ Postgres + Oxigraph (silver/gold graphs)                                    │
 └──────────────────────────┬───────────────────────────────────────────────────────┘
          CDP / cell API (control → sandbox only, NetBird + VPC)
 ┌──────────── VX1 #2 · SANDBOX HOST (zero secrets) ▼───────────────────────────────┐
 │ cell-1 [Skyvern brain? ─CDP─ gVisor Chromium hands ─ egress allowlist proxy]     │
 │ cell-N  … caps: mem/cpu/pids/timeout · destroyed after every session             │
 │ (optional: a throwaway VX1 per high-risk cell)                                   │
 └──────────────────────────┬───────────────────────────────────────────────────────┘
                            ▼
              Vultr Object Storage = bronze (raw captures, content-addressed)
```

## 1. Definition: turning a question into a contract (P1–P2)
- **Brief → PRD** (Vultr inference): personas, jobs to be done, DoD as testable criteria, constraints, and an
  **authority policy** (which publishers count as official for this case's jurisdiction). **Human checkpoint.**
- **PRD → ontology**, Simula-style (`01-engine-definition.md` "Phase 2 method"): factors of variation → taxonomies
  (Best-of-N proposals + a critic from a different model family) → classes, properties, relations, the primary class,
  DoD fields, SHACL rules and signal rules, and alignment (schema.org / Wikidata / a domain standard) → DoD compiled
  to queries. **Human checkpoints** for factors and ontology.
- **Generic by construction:** no domain vocabulary in engine code (a guard test enforces it). A different brief
  yields a different ontology, sources and extraction with zero code changes (a second-brief test proves it).

## 2. Discovery: where the data lives (P3)
- Ontology gaps become targeted queries. Keyless providers: model-proposed candidates, Wikidata official websites,
  open-data catalogs, and web search when not blocked. **A candidate is only a lead; a sandbox capture must confirm
  it.** The authority check applies the approved policy; anything else goes to human approval.
- Output: ranked (source, objective) pairs, each tagged `discovered_by`. Optional site-graph spider sessions run
  on the same cells.

## 3. Local scoping: how to extract from each source (P4)
- A **technical definition document (TDD)** per (source, objective): target properties, the path through the site,
  the method (file / DOM / vision), validation, rate limits, budget, allowed domains, and the **execution-mode range**
  (e.g. start D0, may escalate to S1, never S2). Requester ⇄ source-engineer negotiation when time allows.

## 4. Execution: doing the work in sandboxes (P5)
| Mode | What runs | Where |
|---|---|---|
| **D0** | download + parse (CSV/XLSX/PDF/API), no model | cell or control plane (read-only fetch through egress) |
| **D1** | a crystallized script; the model only maps, parses or repairs fields | cell |
| **S1** | the native agent loop: observe → **Jev gate** → plan (Vultr tool call) → **Jev action guard** → act → **Jev check** → **Vultr vision verify** | native cell (gVisor Chromium hands) |
| **S2** | Skyvern for hard navigation, one unmodified Skyvern brain per cell | Skyvern cell |
- **Escalate** on failed termination predicates, stale bindings, SHACL failures, drift or low yield; **stop** on
  captcha or login. **Crystallize** (Pattern A): write → test on stored captures → read the failure → patch → retry →
  promote a versioned macro; next time it runs at D0/D1. The retry loop is visible in the run view.
- Every step logs **observed / requested / executed / evaluated**, plus mode, backend (vultr | jev | recorded) and cost.

## 5. The browser layer: controller + cells + inference gateway
- **Controller** (MCP service on the control plane): `session.open(tdd, allowed_domains, limits)` →
  `{session_id, live_view_url}`, `act`, `observe`, `close`; a session broker; a warm pool of cells; **recycle = destroy
  + recreate after every session**. Backends: `native` (primary, concurrent) and `skyvern` (S2, one per cell).
- **Cell** = **hands** (gVisor Chromium pod, egress allowlist proxy, memory/cpu/pids/timeout caps, zero secrets) +
  optional **brain** (upstream Skyvern containers, unmodified, holding only a session token). Each cell has its own
  internal network: brain → hands over CDP inside the cell; hands egress only via the allowlist; brain egress only to
  the gateway; no access to the metadata IP, the mesh or other cells. Optional placement: a throwaway VX1 per cell via
  the Vultr API ("Vultr itself as the sandbox fabric").
- **Inference gateway** (control plane): an OpenAI-compatible proxy in front of Vultr. It holds the **only real key**,
  issues per-session tokens (TTL, $ budget, revoked at close), attributes every call to a session and step for spend,
  and runs **Jev + the Vultr content-safety model on page content inside prompts before forwarding**, which gives a
  per-step hook even inside Skyvern's own loop.

## 6. Refinement: from raw to trusted
- **Bronze:** immutable, content-addressed captures (HTML, accessibility tree, screenshots, files, site graphs) in
  Vultr Object Storage. **Silver:** typed observations with evidence; duplicates and conflicts kept. **Gold:**
  reconciled, SHACL-valid values; a double critic on high-stakes fields.
- Entity resolution uses identifiers compared in code plus model confirmation. Metrics and DoD are computed on gold;
  gaps loop back to discovery. An ontology or PRD change **re-refines gold from bronze without re-browsing**.
- Gold export (generic): `entities.jsonl` + `ontology.json` + `trace.jsonl` + `jobs.jsonl` + `metrics.json`
  (with `dod[]`), plus a live run feed during execution.

## 7. The application (Proveedor Abierto)
- Reads only the gold export and live feed; its domain comes from the case's ontology (primary class, labels, rules).
- Dossier per entity with evidence one click away; red flags as explained signals (not accusations, with a dispute
  path); relationships; case journal (any value → step → TDD → objective → ontology → PRD → brief); watchlist and
  open exports (OCDS/CSV/RDF).
- The live run view shows steps, modes, verdicts, retries, screenshots, the six proof checkpoints, and a "decided by
  code / Jev / Vultr" cost story. The approver screens handle PRD, factors, ontology and approve-before-submit
  actions. `/spend` shows the budget. Replay mode runs a cached run as demo insurance.

## 8. Safety model (the track's core)
- **Isolation:** gVisor `runsc`, a network isolated per cell, egress allowlist, resource caps, destruction after every session.
- **Secrets:** the real key lives only in the gateway. Hands hold nothing; brains hold a session-scoped, budget-capped,
  revocable token. *No long-lived credential in any sandbox.*
- **Read-only archetype:** SAFE/LOW actions only (navigate, search, filter, paginate, download); anything else pauses at
  the **approve-before-submit gate**; captcha or login = stop and flag; nothing is bypassed.
- **Proof per job (six checkpoints):** host (runtime, KVM), the task's real result, hostname/uname from inside, an
  isolation probe = BLOCKED, teardown, secrets (0 keys; metadata + mesh BLOCKED).
- **Containment moments for the demo:** a hostile page (prompt injection plus exfiltration attempts) is flagged,
  quarantined and BLOCKED; an extractor running `rm -rf /` or an infinite loop is killed by the caps with the host untouched.
- **Zero-port access (NetBird):** the public URL goes through the reverse proxy with role gating; zero inbound ports on both
  VMs (SSH included); VM↔VM over WireGuard with one-way policies ("fence in your agents"); per-cell live-view URLs via
  `netbird expose` that die with the cell.

## 9. Observability and cost
- The run feed streams to the app; the gateway logs every model call with session/step attribution; `tools/spend`
  tracks the $200 credit (alerts at 50/75/90%). Jev's savings (vision calls avoided) appear in the run view.

## 10. Where things live
| Where | What |
|---|---|
| `github.com/ricalanis/ontofill` | generic engine, `ontofill-scrape` toolkit, schemas, sandbox/cell substrate, infra (Vultr, NetBird), `services/browser-agent` (controller + gateway + backends) |
| `github.com/ricalanis/proveedor-abierto` | case package (brief → PRD → ontology → TDDs → macros, produced by the engine and approved by humans), the app, deploy |
| Vultr (outside git) | VMs, Object Storage (bronze), Postgres + Oxigraph (silver/gold), Serverless Inference |
| NetBird Cloud | peers, groups, policies, reverse-proxy services, expose sessions |

## 11. Model roles (live-verified on Vultr, see `../reference/vultr.md` §2.10)
| Role | Model / backend |
|---|---|
| Planning, PRD, ontology generation | `glm-5.3` |
| Critic (different family) | `minimax-m3` / `qwen3.8-27b` |
| Typed decisions | `glm-5.3-flash` (forced tool, minimal reasoning); Jev pre-screen where it fits |
| Extraction / agent next-action | `qwen3.8-flash-next` |
| Vision verification | `qwen3.8-27b` |
| Injection / content safety | Jev (one-way pre-screen) + `nemotron-3.5-content-safety` (custom policy) |
| Skyvern cells | via the gateway, OpenAI-compatible (`qwen3.8-flash-next` primary; vision per smoke test) |

## 12. Build order (revised 15:45 Sat)
1. Genericity refactor + sandbox VM + Object Storage → 2. inference gateway + controller + native backend + cell
substrate → 3. **first live run of the real case with human approvals (~20:00)** → 4. completeness push (DoD on
live inference) + discovery + Skyvern cells → 5. deploy app behind NetBird + containment scenarios + bonus evidence
(overnight) → 6. harden, cached full run, video, freeze Sun 10:00, submit by 12:00.

## 13. Phase loops and the outer gap loop (user decision, 16:26 Sat)
The harness stays deterministic (code owns phase order, budgets, stop rules and human gates), but each phase is a
**bounded loop**, not a one-shot call, and the run as a whole is a loop. The model decides *within* typed options; it
never owns control flow.
- **One skeleton, reused by every phase:** `gather → propose (Best-of-N) → critique (other model family) → revise →
  check (the phase's exit criteria compiled to code) → human gate`. Stop when the checks pass and the critic has no
  blocking objection, or when the phase budget (iterations, $, wall-clock) runs out; then present with open issues listed.
- **P1:** gather = a research ledger (public prior art, datasets, legal frame) from lead-only search confirmed by
  sandbox capture; propose PRD; critic checks invented targets, feasibility against budget, brief coverage; DoD
  criteria carry `basis: brief|human|proposed`.
- **P2:** already Best-of-N + critic (Simula); add grounding: proposed properties must be observable in sampled
  bronze captures, and every PRD DoD field must be covered.
- **P3:** discovery loop: ontology gaps → queries → leads → capture-confirm → authority check → re-rank, until each
  DoD field has ≥ 1 confirmed candidate source (≥ 2 for high-stakes fields) or budget.
- **P4:** requester ⇄ source-engineer negotiation (two roles, ≤ N rounds) → TDD, validated by a dry run on one capture.
- **Outer gap loop (the run itself):** after P5 → refine → gold metrics vs DoD → gap analysis → a typed decision:
  reopen P3 (new sources), P4 (renegotiate a TDD), P2 (ontology recommend, human gate) or stop. Bounded by the run
  budget; every iteration is a trace event, so the run view shows the engine arguing with itself and closing gaps.
- Human denials feed the same loop as `human` revisions (CONTRACT v0.9.5); the loop never overrides a human basis.
