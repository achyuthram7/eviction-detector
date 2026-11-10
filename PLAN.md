# PLAN — Eviction Settlement Opportunity Detector (Prototype, Team 14)

> Status: **approved with all defaults; milestones 1–6 built.** See §15 for what changed during the build.

---

## 1. Approach in one paragraph

A standalone FastAPI + SQLite app. It reads a simulated daily Odyssey export (CSV), keeps its own *working status* per case, sends tenants and landlords a random-token link or QR code to a short intake, and ranks open cases for staff outreach. The ranking has two layers: (1) a logistic-regression **base score** built only from facts known at filing, and (2) **rule-based adjustments** from the parties' answers, with weights in `config/scoring.yaml`. Every score shows 2–4 plain-language reasons. The tool is advisory only. It never writes to Odyssey, never changes scheduling and never decides eligibility. A synthetic data generator, a pytest scenario suite and the evaluation scripts for the three-week testing window ship with it. Everything runs offline, on synthetic data only.

---

## 2. What I found in the environment

| Item | Finding | Consequence for the plan |
|---|---|---|
| Python | 3.14.7 is installed locally (Anaconda 3.13.9 is also present) | The code targets **3.11+**. Local dev uses 3.14 in a project `.venv`. The Docker image uses `python:3.12-slim`. |
| `make` | **Not installed** | All logic lives in a stdlib `manage.py` CLI. The `Makefile` is a thin wrapper (`make seed` → `python manage.py seed`). The README lists both forms, so Windows users don't need `make`. |
| Docker | **Not installed** | I'll write the `Dockerfile` and `docker-compose.yml` but can't run `docker compose up` here. See Q A5. |
| Git | Your **user folder is itself a Git repo** (branch `main`, no commits). This folder sits inside it. | I'll run `git init` in the project folder so this project's commits never land in that home-folder repo. See Q A4. |

---

## 3. Milestones

After each milestone: run `make test` and `ruff`, fix failures, summarize the changes and any decisions you need to make, and commit.

| # | Milestone | Main deliverables | Scenario tests (§5.G) that must pass at the end |
|---|---|---|---|
| **M1** | Data model, synthetic generator, import/sync | repo scaffold (`git init`, `pyproject.toml`, ruff, pytest, `manage.py`, `Makefile`, config YAMLs); SQLAlchemy models; `synth/` generator (history, live docket, `day1.csv`, `day2.csv`, separate ground-truth file); importer with validation, idempotent upserts, import report, reconciliation; working-status state machine | Malformed row / wrong case type · Same file imported twice · Continuance (new hearing date; queue sort function) · Dismissal before hearing · Diversion stay (30/60-day dates) · Case sealed (restricted flag set) |
| **M2** | Token links, QR codes, both intakes | token + QR generation; tenant and landlord intake (EN/ES, disclosure first, timing capture); resubmission updates; multiple tenants; fallback lookup + staff review queue; landlord standing policy (portfolio link) and automatic application | Token link submit · Non-party fallback → review queue · Same token twice · Two tenants answer differently · Standing policy + new filing · Tenant views own intake (never sees landlord answers, and vice versa) |
| **M3** | Predictor (both layers) and explanations | as-of feature builder (shared by app and eval); LR training + threshold selection → `data/model/model.json`; Layer 2 rules; tiers, pathway, confidence, reasons; rescoring on import, intake and policy change | Neither party responds → "No response — needs outreach" · Repair problems → Legal-aid flag shown with result · plus as-of leakage unit tests |
| **M4** | Dashboard, roles, statuses, checklist, restricted cases, audit | login + roles; queue (sort, HTMX filters); case page; call log; working-status actions; "Both agreed to diversion" checklist + draft order; restricted-case hiding + badge; audit log; `make purge` | Judge views case (both answers + reasons) · Sealed case hidden from clerk and judge views · Restricted case opened → audit log · Continuance re-sorts the queue (end-to-end) |
| **M5** | Evaluation scripts and reports | `eval-model`, `eval-learning`, `expert-pack`, `expert-compare`, usability report, `reports/summary.md` | all of the above (regression) |
| **M6** | README, docs, Docker, polish | README (incl. 5-minute demo script), `docs/ARCHITECTURE.md`, `docs/MODEL_CARD.md`, `docs/DEMO_SCRIPT.md`, Dockerfile, compose, `.env.example`; banner/offline tests; walk the demo script end to end | all 16 + banner-on-every-page test + no-external-URL test |

---

## 4. File tree

