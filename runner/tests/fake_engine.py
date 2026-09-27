"""Stands in for `ontofill run`: writes the run feed like the engine, then exits per FAKE_MODE.

FAKE_MODE: pause:<checkpoint>[:<reason>] (exit 3) | done (exit 0) | fail (noisy stderr, exit 1)
  | fail_phase:<n> (engine-like failure, exit 1) | sleep (wait for a signal)
FAKE_USD: est_usd to record in this run's trace (for budget tests).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("case_dir")
p.add_argument("--to-phase")
p.add_argument("--run-id")
p.add_argument("--budget-usd")
p.add_argument("--lake")
a = p.parse_args()
case_id = os.environ.get("FAKE_CASE_ID", "c1")
run = Path(a.lake) / "runs" / case_id / a.run_id
run.mkdir(parents=True, exist_ok=True)
(Path(a.lake) / "runs" / case_id / "latest.json").write_text(json.dumps({"run_id": a.run_id}))
with open(Path(a.case_dir).parent / "calls.jsonl", "a") as fh:
    fh.write(json.dumps({"run_id": a.run_id, "to_phase": a.to_phase, "budget": a.budget_usd,
                        "secret_seen": "ENGINE_SECRET" in os.environ}) + "\n")
if os.environ.get("FAKE_USD"):
    with open(run / "trace.live.jsonl", "a") as fh:
        fh.write(json.dumps({"step_id": "s", "usage": {"est_usd": float(os.environ["FAKE_USD"])}}) + "\n")
mode = os.environ.get("FAKE_MODE", "done")
if mode.startswith("pause:"):
    cp, _, reason = mode.split(":", 1)[1].partition(":")
    (run / "status.json").write_text(json.dumps({"state": "paused", "checkpoint_pending": cp,
                                                 **({"reason": reason} if reason else {})}))
    print(f"state=paused checkpoint_pending={cp} reason={reason or 'awaiting approval'}")
    sys.exit(3)
if mode.startswith("fail_phase:"):  # like the engine: status failed + phase, the exception message last on stderr
    (run / "status.json").write_text(json.dumps({"state": "failed", "phase": int(mode.split(":")[1]),
                                                 "checkpoint_pending": None}))
    print("Traceback (most recent call last):\n  ...", file=sys.stderr)
    print("RuntimeError: fan-out loop stopped at the iteration cap token=abc123secretvalue", file=sys.stderr)
    sys.exit(1)
if mode == "done":
    (run / "status.json").write_text(json.dumps({"state": "done"}))
    sys.exit(0)
if mode == "fail":
    print("Traceback: boom api_key=sk-THISISASECRETVALUE1234567890abcdef token: abc", file=sys.stderr)
    sys.exit(1)
(run / "status.json").write_text(json.dumps({"state": "running"}))
while True:
    time.sleep(0.1)
