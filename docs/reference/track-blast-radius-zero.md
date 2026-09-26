# Track: Blast Radius Zero — Safe Agent Execution on Vultr (official challenge text, condensed)

We compete in this track. This file quotes and condenses the official challenge description and maps each
requirement to our design. It's the checklist every slice, brief and demo beat must satisfy.

> Build a web-based agent that performs real work (writing and running code, or operating a real browser)
> with every action contained inside a sandbox running on Vultr. Your system should act as a centralized
> control layer that plans a task, dispatches it to an isolated execution environment, and returns verifiable
> output. Multi-step agentic workflows, real executed results rather than described ones, and a production-style web
> application, all on Vultr. "An agent that only chats is a demo; an agent that executes safely is a product."

## Execution patterns (we do both)
- **Pattern B — Sandboxed Browser Use (primary):** goal → model plans → Playwright in a container navigates,
  clicks, types → screenshots return → **a vision model on Serverless Inference verifies the result** → repeat,
  **with a human approving anything final**. The track's own example **"Research with Receipts"** (every claim links to a
  screenshot of its source) is exactly our evidence model.
- **Pattern A — Sandboxed Code Execution (secondary):** model writes code → runs in a container → real output →
  on error, **stderr is fed back for a retry**. Our instance: `code.write/test/promote/repair`, where the agent writes
  an extractor for a source, runs it in the sandbox against stored captures, reads the failures, patches and retries,
  and promotes it to a crystallized macro. The retry loop must be **visible on screen** ("Self-Healing Runner").

## Requirement → our answer → status
| Requirement | Our answer | Status |
|---|---|---|
| VM-based backend on Vultr (mandatory) | VX1 control plane (FastAPI engine + app) + VX1 sandbox host | control VM up; sandbox VM pending |
| Agent LLM calls via Vultr Serverless Inference (mandatory) | All agent reasoning on Vultr (`generated_by.backend=vultr`); Jev is only a supporting typed-decision helper, allowed per judges | live ✓ |
| Vultr = central control and orchestration | Engine plans on Vultr models, dispatches pods to VM #2, optional throwaway instance per risky task via the Vultr API | partial |
| Sandboxes never in the app process | Docker + gVisor pods on a separate VM; internal network + egress proxy | local ✓, VM pending |
| **Process isolation** | gVisor `runsc`, no host mounts, internal network | local runc ✓ → runsc on VM |
| **Secret hygiene: no API keys or credentials inside the sandbox** | Pods get **zero** secrets; every LLM call is made by the control plane; containers can't reach the metadata IP (cloud-init) or the NetBird mesh. A proof probe asserts the pod env/fs has no keys | **must add probe** |
| **Resource limits: time and memory caps on every run** | Per pod: `--memory`, `--cpus`, `--pids-limit`, wall-clock timeout, max steps; recorded in `jobs.jsonl` | **must add + show** |
| **Lifecycle discipline: reset/destroy after each task** | Pod destroyed per job (teardown checkpoint); throwaway VX1 per high-risk task (optional) | pods ✓; instance tier optional |
| Vision model verifies each browser step (Pattern B) | `qwen3.8-27b` (verified live) judges the post-action screenshot against the step goal = the termination predicate; Jev may pre-screen | **must add** |
| Human approves anything final | Read-only by design (no irreversible actions); an **approve-before-submit gate** blocks any non-SAFE/LOW action and asks the approver in the app; the PRD/factors/ontology checkpoints are approvals too | gate UI **must add** |
| Public web app, product-style, clear user flows | Proveedor Abierto app (dossier, run view, journal) behind NetBird reverse proxy with role gating | built; deploy pending |
| REST/WebSocket for run status + streaming | Live run feed (§4b) + app SSE/polling | ✓ (feed) |
| Dashboards: execution loop, retries, screenshot trails | `/run`: step stream with modes, escalations, repairs, thumbnails, proof | ✓, add vision verdicts + retries |
| **Containment moment in the video** (sandbox absorbs rm -rf, infinite loop or hostile page) | Hostile page: prompt injection + attempts to reach the metadata IP or other domains → BLOCKED and flagged; Pattern A: an extractor that runs `rm -rf /` or loops forever → killed by the timeout/memory cap, host untouched | **must script + record** |
| GitHub repo with setup + docs | github.com/ricalanis/ontofill, github.com/ricalanis/proveedor-abierto | public ✓; setup docs to finish |
| Public demo URL | NetBird reverse-proxy URL (investigator), zero open ports | pending VM |
| Recorded demo video | 1-min plan in proveedor-abierto `docs/demo/` | planned |

## Recommended reading (official)
- Agent sandboxing on Vultr: https://docs.vultr.com/how-to-set-up-agent-sandboxing-on-vultr-cloud-compute
- Serverless Inference: https://docs.vultr.com/products/serverless/inference/provisioning · models: https://api.vultrinference.com/v1/models
- Vultr API (throwaway-instance pattern): https://www.vultr.com/api/
- Suggested sandboxes: OpenSandbox, gVisor, E2B, Microsandbox. Ours: gVisor.
