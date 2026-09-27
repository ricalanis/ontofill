"""`ontofill-containment --case <case_dir> --run-id <id>` entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ontofill-containment",
        description="Run the containment fixtures against a run's feed (append-only).",
    )
    parser.add_argument("--case", type=Path, required=True, help="path to the case directory")
    parser.add_argument(
        "--run-id", help="run id to append to (default containment-demo-<yyyymmddhhmm>)"
    )
    args = parser.parse_args(argv)

    from .main import run_containment

    try:
        summary = run_containment(args.case, args.run_id)
    except Exception as exc:  # noqa: BLE001 - the CLI reports the failure and exits nonzero
        print(f"containment failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(
        "run_id={run_id} case_id={case_id} quarantine={q} limit_kill={k} blocked={b}".format(
            run_id=summary["run_id"],
            case_id=summary["case_id"],
            q=summary["hostile"]["quarantine"],
            k=summary["destructive"]["limit_kill"],
            b=",".join(summary["hostile"]["blocked"]) or "none",
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
