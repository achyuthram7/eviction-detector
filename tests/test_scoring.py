"""Layer 2 rules, tiers, pathways, confidence, reasons and threshold selection."""

from __future__ import annotations

import copy
from datetime import date

import numpy as np
import pytest

from core.constants import (
    AGREED_MOVE_OUT,
    DISMISSAL_ON_PAYMENT,
    HIGH,
    LEGAL_AID,
    LOW,
    NEEDS_OUTREACH,
    NO_RESPONSE,
    PAY_AND_STAY,
)
from core.model import metrics_at, recall_threshold
from core.scoring import (
    BaseResult,
    LandlordAnswers,
    TenantAnswers,
    goals_alignment,
    overlap_check,
    score_case,
)

T_HIGH, T_LOW = 0.4, 0.2


def base(prob: float = 0.5, landlord_n: int = 30) -> BaseResult:
    return BaseResult(
        prob=prob,
        contributions={
            "landlord_rate": 0.4,
            "plaintiff_attorney": -0.1,
            "log_amount": -0.2,
            "amount_unknown": 0.0,
            "court": 0.05,
            "days_to_hearing": 0.02,
            "prior_filings_12m": 0.0,
            "defendant_count": 0.01,
        },
        values={
            "landlord_rate": 0.62,
            "prior_filings_12m": 0,
            "plaintiff_attorney": 1.0,
            "defendant_count": 1,
            "log_amount": 7.6,
            "amount_unknown": 0.0,
            "days_to_hearing": 27,
            "court_id": "97K01",
        },
        z={"log_amount": 0.3, "days_to_hearing": -0.1},
        landlord_n=landlord_n,
        global_rate=0.5,
    )


def tenant(**kw) -> TenantAnswers:
    defaults = dict(pay_30d=400.0, pay_monthly=200.0, wants="stay", willing_mediator="yes")
    return TenantAnswers(**{**defaults, **kw})


def landlord(**kw) -> LandlordAnswers:
    defaults = dict(
        accepts_plan=True,
        max_plan_months=4,
        min_to_dismiss=1200.0,
        goal="rent",
        willing_mediator="yes",
        amount_owed_now=1400.0,
    )
    return LandlordAnswers(**{**defaults, **kw})


# --- overlap check -------------------------------------------------------------------------------


def test_overlap_when_plan_covers_minimum():
    cls, info = overlap_check([tenant()], landlord(), 1400.0)  # 400 + 200*4 = 1200
    assert cls == "overlap" and info["capacity"] == 1200.0


def test_bridgeable_when_gap_within_percentage():
    cls, info = overlap_check([tenant(pay_monthly=180.0)], landlord(), 1400.0)  # gap 80 < 15%
    assert cls == "bridgeable" and info["bridged_by"] == "small gap"


def test_bridgeable_when_pending_assistance_covers_gap():
    t = tenant(pay_monthly=50.0, assistance_status="pending", assistance_amount=900.0)
    cls, info = overlap_check([t], landlord(), 1400.0)  # gap 600 covered by 900
    assert cls == "bridgeable" and info["bridged_by"] == "rental assistance"


def test_bridgeable_when_fee_waiver_covers_gap():
    cls, info = overlap_check(
        [tenant(pay_monthly=100.0)], landlord(waive_late_fees=True, fees_included=500.0), 1400.0
    )  # gap 400 <= 500 of waivable fees
    assert cls == "bridgeable" and info["bridged_by"] == "fee waiver"


def test_wide_gap():
    cls, _ = overlap_check([tenant(pay_30d=0.0, pay_monthly=20.0)], landlord(), 1400.0)
    assert cls == "wide"


def test_policy_percentage_minimum_uses_amount_owed():
    ll = landlord(
        min_to_dismiss=None, min_to_dismiss_pct=50.0, amount_owed_now=None, source="policy"
    )
    cls, info = overlap_check([tenant()], ll, 2000.0)
    assert info["minimum"] == 1000.0 and cls == "overlap"


def test_goals_table():
    assert goals_alignment("stay", "rent") == "aligned"
    assert goals_alignment("move", "possession") == "aligned"
    assert goals_alignment("stay", "possession") == "conflict"
    assert goals_alignment("move", "rent") == "partial"
    assert goals_alignment("stay", "both") == "partial"
    assert goals_alignment(None, "rent") == "unknown"


# --- protection rule and categories -------------------------------------------------------


def test_both_willing_wide_gap_lowers_confidence_not_below_referral_threshold():
    t = tenant(pay_30d=0.0, pay_monthly=10.0, willing_mediator="yes")
    ll = landlord(goal="possession", willing_mediator="yes")  # conflict + wide gap
    res = score_case(base(0.15), [t], ll, t_high=T_HIGH, t_low=T_LOW)
    assert res.overlap_class == "wide"
    assert res.final_prob >= T_HIGH and res.potential == HIGH
    assert "overlap" not in res.adjustments  # gap did not move the probability
    assert any(r.startswith("Lowers confidence") for r in res.reasons)
    same_without_gap = score_case(
        base(0.15), [tenant(willing_mediator="yes")], ll, t_high=T_HIGH, t_low=T_LOW
    )
    assert res.detail["confidence_points"] < same_without_gap.detail["confidence_points"]


