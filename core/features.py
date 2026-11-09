"""Base-score features, computed with the as-of rule: only facts known on the filing date.

Shared by the app (scoring a live case) and the evaluation scripts (training/testing), so the
two can never drift apart.

Input frame columns (one row per case):
    case_number, plaintiff_id, court_id, filing_date, first_hearing_date, disposition_date,
    resolved (1.0 / 0.0 / NaN while open), plaintiff_attorney, defendant_count,
    amount_claimed, defendant_names, mailing_address
"""

from __future__ import annotations

import bisect
import math
from collections import defaultdict

import numpy as np
import pandas as pd

from core.normalize import norm_address, norm_name, split_list
from core.settings import get_settings

FEATURE_COLUMNS = (
    "landlord_rate",
    "prior_filings_12m",
    "plaintiff_attorney",
    "defendant_count",
    "log_amount",
    "amount_unknown",
    "days_to_hearing",
)


def _days(series: pd.Series) -> np.ndarray:
    """Dates -> integer day numbers (NaN for missing)."""
    ts = pd.to_datetime(series, errors="coerce")
    out = np.full(len(ts), np.nan)
    mask = ts.notna().to_numpy()
    out[mask] = (ts[mask].to_numpy().astype("datetime64[D]").astype("int64")).astype(float)
    return out


def _case_keys(names: str, address: str) -> set[str]:
    keys = {"n:" + norm_name(n) for n in split_list(names) if n.strip()}
    addr = norm_address(address or "")
    if addr:
        keys.add("a:" + addr)
    return keys


def compute_features(targets: pd.DataFrame, history: pd.DataFrame | None = None) -> pd.DataFrame:
    """Raw features for each row of `targets`, using `history` (default: targets) as the record
    of what the court knew. Returned frame has the same index as `targets`.

    Extra columns (not model inputs): court_id, landlord_n (prior closed cases for this
    landlord), global_rate (court-wide resolution rate as of filing).
    """
    cfg = get_settings().scoring["model"]
    cap = float(get_settings().app["claim_cap"])
    if history is None:
        history = targets
    m = float(cfg["landlord_prior_strength"])
    prior_rate = float(cfg["global_rate_prior"])
    min_global = int(cfg["min_global_cases"])

    t_filing = _days(targets["filing_date"])

    # --- landlord resolution rate (only cases closed BEFORE this filing date) -----------------
    closed = history[history["resolved"].notna() & history["disposition_date"].notna()]
    c_disp = _days(closed["disposition_date"])
    c_res = closed["resolved"].to_numpy(dtype=float)
    order = np.argsort(c_disp, kind="stable")
    g_disp = c_disp[order]
    g_cum = np.concatenate([[0.0], np.cumsum(c_res[order])])  # g_cum[n] = resolved in first n
    by_pl: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for pid, idx in closed.groupby("plaintiff_id").indices.items():
        d = c_disp[idx]
        o = np.argsort(d, kind="stable")
        by_pl[pid] = (d[o], np.concatenate([[0.0], np.cumsum(c_res[idx][o])]))

    n_g = np.searchsorted(g_disp, t_filing, side="left")
    r_g = g_cum[n_g]
    global_rate = np.where(n_g >= min_global, r_g / np.maximum(n_g, 1), prior_rate)

    landlord_n = np.zeros(len(targets))
    landlord_r = np.zeros(len(targets))
    for i, pid in enumerate(targets["plaintiff_id"].to_numpy()):
        hit = by_pl.get(pid)
        if hit is None:
            continue
        n = int(np.searchsorted(hit[0], t_filing[i], side="left"))
        landlord_n[i] = n
        landlord_r[i] = hit[1][n]
    landlord_rate = (landlord_r + m * global_rate) / (landlord_n + m)

    # --- prior filings against the same defendant name or address (12 months) ----------------
    h_filing = _days(history["filing_date"])
    h_numbers = history["case_number"].to_numpy()
    index: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for j, (names, address) in enumerate(
        zip(
            history["defendant_names"].to_numpy(),
            history["mailing_address"].to_numpy(),
            strict=True,
        )
    ):
        if math.isnan(h_filing[j]):
            continue
        for key in _case_keys(names, address):
            index[key].append((h_filing[j], j))
    for entries in index.values():
        entries.sort()
    prior = np.zeros(len(targets))
    t_numbers = targets["case_number"].to_numpy()
    for i, (names, address) in enumerate(
        zip(
            targets["defendant_names"].to_numpy(),
            targets["mailing_address"].to_numpy(),
            strict=True,
        )
    ):
        t = t_filing[i]
        seen: set[int] = set()
        for key in _case_keys(names, address):
            entries = index.get(key)
            if not entries:
                continue
            lo = bisect.bisect_left(entries, (t - 365, -1))
            hi = bisect.bisect_left(entries, (t, -1))
            seen.update(j for _, j in entries[lo:hi] if h_numbers[j] != t_numbers[i])
        prior[i] = len(seen)
    prior = np.minimum(prior, float(cfg["prior_filings_cap"]))

    # --- simple case facts ---------------------------------------------------------------------
    amount = pd.to_numeric(targets["amount_claimed"], errors="coerce").to_numpy(dtype=float)
    unknown = np.isnan(amount) | (amount >= cap)
    log_amount = np.where(unknown, np.nan, np.log1p(np.where(unknown, 0.0, amount)))
    lo_clip, hi_clip = cfg["days_to_hearing_clip"]
    dth = np.clip(_days(targets["first_hearing_date"]) - t_filing, lo_clip, hi_clip)

    return pd.DataFrame(
        {
            "landlord_rate": landlord_rate,
            "prior_filings_12m": prior,
            "plaintiff_attorney": targets["plaintiff_attorney"].astype(float).to_numpy(),
            "defendant_count": np.minimum(
                targets["defendant_count"].astype(float).to_numpy(),
                float(cfg["defendant_count_cap"]),
            ),
            "log_amount": log_amount,
            "amount_unknown": unknown.astype(float),
            "days_to_hearing": dth,
            "court_id": targets["court_id"].astype(str).to_numpy(),
            "landlord_n": landlord_n,
            "global_rate": global_rate,
        },
        index=targets.index,
    )


