# Demo script (5 minutes)

> Prototype — synthetic data only. Not legal advice.

## Before you start

- Run `make demo`, or `docker compose up` and wait for "Uvicorn running".
- To scan QR codes with a phone, set `PUBLIC_BASE_URL=http://<this-computer's-LAN-IP>:8000` before
  seeding/starting, and make sure the phone is on the same Wi-Fi.
- Open `data/demo/demo_cases.json` for the case numbers used below (they change with each seed).
- Use two browser windows: one signed in as `clerk`, one as `facilitator`.

| Step | What to do | What to point out |
|---|---|---|
| 0 | Show the yellow banner on any page. | Synthetic data only; advisory only; the tool never writes to Odyssey. |
| 1 | **clerk** → *Import feed* → **Import day1.csv** | Inserted / updated / **unchanged** counts. Importing the same file again changes nothing. Three rejected rows with reasons: bad case-number pattern, case type `SC` instead of `EV`, defendant count mismatch. |
| 2 | Queue → search `day1_new_filing_for_qr_demo` → open it → scan the tenant QR code (or open the link). On the phone, toggle *Español*, then back. Answer, tick "Repairs", submit. | The disclosure is the first thing on the page: *"The judge and court staff can see your answers. The other party cannot."* About 3 minutes. The tenant sees only their own answers afterwards and can update them through the same link (no duplicate). |
| 3 | Back in the clerk's queue, search the same case. | Tenant "Yes", potential and pathway, the **Legal-aid referral** flag beside the result (never instead of it), consent "Text", next action "Contact landlord". Open the case: 2–4 plain-language reasons. |
| 4 | **clerk** → **Import day2.csv** | The continuance (new hearing date; the queue re-sorts), the dismissal (case leaves the call list), the diversion stay, the newly sealed case. Reconciliation table: cases staff were working that the feed has now decided. |
| 5 | **facilitator** → open `diversion_agreement_candidate` → **Both agreed to diversion** | The Odyssey checklist and draft order with the 30- and 60-day dates, both marked DRAFT. Nothing is sent; the status becomes *In diversion* only when the feed shows the stay. Then search `day2_diversion_stay`: *In diversion*, check dates, **Restricted** badge. |
| 6 | **clerk** → search `day2_newly_sealed` (tick *Show closed*) | Gone, and its URL returns 404. In the facilitator window it is still there with a Restricted badge; opening it appears in **admin → Audit log** (restricted views filter). |
| 7 | Open `reports/summary.md` (or **admin → Reports**) | Recall held ≥ 0.95 on newer cases, the FPR learning curve and champion–challenger, expert agreement (simulated raters until real ones are added), median intake time. Every report carries the synthetic-data disclaimer. |

## If something goes wrong

- **"Not scored yet":** no model exists. Run `make seed`.
- **Demo case numbers don't match:** the CSVs on the import page are generated with the database.
  Use the **Import day1.csv / day2.csv** buttons rather than uploading old copies.
- **To restart cleanly:** run `make demo` (local) or `docker compose down -v && docker compose up`.
