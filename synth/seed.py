"""`make seed`: generate synthetic data, rebuild the database, import, train and score."""

from __future__ import annotations

import csv
import json
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path

from core import clock
from core.db import init_db, session_scope
from core.importer import import_csv, write_export_csv
from core.settings import get_settings
from core.users import create_demo_users
from synth import paths
from synth.generator import World, build_world


def resolve_anchor(cfg: dict) -> date:
    value = str(cfg.get("anchor_date", "today")).strip()
    return clock.today() if value in ("", "today") else date.fromisoformat(value)


def _json_default(value):
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(type(value))


def write_world_files(world: World) -> dict[str, Path]:
    s = world.snapshot_date
    y = world.anchor - timedelta(days=1)
    t = world.anchor
    closed = [c.export_row(s) for c in world.cases if c.filing_date <= s and not c.is_open(s)]
    live = [c.export_row(s) for c in world.cases if c.is_open(s)]
    out = {
        "history": paths.history_csv(),
        "live": paths.live_csv(),
        "day1": paths.demo_dir() / "day1.csv",
        "day2": paths.demo_dir() / "day2.csv",
        "truth": paths.ground_truth_path(),
    }
    write_export_csv(out["history"], closed)
    write_export_csv(out["live"], live)
    write_export_csv(out["day1"], world.export(y, active_since=s) + world.malformed_day1)
    write_export_csv(out["day2"], world.export(t, active_since=y) + world.malformed_day2)

    truth_rows = [c.truth_row() for c in world.cases]
    out["truth"].parent.mkdir(parents=True, exist_ok=True)
    with open(out["truth"], "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(truth_rows[0]))
        writer.writeheader()
        writer.writerows(truth_rows)

    seed_payload = {
        "snapshot_date": s,
        "intakes": world.intakes,
        "policies": world.policies,
        "fallbacks": world.fallbacks,
        "call_logs": world.call_logs,
        "invited": world.invited,
    }
    paths.intake_json().write_text(json.dumps(seed_payload, default=_json_default, indent=1))
    paths.edge_cases_json().write_text(json.dumps(world.edge_cases, indent=2))
    paths.demo_cases_json().parent.mkdir(parents=True, exist_ok=True)
    paths.demo_cases_json().write_text(json.dumps(world.demo_cases, indent=2) + "\n")
    return out


def run_seed(verbose: bool = True) -> World:
    started = _time.perf_counter()
    settings = get_settings()
    cfg = settings.synthetic
    anchor = resolve_anchor(cfg)
    world = build_world(cfg, anchor)
    files = write_world_files(world)

    settings.model_path.unlink(missing_ok=True)  # never score with a model from an old seed
    init_db(drop=True)
    with session_scope() as session:
        create_demo_users(session)
        runs = [
            import_csv(session, files["history"], filename="history.csv"),
            import_csv(session, files["live"], filename="live.csv"),
        ]
    apply_seed_intakes()
    train_and_score()

    if verbose:
        s = world.snapshot_date
        print(f"Anchor date {anchor} | seeded snapshot as of {s}")
        for run in runs:
            print(f"  imported {run.filename}: {run.inserted} inserted, {run.rejected} rejected")
        print(f"  live docket (open at snapshot): {sum(1 for c in world.cases if c.is_open(s))}")
        print(f"  seeded intakes: {len(world.intakes)} | standing policies: {len(world.policies)}")
        print(f"  demo files: {files['day1']} , {files['day2']}")
        print(f"  done in {_time.perf_counter() - started:.1f}s")
    return world


def apply_seed_intakes() -> None:
    """Load seeded intake answers, policies, fallbacks, invitations and call logs."""
    from synth.seed_intake import apply_seed_payload

    apply_seed_payload(json.loads(paths.intake_json().read_text()))


def train_and_score() -> None:
    """Train the production base model, then score every open case."""
    from core.scoring_service import train_production_model

    model = train_production_model()
    val = model.metrics["validation_at_high"]
    print(
        f"  model {model.version}: trained on {model.n_train} closed cases; "
        f"t_high={model.threshold_high:.3f} (validation recall {val['recall']:.3f}, "
        f"FPR {val['fpr']:.3f}); t_low={model.threshold_low:.3f}"
    )
