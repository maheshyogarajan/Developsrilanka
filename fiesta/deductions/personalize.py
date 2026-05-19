"""fiesta.deductions.personalize — order the 10 cards by customer relevance.

Each catalog category carries a `ranking_signal_for_persona` list. The
customer profile carries a (possibly sparse) set of persona signals
derived from S0 triage, S1 onboarding, and S2 income summary. We
compute a relevance score per category and return them ordered.

Default score = 1.0 (every card is shown). Each matching signal adds
+1.0. The `all_personas` sentinel adds +0.5 (so generic cards float in
the middle of the list, never to the top).

The ordering is STABLE within score ties — categories keep their
catalog order. Customer experience matters: solar always comes after
home-office for someone who works from home.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from .catalog_loader import load_catalog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal derivation from profile + income summary.
# ---------------------------------------------------------------------------
def derive_signals(
    profile: dict[str, Any] | None,
    income_summary: dict[str, Any] | None,
) -> set[str]:
    """Map raw profile + income_summary into the persona_signals vocabulary.

    Profile shape (best-effort; missing keys -> no signal):
        {
            "has_home_office": bool,
            "works_from_home": bool,
            "rents_residence": bool,
            "has_dependants": bool,
            "age": int,
            "marital_status": str,
            "philanthropic": bool,
            "religious_active": bool,
            "has_property": bool,
            "career_growth": bool,
            "international_travel": bool,
            "role": str,                  # e.g. "software_developer", "designer"
        }

    Income summary shape:
        {
            "has_foreign_clients": bool,
            "annual_revenue_lkr": Decimal | float,
            "has_subcontractors": bool,
        }
    """
    signals: set[str] = set()
    profile = profile or {}
    income_summary = income_summary or {}

    # Profile-derived signals (direct passthrough)
    direct_keys = (
        "has_home_office", "works_from_home", "rents_residence",
        "has_dependants", "philanthropic", "religious_active",
        "has_property", "career_growth", "international_travel",
    )
    for key in direct_keys:
        if profile.get(key):
            signals.add(key)

    # Derived: senior
    age = profile.get("age")
    if isinstance(age, (int, float)) and age >= 60:
        signals.add("senior")

    # Derived: family
    if profile.get("has_dependants") or profile.get("marital_status") == "married":
        signals.add("family")

    # Derived: role
    role = profile.get("role")
    if role and isinstance(role, str):
        role = role.strip().lower()
        # Normalise common synonyms
        if role in ("software_developer", "developer", "engineer", "programmer"):
            signals.add("software_developer")
        elif role in ("designer", "graphic_designer", "ux", "ux_designer"):
            signals.add("designer")
        elif role in ("sales", "sales_role", "biz_dev"):
            signals.add("sales_role")

    # Income summary-derived
    if income_summary.get("has_foreign_clients") or income_summary.get("foreign_clients"):
        signals.add("foreign_clients")
        signals.add("remote_work")
    if income_summary.get("has_subcontractors"):
        signals.add("has_subcontractors")

    # Revenue threshold for "high_revenue" (Rs 3M+ = top slab)
    rev = income_summary.get("annual_revenue_lkr") or income_summary.get("revenue_lkr")
    try:
        if rev is not None and float(rev) >= 3_000_000:
            signals.add("high_revenue")
    except (TypeError, ValueError):
        pass

    # Solar / environmental — derived from a couple of cues
    if profile.get("has_solar") or profile.get("environmental"):
        signals.add("environmental")

    return signals


# ---------------------------------------------------------------------------
# Scoring + ordering.
# ---------------------------------------------------------------------------
_BASE_SCORE = 1.0
_SIGNAL_MATCH_WEIGHT = 1.0
_ALL_PERSONAS_WEIGHT = 0.5


def _score_category(category: dict[str, Any], signals: set[str]) -> float:
    score = _BASE_SCORE
    for sig in category.get("ranking_signal_for_persona", []) or []:
        if sig == "all_personas":
            score += _ALL_PERSONAS_WEIGHT
        elif sig in signals:
            score += _SIGNAL_MATCH_WEIGHT
    return score


def recommended_deductions(
    profile: dict[str, Any] | None,
    income_summary: dict[str, Any] | None,
    catalog: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return the 10 categories ordered by relevance (highest first).

    Stable within score ties — original catalog order preserved.

    Each returned category dict has a new `_relevance_score` key for
    debugging / UI hints.
    """
    cat = catalog or load_catalog()
    signals = derive_signals(profile, income_summary)
    logger.debug("personalize: signals=%s", sorted(signals))

    enriched = []
    for idx, category in enumerate(cat.get("categories", [])):
        score = _score_category(category, signals)
        out = dict(category)
        out["_relevance_score"] = round(score, 3)
        out["_catalog_order"] = idx
        enriched.append(out)

    # Sort: highest score first; on tie, preserve catalog order.
    enriched.sort(key=lambda c: (-c["_relevance_score"], c["_catalog_order"]))
    return enriched


def explain_ordering(
    profile: dict[str, Any] | None,
    income_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    """Debug helper — returns the signals used and the ranked categories."""
    signals = derive_signals(profile, income_summary)
    ranked = recommended_deductions(profile, income_summary)
    return {
        "signals": sorted(signals),
        "ranked": [
            {
                "id": c["id"],
                "name": c["name"],
                "score": c["_relevance_score"],
                "matched_signals": [
                    s for s in (c.get("ranking_signal_for_persona") or []) if s in signals
                ],
            }
            for c in ranked
        ],
    }
