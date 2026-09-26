# Computer-use ontology — portable summary

A self-contained summary of the computer-use taxonomy used in `computer-use-graph` and
`computer-use-lab`. Condensed from `computer-use-graph/knowledge/01-computer-use-taxonomy.md`,
`02-action-space-ladder.md`, `04-state-abstraction-and-site-graphs.md`, `11-glossary.md`,
`docs/02-design-v0.2.md` and `computer-use-lab/docs/taxonomy-map.md` (snapshot 2026-09-26).

All figures come from public papers as recorded in those notes. Numbers marked ✓ were re-checked
against the primary source. Re-verify unmarked numbers before citing them externally.

---

## 1. Top-level structure

Computer use is not one problem. It breaks down into:

- **Three orthogonal design axes:** A (observation), B (action space), C (control loop)
- **Five sub-problems** that often get lumped together
- **One cross-cutting process:** exploration, which fills the memory some axes rely on
- **Five task archetypes.** The same technique can pay off very differently from one to the next.

```
                ┌─ A  Observation   (what the model sees)
Design axes ────┼─ B  Action space  (what the model requests → what the harness executes)
                └─ C  Control loop  (how decisions are organized)

Sub-problems:   grounding · state estimation · verification · memory/transfer · safety
Process:        exploration (inference-time vs build-time)
Archetypes:     info-seeking · transactional · long-horizon multi-app · authoring/config · routine
```

---

## 2. Axis A — Observation

| Mode | Strength | Weakness |
|---|---|---|
| **Pixels** (screenshot only) | Works on anything rendered: canvas, PDF, remote desktop, native apps | Expensive per step; visual grounding is weak |
| **Text structure** (DOM / accessibility tree) | Cheap, precise, enumerable | Brittle on custom widgets; pruning loses information; the a11y tree can list off-screen or unreachable nodes |
| **Hybrid / set-of-marks** | Screenshot with numbered overlays; the model reasons visually and acts on an index | Still resolves to element-targeted execution underneath |
| **Programmatic** (API / SDK / MCP) | Fastest and most reliable where it exists | Arguably not computer use; needs an integration per app |

Rules:
- Text plus geometry is its own representation. Don't quietly give bounding boxes to a "text-only"
  agent.
- A pure-pixel agent can't emit element IDs unless IDs are exposed to it. Adding marks changes the
  observation treatment.
- API access is an integration benefit. It is not evidence of better grounding.

## 3. Axis B — Action-space ladder (8 rungs)

| # | Rung | Agent emits → executed by |
|---|---|---|
| 0 | OS input events | `click(842,316)`, `type`, `key` → xdotool/pyautogui (the real cursor moves) |
| 1 | Browser input events | coordinates → CDP `Input.dispatchMouseEvent` (tab-scoped, no real cursor) |
| 2 | Element-targeted | `click(id=42)` → Playwright/Selenium locator with actionability checks |
| 3 | DOM/JS manipulation | `el.click()`, `el.value =` → the page's own JS engine (`isTrusted=false`) |
| 4 | Accessibility actions | `AXPress(node)` → platform a11y API (AX, UIA). *Reading* the a11y tree is not B4 |
| 5 | Semantic / macro | `add_to_cart(sku)` → script that expands into rung-2 steps (an *option*) |
| 6 | Private network API | `POST /api/cart` with page cookies/CSRF → HTTP client (only observed endpoints) |
| 7 | Public API / MCP | `create_issue(...)` → documented endpoint. MCP wrapping changes packaging, not privilege |

Principles:
- **Governing tradeoff.** Moving down the ladder buys generality. Moving up buys reliability and
  speed, but every rung above 0 needs an app-specific **binding** (selector, node id, endpoint),
  and bindings go stale.
- **Grounding changes shape as you climb:** which pixel → which selector → which endpoint.
- **Agent space ≠ executor space.** `click(id=42)` still ends as a mouse event. The design choice
  is where to draw the line between what the model decides and what the harness resolves.
- **The rung is a harness setting, not a property of the stack.** Real systems mix rungs per call,
  e.g. B2 with B1 fallback, B5 with B2 recovery, B7 reads with B2 writes.
- **Code-as-action** (CodeAct) is an alternative family. The agent emits executable code instead
  of discrete actions.

### Observation × action compatibility

| Observation | B0/B1 coords | B2 elements | B3 DOM | B4 native a11y | B5 macros | B6/B7 network |
|---|---|---|---|---|---|---|
| Pixels only | Yes | Needs added IDs | Needs bindings | Needs native mapping | With disclosed bindings | With API schema |
| Text structure | Needs geometry or grounder | Yes | Yes | Needs native adapter | Yes | Yes |
| Marked hybrid | Yes | Yes | Yes | Needs native adapter | Yes | Yes |
| Programmatic only | Lacks targets | Needs UI handoff | App-specific | Needs handoff | With preconditions | Natural pairing |

