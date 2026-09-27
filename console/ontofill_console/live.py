"""Live run view model (CONTRACT section 4b) and the replayer that feeds it for rehearsal and as demo insurance.

The replayer turns a finished run (gold export, plus `trace.live.jsonl` when it was recorded) into a live feed:
it appends the steps to `runs/<case_id>/<new_run_id>/trace.live.jsonl` on the original clock, compressed to a
chosen duration, rewrites `status.json` with partial metrics as values arrive, and publishes the gold export
under the new run id when it finishes.
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from . import dod
from .domain import Domain
from .gold import GoldStore, LocalSource

MODE_RANK = {"D0": 0, "D1": 1, "S1": 2, "S2": 3}
MODE_NAMES = {
    "D0": "Deterministic",
    "D1": "Inference-assisted script",
    "S1": "Agentic loop",
    "S2": "Computer use + vision",
}
PHASES = [(1, "Scope"), (2, "Ontology"), (3, "Fan out"), (4, "Local scoping"), (5, "Execute")]
CHECKPOINT_PHASE = {"prd": 1, "factors": 2, "ontology": 2}
# "blocked" is deliberately absent: an isolation probe that is BLOCKED is the success case.
_FAIL_WORDS = ("fail", "captcha", "timeout", "error", "refused")


def _text(step: dict) -> str:
    return " ".join(json.dumps(step.get(k)) if isinstance(step.get(k), (dict, list)) else str(step.get(k) or "")
                    for k in ("requested", "executed", "evaluated")).lower()


def _evaluated(step: dict) -> str:
    ev = step.get("evaluated")
    if isinstance(ev, dict):  # structured: judge by its status, not by words that merely appear in details
        return str(ev.get("status") or ev.get("result") or "").lower()
    return str(ev or "").lower()


def _failed(step: dict) -> bool:
    return any(w in _evaluated(step) for w in _FAIL_WORDS)


def step_kind(step: dict) -> str | None:
    """§12 step kinds: 'verify' (Pattern B vision check), 'repair' (Pattern A attempt), 'gate' (approve-before-submit),
    'kill' (a resource limit stopped the job), 'quarantine' (§12a: a hostile page withheld from planning). Earlier
    guessed shapes are accepted too."""
    ev = step.get("event")
    screen = step.get("screen") if isinstance(step.get("screen"), dict) else {}
    if ev == "quarantine" or screen.get("flagged") is True:
        return "quarantine"
    if ev == "loop" or isinstance(step.get("loop"), dict):
        return "loop"
    req = step.get("requested") if isinstance(step.get("requested"), dict) else {}
    evald = step.get("evaluated") if isinstance(step.get("evaluated"), dict) else {}
    if ev == "verify" or isinstance(step.get("verify"), dict) or "vision" in str(req.get("tool") or "") \
            or "verdict" in evald:
        return "verify"
    if ev == "repair" or isinstance(step.get("repair"), dict):
        return "repair"
    if ev == "action_gate" or isinstance(step.get("gate"), dict):
        return "gate"
    if ev == "limit_kill":
        return "kill"
    return None


VERDICT_ALIASES = {"pass": "achieved", "fail": "not_achieved", "unsure": "uncertain"}


def _details(s: dict, kind: str) -> dict:
    evald = s.get("evaluated") if isinstance(s.get("evaluated"), dict) else {}
    ex = s.get("executed") if isinstance(s.get("executed"), dict) else {}
    if kind == "verify":
        v = s.get("verify") if isinstance(s.get("verify"), dict) else evald
        verdict = str(v.get("verdict") or v.get("status") or "uncertain").lower()
        return {"verdict": VERDICT_ALIASES.get(verdict, verdict), "confidence": v.get("confidence"),
                "backend": v.get("backend"), "model": v.get("model"), "goal": v.get("goal"),
                "reason": v.get("reason") or evald.get("reason"),  # the controller puts the verifier's reason there
                "screenshot_key": v.get("screenshot_key") or s.get("screenshot_key")}
    if kind == "repair":
        r = s.get("repair") if isinstance(s.get("repair"), dict) else {}
        return {"attempt": r.get("attempt") or evald.get("attempt"), "max_attempts": r.get("max_attempts"),
                "result": r.get("result") or evald.get("status"),
                "stderr": r.get("stderr_excerpt") or ex.get("stderr"), "diff_key": r.get("diff_key"),
                "code_key": r.get("code_key"), "patch": ex.get("diff") or ex.get("patch"), "test": r.get("test") or {}}
    if kind == "gate":
        g = s.get("gate") if isinstance(s.get("gate"), dict) else {}
        return {"action": g.get("action"), "risk_tier": g.get("risk_tier"), "decided_by": g.get("decided_by"),
                "outcome": g.get("outcome"), "approval_path": g.get("approval_path")}
    if kind == "kill":
        return {"reason": evald.get("reason") or ex.get("reason")}
    if kind == "loop":
        return _loop_details(s, evald, ex)
    if kind == "quarantine":
        sc = s.get("screen") if isinstance(s.get("screen"), dict) else {}
        return {"jev_choice": sc.get("jev_choice"), "jev_confidence": sc.get("jev_confidence"),
                "safety_verdict": sc.get("safety_verdict"), "reason": sc.get("reason"), "by": sc.get("by"),
                "screenshot_key": s.get("screenshot_key")}
    return {}


def annotate(steps: list[dict]) -> list[dict]:
    """Add `event` to each step. An `event` key from the engine (even null) wins; inference is only a fallback."""
    by_id = {s.get("step_id"): s for s in steps}
    out = []
    for s in steps:
        s = dict(s)
        event = s.get("event")
        if "event" not in s:
            parent = by_id.get(s.get("parent_step_id"))
            text = _text(s)
            if parent and parent.get("phase") == 5 and _failed(parent) and \
                    MODE_RANK.get(s.get("mode"), 0) > MODE_RANK.get(parent.get("mode"), 0):
                event = "escalation"  # a costlier mode after the cheaper one failed its check
            elif "crystalliz" in text or "code.promote" in text:
                event = "crystallization"
            elif _failed(s) and not s.get("value_ids") and step_kind(s) is None:
                event = "failure"
        s["event"] = event
        s["kind"] = step_kind(s)
        if s["kind"]:
            s["detail"] = _details(s, s["kind"])
        out.append(s)
    return out


# --- phase loops (CONTRACT v0.9.6) ---------------------------------------------------------------------------
LOOP_ROLES = ("gather", "propose", "critique", "revise", "check", "decide")
STOP_LABELS = {"checks_passed": "checks passed", "budget": "budget reached", "human": "a person decided",
               "max_iterations": "iteration cap"}


def _loop_details(s: dict, evald: dict, ex: dict) -> dict:
    lp = s.get("loop") if isinstance(s.get("loop"), dict) else {}
    usage = s.get("usage") if isinstance(s.get("usage"), dict) else {}
    gen = s.get("generated_by") if isinstance(s.get("generated_by"), dict) else {}
    role = lp.get("role") if lp.get("role") in LOOP_ROLES else "propose"
    phase = lp.get("phase") if lp.get("phase") == "outer" else (lp.get("phase") or s.get("phase"))
    human = role == "revise" and (gen.get("backend") == "human" or evald.get("source") == "human")
    objections = [str(o) for o in lp.get("objections") or [] if str(o).strip()]
    model = next((m for m in (lp.get("model"), usage.get("model"), None if human else gen.get("model"))
                  if m and m != "none"), None)  # phase_loop writes model "none" for a stage without a model call
    return {"phase": phase, "iteration": lp.get("iteration") if isinstance(lp.get("iteration"), int) else 1,
            "role": role, "model": model,
            "verdict": lp.get("verdict"), "objections": objections, "stop_reason": lp.get("stop_reason"),
            "reopen": ex.get("reopen") if role == "decide" else None, "human": human,
            "reason": evald.get("reason") or ex.get("reason"),
            "usd": usage.get("est_usd") if isinstance(usage.get("est_usd"), (int, float)) else None}


def is_reopen_marker(s: dict) -> bool:
    """An outer-loop decision renders as its own marker row between execution rounds, not inside a thread."""
    d = s.get("detail") or {}
    return s.get("kind") == "loop" and d.get("phase") == "outer" and d.get("role") == "decide"


def phase_name(n) -> str:
    return dict(PHASES).get(n, str(n))


def loop_threads(steps: list[dict]) -> list[dict]:
    """Group annotated loop steps (chronological) into threads, one per run of a phase's loop. A thread continues while
    iteration numbers do not go back; an iteration lower than the thread's highest starts a new thread (the phase was
    run again, e.g. reopened by the gap loop). Thread ids are stable as steps are appended."""
    threads: list[dict] = []
    current: dict = {}
    for idx, s in enumerate(steps):
        if s.get("kind") != "loop" or is_reopen_marker(s):
            continue
        d = s["detail"]
        phase, it = d["phase"], d["iteration"]
        t = current.get(phase)
        if t is None or it < t["max_iteration"]:
            n = sum(1 for x in threads if x["phase"] == phase) + 1
            key = "outer" if phase == "outer" else f"p{phase}"
            t = {"id": f"loop-{key}-{n}", "phase": phase, "first": idx, "last": idx, "max_iteration": it,
                 "by_iteration": {}, "step_ids": []}
            threads.append(t)
            current[phase] = t
        t["last"], t["max_iteration"] = idx, max(t["max_iteration"], it)
        t["step_ids"].append(s.get("step_id"))
        t["by_iteration"].setdefault(it, []).append(s)
    latest = max(threads, key=lambda t: t["last"]) if threads else None
    for t in threads:
        members = [x for it in sorted(t["by_iteration"]) for x in t["by_iteration"][it]]
        t["iterations"] = [{"n": it, "steps": t["by_iteration"][it]} for it in sorted(t["by_iteration"])]
        stops = [x["detail"]["stop_reason"] for x in members if x["detail"].get("stop_reason")]
        t["stop_reason"] = stops[-1] if stops else None
        t["stop_label"] = STOP_LABELS.get(t["stop_reason"], t["stop_reason"]) if t["stop_reason"] else None
        costs = [x["detail"]["usd"] for x in members if x["detail"].get("usd") is not None]
        t["usd"] = round(sum(costs), 4) if costs else None
        t["objections"] = sum(len(x["detail"]["objections"]) for x in members)
        t["label"] = "Gap loop" if t["phase"] == "outer" else f"Phase {t['phase']} loop"
        t["open"] = t is latest
        del t["by_iteration"]
    return threads


def stream_items(steps: list[dict], start: int = 0, threads: list[dict] | None = None,
                 incremental: bool = False) -> tuple[list[dict], list[dict]]:
    """The step stream from index `start` on, newest first: loop steps folded into their thread (placed at the thread's
    first step at or after `start`), everything else as single steps. With `incremental` (the live poll, `start` = the
    steps the page already has), a thread that began before `start` is not placed again: it is returned in the second
    list so the page can re-render it in place."""
    threads = loop_threads(steps) if threads is None else threads
    member = {sid: t for t in threads for sid in t["step_ids"]}
    items, placed, updated = [], set(), {}
    for idx in range(max(start, 0), len(steps)):
        s = steps[idx]
        t = member.get(s.get("step_id")) if s.get("kind") == "loop" else None
        if t is None:
            items.append({"step": s})
        elif incremental and t["first"] < start:
            updated[t["id"]] = t
        elif t["id"] not in placed:
            placed.add(t["id"])
            items.append({"thread": t})
    return items[::-1], list(updated.values())


def loop_summary(steps: list[dict], metrics: dict | None) -> dict:
    """The panel's loop line: iterations and stop reason per phase (metrics.loops when the engine wrote it, else the
    steps), and how often the gap loop reopened each phase."""
    rows = []
    for row in (metrics or {}).get("loops") or []:
        if isinstance(row, dict):
            rows.append({"phase": row.get("phase"), "iterations": row.get("iterations"),
                         "stop_reason": row.get("stop_reason"),
                         "stop_label": STOP_LABELS.get(row.get("stop_reason"), row.get("stop_reason")),
                         "usd": row.get("usd")})
    if not rows:
        for t in loop_threads(steps):
            rows.append({"phase": t["phase"], "iterations": len(t["iterations"]), "stop_reason": t["stop_reason"],
                         "stop_label": t["stop_label"], "usd": t["usd"]})
    reopened: dict = {}
    for s in steps:
        if is_reopen_marker(s) and s["detail"].get("reopen") is not None:
            reopened[s["detail"]["reopen"]] = reopened.get(s["detail"]["reopen"], 0) + 1
    return {"rows": rows, "reopened": sorted(reopened.items(), key=lambda kv: str(kv[0]))}


def property_ratios(metrics: dict, domain: Domain | None) -> list[dict]:
    """Per-property completeness bars from metrics in §11 shape (or the pre-§11 per_field_completeness)."""
    if domain is None:
        return []
    per = ((metrics.get("per_property_completeness") or {}).get(domain.primary_class)
           or metrics.get("per_field_completeness") or {})
    return [{"name": p.id, "label": p.label, "ratio": per.get(p.id)} for p in domain.dod_props()]


def _source_row(src: dict) -> dict:
    """run-status.schema.json writes `discovered_by` as {provider, at}; the panel shows the provider's name."""
    found = src.get("discovered_by")
    if isinstance(found, dict):
        src = {**src, "discovered_by": found.get("provider")}
    return src


