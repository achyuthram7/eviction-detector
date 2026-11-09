"""Fixed vocabularies shared across the app."""

from __future__ import annotations

# Official events in the (simulated) Odyssey feed.
EVENTS = (
    "filed",
    "served",
    "hearing_set",
    "continued",
    "diversion_advised",
    "diversion_stay",
    "agreed_judgment",
    "dismissed",
    "default_judgment",
    "judgment_after_trial",
    "writ_issued",
)
TERMINAL_EVENTS = frozenset(
    {"agreed_judgment", "dismissed", "default_judgment", "judgment_after_trial", "writ_issued"}
)

# Working statuses (the tool's own status; official feed events override them).
NEW = "New"
INVITED = "Invited"
PARTIAL = "Partial response"
BOTH = "Both responded"
REFERRED = "Referred"
IN_DIVERSION = "In diversion"
SETTLED = "Settled"
NO_SETTLEMENT = "No settlement"
CLOSED = "Closed"
STATUSES = (NEW, INVITED, PARTIAL, BOTH, REFERRED, IN_DIVERSION, SETTLED, NO_SETTLEMENT, CLOSED)
TERMINAL_STATUSES = frozenset({SETTLED, NO_SETTLEMENT, CLOSED})
STAFF_SETTABLE = (INVITED, REFERRED, NO_SETTLEMENT)

# Roles.
CLERK = "clerk"
FACILITATOR = "facilitator"
JUDGE = "judge"
ADMIN = "admin"
ROLES = (CLERK, FACILITATOR, JUDGE, ADMIN)
SEES_RESTRICTED = frozenset({FACILITATOR, ADMIN})

# Settlement potential.
HIGH = "High"
MEDIUM = "Medium"
LOW = "Low"
NO_RESPONSE = "No response — needs outreach"
POTENTIALS = (HIGH, MEDIUM, LOW, NO_RESPONSE)

# Pathways.
PAY_AND_STAY = "Pay-and-stay"
DISMISSAL_ON_PAYMENT = "Dismissal on payment"
AGREED_MOVE_OUT = "Agreed move-out"
LEGAL_AID = "Legal-aid referral"
NEEDS_OUTREACH = "Needs outreach"
PATHWAYS = (PAY_AND_STAY, DISMISSAL_ON_PAYMENT, AGREED_MOVE_OUT, LEGAL_AID, NEEDS_OUTREACH)

TENANT = "tenant"
LANDLORD = "landlord"

BANNER_TEXT = "Prototype — synthetic data only. Not legal advice."
DISCLOSURE_TEXT = "The judge and court staff can see your answers. The other party cannot."