```
.
├── PLAN.md  README.md  Makefile  manage.py  pyproject.toml  requirements.txt
├── Dockerfile  docker-compose.yml  docker-entrypoint.sh  .env.example  .gitignore  .dockerignore
├── app/                          FastAPI app
│   ├── main.py                   app factory, middleware, routers, banner context
│   ├── deps.py                   DB session, current user, role guards, restricted-case guard
│   ├── auth.py                   scrypt password hashing (stdlib), login/logout, CSRF token
│   ├── i18n.py   i18n/en.yaml  i18n/es.yaml
│   ├── routes/
│   │   ├── public.py             /r/{token} intake, /find fallback, /policy/{token}
│   │   ├── staff.py              queue, case page, call log, status actions, diversion checklist, QR/letter
│   │   └── admin.py              import + report, reconciliation, review queue, policies, audit log, users
│   ├── templates/                base.html (banner), login, queue (+ _rows partial), case, checklist,
│   │                             intake_tenant, intake_landlord, policy, find, done, letter, import_report,
│   │                             review_queue, audit, reports
│   └── static/css/app.css  static/js/htmx.min.js (vendored, pinned)
├── core/
│   ├── settings.py  clock.py  db.py  models.py
│   ├── importer.py               parse, validate, upsert, idempotency, import report
│   ├── reconcile.py              working status vs. official event
│   ├── status.py                 working-status state machine + feed override rules
│   ├── tokens.py                 token + QR PNG generation
│   ├── intake.py                 save/update answers, fallback matching, policy application
│   ├── features.py               as-of base features (used by app AND eval)
│   ├── model.py                  train / save / load / predict base LR, threshold selection
│   ├── scoring.py                Layer 2, tiers, pathway, confidence, reasons
│   ├── diversion.py              checklist + draft order text from config
│   ├── retention.py              purge + aggregate counts
│   └── audit.py
├── synth/
│   ├── generator.py              history + live docket + intake answers + ground truth
│   └── demo_files.py             day1.csv / day2.csv
├── config/
│   ├── app.yaml                  case-number regex, claim cap, roles, restriction rules, retention days, disposition map
│   ├── scoring.yaml              Layer 2 weights, gap %, tier/recall targets, confidence rules, reason templates
│   ├── synthetic.yaml            volumes, PLACEHOLDER outcome mix, effect sizes, noise, seeds
│   └── diversion_checklist.yaml  PLACEHOLDER Odyssey checklist items + draft order template
├── data/
│   ├── demo/day1.csv  demo/day2.csv          (committed)
│   └── synthetic/  ground_truth/  model/  app.db   (git-ignored)
├── eval/
│   ├── common.py                 disclaimer, bootstrap, plotting helpers, report writer
│   ├── eval_model.py  eval_learning.py  expert_pack.py  expert_compare.py  usability.py  summary.py
│   └── templates/expert_packet.html
├── reports/                      generated (git-ignored)
├── tests/
│   ├── conftest.py               temp DB, small fixture dataset, frozen clock, TestClient per role
│   ├── test_scenarios.py         the 16 scenarios in §5.G, one test each, named after the row
│   ├── test_importer.py  test_status.py  test_features_asof.py  test_scoring.py  test_retention.py
│   └── test_guards.py            banner on every page; no external URLs; app/core never read ground truth
└── docs/ARCHITECTURE.md  MODEL_CARD.md  DEMO_SCRIPT.md
```

---

## 5. Data model (SQLAlchemy 2.0 ORM on SQLite)

SQLAlchemy rather than SQLModel, since it's the more conventional choice. Schema is created with `create_all`, and `make seed` rebuilds the DB, so there are no migrations in the prototype. All datetimes are naive, in court-local time.