def summarize(steps: list[dict], status: dict | None, domain: Domain | None = None) -> dict:
    """Everything the side panel shows, from the step stream plus status.json."""
    status = status or {}
    modes = {m: 0 for m in MODE_RANK}
    events = {"escalation": 0, "crystallization": 0, "repair": 0, "hard_stop": 0, "failure": 0, "verify": 0,
              "action_gate": 0, "limit_kill": 0, "quarantine": 0, "loop": 0}
    verdicts = {"achieved": 0, "not_achieved": 0, "uncertain": 0}
    values = 0
    for s in steps:
        if s.get("mode") in modes:
            modes[s["mode"]] += 1
        if s.get("event") in events:
            events[s["event"]] += 1
        if s.get("kind") == "verify":
            verdicts[(s.get("detail") or {}).get("verdict", "uncertain")] = \
                verdicts.get((s.get("detail") or {}).get("verdict", "uncertain"), 0) + 1
        values += len(s.get("value_ids") or [])
    phase = status.get("phase") or max((s.get("phase") or 1 for s in steps), default=1)
    metrics = status.get("metrics") or {}
    return {
        "state": status.get("state") or ("running" if steps else "waiting"),
        "phase": phase,
        "checkpoint": status.get("checkpoint_pending"),
        "updated_at": status.get("updated_at"),
        "live_view_url": status.get("live_view_url"),
        "sources": [_source_row(x) for x in status.get("sources") or [] if isinstance(x, dict)],
        "metrics": metrics,
        "fields": property_ratios(metrics, domain),
        "modes": modes,
        "mode_total": sum(modes.values()),
        "events": events,
        "verdicts": verdicts,
        "value_count": values,
        "step_count": len(steps),
        "loops": loop_summary(steps, metrics),
    }


