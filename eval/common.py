"""Shared helpers for the evaluation scripts: data loading, bootstrap CIs, charts, reports."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # files only, no display
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.features import frame_from_export  # noqa: E402
from core.settings import get_settings  # noqa: E402
from synth import paths  # noqa: E402

DISCLAIMER = (
    "Results use synthetic data. They show the system works as designed and that the learning "
    "mechanism behaves as intended. They do not show real-world accuracy."
)

# Reference-palette roles (light surface; static PNGs). Slots 1-2 are the validated
# adjacent categorical pair; neutrals are the chart chrome/ink roles.
C = {
    "surface": "#fcfcfb",
    "ink": "#0b0b0b",
    "ink2": "#52514e",
    "muted": "#898781",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "s1": "#2a78d6",
    "s2": "#eb6834",
}


def reports_dir() -> Path:
    d = get_settings().reports_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def figures_dir() -> Path:
    d = reports_dir() / "figures"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_history() -> pd.DataFrame:
    """Closed historical cases (as exported at the seed snapshot) + ground truth columns."""
    raw = pd.read_csv(paths.history_csv(), dtype=str, keep_default_na=False)
    frame = frame_from_export(raw)
    truth = pd.read_csv(paths.ground_truth_path()).set_index("case_number")
    frame = frame[frame["resolved"].notna()].copy()
    frame["latent_p"] = truth.loc[frame["case_number"], "latent_p"].to_numpy()
    frame["true_resolved"] = truth.loc[frame["case_number"], "true_resolved"].to_numpy()
    return frame.sort_values(["filing_date", "case_number"]).reset_index(drop=True)


def load_truth() -> pd.DataFrame:
    return pd.read_csv(paths.ground_truth_path()).set_index("case_number")


def bootstrap(
    stat: Callable[..., float], *arrays: np.ndarray, n: int = 1000, seed: int = 7
) -> tuple[float, float]:
    """Percentile 95% CI of stat(*arrays) over resamples of rows."""
    rng = np.random.default_rng(seed)
    size = len(arrays[0])
    values = []
    for _ in range(n):
        idx = rng.integers(0, size, size)
        v = stat(*(a[idx] for a in arrays))
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            values.append(v)
    if not values:
        return float("nan"), float("nan")
    return float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5))


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


def fmt(value: float, digits: int = 3) -> str:
    return (
        "n/a"
        if value is None or (isinstance(value, float) and math.isnan(value))
        else (f"{value:.{digits}f}")
    )


def fmt_ci(value: float, ci: tuple[float, float], digits: int = 3) -> str:
    return f"{fmt(value, digits)} (95% CI {fmt(ci[0], digits)}–{fmt(ci[1], digits)})"


def pct(value: float) -> str:
    return "n/a" if value is None or math.isnan(value) else f"{value:.1%}"


# --- charts ------------------------------------------------------------------------------------


def new_axes(width: float = 6.4, height: float = 4.0):
    fig, ax = plt.subplots(figsize=(width, height), dpi=150)
    fig.patch.set_facecolor(C["surface"])
    ax.set_facecolor(C["surface"])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(C["axis"])
    ax.tick_params(colors=C["ink2"], labelsize=8)
    ax.grid(True, color=C["grid"], linewidth=0.6)
    ax.set_axisbelow(True)
    ax.xaxis.label.set_color(C["ink2"])
    ax.yaxis.label.set_color(C["ink2"])
    return fig, ax


def title(ax, text: str, subtitle: str | None = None) -> None:
    ax.set_title(text, loc="left", fontsize=10, color=C["ink"], fontweight="bold", pad=14)
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=7.5, color=C["ink2"])


def save(fig, name: str) -> str:
    """Save under reports/figures and return the path relative to reports/."""
    fig.tight_layout()
    path = figures_dir() / name
    fig.savefig(path, facecolor=C["surface"])
    plt.close(fig)
    return f"figures/{name}"


# --- report files ------------------------------------------------------------------------------


def write_report(name: str, heading: str, body: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = f"# {heading}\n\n> **{DISCLAIMER}**\n\n_Generated {stamp}._\n\n{body.strip()}\n"
    path = reports_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_json(name: str, data: dict) -> Path:
    path = reports_dir() / name
    path.write_text(json.dumps(data, indent=2, default=_json_default), encoding="utf-8")
    return path


def read_json(name: str) -> dict | None:
    path = reports_dir() / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _json_default(value):
    if isinstance(value, np.integer | np.floating):
        return value.item()
    if isinstance(value, datetime | pd.Timestamp):
        return value.isoformat()
    return str(value)


def md_table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)
