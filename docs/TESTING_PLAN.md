# Testing Plan: Settlement Predictor

Team 14 – Eviction Settlement Opportunity Detector

## 1. Target user group

Tenants and landlords in Indiana eviction cases, mostly without lawyers, who answer the
questionnaire before their first hearing.

Test group (6–8 people, about 20 minutes each):

- 4–5 tenants, ideally renters who have dealt with back rent or an eviction notice
- 2–3 small landlords or property managers who handle their own cases
- At least 2 Spanish speakers, so the English and Spanish versions can be compared
- A mix of ages and comfort with phones
- Stand-ins if needed: students or community members who rent

## 2. Feature to test

The questionnaire tenants and landlords fill in after finding their case. It asks about money
(what's owed, what the tenant can pay, the landlord's minimum and payment plan), goals (stay or
move, rent or the unit back), rental assistance, problems with the home or case, and
willingness to talk with a mediator. Most questions must be answered, and each of those offers
a "Not sure" answer for people who don't know.

Our predictor turns these answers into a settlement score, so it only works if people answer
accurately. We're testing whether they can, whether each question is clear, and whether people
use "Not sure" only when they really don't know.

## 3. What testers need

No ID, petition or court order. In real use, tenants would need their court papers (case
number, link or QR code) and landlords their rent records.

For this test, each tester gets a made-up scenario card with a case number, a last name and the
facts of their situation, so nobody shares real information.

- Tenant cards use a case number and tenant last name that exist in the live demo data, so the
  answers attach to that case.
- Each card leaves out one or two facts on purpose, to test the "Not sure" answer.
- Cards say to leave the optional phone number blank.

Testers need a phone (preferred) or laptop. We provide a consent form and a short survey.

## 4. Step-by-step guide

1. Open eviction-detector.onrender.com on your phone. The first load can take about a minute.
2. Tap "Tenant or landlord without a link." Don't use the court staff sign-in.
3. Spanish speakers: tap "Español" at the top of the page.
4. In "I am the", choose Tenant or Landlord to match your scenario card.
5. Enter the case number and last name from your card.
6. Answer each question as the person on your card.
   - If your card doesn't give the answer, choose "Not sure".
   - Leave questions marked "optional" blank if your card doesn't cover them.
   - Say out loud whenever a question is confusing or you're unsure what to pick.
7. Before you tap "Send my answers", let us take screenshots of the whole form (3–4 screens on a
   phone). Then tap Send. The next page only says thank you: it never shows case details or your
   answers.
8. Fill in the short survey, and tell us in your own words what a few of the questions were
   asking.

## 5. What we're measuring

- **Answer accuracy:** share of answers that match the scenario card, checked question by
  question from the screenshots. "Not sure" counts as correct only where the card leaves the
  fact out. Target: 90% overall. Any question below 80% gets reworded.
- **"Not sure" use:** how often testers pick "Not sure" when their card does give the answer.
  Any question where this happens to 2 or more testers gets reworded.
- **Clarity:** how often testers hesitate, ask for help or say a question is confusing. Target:
  no question confuses 2 or more testers. With 6–8 testers, that's the "1 in 5" limit.
- **Understanding:** testers explain two key questions for their side in their own words
  (tenants: rental assistance and mediation; landlords: minimum to dismiss and payment plan),
  and say who can see their answers. The correct answer is the judge and court staff, but not
  the other party. Target: 80% correct.
- **Completion:** testers finish without help in under 10 minutes, timed with a stopwatch from
  opening the page to tapping Send. Target: 90% of testers. Also report the median time against
  the 5-minute design goal, noting that talking aloud slows people down.
- **Ease:** survey rating for "The questions were easy to understand" (1–5). Target: average of
  4 or higher.

We compare results for the English and Spanish versions. With only 2–3 Spanish speakers, treat
that comparison as a list of problems to fix rather than as statistics.

### Before test day

- **Deploy the final version first.** The role dropdown, required questions and "Not sure" are
  not on the live site until they're deployed.
- **Make the scenario cards after the last deploy, and don't deploy during the testing window.**
  Each deploy rebuilds the demo data, so case numbers and links can change.
- **Rely on the screenshots, not the website, for results.** The free hosting plan forgets
  submitted answers when the site sleeps (after 15 minutes without visitors) or restarts.
- **Only the judge account can sign in at the moment.**
  - The judge can see tenant answers that matched a case, on that case's page, marked
    "Unverified".
  - Landlord answers sent through "Find your case" always go to the staff review queue, and so do
    tenant answers that didn't match. Only clerk, facilitator and admin accounts can open that
    queue.
- **Check whether testing needs approval.** If this is part of a course, ask whether the
  instructor or university needs to approve testing with people.

## 6. Silent pilot: predictor vs. real case outcomes

Goal: measure how accurate the predictor is on real cases, without letting it affect them.

**How it works**

- For a fixed period, the predictor scores every new eviction case from the court's daily export
  in the background.
- Nobody sees the scores, including staff and judges. If people saw them, the scores could
  change how cases end, and the pilot would partly measure its own influence. In the current
  prototype the judge does see scores, so a silent mode has to be added first.
- When each case closes, its band (High, Medium or Low) is compared with how it actually ended.
  An agreed judgment or a dismissal counts as settled. A default judgment or a judgment after
  trial counts as not settled.
- Layer 1, which uses only filing-day court data, can be checked on every case. Layer 2 can only
  be checked on cases where the parties filled in the questionnaire.

**What we measure**

- **Recall at High:** the share of cases that settled that the predictor had placed in High.
  Target: at least 95%. On the newer synthetic demo cases it was 92.4%.
- **False positive rate:** the share of cases that didn't settle but were still placed in High.
- **Calibration:** whether the percentages match reality. For example, of the cases scored
  around 30%, about 30% should settle.
- **Fairness:** the same measures split by court, by language, and by whether the parties
  answered, to check the predictor doesn't miss one group more than others.

**How long**

Run it until about 200 cases that settled have closed. That measures 95% recall to within about
±3 percentage points. The number of months depends on the court's caseload.

**Decision rule, agreed before the pilot starts**

For example, staff start seeing the score only if recall at High is at least 95%, with the lower
end of its confidence range at 90% or above, and no court or group falls below 90%.

**What it needs first**

- This step uses real court data, so it's outside the prototype's synthetic-data-only rule.
- It needs the court's written approval and a data-sharing agreement.
- It needs secure hosting (not the free demo site) and handling for sealed and confidential
  cases.
- It probably needs an ethics (IRB) review.
- The court has to confirm the real export format and outcome codes. The prototype uses
  placeholders, and these are court procedure questions the team should take to the court
  rather than guess.