| Table | Key columns | Notes |
|---|---|---|
| `users` | id, username, display_name, role (clerk/facilitator/judge/admin), password_hash, active | scrypt hash + per-user salt |
| `plaintiffs` | plaintiff_id (PK, hashed from feed), label, portfolio_token | label is a synthetic display name |
| `landlord_policies` | plaintiff_id (unique), accepts_plan, max_plan_months, min_to_dismiss_pct, waive_late_fees, waive_attorney_fees, extend_moveout_days, goal, willing_to_mediate, updated_at | standing policy, one per plaintiff |
| `cases` | id, case_number (unique), court_id, case_type, filing_date, amount_claimed, amount_is_placeholder, **first_hearing_datetime**, hearing_datetime, plaintiff_id, plaintiff_attorney, defendant_count, mailing_address, latest_event, latest_event_date, disposition_type, disposition_date, confidential_flag, sealed_flag, is_restricted, working_status, settled_subtype, stay_start_date, diversion_agreed_at, row_hash, first_import_id, last_import_id | `row_hash` = hash of the normalized feed fields, which is how idempotency works. `first_hearing_datetime` is kept for the as-of rule. |
| `parties` | id, case_id, role (tenant/landlord), party_index, full_name, last_name_norm, phone, address_norm, token (unique), token_created_at, token_revoked_at, invited_at | one row per tenant; one landlord row per case |
| `tenant_responses` | party_id (**unique**), case_id, source (token/fallback), verified, language, believed_owed, pay_30d, pay_monthly, assistance_status, assistance_amount, wants (stay/move), move_time_days, repair_issues, retaliation_concern, notice_dispute, payment_dispute, legal_aid, willing_mediator, consent_text, consent_call, preferred_language, started_at, submitted_at, updated_at, revision | unique per party → resubmission updates. `revision` counts the resubmissions. |
| `landlord_responses` | party_id (**unique**), case_id, source (token/policy/fallback), amount_owed_now, fees_included, accepts_plan, max_plan_months, min_to_dismiss, waive_late_fees, waive_attorney_fees, extend_moveout, extend_moveout_days, goal, willing_mediator, started_at, submitted_at, updated_at, revision | a case-specific answer overrides a policy-sourced one |
| `intake_timings` | id, party_id, role, language, opened_at, submitted_at, is_synthetic | one row per form open, for the usability report. Seeded rows are flagged synthetic. |
| `fallback_submissions` | id, role_claimed, case_number_entered, last_name_entered, answers_json, matched_party_id (nullable), status (matched_unverified/review/attached/rejected), created_at, reviewed_by, reviewed_at | the staff review queue |
| `case_scores` | id, case_id, model_version, computed_at, base_prob, final_prob, potential, base_tier, pathway, confidence, legal_aid_flag, reasons_json, features_json, is_current | history is kept, and the dashboard reads `is_current` |
| `status_history` | id, case_id, from_status, to_status, source (feed/staff/system), import_id, user_id, at, note | shown on the case page |
| `feed_changes` | id, case_id, import_id, field, old_value, new_value | written only when a value really changes |
| `call_log` | id, case_id, party_id, user_id, at, channel, outcome, notes, next_action, next_action_due | |
| `import_runs` | id, filename, file_sha256, imported_at, user_id, rows_total, inserted, updated, unchanged, rejected, newly_restricted, reconciliation_flags | |
| `import_rejections` | id, import_id, row_number, case_number_raw, reasons | |
| `reconciliation_flags` | id, import_id, case_id, working_status_before, feed_event, kind, message, resolved_by, resolved_at | |
| `audit_log` | id, at, user_id, role, action, case_id, restricted, details | every case view, plus imports, status changes, exports and purges |
| `retention_aggregates` | month, court_id, potential, pathway, outcome_status, n_cases, n_tenant_resp, n_landlord_resp, median_intake_sec | kept after purge |

**Ground truth** (latent probability, true outcome, whether label noise flipped the row, and the true pathway) is written to `data/ground_truth/ground_truth.csv`, **outside the app DB**. `tests/test_guards.py` fails if anything under `app/` or `core/` references that path or module.

### 5.1 Import CSV spec (simulated Odyssey export)

Columns as in the prompt, plus three **optional** columns (Q A2): `latest_event_date`, `plaintiff_name`, `export_date`.

- Dates are ISO (`YYYY-MM-DD`); hearing datetimes are `YYYY-MM-DDTHH:MM`. Booleans accept `true/false/1/0/Y/N`.
- `defendant_names` and `phone` are `;`-separated and aligned by position.
- **Validation** (each failure adds a reason; a row can have several):
  - `case_number` matches `config/app.yaml: case_number_regex` (default `^\d{2}[A-Z]\d{2}-\d{4}-EV-\d{6}$`)
  - `case_type == "EV"`
  - required fields present; dates parse; `latest_event` is in the enum
  - `defendant_count` equals the number of names
  - amount is numeric and ≥ 0
  - no duplicate `case_number` within the file
- **Upsert**: the key is `case_number`. If the normalized row hash is unchanged, nothing is written (not even `updated_at`). If it changed, the changed fields go to `feed_changes`, the working status is re-derived (§6) and the case is rescored. So importing the same file twice gives "0 inserted, 0 updated, N unchanged".
- **Synthetic court IDs** deliberately use non-existent Indiana county numbers (e.g. `97K01`, `98K02`). No synthetic case number can then collide with a real one.

---

## 6. Working status and reconciliation

**Precedence: the official feed always wins.**

1. The feed shows a final outcome:
   - `agreed_judgment` → **Settled**. The subtype (pay-and-stay / move-out) comes from `disposition_type` via `disposition_map`.
   - `dismissed` with a settlement-type `disposition_type` (e.g. dismissed on payment) → **Settled (dismissed on payment)**.
   - `dismissed` with an unknown or other reason → **Closed (dismissed)** (Q B3).
   - `default_judgment`, `judgment_after_trial` or `writ_issued` → **No settlement**.
