# Model card — settlement opportunity score

> Prototype — synthetic data only. Not legal advice. Every number referenced here comes from
> synthetic data: it shows the system works as designed and that the learning mechanism behaves
> as intended, **not** real-world accuracy.

## Intended use

- **What:** rank open Indiana small-claims eviction (EV) cases by how likely they are to resolve
  by agreement or dismissal around the first hearing, so court staff can **prioritize outreach**
  for diversion.
- **Who:** court clerks and facilitators. Judges can view scores and reasons.
- **Advisory only:** the score sets call order. It does not determine eligibility, scheduling or
  any outcome, and every case receives the standard diversion offer.

**Out of scope:** decisions about a case, eligibility screening, predicting judgments, any use
by landlords or tenants, and any use on real data before a silent pilot.

## Label

A case is **resolved** if its disposition is in `config/app.yaml: dispositions` with
`resolved: true`: agreed judgments and every dismissal (PLAN.md default A1). When the
disposition code is unknown, `resolved_events` is used instead. Outcomes count only after their
disposition date (label lag).

## Layer 1: base model

**Algorithm:** L2 logistic regression on standardized features. Coefficients, means, standard
deviations and thresholds are stored in `data/model/model.json`.

**Features**, all *as of the filing date* (nothing learned after filing is used):

| Feature | Definition |
|---|---|
| Landlord resolution rate | (resolved + m·μ) / (closed + m), counting only this landlord's cases closed before the filing date. μ = court-wide rate as of filing; m = 10 |
| Prior filings (12 months) | other filings sharing a normalized defendant name or address in the previous 365 days (cap 5) |
| Plaintiff attorney | 0/1 |
| Defendant count | cap 4 |
| Amount claimed | log(1 + amount); a claim at or above the $10,000 cap counts as **unknown** (indicator + median imputation) |
| Court | one-hot |
| Days to first hearing | first scheduled hearing minus filing date (later continuances excluded) |

**Explanations:** each feature's contribution is coef × z, its effect on the log-odds relative to
an average case.

**Thresholds:**
- **High** = the highest score that keeps recall ≥ 0.95 on a time-based validation slice (the
  newest 20% of closed cases).
- **Low** = below the score that keeps High+Medium recall ≥ 0.99.

## Layer 2: answer rules (`config/scoring.yaml`)

Weights in log-odds units are added to the base score:
- the overlap check: overlap / bridgeable / wide
- goals alignment
- mediation willingness
- pending rental assistance
- fee waivers
- a move-out extension matching the tenant's wish

Further rules:
- **Both sides willing to mediate:** a wide money gap lowers *confidence* only, and the score is
  floored at the referral (High) threshold.
- **No response from either side:** shown as *No response — needs outreach*, never Low.
- **Legal issues** (habitability, retaliation, notice or payment dispute): a *Legal-aid referral*
  flag is shown beside the result and never hides it. The default weight is 0.
- **Reasons:** the 2–4 largest contributions from either layer, in plain language.

These weights are hand-set starting points. They should be fitted once pilot intake data exists.

## Evaluation (synthetic)

| Report | What it shows |
|---|---|
| `reports/model_eval.md` | time-split test: recall at the High threshold with bootstrap CI, FPR, precision, AUC, calibration, staircase; comparison with a noise-free "oracle" |
| `reports/learning.md` | weekly retraining over a 12-week feed on a fixed holdout (threshold re-tuned on half A, reported on half B); champion–challenger log; FPR vs. training size; exploration-sample estimate of missed settlements |
| `reports/expert_compare.md` | rank agreement with expert ratings (simulated until real raters are used) |

**Expected trade-off:** holding recall at 95% makes the High tier broad, so the false positive
rate is high. That is a deliberate policy choice (missing a settleable case costs more than an
extra call), and it should be revisited with real data.

## Fairness and ethics

- **No protected attributes are used.** Names and addresses are used **only** to count prior
  filings, never as features themselves.
- **Prior filings** can encode housing instability or serial-filing practices. The direction of
  that effect must be checked on real data, and staff should read the score as call priority,
  not as a judgment about the tenant.
- **Possible disparate patterns** (by court, area or language) must be audited during the silent
  pilot before any live use: compare tier rates and calibration across groups.
- **Answers are disclosed** to the judge and staff before any question is asked. Participation is
  voluntary. Spanish text is a draft pending translator review.

## Limitations

- The synthetic data comes from a simple generative model, so its effect sizes and outcome mix
  are placeholders.
- The feature set is small. Most remaining false positives come from outcome noise plus the
  recall rule.
- The cold-start learning curve is noisy at the synthetic docket's size (about 40 filings a
  week).
- Sealed and confidential cases are used **in aggregate** for training and landlord rates (PLAN.md
  default B6). This needs court confirmation.

## Maintenance

- Retrain on all cases closed so far (`core.scoring_service.train_production_model`).
- Promote a new version only if it beats the current champion on a fixed holdout
  (`make eval-learning` shows the procedure).
- Keep an exploration sample to estimate missed settlements among low-ranked cases.
