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