2. The feed shows `diversion_stay` → **In diversion**. `stay_start = latest_event_date` (or the export date if it's missing). The 30-day and 60-day status checks and the 90-day end date are shown, counted in calendar days by default (Q B4).
3. Otherwise the status comes from tool activity: **New** (imported) → **Invited** (staff marks the letter sent, or logs a contact) → **Partial response** → **Both responded** (a landlord standing policy counts as the landlord responding). Staff can set **Referred**, and **No settlement** (e.g. mediation failed while the case is still open).

Settled, No settlement and Closed are terminal and drop off the call list. A Settled status is only ever set from the feed.

**Reconciliation** (written per import, shown on the import report):
- the feed overrode a staff status (e.g. *Referred* → feed `dismissed`)
- "diversion agreed" was marked in the tool but no `diversion_stay` appeared within N days (configurable)
- the official event went backwards (e.g. `dismissed` → `hearing_set`, which may mean a reopened or refiled case)
- the case became restricted

**Restriction:** `is_restricted = confidential_flag OR sealed_flag`, checked on every import (Q B2 asks whether to also restrict on the event alone).

---

## 7. Scoring

### 7.1 Layer 1: base score (the as-of rule)

For case *i* filed on date *t* by plaintiff *k*:

| Feature | Formula |
|---|---|
| `landlord_rate` | Empirical-Bayes blend: **(rₖ + m·μₜ) / (nₖ + m)**. Here nₖ is plaintiff *k*'s cases with `disposition_date < t`, rₖ is how many of those resolved, μₜ is the resolution rate of all cases closed before *t*, and m is `landlord_prior_strength` (default 10). |
| `prior_filings_12m` | Count of other cases with `filing_date ∈ [t − 365 d, t)` that share a normalized defendant name **or** a normalized address (street + unit + ZIP). Capped at 5. |
| `plaintiff_attorney` | 0/1 |
| `defendant_count` | Integer, capped at 4 |
| `log_amount`, `amount_unknown` | If the amount is missing or ≥ the cap (`claim_cap: 10000`), then `amount_unknown = 1` and `log_amount` = the training median. Otherwise `log_amount = ln(1 + amount)`. |
| `court_*` | One-hot over `court_id`; the most common court is the reference; an unseen court gives all zeros |
| `days_to_hearing` | `first_hearing_date − filing_date` in days, clipped to [0, 120]. The *first* hearing is used, so later continuances don't leak into the feature. |

Each feature is standardized, **zⱼ = (xⱼ − meanⱼ) / sdⱼ**, using training-set statistics. Then **p_base = σ(β₀ + Σ βⱼ zⱼ)**, fit with scikit-learn `LogisticRegression(L2, C=1)`.

Coefficients, means, SDs, the imputation median, court levels, thresholds and validation metrics are saved to `data/model/model.json`. The per-feature contribution is **cⱼ = βⱼ·zⱼ**, its effect relative to an average case, and these contributions drive the reasons. The court dummies are summed into one "court" reason.

**Label (Q A1):** a case counts as *resolved* (positive) if its disposition is in `resolved_dispositions`. The default set is `agreed_judgment`, `dismissed`, and a diversion stay that ended in either of those.

### 7.2 Layer 2: answer adjustments

`logit_final = logit(p_base) + Σ aᵢ`, where every aᵢ is a weight in `config/scoring.yaml` in logit units. The defaults below are starting points for the team to tune.

**Overlap check.** Definitions:
- A30 = what the tenant can pay within 30 days; M = what they can pay per month after that.
- N = the landlord's maximum plan months (0 if the landlord doesn't accept a plan).
- L = the landlord's minimum to dismiss. If missing, use the policy % × amount owed; if that's missing too, use the amount owed now.
- Capacity **C = A30 + M·N**; shortfall **S = max(0, L − C)**.

Classes:
- **overlap**: S = 0
- **bridgeable**: S ≤ g·L (`bridgeable_gap_pct`, default 0.15), **or** S ≤ the known pending-assistance amount, **or** S ≤ waivable fees (`fees_included`, if the landlord will waive them)
- **wide**: anything else
- **unknown**: a money answer is missing

When there are several tenants, the household capacity is the **max** across tenants. Summing would double-count when each tenant reports household money (Q C2).

```yaml
# config/scoring.yaml (excerpt — defaults, tunable)
overlap:   {overlap: 0.8, bridgeable: 0.3, wide: -0.6, unknown: 0.0}
goals:     {aligned: 0.5, partial: 0.1, conflict: -0.6, unknown: 0.0}
mediation: {both_willing: 1.2, one_willing: 0.3, one_refuses: -0.5}
assistance_pending: 0.3
fee_waiver_offered: 0.2
moveout_extension_matches_tenant: 0.3
legal_issue: 0.0          # flag only, no weight on the probability
bridgeable_gap_pct: 0.15
```

- **Goals:** tenant stay + landlord rent → aligned; tenant move + landlord possession → aligned; tenant move + landlord rent → partial; tenant stay + landlord possession → conflict; landlord "both" → partial.
- **Both willing to mediate (protection rule):** the wide-gap weight is **not** applied to the probability, only to confidence, and **p_final = max(p_final, t_H)**. A case where both sides want to resolve can never drop below the referral threshold (Q A3).
- **Legal-issue flag:** set by repairs/habitability, retaliation, a notice dispute or a payment dispute. It shows a "Legal-aid referral" badge next to the result and never hides the score.

### 7.3 Outputs

- **Potential:**
  - If no tenant has answered **and** there is no landlord answer or policy → **"No response — needs outreach"** (never Low). The base tier is still shown in smaller text for call ordering.
  - Otherwise **High** if p_final ≥ t_H, **Medium** if t_L ≤ p_final < t_H, else **Low**.
