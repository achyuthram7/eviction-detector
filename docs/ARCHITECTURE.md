# Architecture

> Prototype — synthetic data only. Not legal advice.

## Shape

One Python process (FastAPI + Uvicorn) serves server-rendered pages (Jinja2, with HTMX for
partial updates) and talks to one SQLite file through SQLAlchemy. There is no front-end build
step, no background worker and no network call at runtime.

```
 daily Odyssey export (CSV)          party phones / browsers           staff browsers
            │                                  │                              │
            ▼                                  ▼                              ▼
   core.importer ──► core.pipeline ◄── app/routes/public.py         app/routes/staff.py, admin.py
   validate, upsert      │  parties & tokens (core.parties)              │ queue, case page, call log,
   idempotent, report    │  standing policy (core.intake)                │ diversion checklist, audit
                         │  working status (core.status)                 │
                         │  reconciliation (core.reconcile)              │
                         │  rescoring (core.scoring_service)             │
                         ▼                                               ▼
                    SQLite (core.models)  ◄──────────────────────── core.queue (restricted filter)
                         ▲
        model.json ──────┘  (core.model: trained by `make seed` / scoring_service)
```

`synth/` (generator, seed) and `eval/` (evaluation) sit beside the app. They reuse `core/`, so the
evaluation exercises exactly the code the app runs. Ground truth lives in
`data/ground_truth/ground_truth.csv`, which only `synth/` and `eval/` know about; a test fails if
`app/` or `core/` ever mention it.

## Main flows

**Import** (`core/importer.py`)
1. Parse the CSV and validate every row. A rejection is recorded with all its reasons.
2. Upsert by `case_number`. A row whose normalized content hash is unchanged is skipped
   entirely (`export_date` is excluded from the hash), which makes a re-import a no-op.
3. For each changed case, `core.pipeline.after_import` runs in order:
   1. ensure parties and tokens
   2. apply the landlord's standing policy
   3. recompute the working status (feed events override)
   4. write reconciliation flags
   5. rescore

**Working status** (`core/status.py`)
- A terminal official event maps through `config/app.yaml: dispositions` to *Settled*,
  *No settlement* or *Closed*.
- `diversion_stay` → *In diversion*, with stay start and 30/60/90-day dates.
- Otherwise the status comes from tool activity: a staff status (Referred / No settlement),
  then responses (*Both responded* / *Partial response*), then *Invited*, else *New*.
- Every change writes `status_history`.

**Intake** (`app/routes/public.py`, `core/intake.py`)
- `GET /r/{token}` records a form-open timing row. The token maps to exactly one party.
- `POST` validates, closes the timing row and upserts the party's single response row
  (`revision` counts resubmissions). It then refreshes status and rescores.
- `/find` (case number + last name) always returns the same neutral page. A tenant match is
  attached *unverified* and never overwrites link answers; anything else goes to the review queue.
- `/policy/{token}` saves the landlord's standing policy and applies it to every open case of that
  plaintiff. New filings pick it up on import.

**Scoring** (`core/features.py`, `core/model.py`, `core/scoring.py`, `core/scoring_service.py`)
- Layer 1: as-of features → standardized logistic regression → `p_base`, with per-feature
  contributions.
- Layer 2: rule weights in logit units from `config/scoring.yaml`, plus the both-willing floor.
  Produces potential, pathway, confidence, legal-aid flag and 2–4 reasons.
- Base results are stored with each score and reused until the model version changes, because
  as-of features never change after filing. Only Layer 2 is recomputed when answers change.

**Restricted cases** (`core/queue.py`)
- `is_restricted = confidential_flag OR sealed_flag` (plus optional events in config).
- Clerk and judge queries exclude restricted cases, and their URLs return 404 (logged as
  `view_denied`). Facilitators and admins see a *Restricted* badge.
- Every case view is written to `audit_log` with a `restricted` flag.

## Data model

See `core/models.py` (one file) and PLAN.md §5. Key tables:

| Area | Tables |
|---|---|
| Cases and feed | `cases`, `plaintiffs`, `parties`, `import_runs`, `import_rejections`, `feed_changes`, `reconciliation_flags` |
| Intake | `tenant_responses`, `landlord_responses` (unique per party), `landlord_policies`, `fallback_submissions`, `intake_timings` |
| Scoring | `case_scores` (history kept; `is_current`) |
| Staff | `users`, `call_log`, `status_history`, `audit_log` |
| Retention | `retention_aggregates` |

## Security and privacy (prototype level)

- scrypt password hashes (stdlib); a signed, `SameSite=Lax` session cookie; CSRF token on every
  staff POST (form field or `X-CSRF-Token` header for HTMX).
- Party tokens: `secrets.token_urlsafe(16)` (128 bits), never derived from the case number.
- Headers: a strict Content-Security-Policy (same origin only), `Referrer-Policy: no-referrer`
  (tokens live in URLs), `X-Frame-Options: DENY`, `Cache-Control: no-store` on pages.
- Answer visibility: public routes load only the token's own party. Tests check that the other
  party's values never appear.
- Retention: `make purge` removes answers, timings, call notes and contact details after the
  configured period, rolling counts into `retention_aggregates` first.

## Testing

`tests/test_scenarios.py` holds one test per section-G row. Other files cover the importer,
status, as-of features (leakage), scoring rules, the dashboard (roles, CSRF, HTMX partials),
retention, guards (banner on every page, no external URLs, ground-truth isolation) and an
end-to-end smoke test of every evaluation command.
