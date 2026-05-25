"""tests/tax/test_canonical_models.py — MS2 E.0 / Design Lock 2 schema tests.

11 tests covering the canonical Money value object, ResidencyStatus enum,
DTAA stub, the four new ORM models, the two new User columns, and the
RemittanceEntry → Income backfill.

Run::

    cd C:/Users/mahes/fiesta_phase_a/Developsrilanka_ms2_e0
    python -m pytest tests/tax/test_canonical_models.py -v
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from fiesta.tax.credits import (
    ForeignTaxCredit,
    TreatyArticle,
    apply_foreign_tax_credit,
    dtaa_treaty_lookup,
)
from fiesta.tax.money import Money
from fiesta.tax.residency import ResidencyStatus


# ---------------------------------------------------------------------------
# Money value object
# ---------------------------------------------------------------------------
def test_money_lkr_native_derives_amount_lkr():
    """Money.lkr(amount, fx_date) → amount_lkr == amount quantised to 2dp."""
    m = Money.lkr(Decimal("123456.789"), fx_date=date(2026, 5, 25))
    assert m.currency == "LKR"
    assert m.fx_rate == Decimal("1.0")
    assert m.fx_source == "lkr_native"
    assert m.fx_date == date(2026, 5, 25)
    # 123456.789 * 1.0 -> 123456.79 (quantised)
    assert m.amount_lkr == Decimal("123456.79")


def test_money_foreign_with_fx_rate_derives_amount_lkr():
    """Foreign amount × fx_rate → amount_lkr."""
    # USD 1,000 @ 305.50 LKR/USD = 305,500.00 LKR
    m = Money(
        amount=Decimal("1000"),
        currency="USD",
        fx_rate=Decimal("305.50"),
        fx_source="CBSL",
        fx_date=date(2026, 5, 25),
    )
    assert m.amount_lkr == Decimal("305500.00")
    # frozen — attempting to reassign raises
    with pytest.raises((AttributeError, TypeError)):
        m.amount = Decimal("2000")  # type: ignore[misc]


def test_money_to_dict_roundtrip():
    """to_dict emits all 6 fields as strings (date as ISO)."""
    m = Money(
        amount=Decimal("500.5"),
        currency="GBP",
        fx_rate=Decimal("400.000000"),
        fx_source="bank_statement",
        fx_date=date(2026, 4, 1),
    )
    d = m.to_dict()
    assert d == {
        "amount": "500.5",
        "currency": "GBP",
        "fx_rate": "400.000000",
        "fx_source": "bank_statement",
        "fx_date": "2026-04-01",
        "amount_lkr": "200200.00",
    }
    # All values JSON-safe
    import json
    json.dumps(d)


# ---------------------------------------------------------------------------
# ResidencyStatus enum
# ---------------------------------------------------------------------------
def test_residency_status_enum_has_4_values():
    """Locked vocabulary — 4 members, exact string values."""
    members = {m.name: m.value for m in ResidencyStatus}
    assert members == {
        "RESIDENT": "resident",
        "NRR": "nrr",
        "NONRESIDENT": "nonresident",
        "UNKNOWN": "unknown",
    }
    # str-enum: members compare equal to their string values
    assert ResidencyStatus.UNKNOWN == "unknown"


# ---------------------------------------------------------------------------
# DTAA stub (Wave-X seam)
# ---------------------------------------------------------------------------
def test_dtaa_treaty_lookup_returns_none_pre_wave_x():
    """Stub. Always None for every (country, income_type) pre-Wave-X."""
    assert dtaa_treaty_lookup("US", "rsu") is None
    assert dtaa_treaty_lookup("GB", "foreign_remittance") is None
    assert dtaa_treaty_lookup("AU", "investment_foreign") is None
    # Even invalid codes return None — no exception.
    assert dtaa_treaty_lookup("ZZ", "nonsense") is None


def test_apply_foreign_tax_credit_is_noop_pre_wave_x():
    """No-op: returns (sl_liability_lkr, None) for any source country."""
    # Use a duck-typed dummy income with the two attrs the function reads.
    class _DummyIncome:
        source_country = "US"
        source_type = "rsu"

    net, ftc = apply_foreign_tax_credit(Decimal("150000.00"), _DummyIncome())
    assert net == Decimal("150000.00")
    assert ftc is None

    # source_country=None → also no-op (early return).
    class _DummyDomestic:
        source_country = None
        source_type = "employment_lkr"

    net2, ftc2 = apply_foreign_tax_credit(Decimal("75000.00"), _DummyDomestic())
    assert net2 == Decimal("75000.00")
    assert ftc2 is None

    # Sanity-check that the TreatyArticle + ForeignTaxCredit dataclasses
    # exist with the locked field set (instantiation doesn't raise).
    ta = TreatyArticle(
        country="US",
        article_number="15",
        treaty_year=2002,
        rule_text="Employment income exempt if <183 days",
        full_text_ref="ird.gov.lk/dtaa/US_2002.pdf",
        credit_kind="exemption",
    )
    ForeignTaxCredit(
        treaty_article=ta,
        source_country="US",
        income_type="rsu",
        gross_lkr=Decimal("1000000"),
        credit_lkr=Decimal("150000"),
        rationale="US-SL DTAA Art 15 — employment exemption",
    )


# ---------------------------------------------------------------------------
# Income ORM model (§4)
# ---------------------------------------------------------------------------
def test_income_row_creates_via_orm(session, user):
    """Income row insert + read-back: every locked column is present."""
    from fiesta.tax.models import Income

    i = Income(
        user_id=user.id,
        tax_year="2025/26",
        source_type="foreign_remittance",
        amount=Decimal("1000"),
        currency="USD",
        fx_rate=Decimal("305.50"),
        fx_source="CBSL",
        fx_date=date(2026, 5, 25),
        amount_lkr=Decimal("305500.00"),
        source_country="US",
        evidence_refs=[{"type": "manual_entry", "user_id": user.id}],
    )
    session.add(i)
    session.commit()

    fetched = Income.query.filter_by(user_id=user.id).first()
    assert fetched is not None
    assert fetched.source_type == "foreign_remittance"
    assert Decimal(fetched.amount_lkr) == Decimal("305500.00")
    assert fetched.source_country == "US"
    assert fetched.tax_year == "2025/26"
    assert fetched.evidence_refs == [{"type": "manual_entry", "user_id": user.id}]
    # Nullable FKs default to None
    assert fetched.remittance_id is None
    assert fetched.bank_parse_id is None
    assert fetched.rsu_vesting_id is None
    # Auto timestamps populated
    assert isinstance(fetched.created_at, datetime)
    assert isinstance(fetched.updated_at, datetime)


# ---------------------------------------------------------------------------
# AssetDisposal ORM model (§5)
# ---------------------------------------------------------------------------
def test_asset_disposal_row_creates_via_orm(session, user):
    """AssetDisposal insert + read-back: acquisition + disposal flat columns."""
    from fiesta.tax.models import AssetDisposal

    d = AssetDisposal(
        user_id=user.id,
        tax_year="2025/26",
        asset_type="crypto",
        acq_amount=Decimal("0.5"),
        acq_currency="USD",
        acq_fx_rate=Decimal("295.00"),
        acq_fx_source="CBSL",
        acq_fx_date=date(2024, 6, 1),
        acq_amount_lkr=Decimal("14750.00"),  # 0.5 BTC * 25000 ... not real prices
        disp_amount=Decimal("0.5"),
        disp_currency="USD",
        disp_fx_rate=Decimal("305.50"),
        disp_fx_source="CBSL",
        disp_fx_date=date(2026, 5, 1),
        disp_amount_lkr=Decimal("30550.00"),
        gain_lkr=Decimal("15800.00"),
        acquisition_date=date(2024, 6, 1),
        disposal_date=date(2026, 5, 1),
        source_country="US",
        asset_identifier="BTC",
        evidence_refs=[{"type": "exchange_csv", "ref_id": 99}],
    )
    session.add(d)
    session.commit()

    fetched = AssetDisposal.query.filter_by(user_id=user.id).first()
    assert fetched is not None
    assert fetched.asset_type == "crypto"
    assert fetched.asset_identifier == "BTC"
    assert Decimal(fetched.gain_lkr) == Decimal("15800.00")
    assert fetched.acquisition_date == date(2024, 6, 1)
    assert fetched.disposal_date == date(2026, 5, 1)
    assert fetched.source_country == "US"


# ---------------------------------------------------------------------------
# Remittance → Income backfill (§4 last paragraph)
# ---------------------------------------------------------------------------
def test_remittance_backfill_creates_income_rows(session, user):
    """Create 3 RemittanceEntry rows with mixed completeness + run the
    backfill helper from the migration. Assert: 2 succeed (CBSL or bank
    rate present), 1 is skipped (both NULL).
    """
    from remittance_models import RemittanceEntry
    from fiesta.tax.models import Income

    # Entry 1: full CBSL rate → backfill uses CBSL
    r1 = RemittanceEntry(
        user_id=user.id,
        remittance_date=date(2026, 4, 10),
        foreign_currency="USD",
        foreign_amount=Decimal("1000.00"),
        lkr_amount_cbsl=Decimal("305500.00"),
        cbsl_rate=Decimal("305.500000"),
        cbsl_rate_source="CBSL ref:2026-04-10",
        cbsl_rate_captured_at=datetime.utcnow(),
        source_country="US",
        tax_year="2026-27",
    )
    # Entry 2: bank rate only → backfill falls back to bank rate
    r2 = RemittanceEntry(
        user_id=user.id,
        remittance_date=date(2025, 12, 1),
        foreign_currency="GBP",
        foreign_amount=Decimal("500.00"),
        lkr_amount_bank_rate=Decimal("200000.00"),
        source_country="GB",
        tax_year="2025-26",
    )
    # Entry 3: neither CBSL nor bank rate → backfill skips (income_id stays NULL)
    r3 = RemittanceEntry(
        user_id=user.id,
        remittance_date=date(2025, 11, 15),
        foreign_currency="AUD",
        foreign_amount=Decimal("750.00"),
        source_country="AU",
        tax_year="2025-26",
    )
    session.add_all([r1, r2, r3])
    session.commit()

    # Run the backfill helper directly (don't re-run the table-creation step;
    # that already happened in db.create_all).
    import importlib.util as _u
    from pathlib import Path as _P
    repo_root = _P(__file__).resolve().parents[2]
    spec = _u.spec_from_file_location(
        "_m2_001_backfill",
        str(repo_root / "migrations" / "20260525_130100_e_b8_schema.py"),
    )
    assert spec is not None and spec.loader is not None
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)

    n = mod._backfill_remittance_to_income()
    assert n == 2  # r1 + r2; r3 skipped

    # r1 → Income with CBSL fx_source
    session.refresh(r1)
    session.refresh(r2)
    session.refresh(r3)
    assert r1.income_id is not None
    assert r2.income_id is not None
    assert r3.income_id is None

    inc1 = Income.query.get(r1.income_id)
    assert inc1.source_type == "foreign_remittance"
    assert inc1.fx_source == "CBSL"
    assert Decimal(inc1.amount_lkr) == Decimal("305500.00")
    # Tax year derived from remittance_date (10 April 2026 → "2026/27")
    assert inc1.tax_year == "2026/27"
    assert inc1.source_country == "US"
    assert inc1.remittance_id == r1.id
    # Evidence ref present
    assert inc1.evidence_refs == [
        {"type": "remittance_entry", "ref_id": int(r1.id)}
    ]

    inc2 = Income.query.get(r2.income_id)
    assert inc2.fx_source == "bank_statement"
    assert Decimal(inc2.amount_lkr) == Decimal("200000.00")
    # 1 December 2025 → "2025/26"
    assert inc2.tax_year == "2025/26"
    assert inc2.source_country == "GB"

    # Idempotency: running the backfill again is a no-op
    n2 = mod._backfill_remittance_to_income()
    assert n2 == 0


# ---------------------------------------------------------------------------
# User.residency_status / User.income_sources defaults
# ---------------------------------------------------------------------------
def test_user_residency_status_defaults_to_unknown(session):
    """New User rows get residency_status='unknown' without explicit input."""
    from models import User

    u = User(
        email="pytest_e0_default_residency@fiesta.local",
        name="Pytest Default Residency",
        role="user",
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    assert u.residency_status == "unknown"
    # Defensive: ResidencyStatus enum agrees on the default string
    assert u.residency_status == ResidencyStatus.UNKNOWN.value
    session.delete(u)
    session.commit()


def test_user_income_sources_defaults_to_empty_list(session):
    """New User rows get income_sources=[] without explicit input."""
    from models import User

    u = User(
        email="pytest_e0_default_sources@fiesta.local",
        name="Pytest Default Sources",
        role="user",
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    assert u.income_sources == []
    # And we can append + persist a recognised value.
    u.income_sources = ["foreign_remittance", "rsu"]
    session.commit()
    session.refresh(u)
    assert u.income_sources == ["foreign_remittance", "rsu"]
    session.delete(u)
    session.commit()
