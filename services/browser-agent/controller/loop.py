"""The controller's step loop for one browser session: observe → plan → guard → act → verify.

Every model call goes through the inference gateway with the session's token (GatewayClient); this module never
touches an upstream key. Every step is written through shared.steps.StepLog in the CONTRACT §12 shape.

Threading: Playwright's sync API binds a browser to the thread that opened it, so each Session owns one worker
thread and every backend or loop call runs there (`Session.call`).
"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from controller import approvals, guard, planner, screen, verify
from controller.backend import CALL_ERRORS, READ_ACTIONS, Action, ActResult, Backend, Observation
from controller.captures import CaptureStore
from shared import config
from shared.gateway_client import GatewayError
from shared.prices import jev_usd, vultr_usd
from shared.steps import StepLog, new_step_id, now


@dataclass
class Limits:
    max_steps: int = 30  # planner turns + explicit actions per session
    timeout_s: float = 900  # wall clock per session
    max_attempts: int = 3  # not_achieved verdicts (or planning failures) tolerated per goal
    approval_timeout_s: float = 300
    approval_poll_s: float = 0.5
    budget_usd: float = 0.50
    ttl_s: int = 900

    @classmethod
    def from_dict(cls, data: dict | None) -> Limits:
        data = data or {}
        known = {k: data[k] for k in cls.__dataclass_fields__ if data.get(k) is not None}
        return cls(**known)


@dataclass
class Metrics:
    """Run-view metric "Jev screened / vision avoided / $" (skyvern-jev.md §6b)."""

    jev_observations_screened: int = 0  # observations screened before planning (controller Jev or gateway gate)
    jev_flagged: int = 0
    vision_calls_avoided: int = 0
    vision_calls_made: int = 0
    estimated_usd: float = 0.0
    estimated_usd_saved: float = 0.0
    backend_breakdown: dict = field(default_factory=lambda: {"jev": 0, "vultr": 0})
    steps: int = 0  # trace steps emitted
    turns: int = 0  # planner turns + explicit actions; limited by max_steps
    actions: int = 0
    gated: int = 0
    denied: int = 0
    blocked_hosts: list[str] = field(default_factory=list)
    usd_source: str = "local"  # local (priced from usage) | gateway (admin usage)

    def as_dict(self) -> dict:
        return asdict(self) | {"estimated_usd": round(self.estimated_usd, 6),
                               "estimated_usd_saved": round(self.estimated_usd_saved, 6)}


class Stop(Exception):
    """Ends the goal: a limit, an exhausted/revoked token, or a closed session."""

    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


class Session:
    def __init__(self, *, session_id: str, backend: Backend, gateway, steps: StepLog, captures: CaptureStore,
                 allowed_domains: list[str], case_dir: Path | None, job_id: str, limits: Limits | None = None,
                 source_id: str | None = None, artifact_paths: list[str] | None = None, admin=None,
                 vision_grounding: bool = False, start_url: str | None = None, cdp_url: str | None = None,
                 prescreen: bool = True, cell: dict | None = None):
        self.session_id = session_id
        self.backend = backend
        self.gateway = gateway
        self.steps = steps
        self.captures = captures
        self.allowed_domains = list(allowed_domains)
        self.case_dir = Path(case_dir) if case_dir else None
        self.job_id = job_id
        self.limits = limits or Limits()
        self.source_id = source_id or session_id
        self.artifact_paths = artifact_paths or []
        self.admin = admin
        self.mode = "S2" if vision_grounding else "S1"
        self.vision_grounding = vision_grounding
        self.metrics = Metrics()
        self.extracted: dict[str, dict] = {}
        self.scrutiny = False  # set once any screen flags page content in this session (one-way)
        self.prescreen = prescreen  # controller-side Jev injection screen before the planner sees a page
        self._withheld: dict[str, str | None] = {}  # page-block digest -> capture key, for flagged pages
        self._screened: set[str] = set()
        self.closed = False
        self._obs: Observation | None = None
        self._captures = 0
        self._vision_costs: list[float] = []
        self._started = time.monotonic()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"ba-{session_id[:12]}")
        self.cell = cell  # the leased cell (§13a): id, isolation, timings; noted on the first step's `executed`
        self._cell_note = dict(cell) if cell else None
        self._pending_sid: str | None = None  # step id for the model calls of the step being built
        self._open_args = {"session_id": session_id, "allowed_domains": self.allowed_domains,
                           "start_url": start_url, "cdp_url": cdp_url}

    # --- thread confinement -------------------------------------------------------------------------------
    def call(self, fn, *args, **kwargs):
        if self.closed and fn is not self._close:
            raise RuntimeError("session is closed")
        return self._pool.submit(fn, *args, **kwargs).result()

    def open(self) -> dict:
        return self.call(self._open)

    def run_goal(self, goal: str) -> dict:
        return self.call(self._run_goal, goal)

    def run_action(self, action: dict, goal: str | None = None) -> dict:
        return self.call(self._run_action, action, goal)

    def observe(self) -> dict:
        return self.call(self._observe_summary)

    def close(self) -> dict:
        if self.closed:
            return {"closed": True, "metrics": self.metrics.as_dict()}
        try:
            return self.call(self._close)
        finally:
            self._pool.shutdown(wait=False)

    # --- helpers --------------------------------------------------------------------------------------------
    def _gen(self, backend: str = "vultr", model: str | None = None) -> dict:
        return {"backend": backend, "model": model or config.PLANNER_MODEL, "at": now()}

    def _step_id(self) -> str:
        """The id of the step the next model call belongs to: allocated at the call, consumed by the next emit."""
        if self._pending_sid is None:
            self._pending_sid = new_step_id()
        return self._pending_sid

    def _emit(self, **kw) -> dict:
        kw.setdefault("mode", self.mode)
        if self._pending_sid is not None and "step_id" not in kw:  # the step its model calls were attributed to
            kw["step_id"], self._pending_sid = self._pending_sid, None
        if kw.get("generated_by") is None:
            kw["generated_by"] = self._gen()
        if self._cell_note and isinstance(kw.get("executed"), dict):
            kw["executed"] = {**kw["executed"], "cell": self._cell_note}
            self._cell_note = None
        step = self.steps.emit(**kw)
        self.metrics.steps += 1
        for key in {kw.get("screenshot_key"), (kw.get("verify") or {}).get("screenshot_key")} - {None}:
            self.captures.link(key, step["step_id"])
        return step

    def _capture(self, obs: Observation) -> Observation:
        if obs.screenshot_png:
            self._captures += 1
            obs.screenshot_key = self.captures.put(
                obs.screenshot_png, content_type="image/png", url=obs.url if obs.url.startswith("http") else
                "http://invalid/", source_id=self.source_id, step_id=f"pending:{self.session_id}:{self._captures}")
        self._record_blocked(obs.blocked_hosts)
        return obs

    def _record_blocked(self, hosts: list[str]) -> None:
        for host in hosts:
            if host not in self.metrics.blocked_hosts:
                self.metrics.blocked_hosts.append(host)

    def _look(self) -> Observation:
        self._obs = self._capture(self.backend.observe())
        return self._obs

    def _check_limits(self, goal: str) -> None:
        reason = None
        if self.metrics.turns >= self.limits.max_steps:
            reason = "max_steps"
        elif time.monotonic() - self._started > self.limits.timeout_s:
            reason = "timeout"
        if reason:
            self._emit(observed={"url": self._obs.url if self._obs else None},
                       requested={"tool": "goal", "goal": goal},
                       executed={"stopped": True, "turns": self.metrics.turns},
                       evaluated={"reason": reason, "limit": self.limits.max_steps if reason == "max_steps"
                                  else self.limits.timeout_s},
                       event="limit_kill")
            raise Stop("killed", reason)

    def _usage_vultr(self, model: str, response: dict) -> dict:
        usage = response.get("usage") or {}
        tin, tout = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        usd = vultr_usd(model, tin, tout)
        self.metrics.estimated_usd += usd
        self.metrics.backend_breakdown["vultr"] += 1
        return {"model": model, "backend": "vultr", "input_tokens": tin, "output_tokens": tout,
                "est_usd": round(usd, 8)}

    def _jev(self, state, questions, step_id=None) -> dict:
        body = self.gateway.jev(state, questions, step_id=step_id or self._step_id())
        tin = int((body.get("usage") or {}).get("input_tokens") or 0)
        self.metrics.estimated_usd += jev_usd(tin)
        self.metrics.backend_breakdown["jev"] += 1
        return body

    def _chat(self, model: str, messages: list[dict], **params) -> dict:
        try:
            return self.gateway.chat(model, messages, step_id=self._step_id(), **params)
        except GatewayError as exc:
            if exc.status == 402:
                raise Stop("stopped", "session budget exhausted") from exc
            if exc.status == 401:
                raise Stop("stopped", "session token expired or revoked") from exc
            raise

    def _gate_hook(self, response: dict) -> str | None:
        """The gateway reports its injection gate as X-BA-Gate: clean|flagged, read from the client (last_gate,
        see controller.gateway) or from the body (ba_gate) when a transport puts it there."""
        gate = getattr(self.gateway, "last_gate", None) or response.get("ba_gate")
        return gate if gate in ("clean", "flagged") else None

    # --- lifecycle ------------------------------------------------------------------------------------------
    def _open(self) -> dict:
        self.backend.open(self._open_args)
        obs = self._look()
        return {"session_id": self.session_id, "url": obs.url, "title": obs.title}

    def _observe_summary(self) -> dict:
        obs = self._look()
        return {"observation": obs.summary() | {"text_excerpt": obs.text[:1500],
                                                 "elements": [e.brief() for e in obs.elements[:60]]},
                "extracted": self.extracted, "metrics": self._metrics()}

    def _metrics(self) -> dict:
        if self.admin is not None:
            try:
                usage = self.admin.usage(self.session_id)
                self.metrics.estimated_usd = float(usage.get("spent_usd", self.metrics.estimated_usd))
                self.metrics.usd_source = "gateway"
                self.metrics.jev_flagged = max(self.metrics.jev_flagged, int(usage.get("flagged") or 0))
            except CALL_ERRORS:  # the gateway may be gone at close; keep the local estimate
                self.metrics.usd_source = "local"
        if self._vision_costs:
            avg = sum(self._vision_costs) / len(self._vision_costs)
            self.metrics.estimated_usd_saved = self.metrics.vision_calls_avoided * avg
        return self.metrics.as_dict()

    def _close(self) -> dict:
        try:
            self.backend.close()
        finally:
            self.closed = True
        return {"closed": True, "metrics": self._metrics()}

    # --- the loop -------------------------------------------------------------------------------------------
    def _run_goal(self, goal: str) -> dict:
        history: list[str] = []
        failures = 0
        status, summary = "not_achieved", ""
        try:
            while True:
                self._check_limits(goal)
                obs = self._obs or self._look()
                action, plan_step = self._plan(goal, obs, history)
                if action is None:
                    failures += 1
                    history.append(f"planning failed: {plan_step['evaluated'].get('error')}")
                    if failures >= self.limits.max_attempts:
                        status, summary = "not_achieved", "planner failed repeatedly"
                        break
                    continue
                if action.tool == "done":
                    status = "achieved" if action.args.get("status", "achieved") == "achieved" else "not_achievable"
                    summary = str(action.args.get("summary", ""))
                    self._emit(observed=obs.summary(), requested=action.as_dict(),
                               executed={"done": True}, evaluated={"goal": goal, "goal_status": status,
                                                                   "summary": summary},
                               parent_step_id=plan_step["step_id"], screenshot_key=obs.screenshot_key)
                    break
                outcome = self._execute(action, goal, obs, parent=plan_step)
                history.append(outcome["history"])
                if outcome.get("verdict") == "not_achieved" or outcome.get("error"):
                    failures += 1
                    if failures >= self.limits.max_attempts:
                        status, summary = "not_achieved", f"{failures} failed attempts"
                        self._emit(observed=self._obs.summary() if self._obs else {}, requested={"tool": "goal",
                                   "goal": goal}, executed={"attempts": failures},
                                   evaluated={"goal_status": "not_achieved", "reason": "max_attempts"},
                                   parent_step_id=plan_step["step_id"])
                        break
        except Stop as stop:
            status, summary = stop.status, stop.reason
            self._stopped(stop, goal)
        return {"session_id": self.session_id, "goal": goal, "status": status, "summary": summary,
                "url": self._obs.url if self._obs else None, "extracted": self.extracted,
                "metrics": self._metrics()}

    def _stopped(self, stop: Stop, goal: str) -> None:
        """A token the gateway no longer honours is a hard stop (limit kills already emitted their own step)."""
        if stop.status == "stopped":
            self._emit(observed={"url": self._obs.url if self._obs else None},
                       requested={"tool": "goal", "goal": goal}, executed={"stopped": True},
                       evaluated={"reason": stop.reason}, event="hard_stop")

    def _run_action(self, action: dict, goal: str | None) -> dict:
        act = Action(str(action.get("tool")), dict(action.get("args") or {}))
        goal = goal or act.args.get("expectation") or act.describe(self._obs)
        try:
            self._check_limits(goal)
            obs = self._obs or self._look()
            self.metrics.turns += 1
            outcome = self._execute(act, goal, obs, parent=None)
            status = "denied" if outcome.get("denied") else ("error" if outcome.get("error") else
                                                              outcome.get("verdict") or "done")
        except Stop as stop:
            outcome, status = {"history": stop.reason}, stop.status
            self._stopped(stop, goal)
        return {"session_id": self.session_id, "action": act.as_dict(), "status": status,
                "detail": outcome.get("history"), "url": self._obs.url if self._obs else None,
                "extracted": self.extracted, "metrics": self._metrics()}

    def _plan(self, goal: str, obs: Observation, history: list[str]) -> tuple[Action | None, dict]:
        """Screen the observation, then ask the planner. A flagged page is withheld (§12a): the step that saw the
        flag becomes a `quarantine` step, the planner gets a notice instead of the page from then on, and any
        action proposed from a flagged prompt is discarded."""
        digest = self._digest(obs)
        prescreen = None
        if digest not in self._withheld and self.prescreen and digest not in self._screened:
            prescreen = screen.controller_screen(self._jev, planner.page_block(obs))
            if prescreen is not None:
                self._screened.add(digest)
                self.metrics.jev_observations_screened += 1
                if prescreen["flagged"]:
                    self._withhold(digest, obs)
                    self._emit(observed=obs.summary() | {"injection_suspected": True},
                               requested={"tool": "screen", "goal": goal, "by": "controller"},
                               executed={"withheld_from_planning": True, "captured_as": obs.screenshot_key},
                               evaluated={"status": "quarantined_continue", "reason": prescreen["reason"]},
                               event="quarantine", screen=prescreen, screenshot_key=obs.screenshot_key,
                               generated_by=self._gen("jev", prescreen.get("model") or config.JEV_MODEL))
                    prescreen = None
        action, step = self._plan_once(goal, obs, history, digest, prescreen)
        if step.get("event") == "quarantine":  # the gateway flagged the page: plan again without it
            action, step = self._plan_once(goal, obs, history, digest, None)
        return action, step

    @staticmethod
    def _digest(obs: Observation) -> str:
        return hashlib.sha256(planner.page_block(obs).encode()).hexdigest()

    def _withhold(self, digest: str, obs: Observation) -> None:
        self._withheld[digest] = obs.screenshot_key
        self.metrics.jev_flagged += 1
        self.scrutiny = True

    def _plan_once(self, goal: str, obs: Observation, history: list[str], digest: str,
                   prescreen: dict | None) -> tuple[Action | None, dict]:
        model = config.VISION_MODEL if self.vision_grounding else config.PLANNER_MODEL
        self.metrics.turns += 1
        withheld = screen.withheld_notice(self._withheld[digest]) if digest in self._withheld else None
        msgs = planner.messages(goal, obs, history, vision=self.vision_grounding, withheld=withheld)
        error, usage, gateway = None, None, None
        action = None
        try:
            response = self._chat(model, msgs, tools=planner.TOOLS, tool_choice="auto", temperature=0)
            gateway = screen.gateway_screen(self._gate_hook(response))
            usage = self._usage_vultr(model, response)
            action, note = planner.parse(response)
            error = None if action else note
        except GatewayError as exc:
            error = str(exc)
        if withheld:
            record = None  # the page was not sent: nothing of it was screened on this call
        else:
            if gateway and prescreen is None:
                self.metrics.jev_observations_screened += 1
            record = gateway if gateway and gateway["flagged"] else (prescreen or gateway)
        observed = obs.summary() | {"page_withheld": bool(withheld)}
        requested = {"tool": "plan", "goal": goal, "model": model}
        gen = self._gen("vultr", model)
        if record and record["flagged"]:
            self._withhold(digest, obs)
            step = self._emit(observed=observed | {"injection_suspected": True}, requested=requested,
                              executed={"discarded_proposal": action.as_dict() if action else None,
                                        "withheld_from_planning": True, "captured_as": obs.screenshot_key},
                              evaluated={"status": "quarantined_continue", "reason": record["reason"]},
                              event="quarantine", screen=record, screenshot_key=obs.screenshot_key,
                              generated_by=gen, usage=usage)
            return None, step
        step = self._emit(observed=observed, requested=requested,
                          executed={"proposed": action.as_dict() if action else None},
                          evaluated={"ok": action is not None, **({"error": error} if error else {})},
                          screen=record, screenshot_key=obs.screenshot_key, generated_by=gen, usage=usage)
        return action, step

    def _execute(self, action: Action, goal: str, obs: Observation, parent: dict | None) -> dict:
        """Guard, gate, act and verify one action. Returns {"history", "verdict"?, "denied"?, "error"?}."""
        parent_id = parent["step_id"] if parent else None
        desc = action.describe(obs)
        if action.tool in ("click", "type", "select", "extract") and self._digest(obs) in self._withheld:
            return {"history": f"{desc} → refused: this page is withheld (flagged as a possible prompt injection); "
                               "use back, navigate within the allowed domains, or done", "denied": True}
        if action.tool == "extract":
            return self._extract(action, obs, parent_id)
        if action.tool not in READ_ACTIONS:
            gate_step, allowed = self._gate(action, obs, desc, parent_id)
            if not allowed:
                return {"history": f"{desc} → DENIED ({gate_step['evaluated'].get('reason')}). Do not retry it; "
                                   "find another way or call done.", "denied": True}
            parent_id = gate_step["step_id"]
        result = self.backend.act(action)
        self.metrics.actions += 1
        self._record_blocked(result.blocked_hosts)
        after = self._look()
        blocked = sorted(set(result.blocked_hosts) | set(after.blocked_hosts))
        act_step = self._emit(observed=obs.summary(), requested=action.as_dict(),
                              executed=result.as_dict() | {"url_after": after.url, "blocked_hosts": blocked},
                              evaluated={"ok": result.ok, **({"error": result.error} if result.error else {})},
                              parent_step_id=parent_id, screenshot_key=after.screenshot_key)
        if not result.ok:
            return {"history": f"{desc} → failed: {result.error}", "error": result.error}
        if action.tool == "scroll":
            return {"history": f"{desc} → ok"}
        step_goal = str(action.args.get("expectation") or goal)
        verdict = self._verify(step_goal, desc, obs, after, act_step["step_id"])
        return {"history": f"{desc} → {after.url}; check: {verdict['verdict']} ({verdict['backend']})"
                           + (f" — {verdict.get('reason')}" if verdict.get("reason") else ""),
                "verdict": verdict["verdict"]}

    def _gate(self, action: Action, obs: Observation, desc: str, parent_id: str | None) -> tuple[dict, bool]:
        decision = guard.decide(action, obs, self.allowed_domains, self._jev, scrutiny=self.scrutiny)
        gen = self._gen("jev", (decision.jev or {}).get("model") or config.JEV_MODEL) \
            if decision.decided_by == "jev" else self._gen()
        base = {"action": desc, "risk_tier": decision.tier, "decided_by": decision.decided_by}
        if decision.tier != "HIGH":
            step = self._emit(observed=obs.summary(), requested=action.as_dict(), executed={"gated": True},
                              evaluated={"guard": decision.as_dict()}, parent_step_id=parent_id,
                              event="action_gate", gate=base | {"outcome": "allowed", "approval_path": None},
                              generated_by=gen, screenshot_key=obs.screenshot_key)
            return step, True
        self.metrics.gated += 1
        if decision.hard or self.case_dir is None:
            reason = decision.code_reason if decision.hard else "no case directory to ask an approver in"
            step = self._emit(observed=obs.summary(), requested=action.as_dict(), executed={"gated": True},
                              evaluated={"guard": decision.as_dict(), "reason": reason}, parent_step_id=parent_id,
                              event="action_gate", gate=base | {"outcome": "denied", "approval_path": None},
                              generated_by=gen, screenshot_key=obs.screenshot_key)
            self.metrics.denied += 1
            return step, False
        req = approvals.request(self.case_dir, intended_action=desc, risk_tier="HIGH",
                                screenshot_key=obs.screenshot_key or self._placeholder_key(obs),
                                job_id=self.job_id, reason=decision.code_reason if decision.decided_by == "code"
                                else f"Jev rated the action HIGH (code floor: {decision.code_reason})",
                                artifact_paths=self.artifact_paths, generated_by=gen,
                                detail={"session_id": self.session_id, "url": obs.url, "action": action.as_dict(),
                                        "guard": decision.as_dict()})
        approval_path = f"{req.rel_dir}/APPROVAL_PENDING.md"
        pending = self._emit(observed=obs.summary(), requested=action.as_dict(),
                             executed={"approval_request": req.request_id},
                             evaluated={"guard": decision.as_dict(), "waiting_s": self.limits.approval_timeout_s},
                             parent_step_id=parent_id, event="action_gate",
                             gate=base | {"outcome": "pending_approval", "approval_path": approval_path},
                             generated_by=gen, screenshot_key=obs.screenshot_key)
        answer = approvals.wait(req, self.limits.approval_timeout_s, self.limits.approval_poll_s,
                                cancelled=lambda: self.closed)
        outcome = "allowed" if answer.approved else "denied"
        if not answer.approved:
            self.metrics.denied += 1
        step = self._emit(observed=obs.summary(), requested=action.as_dict(),
                          executed={"approval_request": req.request_id},
                          evaluated={"approval": answer.as_dict(), "reason": answer.reason},
                          parent_step_id=pending["step_id"], event="action_gate",
                          gate=base | {"outcome": outcome, "approval_path": approval_path},
                          generated_by=gen, screenshot_key=obs.screenshot_key)
        return step, answer.approved

    def _placeholder_key(self, obs: Observation) -> str:
        """No screenshot (a backend that cannot capture): store the page text instead so the key still resolves."""
        return self.captures.put(f"{obs.url}\n{obs.title}\n{obs.text[:4000]}".encode(), content_type="text/plain",
                                 url=obs.url if obs.url.startswith("http") else "http://invalid/",
                                 source_id=self.source_id, step_id=f"pending:{self.session_id}:text")

    def _extract(self, action: Action, obs: Observation, parent_id: str | None) -> dict:
        result: ActResult = self.backend.act(action)
        values = result.values or {}
        step = self._emit(observed=obs.summary(), requested=action.as_dict(), executed=result.as_dict(),
                   evaluated={"ok": result.ok, "fields_found": sorted(k for k, v in values.items()
                                                                       if v.get("value") not in (None, "")),
                              "fields_missing": sorted(k for k, v in values.items()
                                                       if v.get("value") in (None, ""))},
                   parent_step_id=parent_id, screenshot_key=obs.screenshot_key)
        for name, item in values.items():  # CONTRACT v1.0.5 (proposed): each value cites the step that captured it
            if item.get("value") not in (None, ""):
                self.extracted[name] = item | {"url": obs.url, "screenshot_key": obs.screenshot_key,
                                               "step_id": step["step_id"], "captured_at": step["ts"]}
        found = [k for k, v in values.items() if v.get("value") not in (None, "")]
        return {"history": f"extract → found {', '.join(found) or 'nothing'}",
                **({"error": result.error} if not result.ok else {})}

    def _verify(self, step_goal: str, desc: str, before: Observation, after: Observation, act_step_id: str) -> dict:
        diff = verify.diff_summary(before, after)
        shot = after.screenshot_key or self._placeholder_key(after)
        jev = verify.jev_step_check(self._jev, step_goal=step_goal, action=desc, diff=diff)
        if "error" not in jev:
            usage = {"model": jev["model"], "backend": "jev",
                     "input_tokens": int(jev["usage"].get("input_tokens") or 0),
                     "output_tokens": int(jev["usage"].get("output_tokens") or 0),
                     "est_usd": round(jev_usd(int(jev["usage"].get("input_tokens") or 0)), 8)}
            self._emit(observed={"diff_summary": diff}, requested={"tool": "verify", "first_pass": "jev"},
                       executed={"p_yes": jev["p_yes"]}, evaluated={"verdict": jev["verdict"]},
                       parent_step_id=act_step_id, event="verify", generated_by=self._gen("jev", jev["model"]),
                       usage=usage, verify={"goal": step_goal, "verdict": jev["verdict"],
                                            "confidence": jev["confidence"], "backend": "jev",
                                            "model": jev["model"], "screenshot_key": shot})
            if jev["verdict"] == "achieved":
                self.metrics.vision_calls_avoided += 1
                return {"verdict": "achieved", "backend": "jev"}
        model = config.VISION_MODEL
        msgs = verify.vision_messages(step_goal=step_goal, action=desc, url=after.url,
                                      screenshot_png=after.screenshot_png or b"")
        try:
            response = self._chat(model, msgs, temperature=0)
            usage = self._usage_vultr(model, response)
            self._vision_costs.append(usage["est_usd"])
            content = (response.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            parsed = verify.parse_verdict(content if isinstance(content, str) else str(content))
        except GatewayError as exc:
            usage, parsed = None, {"verdict": "uncertain", "confidence": 0.0, "reason": f"verifier error: {exc}"}
        self.metrics.vision_calls_made += 1
        self._emit(observed={"url": after.url, "jev_first_pass": jev}, requested={"tool": "verify", "model": model},
                   executed={"verdict": parsed["verdict"]}, evaluated={"reason": parsed.get("reason", "")},
                   parent_step_id=act_step_id, event="verify", generated_by=self._gen("vultr", model), usage=usage,
                   verify={"goal": step_goal, "verdict": parsed["verdict"], "confidence": parsed["confidence"],
                           "backend": "vultr", "model": model, "screenshot_key": shot})
        return {"verdict": parsed["verdict"], "backend": "vultr", "reason": parsed.get("reason", "")}
