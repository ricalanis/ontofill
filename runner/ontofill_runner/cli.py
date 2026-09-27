"""`ontofill-runner serve` (the systemd service) and `ontofill-runner status`."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import config_from_env
from .runner import Runner
from .state import State


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ontofill-runner", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="watch the registered cases and resume decided runs")
    serve.add_argument("--once", action="store_true", help="one poll, then exit (tests, cron)")
    sub.add_parser("status", help="print each case's runner state as JSON")
    sub.add_parser("bronze-probe", help="append one bronze sample to <state>/bronze-probe.jsonl (a timer runs it)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    cfg = config_from_env()
    if args.cmd == "serve":
        Runner(cfg).serve(once=args.once)
        return 0
    if args.cmd == "bronze-probe":
        from .bronze_probe import append, sample

        line = sample(cfg)
        append(cfg.state_dir, line)
        print(json.dumps(line))
        return 0
    st = State(cfg.state_dir)
    print(json.dumps({"kill_switch": st.killed, "cases": {cid: st.status(cid) for cid in cfg.cases}}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
