"""The runner loop: resume a paused run when its checkpoint is decided; start a run only when the console asks.

One engine child per case (flock), per-case and global budget caps, a kill switch, and every transition recorded in
the case status and the global event log. The engine does the work (and its own outer gap loop); the runner only
decides WHEN to call `ontofill run … --run-id <the same run>` again.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, load_registry
from .lake import CaseLake, lake_for
from .state import State, decision_for, decision_run_id, now

log = logging.getLogger("ontofill-runner")

PAUSED_EXIT = 3  # the engine paused at a checkpoint and waits for a decision
NEEDS_HUMAN_EXIT = 4  # P3 found no authoritative source; a person must revise the question
SECRETISH = [
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|authorization|bearer)\b[^\n]*"),
    re.compile(r"[A-Za-z0-9_\-]{32,}"),
]


def redact(text: str) -> str:
    text = SECRETISH[0].sub(lambda m: f"{m.group(1)}=<redacted>", text)  # the rest of the line goes
    return SECRETISH[1].sub("<redacted>", text)


def tail(path: Path, n: int = 20) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()[-n:]
    except OSError:
        return ""
    return redact("\n".join(lines))


def last_reason_line(path: Path) -> str | None:
    """The engine's own last word: its `state=… reason=…` line if it printed one, else its last non-empty log line
    (an exception's message), redacted and bounded."""
    text = tail(path, 60)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith("state=") and " reason=" in ln:
            return ln.split(" reason=", 1)[1][:300] or None
    return lines[-1][:300] if lines else None


def load_env_file(path: Path | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path:
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    return out


@dataclass
class Child:
    proc: subprocess.Popen
    run_id: str
    kind: str  # started | resumed
    lock_fd: int
    log_path: Path
    started: float = field(default_factory=time.monotonic)
    stop_reason: str | None = None  # killed | budget_stop
    stop_deadline: float | None = None


class Runner:
    def __init__(self, cfg: Config, env: dict[str, str] | None = None):
        self.cfg = cfg
        self.env = dict(os.environ if env is None else env)
        self.state = State(cfg.state_dir)
        self.children: dict[str, Child] = {}
        self._lakes: dict[str, CaseLake] = {}
        self._usd_cache: dict[tuple[str, str], float] = {}

    # --- spend --------------------------------------------------------------------------------------------------
    def lake(self, cid: str) -> CaseLake:
        if cid not in self._lakes:
            spec = self.cfg.cases[cid]
            self._lakes[cid] = lake_for(spec.case_dir, spec.lake, self.env)
        return self._lakes[cid]

    def case_spent(self, cid: str, active_run: str | None = None) -> float:
        """Sum of est_usd in the case's run traces (finished runs cached; the active run re-read)."""
        lake = self.lake(cid)
        total = 0.0
        for rid in lake.run_ids():
            key = (cid, rid)
            if rid == active_run or key not in self._usd_cache:
                self._usd_cache[key] = lake.trace_usd(rid)
            total += self._usd_cache[key]
        return total

    def global_spent(self) -> float | None:
        """All model spend on the gateway (every principal and session), from its call log."""
        path = self.cfg.gateway_log
        if not path or not path.is_file():
            return None
        total = 0.0
        with path.open(errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("status") == 200:
                    total += float(row.get("est_usd") or 0)
        return total

    def case_budget(self, cid: str) -> float:
        usd = self.cfg.budgets.get(cid, self.cfg.default_case_usd)
        return min(usd, self.cfg.global_usd) if self.cfg.global_usd is not None else usd

    def case_to_phase(self, cid: str) -> int:
        return self.cfg.to_phases.get(cid, self.cfg.to_phase)

    def refresh_cases(self) -> None:
        """Re-read the data-driven registry each tick; env cases are a fallback under it (registry wins). A missing or
        malformed registry keeps the last good copy; cases that disappear or become archived stop being considered
        (a running engine for them keeps being policed until it exits)."""
        if self.cfg.cases_root is None:
            return
        try:
            reg_cases, reg_budgets, reg_phases = load_registry(self.cfg.cases_root)
        except (OSError, ValueError) as exc:
            if not getattr(self, "_registry_ok", False):
                log.warning("case registry unreadable (%s); using the env cases", type(exc).__name__)
                running = {c: s for c, s in self.cfg.cases.items() if c in self.children}
                self.cfg.cases = {**self.cfg.env_cases, **running}
            return
        self._registry_ok = True
        merged = {**self.cfg.env_cases, **reg_cases}
        for cid, spec in merged.items():
            if cid in self.cfg.cases and self.cfg.cases[cid].case_dir != spec.case_dir:
                self._lakes.pop(cid, None)
        for cid in self.children:  # never drop a case whose engine is still running
            merged.setdefault(cid, self.cfg.cases[cid])
        self.cfg.cases = merged
        self.cfg.budgets = {**self.cfg.env_budgets, **reg_budgets}
        self.cfg.to_phases = reg_phases

    # --- loop ---------------------------------------------------------------------------------------------------
    def poll_once(self) -> None:
        self.refresh_cases()
        killed = self.state.killed
        glob = self.global_spent()
        if glob is not None:  # the number the global cap is enforced on, published every tick for the console
            for cid in self.cfg.cases:
                st = self.state.status(cid)
                if st.get("spent_usd_global") != round(glob, 4) or "spent_usd_global_basis" not in st:
                    self.state.set_status(
                        cid,
                        spent_usd_global=round(glob, 4),
                        spent_usd_global_basis="gateway call log, every principal and session",
                    )
        for cid in self.cfg.cases:
            try:
                self._reap(cid)
                if cid in self.children:
                    self._police(cid, killed, glob)
                else:
                    self._consider(cid, killed, glob)
            except Exception as exc:  # noqa: BLE001 - one case's failure never stops the others
                log.warning("case %s: %s", cid, redact(f"{type(exc).__name__}: {exc}"))
                self.state.set_status(cid, reason=redact(f"{type(exc).__name__}: {exc}")[:300])

    def _transition(self, cid: str, state: str, detail: str = "", **fields) -> None:
        """Set the case state; an event is logged only when the state actually changes into a notable one."""
        prev = self.state.status(cid).get("state")
        self.state.set_status(cid, state=state, **fields)
        if prev != state and state in ("killed", "budget_stop", "failed", "done", "needs_human"):
            kind = "needs-human" if state == "needs_human" else state
            self.state.event(cid, kind, detail, run_id=fields.get("run_id"))

    def _consider(self, cid: str, killed: bool, glob: float | None) -> None:
        spec = self.cfg.cases[cid]
        status = self.state.status(cid)
        control = self.state.control(cid)
        paused = bool(control.get("paused"))
        if bool(status.get("paused")) != paused:
            self.state.set_status(cid, paused=paused)
            self.state.event(cid, "paused" if paused else "unpaused", "operator action in the console")
        retry = control.get("retry_at")
        if retry and retry != status.get("handled_retry_at"):  # console "resume": re-arm one relaunch after a failure
            self.state.set_status(cid, handled_retry_at=retry, last_trigger=None)
            status = self.state.status(cid)
        lake = self.lake(cid)
        run_id = lake.active_run_id()
        lstatus = (lake.status(run_id) if run_id else None) or {}

        start = control.get("start_requested") if isinstance(control.get("start_requested"), dict) else None
        trigger = None
        if start and start.get("at") and start.get("at") != status.get("handled_start_at"):
            if status.get("seen_start_at") != start.get("at"):
                self.state.set_status(cid, seen_start_at=start.get("at"))
                self.state.event(cid, "start_requested", f"by {start.get('by') or '?'}")
            trigger = {
                "kind": "started",
                "run_id": "run-" + uuid.uuid4().hex[:12],
                "to_phase": int(start.get("to_phase") or self.case_to_phase(cid)),
                "handled_start_at": start["at"],
            }
        elif (
            status.get("state") == "killed"
            and not killed
            and status.get("run_id") == run_id
            and run_id
            and run_id == lake.latest_run_id()  # R43: never relaunch a superseded run
            and lstatus.get("state") not in ("paused", "done", "completed", "finished")
        ):
            # this runner stopped the run mid-phase with the kill switch; with the switch lifted, relaunch the SAME run
            trigger = {
                "kind": "resumed",
                "run_id": run_id,
                "to_phase": self.case_to_phase(cid),
                "after_kill": True,
                "last_trigger": f"after-kill:{run_id}:{status.get('updated_at') or ''}",
            }
        elif lstatus.get("state") == "paused" and lstatus.get("checkpoint_pending"):
            cp = lstatus["checkpoint_pending"]
            sha = decision_for(spec.case_dir, cp)
            if sha is None:
                self._transition(cid, "waiting_approval", run_id=run_id, checkpoint=cp, reason=None)
                return
            # R43: a decision resumes only the run it was made for. A marker names its run (run_id); an older
            # marker without one may only resume the case's latest run, never a superseded paused run.
            decided_for = decision_run_id(spec.case_dir, cp)
            if (decided_for and decided_for != run_id) or (not decided_for and run_id != lake.latest_run_id()):
                if status.get("state") not in ("failed", "killed", "budget_stop", "needs_human", "done"):
                    self._transition(
                        cid,
                        "idle",
                        run_id=run_id,
                        reason=f"the {cp} decision was made for {decided_for or 'another run'}, "
                        f"not {run_id}; start a new run",
                    )
                return
            key = f"{run_id}:{cp}:{sha}"
            if status.get("last_trigger") == key:  # already resumed for this exact decision
                if status.get("state") == "failed":
                    return  # a failed resume stays failed until a new decision or a console "resume"
                again = "the engine paused again at this checkpoint after the decision"
                self._transition(
                    cid,
                    "waiting_approval",
                    run_id=run_id,
                    checkpoint=cp,
                    reason=f"{again}: {lstatus['reason']}" if lstatus.get("reason") else f"{again}; see the run",
                )
                return
            trigger = {
                "kind": "resumed",
                "run_id": run_id,
                "to_phase": self.case_to_phase(cid),
                "last_trigger": key,
                "checkpoint": cp,
            }
        elif lstatus.get("state") in ("done", "completed", "finished"):
            self._transition(cid, "done", run_id=run_id, checkpoint=None)
            return
        if trigger is None:
            if status.get("state") not in ("failed", "killed", "budget_stop", "needs_human"):
                self._transition(cid, "idle", run_id=run_id)
            return
        if killed:
            self._transition(cid, "killed", "kill switch is on", run_id=trigger["run_id"])
            return
        if paused:
            self._transition(cid, "paused", run_id=trigger["run_id"])
            return
        if lstatus.get("state") == "running" and trigger["kind"] == "resumed" and not trigger.get("after_kill"):
            return  # someone (an operator CLI) is running it right now
        spent = self.case_spent(cid)
        budget = self.case_budget(cid)
        over_global = self.cfg.global_usd is not None and glob is not None and glob >= self.cfg.global_usd
        if spent >= budget or over_global:
            why = (
                f"global spend ${glob:.2f} ≥ ${self.cfg.global_usd:.2f}"
                if over_global
                else f"case spend ${spent:.2f} ≥ ${budget:.2f}"
            )
            self._transition(
                cid,
                "budget_stop",
                why,
                run_id=trigger["run_id"],
                spent_usd_case=round(spent, 4),
                spent_usd_global=None if glob is None else round(glob, 4),
                reason=why,
            )
            return
        self._launch(cid, trigger, remaining=budget - spent, spent=spent, glob=glob)

    def _launch(self, cid: str, trigger: dict, remaining: float, spent: float, glob: float | None) -> None:
        # --budget-usd is the case's CONSTANT cap, not the remaining amount: the engine folds it into its checkpoint
        # input fingerprints (e.g. the PRD's), so a value that changes between runs would regenerate an artifact the
        # approver already decided and the digest check would refuse the decision. The runner still stops the engine
        # when cumulative case spend reaches the cap (_police).
        spec = self.cfg.cases[cid]
        cdir = self.state.case_dir(cid)
        cdir.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(cdir / "lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(lock_fd)
            log.info("case %s: another runner holds the lock; not launching", cid)
            return
        os.ftruncate(lock_fd, 0)
        run_id = trigger["run_id"]
        cmd = shlex.split(
            self.cfg.engine_cmd.format(
                case_dir=shlex.quote(str(spec.case_dir)),
                to_phase=trigger["to_phase"],
                run_id=run_id,
                budget=f"{self.case_budget(cid):.2f}",
            )
        )
        env = dict(self.env) | load_env_file(self.cfg.engine_env_file)
        log_path = cdir / f"engine-{run_id}.log"
        fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "ab") as out:
            proc = subprocess.Popen(
                cmd,
                cwd=self.cfg.engine_dir or None,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        os.write(lock_fd, str(proc.pid).encode())
        self.children[cid] = Child(proc, run_id, trigger["kind"], lock_fd, log_path)
        at = now()
        # while it runs no checkpoint is pending: the one just decided is kept apart (resumed_from_checkpoint)
        fields = {
            "run_id": run_id,
            "pid": proc.pid,
            "reason": None,
            "spent_usd_case": round(spent, 4),
            "spent_usd_global": None if glob is None else round(glob, 4),
            "checkpoint": None,
            "resumed_from_checkpoint": trigger.get("checkpoint"),
            "engine_stop": None,
        }
        fields["last_resumed_at" if trigger["kind"] == "resumed" else "last_started_at"] = at
        for key in ("last_trigger", "handled_start_at"):
            if trigger.get(key):
                fields[key] = trigger[key]
        self.state.set_status(cid, state="running", running_since=at, **fields)
        detail = (
            "resumed after the kill switch was lifted"
            if trigger.get("after_kill")
            else f"resumed after the {trigger.get('checkpoint')} decision"
            if trigger["kind"] == "resumed"
            else f"new run to phase {trigger['to_phase']}"
        )
        self.state.event(cid, trigger["kind"], detail, run_id=run_id)
        log.info("case %s: %s %s (pid %s)", cid, trigger["kind"], run_id, proc.pid)

    def _police(self, cid: str, killed: bool, glob: float | None) -> None:
        """A running child: stop it on the kill switch or a crossed budget (SIGTERM, then SIGKILL after grace)."""
        child = self.children[cid]
        if child.stop_reason is None:
            if killed:
                self._stop(cid, "killed")
            else:
                over_global = self.cfg.global_usd is not None and glob is not None and glob >= self.cfg.global_usd
                if over_global or self.case_spent(cid, active_run=child.run_id) >= self.case_budget(cid):
                    self._stop(cid, "budget_stop")
        elif child.stop_deadline and time.monotonic() > child.stop_deadline and child.proc.poll() is None:
            try:
                os.killpg(child.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _stop(self, cid: str, reason: str) -> None:
        child = self.children[cid]
        child.stop_reason = reason
        child.stop_deadline = time.monotonic() + self.cfg.kill_grace_s
        try:
            os.killpg(child.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def _reap(self, cid: str) -> None:
        child = self.children.get(cid)
        if child is None or child.proc.poll() is None:
            return
        rc = child.proc.returncode
        del self.children[cid]
        try:
            fcntl.flock(child.lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(child.lock_fd)
        run_id = child.run_id
        try:
            lstatus = self.lake(cid).status(run_id) or {}
        except Exception:  # noqa: BLE001 - an unreadable lake must not hide how the engine stopped
            lstatus = {}
        why = lstatus.get("reason") or last_reason_line(child.log_path)
        # how the engine stopped, from its run status and its own last line; replaces any earlier record
        stop = {
            "exit_code": rc,
            "at": now(),
            "state": lstatus.get("state"),
            "phase": lstatus.get("phase"),
            "checkpoint_pending": lstatus.get("checkpoint_pending"),
            "reason": why,
        }
        if child.stop_reason == "killed":  # interrupted, not answered: lifting the switch resumes it
            self._transition(
                cid,
                "killed",
                "stopped by the kill switch",
                run_id=run_id,
                pid=None,
                last_trigger=None,
                checkpoint=None,
                engine_stop=stop,
            )
        elif child.stop_reason == "budget_stop":
            self._usd_cache.pop((cid, run_id), None)  # the run just grew: re-read its trace for the final spend
            self._transition(
                cid,
                "budget_stop",
                "stopped: budget reached",
                run_id=run_id,
                pid=None,
                reason="budget reached while running",
                last_trigger=None,
                checkpoint=None,
                engine_stop=stop,
                spent_usd_case=round(self.case_spent(cid, run_id), 4),
            )
        elif rc == PAUSED_EXIT:
            cp = lstatus.get("checkpoint_pending")
            self.state.set_status(
                cid,
                state="waiting_approval",
                run_id=run_id,
                checkpoint=cp,
                pid=None,
                reason=lstatus.get("reason"),
                engine_stop=stop,
            )
            self.state.event(
                cid,
                "paused_at_checkpoint",
                f"waiting for the {cp} decision" + (f" (engine: {lstatus['reason']})" if lstatus.get("reason") else ""),
                run_id=run_id,
            )
        elif rc == NEEDS_HUMAN_EXIT:
            reason = lstatus.get("reason")
            if lstatus.get("state") == "paused" and isinstance(reason, str) and reason:
                # exit 4 = the engine needs a person, in any phase: P3 found no source (R28/R37) or P1 could not
                # produce a valid PRD after a decision (exhausted validation). Never "failed", never relaunched.
                phase = lstatus.get("phase")
                if phase == 3 and reason.startswith("sources unreachable"):
                    ask = (
                        "sources were unreachable (blocked, redirected or 403): fix access or revise the PRD "
                        "authority policy, then start a new run"
                    )
                elif phase == 3:
                    ask = "revise the brief or PRD authority policy, then start a new run"
                elif phase == 1:
                    ask = (
                        "the PRD could not be redrafted into a valid one: revise the decision or the brief, or "
                        "wait for an engine fix, then start a new run"
                    )
                else:
                    ask = "see the engine's reason, then start a new run"
                self._transition(
                    cid,
                    "needs_human",
                    f"{reason} | needs you: {ask}",
                    run_id=run_id,
                    phase=phase,
                    checkpoint=None,
                    pid=None,
                    reason=reason,
                    engine_stop=stop,
                )
            else:
                detail = f"engine exited {rc}\n{tail(child.log_path)}"
                self.state.set_status(
                    cid, state="failed", run_id=run_id, pid=None, reason=f"engine exited {rc}", engine_stop=stop
                )
                self.state.event(cid, "failed", detail, run_id=run_id)
        elif rc == 0:
            self._transition(
                cid, "done", "the run finished", run_id=run_id, pid=None, checkpoint=None, engine_stop=stop
            )
        else:
            detail = f"engine exited {rc}\n{tail(child.log_path)}"
            where = f" in phase {lstatus['phase']}" if lstatus.get("phase") else ""
            self.state.set_status(
                cid,
                state="failed",
                run_id=run_id,
                pid=None,
                checkpoint=None,
                engine_stop=stop,
                reason=f"engine exited {rc}{where}" + (f": {why}" if why else ""),
            )
            self.state.event(cid, "failed", detail, run_id=run_id)

    def shutdown(self) -> None:
        """Service stop: let running engines finish their current step (SIGTERM), then leave."""
        for cid in list(self.children):
            self._stop(cid, "killed")
        deadline = time.monotonic() + self.cfg.kill_grace_s
        while self.children and time.monotonic() < deadline:
            for cid in list(self.children):
                self._reap(cid)
            time.sleep(0.2)
        for cid, child in list(self.children.items()):
            try:
                os.killpg(child.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.proc.wait(timeout=5)
            self._reap(cid)

    def serve(self, once: bool = False) -> None:
        stop = {"flag": False}

        def on_term(*_):
            stop["flag"] = True

        signal.signal(signal.SIGTERM, on_term)
        signal.signal(signal.SIGINT, on_term)
        log.info("runner: %d case(s), poll %ss, state %s", len(self.cfg.cases), self.cfg.poll_s, self.cfg.state_dir)
        while not stop["flag"]:
            self.poll_once()
            if once:
                break
            slept = 0.0
            while slept < self.cfg.poll_s and not stop["flag"]:
                time.sleep(0.2)
                slept += 0.2
        if not once:
            self.shutdown()