- **Thresholds:**
  - t_H is the highest threshold that keeps **recall ≥ 0.95** on the time-based validation set.
  - t_L is the highest threshold that keeps High+Medium recall ≥ 0.99 (`medium_recall_target`, Q A3).
  - **Heads-up:** with roughly half of cases resolving, recall ≥ 0.95 makes **High a large tier** (likely 70–85 % of cases). The cost shows up as the false positive rate, which is exactly what the learning curve tracks. This follows from the brief; I mention it so nobody is surprised in the demo.
- **Pathway** (first match wins):
  1. No response from either side → *Needs outreach*
  2. Legal issue flagged **and** (wide gap, goal conflict or unknown) → *Legal-aid referral*
  3. Tenant would move, or landlord wants possession and offers a move-out extension → *Agreed move-out*
  4. Tenant wants to stay, landlord wants rent or both, and A30 ≥ L → *Dismissal on payment*
  5. Tenant wants to stay, landlord accepts a plan, and the gap is overlap or bridgeable → *Pay-and-stay*
  6. Only one side has answered: infer from that side (stay → Pay-and-stay; move or possession → Agreed move-out)
  7. Otherwise → *Needs outreach*
- **Confidence** (points from `scoring.yaml`):
  - both responded +2, one side +1
  - landlord has ≥ 20 prior closed cases +1
  - gap: overlap +1, bridgeable 0, wide −1
  - |p_final − t_H| ≥ 0.15 → +1
  - tenants disagree (multiple tenants) −1
  - Totals: ≥ 4 → **High**, 2–3 → **Medium**, ≤ 1 → **Low**
- **Reasons:**
  - Rank all base contributions cⱼ and all Layer 2 aᵢ by absolute size; take the top 4, and at least 2 (adding the landlord-rate reason if needed).
  - Each fills a plain-language template stating its direction, e.g. *"Raises: this landlord resolved 64 % of past cases without a judgment (court average 51 %)."* or *"Lowers: the claim lists the $10,000 small-claims cap, so the amount actually owed is unknown."*
  - The templates live in `scoring.yaml` so the team can edit the wording.
- **When scores are recomputed:** on import, on every intake submit, on a policy change, and when the model is retrained.

---

## 8. Synthetic data generator (`make seed`)

- **Volumes:**
  - about 6,000 closed cases over 3 years ending at the anchor date, plus about 300 open live-docket cases
  - about 4 courts and about 400 plaintiffs, with Zipf volumes so a few large repeat filers dominate
  - Faker names, addresses and phones (phones from the reserved 555-01xx range)
- **Latent model:**
  - `logit pᵢ = α + landlord effect (by type: payment-seeker +, possession-seeker −, mixed ~0, plus noise) + attorney + defendant count + amount effect (higher owed → lower) + court effect + days-to-hearing + prior-filings effect + noise`
  - α is calibrated so the overall resolution share matches `synthetic.yaml`
  - yᵢ ~ Bernoulli(pᵢ); then **label noise** flips the recorded outcome with probability ε (default 0.05)
  - the disposition category is then drawn within the resolved or unresolved group using the configured mix
- **Outcome mix:** `synthetic.yaml` holds **clearly labeled `PLACEHOLDER` shares** (e.g. default judgment 0.40, dismissed 0.30, agreed judgment 0.15, judgment after trial 0.08, diversion → dismissed or agreed 0.07; writ issued after some possession judgments). The team will replace these with Indiana's published Methods of Disposition shares.
- **$10,000 placeholder:** about 25 % of claims (placeholder share) list the cap instead of the real amount.
- **Timing:** service 3–10 days after filing; first hearing about 20 days after service; dismissals and agreements spread before and at the hearing; some continuances.
- **Intake answers for a subset of the live docket:**
  - placeholder response rates: tenant 35 %, landlord 50 %; about 30 % of large filers have a standing policy
  - answers correlate with the latent pᵢ, plus noise
  - completion times are drawn from a log-normal (median about 3.5 min) and flagged `is_synthetic`
- **Edge cases** (each gets at least one deliberate instance, listed in `data/synthetic/edge_cases.json`):
  - several tenants on one case
  - a continuance
  - a dismissal before the hearing
  - a case sealed partway through
  - a refiled case (same plaintiff, defendant and address after a dismissal)
  - an unmatched fallback submission
  - a reused token (`revision = 2`)
- **`day1.csv` / `day2.csv`:**
  - day 1 has about 25 new filings plus the current state of some open cases
  - day 2 updates day-1 cases with a continuance, a dismissal, a diversion stay and a newly sealed case, plus a few new filings
- **Determinism:** dates are generated relative to an anchor date (default: today). `APP_TODAY` can freeze the app clock for reproducible demos and tests. Faker and numpy seeds are fixed, so the same seed and anchor give the same data.
- **Pipeline:**
  1. generate the CSVs
  2. import them **through the real importer**, so they're validated
  3. train the production model on closed history (newest 20 % held out to set t_H and t_L) and write `model.json`
  4. create parties, tokens and seeded intakes, and apply policies
  5. score the live docket
  6. create the demo users

---

## 9. Evaluation design (`eval/`, outputs to `reports/`)

