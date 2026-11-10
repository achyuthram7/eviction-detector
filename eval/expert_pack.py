"""`make expert-pack`: 50 blind case scenarios for expert raters.

Stratified across settlement potential (incl. "No response") and pathway, drawn from the open
docket. The packet shows facts and answers but NOT the tool's score, so raters are not anchored.
Writes to reports/expert_pack/:
  packet.html             printable rater packet
  ratings.csv             blank rating sheet (scenario_id, rater_id, likelihood_1_5, notes)
  key.csv                 scenario -> case number + tool output (NOT for raters)
  ratings_simulated.csv   SIMULATED raters, only so `make expert-compare` can be demonstrated
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from datetime import datetime

import numpy as np
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select

from app.i18n import translator
from app.presenters import landlord_rows, tenant_rows
from core.constants import BANNER_TEXT, TENANT
from core.db import session_scope
from core.models import Case, CaseScore, LandlordResponse, TenantResponse
from core.settings import ROOT, get_settings
from eval.common import DISCLAIMER, load_truth, reports_dir

N_SCENARIOS = 50


def _allocate(strata: dict[tuple, list], total: int) -> dict[tuple, int]:
    """At least one per stratum, the rest proportional to size (largest remainder)."""
    keys = sorted(strata, key=lambda k: (-len(strata[k]), k))
    alloc = {k: 0 for k in keys}
    for k in keys[:total]:
        alloc[k] = 1
    left = total - sum(alloc.values())
    size = sum(len(v) for v in strata.values())
    if left > 0 and size:
        quotas = {k: left * len(strata[k]) / size for k in keys}
        for k in keys:
            alloc[k] += math.floor(quotas[k])
        rest = total - sum(alloc.values())
        for k in sorted(keys, key=lambda k: -(quotas[k] - math.floor(quotas[k])))[:rest]:
            alloc[k] += 1
    alloc = {k: min(v, len(strata[k])) for k, v in alloc.items()}
    # Hand slots lost to small strata to the largest strata that still have cases.
    while sum(alloc.values()) < min(total, size):
        k = max(
            (k for k in keys if alloc[k] < len(strata[k])), key=lambda k: len(strata[k]) - alloc[k]
        )
        alloc[k] += 1
    return alloc


def _vignette(case: Case, score: CaseScore, tenants, landlord) -> dict:
    t = translator("en")
    base = (score.features or {}).get("base", {})
    values = base.get("values", {})
    n_prior = int(base.get("landlord_n", 0))
    facts = [
        ("Court", case.court_id),
        ("Days from filing to first hearing", int(values.get("days_to_hearing") or 0)),
        (
            "Amount claimed",
            "lists the $10,000 small-claims cap (actual amount unknown)"
            if case.amount_is_placeholder
            else f"${case.amount_claimed:,.0f}",
        ),
        ("Landlord has an attorney", "yes" if case.plaintiff_attorney else "no"),
        ("Tenants named", case.defendant_count),
        (
            "Landlord's prior closed cases in this data",
            f"{n_prior} (about {values.get('landlord_rate', 0):.0%} ended without a judgment, "
            "blended with the court average when few)"
            if n_prior
            else "none on record",
        ),
        (
            "Other eviction filings against the tenant/address in the past year",
            int(values.get("prior_filings_12m") or 0),
        ),
    ]
    return {
        "facts": facts,
        "tenants": [tenant_rows(r, t) for r in tenants],
        "landlord": landlord_rows(landlord, t) if landlord else None,
        "landlord_policy": bool(landlord and landlord.source == "policy"),
    }


def run() -> dict:
    out = reports_dir() / "expert_pack"
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(int(get_settings().scoring["evaluation"]["seed"]))
    with session_scope() as s:
        rows = s.execute(
            select(Case, CaseScore)
            .join(CaseScore, (CaseScore.case_id == Case.id) & CaseScore.is_current.is_(True))
            .where(Case.is_restricted.is_(False))  # never put restricted cases in a packet
        ).all()
        strata: dict[tuple, list] = defaultdict(list)
        for case, score in rows:
            strata[(score.potential, score.pathway)].append((case, score))
        alloc = _allocate(strata, min(N_SCENARIOS, len(rows)))
        chosen = []
        for key, n in alloc.items():
            pool = strata[key]
            for i in rng.choice(len(pool), size=n, replace=False):
                chosen.append(pool[int(i)])
        order = rng.permutation(len(chosen))
        scenarios, key_rows = [], []
        for pos, i in enumerate(order, start=1):
            case, score = chosen[int(i)]
            sid = f"S{pos:02d}"
            tenants = s.scalars(
                select(TenantResponse).where(TenantResponse.case_id == case.id)
            ).all()
            landlord = s.scalar(select(LandlordResponse).where(LandlordResponse.case_id == case.id))
            scenarios.append({"id": sid, **_vignette(case, score, tenants, landlord)})
            key_rows.append(
                {
                    "scenario_id": sid,
                    "case_number": case.case_number,
                    "tool_probability": round(score.final_prob, 4),
                    "potential": score.potential,
                    "base_tier": score.base_tier,
                    "pathway": score.pathway,
                    "confidence": score.confidence,
                    "legal_aid_flag": score.legal_aid_flag,
                    "tenant_parties": sum(1 for p in case.parties if p.role == TENANT),
                }
            )

    env = Environment(
        loader=FileSystemLoader(str(ROOT / "eval" / "templates")),
        autoescape=select_autoescape(["html"]),
    )
    html = env.get_template("expert_packet.html").render(
        scenarios=scenarios,
        banner=BANNER_TEXT,
        disclaimer=DISCLAIMER,
        generated=datetime.now().strftime("%Y-%m-%d"),
    )
    (out / "packet.html").write_text(html, encoding="utf-8")

    with open(out / "ratings.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["scenario_id", "rater_id", "likelihood_1_5", "notes"])
        for sc in scenarios:
            w.writerow([sc["id"], "", "", ""])
    with open(out / "key.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(key_rows[0]))
        w.writeheader()
        w.writerows(key_rows)

    # Simulated raters: noisy readings of each case's latent probability (ground truth).
    truth = load_truth()
    with open(out / "ratings_simulated.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["scenario_id", "rater_id", "likelihood_1_5", "notes"])
        for r in key_rows:
            latent = float(truth.loc[r["case_number"], "latent_p"])
            for rater in ("SIMULATED-1", "SIMULATED-2", "SIMULATED-3"):
                value = int(np.clip(round(1 + 4 * (latent + rng.normal(0, 0.15))), 1, 5))
                w.writerow([r["scenario_id"], rater, value, "simulated rater"])

    counts = defaultdict(int)
    for r in key_rows:
        counts[r["potential"]] += 1
    return {"scenarios": len(scenarios), "by_potential": dict(counts), "folder": str(out)}


def main() -> None:
    r = run()
    print(
        f"expert-pack: {r['scenarios']} scenarios {r['by_potential']} -> "
        "reports/expert_pack/packet.html, ratings.csv"
    )


if __name__ == "__main__":
    main()