CHECKPOINTS = [
    ("host", "Host check", "Sandbox host and runtime (gVisor runsc), CPU virtualization"),
    ("task", "Task and real result", "What was dispatched and what came back"),
    ("where", "Where it ran", "Hostname and uname from inside the pod"),
    ("isolation", "Isolation probe", "A non-allowlisted domain and a write outside the pod must be BLOCKED"),
    ("teardown", "Teardown", "Pod gone, no sandboxes left"),
    ("secrets", "Secret hygiene", "No keys in the pod; the metadata IP and the NetBird mesh are BLOCKED"),
]
KILL_LABELS = {"timeout": "wall-clock timeout", "memory": "memory cap", "pids": "process cap",
               "max_steps": "step cap", "steps": "step cap",
               "browser_closed": "browser closed", "cdp_unreachable": "browser unreachable"}
LIMIT_REASONS = ("timeout", "memory", "pids", "max_steps", "steps")


def job_stop_reason(job: dict) -> str | None:
    """Why a sandbox job ended early: jobs.schema.json `failure_reason` (earlier guessed shape: `killed_by`)."""
    return job.get("killed_by") or job.get("failure_reason")


def checkpoint_state(job: dict, key: str) -> str:
    """pass | fail | pending for one of the five proof checkpoints of a sandbox job."""
    cp = (job.get("checkpoints") or {}).get(key)
    if not cp or cp.get("not_run") is True:  # jobs.schema.json: a checkpoint the job never reached is not a failure
        return "pending"
    if key == "secrets":
        no_keys = not (cp.get("env_keys_found") or cp.get("files_with_keys"))
        probes = [str(cp.get(k, "")).upper() for k in ("metadata_ip", "mesh") if k in cp]
        probes += [str(p.get("result", "")).upper() for p in cp.get("probes") or []]
        blocked = bool(probes) and all(p == "BLOCKED" for p in probes)
        return "pass" if cp.get("ok", True) and no_keys and blocked else "fail"
    if key == "isolation":
        probes = cp.get("probes") or []
        blocked = bool(probes) and all(str(p.get("result", "")).upper() == "BLOCKED" for p in probes)
        return "pass" if blocked else ("fail" if probes else "pending")
    return "pass" if cp.get("ok") else "fail"


