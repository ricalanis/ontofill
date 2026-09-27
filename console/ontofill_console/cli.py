"""`ontofill-console serve | replay | fixtures`."""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
import threading
from pathlib import Path

from . import fixtures, live


def _scratch_copy(lake: Path, scratch: Path | None) -> Path:
    """Replays write a live feed, so they only ever run on a writable copy of a local lake, never the lake itself."""
    target = Path(scratch) if scratch else Path(tempfile.mkdtemp(prefix="ontofill-console-replay-"))
    if target.resolve() == Path(lake).resolve():
        raise SystemExit("--scratch must differ from the case's lake")
    shutil.copytree(lake, target, dirs_exist_ok=True)
    return target


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .web import create_app, settings_from_env

    if args.identity:
        os.environ["ONTOFILL_CONSOLE_IDENTITY"] = args.identity
    if args.fixtures:
        os.environ["ONTOFILL_CONSOLE_CASES"] = fixtures.generate(Path(args.fixtures))
    settings = settings_from_env()
    if args.replay:
        case_id, _, run_id = args.replay.partition(":")
        case = settings.cases.get(case_id)
        root = getattr(getattr(case, "store", None), "source", None)
        if case is None or not hasattr(root, "root"):
            raise SystemExit(f"--replay needs a registered case with a local lake: {case_id!r}")
        scratch = _scratch_copy(root.root, args.scratch)
        from .gold import store_for_case

        case.store = store_for_case(case.root, str(scratch))
        replayer = live.Replayer(scratch, run_id or None, duration=args.duration)
        print(f"replaying {replayer.run.run_id} as {replayer.new_run_id} in scratch lake {scratch}")
        threading.Timer(args.delay, replayer.start).start()
    uvicorn.run(create_app(settings), host=args.host, port=args.port)
    return 0


def _replay(args: argparse.Namespace) -> int:
    scratch = _scratch_copy(Path(args.lake), args.scratch)
    replayer = live.Replayer(scratch, args.run_id, duration=args.duration, new_run_id=args.new_run_id)
    print(
        f"replaying {replayer.run.run_id} -> {replayer.new_run_id} in scratch lake {scratch} over {args.duration:.0f}s"
    )
    replayer.play()
    return 0


def _fixtures(args: argparse.Namespace) -> int:
    print(f"ONTOFILL_CONSOLE_CASES={fixtures.generate(Path(args.out))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ontofill-console", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the console (cases from ONTOFILL_CONSOLE_CASES)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8410)
    s.add_argument("--identity", choices=("sso", "local"), help="override ONTOFILL_CONSOLE_IDENTITY (local: dev only)")
    s.add_argument("--fixtures", metavar="DIR", help="generate the synthetic cases in DIR and serve them")
    s.add_argument("--replay", metavar="CASE[:RUN]", help="also replay a finished run of CASE as live (scratch lake)")
    s.add_argument("--scratch", type=Path, help="scratch lake for --replay (default: a new temp dir)")
    s.add_argument("--duration", type=float, default=45.0)
    s.add_argument("--delay", type=float, default=2.0)
    s.set_defaults(fn=_serve)
    r = sub.add_parser("replay", help="replay a finished run from a local lake into a scratch copy")
    r.add_argument("lake", type=Path)
    r.add_argument("--run-id")
    r.add_argument("--new-run-id")
    r.add_argument("--scratch", type=Path)
    r.add_argument("--duration", type=float, default=45.0)
    r.set_defaults(fn=_replay)
    f = sub.add_parser("fixtures", help="write the synthetic cases and print the registry spec")
    f.add_argument("out", nargs="?", default=".cache/console-fixtures")
    f.set_defaults(fn=_fixtures)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