## 4. Axis C — Control loop

| Family | Description |
|---|---|
| **ReAct single agent** | observe → think → act. The baseline everyone reports. |
| **Planner/executor split** | Planner emits subgoals and an executor grounds and acts. Sometimes a dedicated grounder is added. Agent-E adds before/after DOM "change observation". |
| **Reflection / retry** | Reflexion lineage: one bounded diagnosis after an observable failure, then recovery. |
| **Search over states** | Exploration at inference time: branch, backtrack, undo. Needs resets or simulation (tree search with reset+replay; ExACT's reflective MCTS; WebDreamer's LLM-as-world-model). |
| **Memory-augmented** | Persist from past episodes and retrieve. What gets stored defines the variant: NL **workflows** (AWM), **demonstrations** (WILBUR), executable **skills** (ASI, SkillWeaver, WALT, PolySkill), or a per-site **map** (Environment Maps). |
| **Trained** | SFT on synthetic trajectories (NNetNav, Explorer, Synatra) or RL on execution rewards (WebRL lineage). |

Caveat on memory: in the papers that actually ran a flat-memory baseline, flat retrieval recovered
about ⅔ of the structured system's gain (9.1 of 14.0pp ✓; 7.8 of 12.0pp ✓).

## 5. Five sub-problems

| Sub-problem | Question | Example probe | Diagnostic |
|---|---|---|---|
| **Grounding** | Which pixel, element, or endpoint does "the blue Submit button" map to? | Duplicate buttons, off-screen target, stale mark | Correct-target rate, resolution failures |
| **State estimation** | What page am I on? Did the action work? Is there a modal? | Same URL with a different cart/modal; loading vs done | State aliasing, missed transitions, repeated actions |
| **Verification** | Did the task complete, including side effects? | False success toast, wrong quantity, duplicate order | False completion, missed success. Programmatic validators under-credit; LLM judges over-credit |
| **Memory / transfer** | Can learning carry over within an episode → across episodes on one site → across sites? | New instances, UI drift, changed prices | Retrieval provenance, stale-use rate, build cost, reuse crossover. Cross-site transfer is essentially unsolved (1/48 → 0/48 ✓) |
| **Safety** | Prompt injection, policy compliance, irreversible actions | Injected instructions, forbidden write, unsafe retry | Attack success, unauthorized writes, benign false blocks |

## 6. Exploration (cross-cutting process)

Exploration is how a system deliberately wanders to gather what it will exploit later. It shows up
in two places that are easy to mix up:

- **Inference-time** (Axis C search): branch within one episode. You pay per task, and it needs
  resets because the web is "rife with irreversible actions".
- **Build-time**: crawl a site before any task to fill memory (demos, skills, graph). You pay once
  and the cost amortizes over N later tasks.

Build-time exploration fails in two known ways:
- **Safety.** "GET is safe" has no equivalent for clicks: Delete and Next are both `<button>`. The
  best published destructive-action classifier reaches ~76% recall on high-risk actions ✓, which
  makes it a gate, not a safeguard. Keep HIGH-risk edges out of exploration entirely.
- **Noisy TV.** A crawler that rewards novelty camps on unlearnable randomness: ads, timestamps,
  tokens, A/B variants. The fix is to reward **learning progress** (the reduction in prediction
  error) over type-abstracted states and stop when marginal progress drops below ε. Noisy TV is a
  failure mode, not a sixth sub-problem.

## 7. Task archetypes

| Archetype | Shape | Fit for structured (graph) memory |
|---|---|---|
| Read-only information seeking | Heavy on navigation and exploration, cheap to retry | Good |
| Transactional (forms, purchase, CRUD) | Short navigation, high precision, irreversible | May not amortize |
| Long-horizon multi-app | e.g. email → sheet → internal tool | Hard; per-site maps don't transfer |
| **Authoring / configuration** | Deep menus, settings, admin | **The most graph-shaped.** Structure beat flat memory here (GitLab +11.1pp, CMS +7.1pp ✓) |
| Repeat / routine | Same template run N times | Where amortization should dominate |

---

## 8. Site-graph ontology (the memory data model)

A two-layer **type / instance** graph whose edges are affordances, formalized as options. No
published system was found that builds exactly this. The individual assumptions are supported,
but the combination is untested.

### Nodes
- **Instance layer.** Concrete pages/URLs/states. Acts as a cache under the type layer.
- **Primary key** = (URL *template*, DOM-skeleton hash of tag + role + structural path, with
  volatile content stripped). Timestamps, prices, ad slots, and tokens are stripped before hashing
  (Crawljax-style state abstraction).
- **Embeddings are only a merge signal.** They merge over-split nodes (A/B layouts, markup churn).
  They are never the identity key, and merges are reviewed.
