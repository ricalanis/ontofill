"""Contract-stable command-line entry point."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ontofill")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run one or more case phases")
    run.add_argument("case_dir", type=Path)
    run.add_argument("--from-phase", type=int, choices=range(1, 6), default=1)
    run.add_argument("--to-phase", type=int, choices=range(1, 6), default=5)
    run.add_argument("--run-id")
    run.add_argument("--budget-usd", type=float)
    run.add_argument("--preview-past-checkpoints", action="store_true")

    refine = commands.add_parser("refine", help="Rebuild silver and gold from stored bronze")
    refine.add_argument("case_dir", type=Path)
    refine.add_argument("--run-id")

    export = commands.add_parser("export", help="Write the gold export")
    export.add_argument("case_dir", type=Path)
    export.add_argument("--run-id")

    cells = commands.add_parser("cells", help="Manage sandbox browser cells")
    cell_commands = cells.add_subparsers(dest="cells_command", required=True)
    serve = cell_commands.add_parser("serve", help="Serve the loopback cell API")
    serve.add_argument("--port", type=int, default=8766)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "cells":
        if not 1 <= args.port <= 65535:
            parser.error("--port must be between 1 and 65535")
        token = os.environ.get("ONTOFILL_CELLS_TOKEN", "")
        if not token:
            parser.error("ONTOFILL_CELLS_TOKEN must be set")
        docker_host = os.environ.get("ONTOFILL_SANDBOX_DOCKER_HOST", "")
        parsed = urlsplit(docker_host)
        if (
            parsed.scheme != "ssh"
            or not parsed.hostname
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            parser.error("ONTOFILL_SANDBOX_DOCKER_HOST must be ssh://[user@]host")

        from ontofill.sandbox.cell_api import serve_cells
        from ontofill.sandbox.cells import CellManager

        server = serve_cells(CellManager(), token=token, port=args.port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    if args.command == "run" and args.from_phase > args.to_phase:
        parser.error("--from-phase must be at most --to-phase")
    if args.command == "run" and args.budget_usd is not None and args.budget_usd < 0:
        parser.error("--budget-usd must be nonnegative")
    if not args.case_dir.is_dir():
        parser.error(f"case directory does not exist: {args.case_dir}")

    from ontofill.workflow import export_case, refine_case, run_case

    if args.command == "run":
        return run_case(
            args.case_dir,
            from_phase=args.from_phase,
            to_phase=args.to_phase,
            run_id=args.run_id,
            budget_usd=args.budget_usd,
            preview_past_checkpoints=args.preview_past_checkpoints,
        )
    if args.command == "refine":
        return refine_case(args.case_dir, run_id=args.run_id)
    return export_case(args.case_dir, run_id=args.run_id)
