"""tests/tax/test_b10_nrr.py — B10 NRR classifier + engine integration.

10 tests covering classification (resident / NRR / non-resident / unknown),
date-anchored window expiry, foreign-income exemption in compute_tax_25_26,
income_sources auto-update, and classification_log append-only behaviour.

Run::

    cd C:/Users/mahes/fiesta_phase_a/Developsrilanka_ms2_b10
    python -m pytest tests/tax/test_b10_nrr.py -v
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from dateutil.relativedelta import relativedelta

from fiesta.tax.nrr_classifier import (
    NRR_CONCESSION_WINDOW_YEARS,
    NRR_MIN_YEARS_ABROAD,
    SL_RESIDENT_MIN_DAYS,
    ClassificationResult,
    classify_user_residency,
    current_tax_year_start,
    is_nrr_window_active,
    nrr_window_end,
)
from fiesta.tax.residency import ResidencyStatus


# ---------------------------------------------------------------------------
# Local fixtures — reuse session/user from tests/tax/conftest.py
# ---------------------------------------------------------------------------
@pytest.fixture
def fresh_user(session):
    """Throw-away user with a unique email per test."""
    from models import User
    import uuid

    u = User(
        email=f"b10_test_{uuid.uuid4().hex[:8]}@fiesta.local",
        name="B10 Test User",
        role="user",
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
    )
    session.add(u)
    session.commit()
    yield u
    session.delete(u)
    session.commit()


def _today_for_ty(year: int, month: int = 6, day: int = 1) -> date:
    """Convenience: return a date inside a chosen tax year for deterministic tests."""
    return date(year, month, day)


# ---------------------------------------------------------------------------
# Helpers — set the "fake" days_in_sl_<TY> field by mocking the lookup
# ---------------------------------------------------------------------------
def _set_days_in_sl(user, ty_start: date, days: int) -> None:
    """Attach a dynamic days_in_sl_<YY_YY> attribute the classifier reads."""
    ty_label = f"{str(ty_start.year)[2:]}_{str(ty_start.year + 1)[2:]}"
    setattr(user, f"days_in_sl_{ty_label}", days)


# ---------------------------------------------------------------------------
# 1. test_resident_user_classified_as_resident
# ---------------------------------------------------------------------------
def test_resident_user_classified_as_resident(fresh_user):
    """≥183 days in SL + no return-from-abroad date → RESIDENT."""
    on = _today_for_ty(2026, 6, 1)  # inside 2026/27 TY (1 Apr 2026 - 31 Mar 2027)
    ty_start = current_tax_year_start(on)
    _set_days_in_sl(fresh_user, ty_start, 200)

    result = classify_user_residency(fresh_user, on=on, persist=False)
    assert result.status == ResidencyStatus.RESIDENT
    assert result.confidence == "high"
    assert "183" in result.reasoning or "days" in result.reasoning
    assert result.signals["days_in_sl"] == 200


# ---------------------------------------------------------------------------
# 2. test_returned_user_classified_as_nrr_if_5yr_abroad
# ---------------------------------------------------------------------------
def test_returned_user_classified_as_nrr_if_5yr_abroad(fresh_user):
    """Returned within lookback + ≥5y abroad + within 3y window → NRR."""
    on = _today_for_ty(2026, 6, 1)
    # Returned 6 months ago (well within 2-TY lookback) after 7 years abroad
    fresh_user.returned_to_sl_date = on - relativedelta(months=6)
    fresh_user.years_abroad_prior_to_return = 7

    result = classify_user_residency(fresh_user, on=on, persist=False)
    assert result.status == ResidencyStatus.NRR
    assert result.confidence == "high"
    assert "Non-Resident Returnee" in result.reasoning or "NRR" in result.reasoning
    assert result.signals["years_abroad_prior_to_return"] == 7


# ---------------------------------------------------------------------------
# 3. test_returned_user_NOT_nrr_if_only_2yr_abroad
# ---------------------------------------------------------------------------
def test_returned_user_NOT_nrr_if_only_2yr_abroad(fresh_user):
    """Returned recently but only 2 years abroad → NOT NRR.

    With 200 days in SL → RESIDENT (regular). Without day-count signal →
    UNKNOWN (we don't auto-classify NRR just because someone moved).
    """
    on = _today_for_ty(2026, 6, 1)
    ty_start = current_tax_year_start(on)
    fresh_user.returned_to_sl_date = on - relativedelta(months=3)
    fresh_user.years_abroad_prior_to_return = 2  # below 5-year threshold
    _set_days_in_sl(fresh_user, ty_start, 250)

    result = classify_user_residency(fresh_user, on=on, persist=False)
    assert result.status != ResidencyStatus.NRR
    assert result.status == ResidencyStatus.RESIDENT  # 250 days → resident


# ---------------------------------------------------------------------------
# 4. test_nrr_window_expires_after_3_years
# ---------------------------------------------------------------------------
def test_nrr_window_expires_after_3_years(fresh_user):
    """At return_date + 3 years exactly, NRR window is EXPIRED."""
    return_date = date(2023, 6, 1)
    fresh_user.returned_to_sl_date = return_date
    fresh_user.years_abroad_prior_to_return = 8

    # Just before window end: still NRR-eligible (but only if within lookback)
    just_before_expiry = return_date + relativedelta(years=3) - timedelta(days=1)
    # however 2026-05-31 is >2 TYs after 2023-06-01 return, so NRR lookback
    # FAILS. Confirm via is_nrr_window_active directly:
    assert is_nrr_window_active(return_date, on=just_before_expiry) is True

    # Boundary day (return_date + 3y exact) → window NOT active per the
    # strict "exact boundary = expired" rule.
    boundary = return_date + relativedelta(years=3)
    assert is_nrr_window_active(return_date, on=boundary) is False
    assert nrr_window_end(return_date) == boundary

    # Past the boundary, classifier never returns NRR
    on_after = boundary + timedelta(days=10)
    result = classify_user_residency(fresh_user, on=on_after, persist=False)
    assert result.status != ResidencyStatus.NRR


# ---------------------------------------------------------------------------
# 5. test_nonresident_user_classified
# ---------------------------------------------------------------------------
def test_nonresident_user_classified(fresh_user):
    """< 183 days in SL + no NRR claim + no COI → NONRESIDENT."""
    on = _today_for_ty(2026, 6, 1)
    ty_start = current_tax_year_start(on)
    _set_days_in_sl(fresh_user, ty_start, 30)  # well below 183

    result = classify_user_residency(fresh_user, on=on, persist=False)
    assert result.status == ResidencyStatus.NONRESIDENT
    assert result.confidence == "high"
    assert "Non-resident" in result.reasoning or "non-resident" in result.reasoning


# ---------------------------------------------------------------------------
# 6. test_unknown_classification_for_insufficient_signals
# ---------------------------------------------------------------------------
def test_unknown_classification_for_insufficient_signals(fresh_user):
    """Empty profile + no remittances → UNKNOWN."""
    on = _today_for_ty(2026, 6, 1)
    # Don't set days_in_sl, don't set returned_to_sl_date, no remittances.
    result = classify_user_residency(fresh_user, on=on, persist=False)
    assert result.status == ResidencyStatus.UNKNOWN
    assert result.confidence == "low"
    assert "Insufficient signals" in result.reasoning


# ---------------------------------------------------------------------------
# 7. test_nrr_foreign_income_exempt_in_tax_compute
# ---------------------------------------------------------------------------
def test_nrr_foreign_income_exempt_in_tax_compute():
    """compute_tax_25_26 with residency_status=NRR + within window
    → foreign_lkr treated as 0 (no foreign-income contribution to tax).
    """
    from fiesta.tax import Income, compute_tax_25_26

    on = date(2026, 6, 1)
    return_date = on - relativedelta(months=6)  # active NRR window

    income_with_foreign = Income(
        employment_lkr=Decimal("1500000"),
        foreign_lkr=Decimal("5000000"),
    )

    # NRR run → foreign exempt
    nrr_result = compute_tax_25_26(
        income_with_foreign,
        residency_status=ResidencyStatus.NRR,
        returned_to_sl_date=return_date,
        on=on,
    )

    # Resident run → foreign taxed
    resident_result = compute_tax_25_26(
        income_with_foreign,
        residency_status=ResidencyStatus.RESIDENT,
    )

    # NRR should owe less tax than resident (foreign income exempted)
    assert nrr_result.gross_tax_lkr < resident_result.gross_tax_lkr
    # Gross income excludes the foreign portion in the NRR computation
    assert nrr_result.gross_income_lkr == Decimal("1500000")
    assert resident_result.gross_income_lkr == Decimal("6500000")


# ---------------------------------------------------------------------------
# 8. test_resident_foreign_income_taxed_in_tax_compute
# ---------------------------------------------------------------------------
def test_resident_foreign_income_taxed_in_tax_compute():
    """compute_tax_25_26 with residency_status=RESIDENT or None
    → foreign_lkr flows into the gross/taxable computation normally.
    """
    from fiesta.tax import Income, compute_tax_25_26

    income = Income(
        employment_lkr=Decimal("1500000"),
        foreign_lkr=Decimal("2000000"),
    )

    no_status = compute_tax_25_26(income)  # status not supplied
    resident = compute_tax_25_26(income, residency_status=ResidencyStatus.RESIDENT)

    # Both should produce the same answer — no-status defaults to taxing foreign
    assert no_status.gross_tax_lkr == resident.gross_tax_lkr
    assert resident.gross_income_lkr == Decimal("3500000")


def test_nrr_window_expired_still_taxes_foreign():
    """NRR status but window already expired → foreign income taxed normally."""
    from fiesta.tax import Income, compute_tax_25_26

    on = date(2026, 6, 1)
    # Returned 4 years ago — window expired 1 year ago
    return_date = on - relativedelta(years=4)

    income = Income(
        employment_lkr=Decimal("1500000"),
        foreign_lkr=Decimal("3000000"),
    )
    nrr_expired = compute_tax_25_26(
        income,
        residency_status=ResidencyStatus.NRR,
        returned_to_sl_date=return_date,
        on=on,
    )
    resident = compute_tax_25_26(income, residency_status=ResidencyStatus.RESIDENT)

    # Expired NRR should produce same result as resident
    assert nrr_expired.gross_tax_lkr == resident.gross_tax_lkr
    assert nrr_expired.gross_income_lkr == Decimal("4500000")


# ---------------------------------------------------------------------------
# 9. test_income_sources_auto_updated_on_classify
# ---------------------------------------------------------------------------
def test_income_sources_auto_updated_on_classify(session, fresh_user):
    """When user has remittances + classifier runs, 'foreign_remittance'
    appended to income_sources idempotently.
    """
    from remittance_models import RemittanceEntry

    # Add a remittance row
    r = RemittanceEntry(
        user_id=fresh_user.id,
        remittance_date=date(2026, 5, 10),
        foreign_currency="USD",
        foreign_amount=Decimal("1000.00"),
        lkr_amount_cbsl=Decimal("305500.00"),
        cbsl_rate=Decimal("305.500000"),
        source_country="US",
        tax_year="2026-27",
    )
    session.add(r)
    session.commit()

    # Baseline: income_sources is empty
    assert (fresh_user.income_sources or []) == []

    classify_user_residency(fresh_user, on=date(2026, 6, 1), persist=True)
    session.refresh(fresh_user)
    assert "foreign_remittance" in (fresh_user.income_sources or [])

    # Idempotent: re-run should not duplicate
    classify_user_residency(fresh_user, on=date(2026, 6, 1), persist=True)
    session.refresh(fresh_user)
    count = (fresh_user.income_sources or []).count("foreign_remittance")
    assert count == 1


# ---------------------------------------------------------------------------
# 10. test_classification_log_appended
# ---------------------------------------------------------------------------
def test_classification_log_appended(session, fresh_user):
    """Each persist=True run appends one entry to residency_classification_log."""
    on1 = date(2026, 6, 1)
    on2 = date(2026, 7, 1)
    ty_start = current_tax_year_start(on1)
    _set_days_in_sl(fresh_user, ty_start, 200)

    # Baseline: empty log
    assert (fresh_user.residency_classification_log or []) == []

    classify_user_residency(fresh_user, on=on1, persist=True)
    session.refresh(fresh_user)
    log = fresh_user.residency_classification_log or []
    assert len(log) == 1
    assert log[0]["status"] == "resident"
    assert "at" in log[0]
    assert "reasoning" in log[0]
    assert "signals" in log[0]

    # Second run appends a second entry
    classify_user_residency(fresh_user, on=on2, persist=True)
    session.refresh(fresh_user)
    log = fresh_user.residency_classification_log or []
    assert len(log) == 2
    # Both entries record the same resident status (still ≥183 days)
    assert all(entry["status"] == "resident" for entry in log)
