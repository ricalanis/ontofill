# Ontofill: any open question → an evidence-backed dataset, run safely on Vultr

Ontofill turns an open brief into a reviewed PRD and definition of done, an ontology,
source objectives, scoped extraction plans, and a dataset whose values link back to
captured public evidence. Vultr inference makes the typed decisions; isolated sandbox
jobs capture sources; the lake keeps raw evidence, observations, and validated exports.
The engine accepts a **case** from an application repo and runs the same five phases for
different subjects. [Proveedor Abierto](https://github.com/ricalanis/proveedor-abierto/tree/main/case)
is the reference case used to exercise the engine.

**For judges:** [Live, read-only Ontofill Console](https://ontofill-console-judges.eu1.netbird.services).
The password is linked from [Proveedor Abierto's For judges section](https://github.com/ricalanis/proveedor-abierto#for-judges),
which also states where each case stands.

## What is proven live (Sun 27 Sep)

- **Two Vultr VMs, one boundary.** The control VM plans every phase on Vultr Serverless Inference and dispatches
  work; the sandbox VM runs each job in a gVisor (`runsc`) cell with memory, CPU, process and time caps, and
  destroys it afterwards. Every job reports six proof checks: host check, task result, where it ran, isolation
  probe (BLOCKED), teardown, and secret hygiene (0 keys in the cell; cloud metadata IP and mesh BLOCKED).
- **One key, outside the sandbox.** Only the inference gateway on the control VM holds the Vultr key. It issues
  per-session tokens, attributes every call to a run and step, and screens captured page text before it reaches a
  model. The console's Inference view shows each call and its provider.
- **Containment.** A hostile page (prompt injection plus attempts to reach the metadata IP) is quarantined and
  flagged, and an extractor that runs `rm -rf /` and then loops forever
  (`sandbox/fixtures/destructive_loop.py`) is killed by its cell's limits with a host sentinel intact
  (`tools/containment`, recorded run in the console).
- **Pattern B vision verification.** A Vultr vision model (`qwen3.8-27b`) judges a sandboxed browser cell's
  screenshot against the step goal: proven live in a controller session. The case runs so far used document and
  page capture, so their traces show no vision steps yet.
- **Pattern A, self-healing extractors.** The engine writes an extractor and runs it in a networkless cell against
  stored captures. Live runs show both halves: a passing extractor promoted to a versioned macro, and a failing one
  whose output diff is fed back for a patch and retry, then escalated to the browser after the attempt cap.
- **People only approve.** A run stops at the PRD, factors, ontology and source-review checkpoints. Each decision in
  the console is bound to the sha256 of the exact artifact reviewed and appended to the case's decision log; a deny
  with a reason makes the engine redraft. The runner service resumes the run with no operator in the loop.
- **Generic by construction.** The same code runs the Mexican procurement case and an unrelated San Francisco
  library case, each from a one-paragraph brief.
- **Honest gap.** The reference case has **no engine gold yet**: its primary procurement portal needs a data API
  that the sandbox egress allowlist refused, and the source critic accepted none of the other candidates. The
  allowlist now admits that portal's own API and CDN hosts (`378a8c0`), and the run resumed on it. The portal now
  renders inside the sandbox. Only a trusted publisher's sibling hosts were added, GET only; third-party hosts stay
  blocked and POSTs are refused, so the blast radius stayed zero while egress widened. The engine run
  continues after submission; its status is live on the judges console, with every run, review and failure.
- **Building in the open.** A simpler second case (San Francisco library branches) surfaced five engine defects
  that the hard case had hidden. Three are fixed with tests: city-level jurisdictions in P1, invalid ontology rules
  set aside with salvage in P2, and exhausted phases reported as needs-human instead of a crash. Two are found and
  queued: rules whose label does not match their predicate, and a core PRD field that is still not bound to a
  property (a first fix shipped in `378a8c0` but did not trigger on the rerun).

## How it works

The engine turns a question into a reviewed PRD, an inferred ontology, confirmed sources,
bounded collection, and linked data with evidence for each value. See
[From an open question to linked data](docs/planning/04-question-to-linked-data.md)
for the steps and the artifacts they produce.

## The engine in three diagrams

**1. Global: two Vultr VMs, one boundary.** People reach the system only through the NetBird proxy. The control
plane plans on Vultr Serverless Inference through the gateway, which holds the only key, and dispatches jobs one way
to gVisor cells that hold no secrets.

```mermaid
flowchart TB
  people["People<br/>judges: password · approvers: SSO"] -->|HTTPS| nb["NetBird reverse proxy<br/>TLS + auth · zero inbound ports"]
  nb -->|WireGuard| cp
  subgraph cp["VX1 #1 · control plane"]
    engine["Engine P1–P5 + runner"]
    gw["Inference gateway<br/>only Vultr key · per-session tokens · page-text screening"]
    ctl["Browser controller"]
    ui["Console + product"]
    db[("Postgres + Oxigraph<br/>silver · gold")]
    engine --> gw
    engine --> ctl
    engine --> db
    ui --> db
  end
  gw -->|every LLM call| vsi["Vultr Serverless Inference"]
  ctl -->|dispatch over NetBird · one way| sb
  subgraph sb["VX1 #2 · sandbox host · zero secrets"]
    cell["gVisor runsc cell<br/>Chromium or parse job<br/>mem · CPU · pids · time caps<br/>destroyed after every job"]
    proxy["Egress allowlist proxy<br/>GET only"]
    cell --> proxy
  end
  proxy --> web["Public web<br/>allowlisted publisher hosts"]
  engine -->|raw captures| bronze[("Vultr Object Storage<br/>bronze, content-addressed")]
```

**2. The council: how the engine decides.** Every phase runs the same bounded loop. A planner proposes, a critic
from another model family objects, and code checks the exit criteria. A person approves or denies with a reason, and
that reason becomes a human revision the loop can never override. The models choose within typed options; code owns
the order, the budgets and the stop rules.

```mermaid
flowchart LR
  g["1 Gather<br/>research ledger · leads · sandbox captures"] --> p["2 Propose<br/>planner glm-5.3, best of N"]
  p --> c["3 Critique<br/>critic from another family<br/>minimax-m3 / qwen3.8-27b"]
  c --> r["4 Revise<br/>answer each objection"]
  r --> k{"5 Check in code<br/>exit criteria pass?<br/>no blocking objection?"}
  k -->|not yet, within budget| c
  k -->|yes| h["6 Human gate<br/>approve · bound to the file's sha256"]
  h -->|deny + reason| p
  h -->|approve| next["Next phase"]
  s["Safety screen<br/>page text screened before any model;<br/>flagged pages quarantined"] -.-> p
  s -.-> c
```

The same loop drafts the PRD (P1), the ontology (P2), judges sources (P3), writes each per-source plan (P4), and
runs the gap loop after gold.

**3. Extraction: from a plan to gold with receipts.** Each (source, objective) gets a plan. Execution starts at the
cheapest mode and climbs only when a check fails. Bronze is kept, so a changed ontology re-refines gold without
browsing again.

```mermaid
flowchart LR
  plan["Plan per source<br/>properties · path · allowed domains · mode range"] --> cell
  subgraph cell["Execute in a gVisor cell · zero secrets"]
    d0["D0 download + parse"] --> d1["D1 macro"] --> s1["S1 agent loop<br/>+ Vultr vision verify"] --> s2["S2 Skyvern"]
  end
  cell --> bronze[("Bronze<br/>raw captures")]
  d1 <-->|failing extractor| pa["Pattern A: write → test in a networkless cell<br/>→ stderr fed back → patch → promote to a macro"]
  bronze --> silver[("Silver<br/>observations + evidence<br/>conflicts kept")]
  silver --> refine["Refine<br/>reconcile · SHACL"] --> gold[("Gold<br/>values with receipts")]
  gold --> dod{"DoD met?"}
  dod -->|gap| reopen["Reopen P3 sources · P4 plan · P2 ontology (human gate)"]
  reopen -.-> plan
  bronze -.->|ontology changed: re-refine, no re-browse| refine
```

## Runs on Vultr and NetBird

Vultr provides inference, the control and sandbox VMs, and the evidence lake. NetBird
connects the VMs and provides access without public inbound ports. See
[How Ontofill uses Vultr and NetBird](docs/reference/vultr-netbird-usage.md)
for the deployed layout and network boundaries.

Tracked files contain code and documentation only. Recorded previews use the ignored `.cache/`
directory for a scratch case, local bronze objects, and silver observations.

| Folder | What goes there |
|--------|-----------------|
| `src/ontofill/` | The engine package: phases, executor, refiner, inference, lake adapters, sandbox control |
| `packages/ontofill-scrape/` | Resilience-first scraping toolkit, separately usable (tool contract, failures, policies) |
| `sandbox/` | Browser/spider pod images (Docker + gVisor), egress policy |
| `infra/` | Vultr VMs, Object Storage, Postgres, Oxigraph, NetBird (zero open ports) |
| `schemas/` | JSON Schemas for the case-package file formats the engine reads/writes |
| `tests/` | Unit tests and replay tests on stored captures |
| `docs/` | Definition docs (copied from planning) |

License: Apache-2.0. See `docs/planning/01-engine-definition.md`.

## Built during the event

Built during the Vultr Agent Arena (Sat 11:30 → Sun 12:00 PT). Before the event this repository held the
definition documents and an empty package scaffold (`a648775`, Sat 13:31). Everything else was built during the
event, in the granular public [commit history](https://github.com/ricalanis/ontofill/commits/main)
(about 400 commits; the suite runs 720 tests on `main`). By work block:

| When (PT) | What was built | Example commits |
|-----------|----------------|-----------------|
| Sat 13:31–14:13 | Contract, file and S3 lake adapters, the scrape toolkit, live run feed, sandbox job proof feed | `56f7324`, `571aaae`, `a7e2cd1` |
| Sat 14:53–15:51 | Live Vultr typed decisions with an independent critic, ontology-driven refine and export, the native gVisor browser cell | `5c44202`, `2818822`, `6076a6d` |
| Sat 16:03–18:15 | Vultr provisioning, isolated Skyvern cell, checkpoint deny steering, PRD and gateway proof checkpoints, the engine on the screened gateway token | `dd811a8`, `62126e8`, `1ef6e4d` |
| Sat 18:48–20:56 | P5 connected to the browser controller, teardown proof, containment command, runner service with kill switch, console Inference and Summary views, honest taxonomy metrics, screening of captured content | `7774775`, `a86cb3c`, `fdc8dd7`, `bf8d9b2` |
| Sat 21:10–Sun 03:47 | Sandbox document parsing, stable digest-bound approvals, source-review checkpoint, direct document parsing in P5, reviewed download gate, jurisdiction guard | `e2429dc`, `91d2c82`, `fd9ce1f`, `e76d552` |
| Sun 04:46–10:24 | Fixes driven by the real case's live runs: budget and wall-clock accounting, wide authority policy, entity anchors and query rotation for discovery, the document profiler, SPA request capture, P2 rule salvage | `936d0cc`, `b217f02`, `933ce27`, `0474139`, `f9a3fb7` |

Each live failure on the real case was filed with its evidence, fixed in code with a test, deployed in a window
with no engine running, and rerun. Nothing in a run was edited by hand.

## Try a second brief through the ontology checkpoint

This uses the unrelated public-library brief in `tests/genericity`. Run these commands
from the Ontofill repo root with `ONTOFILL_GATEWAY_TOKEN` and the gateway's
`VULTR_INFERENCE_BASE_URL` in the ignored `.env`.
The explicit `run-` ID rejects a recorded fallback. All case files and lake objects go
under a new ignored `.cache/` directory; this does not touch the reference case.

```sh
mkdir -p .cache
demo_root="$(mktemp -d "$PWD/.cache/library.XXXXXX")"
mkdir -p "$demo_root/case"
cp tests/genericity/cases/libraries/brief.md "$demo_root/case/brief.md"
demo_run="run-library-$(date +%s)"
run_library() {
  LAKE_ROOT="$demo_root/lake" SILVER_DATABASE_URL= OXIGRAPH_URL= \
    uv run ontofill run "$demo_root/case" --to-phase 2 \
      --run-id "$demo_run" --budget-usd 1.00
}
run_library
```

The first call stops at `01-scope/APPROVAL_PENDING.md` with exit code 3. After a human
reviews and accepts `prd.json` and its DoD basis, this scratch-only helper writes an
approval bound to the exact file bytes:

```sh
approve_reviewed() {
  uv run python - "$demo_root/case" "$1" "$2" "$demo_run" <<'PY'
import hashlib, json, sys
from datetime import UTC, datetime
from pathlib import Path
root, relative, checkpoint = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
artifact = root / relative
marker = {
    "approver": "Local reviewer", "date": datetime.now(UTC).date().isoformat(),
    "checkpoint": checkpoint, "identity_source": "local", "run_id": sys.argv[4],
    "artifact_sha256": {relative: hashlib.sha256(artifact.read_bytes()).hexdigest()},
}
(artifact.parent / "APPROVED").write_text(json.dumps(marker) + "\n", encoding="utf-8")
PY
}
approve_reviewed 01-scope/prd.json prd
run_library
# Review factors.json before continuing:
approve_reviewed 02-ontology/factors/factors.json factors
run_library
```

The final call stops at `02-ontology/APPROVAL_PENDING.md` with a live
`ontology.json` ready for review. Keep the same `demo_root` and `demo_run` values.
A denial with a reason regenerates the rejected artifact on the next call; it never
skips a checkpoint. See [the approval schema](schemas/approved.schema.json).

## Run the reference case

```sh
uv sync
uv run ontofill run ../proveedor-abierto/case
```

With no Vultr inference credentials, `run` uses a clearly labeled recorded decision double.
It generates a `mock-` run in `.cache/case-mock/`, returns exit code 3 for outstanding human
checkpoints, and never advances the case's latest pointers. Source URLs are discovered at
runtime from the case brief; values are emitted only from observed public cells.

With `ONTOFILL_GATEWAY_TOKEN` and `VULTR_INFERENCE_BASE_URL` set in the ignored
`.env`, the engine selects a live Vultr model through the screened gateway,
regenerates recorded artifacts, pauses for PRD, factors, and ontology approvals,
and resumes on the next `run`. The application owns its case directory and approval
markers. `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` enable Vultr Object Storage
through the S3 lake adapter.
