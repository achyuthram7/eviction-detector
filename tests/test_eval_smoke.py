"""Smoke test: every evaluation command runs end to end on a small synthetic world."""

from __future__ import annotations

import copy
from datetime import date

from core.constants import BANNER_TEXT
from core.db import session_scope
from core.importer import import_csv
from core.scoring_service import train_production_model
from core.settings import get_settings
from core.users import create_demo_users
from eval import eval_learning, eval_model, expert_compare, expert_pack, summary, usability
from eval.common import DISCLAIMER
from synth.generator import build_world
from synth.seed import apply_seed_intakes, write_world_files


def test_all_evaluations_produce_reports():
    cfg = copy.deepcopy(get_settings().synthetic)
    cfg.update(history_cases=2000, live_cases=120, history_years=2)
    cfg["plaintiffs"]["count"] = 120
    files = write_world_files(build_world(cfg, date(2026, 3, 16)))
    with session_scope() as s:
        create_demo_users(s)
        import_csv(s, files["history"])
        import_csv(s, files["live"])
    apply_seed_intakes()
    train_production_model()

    m = eval_model.run()
    assert m["validation_at_threshold"]["recall"] >= 0.95
    assert 0.5 < m["auc"] <= 1.0
    learn = eval_learning.run()
    assert learn["versions"] and learn["extended"]
    pack = expert_pack.run()
    assert pack["scenarios"] == 50
    comp = expert_compare.run()
    assert comp["simulated"] is True and -1 <= comp["spearman"] <= 1
    usability.run()
    summary.main()

    reports = get_settings().reports_dir
    for name in ("model_eval.md", "learning.md", "expert_compare.md", "usability.md", "summary.md"):
        text = (reports / name).read_text(encoding="utf-8")
        assert DISCLAIMER in text, name
    for png in (
        "roc.png",
        "calibration.png",
        "staircase.png",
        "learning_weekly.png",
        "learning_by_size.png",
        "intake_times.png",
    ):
        assert (reports / "figures" / png).stat().st_size > 1000, png
    packet = (reports / "expert_pack" / "packet.html").read_text(encoding="utf-8")
    assert BANNER_TEXT in packet and "Scenario S50" in packet
    assert "EV-" not in packet  # no case numbers in the rater packet
