"""`make eval-model`: time-split evaluation of the base model.

Train on the oldest 60 % of closed cases (by filing date), choose the High threshold on the next
20 % so recall >= target, and report on the newest 20 %: AUC, calibration, a tier "staircase",
and recall / FPR / precision at the threshold with bootstrap 95 % CIs. Labels respect lag: a
case is used for training/validation only if it closed before that split's boundary.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.metrics import brier_score_loss, roc_auc_score, roc_curve

from core.features import compute_features
from core.model import fit_model, metrics_at, recall_threshold
from core.settings import get_settings
from eval.common import (
    C,
    bootstrap,
    fmt,
    fmt_ci,
    load_history,
    md_table,
    new_axes,
    pct,
    save,
    title,
    wilson,
    write_json,
    write_report,
)


def split(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    b1 = frame["filing_date"].quantile(0.6)
    b2 = frame["filing_date"].quantile(0.8)
    f, d = frame["filing_date"], frame["disposition_date"]
    train = ((f < b1) & (d < b1)).to_numpy()
    val = ((f >= b1) & (f < b2) & (d < b2)).to_numpy()
    test = (f >= b2).to_numpy()
    return train, val, test, {"train_before": b1.date(), "test_from": b2.date()}


def _auc(y, p):
    return roc_auc_score(y, p) if 0 < y.sum() < len(y) else float("nan")


def run() -> dict:
    cfg = get_settings().scoring
    target = float(cfg["tiers"]["recall_target"])
    medium_target = float(cfg["tiers"]["medium_recall_target"])
    hist = load_history()
    feats = compute_features(hist, hist)
    y = hist["resolved"].to_numpy(dtype=float)
    train, val, test, bounds = split(hist)

    model = fit_model(feats[train], y[train], C=float(cfg["model"]["C"]), version="eval-model")
    p_val = model.predict(feats[val])
    p_test = model.predict(feats[test])
    y_val, y_test = y[val], y[test]
    t_high = recall_threshold(y_val, p_val, target)
    t_low = min(t_high, recall_threshold(y_val, p_val, medium_target))

    at_val = metrics_at(y_val, p_val, t_high)
    at_test = metrics_at(y_test, p_test, t_high)
    ci = {
        key: bootstrap(lambda yy, pp, k=key: metrics_at(yy, pp, t_high)[k], y_test, p_test)
        for key in ("recall", "fpr", "precision")
    }
    auc = _auc(y_test, p_test)
    auc_ci = bootstrap(_auc, y_test, p_test)
    truth_test = hist.loc[test, "true_resolved"].to_numpy(dtype=float)
    latent_test = hist.loc[test, "latent_p"].to_numpy(dtype=float)
    auc_truth = _auc(truth_test, p_test)
    auc_oracle = _auc(y_test, latent_test)
    oracle_at95 = metrics_at(y_test, latent_test, recall_threshold(y_test, latent_test, target))
    brier = brier_score_loss(y_test, p_test)

    # --- staircase: actual resolution rate by tier and by decile ------------------------------
    tiers = np.where(p_test >= t_high, "High", np.where(p_test >= t_low, "Medium", "Low"))
    tier_rows = []
    for tier in ("High", "Medium", "Low"):
        mask = tiers == tier
        k, n = int(y_test[mask].sum()), int(mask.sum())
        lo, hi = wilson(k, n)
        tier_rows.append(
            {
                "tier": tier,
                "n": n,
                "share": n / len(y_test) if len(y_test) else float("nan"),
                "mean_predicted": float(p_test[mask].mean()) if n else float("nan"),
                "actual_rate": k / n if n else float("nan"),
                "ci": (lo, hi),
            }
        )
    order = np.argsort(p_test)
    deciles = np.array_split(order, 10)
    decile_rows = []
    for i, idx in enumerate(deciles, start=1):
        k, n = int(y_test[idx].sum()), len(idx)
        decile_rows.append(
            {
                "decile": i,
                "n": n,
                "mean_predicted": float(p_test[idx].mean()),
                "actual_rate": k / n,
                "ci": wilson(k, n),
            }
        )

    # --- charts ---------------------------------------------------------------------------------
    figs = {}
    fig, ax = new_axes(5.2, 4.2)
    fpr_curve, tpr_curve, _ = roc_curve(y_test, p_test)
    ax.plot([0, 1], [0, 1], color=C["muted"], linewidth=1, linestyle="--")
    ax.plot(fpr_curve, tpr_curve, color=C["s1"], linewidth=2)
    ax.scatter(
        [at_test["fpr"]],
        [at_test["recall"]],
        s=40,
        color=C["s1"],
        zorder=3,
        edgecolor=C["surface"],
        linewidth=1.5,
    )
    ax.annotate(
        f"High threshold: recall {at_test['recall']:.2f}, FPR {at_test['fpr']:.2f}",
        xy=(at_test["fpr"], at_test["recall"]),
        xytext=(0.38, 0.25),
        textcoords="data",
        fontsize=7.5,
        color=C["ink2"],
        arrowprops={"arrowstyle": "-", "color": C["muted"], "linewidth": 0.8},
    )
    ax.set_xlabel("False positive rate")
    ax.set_ylabel("Recall (true positive rate)")
    title(ax, f"ROC on the newest 20% of cases (AUC {auc:.2f})", "Base model, synthetic data")
    figs["roc"] = save(fig, "roc.png")

    fig, ax = new_axes(5.2, 4.2)
    frac_pos, mean_pred = calibration_curve(y_test, p_test, n_bins=10, strategy="quantile")
    ax.plot([0, 1], [0, 1], color=C["muted"], linewidth=1, linestyle="--")
    ax.plot(
        mean_pred,
        frac_pos,
        color=C["s1"],
        linewidth=2,
        marker="o",
        markersize=5,
        markeredgecolor=C["surface"],
    )
    ax.text(0.62, 0.55, "perfect calibration", rotation=38, fontsize=7, color=C["muted"])
    ax.set_xlabel("Predicted probability (decile mean)")
    ax.set_ylabel("Observed resolution rate")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    title(ax, f"Calibration (Brier {brier:.3f})", "Each point is one decile of predicted scores")
    figs["calibration"] = save(fig, "calibration.png")

    fig, ax = new_axes(6.4, 3.8)
    rates = [r["actual_rate"] for r in decile_rows]
    errs = np.array(
        [
            [r["actual_rate"] - r["ci"][0] for r in decile_rows],
            [r["ci"][1] - r["actual_rate"] for r in decile_rows],
        ]
    )
    ax.bar(
        range(1, 11),
        rates,
        width=0.72,
        color=C["s1"],
        yerr=errs,
        error_kw={"ecolor": C["ink2"], "elinewidth": 1, "capsize": 2},
    )
    ax.axhline(y_test.mean(), color=C["muted"], linewidth=1, linestyle="--")
    ax.text(0.55, y_test.mean() + 0.015, "overall rate", fontsize=7, color=C["muted"])
    ax.set_xticks(range(1, 11))
    ax.set_xlabel("Score decile (1 = lowest predicted, 10 = highest)")
    ax.set_ylabel("Actual resolution rate")
    ax.set_ylim(0, 1)
    title(
        ax,
        "Staircase: actual resolution rate rises with the score",
        "Newest 20% of closed cases; bars show 95% Wilson intervals",
    )
    figs["staircase"] = save(fig, "staircase.png")

    coef_rows = sorted(zip(model.feature_names, model.coef, strict=True), key=lambda r: -abs(r[1]))
    results = {
        "n_train": int(train.sum()),
        "n_validation": int(val.sum()),
        "n_test": int(test.sum()),
        "bounds": {k: str(v) for k, v in bounds.items()},
        "recall_target": target,
        "threshold_high": t_high,
        "threshold_low": t_low,
        "validation_at_threshold": at_val,
        "test_at_threshold": at_test,
        "test_ci": ci,
        "auc": auc,
        "auc_ci": auc_ci,
        "auc_vs_noise_free_truth": auc_truth,
        "oracle_auc": auc_oracle,
        "oracle_at_recall_target": oracle_at95,
        "brier": brier,
        "base_rate_test": float(y_test.mean()),
        "tiers": tier_rows,
        "deciles": decile_rows,
        "figures": figs,
        "coefficients": {name: float(c) for name, c in coef_rows},
    }
    write_json("model_eval.json", results)
    write_report("model_eval.md", "Base model evaluation (time split)", _markdown(results))
    return results


def _markdown(r: dict) -> str:
    t, ci = r["test_at_threshold"], r["test_ci"]
    tier_table = md_table(
        ["Tier", "Cases", "Share", "Mean predicted", "Actual resolution rate (95% CI)"],
        [
            [
                x["tier"],
                x["n"],
                pct(x["share"]),
                fmt(x["mean_predicted"]),
                f"{pct(x['actual_rate'])} ({pct(x['ci'][0])}–{pct(x['ci'][1])})",
            ]
            for x in r["tiers"]
        ],
    )
    decile_table = md_table(
        ["Decile", "Cases", "Mean predicted", "Actual rate"],
        [
            [x["decile"], x["n"], fmt(x["mean_predicted"]), pct(x["actual_rate"])]
            for x in r["deciles"]
        ],
    )
    coef_table = md_table(
        ["Feature (standardized)", "Coefficient"],
        [[k, fmt(v)] for k, v in r["coefficients"].items()],
    )
    return f"""