Every report opens with the required disclaimer: *"Results use synthetic data. They show the system works as designed and that the learning mechanism behaves as intended. They do not show real-world accuracy."*

- **`eval-model`:**
  - Time split by filing date: oldest 60 % train, next 20 % validation, newest 20 % test. Only cases with `disposition_date` before each split boundary are eligible, so labels respect lag.
  - t_H is chosen on validation for recall ≥ 0.95.
  - Reported on test: AUC (with ROC PNG), a calibration plot (10 bins) with the Brier score, and a staircase table (High/Medium/Low and deciles: n, share, mean predicted, actual resolution rate).
  - Also at t_H: recall, FPR and precision, with **bootstrap 95 % CIs** (1,000 resamples, fixed seed; the threshold stays fixed, so the CIs reflect test-sample uncertainty).
  - A sanity row gives AUC against the noise-free ground truth.
- **`eval-learning`:**
  - Simulates a court launching the tool with only `warm_start_weeks` (default 6) of closed cases, then a 12-week live feed with weekly retraining on every case closed so far. Labels are usable only after `disposition_date`, and landlord-rate features see only data available at that time.
  - A **fixed holdout** (cases filed after the simulation window, never trained on) is split once into a tuning half A, where t_H is re-tuned each week to hold recall ≥ 0.95, and an evaluation half B, where metrics are reported. Splitting avoids tuning and scoring on the same rows.
  - A champion–challenger log (`reports/learning/champion_log.csv`) records each week's version. The challenger replaces the champion only if its FPR at recall ≥ 0.95 on holdout B is lower; ties go to AUC.
  - Charts: FPR against training size with bootstrap CIs, plus an extended curve from random subsamples of the full history (n = 100 … 4,000) showing what more data buys.
  - **Exploration:** each week a random 7.5 % (configurable, 5–10 %) of below-t_H cases get outreach anyway. Missed settlements are estimated as (resolution rate in the sample) × (number of below-threshold cases), with a Wilson CI, and checked against the true number from ground truth.
- **`expert-pack`:**
  - 50 scenarios from the live docket, stratified by potential (incl. No response) × pathway.
  - A printable HTML packet with a **blind vignette** (facts and answers, not the tool's score, so raters aren't anchored).
  - A blank `ratings.csv` (`scenario_id, rater_id, likelihood_1_5, notes`) and a separate `key.csv` that is not for raters.
  - To show the compare step working, it also writes `ratings_simulated.csv`, marked **SIMULATED RATERS** everywhere it appears.
- **`expert-compare RATINGS=…`:**
  - Spearman ρ between the tool's p_final and the mean expert rating, with a bootstrap CI.
  - Overlap between the tool's High tier and the experts' top tier (mean ≥ 4): the share of each and the Jaccard index.
  - The 10 largest rank disagreements, each with its vignette summary.
- **Usability:** median, IQR, p90 and % under 5 minutes of `submitted_at − opened_at`, by role and language. Synthetic seeded timings and real test-window submissions are reported **separately**.
- **`summary.md`:** one page covering scenario test results (`make test` writes JUnit XML), recall held ≥ 0.95 (target vs. achieved, with CI), FPR and precision at the threshold, AUC, the learning curve (first vs. last FPR, champion changes), estimated missed settlements, expert agreement and median intake time.

---

## 10. Dashboard, roles and permissions

| Capability | clerk | facilitator | judge | admin |
|---|:-:|:-:|:-:|:-:|
| Queue + case pages (unrestricted cases) | ✓ | ✓ | ✓ (read-only) | ✓ |
| Restricted cases (with "Restricted" badge) | hidden | ✓ | hidden | ✓ |
| Both parties' answers + score reasons | ✓ | ✓ | ✓ | ✓ |
| Call log, next action, status actions (Invited / Referred / No settlement) | ✓ | ✓ | – | ✓ |
| "Both agreed to diversion" checklist + draft order | ✓ | ✓ | – | ✓ |
| Import feed files, view import / reconciliation reports | ✓ | – | – | ✓ |
| Fallback review queue | ✓ | ✓ | – | ✓ |
| Landlord policies, users, audit log, reports page | – | – | – | ✓ |

- **Hiding** is enforced in the query layer, not just the templates: for a clerk or judge, a restricted case doesn't appear in lists **and** its direct URL returns 404.
- **Audit:** every case view writes an `audit_log` row with who, when, role and whether the case was restricted.
- **Queue:**
  - Columns are as in the brief.
  - Default sort is days to hearing ascending, then potential, then p_final. Past-due hearings are marked.
  - HTMX filters cover working status, potential and pathway. Terminal cases are hidden unless "show closed" is ticked.
- **Diversion checklist:** the items and the draft order text come from `config/diversion_checklist.yaml`, filled with the case number and the 30-, 60- and 90-day dates, and shown in copy boxes.
  - Nothing is sent anywhere. Pressing the button records `diversion_agreed_at` and an audit entry. The status becomes *In diversion* only when the feed shows `diversion_stay`.
  - **The shipped text is a clearly marked PLACEHOLDER (Q B1).**
