"""Tiny translation helper for the party-facing pages (English / Spanish)."""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from pathlib import Path

import yaml

LANGS = ("en", "es")
_DIR = Path(__file__).resolve().parent / "locales"


@cache
def _strings(lang: str) -> dict[str, str]:
    with open(_DIR / f"{lang}.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def pick_lang(value: str | None) -> str:
    return value if value in LANGS else "en"


def translator(lang: str) -> Callable[..., str]:
    lang = pick_lang(lang)
    primary, fallback = _strings(lang), _strings("en")

    def t(key: str, **kwargs) -> str:
        text = primary.get(key) or fallback.get(key) or key
        return text.format(**kwargs) if kwargs else text

    return t
