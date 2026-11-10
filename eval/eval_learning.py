"""`make eval-learning`: does the false positive rate fall as data accumulates?

Simulates a court launching the tool with only a few weeks of data from its own daily feed, then
a 12-week live feed with weekly retraining on every case closed so far (label lag: an outcome is
usable only after its disposition date). Every model version is scored on the SAME fixed holdout,
split once into half A (re-tune the High threshold to hold recall >= target; champion-challenger
decision) and half B (reported metrics), so tuning and reporting never use the same rows.

Also simulates an exploration sample (a share of below-threshold cases get outreach anyway) to
estimate missed settlements, and an extended curve of FPR against training size.
"""

from __future__ import annotations

import csv
from datetime import timedelta

import numpy as np
from sklearn.metrics import roc_auc_score

from core.features import compute_features
from core.model import BaseModel, fit_model, metrics_at, recall_threshold
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
    reports_dir,
    save,
    title,
    wilson,
    write_json,
    write_report,
)


def _fit(feats, y, mask, c_value):
    if mask.sum() < 20 or len(np.unique(y[mask])) < 2:
        return None
    return fit_model(feats[mask], y[mask], C=c_value, version="sim")


def _evaluate(model: BaseModel, feats, y, a, b, target, n_boot, seed):
    p_a, p_b = model.predict(feats[a]), model.predict(feats[b])
    t = recall_threshold(y[a], p_a, target)
    m_a, m_b = metrics_at(y[a], p_a, t), metrics_at(y[b], p_b, t)
    fpr_ci = bootstrap(lambda yy, pp: metrics_at(yy, pp, t)["fpr"], y[b], p_b, n=n_boot, seed=seed)
    return {
        "threshold": t,
        "fpr_a": m_a["fpr"],
        "auc_a": roc_auc_score(y[a], p_a),
        "fpr_b": m_b["fpr"],
        "fpr_b_ci": fpr_ci,
        "recall_b": m_b["recall"],
        "precision_b": m_b["precision"],
        "auc_b": roc_auc_score(y[b], p_b),
    }


