"""fiesta.triage.questions — S1 triage question catalog.

The 3 neutral fact-finds shown post-signup. Each question is a structured dict
so the route + template + validator + tests all read from the same source of
truth. Keep this file small and dumb — it is intentionally a catalog, not
business logic.

Voice rules (council brief 2026-05-20):
  * No priming language ("Are you a freelancer?" -> "Earning vehicle?")
  * No sales language ("Save tax with us!" -> [absent])
  * Multi-select where natural (mixed-vehicle earners are common)
  * Plain phrasing, no jargon

Branching downstream (consumed by /fie dashboard + S5 deduction chips):
  * Q1 = pure_foreign      -> emphasise foreign-remittance flows + S4 FX rows
  * Q1 = mixed             -> emphasise both
  * Q1 = pure_local        -> hide foreign-income flows on S4
  * Q2 contains studio     -> show subcontractor agreement chip on S5
  * Q2 contains property   -> route to S7 property owner screen
  * Q3 = used_lankatax     -> import-existing-data tile on dashboard
  * Q3 = never_filed       -> show first-filer onboarding banner

This file does NOT compute the branching — see persona/router for that.
S1 only persists raw answers; downstream readers interpret them.
"""

from __future__ import annotations

from typing import Any, Dict, List

# Question IDs are stable; never rename. Adding new options is additive-safe.
QUESTIONS: List[Dict[str, Any]] = [
    {
        "id": "earning_source",
        "order": 1,
        "prompt": "How do you mainly earn?",
        "subtext": "Pick the option that matches today. You can change this later.",
        "kind": "single",  # one answer
        "options": [
            {
                "id": "pure_foreign",
                "label": "Mostly from clients or employers outside Sri Lanka",
                "hint": "Foreign clients, overseas employer, remittances inward.",
            },
            {
                "id": "mixed",
                "label": "A mix of foreign and local",
                "hint": "Some of each.",
            },
            {
                "id": "pure_local",
                "label": "Mostly from Sri Lanka",
                "hint": "Local clients, local employer, local rental.",
            },
        ],
    },
    {
        "id": "earning_vehicle",
        "order": 2,
        "prompt": "How is the work done?",
        "subtext": "Pick all that apply.",
        "kind": "multi",  # one or more answers
        "options": [
            {
                "id": "solo_freelancer",
                "label": "Solo freelancer / independent",
                "hint": "Just you, billing clients.",
            },
            {
                "id": "studio_with_subcontractors",
                "label": "Small studio with people I subcontract to",
                "hint": "You pay others to help deliver.",
            },
            {
                "id": "employee_with_side",
                "label": "Salaried employee with side income",
                "hint": "Day job plus extras.",
            },
            {
                "id": "property",
                "label": "Property / rental income",
                "hint": "House, apartment, or land you rent out.",
            },
            {
                "id": "other",
                "label": "Something else",
                "hint": "Pick this and we'll ask later.",
            },
        ],
    },
    {
        "id": "filing_history",
        "order": 3,
        "prompt": "How have you filed taxes before?",
        "subtext": "There is no wrong answer — this just tells us what to skip.",
        "kind": "single",
        "options": [
            {
                "id": "never_filed",
                "label": "I have never filed",
                "hint": "First time filer.",
            },
            {
                "id": "filed_manually_with_help",
                "label": "Filed manually, usually with help",
                "hint": "Accountant, friend, or directly with IRD.",
            },
            {
                "id": "used_lankatax",
                "label": "Used Lanka.tax before",
                "hint": "We can pull what we already know.",
            },
            {
                "id": "used_other_platform",
                "label": "Used another platform",
                "hint": "Different tax-prep service or tool.",
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Lookup helpers — keep callers (routes, validators) from re-walking the list
# ---------------------------------------------------------------------------

QUESTIONS_BY_ID: Dict[str, Dict[str, Any]] = {q["id"]: q for q in QUESTIONS}

QUESTION_ORDER: List[str] = [q["id"] for q in sorted(QUESTIONS, key=lambda x: x["order"])]


def get_question(qid: str) -> Dict[str, Any]:
    """Return the question dict for a given id, or KeyError."""
    return QUESTIONS_BY_ID[qid]


def valid_option_ids(qid: str) -> List[str]:
    """Return the list of valid option ids for a given question."""
    q = QUESTIONS_BY_ID.get(qid)
    if not q:
        return []
    return [opt["id"] for opt in q["options"]]


def is_multi(qid: str) -> bool:
    """True if a question allows multi-select."""
    q = QUESTIONS_BY_ID.get(qid)
    return bool(q and q.get("kind") == "multi")


def next_question_id(current_qid: str) -> str | None:
    """Return the next question id in the canonical sequence, or None if done."""
    try:
        idx = QUESTION_ORDER.index(current_qid)
    except ValueError:
        return None
    if idx + 1 >= len(QUESTION_ORDER):
        return None
    return QUESTION_ORDER[idx + 1]


__all__ = [
    "QUESTIONS",
    "QUESTIONS_BY_ID",
    "QUESTION_ORDER",
    "get_question",
    "valid_option_ids",
    "is_multi",
    "next_question_id",
]
