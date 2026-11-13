"""Settings: YAML config files plus a few environment-variable overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from core.constants import ROLES

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"


def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    reports_dir: Path
    database_url: str
    sqlite_busy_timeout: float
    secret_key: str
    public_base_url: str
    active_staff_roles: frozenset[str]
    app: dict
    scoring: dict
    synthetic: dict
    diversion: dict

    @property
    def model_path(self) -> Path:
        return self.data_dir / "model" / "model.json"

    @property
    def synthetic_dir(self) -> Path:
        return self.data_dir / "synthetic"

    @property
    def demo_dir(self) -> Path:
        return self.data_dir / "demo"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = Path(os.environ.get("DATA_DIR") or ROOT / "data")
    reports_dir = Path(os.environ.get("REPORTS_DIR") or ROOT / "reports")
    db_url = os.environ.get("DATABASE_URL") or f"sqlite:///{(data_dir / 'app.db').as_posix()}"
    if (
        db_url.startswith("sqlite:///")
        and not Path(db_url.removeprefix("sqlite:///")).is_absolute()
    ):
        # Relative SQLite paths are relative to the project root, not the current directory.
        db_url = f"sqlite:///{(ROOT / db_url.removeprefix('sqlite:///')).as_posix()}"
    app = load_yaml("app.yaml")
    roles_env = os.environ.get("ACTIVE_STAFF_ROLES")
    roles = roles_env.split(",") if roles_env else app.get("active_staff_roles") or ROLES
    return Settings(
        root=ROOT,
        data_dir=data_dir,
        reports_dir=reports_dir,
        database_url=db_url,
        # Seconds a write waits for SQLite's lock before giving up (the driver default is 5,
        # too short on a small shared CPU such as Render's free plan).
        sqlite_busy_timeout=float(os.environ.get("SQLITE_BUSY_TIMEOUT") or 30),
        secret_key=os.environ.get("SECRET_KEY") or "dev-only-secret-change-me",
        public_base_url=(os.environ.get("PUBLIC_BASE_URL") or "http://localhost:8000").rstrip("/"),
        # Staff roles allowed to sign in; the others are paused (config/app.yaml).
        active_staff_roles=frozenset(r.strip().lower() for r in roles if r.strip()) & set(ROLES),
        app=app,
        scoring=load_yaml("scoring.yaml"),
        synthetic=load_yaml("synthetic.yaml"),
        diversion=load_yaml("diversion_checklist.yaml"),
    )


def reset_settings() -> None:
    """Forget cached settings (tests change environment variables)."""
    get_settings.cache_clear()