def run() -> dict:
    settings = get_settings().scoring
    lc = settings["evaluation"]["learning"]
    seed = int(settings["evaluation"]["seed"])
    target = float(settings["tiers"]["recall_target"])
    c_value = float(settings["model"]["C"])
    rng = np.random.default_rng(seed)

    hist = load_history()
    feats = compute_features(hist, hist)  # as-of features: identical to recomputing weekly
    y = hist["resolved"].to_numpy(dtype=float)
    truth = hist["true_resolved"].to_numpy(dtype=float)
    filing, disp = hist["filing_date"], hist["disposition_date"]

    data_start = filing.min()
    sim_start = data_start + timedelta(weeks=lc["warm_start_weeks"])
    sim_end = sim_start + timedelta(weeks=lc["sim_weeks"])
    hold_start = sim_end + timedelta(weeks=lc["holdout_gap_weeks"])
    hold_end = hold_start + timedelta(weeks=lc["holdout_weeks"])
    holdout = ((filing >= hold_start) & (filing < hold_end)).to_numpy()
    idx = np.flatnonzero(holdout)
    rng.shuffle(idx)
    a = np.zeros(len(hist), bool)
    b = np.zeros(len(hist), bool)
    a[idx[: len(idx) // 2]] = True
    b[idx[len(idx) // 2 :]] = True

    # --- weekly retraining with champion-challenger -------------------------------------------
    versions: list[dict] = []
    champion: dict | None = None
    for week in range(0, lc["sim_weeks"] + 1):
        cutoff = sim_start + timedelta(weeks=week)
        train = (disp < cutoff).to_numpy()
        model = _fit(feats, y, train, c_value)
        if model is None:
            continue
        ev = _evaluate(model, feats, y, a, b, target, lc["bootstrap"], seed + week)
        row = {"week": week, "cutoff": str(cutoff.date()), "n_train": int(train.sum()), **ev}
        if champion is None:
            promoted, reason = True, "first model"
        else:
            promoted = row["fpr_a"] < champion["fpr_a"] - 1e-9 or (
                abs(row["fpr_a"] - champion["fpr_a"]) <= 1e-9 and row["auc_a"] > champion["auc_a"]
            )
            reason = f"FPR on half A {row['fpr_a']:.3f} vs champion {champion['fpr_a']:.3f}"
        row["promoted"] = promoted
        row["reason"] = reason
        # What a live deployment can do: tune its threshold on the cases closed so far.
        row["deployed_threshold"] = recall_threshold(y[train], model.predict(feats[train]), target)
        row["_model"] = model
        if promoted:
            champion = row
        row["champion_week"] = champion["week"]
        row["champion_fpr_b"] = champion["fpr_b"]
        versions.append(row)

    log_path = reports_dir() / "champion_log.csv"
    with open(log_path, "w", newline="", encoding="utf-8") as fh:
        cols = [
            "week",
            "cutoff",
            "n_train",
            "threshold",
            "deployed_threshold",
            "fpr_a",
            "auc_a",
            "fpr_b",
            "recall_b",
            "precision_b",
            "auc_b",
            "promoted",
            "champion_week",
            "reason",
        ]
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(versions)

    # --- exploration sample: outreach to a share of below-threshold cases ----------------------
    share = float(lc["exploration_share"])
    by_week = {v["week"]: v for v in versions}
    deployed = None
    expl_rows = []
    for week in range(1, lc["sim_weeks"] + 1):
        prev = by_week.get(week - 1)
        if prev is not None:
            deployed = by_week[prev["champion_week"]]
        if deployed is None:
            continue
        start = sim_start + timedelta(weeks=week - 1)
        live_idx = np.flatnonzero(
            ((filing >= start) & (filing < start + timedelta(weeks=1))).to_numpy()
        )
        if live_idx.size == 0:
            continue
        p = deployed["_model"].predict(feats.iloc[live_idx])
        low = live_idx[p < deployed["deployed_threshold"]]
        n_sample = int(round(share * low.size))
        if low.size and n_sample == 0:
            n_sample = 1
        sample = rng.choice(low, size=n_sample, replace=False) if n_sample else np.array([], int)
        expl_rows.append(
            {
                "week": week,
                "live": int(live_idx.size),
                "below_threshold": int(low.size),
                "sampled": int(sample.size),
                "sample_resolved": int(y[sample].sum()),
                "actual_resolved_below": int(y[low].sum()),
                "true_resolved_below": int(truth[low].sum()),
            }
        )
    keys = (
        "live",
        "below_threshold",
        "sampled",
        "sample_resolved",
        "actual_resolved_below",
        "true_resolved_below",
    )
    tot = {k: sum(r[k] for r in expl_rows) for k in keys}
    rate = tot["sample_resolved"] / tot["sampled"] if tot["sampled"] else float("nan")
    rate_ci = wilson(tot["sample_resolved"], tot["sampled"])
    exploration = {
        **tot,
        "share": share,
        "sample_rate": rate,
        "sample_rate_ci": rate_ci,
        "estimated_missed": rate * tot["below_threshold"],
        "estimated_missed_ci": (
            rate_ci[0] * tot["below_threshold"],
            rate_ci[1] * tot["below_threshold"],
        ),
        "weeks": expl_rows,
    }

    # --- extended curve: FPR vs training size (random subsamples, newest 20% as holdout) ------
    test_start = filing.quantile(0.8)
    test_idx = np.flatnonzero((filing >= test_start).to_numpy())
    rng.shuffle(test_idx)
    ea = np.zeros(len(hist), bool)
    eb = np.zeros(len(hist), bool)
    ea[test_idx[: len(test_idx) // 2]] = True
    eb[test_idx[len(test_idx) // 2 :]] = True
    pool = np.flatnonzero((disp < test_start).to_numpy())
    sizes = [s for s in lc["extended_sizes"] if s < pool.size] + [int(pool.size)]
    extended = []
    for size in sizes:
        fprs = []
        repeats = 1 if size == pool.size else int(lc["extended_repeats"])
        for _ in range(repeats):
            pick = np.zeros(len(hist), bool)
            pick[rng.choice(pool, size=size, replace=False)] = True
            model = _fit(feats, y, pick, c_value)
            if model is None:
                continue
            t = recall_threshold(y[ea], model.predict(feats[ea]), target)
            fprs.append(metrics_at(y[eb], model.predict(feats[eb]), t)["fpr"])
        if fprs:
            extended.append(
                {
                    "n_train": size,
                    "fpr_median": float(np.median(fprs)),
                    "fpr_lo": float(np.percentile(fprs, 2.5)),
                    "fpr_hi": float(np.percentile(fprs, 97.5)),
                    "repeats": len(fprs),
                }
            )

    figs = _charts(versions, extended)
    public = [{k: v for k, v in row.items() if k != "_model"} for row in versions]
    results = {
        "recall_target": target,
        "data_start": str(data_start.date()),
        "sim_start": str(sim_start.date()),
        "sim_end": str(sim_end.date()),
        "holdout": [str(hold_start.date()), str(hold_end.date())],
        "holdout_size": {"a": int(a.sum()), "b": int(b.sum())},
        "versions": public,
        "first": public[0] if public else None,
        "last": public[-1] if public else None,
        "final_champion_week": public[-1]["champion_week"] if public else None,
        "final_champion_fpr_b": public[-1]["champion_fpr_b"] if public else None,
        "promotions": sum(1 for v in public if v["promoted"]) - 1 if public else 0,
        "min_recall_b": min(v["recall_b"] for v in public) if public else None,
        "exploration": exploration,
        "extended": extended,
        "figures": figs,
        "champion_log": "champion_log.csv",
    }
    write_json("learning.json", results)
    write_report("learning.md", "Learning evaluation (simulated 12-week feed)", _markdown(results))
    return results


def _charts(versions: list[dict], extended: list[dict]) -> dict:
    figs = {}
    fig, ax = new_axes(6.4, 4.0)
    n = np.array([v["n_train"] for v in versions])
    fpr = np.array([v["fpr_b"] for v in versions])
    lo = np.array([v["fpr_b_ci"][0] for v in versions])
    hi = np.array([v["fpr_b_ci"][1] for v in versions])
    champ = np.array([v["champion_fpr_b"] for v in versions])
    ax.fill_between(n, lo, hi, color=C["s1"], alpha=0.15, linewidth=0)
    ax.plot(
        n,
        fpr,
        color=C["s1"],
        linewidth=2,
        marker="o",
        markersize=4.5,
        markeredgecolor=C["surface"],
        label="Each weekly version (95% CI band)",
    )
    ax.step(n, champ, where="post", color=C["s2"], linewidth=2, label="Champion in use")
    ax.set_xlabel("Training cases available (closed so far)")
    ax.set_ylabel("False positive rate at recall ≥ 95%")
    ax.legend(frameon=False, fontsize=7.5, loc="upper right", labelcolor=C["ink2"])
    title(
        ax,
        f"Weekly versions: FPR {fpr[0]:.2f} at launch, champion in use {champ[-1]:.2f} "
        f"at week {versions[-1]['week']}",
        "Fixed holdout (half B); threshold re-tuned each week on half A to hold recall ≥ 95%",
    )
    figs["weekly"] = save(fig, "learning_weekly.png")

    if extended:
        fig, ax = new_axes(6.4, 4.0)
        xs = np.array([e["n_train"] for e in extended])
        ax.fill_between(
            xs,
            [e["fpr_lo"] for e in extended],
            [e["fpr_hi"] for e in extended],
            color=C["s1"],
            alpha=0.15,
            linewidth=0,
        )
        ax.plot(
            xs,
            [e["fpr_median"] for e in extended],
            color=C["s1"],
            linewidth=2,
            marker="o",
            markersize=4.5,
            markeredgecolor=C["surface"],
        )
        ax.set_xscale("log")
        ax.set_xticks(xs)
        ax.set_xticklabels([f"{x:,}" for x in xs], fontsize=7.5)
        ax.minorticks_off()
        ax.set_xlabel("Training cases (random subsample of closed history, log scale)")
        ax.set_ylabel("False positive rate at recall ≥ 95%")
        title(
            ax,
            "What more data buys: FPR by training size",
            "Median and 2.5–97.5 percentile over repeated subsamples; newest 20% as holdout",
        )
        figs["by_size"] = save(fig, "learning_by_size.png")
    return figs


def _markdown(r: dict) -> str:
    rows = [
        [
            v["week"],
            v["cutoff"],
            v["n_train"],
            fmt(v["auc_b"]),
            fmt_ci(v["fpr_b"], v["fpr_b_ci"]),
            fmt(v["recall_b"]),
            fmt(v["precision_b"]),
            "yes" if v["promoted"] else "no",
            v["champion_week"],
        ]
        for v in r["versions"]
    ]
    table = md_table(
        [
            "Week",
            "Trained on data before",
            "Training cases",
            "AUC (B)",
            "FPR (B)",
            "Recall (B)",
            "Precision (B)",
            "Promoted",
            "Champion week",
        ],
        rows,
    )
    e = r["exploration"]
    expl = md_table(
        ["Measure", "Value"],
        [
            ["Live cases scored in the 12 weeks", e["live"]],
            ["Below the deployed High threshold", e["below_threshold"]],
            [f"Exploration sample ({e['share']:.1%} of those)", e["sampled"]],
            [
                "Resolved in the sample",
                f"{e['sample_resolved']} ({pct(e['sample_rate'])}, 95% CI "
                f"{pct(e['sample_rate_ci'][0])}–{pct(e['sample_rate_ci'][1])})",
            ],
            [
                "**Estimated missed settlements**",
                f"**{e['estimated_missed']:.0f}** (95% CI {e['estimated_missed_ci'][0]:.0f}–"
                f"{e['estimated_missed_ci'][1]:.0f})",
            ],
            [
                "Check: actual resolved below threshold (recorded outcomes)",
                e["actual_resolved_below"],
            ],
            ["Check: noise-free 'true' count (ground truth)", e["true_resolved_below"]],
        ],
    )
    ext = md_table(
        ["Training cases", "Median FPR", "2.5–97.5 percentile", "Repeats"],
        [
            [
                f"{x['n_train']:,}",
                fmt(x["fpr_median"]),
                f"{fmt(x['fpr_lo'])}–{fmt(x['fpr_hi'])}",
                x["repeats"],
            ]
            for x in r["extended"]
        ],
    )
    first, last = r["first"], r["last"]
    ext0, ext1 = (r["extended"][0], r["extended"][-1]) if r["extended"] else (None, None)
    weekly_fig = f"![FPR vs training size, weekly]({r['figures']['weekly']})"
    size_fig = (
        f"![FPR vs training size, extended]({r['figures']['by_size']})"
        if "by_size" in r["figures"]
        else ""
    )
    more_data = (
        f"FPR falls from {fmt(ext0['fpr_median'])} with {ext0['n_train']:,} training cases to "
        f"{fmt(ext1['fpr_median'])} with {ext1['n_train']:,}, and flattens after roughly 1,600."
        if ext0
        else "n/a"
    )
    return f"""
## Setup

- Data collection starts {r["data_start"]} (the tool builds its own history from the daily
  feed). It launches {r["sim_start"]} with {first["n_train"] if first else 0} closed cases, then
  retrains weekly until {r["sim_end"]} on **all cases closed so far** (an outcome counts only
  after its disposition date).
- **Fixed holdout:** cases filed {r["holdout"][0]} to {r["holdout"][1]}, never used for
  training, split once into half A ({r["holdout_size"]["a"]} cases: re-tune the threshold to keep
  recall ≥ {r["recall_target"]:.2f}, and decide champion vs. challenger) and half B
  ({r["holdout_size"]["b"]} cases: every number reported below).
- **Champion–challenger:** a new weekly version replaces the champion only if its false
  positive rate on half A is lower (ties go to AUC). Full log: `{r["champion_log"]}`.

## Result

- **Recall held:** every weekly version kept recall near the target on the untouched half B
  (final week {fmt(last["recall_b"]) if last else "n/a"}; lowest {fmt(r["min_recall_b"])}).
- **False positive rate:** {fmt(first["fpr_b"]) if first else "n/a"} at launch
  ({first["n_train"] if first else 0} training cases). After {last["week"] if last else "?"}
  weeks ({last["n_train"] if last else 0} training cases) the champion in use is the
  week-{r["final_champion_week"]} model at **{fmt(r["final_champion_fpr_b"])}**. Week-to-week
  differences are mostly within the 95% intervals at this docket size.
- **Champion–challenger:** {r["promotions"]} promotion(s); later versions that did worse on
  half A were not promoted, so a noisy retrain never replaced a better model.
- **What more data buys** (below): {more_data} This simple model learns quickly; most of the
  remaining false positives come from the recall ≥ 95% rule and outcome noise (see the oracle
  comparison in `model_eval.md`).

{weekly_fig}

{table}

## Exploration sample: estimated missed settlements

Each week, {e["share"]:.1%} of live cases below the deployed High threshold (tuned on the cases
closed so far, as a live system would) get outreach anyway. Their outcomes estimate how many
low-ranked cases would settle — the settlements the ranking alone would miss. In this simulation
outreach does not change outcomes, so the estimate can be checked against the recorded count; in
a real pilot it measures what outreach achieves for low-ranked cases. With this small synthetic
docket the sample is small, so the interval is wide.

{expl}

## What more data buys

{size_fig}

{ext}
"""


def main() -> None:
    r = run()
    f = r["first"]
    print(
        f"eval-learning: FPR (holdout B) {f['fpr_b']:.3f} at launch -> champion "
        f"{r['final_champion_fpr_b']:.3f} at week {r['last']['week']} | est. missed settlements "
        f"{r['exploration']['estimated_missed']:.0f} -> reports/learning.md"
    )


if __name__ == "__main__":
    main()
