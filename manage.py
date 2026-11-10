"""Project command runner. `python manage.py <command>` works on any OS; the Makefile wraps it.

Commands: setup, seed, run, demo, import, test, lint, eval-model, eval-learning, expert-pack,
expert-compare, usability, summary, eval-all, purge
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _run(cmd: list[str]) -> int:
    print("$ " + " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=ROOT)


def cmd_setup(_args) -> int:
    return _run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])


def cmd_seed(_args) -> int:
    from synth.seed import run_seed

    run_seed()
    return 0


def cmd_run(args) -> int:
    import uvicorn

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=False)
    return 0


def cmd_import(args) -> int:
    from core.db import session_scope
    from core.importer import import_csv

    with session_scope() as session:
        run = import_csv(session, Path(args.file))
        print(
            f"{run.filename}: {run.rows_total} rows | {run.inserted} inserted | "
            f"{run.updated} updated | {run.unchanged} unchanged | {run.rejected} rejected | "
            f"{run.newly_restricted} newly restricted | {run.reconciliation_flags} flags"
        )
        if run.file_error:
            print("File error:", run.file_error)
        for rej in run.rejections:
            print(f"  row {rej.row_number} ({rej.case_number_raw}): {'; '.join(rej.reasons)}")
    return 0


def cmd_test(_args) -> int:
    from core.settings import get_settings

    # Same folder the summary reads (REPORTS_DIR when set, e.g. on a hosted disk).
    reports = get_settings().reports_dir
    reports.mkdir(parents=True, exist_ok=True)
    return _run([sys.executable, "-m", "pytest", f"--junitxml={reports / 'test_results.xml'}"])


def cmd_lint(_args) -> int:
    rc = _run([sys.executable, "-m", "ruff", "check", "."])
    return rc or _run([sys.executable, "-m", "ruff", "format", "--check", "."])


def _eval(module: str, extra: list[str] | None = None) -> int:
    return _run([sys.executable, "-m", module, *(extra or [])])


def cmd_eval_model(_args) -> int:
    return _eval("eval.eval_model")


def cmd_eval_learning(_args) -> int:
    return _eval("eval.eval_learning")


def cmd_expert_pack(_args) -> int:
    return _eval("eval.expert_pack")


def cmd_expert_compare(args) -> int:
    return _eval("eval.expert_compare", ["--ratings", args.ratings] if args.ratings else [])


def cmd_usability(_args) -> int:
    return _eval("eval.usability")


def cmd_summary(_args) -> int:
    return _eval("eval.summary")


def cmd_eval_all(args) -> int:
    for fn in (
        cmd_eval_model,
        cmd_eval_learning,
        cmd_expert_pack,
        cmd_expert_compare,
        cmd_usability,
        cmd_summary,
    ):
        rc = fn(args)
        if rc:
            return rc
    return 0


def cmd_demo(args) -> int:
    """Fresh seed + every report, ready for the README demo."""
    rc = cmd_seed(args)
    if rc:
        return rc
    rc = cmd_test(args)
    if rc:
        print("WARNING: some tests failed; continuing so the demo data is still built.")
    rc = cmd_eval_all(argparse.Namespace(ratings=None))
    if rc:
        return rc
    from core.users import DEMO_USERS

    print("\nDemo ready. Start the app with:  python manage.py run   (or: make run)")
    print("Then open http://localhost:8000  — demo logins:")
    for username, _display, role, password in DEMO_USERS:
        print(f"  {role:<12} {username:<12} {password}")
    print("Demo case numbers: data/demo/demo_cases.json")
    return 0


def cmd_purge(args) -> int:
    from core.db import session_scope
    from core.retention import purge

    with session_scope() as session:
        result = purge(session, days=args.days)
    print(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup")
    sub.add_parser("seed")
    p = sub.add_parser("run")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    sub.add_parser("demo")
    p = sub.add_parser("import")
    p.add_argument("file")
    sub.add_parser("test")
    sub.add_parser("lint")
    for name in ("eval-model", "eval-learning", "expert-pack", "usability", "summary"):
        sub.add_parser(name)
    p = sub.add_parser("expert-compare")
    p.add_argument("--ratings", default=None)
    p = sub.add_parser("eval-all")
    p.set_defaults(ratings=None)
    p = sub.add_parser("purge")
    p.add_argument("--days", type=int, default=None)
    args = parser.parse_args(argv)
    handler = globals()["cmd_" + args.command.replace("-", "_")]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
