"""Test fixtures: a fresh SQLite DB per test, a frozen clock, and small CSV builders."""

from __future__ import annotations

import pytest

from core.db import init_db, new_session, reset_engine
from core.settings import reset_settings

TODAY = "2026-03-16"


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("APP_TODAY", TODAY)
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    reset_settings()
    reset_engine()
    init_db(drop=True)
    yield
    reset_engine()
    reset_settings()


@pytest.fixture
def session():
    s = new_session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def simple_model():
    """A hand-set base model so scoring tests are fast and deterministic."""
    from core.features import FEATURE_COLUMNS
    from core.model import BaseModel
    from core.settings import get_settings

    def make(intercept: float = 0.0, t_high: float = 0.4, t_low: float = 0.2) -> BaseModel:
        n = len(FEATURE_COLUMNS)
        coef = [0.8, 0.1, 0.05, 0.1, -0.3, -0.05, 0.1]
        model = BaseModel(
            version=f"test-{intercept}",
            trained_at="2026-03-01T00:00:00",
            feature_names=list(FEATURE_COLUMNS),
            means=[0.5, 0.2, 0.5, 1.3, 7.5, 0.25, 28.0][:n],
            stds=[0.15, 0.5, 0.5, 0.5, 0.6, 0.43, 5.0][:n],
            coef=coef[:n],
            intercept=intercept,
            court_levels=["97K01"],
            court_reference="97K01",
            impute={"log_amount": 7.5, "days_to_hearing": 28.0},
            base_rate=0.5,
            n_train=100,
            threshold_high=t_high,
            threshold_low=t_low,
        )
        model.save(get_settings().model_path)
        return model

    return make
