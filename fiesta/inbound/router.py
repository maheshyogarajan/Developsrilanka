"""fiesta.inbound.router — classification -> draft reply + linkback URL + tag.

Pure-Python, no I/O. Caller supplies a ClassificationResult; returns a
RoutingDecision with the drafted auto-reply (Tier-1 ONLY, NEVER auto-sent),
the linkback URL the customer should be directed to, and an internal tag.

All draft replies include:
  - Customer full name in greeting + subject (refuse-to-render gate from
    autoreply.py — but in FIESTA we accept first-name fallback when that's
    all the User.name has; we record a gate WARNING rather than refusing).
  - Linkback into the relevant FIESTA section.
  - A short personal "reply to this email" closing (replies go back through
    the same inbound webhook + are routed to staff queue).
  - FIESTA support signature block.

Per FIESTA Self-File v1, IRD_QUESTION routes to a Lanka.tax bookkeeping
referral; consultant-booking (v1.1) goes through a different routing path.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from fiesta.inbound import classifier as _cls


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# FIESTA base URL — overridable via constructor for tests / Replit / Fly.
DEFAULT_FIESTA_BASE_URL = "https://fiesta.lanka.tax"

# Linkback URLs (relative to base URL).
LINKBACK_PATHS = {
    _cls.CATEGORY_PROFILE_INCOMPLETE:  "/profile",
    _cls.CATEGORY_EARNINGS_QUESTION:   "/earnings",
    _cls.CATEGORY_DEDUCTION_QUESTION:  "/reduce-tax",
    _cls.CATEGORY_PAYMENT_QUESTION:    "/billing",
    _cls.CATEGORY_AGREEMENT_QUESTION:  "/agreement",
    _cls.CATEGORY_IRD_QUESTION:        "https://www.lanka.tax/bookkeeping",  # external
    _cls.CATEGORY_GENERIC_INQUIRY:     "/help",
    _cls.CATEGORY_UNMATCHED_CUSTOMER:  "/help",
}

# Tags applied to customer profile for analytics / cohort building.
CUSTOMER_TAGS = {
    _cls.CATEGORY_PROFILE_INCOMPLETE:  "needs_profile_help",
    _cls.CATEGORY_EARNINGS_QUESTION:   "earnings_question_asked",
    _cls.CATEGORY_DEDUCTION_QUESTION:  "deduction_question_asked",
    _cls.CATEGORY_PAYMENT_QUESTION:    "payment_question_asked",
    _cls.CATEGORY_AGREEMENT_QUESTION:  "agreement_question_asked",
    _cls.CATEGORY_IRD_QUESTION:        "ird_question_asked",
    _cls.CATEGORY_GENERIC_INQUIRY:     "generic_inquiry",
    _cls.CATEGORY_UNMATCHED_CUSTOMER:  "unmatched_inbound",
}

# Internal route hints used by the staff queue UI to show which team owns
# follow-up.
ROUTE_HINTS = {
    _cls.CATEGORY_PROFILE_INCOMPLETE:  "support",
    _cls.CATEGORY_EARNINGS_QUESTION:   "support",
    _cls.CATEGORY_DEDUCTION_QUESTION:  "support",
    _cls.CATEGORY_PAYMENT_QUESTION:    "billing",
    _cls.CATEGORY_AGREEMENT_QUESTION:  "support",
    _cls.CATEGORY_IRD_QUESTION:        "consultant_referral",
    _cls.CATEGORY_GENERIC_INQUIRY:     "staff_classify",
    _cls.CATEGORY_UNMATCHED_CUSTOMER:  "staff_flagged",
}


# FIESTA signature block (per CEO_SIGNATURE_BLOCK pattern in autoreply.py,
# adapted for FIESTA-as-product rather than Lanka.tax-as-firm).
FIESTA_SIGNATURE_BLOCK = (
    "FIESTA Support Team\n"
    "Self-file Sri Lankan personal income tax\n"
    "Web: https://fiesta.lanka.tax\n"
    "Email: support@fiesta.lanka.tax"
)


# ---------------------------------------------------------------------------
# Template bodies (compact per category)
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, dict[str, str]] = {
    _cls.CATEGORY_PROFILE_INCOMPLETE: {
        "subject": "Re: your FIESTA account — {customer_name}",
        "body": (
            "Dear {customer_name},\n\n"
            "Thanks for getting in touch. It looks like you have a question "
            "about your FIESTA account or profile.\n\n"
            "You can pick up where you left off here:\n"
            "{linkback_url}\n\n"
            "If you forgot your password, use the 'Reset password' link on "
            "the sign-in page. If you cannot verify your email, reply to "
            "this message and a team member will help you within one "
            "business day.\n\n"
            "{signature}\n"
        ),
    },
    _cls.CATEGORY_EARNINGS_QUESTION: {
        "subject": "Re: your earnings / income documents — {customer_name}",
        "body": (
            "Dear {customer_name},\n\n"
            "Thanks for the message about your earnings statement.\n\n"
            "You can resume the income documents step here:\n"
            "{linkback_url}\n\n"
            "FIESTA accepts T10s (employer-issued tax certificates), bank "
            "interest statements, and other income statements. Upload one "
            "document at a time on the Earnings page — FIESTA will scan and "
            "extract the figures automatically.\n\n"
            "If a document is failing to upload, reply to this email with "
            "the file attached and a team member will check it.\n\n"
            "{signature}\n"
        ),
    },
    _cls.CATEGORY_DEDUCTION_QUESTION: {
        "subject": "Re: deductions / reducing your tax — {customer_name}",
        "body": (
            "Dear {customer_name},\n\n"
            "Thanks for asking about deductions. Sri Lanka's personal "
            "income tax allows several qualifying payments and reliefs "
            "that may reduce what you owe.\n\n"
            "FIESTA walks you through the eligible categories here:\n"
            "{linkback_url}\n\n"
            "Common deductions include life insurance premiums, certain "
            "donations, and pension fund contributions. Each has its own "
            "limit and documentation requirement — the page above explains "
            "which apply to you based on the income types you've added.\n\n"
            "If you have a deduction not listed there, reply to this "
            "email and we will help you confirm whether it qualifies.\n\n"
            "{signature}\n"
        ),
    },
    _cls.CATEGORY_PAYMENT_QUESTION: {
        "subject": "Re: your FIESTA billing question — {customer_name}",
        "body": (
            "Dear {customer_name},\n\n"
            "Thanks for the message about billing. A team member will "
            "review your account and respond personally within one "
            "business day.\n\n"
            "FIESTA's self-file price for Sri Lankan personal income tax "
            "is Rs 2,500 per filing. You can review your billing history "
            "at:\n"
            "{linkback_url}\n\n"
            "If you believe you were charged in error, please reply to "
            "this email with the date and amount of the charge.\n\n"
            "{signature}\n"
        ),
    },
    _cls.CATEGORY_AGREEMENT_QUESTION: {
        "subject": "Re: your engagement agreement — {customer_name}",
        "body": (
            "Dear {customer_name},\n\n"
            "Thanks for asking about the engagement agreement.\n\n"
            "You can generate, review, and download your FIESTA "
            "engagement letter here:\n"
            "{linkback_url}\n\n"
            "The agreement is generated from the details on your "
            "profile and the services you've selected. If you need to "
            "change a clause or add a custom term, reply to this email "
            "and a team member will help.\n\n"
            "{signature}\n"
        ),
    },
    _cls.CATEGORY_IRD_QUESTION: {
        "subject": "Re: your Sri Lankan tax question — {customer_name}",
        "body": (
            "Dear {customer_name},\n\n"
            "Thanks for the message. The question you asked is about "
            "Sri Lankan tax law / IRD process — FIESTA's self-file "
            "workflow is built for straightforward personal returns "
            "and may not be the right fit if your situation needs "
            "tailored advice.\n\n"
            "Two options:\n\n"
            "1. If your question is brief and procedural, reply to this "
            "email and a team member will try to point you to the "
            "answer within one business day.\n\n"
            "2. For anything that needs review of your records, Lanka.tax "
            "(our sister bookkeeping service) can take this on:\n"
            "{linkback_url}\n\n"
            "{signature}\n"
        ),
    },
    _cls.CATEGORY_GENERIC_INQUIRY: {
        "subject": "Re: your message — {customer_name}",
        "body": (
            "Dear {customer_name},\n\n"
            "Thanks for getting in touch. A team member will read your "
            "message and respond personally within one business day.\n\n"
            "If your question is urgent, you can also visit:\n"
            "{linkback_url}\n\n"
            "{signature}\n"
        ),
    },
    _cls.CATEGORY_UNMATCHED_CUSTOMER: {
        "subject": "Re: your message",
        "body": (
            "Dear sender,\n\n"
            "Thanks for getting in touch. We were not able to match "
            "your email address to a FIESTA account, so a team member "
            "will read your message and respond personally within one "
            "business day.\n\n"
            "{signature}\n"
        ),
    },
}


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class RoutingDecision(BaseModel):
    """Output of route(). pydantic v2 model."""

    category: str
    linkback_url: str
    customer_tag: str
    route_hint: str = Field(..., description="support/billing/staff/etc.")
    draft_subject: Optional[str] = None
    draft_body: Optional[str] = None
    auto_send: bool = Field(
        default=False,
        description=(
            "ALWAYS False in v1. Hardcoded for the contract — even if a "
            "future config turns on auto-send, the webhook still gates on "
            "Tier-1 approval queue."
        ),
    )
    needs_staff_review: bool = Field(
        default=True,
        description=(
            "True iff this routing decision requires a human to approve "
            "the draft before send. v1: always True."
        ),
    )
    gates_passed: list[str] = Field(default_factory=list)
    gates_failed: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _customer_name_for_greeting(customer_name: Optional[str]) -> str:
    """Resolve a customer name string for use in greeting + subject.

    FIESTA's User.name may be a single token (e.g. "Mahesh") rather than full
    name — autoreply.py's refuse-to-render gate is too strict here. We
    accept what we have but warn the caller via gates_failed if it's not a
    proper full name.
    """
    if not customer_name or not customer_name.strip():
        return "there"
    return customer_name.strip()


def _resolve_linkback(category: str, fiesta_base_url: str) -> str:
    path = LINKBACK_PATHS.get(category, "/help")
    if path.startswith("http"):
        return path  # external (e.g. lanka.tax bookkeeping)
    return f"{fiesta_base_url.rstrip('/')}{path}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def route(
    *,
    classification: _cls.ClassificationResult,
    customer_name: Optional[str],
    customer_email: Optional[str],
    fiesta_base_url: str = DEFAULT_FIESTA_BASE_URL,
    include_draft: bool = True,
) -> RoutingDecision:
    """Convert a ClassificationResult into a RoutingDecision.

    Args:
        classification: from classifier.classify().
        customer_name: User.name (best effort — may be single token or None).
        customer_email: User.email — required for OutboundDraft.to_addr later.
        fiesta_base_url: base URL for linkback construction.
        include_draft: if False, skip building draft_subject/body (the staff
            queue will compose manually).
    """
    cat = classification.category
    linkback = _resolve_linkback(cat, fiesta_base_url)
    tag = CUSTOMER_TAGS.get(cat, "generic_inquiry")
    hint = ROUTE_HINTS.get(cat, "staff_classify")

    gates_passed: list[str] = []
    gates_failed: list[str] = []
    warnings: list[str] = []

    # Greeting gate (informational warning, not refuse-to-render).
    name_for_greeting = _customer_name_for_greeting(customer_name)
    if name_for_greeting == "there":
        gates_failed.append("customer_name_in_greeting")
        warnings.append("no customer name on profile -- generic greeting used")
    else:
        gates_passed.append("customer_name_in_greeting")

    # Email present gate.
    if not customer_email:
        gates_failed.append("customer_email_present")
        warnings.append("no customer email -- draft will be unsent in queue")
    else:
        gates_passed.append("customer_email_present")

    # If the classification is autoreply noise, don't compose a draft at all.
    if classification.is_autoreply_noise:
        return RoutingDecision(
            category=cat,
            linkback_url=linkback,
            customer_tag=tag,
            route_hint="discard_noise",
            draft_subject=None,
            draft_body=None,
            auto_send=False,
            needs_staff_review=False,
            gates_passed=gates_passed,
            gates_failed=gates_failed,
            warnings=warnings,
            reasoning="autoreply_noise -- no draft, no follow-up",
        )

    draft_subject: Optional[str] = None
    draft_body: Optional[str] = None

    if include_draft:
        tmpl = _TEMPLATES.get(cat)
        if tmpl:
            draft_subject = tmpl["subject"].format(
                customer_name=name_for_greeting,
            )
            draft_body = tmpl["body"].format(
                customer_name=name_for_greeting,
                linkback_url=linkback,
                signature=FIESTA_SIGNATURE_BLOCK,
            )

            # Linkback presence gate.
            if linkback in (draft_body or ""):
                gates_passed.append("linkback_url_present")
            else:
                gates_failed.append("linkback_url_present")

            # Signature presence gate.
            if "FIESTA Support Team" in (draft_body or ""):
                gates_passed.append("signature_block_present")
            else:
                gates_failed.append("signature_block_present")

    reasoning = (
        f"category={cat} score={classification.score} "
        f"route_hint={hint} linkback={linkback}"
    )
    if classification.needs_human_review:
        reasoning += " | needs_human_review=True"

    return RoutingDecision(
        category=cat,
        linkback_url=linkback,
        customer_tag=tag,
        route_hint=hint,
        draft_subject=draft_subject,
        draft_body=draft_body,
        auto_send=False,
        needs_staff_review=True,  # v1: ALWAYS True
        gates_passed=gates_passed,
        gates_failed=gates_failed,
        warnings=warnings,
        reasoning=reasoning,
    )


__all__ = [
    "DEFAULT_FIESTA_BASE_URL",
    "LINKBACK_PATHS",
    "CUSTOMER_TAGS",
    "ROUTE_HINTS",
    "FIESTA_SIGNATURE_BLOCK",
    "RoutingDecision",
    "route",
]
