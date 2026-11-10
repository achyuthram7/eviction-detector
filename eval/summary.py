"""`make summary`: one-page summary combining every evaluation result."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from eval.common import fmt, fmt_ci, pct, read_json, reports_dir, write_report


def _tests() -> dict | None:
    path = reports_dir() / "test_results.xml"
    if not path.exists():
        return None
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = sum(int(s.get("tests", 0)) for s in suites)
    failed = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    scenario = [
        c
        for s in suites
        for c in s.iter("testcase")
        if c.get("classname", "").endswith("test_scenarios")
    ]
    scenario_failed = [
        c for c in scenario if c.find("failure") is not None or c.find("error") is not None
    ]
    return {
        "total": total,
        "failed": failed,
        "skipped": skipped,
        "scenario_total": len(scenario),
        "scenario_failed": len(scenario_failed),
    }


def build() -> str:
    tests = _tests()
    model = read_json("model_eval.json")
    learn = read_json("learning.json")
    expert = read_json("expert_compare.json")
    usab = read_json("usability.json")
    lines = []

    lines.append("## Scenario tests")
    if tests:
        status = "all pass" if tests["failed"] == 0 else f"**{tests['failed']} failing**"
        lines.append(
            f"- {tests['total']} automated tests, {status}; the section-G scenario suite has "
            f"{tests['scenario_total']} tests ({tests['scenario_failed']} failing)."
        )
    else:
        lines.append("- Not run yet: `make test` writes `reports/test_results.xml`.")

    lines.append("\n## Recall held at ≥ 0.95 (base model, time split)")
    if model:
        t, ci = model["test_at_threshold"], model["test_ci"]
        lines += [
            f"- Threshold chosen on validation for recall ≥ {model['recall_target']:.2f} "
            f"(validation recall {fmt(model['validation_at_threshold']['recall'])}).",
            f"- On the newest {model['n_test']:,} cases: **recall {fmt_ci(t['recall'], ci['recall'])}**, "
            f"false positive rate {fmt_ci(t['fpr'], ci['fpr'])}, precision "
            f"{fmt_ci(t['precision'], ci['precision'])}; {pct(t['flagged_share'])} of cases flagged High.",
            f"- AUC {fmt_ci(model['auc'], model['auc_ci'])}; the staircase rises from "
            f"{pct(model['deciles'][0]['actual_rate'])} (lowest decile) to "
            f"{pct(model['deciles'][-1]['actual_rate'])} (highest). Details: `model_eval.md`.",
        ]
    else:
        lines.append("- Not run yet (`make eval-model`).")

    lines.append("\n## False positive learning curve")
    if learn:
        f = learn["first"]
        ext = learn["extended"]
        lines += [
            f"- 12-week simulation with weekly retraining: recall stayed near target on the fixed "
            f"holdout (lowest {fmt(learn['min_recall_b'])}); FPR {fmt(f['fpr_b'])} at launch "
            f"({f['n_train']} cases) → champion in use {fmt(learn['final_champion_fpr_b'])} after "
            f"{learn['last']['week']} weeks ({learn['last']['n_train']} cases); "
            f"{learn['promotions']} champion promotion(s), worse retrains rejected.",
        ]
        if ext:
            lines.append(
                f"- By training size: FPR {fmt(ext[0]['fpr_median'])} with {ext[0]['n_train']:,} "
                f"cases → {fmt(ext[-1]['fpr_median'])} with {ext[-1]['n_train']:,}."
            )
        e = learn["exploration"]
        lines.append(
            f"- Exploration sample ({e['share']:.1%} of below-threshold cases): estimated "
            f"{e['estimated_missed']:.0f} missed settlements (95% CI "
            f"{e['estimated_missed_ci'][0]:.0f}–{e['estimated_missed_ci'][1]:.0f}); recorded "
            f"count {e['actual_resolved_below']}."
        )
        lines.append(f"\n![Learning curve]({learn['figures']['weekly']})")
        if "by_size" in learn["figures"]:
            lines.append(f"![FPR by training size]({learn['figures']['by_size']})")
    else:
        lines.append("- Not run yet (`make eval-learning`).")

    lines.append("\n## Expert comparison")
    if expert:
        o = expert["overlap"]
        tag = " **(simulated raters — replace with real ratings)**" if expert["simulated"] else ""
        lines.append(
            f"- Spearman {fmt_ci(expert['spearman'], expert['spearman_ci'])} over "
            f"{expert['scenarios_rated']} scenarios{tag}; {o['both']} of the experts' "
            f"{o['expert_top']} top-rated scenarios are in the tool's High tier. "
            "Details: `expert_compare.md`."
        )
    else:
        lines.append("- Not run yet (`make expert-pack` then `make expert-compare`).")

    lines.append("\n## Usability")
    if usab:
        g = usab["groups"][usab["headline"]]["all"]
        if g.get("n"):
            kind = (
                "testing-window submissions"
                if usab["headline"] == "live"
                else ("seeded synthetic timings — no real submissions yet")
            )
            lines.append(
                f"- **Median intake time {g['median_s'] / 60:.1f} min** ({kind}, n = {g['n']}); "
                f"{g['under_5_min']:.0%} under the 5-minute target. Details: `usability.md`."
            )
        else:
            lines.append("- No completed intakes recorded.")
    else:
        lines.append("- Not run yet (`make usability`).")

    lines.append(
        "\n## Caveats\n"
        "- Synthetic outcome mix and effect sizes are placeholders "
        "(`config/synthetic.yaml`); replace with Indiana's published shares before drawing "
        "conclusions.\n"
        "- The Odyssey checklist, order text, retention period and restriction rules are "
        "placeholders pending court confirmation (PLAN.md section 14B).\n"
        "- Next step: a silent pilot on real court data, scoring cases without acting on the "
        "scores, then comparing against recorded outcomes."
    )
    return "\n".join(lines)


def main() -> None:
    path = write_report("summary.md", "Summary: Eviction Settlement Opportunity Detector", build())
    print(f"summary -> {path}")


if __name__ == "__main__":
    main()
