"""
T5 — Wave B1: FX rate service tests.

Verifies:
- get_rate returns None for LKR / empty currency / invalid input
- get_rate uses local cache when available (no network call)
- store_manual_rate persists + sanity-filters
- Sanity range rejects out-of-band values
- Freeze-at-entry: a saved RemittanceEntry's cbsl_rate is NOT mutated by a
  later fx_rate_service call
"""
from datetime import date, datetime
from decimal import Decimal

import pytest

from .conftest import login_as


def test_get_rate_lkr_returns_none():
    from fx_rate_service import get_rate
    assert get_rate("LKR", date(2026, 3, 15)) is None
    assert get_rate("", date(2026, 3, 15)) is None
    assert get_rate(None, date(2026, 3, 15)) is None


def test_get_rate_invalid_date_returns_none():
    from fx_rate_service import get_rate
    assert get_rate("USD", None) is None
    assert get_rate("USD", "2026-03-15") is None  # str, not date


def test_store_manual_rate_persists_and_is_retrievable(app):
    from fx_rate_service import store_manual_rate, get_rate
    with app.app_context():
        d = date(2026, 1, 5)
        fx = store_manual_rate("AUD", d, Decimal("215.00"))
        assert fx.value == Decimal("215.00")
        assert fx.source == "manual"
        # Cache lookup should now return it
        looked_up = get_rate("AUD", d)
        assert looked_up is not None
        assert looked_up.value == Decimal("215.00")


def test_store_manual_rate_sanity_filter(app):
    """A wildly out-of-range rate is NOT cached (sanity guard)."""
    from fx_rate_service import store_manual_rate, get_rate
    with app.app_context():
        d = date(2026, 1, 6)
        # USD sanity range: 250-450. 9999 is way over.
        fx = store_manual_rate("USD", d, Decimal("9999.00"))
        # The FxRate object is returned but cache lookup should NOT find it
        # (because _passes_sanity rejected the write).
        looked_up = get_rate("USD", d)
        assert looked_up is None, "Out-of-range rate should NOT be cached"


def test_ird_defensible_flag():
    from fx_rate_service import FxRate
    cbsl = FxRate("USD", date(2026, 3, 15), Decimal("305"), "cbsl", datetime.utcnow())
    proxy = FxRate("USD", date(2026, 3, 15), Decimal("305"), "ecb_proxy", datetime.utcnow())
    manual = FxRate("USD", date(2026, 3, 15), Decimal("305"), "manual", datetime.utcnow())
    assert cbsl.is_ird_defensible is True
    assert proxy.is_ird_defensible is False
    assert manual.is_ird_defensible is False


def test_label_for_ui():
    from fx_rate_service import FxRate
    cbsl = FxRate("USD", date(2026, 3, 15), Decimal("305"), "cbsl", datetime.utcnow())
    proxy = FxRate("USD", date(2026, 3, 15), Decimal("305"), "ecb_proxy", datetime.utcnow())
    manual = FxRate("USD", date(2026, 3, 15), Decimal("305"), "manual", datetime.utcnow())
    assert "CBSL" in cbsl.label_for_ui
    assert "Proxy" in proxy.label_for_ui or "proxy" in proxy.label_for_ui
    assert "Manual" in manual.label_for_ui


def test_freeze_at_entry_invariant(app, db_session, user_a):
    """A saved RemittanceEntry's cbsl_rate must NOT change when fx_rate_service
    later returns a different value for the same date+currency. (Frozen-at-entry.)"""
    from remittance_models import RemittanceEntry, current_sl_tax_year
    from fx_rate_service import store_manual_rate

    d = date(2026, 2, 20)
    # First: cache a "historical" rate
    store_manual_rate("USD", d, Decimal("300.00"))

    # Create a remittance entry that captures that rate
    e = RemittanceEntry(
        user_id=user_a.id,
        remittance_date=d,
        foreign_currency="USD",
        foreign_amount=Decimal("1000"),
        cbsl_rate=Decimal("300.00"),
        cbsl_rate_source="manual",
        cbsl_rate_captured_at=datetime.utcnow(),
        lkr_amount_cbsl=Decimal("300000.00"),
        tax_year=current_sl_tax_year(d),
    )
    db_session.add(e)
    db_session.commit()
    saved_id = e.id
    saved_rate = e.cbsl_rate

    # Now: someone (or a future job) updates the cached rate for that date
    store_manual_rate("USD", d, Decimal("310.00"))

    # The saved entry's rate MUST be unchanged.
    refreshed = RemittanceEntry.query.get(saved_id)
    assert refreshed.cbsl_rate == saved_rate == Decimal("300.00"), (
        f"Frozen-at-entry invariant violated: saved rate mutated from "
        f"{saved_rate} to {refreshed.cbsl_rate} after cache update."
    )
