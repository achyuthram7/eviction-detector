"""File locations for generated data.

Ground truth is defined here (not in core/) on purpose: the app must never read it.
Only synth/ and eval/ import GROUND_TRUTH_PATH.
"""

from __future__ import annotations

from pathlib import Path

from core.settings import get_settings


def synthetic_dir() -> Path:
    return get_settings().data_dir / "synthetic"


def demo_dir() -> Path:
    return get_settings().data_dir / "demo"


def ground_truth_path() -> Path:
    return get_settings().data_dir / "ground_truth" / "ground_truth.csv"


def history_csv() -> Path:
    return synthetic_dir() / "history.csv"


def live_csv() -> Path:
    return synthetic_dir() / "live.csv"


def intake_json() -> Path:
    return synthetic_dir() / "intake_seed.json"


def edge_cases_json() -> Path:
    return synthetic_dir() / "edge_cases.json"


def demo_cases_json() -> Path:
    return demo_dir() / "demo_cases.json"