def test_no_response_is_its_own_category_never_low():
    res = score_case(base(0.02), [], None, t_high=T_HIGH, t_low=T_LOW)
    assert res.base_tier == LOW
    assert res.potential == NO_RESPONSE and res.pathway == NEEDS_OUTREACH


def test_legal_issue_flag_never_hides_score():
    res = score_case(
        base(0.5), [tenant(repair_issues=True)], landlord(), t_high=T_HIGH, t_low=T_LOW
    )
    assert res.legal_aid_flag is True
    assert res.potential in ("High", "Medium", "Low") and 0 < res.final_prob < 1


def test_legal_aid_pathway_when_no_viable_deal():
    res = score_case(
        base(0.5),
        [tenant(pay_30d=0.0, pay_monthly=5.0, retaliation_concern=True)],
        landlord(),
        t_high=T_HIGH,
        t_low=T_LOW,
    )
    assert res.pathway == LEGAL_AID and res.legal_aid_flag


@pytest.mark.parametrize(
    ("t_kw", "l_kw", "expected"),
    [
        ({"wants": "move", "move_time_days": 30}, {}, AGREED_MOVE_OUT),
        ({"pay_30d": 1300.0}, {}, DISMISSAL_ON_PAYMENT),
        ({}, {}, PAY_AND_STAY),
        ({}, {"goal": "possession", "extend_moveout": True}, AGREED_MOVE_OUT),
    ],
)
def test_pathways(t_kw, l_kw, expected):
    res = score_case(base(), [tenant(**t_kw)], landlord(**l_kw), t_high=T_HIGH, t_low=T_LOW)
    assert res.pathway == expected


def test_only_landlord_answered_is_partial_not_no_response():
    res = score_case(base(0.5), [], landlord(goal="possession"), t_high=T_HIGH, t_low=T_LOW)
    assert res.potential != NO_RESPONSE and res.pathway == AGREED_MOVE_OUT


def test_reasons_always_two_to_four():
    rng = np.random.default_rng(0)
    for _ in range(200):
        tenants = (
            []
            if rng.random() < 0.3
            else [
                tenant(
                    pay_30d=float(rng.integers(0, 2000)),
                    wants=rng.choice(["stay", "move"]),
                    willing_mediator=rng.choice(["yes", "no", "unsure"]),
                    repair_issues=bool(rng.random() < 0.2),
                )
            ]
        )
        ll = (
            None
            if rng.random() < 0.4
            else landlord(
                goal=rng.choice(["rent", "possession", "both"]),
                accepts_plan=bool(rng.random() < 0.5),
            )
        )
        res = score_case(
            base(float(rng.uniform(0.05, 0.95))), tenants, ll, t_high=T_HIGH, t_low=T_LOW
        )
        assert 2 <= len(res.reasons) <= 4, res.reasons
        assert all(isinstance(r, str) and len(r) > 10 for r in res.reasons)


def test_confidence_rises_with_both_responses():
    none = score_case(base(0.5, landlord_n=0), [], None, t_high=T_HIGH, t_low=T_LOW)
    both = score_case(base(0.5, landlord_n=50), [tenant()], landlord(), t_high=T_HIGH, t_low=T_LOW)
    assert none.confidence == "Low" and both.confidence == "High"


# --- thresholds ----------------------------------------------------------------------------------


def test_recall_threshold_keeps_target_recall():
    rng = np.random.default_rng(1)
    y = rng.integers(0, 2, 1000)
    p = np.clip(y * 0.3 + rng.uniform(0, 0.7, 1000), 0, 1)
    t = recall_threshold(y, p, 0.95)
    m = metrics_at(y, p, t)
    assert m["recall"] >= 0.95
    assert metrics_at(y, p, t + 1e-6)["recall"] < 0.95  # highest such threshold


def test_production_training_holds_recall_on_validation(session):
    from core.scoring_service import train_production_model
    from core.settings import get_settings
    from synth.generator import build_world
    from synth.seed import write_world_files

    cfg = copy.deepcopy(get_settings().synthetic)
    cfg.update(history_cases=1500, live_cases=60, history_years=2)
    cfg["plaintiffs"]["count"] = 80
    files = write_world_files(build_world(cfg, date(2026, 3, 16)))
    from core.importer import import_csv

    import_csv(session, files["history"])
    import_csv(session, files["live"])
    session.commit()
    model = train_production_model()
    val = model.metrics["validation_at_high"]
    assert val["recall"] >= 0.95
    assert model.threshold_low <= model.threshold_high
    assert get_settings().model_path.exists()