- **Party links:**
  - Each party gets `secrets.token_urlsafe(16)` (128-bit, never derived from the case number) in a link `{PUBLIC_BASE_URL}/r/{token}`.
  - Staff can view the QR PNG and print a one-page invitation letter (EN/ES) per party.
  - A token stops accepting answers once the case is terminal, and then shows a neutral message.
- **Fallback (`/find`):**
  - A single page asks for role, case number, last name **and** the intake answers together. It always returns the **same** neutral "Thank you — court staff will review this" page, so it reveals nothing, not even whether the case exists.
  - A match attaches the answers as `source=fallback, verified=False`, labeled "Unverified" for staff. No match goes to the review queue.
  - Fallback answers never overwrite answers submitted through a token.
- **Intake pages:**
  - The disclosure ("The judge and court staff can see your answers. The other party cannot.") is the first thing on the page, above question 1.
  - It's a single mobile-first page with an EN/ES toggle. After submitting, the party sees **only their own answers**.
- **Landlord standing policy:** set through the plaintiff's portfolio link `/policy/{token}`. It's auto-applied as a `source=policy` landlord response to every open and new case for that `plaintiff_id`. Case-specific answers win over it.
- **Purge (`make purge`):** for cases terminal for more than `retention_days` (default 90 days, PLACEHOLDER, Q B5):
  - deletes intake answers, fallback answers, call-log notes and party phone and address
  - first rolls the counts up into `retention_aggregates`
  - keeps the case shell, the scores and the audit log, and records the purge in the audit log

---

## 11. Guarantees and how they're enforced

| Rule | Enforcement |
|---|---|
| Synthetic only + banner | Faker only; `base.html` renders the banner on every page (staff, public, packet, letter); a test crawls every GET route for the banner text |
| Advisory only | No code path writes to scheduling or eligibility. The diversion button only shows text. Every case keeps the standard offer (a footer note on the queue) |
| Answer visibility | Public routes only load the party bound to the token; a test checks the other party's values never appear in the HTML |
| Explainable | `scoring.py` asserts 2 ≤ len(reasons) ≤ 4; a test runs it over the whole live docket |
| Offline | HTMX is vendored; no CDN, web fonts or analytics; a test fails on any `http(s)://` in templates or static files outside `PUBLIC_BASE_URL` |
| Ground truth isolation | A test greps `app/` and `core/` for any ground-truth reference |
| Security basics | scrypt password hashes, a signed session cookie (`SECRET_KEY`), SameSite=Lax, CSRF tokens on staff POSTs, tokens with 128 bits of entropy |

---

## 12. Commands

`python manage.py <cmd>` always works; `make <cmd>` wraps it.

| Command | Does |
|---|---|
| `setup` | create `.venv`, `pip install -r requirements.txt` |
| `seed` | rebuild the DB with synthetic data, train the model, write `day1.csv` / `day2.csv` |
| `run` | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| `demo` | `seed` plus every report, so all 7 steps of the README demo work, then print the logins |
| `import FILE=…` | CLI import, the same code as the upload page |
| `test` / `lint` | pytest (+ JUnit XML for the summary) / `ruff check` + `ruff format --check` |
| `eval-model`, `eval-learning`, `expert-pack`, `expert-compare RATINGS=…`, `usability`, `summary`, `eval-all` | §9 |
| `purge` | §10 |

**Docker:** the image is based on `python:3.12-slim`. On first start, the entrypoint runs `demo` if the DB doesn't exist (about a minute), then starts uvicorn. The `data/` and `reports/` folders are volumes.

**Environment variables:** `SECRET_KEY`, `DATABASE_URL` (default `sqlite:///data/app.db`), `PUBLIC_BASE_URL` (what the QR codes encode; set it to your LAN IP so a phone can open the link), `APP_TODAY` (optional frozen date), `SEED_ON_START`.

---

## 13. Dependencies and network calls that need your OK

**Listed stack:** fastapi, sqlalchemy, jinja2, pandas, numpy, scikit-learn, matplotlib, qrcode, faker, pyyaml, pytest, ruff.

**Not explicitly listed but required:** none of these add new functionality, and each is the standard companion of a listed package.

| Package | Why |
|---|---|
| `uvicorn` | the server that runs FastAPI |
| `python-multipart` | FastAPI needs it to parse HTML forms and file uploads |
| `itsdangerous` | Starlette's signed session cookies |
| `httpx` | test only; FastAPI's `TestClient` |
| `pillow` | `qrcode[pil]`, to write PNGs |
| `scipy` | already a scikit-learn dependency; used for Spearman |

**Network calls (all at setup or build time; none at runtime):**
1. `pip install` from PyPI
2. a one-time download of **htmx 2.0.x** (`htmx.min.js`, about 50 KB) to vendor into `app/static/js/`
3. Docker pulling `python:3.12-slim`

---

## 14. Open questions

### A. Please decide before Milestone 1 (a default is proposed for each)