def isolation_tier(runtime: str | None) -> dict | None:
    """Where a runtime sits on the track deck's isolation ladder ("a container is not a sandbox")."""
    r = (runtime or "").lower()
    if not r:
        return None
    if any(k in r for k in ("firecracker", "kata", "microvm", "libkrun")):
        return {"tier": 4, "name": "microVM", "ok": True, "note": "own kernel; the VM is thrown away"}
    if "runsc" in r or "gvisor" in r:
        return {"tier": 3, "name": "gVisor user-space kernel", "ok": True, "note": "syscalls intercepted; only the sandbox process can die"}
    if "runc" in r or "docker" in r:
        return {"tier": 2, "name": "container (runc)", "ok": False, "note": "shared kernel: a container is not a sandbox"}
    return {"tier": None, "name": runtime, "ok": False, "note": "runtime not on the ladder"}


def proof(jobs: list[dict], steps: list[dict] | None = None) -> dict:
    """Aggregate proof over all jobs, plus the latest job whose checkpoints all resolved (the one to show judges)."""
    counts = {key: {"pass": 0, "fail": 0, "pending": 0} for key, _, _ in CHECKPOINTS}
    for job in jobs:
        for key, _, _ in CHECKPOINTS:
            counts[key][checkpoint_state(job, key)] += 1
    complete = [j for j in jobs if all(checkpoint_state(j, k) != "pending" for k, _, _ in CHECKPOINTS)]
    featured = complete[-1] if complete else (jobs[-1] if jobs else None)
    runtime = (((featured or {}).get("checkpoints") or {}).get("host") or {}).get("runtime")
    kills = [s for s in steps or [] if s.get("event") == "limit_kill"]
    killed = [{"job_id": j.get("job_id"), "reason": job_stop_reason(j), "limits": j.get("limits"),
               "usage": j.get("usage")} for j in jobs if job_stop_reason(j)]
    killed += [{"job_id": s.get("step_id"), "reason": _details(s, "kill")["reason"], "limits": None, "usage": None,
                "source_id": s.get("source_id")} for s in kills]
    return {"jobs": len(jobs), "counts": counts, "featured": featured, "recent": jobs[-8:][::-1],
            "checkpoints": CHECKPOINTS, "state": checkpoint_state, "tier": isolation_tier(runtime),
            "killed": killed[-5:][::-1], "killed_total": len(killed), "kill_labels": KILL_LABELS,
            "with_limits": sum(1 for j in jobs if j.get("limits"))}


