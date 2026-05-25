"""tests/tax/test_g3_rental_lkr.py — MS4 W3c / G3.4 LKR rental tests.

6 tests covering:

  1. record_rental_income creates RentalIncomeEntry + paired Income row
  2. Non-LKR currency is rejected (LOCAL module only)
  3. record_rental_deduction subtracts from taxable income
  4. compute_rental_taxable_income — pure function clipped at zero
  5. Idempotent — same (user, year, address, period_start) updates not duplicates
  6. compute_rental_lkr_tax_year aggregates into per-user totals

Run::

    cd C:/Users/mahes/fiesta_phase_a/Developsrilanka_ms4_w3c
    python -m pytest tests/tax/test_g3_rental_lkr.py -v
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from fiesta.tax.money import Money


# ---------------------------------------------------------------------------
# Per-test cleanup + per-test user override (mirrors test_b12_business pattern)
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _purge_rental_rows(session):
    from fiesta.tax.rental_lkr import (
        RentalDeductionEntry,
        RentalIncomeEntry,
    )
    from fiesta.tax.models import Income
    try:
        RentalDeductionEntry.query.delete()
        RentalIncomeEntry.query.delete()
        Income.query.filter(Income.source_type == "rental_lkr").delete(
            synchronize_session=False,
        )
        session.commit()
    except Exception:
        session.rollback()
    yield
    try:
        RentalDeductionEntry.query.delete()
        RentalIncomeEntry.query.delete()
        Income.query.filter(Income.source_type == "rental_lkr").delete(
            synchronize_session=False,
        )
        session.commit()
    except Exception:
        session.rollback()
    try:
        session.expire_all()
        session.close()
    except Exception:
        pass


@pytest.fixture
def user(session):  # noqa: F811 — override shared fixture
    from datetime import datetime, timedelta
    from models import User
    from app import db as _db
    from sqlalchemy import text

    u = User(
        email=f"pytest_g34_{uuid4().hex[:10]}@fiesta.local",
        name="Pytest G3.4",
        role="user",
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
    )
    session.add(u)
    session.commit()
    uid = int(u.id)
    yield u
    try:
        session.expunge(u)
    except Exception:
        pass
    try:
        _db.session.execute(text(
            "DELETE FROM rental_deduction_entries WHERE rental_income_id IN "
            "(SELECT id FROM rental_income_entries WHERE user_id = :uid)"
        ), {"uid": uid})
        _db.session.execute(text(
            "DELETE FROM rental_income_entries WHERE user_id = :uid"
        ), {"uid": uid})
        _db.session.execute(text(
            "DELETE FROM incomes WHERE user_id = :uid"
        ), {"uid": uid})
        _db.session.execute(text(
            "DELETE FROM user WHERE id = :uid"
        ), {"uid": uid})
        _db.session.commit()
    except Exception:
        _db.session.rollback()


# ---------------------------------------------------------------------------
# 1. record_rental_income creates RentalIncomeEntry + Income row
# ---------------------------------------------------------------------------
def test_record_rental_income_creates_entry_and_income_row(session, user):
    from fiesta.tax.rental_lkr import record_rental_income
    from fiesta.tax.models import Income

    money = Money.lkr(amount=Decimal("150000.00"), fx_date=date(2025, 8, 1))
    entry = record_rental_income(
        user=user,
        property_address="12 Main St, Colombo",
        gross_rent_money=money,
        tenant_name="Asanka Perera",
        period_start=date(2025, 8, 1),
        period_end=date(2026, 1, 31),
        tax_year="2025/26",
    )

    assert entry.id is not None
    assert entry.property_address == "12 Main St, Colombo"
    assert entry.tenant_name == "Asanka Perera"
    assert entry.tax_year == "2025/26"
    assert entry.income_id is not None
    assert entry.source_country == "LK"

    inc = Income.query.get(int(entry.income_id))
    assert inc is not None
    assert inc.source_type == "rental_lkr"
    assert inc.user_id == user.id
    assert inc.currency == "LKR"
    assert Decimal(inc.amount_lkr) == Decimal("150000.00")
    assert inc.source_country == "LK"
    # Evidence ref back-pointer to the RentalIncomeEntry
    refs = inc.evidence_refs or []
    assert any(
        r.get("type") == "rental_income_entry"
        and int(r.get("ref_id", -1)) == entry.id
        for r in refs
    )
    # user.income_sources auto-populated with 'rental_lkr'
    sources = list(user.income_sources or [])
    assert "rental_lkr" in sources


# ---------------------------------------------------------------------------
# 2. Non-LKR currency rejected (LOCAL module only)
# ---------------------------------------------------------------------------
def test_non_lkr_currency_rejected(session, user):
    from fiesta.tax.rental_lkr import record_rental_income

    usd_money = Money(
        amount=Decimal("500"), currency="USD",
        fx_rate=Decimal("302.00"), fx_source="CBSL",
        fx_date=date(2025, 9, 1),
    )
    with pytest.raises(ValueError, match="rental_lkr engine only accepts LKR"):
        record_rental_income(
            user=user,
            property_address="42 Galle Rd, Colombo",
            gross_rent_money=usd_money,
            period_start=date(2025, 9, 1),
            tax_year="2025/26",
        )


# ---------------------------------------------------------------------------
# 3. Adding deductions subtracts from taxable income
# ---------------------------------------------------------------------------
def test_record_rental_deduction_subtracts_from_taxable_income(session, user):
    from fiesta.tax.rental_lkr import (
        compute_rental_taxable_income_for_entry,
        record_rental_deduction,
        record_rental_income,
    )

    gross = Money.lkr(amount=Decimal("600000"), fx_date=date(2025, 6, 1))
    entry = record_rental_income(
        user=user,
        property_address="55 Lake Crescent",
        gross_rent_money=gross,
        period_start=date(2025, 6, 1),
        tax_year="2025/26",
    )

    # Before any deductions: net = gross
    p0 = compute_rental_taxable_income_for_entry(entry)
    assert p0 == Decimal("600000.00")

    # Add LKR 50,000 repair
    record_rental_deduction(
        user=user,
        rental_income_id=entry.id,
        category="repairs",
        amount_money=Money.lkr(amount=Decimal("50000"), fx_date=date(2025, 6, 15)),
        description="Roof repair after monsoon",
    )
    p1 = compute_rental_taxable_income_for_entry(entry)
    assert p1 == Decimal("550000.00")

    # Add LKR 80,000 mortgage interest
    record_rental_deduction(
        user=user,
        rental_income_id=entry.id,
        category="mortgage_interest",
        amount_money=Money.lkr(amount=Decimal("80000"), fx_date=date(2025, 7, 1)),
        description="HDFC mortgage interest H1",
    )
    p2 = compute_rental_taxable_income_for_entry(entry)
    assert p2 == Decimal("470000.00")


# ---------------------------------------------------------------------------
# 4. Pure-function compute_rental_taxable_income
# ---------------------------------------------------------------------------
def test_pure_function_rental_taxable_income_clipped_at_zero():
    from fiesta.tax.rental_lkr import compute_rental_taxable_income

    # Profit
    assert compute_rental_taxable_income(
        Decimal("100000"), [Decimal("30000"), Decimal("20000")],
    ) == Decimal("50000.00")
    # Zero
    assert compute_rental_taxable_income(
        Decimal("100"), [Decimal("100")],
    ) == Decimal("0.00")
    # Loss → clipped
    assert compute_rental_taxable_income(
        Decimal("100"), [Decimal("500")],
    ) == Decimal("0.00")


# ---------------------------------------------------------------------------
# 5. Idempotent — same (user, year, address, period_start) updates not dupes
# ---------------------------------------------------------------------------
def test_idempotent_record_rental_income(session, user):
    from fiesta.tax.rental_lkr import (
        RentalIncomeEntry,
        record_rental_income,
    )
    from fiesta.tax.models import Income

    e1 = record_rental_income(
        user=user,
        property_address="7B Park Lane",
        gross_rent_money=Money.lkr(amount=Decimal("80000"), fx_date=date(2025, 4, 1)),
        period_start=date(2025, 4, 1),
        tax_year="2025/26",
    )
    initial_id = e1.id
    initial_income_id = e1.income_id

    # Second save — same (year, address, period_start), different gross
    e2 = record_rental_income(
        user=user,
        property_address="7B Park Lane",
        gross_rent_money=Money.lkr(amount=Decimal("120000"), fx_date=date(2025, 5, 1)),
        period_start=date(2025, 4, 1),
        tax_year="2025/26",
    )
    assert e2.id == initial_id
    assert e2.income_id == initial_income_id

    rows = (
        RentalIncomeEntry.query
        .filter_by(
            user_id=user.id, tax_year="2025/26",
            property_address="7B Park Lane", period_start=date(2025, 4, 1),
        )
        .all()
    )
    assert len(rows) == 1

    inc = Income.query.get(int(e2.income_id))
    assert Decimal(inc.amount_lkr) == Decimal("120000.00")

    # Different period_start → NEW row (mid-year tenant change scenario)
    e3 = record_rental_income(
        user=user,
        property_address="7B Park Lane",
        gross_rent_money=Money.lkr(amount=Decimal("100000"), fx_date=date(2025, 10, 1)),
        period_start=date(2025, 10, 1),
        tax_year="2025/26",
    )
    assert e3.id != initial_id

    # Case-insensitive on address: '7b park lane' matches '7B Park Lane'
    e4 = record_rental_income(
        user=user,
        property_address="7b park lane",
        gross_rent_money=Money.lkr(amount=Decimal("130000"), fx_date=date(2025, 5, 15)),
        period_start=date(2025, 4, 1),
        tax_year="2025/26",
    )
    assert e4.id == initial_id

    # income_sources contains 'rental_lkr' exactly once
    sources = list(user.income_sources or [])
    assert sources.count("rental_lkr") == 1


# ---------------------------------------------------------------------------
# 6. compute_rental_lkr_tax_year aggregates totals + flags LK-only
# ---------------------------------------------------------------------------
def test_compute_rental_lkr_tax_year_aggregates(session, user):
    from fiesta.tax.rental_lkr import (
        compute_rental_lkr_tax_year,
        record_rental_deduction,
        record_rental_income,
    )

    # Property 1: 500K gross − 100K deductions = 400K net
    e1 = record_rental_income(
        user=user,
        property_address="P1 Address",
        gross_rent_money=Money.lkr(amount=Decimal("500000"), fx_date=date(2025, 4, 1)),
        period_start=date(2025, 4, 1),
        tax_year="2025/26",
    )
    record_rental_deduction(
        user=user, rental_income_id=e1.id, category="repairs",
        amount_money=Money.lkr(amount=Decimal("100000"), fx_date=date(2025, 5, 1)),
    )

    # Property 2: 300K gross − 50K agent fees = 250K net
    e2 = record_rental_income(
        user=user,
        property_address="P2 Address",
        gross_rent_money=Money.lkr(amount=Decimal("300000"), fx_date=date(2025, 8, 1)),
        period_start=date(2025, 8, 1),
        tax_year="2025/26",
    )
    record_rental_deduction(
        user=user, rental_income_id=e2.id, category="agent_fees",
        amount_money=Money.lkr(amount=Decimal("50000"), fx_date=date(2025, 9, 1)),
    )

    result = compute_rental_lkr_tax_year(user, "2025/26")

    assert result["tax_year"] == "2025/26"
    assert len(result["rentals"]) == 2
    assert result["gross_total_lkr"] == Decimal("800000.00")
    assert result["deductions_total_lkr"] == Decimal("150000.00")
    assert result["taxable_income_total_lkr"] == Decimal("650000.00")
    assert result["lkr_taxable_income_lkr"] == Decimal("650000.00")
    # LOCAL module — DTAA always False, no foreign credits.
    assert result["dtaa_deferred"] is False
    assert result["dtaa_credits"] == []


# ---------------------------------------------------------------------------
# 7. Tax-bill aggregator surfaces rental lines + engine bucket wiring
# ---------------------------------------------------------------------------
@pytest.mark.xfail(
    reason=(
        "Aggregator integration test — depends on full Flask DB schema with "
        "all upstream loader tables (profile/deductions/property/etc.) "
        "available; conftest SQLite test DB only creates the canonical-models "
        "subset. Mirrors test_b12_business test 9's xfail pattern. The "
        "aggregator loader + engine-bucket wiring is exercised end-to-end "
        "via assemble_tax_inputs in the integration suite."
    ),
    strict=False,
)
def test_rental_lines_surface_in_aggregator(session, user):
    from fiesta.tax.rental_lkr import record_rental_income
    from fiesta.tax_bill.aggregator import assemble_tax_inputs

    record_rental_income(
        user=user,
        property_address="Aggregator Test",
        gross_rent_money=Money.lkr(amount=Decimal("200000"), fx_date=date(2025, 7, 1)),
        period_start=date(2025, 7, 1),
        tax_year="2025/26",
    )

    inputs = assemble_tax_inputs(user.id, "2025-26")
    assert "fiesta.tax.rental_lkr" in inputs.sources_loaded
    assert len(inputs.rental_lkr_lines) == 1
    assert inputs.rental_lkr_gross_total_lkr == Decimal("200000.00")
    assert inputs.rental_lkr_taxable_income_total_lkr == Decimal("200000.00")
    # Engine bucket wired to rental_lkr
    assert inputs.engine_income_kwargs.get("rental_lkr") >= Decimal("200000.00")