1. **Positive label.** Which dispositions count as "resolved / settled"? Default: `agreed_judgment`, any `dismissed`, and a diversion stay ending in either. Should a dismissal for failure to prosecute count? The feed may not distinguish it.
2. **CSV additions.** May I add the optional columns `latest_event_date` (needed for accurate 30/60-day dates), `plaintiff_name` (synthetic, for display and landlord fallback) and `export_date`? Without them I'd use the import date as a stand-in.
3. **Tiers.**
   - Is "referral threshold" = the High threshold t_H?
   - Is Medium/Low split at the 0.99 cumulative-recall point OK?
   - Is "both willing to mediate → at least High" the right reading of the protection rule?
4. **Git.** May I `git init` a new repo in this project folder (keeping it out of the home-folder repo)?
5. **Docker.** Docker isn't installed here, so I can't verify `docker compose up`. Options: (a) you install Docker Desktop and I verify, (b) a teammate verifies on their machine, (c) I verify everything else and mark that item "untested". Which?
6. **Dependencies and network.** OK to install the six packages in §13 and make the three setup-time network calls (incl. the htmx download)? If the htmx download is refused, I'll build with plain forms and about 30 lines of vanilla JS instead.

### B. Legal / court procedure — I will **not** guess. Placeholders ship until you confirm.

1. **Odyssey checklist and draft order.** What entries must staff make in Odyssey after a diversion agreement, and what's the wording of the case management order? Until then I'll ship generic placeholders marked "DRAFT — confirm with court", for example: record agreement event, enter stay, set 30-day check, set 60-day check, confirm confidentiality.
2. **When to restrict.** Restrict only when the feed's `confidential_flag` / `sealed_flag` is set (per the brief), or also as soon as `latest_event` is `diversion_stay` or `dismissed`, before the flag arrives? Default: flags only. The conservative option is one config switch.
3. **Dismissal → Settled vs. Closed.** Default: a dismissal with a settlement-type disposition is **Settled**; a dismissal with an unknown reason is **Closed (dismissed)**. Both leave the call list. What `disposition_type` values does the real export use?
4. **30/60/90-day counting.** Calendar days, or court days with weekend/holiday roll-forward? Default: calendar days.
5. **Retention.** How many days after closure should intake details be purged? Default placeholder: 90.
6. **Sealed and confidential data in training.** May sealed or confidential cases be used **in aggregate** to train the model and compute landlord rates, while never being shown? Default: yes, aggregate only. Excluding them would remove most dismissals and bias the model.
7. **Retaliation question.** The legal-issue flag mentions retaliation, but the tenant intake list doesn't ask about it. May I add one checkbox ("I think the case was filed because I complained or asked for repairs")?

### C. Defaults I'll use unless you object

1. The landlord fallback (without `plaintiff_name`) always goes to the review queue.
2. Several tenants: capacity = max across tenants; mediation = willing if any tenant is willing; disagreeing goals lower confidence.
3. The standing policy stores the "minimum to dismiss" as a **% of amount owed**. The landlord intake gets an optional "fees included in amount owed ($)" field so fee waivers can bridge gaps.
4. Pending assistance with an unknown amount counts as *bridgeable*, with confidence lowered by one point.
5. "No response — needs outreach" applies whenever neither side has answered, including right after import, with the base tier shown as a sub-label.
6. Spanish text is drafted by me and marked for review by a qualified translator before any testing with people.
7. Tokens are stored in plaintext in the prototype DB so letters can be reprinted. The production note says to hash them and print at issuance.
8. The app's exploration sample is **not** added to the live queue; it's simulated only in `eval-learning`. It's listed in next steps for the silent pilot.

---

## 15. Implementation notes (after the build)

**Decisions applied:** every default in §14 (A1–A6, B1–B7, C1–C8). For A5, the Docker files are
written but could not be run on the build machine, which has no Docker.

**Changes from the plan:**

1. **A fourth optional export column, `first_hearing_datetime`.** Closed-history exports show
   only the latest hearing, so continuances leaked into "days to first hearing" (an as-of
   violation). The importer uses the column when present; otherwise it keeps the first
   hearing it has seen.
2. **Stronger placeholder landlord effects in `config/synthetic.yaml`.** This gives the model
   a learnable signal: a perfect learner reaches AUC ≈ 0.79. These are still placeholders.
3. **Import buttons for the server's own `data/demo/*.csv`.** Inside Docker the seed
   regenerates those files for the current date, so uploading a host copy could mismatch the
   database.
4. **Learning evaluation changes.**
   - The holdout covers 52 weeks.
   - The exploration sample uses a threshold tuned on the cases closed so far, as a live system
     would.
   - The report states plainly that week-to-week changes are mostly within their confidence
     intervals at this docket size.
5. **Wider reconciliation flags.** Flags also fire when the feed decides a case staff were
   actively working (Partial response / Both responded), not only Referred.
6. **Environment on the build machine.**
   - Windows' long-path limit and Smart App Control blocked compiled wheels in a venv inside
     the project folder.
   - The venv was therefore built on Anaconda's Python 3.13 at a short path.
   - The Docker image uses Python 3.12 with `requirements.lock.txt`.
