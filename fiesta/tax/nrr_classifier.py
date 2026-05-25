"""fiesta.tax.nrr_classifier — B10 NRR (Non-Resident Returnee) classifier.

MS2 Stage E.1 — Tier D6 / B10. Classifies a User into one of four
``ResidencyStatus`` enum values (RESIDENT / NRR / NONRESIDENT / UNKNOWN)
and persists the result on ``User.residency_status``.

Reads (in priority order):
  1. User-declared profile facts: ``returned_to_sl_date`` +
     ``years_abroad_prior_to_return`` (collected via the S1 triage/profile
     onboarding question and the S3 profile tax-residency panel).
  2. Day-count signals: if any ``User.days_in_sl_<YY_YY>`` column exists
     (added by future Section G G1 work), use it. Otherwise infer presence
     from remittance dates: month with at least one inward remittance
     plausibly = in SL (light signal).
  3. ``FiestaProfile.days_in_sl_current_year`` — the existing S3 profile field.

Decision tree (binding):
  a. If declared NRR facts + return_date within current TY or prior 2 TYs
     AND years_abroad_prior_to_return >= 5  → NRR (date-anchored window).
     The NRR window EXPIRES at return_date + 3 years.
  b. Else if SL-resident test passes (days_in_sl_TY >= 183 OR
     center-of-vital-interests in SL — proxied by tax_resident_year >= 3
     AND has_address_in_sl)  → RESIDENT.
  c. Else if non-resident test passes (days_in_sl_TY < 183 AND no center
     of vital interests AND no NRR claim)  → NONRESIDENT.
  d. Else  → UNKNOWN.

Side-effects of ``classify_user_residency(user, *, persist=True)``:
  - Sets ``user.residency_status`` to the resolved enum value.
  - Appends a structured reasoning entry to
    ``user.residency_classification_log`` (JSON array).
  - If remittances exist AND ``'foreign_remittance'`` not already in
    ``user.income_sources``, appends it (idempotent — never duplicates).
  - Commits the SQLAlchemy session ONLY when ``persist=True`` (default).
    Tests pass ``persist=False`` to inspect the returned value without DB
    side-effects.

IRA citation (TODO — verify with KG when rate limit clears):
  §69 IRA (Resident persons) — 183-day test + center-of-vital-interests
    proxy + government-employee rules. PROVED via mcp__ira__get_section.
  Third Schedule (Exempt Amounts) — NRR concessional treatment is widely
    understood as Third Schedule paragraphs covering foreign-currency
    accounts of non-resident returnees and reduced-rate provisions for
    returnees within their 3-year transitional window. EXACT paragraph
    TODO — IRA KG was rate-limited at classifier build time
    (2026-05-25). Sixth Schedule (Temporary Concessions) may also apply
    for years post-2022 amendments. Surfaced as TODO in the profile-page
    explainer; do NOT publish the citation as VERIFIED until KG lookup
    completes.

Returns:
  ``ClassificationResult`` — dataclass with .status (ResidencyStatus),
  .reasoning (str), and .confidence (str: 'high' / 'medium' / 'low').

Pure-ish: NO network. ORM + Decimal/date arithmetic only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Optional

from dateutil.relativedelta import relativedelta

from .residency import ResidencyStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Locked constants — referenced by tests + UI explainer
# ---------------------------------------------------------------------------
NRR_MIN_YEARS_ABROAD: int = 5
NRR_CONCESSION_WINDOW_YEARS: int = 3
SL_RESIDENT_MIN_DAYS: int = 183  # IRA §69(1)(b)
NRR_LOOKBACK_TAX_YEARS: int = 2  # current TY + prior 2 TYs


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ClassificationResult:
    """Outcome of one classification run.

    ``status``   — the resolved ResidencyStatus enum value.
    ``reasoning`` — plain-English explanation suitable for the profile-page
                    "Why this matters" expandable.
    ``confidence`` — 'high' (declared facts present), 'medium' (signals
                     only), 'low' (default fallback).
    ``signals``   — dict of the raw signals the classifier weighed.
    """

    status: ResidencyStatus
    reasoning: str
    confidence: str
    signals: dict


# ---------------------------------------------------------------------------
# Tax-year helpers (SL Y/A runs 1 April → 31 March)
# ---------------------------------------------------------------------------
def current_tax_year_start(on: Optional[date] = None) -> date:
    """Return 1 April of the current SL Y/A."""
    d = on or date.today()
    start_year = d.year if d.month >= 4 else d.year - 1
    return date(start_year, 4, 1)


def tax_year_label_from_start(start: date) -> str:
    """Format ``YYYY/YY`` for a given Y/A start date (1 April)."""
    return f"{start.year}/{str(start.year + 1)[2:]}"


def _is_within_nrr_lookback(return_date: date, on: Optional[date] = None) -> bool:
    """True if return_date lies within current TY or the prior ``NRR_LOOKBACK_TAX_YEARS`` TYs.

    Date-anchored — never use a static ``current_year - X`` comparison.
    """
    on = on or date.today()
    current_ty_start = current_tax_year_start(on)
    # Earliest TY start we still consider "returnee" for THIS year's
    # classifier run = current TY start minus NRR_LOOKBACK_TAX_YEARS years.
    earliest_start = current_ty_start - relativedelta(years=NRR_LOOKBACK_TAX_YEARS)
    return earliest_start <= return_date <= on


def nrr_window_end(return_date: date) -> date:
    """Date the NRR concession window expires (return_date + 3 years exact)."""
    return return_date + relativedelta(years=NRR_CONCESSION_WINDOW_YEARS)


def is_nrr_window_active(return_date: date, on: Optional[date] = None) -> bool:
    """True iff the NRR 3-year concession window is still open on ``on``.

    Boundary: on == return_date + 3y EXACTLY → NOT active (window expired
    the moment the 3rd anniversary rolled over). Caller is responsible for
    deciding whether the boundary day is itself "within" or "after" — we
    follow the strict "exact boundary = expired" rule per design contract.
    """
    on = on or date.today()
    return on < nrr_window_end(return_date)


# ---------------------------------------------------------------------------
# Signal extractors
# ---------------------------------------------------------------------------
def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    """Safe attribute access — returns ``default`` for missing OR None values."""
    val = getattr(obj, name, default)
    return val if val is not None else default


def _extract_days_in_sl(user: Any, ty_start: date) -> Optional[int]:
    """Return days_in_sl for the given tax-year start, or None.

    Looks first for a ``User.days_in_sl_<YY_YY>`` column (future Section G
    G1 schema). Falls back to ``FiestaProfile.days_in_sl_current_year`` if
    the user has a profile and the current_year reference matches ty_start.
    """
    ty_label = f"{str(ty_start.year)[2:]}_{str(ty_start.year + 1)[2:]}"
    user_attr = f"days_in_sl_{ty_label}"
    val = getattr(user, user_attr, None)
    if val is not None:
        try:
            return int(val)
        except (TypeError, ValueError):
            pass

    # Fallback: FiestaProfile.days_in_sl_current_year
    profile = getattr(user, "fiesta_profile", None)
    # SQLAlchemy backref may return an InstrumentedList for non-uselist=False
    # relationships; the profile relationship in this codebase is uselist=False
    # so ``profile`` is the FiestaProfile row directly (or None).
    if profile is not None:
        days = getattr(profile, "days_in_sl_current_year", None)
        if days is not None:
            try:
                return int(days)
            except (TypeError, ValueError):
                pass
    return None


def _has_center_of_vital_interests_in_sl(user: Any) -> bool:
    """Proxy for "centre of vital interests in SL" (§69 secondary test).

    True if FiestaProfile.tax_resident_year >= 3 (settled resident) AND
    profile country is 'LK' AND there is at least one address line. This is
    a conservative proxy — real IRA case-law on §69 weighs family/home/
    economic ties, which FIESTA does not collect explicitly.
    """
    profile = getattr(user, "fiesta_profile", None)
    if profile is None:
        return False
    try:
        tax_resident_year = int(getattr(profile, "tax_resident_year", 0) or 0)
    except (TypeError, ValueError):
        tax_resident_year = 0
    country = getattr(profile, "country", "") or ""
    address_line1 = getattr(profile, "address_line1", "") or ""
    return tax_resident_year >= 3 and country == "LK" and bool(address_line1.strip())


def _infer_days_in_sl_from_remittances(
    user_id: int,
    ty_start: date,
    ty_end: date,
) -> Optional[int]:
    """Heuristic — count distinct months with at least one remittance in TY.

    Light signal only — multiplied by ~30 to get a rough days-equivalent.
    Never used to PROVE residency; only used to fill UNKNOWN when no
    explicit days_in_sl signal exists.
    """
    try:
        from remittance_models import RemittanceEntry
    except Exception:  # pragma: no cover — model not imported in some test ctxs
        return None
    try:
        rows = (
            RemittanceEntry.query
            .filter(RemittanceEntry.user_id == user_id)
            .filter(RemittanceEntry.remittance_date >= ty_start)
            .filter(RemittanceEntry.remittance_date < ty_end)
            .all()
        )
    except Exception:
        return None
    months = {(r.remittance_date.year, r.remittance_date.month) for r in rows if r.remittance_date}
    if not months:
        return None
    return len(months) * 30  # 12 months → 360; sufficient for >=183 threshold


def _has_any_remittances(user_id: int) -> bool:
    """True if the user has at least one RemittanceEntry."""
    try:
        from remittance_models import RemittanceEntry
        return RemittanceEntry.query.filter(RemittanceEntry.user_id == user_id).first() is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# The classifier
# ---------------------------------------------------------------------------
def classify_user_residency(
    user: Any,
    *,
    on: Optional[date] = None,
    persist: bool = True,
) -> ClassificationResult:
    """Classify ``user`` into one of the four ResidencyStatus values.

    Args:
      user: SQLAlchemy ``User`` instance (must have .id). Reads:
        - .returned_to_sl_date (Date, nullable)
        - .years_abroad_prior_to_return (Integer, nullable)
        - .residency_classification_log (JSON list — appended to on persist)
        - .residency_status (str — overwritten on persist)
        - .income_sources (JSON list — appended to on persist, idempotent)
        - .fiesta_profile (relationship, optional — for fallback signals)
      on: Reference date for the classification (defaults to today). Lets
        callers reproduce historical decisions.
      persist: When True (default), writes back to the User row + commits.
        When False, returns the result without DB side-effects (used by
        unit tests + dry-runs).

    Returns:
      ClassificationResult with .status, .reasoning, .confidence, .signals.
    """
    on = on or date.today()
    ty_start = current_tax_year_start(on)
    ty_end = ty_start + relativedelta(years=1)
    ty_label = tax_year_label_from_start(ty_start)

    returned_to_sl_date: Optional[date] = _get_attr(user, "returned_to_sl_date")
    years_abroad: Optional[int] = _get_attr(user, "years_abroad_prior_to_return")
    days_in_sl = _extract_days_in_sl(user, ty_start)
    has_coi = _has_center_of_vital_interests_in_sl(user)
    has_remittances = _has_any_remittances(_get_attr(user, "id", 0)) if _get_attr(user, "id") else False

    signals: dict = {
        "on": on.isoformat(),
        "ty_label": ty_label,
        "returned_to_sl_date": returned_to_sl_date.isoformat() if returned_to_sl_date else None,
        "years_abroad_prior_to_return": years_abroad,
        "days_in_sl": days_in_sl,
        "has_center_of_vital_interests": has_coi,
        "has_remittances": has_remittances,
    }

    # If days_in_sl is unknown but remittances exist, derive a hint.
    days_in_sl_hint: Optional[int] = None
    if days_in_sl is None and _get_attr(user, "id"):
        days_in_sl_hint = _infer_days_in_sl_from_remittances(
            user.id, ty_start, ty_end
        )
        signals["days_in_sl_hint_from_remittances"] = days_in_sl_hint

    # ---------------- Rule a: NRR ----------------
    if (
        returned_to_sl_date is not None
        and years_abroad is not None
        and years_abroad >= NRR_MIN_YEARS_ABROAD
        and _is_within_nrr_lookback(returned_to_sl_date, on)
        and is_nrr_window_active(returned_to_sl_date, on)
    ):
        window_end = nrr_window_end(returned_to_sl_date)
        reasoning = (
            f"Non-Resident Returnee (NRR). You returned to Sri Lanka on "
            f"{returned_to_sl_date.isoformat()} after {years_abroad} years "
            f"abroad — both within the lookback window and inside the "
            f"3-year concession period (window expires "
            f"{window_end.isoformat()}). Foreign-source income exempt "
            f"during the window per the IRA Third Schedule "
            f"(NRR concession; exact paragraph TODO — pending IRA KG "
            f"verification)."
        )
        result = ClassificationResult(
            status=ResidencyStatus.NRR,
            reasoning=reasoning,
            confidence="high",
            signals=signals,
        )
        _maybe_persist(user, result, has_remittances, persist)
        return result

    # ---------------- Rule b: RESIDENT ----------------
    is_resident_by_days = days_in_sl is not None and days_in_sl >= SL_RESIDENT_MIN_DAYS
    is_resident_by_days_hint = (
        days_in_sl is None
        and days_in_sl_hint is not None
        and days_in_sl_hint >= SL_RESIDENT_MIN_DAYS
    )
    if is_resident_by_days or has_coi:
        if is_resident_by_days:
            basis = f"present in SL {days_in_sl} days in {ty_label} (>= 183, IRA §69(1)(b))"
            conf = "high"
        else:
            basis = (
                f"centre of vital interests in SL "
                f"(settled resident with LK address — IRA §69(1)(a) proxy)"
            )
            conf = "medium"
        reasoning = f"SL-resident: {basis}. Worldwide income assessable; foreign income taxed at the 25/26 dual-track 15% cap."
        result = ClassificationResult(
            status=ResidencyStatus.RESIDENT,
            reasoning=reasoning,
            confidence=conf,
            signals=signals,
        )
        _maybe_persist(user, result, has_remittances, persist)
        return result

    if is_resident_by_days_hint:
        reasoning = (
            f"SL-resident (inferred): remittance-month proxy suggests "
            f"~{days_in_sl_hint} days in SL during {ty_label}. Soft signal; "
            f"confirm via the profile day-count field."
        )
        result = ClassificationResult(
            status=ResidencyStatus.RESIDENT,
            reasoning=reasoning,
            confidence="low",
            signals=signals,
        )
        _maybe_persist(user, result, has_remittances, persist)
        return result

    # ---------------- Rule c: NONRESIDENT ----------------
    # Trigger only when we have AT LEAST ONE concrete signal that points
    # away from SL residency. Otherwise we fall through to UNKNOWN — never
    # mark someone non-resident from an empty profile.
    has_negative_signal = (
        (days_in_sl is not None and days_in_sl < SL_RESIDENT_MIN_DAYS)
        or has_remittances  # remittances imply earnings abroad → tilt non-resident
    )
    if has_negative_signal and not has_coi:
        if days_in_sl is not None:
            basis = f"only {days_in_sl} days in SL during {ty_label} (< 183, IRA §69(1)(b))"
            conf = "high"
        else:
            basis = "foreign remittances on file and no SL centre-of-vital-interests"
            conf = "low"
        reasoning = f"Non-resident: {basis}. Only SL-source income is assessable in Sri Lanka."
        result = ClassificationResult(
            status=ResidencyStatus.NONRESIDENT,
            reasoning=reasoning,
            confidence=conf,
            signals=signals,
        )
        _maybe_persist(user, result, has_remittances, persist)
        return result

    # ---------------- Rule d: UNKNOWN ----------------
    reasoning = (
        "Insufficient signals to classify. Need at least one of: "
        "days in SL this tax year, declared return-from-abroad date, "
        "or a complete tax-residency profile."
    )
    result = ClassificationResult(
        status=ResidencyStatus.UNKNOWN,
        reasoning=reasoning,
        confidence="low",
        signals=signals,
    )
    _maybe_persist(user, result, has_remittances, persist)
    return result


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _maybe_persist(
    user: Any,
    result: ClassificationResult,
    has_remittances: bool,
    persist: bool,
) -> None:
    """Apply the result to the User row + commit, if ``persist=True``.

    Updates ``residency_status``, appends to ``residency_classification_log``,
    and idempotently adds ``'foreign_remittance'`` to ``income_sources`` when
    the user has remittances.
    """
    if not persist:
        return

    try:
        from app import db
    except Exception:  # pragma: no cover — no Flask app in some unit ctxs
        return

    # 1. residency_status
    user.residency_status = result.status.value

    # 2. residency_classification_log (append)
    log = list(getattr(user, "residency_classification_log", None) or [])
    log.append(
        {
            "at": datetime.utcnow().isoformat(timespec="seconds"),
            "status": result.status.value,
            "confidence": result.confidence,
            "reasoning": result.reasoning,
            "signals": result.signals,
        }
    )
    user.residency_classification_log = log
    # JSON columns: re-assignment is required for SQLAlchemy to detect change
    # on mutable types. Setting the attribute above is sufficient.

    # 3. income_sources — append 'foreign_remittance' (idempotent)
    if has_remittances:
        sources = list(getattr(user, "income_sources", None) or [])
        if "foreign_remittance" not in sources:
            sources.append("foreign_remittance")
            user.income_sources = sources

    try:
        db.session.commit()
    except Exception as exc:  # pragma: no cover
        db.session.rollback()
        logger.error("NRR classifier persist failed for user_id=%s: %s", _get_attr(user, "id"), exc)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------
__all__ = [
    "ClassificationResult",
    "classify_user_residency",
    "current_tax_year_start",
    "tax_year_label_from_start",
    "is_nrr_window_active",
    "nrr_window_end",
    "NRR_MIN_YEARS_ABROAD",
    "NRR_CONCESSION_WINDOW_YEARS",
    "SL_RESIDENT_MIN_DAYS",
    "NRR_LOOKBACK_TAX_YEARS",
]
