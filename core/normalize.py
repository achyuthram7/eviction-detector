"""Parsing and normalization helpers for feed values, names and addresses."""

from __future__ import annotations

import re
from datetime import date, datetime

_NON_ALPHA = re.compile(r"[^a-z\s]")
_NON_ALNUM = re.compile(r"[^a-z0-9\s]")
_SPACES = re.compile(r"\s+")

_ADDRESS_WORDS = {
    "street": "st",
    "avenue": "ave",
    "road": "rd",
    "drive": "dr",
    "lane": "ln",
    "court": "ct",
    "boulevard": "blvd",
    "place": "pl",
    "apartment": "unit",
    "apt": "unit",
    "suite": "unit",
    "ste": "unit",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
}

TRUE_VALUES = {"true", "1", "y", "yes", "t"}
FALSE_VALUES = {"false", "0", "n", "no", "f", ""}


def norm_name(name: str) -> str:
    """Order-insensitive normalized person name ("Smith, Ann" == "ann smith")."""
    tokens = _SPACES.sub(" ", _NON_ALPHA.sub(" ", name.lower())).split()
    return " ".join(sorted(tokens))


def last_name_norm(full_name: str) -> str:
    """Last whitespace token, letters only, lower case. "Ann Smith-Lee" -> "smithlee"."""
    tokens = full_name.replace(",", " ").split()
    if not tokens:
        return ""
    # "Smith, Ann" form: the surname comes first.
    last = tokens[0] if "," in full_name else tokens[-1]
    return re.sub(r"[^a-z]", "", last.lower())


def norm_address(address: str) -> str:
    text = address.lower().replace("#", " unit ")
    tokens = _SPACES.sub(" ", _NON_ALNUM.sub(" ", text)).split()
    return " ".join(_ADDRESS_WORDS.get(t, t) for t in tokens)


def split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(";")]


def parse_bool(value: str | None) -> bool | None:
    v = (value or "").strip().lower()
    if v in TRUE_VALUES:
        return True
    if v in FALSE_VALUES:
        return False
    return None


def parse_date(value: str | None) -> date | None:
    v = (value or "").strip()
    if not v:
        return None
    return date.fromisoformat(v[:10])


def parse_datetime(value: str | None) -> datetime | None:
    v = (value or "").strip()
    if not v:
        return None
    if len(v) == 10:
        return datetime.fromisoformat(v + "T00:00")
    return datetime.fromisoformat(v.replace(" ", "T")).replace(second=0, microsecond=0)


def parse_amount(value: str | None) -> float | None:
    v = (value or "").strip().replace("$", "").replace(",", "")
    if not v:
        return None
    return round(float(v), 2)