## Setup

- Closed historical cases split by **filing date**: train {r["n_train"]} (filed and closed before
  {r["bounds"]["train_before"]}), validation {r["n_validation"]}, test {r["n_test"]} (filed from
  {r["bounds"]["test_from"]}). Label lag respected: a case counts only once it has closed.
- Features follow the as-of rule (only what was known on the filing date).
- The **High threshold** is chosen on the validation set: the highest score that keeps recall
  ≥ {r["recall_target"]:.2f}. Threshold = **{r["threshold_high"]:.3f}**; Medium/Low split at
  {r["threshold_low"]:.3f}.

## Results on the test set (newest cases)

| Metric | Value |
|---|---|
| Recall at the High threshold | {fmt_ci(t["recall"], ci["recall"])} |
| False positive rate | {fmt_ci(t["fpr"], ci["fpr"])} |
| Precision | {fmt_ci(t["precision"], ci["precision"])} |
| Share of cases flagged High | {pct(t["flagged_share"])} |
| AUC | {fmt_ci(r["auc"], r["auc_ci"])} |
| Brier score | {fmt(r["brier"])} |
| Base resolution rate | {pct(r["base_rate_test"])} |
| Validation recall at threshold | {fmt(r["validation_at_threshold"]["recall"])} |

Bootstrap CIs use 1,000 resamples of the test set with the threshold held fixed, so they show
test-sample uncertainty only.

Sanity checks (synthetic data only): AUC against the noise-free "true" outcome is
{fmt(r["auc_vs_noise_free_truth"])}; a perfect learner that knew each case's latent probability
would reach AUC {fmt(r["oracle_auc"])} and FPR {fmt(r["oracle_at_recall_target"]["fpr"])} at
the same recall target. The gap is what more data or better features could still buy.

**Reading the trade-off.** Holding recall at 95 % means almost every case that later resolves is
flagged High, so the High tier is large and the false positive rate is high. That is the cost of
the recall rule; the learning curve (`learning.md`) tracks how it falls as data accumulates.

![ROC]({r["figures"]["roc"]})
![Calibration]({r["figures"]["calibration"]})
![Staircase]({r["figures"]["staircase"]})

## Staircase by tier

{tier_table}

## Staircase by decile

{decile_table}

## Coefficients (largest first)

{coef_table}
"""


def main() -> None:
    r = run()
    t = r["test_at_threshold"]
    print(
        f"eval-model: AUC {r['auc']:.3f} | test recall {t['recall']:.3f} | FPR {t['fpr']:.3f} "
        f"| precision {t['precision']:.3f} -> reports/model_eval.md"
    )


if __name__ == "__main__":
    main()