- **Type layer.** Built by clustering instances, and carries the union of affordances seen across
  them. This is the object that transfers. An LLM names types for display, never for identity.
- **Hidden state is not part of node identity.** Auth, cart, active filters, and open modal form
  a small factored **episode context vector** that edges check as **preconditions**. This avoids a
  node explosion.

### Edges = options (semi-MDP: initiation set, policy, termination condition)

```
edge = {
  source_type, source_instance, target_type, target_instance,
  affordance:    { role, accessible_name, structural_path },   # never coordinates
  action:        { type, params },
  preconditions: { auth, cart_nonempty, filters, modal_open }, # each nullable
  risk_tier:     SAFE | LOW | HIGH,
  reversible:    bool,
  success_prob, observed_cost,
  termination_predicate,   # verifies the option completed; doubles as a staleness probe
  last_verified, decay_policy
}
```

- **Staleness.** A failed termination predicate during normal use is a free staleness signal.
  Scheduled re-verification is only needed for low-traffic types.
- **Usage modes, strongest evidence first:** (1) context/hints mode, (2) macro mode with a binary
  per-step verifier, (3) uncertainty-gated switching, which is still the open experiment.
- **Reference stack:** the agent acts at rung 2 over an actionability-filtered a11y candidate
  set. Graph edges are rung-5 options expanding into rung-2 steps. Rung-6 reads are declared;
  there are no rung-6 writes.

---

## 9. Observability rule

Every run or lesson should record four things per step. This is what turns the taxonomy into
something you can observe rather than a list of labels:

1. what the model **observed** (Axis A channel)
2. what it **requested** (Axis B rung, in agent space)
3. what the harness **actually executed** (executor space, including primitive expansion)
4. what an **independent evaluator** found (against private ground truth, not the live UI)

---

## 10. Core glossary

- **Affordance.** An action available from a state, identified by role + accessible name +
  structural path.
- **Actionability.** Visible, stable, receives events (not occluded), enabled. Used to filter the
  candidate set.
- **Binding.** The app-specific handle a high-rung action depends on. It can go stale.
- **Grounding.** Mapping a description to a concrete target (pixel, element, endpoint).
- **Option.** (initiation set, policy, termination condition), from Sutton, Precup & Singh 1999.
  The formal model of an edge or macro.
- **State abstraction function.** The rule that decides when two observed pages are the "same
  state" (Crawljax).
- **Episode context vector.** Hidden state (auth, cart, filters, modal) that isn't part of node
  identity and is checked as an edge precondition.
- **Type layer / instance layer.** Abstract page types (transferable) over concrete pages (cache).
- **Termination predicate.** A checkable assertion that an option completed. Doubles as the
  staleness signal.
- **Learning progress.** Intrinsic reward equal to the reduction in prediction error. Robust to
  noisy TV.
- **Noisy TV.** An unlearnable random source that traps curiosity-driven exploration.
- **Amortization crossover.** The number of tasks N at which one-time graph construction cost is
  recouped.
- **Firewall.** Keeps eval-task information out of map construction (a task-agnostic crawler).
- **Validator.** Scores success. Programmatic validators under-credit; LLM judges over-credit.
- **isTrusted.** DOM event flag: `true` for events generated by the user agent, `false` for
  `el.click()`/`dispatchEvent`.

---

## 11. Key public sources

| Topic | Source |
|---|---|
| Hybrid observation, benchmark | WebVoyager — arXiv:2401.13919 |
| Self-hosted benchmark | WebArena — arXiv:2307.13854 |
| Set-of-marks | Set-of-Mark — arXiv:2310.11441 |
| Code-as-action | CodeAct — arXiv:2402.01030 |
| Action rung as config | BrowserGym ecosystem — arXiv:2412.05467 |
| Pre-LLM macros | Workflow-guided exploration — arXiv:1802.08802 |
| Real tree search | Tree Search for LM Agents — arXiv:2407.01476 |
| Simulated search | WebDreamer — arXiv:2411.06559 |
| Workflow memory | Agent Workflow Memory — arXiv:2409.07429 |
| Grounding | SeeClick / ScreenSpot — arXiv:2401.10935 |
| State estimation | Agent-E — arXiv:2407.13032 |
| Verification | AgentRewardBench — arXiv:2504.08942 |
| Site maps (closest prior work) | Environment Maps — arXiv:2603.23610 |
| Exploration policy | NNetNav — arXiv:2410.02907 |
| Noisy-TV fix | Learning Progress Monitoring — arXiv:2509.25438 |
| Routine regime | WorkArena — arXiv:2403.07718 |
| Structure vs flat retrieval | RAGSearch — arXiv:2604.09666 |
| Long-horizon multi-app | TheAgentCompany — arXiv:2412.14161 |
| Options formalism | Sutton, Precup & Singh (1999), *Between MDPs and semi-MDPs* |
