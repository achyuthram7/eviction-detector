"""Synthetic generator: valid rows, edge cases present, ground truth kept apart."""

from __future__ import annotations

import copy
import re
from datetime import date, timedelta

import pytest

from core.importer import validate_row
from core.settings import get_settings
from synth.generator import build_world


@pytest.fixture(scope="module")
def world():
    cfg = copy.deepcopy(get_settings().synthetic)
    cfg.update(history_cases=900, live_cases=60, history_years=1)
    cfg["plaintiffs"]["count"] = 60
    return build_world(cfg, date(2026, 3, 16))


def test_all_exported_rows_are_valid(world):
    rows = world.export(world.snapshot_date)
    assert len(rows) > 800
    for r in rows:
        record, reasons = validate_row(r)
        assert reasons == [], (r["case_number"], reasons)


def test_names_and_phones_are_synthetic(world):
    for c in world.cases[:300]:
        for phone in c.household.phones:
            assert phone is None or re.fullmatch(r"\d{3}-555-01\d{2}", phone)
        assert c.court_id[:2] in {"97", "98", "99"}  # no such Indiana counties


def test_exports_never_contain_ground_truth(world):
    row = world.export(world.snapshot_date)[0]
    assert not {"latent_p", "true_resolved", "label_flipped", "plaintiff_type"} & set(row)


def test_edge_cases_are_generated(world):
    edges = world.edge_cases
    for key in (
        "multiple_tenants_answer_differently",
        "reused_token",
        "dismissed_before_hearing",
        "continuance_history",
        "refiled",
        "unmatched_fallback",
    ):
        assert key in edges, key


def test_day2_demo_changes(world):
    t = world.anchor
    y = t - timedelta(days=1)
    day1 = {r["case_number"]: r for r in world.export(y, active_since=world.snapshot_date)}
    day2 = {r["case_number"]: r for r in world.export(t, active_since=y)}
    d = world.demo_cases
    assert day2[d["day2_continuance"]]["latest_event"] == "continued"
    assert (
        day2[d["day2_continuance"]]["hearing_datetime"]
        > day1[d["day2_continuance"]]["hearing_datetime"]
    )
    assert day2[d["day2_dismissal"]]["latest_event"] == "dismissed"
    assert day2[d["day2_diversion_stay"]]["latest_event"] == "diversion_stay"
    assert day2[d["day2_newly_sealed"]]["sealed_flag"] == "true"
    assert day1[d["day2_newly_sealed"]]["sealed_flag"] == "false"
    assert d["day1_new_filing_for_qr_demo"] in day1


def test_outcome_mix_roughly_matches_config(world):
    closed = [c for c in world.cases if not c.is_open(world.snapshot_date)]
    rate = sum(c.recorded_resolved for c in closed) / len(closed)
    assert 0.40 < rate < 0.62
