"""`make expert-compare RATINGS=ratings.csv`: tool vs. expert agreement.

Reports the Spearman rank correlation between the tool's probability and the mean expert
rating (with a bootstrap CI), the overlap between the tool's High tier and the experts' top tier
(mean rating >= 4), and the 10 largest disagreements. Without RATINGS, a filled-in
reports/expert_pack/ratings.csv is used if present, else the clearly labelled simulated ratings.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from eval.common import bootstrap, fmt, fmt_ci, md_table, reports_dir, write_json, write_report


def _pick_ratings(path: str | None) -> Path:
    folder = reports_dir() / "expert_pack"
    if path:
        return Path(path)
    filled = folder / "ratings.csv"
    if filled.exists():
        df = pd.read_csv(filled)
        if df["likelihood_1_5"].notna().any():
            return filled
    return folder / "ratings_simulated.csv"


def _rho(x: np.ndarray, y: np.ndarray) -> float:
    if len(np.unique(x)) < 2 or len(np.unique(y)) < 2:
        return float("nan")
    return float(spearmanr(x, y).statistic)


def run(ratings_path: str | None = None) -> dict:
    folder = reports_dir() / "expert_pack"
    key = pd.read_csv(folder / "key.csv")
    path = _pick_ratings(ratings_path)
    ratings = pd.read_csv(path)
    ratings = ratings[pd.to_numeric(ratings["likelihood_1_5"], errors="coerce").between(1, 5)]
    ratings["likelihood_1_5"] = ratings["likelihood_1_5"].astype(float)
    simulated = ratings["rater_id"].astype(str).str.startswith("SIMULATED").any()
    if ratings.empty:
        raise SystemExit(f"No ratings (1-5) found in {path}")
    per = ratings.groupby("scenario_id").agg(
        expert_mean=("likelihood_1_5", "mean"), raters=("rater_id", "nunique")
    )
    df = key.merge(per, left_on="scenario_id", right_index=True, how="inner")
    tool, expert = df["tool_probability"].to_numpy(), df["expert_mean"].to_numpy()
    rho = _rho(tool, expert)
    rho_ci = bootstrap(_rho, tool, expert, n=1000)

    # "No response" cases have no answer-based tier; their base tier stands in for it.
    effective = df["potential"].where(
        ~df["potential"].str.startswith("No response"), df["base_tier"]
    )
    tool_top = set(df.loc[effective == "High", "scenario_id"])
    expert_top = set(df.loc[df["expert_mean"] >= 4, "scenario_id"])
    both = tool_top & expert_top
    union = tool_top | expert_top
    overlap = {
        "tool_high": len(tool_top),
        "expert_top": len(expert_top),
        "both": len(both),
        "jaccard": len(both) / len(union) if union else float("nan"),
        "share_of_expert_top_flagged_high": len(both) / len(expert_top)
        if expert_top
        else float("nan"),
        "share_of_tool_high_rated_top": len(both) / len(tool_top) if tool_top else float("nan"),
    }

    df["tool_rank_pct"] = df["tool_probability"].rank(pct=True)
    df["expert_rank_pct"] = df["expert_mean"].rank(pct=True)
    df["gap"] = df["tool_rank_pct"] - df["expert_rank_pct"]
    worst = df.reindex(df["gap"].abs().sort_values(ascending=False).index).head(10)
    disagreements = [
        {
            "scenario_id": r.scenario_id,
            "tool_probability": float(r.tool_probability),
            "potential": r.potential,
            "pathway": r.pathway,
            "expert_mean": float(r.expert_mean),
            "direction": "tool higher" if r.gap > 0 else "experts higher",
            "rank_gap": float(abs(r.gap)),
        }
        for r in worst.itertuples()
    ]
    results = {
        "ratings_file": str(path),
        "simulated": bool(simulated),
        "scenarios_rated": int(len(df)),
        "raters": int(ratings["rater_id"].nunique()),
        "spearman": rho,
        "spearman_ci": rho_ci,
        "overlap": overlap,
        "disagreements": disagreements,
    }
    write_json("expert_compare.json", results)
    write_report("expert_compare.md", "Tool vs. expert ratings", _markdown(results))
    return results


def _markdown(r: dict) -> str:
    warn = (
        "> **SIMULATED RATERS.** These ratings were generated from the synthetic ground truth "
        "only to demonstrate the comparison. Replace them with real expert ratings "
        "(`make expert-compare RATINGS=path/to/ratings.csv`).\n\n"
        if r["simulated"]
        else ""
    )
    o = r["overlap"]
    dis = md_table(
        [
            "Scenario",
            "Tool probability",
            "Tool potential",
            "Pathway",
            "Expert mean (1–5)",
            "Direction",
        ],
        [
            [
                d["scenario_id"],
                fmt(d["tool_probability"]),
                d["potential"],
                d["pathway"],
                fmt(d["expert_mean"], 2),
                d["direction"],
            ]
            for d in r["disagreements"]
        ],
    )
    return f"""
{warn}Ratings file: `{Path(r["ratings_file"]).name}` — {r["scenarios_rated"]} scenarios,
{r["raters"]} rater(s).

## Agreement

| Measure | Value |
|---|---|
| Spearman rank correlation (tool probability vs. mean expert rating) | {fmt_ci(r["spearman"], r["spearman_ci"])} |
| Tool High tier (base tier for no-response cases) | {o["tool_high"]} scenarios |
| Experts' top tier (mean rating ≥ 4) | {o["expert_top"]} scenarios |
| In both | {o["both"]} (Jaccard overlap {fmt(o["jaccard"], 2)}) |
| Share of the experts' top tier that the tool flags High | {fmt(o["share_of_expert_top_flagged_high"], 2)} |
| Share of the tool's High tier the experts rate top | {fmt(o["share_of_tool_high_rated_top"], 2)} |

Because the High threshold is set for 95% recall, the tool's High tier is deliberately broad, so
it should contain most of the experts' top tier; the reverse share is expected to be lower.

## The 10 largest disagreements

Ranked by the gap between the tool's and the experts' percentile ranks. Use the scenario IDs with
`expert_pack/key.csv` (staff only) to open the cases and discuss them.

{dis}
"""


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ratings", default=None)
    args = parser.parse_args(argv)
    r = run(args.ratings)
    label = " (SIMULATED raters)" if r["simulated"] else ""
    print(
        f"expert-compare{label}: Spearman {r['spearman']:.3f} over {r['scenarios_rated']} "
        "scenarios -> reports/expert_compare.md"
    )


if __name__ == "__main__":
    main()
