"""Usability: intake completion time from the recorded open/submit timestamps.

Seeded (synthetic) timings and real submissions made during the testing window are reported
separately, so testers' timings are never mixed with generated ones.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import select

from core.db import session_scope
from core.models import IntakeTiming
from eval.common import C, fmt, md_table, new_axes, save, title, write_json, write_report

TARGET_SECONDS = 300


def _summary(df: pd.DataFrame) -> dict:
    s = df["seconds"].to_numpy()
    if s.size == 0:
        return {"n": 0}
    return {
        "n": int(s.size),
        "median_s": float(np.median(s)),
        "p25_s": float(np.percentile(s, 25)),
        "p75_s": float(np.percentile(s, 75)),
        "p90_s": float(np.percentile(s, 90)),
        "under_5_min": float((s < TARGET_SECONDS).mean()),
    }


def run() -> dict:
    with session_scope() as s:
        rows = [
            (
                t.role,
                t.kind,
                t.language,
                t.is_synthetic,
                (t.submitted_at - t.opened_at).total_seconds(),
            )
            for t in s.scalars(select(IntakeTiming).where(IntakeTiming.submitted_at.is_not(None)))
        ]
    df = pd.DataFrame(rows, columns=["role", "kind", "language", "synthetic", "seconds"])
    df = df[df["seconds"] > 0]
    groups = {}
    for label, part in (("live", df[~df["synthetic"]]), ("synthetic", df[df["synthetic"]])):
        groups[label] = {
            "all": _summary(part),
            "by_role": {r: _summary(g) for r, g in part.groupby("role")},
            "by_language": {lang: _summary(g) for lang, g in part.groupby("language")},
        }
    headline = "live" if groups["live"]["all"]["n"] else "synthetic"
    figs = {}
    part = df[~df["synthetic"]] if headline == "live" else df[df["synthetic"]]
    if not part.empty:
        fig, ax = new_axes(6.4, 3.6)
        minutes = part["seconds"].to_numpy() / 60
        ax.hist(
            minutes,
            bins=np.arange(0, max(12, minutes.max() + 1), 0.5),
            color=C["s1"],
            edgecolor=C["surface"],
            linewidth=1,
        )
        ax.axvline(5, color=C["ink2"], linewidth=1, linestyle="--")
        ax.text(5.1, ax.get_ylim()[1] * 0.92, "5-minute target", fontsize=7, color=C["ink2"])
        ax.set_xlabel("Minutes from opening the form to submitting")
        ax.set_ylabel("Intakes")
        title(
            ax,
            f"Intake completion time ({headline} submissions)",
            f"Median {np.median(minutes):.1f} min · n = {len(minutes)}",
        )
        figs["hist"] = save(fig, "intake_times.png")
    results = {
        "headline": headline,
        "groups": groups,
        "target_seconds": TARGET_SECONDS,
        "figures": figs,
    }
    write_json("usability.json", results)
    write_report("usability.md", "Usability: intake completion time", _markdown(results))
    return results


def _rows(groups: dict, label: str) -> list[list]:
    g = groups[label]
    rows = []
    for name, stats in (
        [("All", g["all"])]
        + [(f"Role: {k}", v) for k, v in g["by_role"].items()]
        + [(f"Language: {k}", v) for k, v in g["by_language"].items()]
    ):
        if not stats.get("n"):
            continue
        rows.append(
            [
                name,
                stats["n"],
                fmt(stats["median_s"] / 60, 1),
                f"{fmt(stats['p25_s'] / 60, 1)}–{fmt(stats['p75_s'] / 60, 1)}",
                fmt(stats["p90_s"] / 60, 1),
                f"{stats['under_5_min']:.0%}",
            ]
        )
    return rows


def _markdown(r: dict) -> str:
    headers = ["Group", "Intakes", "Median (min)", "IQR (min)", "90th pct (min)", "Under 5 min"]
    live = _rows(r["groups"], "live")
    synth = _rows(r["groups"], "synthetic")
    fig = f"![Intake times]({r['figures']['hist']})" if "hist" in r["figures"] else ""
    live_md = (
        md_table(headers, live)
        if live
        else (
            "_No real submissions recorded yet. During the testing window, testers' submissions "
            "through the token links appear here._"
        )
    )
    return f"""
Completion time = submit time minus the time the form was opened (both recorded by the app).
Target: under 5 minutes.

## Testing-window submissions (real)

{live_md}

## Seeded synthetic timings (generated, not real people)

{md_table(headers, synth) if synth else "_none_"}

{fig}
"""


def main() -> None:
    r = run()
    g = r["groups"][r["headline"]]["all"]
    median = g.get("median_s")
    shown = f"{median / 60:.1f} min" if median else "n/a"
    print(
        f"usability: median intake time {shown} ({r['headline']}, n={g.get('n', 0)}) "
        "-> reports/usability.md"
    )


if __name__ == "__main__":
    main()
