"""As-of rule: base features only use what was known on the filing date."""

from __future__ import annotations

import math

import pandas as pd

from core.features import compute_features


def _frame(rows: list[dict]) -> pd.DataFrame:
    base = {
        "plaintiff_id": "P1",
        "court_id": "97K01",
        "first_hearing_date": None,
        "plaintiff_attorney": False,
        "defendant_count": 1,
        "amount_claimed": 1500.0,
        "mailing_address": "",
        "disposition_date": None,
        "resolved": float("nan"),
    }
    df = pd.DataFrame([{**base, **r} for r in rows])
    for col in ("filing_date", "first_hearing_date", "disposition_date"):
        df[col] = pd.to_datetime(df[col])
    return df


def test_landlord_rate_ignores_cases_closed_after_filing():
    rows = [
        # closed BEFORE the target's filing date: resolved
        {
            "case_number": "A",
            "filing_date": "2025-01-01",
            "disposition_date": "2025-02-01",
            "resolved": 1.0,
            "defendant_names": "A A",
        },
        # filed earlier but closed AFTER the target's filing date: must be ignored
        {
            "case_number": "B",
            "filing_date": "2025-01-05",
            "disposition_date": "2025-06-01",
            "resolved": 0.0,
            "defendant_names": "B B",
        },
        {"case_number": "T", "filing_date": "2025-03-01", "defendant_names": "T T"},
    ]
    df = _frame(rows)
    feats = compute_features(df, df)
    target = feats.iloc[2]
    assert target["landlord_n"] == 1
    # m = 10 prior-strength blend toward the global prior (0.5 while < 50 closed cases)
    assert math.isclose(target["landlord_rate"], (1 + 10 * 0.5) / (1 + 10))


def test_landlord_with_few_cases_is_blended_toward_average():
    rows = [
        {
            "case_number": f"C{i}",
            "filing_date": "2024-01-01",
            "disposition_date": "2024-02-01",
            "resolved": 1.0,
            "defendant_names": f"X{i} Y",
            "plaintiff_id": "BIG",
        }
        for i in range(60)
    ] + [
        {
            "case_number": "S1",
            "filing_date": "2024-01-01",
            "disposition_date": "2024-02-01",
            "resolved": 0.0,
            "defendant_names": "S One",
            "plaintiff_id": "SMALL",
        },
        {
            "case_number": "T",
            "filing_date": "2024-06-01",
            "defendant_names": "T T",
            "plaintiff_id": "SMALL",
        },
    ]
    df = _frame(rows)
    target = compute_features(df, df).iloc[-1]
    global_rate = 60 / 61
    assert math.isclose(target["global_rate"], global_rate)
    expected = (0 + 10 * global_rate) / (1 + 10)
    assert math.isclose(target["landlord_rate"], expected)
    assert target["landlord_rate"] > 0.8  # one bad case does not sink a new landlord


def test_prior_filings_same_name_or_address_previous_12_months_only():
    rows = [
        {"case_number": "OLD", "filing_date": "2023-01-01", "defendant_names": "Ann Smith"},
        {"case_number": "NAME", "filing_date": "2024-11-01", "defendant_names": "Smith, Ann"},
        {
            "case_number": "ADDR",
            "filing_date": "2025-01-10",
            "defendant_names": "Bo Other",
            "mailing_address": "5 Elm Street Apt 2, Faketown, IN",
        },
        {"case_number": "LATER", "filing_date": "2025-09-01", "defendant_names": "Ann Smith"},
        {
            "case_number": "T",
            "filing_date": "2025-03-01",
            "defendant_names": "Ann Smith",
            "mailing_address": "5 elm st apt 2 faketown in",
        },
    ]
    df = _frame(rows)
    target = compute_features(df, df).iloc[-1]
    assert target["prior_filings_12m"] == 2  # NAME + ADDR; not OLD (>12 mo) or LATER (future)


def test_ten_thousand_cap_is_unknown_amount():
    df = _frame(
        [
            {
                "case_number": "A",
                "filing_date": "2025-01-01",
                "defendant_names": "A A",
                "amount_claimed": 10000.0,
            },
            {
                "case_number": "B",
                "filing_date": "2025-01-01",
                "defendant_names": "B B",
                "amount_claimed": 2000.0,
            },
        ]
    )
    feats = compute_features(df, df)
    assert feats.iloc[0]["amount_unknown"] == 1.0 and math.isnan(feats.iloc[0]["log_amount"])
    assert feats.iloc[1]["amount_unknown"] == 0.0
    assert math.isclose(feats.iloc[1]["log_amount"], math.log1p(2000))


def test_days_to_hearing_uses_first_hearing():
    df = _frame(
        [
            {
                "case_number": "A",
                "filing_date": "2025-01-01",
                "defendant_names": "A A",
                "first_hearing_date": "2025-01-29",
            }
        ]
    )
    assert compute_features(df, df).iloc[0]["days_to_hearing"] == 28
