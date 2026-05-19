"""fiesta.inbound.classifier — keyword + precondition inbound classifier.

Pure-Python, no I/O. Maps an inbound email (subject + body + optional
customer context) into one of 7 v1 categories:

    PROFILE_INCOMPLETE   -> route to S3 (profile)
    EARNINGS_QUESTION    -> route to S4 (earnings / income statement upload)
    DEDUCTION_QUESTION   -> route to S5 (reduce tax / deductions) + FAQ
    PAYMENT_QUESTION     -> route to support@ + Tier 1 reply queue
    AGREEMENT_QUESTION   -> route to S8 (agreement generator) + FAQ
    IRD_QUESTION         -> route to consultant booking (v1.1) OR Lanka.tax
                            bookkeeping referral (v1 fallback)
    GENERIC_INQUIRY      -> AI-classify hook + Tier-1 staff draft

Pattern is inherited from fiesta.delivery_ops.autoreply._classify_inbound:
subject hits weighted 3x body hits; preconditions boost the matching category.

Stdlib only; pydantic v2 for output dataclass.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

CATEGORY_PROFILE_INCOMPLETE = "PROFILE_INCOMPLETE"
CATEGORY_EARNINGS_QUESTION = "EARNINGS_QUESTION"
CATEGORY_DEDUCTION_QUESTION = "DEDUCTION_QUESTION"
CATEGORY_PAYMENT_QUESTION = "PAYMENT_QUESTION"
CATEGORY_AGREEMENT_QUESTION = "AGREEMENT_QUESTION"
CATEGORY_IRD_QUESTION = "IRD_QUESTION"
CATEGORY_GENERIC_INQUIRY = "GENERIC_INQUIRY"
CATEGORY_UNMATCHED_CUSTOMER = "UNMATCHED_CUSTOMER"  # flagged for staff

CATEGORIES = (
    CATEGORY_PROFILE_INCOMPLETE,
    CATEGORY_EARNINGS_QUESTION,
    CATEGORY_DEDUCTION_QUESTION,
    CATEGORY_PAYMENT_QUESTION,
    CATEGORY_AGREEMENT_QUESTION,
    CATEGORY_IRD_QUESTION,
    CATEGORY_GENERIC_INQUIRY,
    CATEGORY_UNMATCHED_CUSTOMER,
)


# ---------------------------------------------------------------------------
# Keyword maps (subject hit weight 3, body hit weight 1)
# ---------------------------------------------------------------------------

# Conservative keyword sets — tuned to bias toward GENERIC_INQUIRY rather than
# wrong-category routing. False positives in a wrong category waste staff time.
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    CATEGORY_PROFILE_INCOMPLETE: (
        "profile", "password", "login", "log in", "sign in", "can't log",
        "cannot log", "forgot password", "reset password", "account locked",
        "username", "verify email", "email verification", "verification link",
        "where do i sign", "my account", "complete profile", "fill profile",
    ),
    CATEGORY_EARNINGS_QUESTION: (
        "income statement", "salary slip", "payslip", "earnings",
        "upload statement", "statement upload", "bank statement",
        "epf", "etf", "employer letter", "income document",
        "t10", "pay-slip", "tax statement",
        "where do i upload", "can't upload", "upload failed",
        "income upload", "salary",
    ),
    CATEGORY_DEDUCTION_QUESTION: (
        "deduction", "reduce tax", "claim", "expenses", "tax relief",
        "qualifying payment", "donation", "life insurance",
        "personal relief", "what can i claim", "what deductions",
        "save tax", "reduce my tax", "lower my tax",
    ),
    CATEGORY_PAYMENT_QUESTION: (
        "payment", "paid", "refund", "billing", "charge",
        "rs 2500", "rs 2,500", "rs2500", "2500", "2,500",
        "stripe", "card declined", "invoice", "receipt",
        "how much", "cost", "price", "fee",
        "double charge", "wrong charge", "money back",
    ),
    CATEGORY_AGREEMENT_QUESTION: (
        "agreement", "contract", "engagement letter", "s8", "s9",
        "generator", "agreement generator",
        "scope of work", "sow", "service agreement",
        "engagement scope", "agreement template",
    ),
    CATEGORY_IRD_QUESTION: (
        "ird", "inland revenue", "tax authority",
        "ramis", "tin", "tax identification",
        "withholding tax", "wht ", "vat ", "nbt",
        "tax law", "section ", "income tax act",
        "tax return", "filing the return", "file my return",
        "tax compliance", "tax bracket", "tax rate", "tax slab",
        "non resident", "non-resident", "foreign income tax",
        "residency rule", "183 day", "183-day",
        "lanka.tax bookkeeping",  # referral
    ),
}


# Preconditions: when a customer context flag is set, boost a category.
# These keys must match what router/webhook pass into classify() as
# customer_context. None of them are required — best-effort boosts.
PRECONDITION_BOOSTS: dict[str, dict[str, int]] = {
    CATEGORY_PROFILE_INCOMPLETE: {
        "profile_incomplete": 2,
        "email_unverified": 2,
    },
    CATEGORY_EARNINGS_QUESTION: {
        "income_upload_pending": 2,
        "s4_started_not_completed": 1,
    },
    CATEGORY_DEDUCTION_QUESTION: {
        "s5_started_not_completed": 1,
        "no_deductions_yet": 1,
    },
    CATEGORY_PAYMENT_QUESTION: {
        "payment_pending": 2,
        "payment_failed_recent": 2,
    },
    CATEGORY_AGREEMENT_QUESTION: {
        "s8_started_not_completed": 1,
        "agreement_generated_recent": 1,
    },
    CATEGORY_IRD_QUESTION: {
        "filing_blocked_on_ird": 2,
    },
}


# Subject patterns that strongly indicate auto-generated mail-back (out of office,
# bounces, etc.) — we never auto-respond to these.
AUTOREPLY_NOISE_PATTERNS = (
    r"\bout of office\b",
    r"\bautoreply\b",
    r"\bauto-?reply\b",
    r"\bauto-?response\b",
    r"\bmail delivery (failed|subsystem|notification)\b",
    r"\bdelivery (status|notification|failure)\b",
    r"\bundeliverable\b",
    r"\bunable to deliver\b",
    r"\bvacation (auto|reply|notice)\b",
)


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class ClassificationResult(BaseModel):
    """Output of classify(). pydantic v2 model."""

    category: str = Field(..., description="One of CATEGORIES.")
    score: int = Field(..., description="Score of the winning category.")
    candidates: list[dict[str, Any]] = Field(
        default_factory=list,
        description="All categories that had at least one keyword hit.",
    )
    matched_keywords: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Per-category keyword hit list for audit.",
    )
    reasoning: str = Field(..., description="Human-readable explanation.")
    is_autoreply_noise: bool = Field(
        default=False,
        description="True if subject looks like an automated bounce / OOO reply.",
    )
    needs_human_review: bool = Field(
        default=False,
        description="True for tied scores or low confidence -- staff classifies.",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _norm(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.lower()).strip()


def detect_autoreply_noise(subject: str, body: str) -> bool:
    """Heuristic: is the inbound likely an OOO / bounce / autoresponder?"""
    sub = _norm(subject)
    bod = _norm(body)[:400]  # only check the start of body
    blob = f"{sub} {bod}"
    for pat in AUTOREPLY_NOISE_PATTERNS:
        if re.search(pat, blob):
            return True
    return False


def classify(
    *,
    subject: str,
    body: str,
    customer_context: Optional[dict[str, Any]] = None,
    customer_matched: bool = True,
) -> ClassificationResult:
    """Classify an inbound customer reply into one of CATEGORIES.

    Args:
        subject: Inbound email subject (verbatim, will be normalized).
        body: Inbound email body (text — caller should strip HTML upstream).
        customer_context: Optional dict of customer-state flags used for
            precondition boosts. Unknown keys are ignored. None == no boosts.
        customer_matched: When False, returns category=UNMATCHED_CUSTOMER
            immediately (no classification attempted).

    Returns:
        ClassificationResult.
    """
    if not customer_matched:
        return ClassificationResult(
            category=CATEGORY_UNMATCHED_CUSTOMER,
            score=0,
            candidates=[],
            matched_keywords={},
            reasoning="customer_not_matched_route_to_staff_queue",
            needs_human_review=True,
        )

    # Autoreply noise short-circuit — never reply to an OOO bounce.
    if detect_autoreply_noise(subject, body):
        return ClassificationResult(
            category=CATEGORY_GENERIC_INQUIRY,
            score=0,
            candidates=[],
            matched_keywords={},
            reasoning="autoreply_noise_detected_no_response_needed",
            is_autoreply_noise=True,
            needs_human_review=False,
        )

    subj_norm = _norm(subject)
    body_norm = _norm(body)

    scores: dict[str, int] = {}
    matched: dict[str, list[str]] = {}

    for cat, keywords in CATEGORY_KEYWORDS.items():
        score = 0
        hits: list[str] = []
        for kw in keywords:
            kw_l = kw.lower()
            if kw_l in subj_norm:
                score += 3
                hits.append(f"subj:{kw}")
            elif kw_l in body_norm:
                score += 1
                hits.append(f"body:{kw}")
        if score > 0:
            scores[cat] = score
            matched[cat] = hits

    # Precondition boosts (optional customer context).
    if customer_context:
        for cat, boosts in PRECONDITION_BOOSTS.items():
            for flag, weight in boosts.items():
                if customer_context.get(flag):
                    scores[cat] = scores.get(cat, 0) + weight
                    matched.setdefault(cat, []).append(f"ctx:{flag}+{weight}")

    if not scores:
        return ClassificationResult(
            category=CATEGORY_GENERIC_INQUIRY,
            score=0,
            candidates=[],
            matched_keywords={},
            reasoning="no_keyword_or_context_matches_default_to_generic",
            needs_human_review=True,
        )

    # Rank by score DESC; on ties prefer the more specific category
    # (PROFILE_INCOMPLETE > EARNINGS > DEDUCTION > PAYMENT > AGREEMENT > IRD).
    SPECIFICITY = {
        CATEGORY_PROFILE_INCOMPLETE: 70,
        CATEGORY_EARNINGS_QUESTION: 60,
        CATEGORY_DEDUCTION_QUESTION: 50,
        CATEGORY_PAYMENT_QUESTION: 55,  # payments matter more than deductions
        CATEGORY_AGREEMENT_QUESTION: 40,
        CATEGORY_IRD_QUESTION: 30,
        CATEGORY_GENERIC_INQUIRY: 10,
    }

    ranked = sorted(
        scores.items(),
        key=lambda kv: (-kv[1], -SPECIFICITY.get(kv[0], 0)),
    )
    candidates = [
        {"category": cat, "score": sc, "specificity": SPECIFICITY.get(cat, 0)}
        for cat, sc in ranked
    ]

    winner_cat, winner_score = ranked[0]

    # Tie detection: if multiple categories with same score AND same specificity,
    # mark as needs_human_review.
    tied = [c for c, s in ranked if s == winner_score]
    if len(tied) > 1:
        tied_specs = {SPECIFICITY.get(c, 0) for c in tied}
        if len(tied_specs) == 1:
            return ClassificationResult(
                category=CATEGORY_GENERIC_INQUIRY,
                score=winner_score,
                candidates=candidates,
                matched_keywords=matched,
                reasoning=(
                    f"tied_score={winner_score} across {len(tied)} "
                    f"categories with same specificity={list(tied_specs)[0]}; "
                    "routing to generic for human review"
                ),
                needs_human_review=True,
            )
        # else specificity tiebreaks — winner is already first in ranked

    # Low-confidence guard: if winner_score==1 (single body hit) and customer
    # context didn't boost it, mark needs_human_review.
    if winner_score == 1:
        return ClassificationResult(
            category=winner_cat,
            score=winner_score,
            candidates=candidates,
            matched_keywords=matched,
            reasoning=(
                f"low_confidence_single_body_hit cat={winner_cat} "
                "score=1 needs_human_review"
            ),
            needs_human_review=True,
        )

    return ClassificationResult(
        category=winner_cat,
        score=winner_score,
        candidates=candidates,
        matched_keywords=matched,
        reasoning=(
            f"single_top cat={winner_cat} score={winner_score} "
            f"hits={matched.get(winner_cat, [])[:5]}"
        ),
        needs_human_review=False,
    )


__all__ = [
    "CATEGORIES",
    "CATEGORY_PROFILE_INCOMPLETE",
    "CATEGORY_EARNINGS_QUESTION",
    "CATEGORY_DEDUCTION_QUESTION",
    "CATEGORY_PAYMENT_QUESTION",
    "CATEGORY_AGREEMENT_QUESTION",
    "CATEGORY_IRD_QUESTION",
    "CATEGORY_GENERIC_INQUIRY",
    "CATEGORY_UNMATCHED_CUSTOMER",
    "CATEGORY_KEYWORDS",
    "PRECONDITION_BOOSTS",
    "ClassificationResult",
    "classify",
    "detect_autoreply_noise",
]
