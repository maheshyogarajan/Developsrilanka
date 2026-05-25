"""tests/tax/test_b12_business.py — MS3 Stage E.1 / B12 business-income tests.

9 tests covering:

  1. record_business_income creates BusinessIncomeEntry + paired Income row
  2. record_business_income supports LKR-native (currency='LKR', fx_rate=1)
  3. record_business_income supports foreign currency (USD with FX rate)
  4. add_business_expense subtracts from taxable profit
  5. compute_business_taxable_profit computes LKR profit (gross − expenses)
     with mixed currencies — verifies the LKR-conversion contract
  6. Idempotent — same (user, tax_year, business_name) updates, not duplicates
  7. compute_business_tax integrates into per-user totals + engine buckets
  8. Foreign business invokes the DTAA seam (apply_foreign_tax_credit)
  9. Tax-bill aggregator surfaces the business line + DTAA banner

Run::

    cd C:/Users/mahes/fiesta_phase_a/Developsrilanka_ms3_b12
    python -m pytest tests/tax/test_b12_business.py -v
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from fiesta.tax.money import Money


# ---------------------------------------------------------------------------
# Per-test cleanup + per-test user fixture override.
#
# SQLite in-memory test DB does NOT enforce FK CASCADE (PRAGMA foreign_keys
# is OFF by default), so deleting the User fixture row leaves
# business_income_entries + incomes rows in place. SQLite also reuses
# autoincremented integer PKs after a delete, which means the next test's
# User can inherit a previous test's PK and see leftover business rows.
# _purge_business_rows explicitly purges before each test.
#
# Additionally, the shared `user` fixture in tests/tax/conftest.py hardcodes
# the same email across tests — when a preceding xfail test leaves the user
# row uncleaned (because of unrelated fiesta_profile cascade issues), the
# next user-create hits a UNIQUE(email) violation. We override the user
# fixture here with a per-test-unique email + best-effort teardown.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _purge_business_rows(session):
    """Wipe business + paired income rows for any user id, before each test."""
    from fiesta.tax.business_income import (
        BusinessExpenseEntry,
        BusinessIncomeEntry,
    )
    from fiesta.tax.models import Income

    try:
        BusinessExpenseEntry.query.delete()
        BusinessIncomeEntry.query.delete()
        Income.query.filter(
            Income.source_type.in_(("business_lkr", "business_foreign"))
        ).delete(synchronize_session=False)
        session.commit()
    except Exception:
        session.rollback()
    yield
    try:
        BusinessExpenseEntry.query.delete()
        BusinessIncomeEntry.query.delete()
        Income.query.filter(
            Income.source_type.in_(("business_lkr", "business_foreign"))
        ).delete(synchronize_session=False)
        session.commit()
    except Exception:
        session.rollback()
    # Expire all ORM-tracked objects so the next test starts with a clean
    # identity map — prevents SQLAlchemy from trying to cascade-load
    # relationships (e.g., fiesta_profile) on stale User objects when other
    # test files' fixtures delete users.
    try:
        session.expire_all()
        session.close()
    except Exception:
        pass


@pytest.fixture
def user(session, request):  # noqa: F811 — override shared fixture
    """Per-test User with a UNIQUE email (insulates against leftover rows
    from xfail tests in other files that use the shared email) AND a
    raw-SQL teardown that bypasses ORM cascade-mapping (which can fail when
    fiesta_profile / related tables are not created in the test DB).
    """
    from datetime import datetime, timedelta
    from uuid import uuid4

    from models import User
    from app import db as _db
    from sqlalchemy import text

    u = User(
        email=f"pytest_b12_{uuid4().hex[:10]}@fiesta.local",
        name="Pytest B12",
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
    # Raw-SQL teardown: bypasses SQLAlchemy's ORM cascade machinery so we
    # don't get tripped up by relationships pointing at tables that aren't
    # created in this conftest's lean schema (e.g., fiesta_profile).
    try:
        session.expunge(u)
    except Exception:
        pass
    try:
        _db.session.execute(text("DELETE FROM business_expense_entries WHERE business_income_id IN (SELECT id FROM business_income_entries WHERE user_id = :uid)"), {"uid": uid})
        _db.session.execute(text("DELETE FROM business_income_entries WHERE user_id = :uid"), {"uid": uid})
        _db.session.execute(text("DELETE FROM incomes WHERE user_id = :uid"), {"uid": uid})
        _db.session.execute(text("DELETE FROM user WHERE id = :uid"), {"uid": uid})
        _db.session.commit()
    except Exception:
        _db.session.rollback()


# ---------------------------------------------------------------------------
# 1. record_business_income → BusinessIncomeEntry + Income row
# ---------------------------------------------------------------------------
def test_record_business_income_creates_income_row_and_business_entry(session, user):
    from fiesta.tax.business_income import record_business_income
    from fiesta.tax.models import Income

    money = Money.lkr(amount=Decimal("1500000.00"), fx_date=date(2025, 8, 15))
    entry = record_business_income(
        user=user,
        gross_receipts_money=money,
        business_name="Acme Consulting",
        business_type="sole_prop",
        tax_year="2025/26",
    )

    assert entry.id is not None
    assert entry.business_name == "Acme Consulting"
    assert entry.business_type == "sole_prop"
    assert entry.tax_year == "2025/26"
    assert entry.income_id is not None

    inc = Income.query.get(int(entry.income_id))
    assert inc is not None
    assert inc.source_type == "business_lkr"
    assert inc.user_id == user.id
    assert inc.tax_year == "2025/26"
    assert Decimal(inc.amount_lkr) == Decimal("1500000.00")
    assert inc.currency == "LKR"
    # Evidence ref back-pointer to the BusinessIncomeEntry
    refs = inc.evidence_refs or []
    assert any(
        r.get("type") == "business_income_entry" and int(r.get("ref_id", -1)) == entry.id
        for r in refs
    ), f"expected business_income_entry ref in {refs!r}"
    # FK is set both directions
    assert int(inc.business_income_id) == int(entry.id)


# ---------------------------------------------------------------------------
# 2. LKR-native support
# ---------------------------------------------------------------------------
def test_record_business_income_supports_lkr(session, user):
    from fiesta.tax.business_income import record_business_income
    from fiesta.tax.models import Income

    money = Money(
        amount=Decimal("500000"),
        currency="LKR",
        fx_rate=Decimal("1.0"),
        fx_source="lkr_native",
        fx_date=date(2025, 9, 1),
    )
    entry = record_business_income(
        user=user,
        gross_receipts_money=money,
        business_name="Local Bookstore",
        tax_year="2025/26",
    )
    inc = Income.query.get(int(entry.income_id))
    assert inc.source_type == "business_lkr"
    assert inc.currency == "LKR"
    assert Decimal(inc.fx_rate) == Decimal("1.0")
    assert inc.fx_source == "lkr_native"
    assert Decimal(inc.amount_lkr) == Decimal("500000.00")
    # income_sources auto-populated with 'business_lkr' (and NOT business_foreign)
    sources = list(user.income_sources or [])
    assert "business_lkr" in sources
    assert "business_foreign" not in sources


# ---------------------------------------------------------------------------
# 3. Foreign-currency support — USD with FX rate
# ---------------------------------------------------------------------------
def test_record_business_income_supports_foreign(session, user):
    from fiesta.tax.business_income import record_business_income
    from fiesta.tax.models import Income

    # USD 20,000 at FX 305 → LKR 6,100,000
    money = Money(
        amount=Decimal("20000"),
        currency="USD",
        fx_rate=Decimal("305.00"),
        fx_source="CBSL",
        fx_date=date(2025, 10, 1),
    )
    entry = record_business_income(
        user=user,
        gross_receipts_money=money,
        business_name="Acme US LLC",
        business_type="sole_prop",
        source_country="US",
        tax_year="2025/26",
    )
    assert entry.source_country == "US"
    inc = Income.query.get(int(entry.income_id))
    assert inc.source_type == "business_foreign"
    assert inc.currency == "USD"
    assert Decimal(inc.fx_rate) == Decimal("305.00")
    assert inc.fx_source == "CBSL"
    assert Decimal(inc.amount_lkr) == Decimal("6100000.00")
    assert inc.source_country == "US"
    # income_sources auto-populated with 'business_foreign'
    sources = list(user.income_sources or [])
    assert "business_foreign" in sources


# ---------------------------------------------------------------------------
# 4. Adding an expense subtracts from profit
# ---------------------------------------------------------------------------
def test_add_expense_subtracts_from_profit(session, user):
    from fiesta.tax.business_income import (
        add_business_expense,
        compute_business_taxable_profit_for_entry,
        record_business_income,
    )

    gross = Money.lkr(amount=Decimal("1000000"), fx_date=date(2025, 6, 1))
    entry = record_business_income(
        user=user, gross_receipts_money=gross,
        business_name="Acme Studio", tax_year="2025/26",
    )

    # Before any expenses: profit == gross
    p0 = compute_business_taxable_profit_for_entry(entry)
    assert p0 == Decimal("1000000.00")

    # Add LKR 200,000 rent expense
    add_business_expense(
        business_entry_id=entry.id,
        expense_money=Money.lkr(amount=Decimal("200000"), fx_date=date(2025, 6, 5)),
        category="rent",
        description="Office rent — Q1",
    )
    p1 = compute_business_taxable_profit_for_entry(entry)
    assert p1 == Decimal("800000.00")

    # Add LKR 100,000 utilities expense
    add_business_expense(
        business_entry_id=entry.id,
        expense_money=Money.lkr(amount=Decimal("100000"), fx_date=date(2025, 7, 1)),
        category="utilities",
        description="Electricity + internet Q1",
    )
    p2 = compute_business_taxable_profit_for_entry(entry)
    assert p2 == Decimal("700000.00")


# ---------------------------------------------------------------------------
# 5. compute_business_taxable_profit — foreign gross + LKR expenses
# ---------------------------------------------------------------------------
def test_compute_business_taxable_profit_in_lkr(session, user):
    """Verify the pure function — gross_lkr − sum(expense_lkr), clipped at 0.

    Also verifies the LKR-conversion contract: a foreign-currency gross
    receipts row + LKR-native expenses produce the right LKR profit, since
    the engine sees only LKR amounts (Design Lock 2 §1 amount_lkr is
    derived for both sides).
    """
    from fiesta.tax.business_income import (
        add_business_expense,
        compute_business_taxable_profit,
        compute_business_taxable_profit_for_entry,
        record_business_income,
    )

    # USD 5,000 at FX 300 → LKR 1,500,000 gross
    gross = Money(
        amount=Decimal("5000"), currency="USD",
        fx_rate=Decimal("300.00"), fx_source="CBSL",
        fx_date=date(2025, 5, 1),
    )
    entry = record_business_income(
        user=user, gross_receipts_money=gross,
        business_name="Acme Foreign Inc",
        source_country="US", tax_year="2025/26",
    )

    # LKR 400,000 professional fees
    add_business_expense(
        business_entry_id=entry.id,
        expense_money=Money.lkr(amount=Decimal("400000"), fx_date=date(2025, 5, 10)),
        category="professional_fees",
    )
    # USD 500 at FX 302 → LKR 151,000 bank charges
    add_business_expense(
        business_entry_id=entry.id,
        expense_money=Money(
            amount=Decimal("500"), currency="USD",
            fx_rate=Decimal("302.00"), fx_source="manual",
            fx_date=date(2025, 6, 1),
        ),
        category="bank_charges",
    )

    # Pure function check first
    pure = compute_business_taxable_profit(
        Decimal("1500000"),
        [Decimal("400000"), Decimal("151000")],
    )
    assert pure == Decimal("949000.00")

    # DB-driven function reads the same Decimals and produces the same answer
    db_check = compute_business_taxable_profit_for_entry(entry)
    assert db_check == Decimal("949000.00")

    # Loss-clipped at zero
    zero_clip = compute_business_taxable_profit(
        Decimal("100"), [Decimal("500")],
    )
    assert zero_clip == Decimal("0.00")


# ---------------------------------------------------------------------------
# 6. Idempotency — same (user, tax_year, business_name) updates not duplicates
# ---------------------------------------------------------------------------
def test_idempotent_business_name_same_year_updates_not_duplicates(session, user):
    from fiesta.tax.business_income import (
        BusinessIncomeEntry,
        record_business_income,
    )
    from fiesta.tax.models import Income

    # First save
    e1 = record_business_income(
        user=user,
        gross_receipts_money=Money.lkr(amount=Decimal("500000"), fx_date=date(2025, 4, 30)),
        business_name="Acme Consulting",
        tax_year="2025/26",
    )
    initial_id = e1.id
    initial_income_id = e1.income_id

    # Second save — same name + tax_year, different gross
    e2 = record_business_income(
        user=user,
        gross_receipts_money=Money.lkr(amount=Decimal("750000"), fx_date=date(2025, 6, 30)),
        business_name="Acme Consulting",
        tax_year="2025/26",
    )

    # Same row id, same income_id — overwrite, not insert
    assert e2.id == initial_id
    assert e2.income_id == initial_income_id

    # DB-level: still only ONE BusinessIncomeEntry for this combo
    rows = (
        BusinessIncomeEntry.query
        .filter_by(user_id=user.id, tax_year="2025/26", business_name="Acme Consulting")
        .all()
    )
    assert len(rows) == 1

    # Income row's amount was overwritten
    inc = Income.query.get(int(e2.income_id))
    assert Decimal(inc.amount_lkr) == Decimal("750000.00")

    # Different name, same year → NEW row
    e3 = record_business_income(
        user=user,
        gross_receipts_money=Money.lkr(amount=Decimal("250000"), fx_date=date(2025, 7, 1)),
        business_name="Acme Trading",  # different name
        tax_year="2025/26",
    )
    assert e3.id != initial_id

    # Case-insensitive de-duplication: 'acme consulting' matches 'Acme Consulting'
    e4 = record_business_income(
        user=user,
        gross_receipts_money=Money.lkr(amount=Decimal("900000"), fx_date=date(2025, 8, 1)),
        business_name="acme consulting",
        tax_year="2025/26",
    )
    assert e4.id == initial_id

    # income_sources contains 'business_lkr' exactly once
    sources = list(user.income_sources or [])
    assert sources.count("business_lkr") == 1


# ---------------------------------------------------------------------------
# 7. compute_business_tax — per-user totals + bucket split for the engine
# ---------------------------------------------------------------------------
def test_business_income_in_compute_business_tax(session, user):
    from fiesta.tax.business_income import (
        add_business_expense,
        compute_business_tax,
        record_business_income,
    )

    # LKR business: gross 800K − expenses 100K = profit 700K
    e_lkr = record_business_income(
        user=user,
        gross_receipts_money=Money.lkr(amount=Decimal("800000"), fx_date=date(2025, 5, 1)),
        business_name="Local Studio",
        tax_year="2025/26",
    )
    add_business_expense(
        business_entry_id=e_lkr.id,
        expense_money=Money.lkr(amount=Decimal("100000"), fx_date=date(2025, 5, 15)),
        category="rent",
    )

    # Foreign business: USD 10000 @ 300 = LKR 3,000,000 gross
    # − USD 500 @ 300 = LKR 150,000 expenses → profit LKR 2,850,000
    e_for = record_business_income(
        user=user,
        gross_receipts_money=Money(
            amount=Decimal("10000"), currency="USD",
            fx_rate=Decimal("300.00"), fx_source="CBSL",
            fx_date=date(2025, 9, 1),
        ),
        business_name="Acme US",
        source_country="US",
        tax_year="2025/26",
    )
    add_business_expense(
        business_entry_id=e_for.id,
        expense_money=Money(
            amount=Decimal("500"), currency="USD",
            fx_rate=Decimal("300.00"), fx_source="CBSL",
            fx_date=date(2025, 9, 10),
        ),
        category="professional_fees",
    )

    result = compute_business_tax(user, "2025/26")
    assert result["tax_year"] == "2025/26"
    assert len(result["businesses"]) == 2

    # Totals
    assert result["gross_total_lkr"] == Decimal("3800000.00")  # 800K + 3M
    assert result["expenses_total_lkr"] == Decimal("250000.00")  # 100K + 150K
    assert result["taxable_profit_total_lkr"] == Decimal("3550000.00")  # 700K + 2.85M

    # Bucket split for engine routing
    assert result["lkr_taxable_profit_lkr"] == Decimal("700000.00")
    assert result["foreign_taxable_profit_lkr"] == Decimal("2850000.00")

    # DTAA banner true because we have at least one foreign-source business
    assert result["dtaa_deferred"] is True


# ---------------------------------------------------------------------------
# 8. Foreign business invokes the DTAA seam
# ---------------------------------------------------------------------------
def test_foreign_business_calls_dtaa_stub(session, user):
    """Verify the DTAA seam (apply_foreign_tax_credit) is invoked for every
    foreign-source row. Pre-Wave-X the stub returns (liability, None) so no
    credit is applied — but the call site IS the seam Wave-X drops into.
    """
    from fiesta.tax import business_income as biz
    from fiesta.tax.business_income import (
        compute_business_tax,
        record_business_income,
    )

    # Two foreign businesses + one LKR business → DTAA seam called twice
    for name, country in [("Foreign A", "US"), ("Foreign B", "GB")]:
        record_business_income(
            user=user,
            gross_receipts_money=Money(
                amount=Decimal("1000"), currency="USD",
                fx_rate=Decimal("300.00"), fx_source="CBSL",
                fx_date=date(2025, 7, 1),
            ),
            business_name=name,
            source_country=country,
            tax_year="2025/26",
        )
    record_business_income(
        user=user,
        gross_receipts_money=Money.lkr(amount=Decimal("100000"), fx_date=date(2025, 7, 1)),
        business_name="Local Z",
        tax_year="2025/26",
    )

    with patch.object(biz, "apply_foreign_tax_credit",
                      wraps=biz.apply_foreign_tax_credit) as spy:
        result = compute_business_tax(user, "2025/26")
        # Two foreign businesses → two DTAA-seam calls; LKR business is skipped
        assert spy.call_count == 2
        # Pre-Wave-X stub → no credits captured
        assert result["dtaa_credits"] == []
        # But the banner flag is True
        assert result["dtaa_deferred"] is True

        # The arg-types pattern: (Decimal liability, Income row)
        from fiesta.tax.models import Income
        for call_args in spy.call_args_list:
            args, _ = call_args
            assert isinstance(args[0], Decimal)
            assert isinstance(args[1], Income)
            assert args[1].source_type == "business_foreign"


# ---------------------------------------------------------------------------
# 9. Tax-bill aggregator surfaces business lines + DTAA banner
# ---------------------------------------------------------------------------
def test_dtaa_banner_renders_for_foreign_business_on_tax_bill(session, user):
    """Verify the aggregator loads business lines + sets the DTAA banner.

    Also verifies the engine-bucket routing: LKR taxable profit goes to
    business_lkr, foreign taxable profit goes to foreign_lkr (25/26
    dual-track foreign cap).
    """
    from fiesta.tax.business_income import (
        add_business_expense,
        record_business_income,
    )
    from fiesta.tax_bill.aggregator import assemble_tax_inputs

    # LKR business: gross 600K, no expenses → profit 600K
    e_lkr = record_business_income(
        user=user,
        gross_receipts_money=Money.lkr(amount=Decimal("600000"), fx_date=date(2025, 4, 15)),
        business_name="Local A",
        tax_year="2025/26",
    )

    # Foreign business: USD 2,000 @ 305 = LKR 610,000 gross, no expenses
    e_for = record_business_income(
        user=user,
        gross_receipts_money=Money(
            amount=Decimal("2000"), currency="USD",
            fx_rate=Decimal("305.00"), fx_source="CBSL",
            fx_date=date(2025, 9, 30),
        ),
        business_name="Foreign B",
        source_country="GB",
        tax_year="2025/26",
    )
    add_business_expense(
        business_entry_id=e_for.id,
        expense_money=Money.lkr(amount=Decimal("50000"), fx_date=date(2025, 10, 1)),
        category="equipment",
    )

    inputs = assemble_tax_inputs(user.id, "2025-26")
    assert "fiesta.tax.business_income" in inputs.sources_loaded
    assert len(inputs.business_lines) == 2
    # Totals
    assert inputs.business_gross_total_lkr == Decimal("1210000.00")  # 600K + 610K
    assert inputs.business_expenses_total_lkr == Decimal("50000.00")
    assert inputs.business_taxable_profit_total_lkr == Decimal("1160000.00")
    # DTAA banner true (foreign business present)
    assert inputs.business_dtaa_deferred is True
    # Engine-bucket routing
    assert inputs.business_lkr_taxable_profit_lkr == Decimal("600000.00")
    assert inputs.business_foreign_taxable_profit_lkr == Decimal("560000.00")  # 610K − 50K
    # Engine income kwargs are wired:
    assert inputs.engine_income_kwargs["business_lkr"] >= Decimal("600000.00")
    assert inputs.engine_income_kwargs["foreign_lkr"] >= Decimal("560000.00")
    # Synthetic categories surfaced for the UI breakdown
    assert inputs.income_by_category_lkr.get("business_lkr") == Decimal("600000.00")
    assert inputs.income_by_category_lkr.get("business_foreign") == Decimal("560000.00")
