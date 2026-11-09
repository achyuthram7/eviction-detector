"""Layer 1: logistic regression on standardized as-of features.

The fitted coefficients, means and standard deviations are stored in JSON so the dashboard can
explain every score (contribution of feature j = coef_j * z_j, relative to an average case).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from core.features import FEATURE_COLUMNS

IMPUTED = ("log_amount", "days_to_hearing")


@dataclass
class BaseModel:
    version: str
    trained_at: str
    feature_names: list[str]
    means: list[float]
    stds: list[float]
    coef: list[float]
    intercept: float
    court_levels: list[str]
    court_reference: str
    impute: dict[str, float]
    base_rate: float
    n_train: int
    threshold_high: float = 0.5
    threshold_low: float = 0.25
    recall_target: float = 0.95
    medium_recall_target: float = 0.99
    metrics: dict = field(default_factory=dict)

    # --- design matrix -------------------------------------------------------------------------

    def design(self, feats: pd.DataFrame) -> np.ndarray:
        cols = []
        for name in FEATURE_COLUMNS:
            col = feats[name].to_numpy(dtype=float).copy()
            if name in self.impute:
                col[np.isnan(col)] = self.impute[name]
            cols.append(col)
        courts = feats["court_id"].astype(str).to_numpy()
        for level in self.court_levels:
            if level != self.court_reference:
                cols.append((courts == level).astype(float))
        return np.column_stack(cols)

    def z(self, feats: pd.DataFrame) -> np.ndarray:
        return (self.design(feats) - np.array(self.means)) / np.array(self.stds)

    def logit(self, feats: pd.DataFrame) -> np.ndarray:
        return self.z(feats) @ np.array(self.coef) + self.intercept

    def predict(self, feats: pd.DataFrame) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-self.logit(feats)))

    def contributions(self, feats: pd.DataFrame) -> np.ndarray:
        """Per-feature effect on the logit relative to an average (z = 0) case."""
        return self.z(feats) * np.array(self.coef)

    def tier(self, p: float) -> str:
        if p >= self.threshold_high:
            return "High"
        if p >= self.threshold_low:
            return "Medium"
        return "Low"

    # --- persistence ---------------------------------------------------------------------------

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: Path) -> BaseModel:
        return cls(**json.loads(path.read_text()))


def fit_model(
    feats: pd.DataFrame,
    y: np.ndarray,
    *,
    C: float = 1.0,
    version: str | None = None,
) -> BaseModel:
    y = np.asarray(y, dtype=float)
    counts = feats["court_id"].astype(str).value_counts()
    levels = sorted(counts.index.tolist())
    reference = str(counts.idxmax())
    impute = {}
    for name in IMPUTED:
        col = feats[name].to_numpy(dtype=float)
        known = col[~np.isnan(col)]
        impute[name] = float(np.median(known)) if known.size else 0.0
    shell = BaseModel(
        version=version or datetime.now().strftime("base-%Y%m%d-%H%M%S"),
        trained_at=datetime.now().isoformat(timespec="seconds"),
        feature_names=list(FEATURE_COLUMNS) + [f"court_{c}" for c in levels if c != reference],
        means=[],
        stds=[],
        coef=[],
        intercept=0.0,
        court_levels=levels,
        court_reference=reference,
        impute=impute,
        base_rate=float(y.mean()) if y.size else 0.5,
        n_train=int(y.size),
    )
    x = shell.design(feats)
    means = x.mean(axis=0)
    stds = x.std(axis=0)
    stds[stds == 0] = 1.0
    shell.means, shell.stds = means.tolist(), stds.tolist()
    clf = LogisticRegression(C=C, max_iter=2000)
    clf.fit((x - means) / stds, y)
    shell.coef = clf.coef_[0].tolist()
    shell.intercept = float(clf.intercept_[0])
    return shell


def recall_threshold(y: np.ndarray, p: np.ndarray, target: float) -> float:
    """Highest threshold t such that recall(p >= t) >= target."""
    y = np.asarray(y, dtype=float)
    pos = np.sort(np.asarray(p, dtype=float)[y == 1])[::-1]
    if pos.size == 0:
        return 0.5
    k = max(1, math.ceil(target * pos.size - 1e-9))
    return float(pos[k - 1])


def metrics_at(y: np.ndarray, p: np.ndarray, threshold: float) -> dict:
    y = np.asarray(y, dtype=float)
    flagged = np.asarray(p, dtype=float) >= threshold
    tp = int(np.sum(flagged & (y == 1)))
    fp = int(np.sum(flagged & (y == 0)))
    fn = int(np.sum(~flagged & (y == 1)))
    tn = int(np.sum(~flagged & (y == 0)))
    return {
        "threshold": float(threshold),
        "recall": tp / (tp + fn) if tp + fn else float("nan"),
        "fpr": fp / (fp + tn) if fp + tn else float("nan"),
        "precision": tp / (tp + fp) if tp + fp else float("nan"),
        "flagged_share": float(flagged.mean()) if flagged.size else float("nan"),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
    }


def set_thresholds(
    model: BaseModel,
    y_val: np.ndarray,
    p_val: np.ndarray,
    *,
    recall_target: float,
    medium_recall_target: float,
) -> None:
    model.recall_target = recall_target
    model.medium_recall_target = medium_recall_target
    model.threshold_high = recall_threshold(y_val, p_val, recall_target)
    model.threshold_low = min(
        model.threshold_high, recall_threshold(y_val, p_val, medium_recall_target)
    )
