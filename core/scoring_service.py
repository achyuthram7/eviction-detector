"""Database side of scoring: build feature frames from cases, train the production model,
and (re)score open cases.

Base features follow the as-of rule, so they never change after filing; a case's stored base
result is reused until the model version changes, and only Layer 2 is recomputed when answers
change.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core import clock
from core.constants import TERMINAL_EVENTS
from core.db import session_scope
from core.features import FEATURE_COLUMNS, compute_features
from core.model import BaseModel, fit_model, metrics_at, set_thresholds
from core.models import Case, CaseScore, LandlordResponse, TenantResponse
from core.scoring import BaseResult, LandlordAnswers, ScoreResult, TenantAnswers, score_case
from core.settings import get_settings
from core.status import is_resolved

_model_cache: dict[str, tuple[float, BaseModel]] = {}


def load_model() -> BaseModel | None:
    path = get_settings().model_path
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    cached = _model_cache.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    model = BaseModel.load(path)
    _model_cache[str(path)] = (mtime, model)
    return model


def cases_frame(session: Session) -> pd.DataFrame:
    """Every case the court knows about, in the feature-input shape."""
    rows = session.execute(
        select(
            Case.id,
            Case.case_number,
            Case.plaintiff_id,
            Case.court_id,
            Case.filing_date,
            Case.first_hearing_datetime,
            Case.hearing_datetime,
            Case.disposition_date,
            Case.latest_event,
            Case.disposition_type,
            Case.plaintiff_attorney,
            Case.defendant_count,
            Case.amount_claimed,
            Case.defendant_names,
            Case.mailing_address,
        )
    ).all()
    df = pd.DataFrame(
        rows,
        columns=[
            "id",
            "case_number",
            "plaintiff_id",
            "court_id",
            "filing_date",
            "first_hearing",
            "hearing",
            "disposition_date",
            "latest_event",
            "disposition_type",
            "plaintiff_attorney",
            "defendant_count",
            "amount_claimed",
            "defendant_names",
            "mailing_address",
        ],
    )
    if df.empty:
        return df
    first = pd.to_datetime(df["first_hearing"]).fillna(pd.to_datetime(df["hearing"]))
    df["first_hearing_date"] = first.dt.normalize()
    df["filing_date"] = pd.to_datetime(df["filing_date"])
    df["disposition_date"] = pd.to_datetime(df["disposition_date"])
    resolved = [
        is_resolved(e, d) for e, d in zip(df["latest_event"], df["disposition_type"], strict=True)
    ]
    df["resolved"] = [np.nan if r is None else float(r) for r in resolved]
    df.loc[df["disposition_date"].isna(), "resolved"] = np.nan
    return df.set_index("id", drop=False)


def train_production_model(*, rescore: bool = True) -> BaseModel:
    """Train on closed history; set thresholds on the newest share (time-based validation)."""
    settings = get_settings()
    cfg = settings.scoring
    path = settings.model_path
    with session_scope() as session:
        frame = cases_frame(session)
    closed = frame[frame["resolved"].notna()].sort_values("filing_date")
    feats = compute_features(closed, frame)
    n_val = max(1, int(round(len(closed) * cfg["model"]["validation_share"])))
    val_idx = closed.index[-n_val:]
    val_start = closed.loc[val_idx, "filing_date"].min()
    train_mask = (closed["filing_date"] < val_start) & (closed["disposition_date"] < val_start)
    train_idx = closed.index[train_mask.to_numpy()]
    model = fit_model(
        feats.loc[train_idx],
        closed.loc[train_idx, "resolved"].to_numpy(),
        C=float(cfg["model"]["C"]),
        version=clock.now().strftime("base-%Y%m%d-%H%M%S"),
    )
    y_val = closed.loc[val_idx, "resolved"].to_numpy()
    p_val = model.predict(feats.loc[val_idx])
    set_thresholds(
        model,
        y_val,
        p_val,
        recall_target=cfg["tiers"]["recall_target"],
        medium_recall_target=cfg["tiers"]["medium_recall_target"],
    )
    model.metrics = {
        "n_train": int(len(train_idx)),
        "n_validation": int(n_val),
        "validation_from": str(val_start.date()),
        "validation_at_high": metrics_at(y_val, p_val, model.threshold_high),
    }
    model.save(path)
    _model_cache.pop(str(path), None)
    if rescore:
        with session_scope() as session:
            open_ids = list(
                session.scalars(select(Case.id).where(Case.latest_event.not_in(TERMINAL_EVENTS)))
            )
            rescore_cases(session, open_ids, force_base=True)
    return model


def _base_results(model: BaseModel, frame: pd.DataFrame, ids: list[int]) -> dict[int, BaseResult]:
    targets = frame.loc[ids]
    feats = compute_features(targets, frame)
    probs = model.predict(feats)
    contrib = model.contributions(feats)
    zs = model.z(feats)
    names = model.feature_names
    out = {}
    for row_i, case_id in enumerate(ids):
        c: dict[str, float] = {}
        z: dict[str, float] = {}
        for j, name in enumerate(names):
            key = "court" if name.startswith("court_") else name
            c[key] = c.get(key, 0.0) + float(contrib[row_i, j])
            if not name.startswith("court_"):
                z[name] = float(zs[row_i, j])
        if "court" not in c:
            c["court"] = 0.0
        values = {k: float(feats.iloc[row_i][k]) for k in FEATURE_COLUMNS}
        values["court_id"] = str(feats.iloc[row_i]["court_id"])
        out[case_id] = BaseResult(
            prob=float(probs[row_i]),
            contributions=c,
            values=values,
            z=z,
            landlord_n=int(feats.iloc[row_i]["landlord_n"]),
            global_rate=float(feats.iloc[row_i]["global_rate"]),
        )
    return out


def _clean(value):
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _tenant_answers(r: TenantResponse) -> TenantAnswers:
    return TenantAnswers(**{f: getattr(r, f) for f in TenantAnswers.__dataclass_fields__})


def _landlord_answers(r: LandlordResponse) -> LandlordAnswers:
    return LandlordAnswers(**{f: getattr(r, f) for f in LandlordAnswers.__dataclass_fields__})


def rescore_cases(session: Session, case_ids: list[int], *, force_base: bool = False) -> int:
    model = load_model()
    if model is None or not case_ids:
        return 0
    open_cases = {
        c.id: c
        for i in range(0, len(case_ids), 500)
        for c in session.scalars(
            select(Case).where(
                Case.id.in_(case_ids[i : i + 500]), Case.latest_event.not_in(TERMINAL_EVENTS)
            )
        )
    }
    if not open_cases:
        return 0
    ids = list(open_cases)
    current = {
        s.case_id: s
        for i in range(0, len(ids), 500)
        for s in session.scalars(
            select(CaseScore).where(
                CaseScore.case_id.in_(ids[i : i + 500]), CaseScore.is_current.is_(True)
            )
        )
    }
    reuse = {
        cid: s
        for cid, s in current.items()
        if not force_base and s.model_version == model.version and "base" in (s.features or {})
    }
    need = [cid for cid in ids if cid not in reuse]
    bases: dict[int, BaseResult] = {}
    if need:
        bases = _base_results(model, cases_frame(session), need)
    for cid, s in reuse.items():
        b = s.features["base"]
        bases[cid] = BaseResult(
            prob=b["prob"],
            contributions=b["contributions"],
            values=b["values"],
            z=b["z"],
            landlord_n=b["landlord_n"],
            global_rate=b["global_rate"],
        )

    tenants: dict[int, list[TenantAnswers]] = {cid: [] for cid in ids}
    landlords: dict[int, LandlordAnswers] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        for r in session.scalars(select(TenantResponse).where(TenantResponse.case_id.in_(chunk))):
            tenants[r.case_id].append(_tenant_answers(r))
        for r in session.scalars(
            select(LandlordResponse).where(LandlordResponse.case_id.in_(chunk))
        ):
            landlords[r.case_id] = _landlord_answers(r)

    now = clock.now()
    for i in range(0, len(ids), 500):
        session.execute(
            update(CaseScore)
            .where(CaseScore.case_id.in_(ids[i : i + 500]), CaseScore.is_current.is_(True))
            .values(is_current=False)
        )
    for cid in ids:
        case = open_cases[cid]
        base = bases[cid]
        claimed = None if case.amount_is_placeholder else case.amount_claimed
        result: ScoreResult = score_case(
            base,
            tenants[cid],
            landlords.get(cid),
            t_high=model.threshold_high,
            t_low=model.threshold_low,
            amount_claimed=claimed,
        )
        session.add(
            CaseScore(
                case_id=cid,
                model_version=model.version,
                computed_at=now,
                base_prob=result.base_prob,
                final_prob=result.final_prob,
                potential=result.potential,
                base_tier=result.base_tier,
                pathway=result.pathway,
                confidence=result.confidence,
                legal_aid_flag=result.legal_aid_flag,
                overlap_class=result.overlap_class,
                reasons=result.reasons,
                adjustments=result.adjustments,
                features={
                    "base": {
                        "prob": base.prob,
                        "contributions": base.contributions,
                        "values": {k: _clean(v) for k, v in base.values.items()},
                        "z": base.z,
                        "landlord_n": base.landlord_n,
                        "global_rate": base.global_rate,
                    },
                    "detail": result.detail,
                },
                is_current=True,
            )
        )
    session.flush()
    return len(ids)