def frame_from_export(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize an export-shaped frame (CSV columns) into the feature-input shape."""
    from core.status import is_resolved

    out = pd.DataFrame(
        {
            "case_number": df["case_number"].astype(str),
            "plaintiff_id": df["plaintiff_id"].astype(str),
            "court_id": df["court_id"].astype(str),
            "filing_date": pd.to_datetime(df["filing_date"]),
            "first_hearing_date": pd.to_datetime(
                df["first_hearing_datetime"]
                if "first_hearing_datetime" in df
                else df["hearing_datetime"],
                errors="coerce",
            ).dt.normalize(),
            "disposition_date": pd.to_datetime(df["disposition_date"], errors="coerce"),
            "plaintiff_attorney": df["plaintiff_attorney"]
            .astype(str)
            .str.lower()
            .isin(["true", "1", "y", "yes"]),
            "defendant_count": pd.to_numeric(df["defendant_count"]).astype(int),
            "amount_claimed": pd.to_numeric(df["amount_claimed"], errors="coerce"),
            "defendant_names": df["defendant_names"].fillna("").astype(str),
            "mailing_address": df["mailing_address"].fillna("").astype(str),
        }
    )
    events = df["latest_event"].astype(str).to_numpy()
    dtypes = df["disposition_type"].fillna("").astype(str).to_numpy()
    resolved = [is_resolved(e, d or None) for e, d in zip(events, dtypes, strict=True)]
    out["resolved"] = [np.nan if r is None else float(r) for r in resolved]
    out.loc[out["disposition_date"].isna(), "resolved"] = np.nan
    return out