# Replayer ------------------------------------------------------------------------------------------------------

def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _ts(step: dict) -> float:
    try:
        return datetime.fromisoformat(str(step.get("ts"))).timestamp()
    except ValueError:
        return 0.0


class Replayer:
    """Replay a finished run from a file lake into the live feed of the same (or another) file lake."""

    def __init__(self, root: Path, source_run_id: str | None = None, *, target_root: Path | None = None,
                 case_id: str | None = None, new_run_id: str | None = None, duration: float = 45.0,
                 speed: float | None = None, publish_gold: bool = True):
        self.source = GoldStore(LocalSource(root), case_id)
        self.run = self.source.run(source_run_id)
        self.case_id = self.source.case_id
        self.target = Path(target_root or root)
        self.new_run_id = new_run_id or f"{self.run.run_id}-replay-{datetime.now(UTC):%H%M%S}"
        self.duration = duration
        self.speed = speed  # when set, replay at N x the recorded pace (long gaps still capped) instead of a duration
        self.publish_gold = publish_gold
        recorded = self.source.live_steps(self.run.run_id)  # prefer the live record: it has screenshot keys
        steps = recorded or list(self.run.trace)
        self.steps = sorted(steps, key=_ts)
        self.jobs_by_step: dict[str, list[dict]] = {}
        for job in self.source.live_jobs(self.run.run_id):
            self.jobs_by_step.setdefault(job.get("step_id"), []).append(job)
        self.recorded_status = self.source.live_status(self.run.run_id) or {}
        self._stop = threading.Event()

    @property
    def run_dir(self) -> Path:
        return self.target / "runs" / self.case_id / self.new_run_id

    def _thumb(self, step: dict) -> str | None:
        for vid in step.get("value_ids") or []:
            ref = self.run.values.get(vid)
            for e in (ref.data.get("evidence") if ref else None) or []:
                if e.get("screenshot_key") and e.get("source_id") == step.get("source_id"):
                    return e["screenshot_key"]
        return None

    def _partial_metrics(self, emitted: set[str]) -> dict:
        """Metrics as far as the replay has got, in §11 shape."""
        seen = []
        for e in self.run.entities:
            props = {name: (v if v.get("value_id") in emitted else {**v, "status": "missing", "evidence": []})
                     for name, v in (e.get("properties") or {}).items() if isinstance(v, dict)}
            if any(v.get("value_id") in emitted for v in (e.get("properties") or {}).values() if isinstance(v, dict)):
                seen.append({**e, "properties": props})
        m = dod.compute(seen, self.run.domain)
        m.pop("primary_class", None)
        return m

    def _status(self, state: str, phase: int, emitted: set[str], health: dict) -> dict:
        types = {}
        for ent in self.run.entities:
            for v in (ent.get("properties") or {}).values():
                for e in (v.get("evidence") if isinstance(v, dict) else None) or []:
                    types.setdefault(e.get("source_id"), e.get("source_type"))
        return {
            "run_id": self.new_run_id, "state": state, "phase": phase, "checkpoint_pending": None,
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "sources": [{"source_id": sid, "source_type": types.get(sid) or "unknown", "health": h}
                        for sid, h in health.items()],
            "metrics": self._metrics_with_provenance(emitted),
            "generated_by": self.generated_by,
        }

    @property
    def generated_by(self) -> dict:
        """Provenance of the replayed run: the recording's own, never upgraded."""
        gen = self.recorded_status.get("generated_by") or self.run.metrics.get("generated_by")
        if gen:
            return gen
        return {"backend": self.run.inference_backend or "recorded", "model": "unknown",
                "at": datetime.now(UTC).isoformat(timespec="seconds")}

    def _metrics_with_provenance(self, emitted: set[str]) -> dict:
        metrics = self._partial_metrics(emitted)
        metrics["inference_backend"] = self.generated_by["backend"]
        return metrics

    def stop(self) -> None:
        self._stop.set()

    def play(self, sleep=time.sleep) -> str:
        """Run the replay to completion (blocking). Returns the new run id."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        live = self.run_dir / "trace.live.jsonl"
        live.write_text("")
        jobs_file = self.run_dir / "jobs.jsonl"
        jobs_file.write_text("")
        _write_atomic(self.target / "runs" / self.case_id / "latest.json", json.dumps({"run_id": self.new_run_id}))
        span = max(_ts(self.steps[-1]) - _ts(self.steps[0]), 1e-9) if self.steps else 1.0
        scale = 1.0 / self.speed if self.speed else self.duration / span
        emitted: set[str] = set()
        health: dict[str, dict] = {}
        last_status = 0.0
        prev = _ts(self.steps[0]) if self.steps else 0.0
        for step in self.steps:
            if self._stop.is_set():
                break
            sleep(max(0.0, min((_ts(step) - prev) * scale, 5.0)))
            prev = _ts(step)
            rec = dict(step, run_id=self.new_run_id)
            if not rec.get("screenshot_key"):
                thumb = self._thumb(step)
                if thumb:
                    rec["screenshot_key"] = thumb
            with live.open("a") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            with jobs_file.open("a") as fh:  # complete records only: jobs.schema.json requires all five checkpoints
                for job in self.jobs_by_step.get(step.get("step_id"), []):
                    fh.write(json.dumps(dict(job, run_id=self.new_run_id), ensure_ascii=False) + "\n")
            emitted.update(step.get("value_ids") or [])
            if step.get("phase") == 5 and step.get("source_id"):
                h = health.setdefault(step["source_id"], {"ok": 0, "failed": 0, "yield": 0})
                if annotate([step])[0]["event"] == "failure":
                    h["failed"] += 1
                else:
                    h["ok"] += 1
                h["yield"] += len(step.get("value_ids") or [])
            now = time.monotonic()
            if now - last_status > 0.4 or step is self.steps[-1]:
                _write_atomic(self.run_dir / "status.json",
                              json.dumps(self._status("running", step.get("phase") or 1, emitted, health)))
                last_status = now
        done = not self._stop.is_set()
        _write_atomic(self.run_dir / "status.json", json.dumps(
            self._status("done" if done else "failed", 5, emitted, health)))
        if done and self.publish_gold:
            self._publish_gold()
        return self.new_run_id

    def _publish_gold(self) -> None:
        out = self.target / "gold" / self.case_id / self.new_run_id
        out.mkdir(parents=True, exist_ok=True)
        trace = [dict(s, run_id=self.new_run_id) for s in self.run.trace]
        (out / "trace.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in trace))
        src = self.source.prefix + "/" + self.run.run_id
        for name in ("entities.jsonl", "ontology.json", "dod-queries.json"):
            data = self.source.source.read(f"{src}/{name}")  # copied verbatim
            if data is not None:
                (out / name).write_bytes(data)
        metrics = copy.deepcopy(self.run.metrics)
        metrics["run_id"] = self.new_run_id
        (out / "metrics.json").write_text(json.dumps(metrics, indent=2))
        _write_atomic(self.target / "gold" / self.case_id / "latest.json", json.dumps({"run_id": self.new_run_id}))

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.play, name="pa-replay", daemon=True)
        thread.start()
        return thread
