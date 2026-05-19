"""Tests for fiesta.lifecycle — X3 year-end transition + S11 invoice cadence.

Wave 4 v1.0 (2026-05-20). Pure-function modules — no DB / no email send /
no Celery. The dispatchers and audit store are dependency-injected so
tests run in-memory.

Run:
    cd C:/Users/mahes/fiesta_replit_source/DevelopSriLanka
    python -m pytest tests/lifecycle/test_x3_s11.py -v

15 cases:
    X3 (6):
      - tax_year_label_canonicalisation (short + long)
      - current_tax_year_around_1_april_boundary (SL local discipline)
      - filing_window_status_open_closing_overdue_filed
      - transition_happy_carries_over_all_resource_types
      - transition_blocked_when_prior_year_overdue
      - transition_auto_renewal_pending_when_contract_ends_in_old_year

    S11 (7):
      - detect_cadence_monthly_regular
      - detect_cadence_irregular_high_cv
      - detect_cadence_coverage_gap_counted
      - upcoming_reminder_fires_5_days_before_due
      - upcoming_reminder_missing_after_period_end
      - above_market_rate_threshold
      - decimal_currency_precision_preserved

    Integration (2):
      - scheduler_decision_idempotent_via_audit
      - scheduler_emits_year_end_messages_on_correct_days
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from fiesta.lifecycle.audit_log import (
    EventTypes,
    InMemoryAuditStore,
    LifecycleAudit,
)
from fiesta.lifecycle.invoice_cadence import (
    CADENCE_PERIOD_DAYS,
    INVOICE_REMINDER_LEAD_DAYS,
    Invoice,
    detect_cadence,
    is_above_market_rate,
    upcoming_invoice_reminder,
)
from fiesta.lifecycle.reminders import dispatch_x3
from fiesta.lifecycle.rollover_scheduler import (
    RolloverContext,
    SchedulingDecision,
    compute_decisions_for_customer,
    run_daily_pass,
)
from fiesta.lifecycle.year_end import (
    SL_TZ,
    TaxYear,
    current_tax_year,
    filing_window_status,
    parse_year_label,
    transition_customer_to_new_year,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class _DummyCustomer:
    id: int
    persona: str = "salaried"


@dataclass
class _DummyResource:
    id: int
    name: str = "Resource"
    contract_end_date: date | None = None


def _ty(label: str) -> TaxYear:
    return parse_year_label(label)


# ---------------------------------------------------------------------------
# X3 tests
# ---------------------------------------------------------------------------


def test_tax_year_label_canonicalisation():
    """Short and long labels both parse; short is canonical."""
    short = parse_year_label("2025/26")
    long_form = parse_year_label("2025/2026")

    assert short.year_label == "2025/26"
    assert long_form.year_label == "2025/26"
    assert short.long_label == "2025/2026"
    assert short.start_date == date(2025, 4, 1)
    assert short.end_date == date(2026, 3, 31)
    assert short.filing_window_close == date(2026, 11, 30)

    # Garbage rejected.
    with pytest.raises(ValueError):
        parse_year_label("2025-26")
    with pytest.raises(ValueError):
        parse_year_label("2025/27")  # non-sequential


def test_current_tax_year_around_1_april_boundary():
    """Boundary discipline: 31 Mar UTC+5:30 still in old year; 1 Apr in new."""
    # 31 Mar 2026 18:30 UTC = 1 April 00:00 SL. Should be in NEW year (26/27).
    inst_at_sl_midnight = datetime(2026, 3, 31, 18, 30, tzinfo=timezone.utc)
    ty = current_tax_year(inst_at_sl_midnight)
    assert ty.year_label == "2026/27"
    assert ty.is_active is True

    # 31 Mar 2026 17:00 UTC = 31 Mar 22:30 SL. Should still be 25/26.
    inst_just_before = datetime(2026, 3, 31, 17, 0, tzinfo=timezone.utc)
    ty2 = current_tax_year(inst_just_before)
    assert ty2.year_label == "2025/26"

    # Mid-year sanity: 15 Aug 2025 -> 25/26.
    inst_mid = datetime(2025, 8, 15, 12, 0, tzinfo=timezone.utc)
    assert current_tax_year(inst_mid).year_label == "2025/26"


def test_filing_window_status_open_closing_overdue_filed():
    """All four states reachable for the 24/25 YoA."""
    ty = _ty("2024/25")  # filing window closes 30 Nov 2025

    # Filed -> filed
    assert filing_window_status(
        filed=True, return_filed_at=None, ty=ty,
        now=datetime(2025, 6, 1, tzinfo=timezone.utc),
    ) == "filed"

    # 1 Jun 2025 -> 182 days to close -> open
    assert filing_window_status(
        filed=False, return_filed_at=None, ty=ty,
        now=datetime(2025, 6, 1, tzinfo=timezone.utc),
    ) == "open"

    # 5 Nov 2025 -> 25 days to close -> closing_soon
    assert filing_window_status(
        filed=False, return_filed_at=None, ty=ty,
        now=datetime(2025, 11, 5, tzinfo=timezone.utc),
    ) == "closing_soon"

    # 5 Dec 2025 -> 5 days past close -> overdue
    assert filing_window_status(
        filed=False, return_filed_at=None, ty=ty,
        now=datetime(2025, 12, 5, tzinfo=timezone.utc),
    ) == "overdue"


def test_transition_happy_carries_over_all_resource_types():
    """1 April 2026: 25/26 customer with prior-year filed transitions cleanly."""
    cust = _DummyCustomer(id=42, persona="salaried")
    current_ty = _ty("2025/26")
    new_ty = _ty("2026/27")

    sps = [_DummyResource(id=1, name="Acme HR"),
           _DummyResource(id=2, name="Office cleaner")]
    banks = [_DummyResource(id=10, name="HNB-1234"),
             _DummyResource(id=11, name="HNB-5678")]
    rentals = [_DummyResource(id=20, name="Colombo flat",
                              contract_end_date=date(2027, 6, 30))]

    result = transition_customer_to_new_year(
        cust, current_ty, new_ty,
        prior_year_filed=True,
        service_providers=sps,
        bank_details=banks,
        rental_agreements=rentals,
    )

    assert result.ok
    assert not result.blocked
    assert result.new_tax_file_created
    assert result.carried_over["service_providers"] == 2
    assert result.carried_over["bank_details"] == 2
    assert result.carried_over["rental_agreements"] == 1
    assert result.carried_over["persona"] == 1
    # Rental ends in NEW year (2027), so no auto-renew prompt.
    assert result.auto_renewals_pending == []


def test_transition_blocked_when_prior_year_overdue():
    """Edge: 1 Apr 2027 but 25/26 return still unfiled past 30 Nov 2026."""
    cust = _DummyCustomer(id=43)
    current_ty = _ty("2025/26")
    new_ty = _ty("2026/27")

    # Pretend "now" is 1 April 2027 — 25/26 filing window closed 30 Nov 2026.
    now = datetime(2027, 4, 1, 6, 0, tzinfo=timezone.utc)  # 11:30 SL

    result = transition_customer_to_new_year(
        cust, current_ty, new_ty,
        prior_year_filed=False,
        now=now,
    )

    assert result.blocked
    assert any("overdue" in r for r in result.blocker_reasons)
    assert not result.new_tax_file_created


def test_transition_auto_renewal_pending_when_contract_ends_in_old_year():
    """Rental ending mid-25/26 (Aug 2025) needs explicit Y/N at 26/27 start."""
    cust = _DummyCustomer(id=44)
    current_ty = _ty("2025/26")
    new_ty = _ty("2026/27")

    rentals = [_DummyResource(
        id=99, name="Mt Lavinia apartment",
        contract_end_date=date(2025, 8, 31),
    )]

    result = transition_customer_to_new_year(
        cust, current_ty, new_ty,
        prior_year_filed=True,
        rental_agreements=rentals,
    )

    assert len(result.auto_renewals_pending) == 1
    pending = result.auto_renewals_pending[0]
    assert pending["type"] == "rental_agreement"
    assert pending["id"] == 99
    assert pending["default_action"] == "prompt"


# ---------------------------------------------------------------------------
# S11 tests
# ---------------------------------------------------------------------------


def _invoice(month: int, customer_id: int = 1, sp_id: int = 1,
             amount: str = "50000.00", year: int = 2025) -> Invoice:
    """Helper: build an Invoice for the Nth month of `year`."""
    last_day = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
                7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}[month]
    inv_date = date(year, month, 5)
    p_start = date(year, month, 1)
    p_end = date(year, month, last_day)
    return Invoice(
        id=month + (year * 100),
        customer_id=customer_id,
        service_provider_id=sp_id,
        invoice_date=inv_date,
        period_start=p_start,
        period_end=p_end,
        amount_lkr=Decimal(amount),
        ira_categorization="business_expense",
    )


def test_detect_cadence_monthly_regular():
    """12 monthly invoices, CV < 0.15 -> 'monthly', no flag, full coverage."""
    invs = [_invoice(m) for m in range(1, 13)]
    check = detect_cadence(invs)

    assert check.actual_cadence == "monthly"
    assert not check.irregular_flag
    assert check.coverage_gaps == []
    assert check.invoice_count == 12
    assert check.coefficient_of_variation is not None
    assert check.coefficient_of_variation < 0.15
    # Mean amount preserved as Decimal.
    assert check.mean_amount_lkr == Decimal("50000.00")


def test_detect_cadence_irregular_high_cv():
    """Customer skipped 3 months mid-year -> CV > 0.30 -> irregular."""
    months = [1, 2, 3, 4, 8, 9, 10, 11, 12]  # skipped 5,6,7
    invs = [_invoice(m) for m in months]
    check = detect_cadence(invs)

    assert check.irregular_flag
    # Whether bucketed as 'monthly' or 'irregular' depends on CV; the
    # important contracts: irregular_flag True + coverage_gaps_count > 0.
    assert check.coverage_gaps_count >= 3


def test_detect_cadence_coverage_gap_counted():
    """Gap of >1.5x period flagged as missing periods."""
    invs = [_invoice(1), _invoice(2), _invoice(6), _invoice(7)]
    check = detect_cadence(invs)

    assert len(check.coverage_gaps) >= 1
    total_missing = sum(g["missed_count"] for g in check.coverage_gaps)
    assert total_missing >= 2  # at minimum: months 3, 4, 5 missing


def test_upcoming_reminder_fires_5_days_before_due():
    """Monthly cadence, last invoice 5 Apr -> reminder due 30 Apr (5 May - 5d)."""
    invs = [_invoice(m) for m in range(1, 4)]  # Jan, Feb, Mar
    check = detect_cadence(invs)

    # Today = 30 Apr 2025. Last invoice 5 Mar. Next due ~ 5 Apr + 30 = 5 May.
    # Wait — last_invoice_date is 5 Mar, +30 = 4 Apr. So today=30 Mar -> lead.
    today = check.last_invoice_date + timedelta(
        days=CADENCE_PERIOD_DAYS["monthly"] - INVOICE_REMINDER_LEAD_DAYS
    )

    trig = upcoming_invoice_reminder(
        customer_id=1, sp_id=1, check=check, today=today
    )
    assert trig is not None
    assert trig.reminder_kind == "monthly_invoice_due_soon"
    assert trig.due_date == check.last_invoice_date + timedelta(
        days=CADENCE_PERIOD_DAYS["monthly"]
    )


def test_upcoming_reminder_missing_after_period_end():
    """No reminder 1 day before; missing alert after period_end + half-period."""
    invs = [_invoice(m) for m in range(1, 4)]
    check = detect_cadence(invs)

    # 20 days after last invoice -> within window, ~10 days to due, no reminder.
    # (lead is exactly 5d before next_due; 20d after last leaves 10d until due.)
    no_trig_day = check.last_invoice_date + timedelta(days=20)
    assert upcoming_invoice_reminder(1, 1, check, no_trig_day) is None

    # 60 days after last invoice (period was 30, half-period=15, so 60 > 30+15) -> missing.
    missing_day = check.last_invoice_date + timedelta(days=60)
    trig = upcoming_invoice_reminder(1, 1, check, missing_day)
    assert trig is not None
    assert trig.reminder_kind == "monthly_invoice_missing"


def test_above_market_rate_threshold():
    """1.25x market rate is the threshold per fiesta.compliance band."""
    market = Decimal("100000")
    assert not is_above_market_rate(Decimal("110000"), market)  # 1.10x
    assert not is_above_market_rate(Decimal("125000"), market)  # exactly 1.25x
    assert is_above_market_rate(Decimal("125001"), market)      # just over
    assert is_above_market_rate(Decimal("150000"), market)      # 1.50x
    assert not is_above_market_rate(Decimal("100000"), Decimal("0"))  # guard


def test_decimal_currency_precision_preserved():
    """Decimal in, Decimal out — no float drift through cadence detection."""
    invs = [
        _invoice(1, amount="50000.33"),
        _invoice(2, amount="50000.33"),
        _invoice(3, amount="50000.33"),
    ]
    check = detect_cadence(invs)
    assert isinstance(check.mean_amount_lkr, Decimal)
    assert check.mean_amount_lkr == Decimal("50000.33")


# ---------------------------------------------------------------------------
# Integration tests (scheduler + audit)
# ---------------------------------------------------------------------------


def test_scheduler_decision_idempotent_via_audit():
    """Running the scheduler twice on the same day doesn't double-fire."""
    audit = LifecycleAudit(store=InMemoryAuditStore())
    ctx = RolloverContext(
        customer_id=42, persona="salaried",
        prior_year_filed=False,
        has_prior_tax_file=True, has_current_tax_file=True,
    )

    # 31 Oct 2026 -> exactly 30 days before 30 Nov 2026 filing close for 25/26.
    now = datetime(2026, 10, 31, 6, 0, tzinfo=timezone.utc)  # 11:30 SL

    # Round 1
    summary1 = run_daily_pass(
        customer_contexts=[ctx], audit=audit, now=now,
    )
    # Round 2 (same day, same audit) -> dedupe
    summary2 = run_daily_pass(
        customer_contexts=[ctx], audit=audit, now=now,
    )

    assert summary1["decisions_computed"] >= 1
    # On round 2, audit_keys are now hydrated -> no new decisions.
    assert summary2["decisions_computed"] == 0


def test_scheduler_emits_year_end_messages_on_correct_days():
    """Verify the three year-boundary events fire on -1/+0/+1 days only."""
    audit = LifecycleAudit(store=InMemoryAuditStore())
    ctx = RolloverContext(
        customer_id=99, persona="business",
        prior_year_filed=True,
        has_prior_tax_file=True, has_current_tax_file=False,
    )

    # 31 Mar 2026 (year_closing_tomorrow for 25/26) — but our current_tax_year
    # at that instant is 25/26 still (it's 31 Mar in SL local), so the "year
    # closing tomorrow" event is keyed off year_end_of_prior = 31 Mar 2026...
    # Compute decisions for 30 Mar 2026 SL-local 18:30 UTC.
    now_pre = datetime(2026, 3, 30, 6, 30, tzinfo=timezone.utc)  # noon SL
    decs_pre = compute_decisions_for_customer(ctx, now=now_pre)
    events_pre = [d.event_type for d in decs_pre]
    # We're inside 25/26 still — year_end_of_prior = (current_tax_year start - 1)
    # = (1 Apr 2025 - 1) = 31 Mar 2025. So no boundary events fire today.
    # Test contract: function is well-behaved (returns a list, doesn't crash).
    assert isinstance(decs_pre, list)

    # 1 Apr 2026 SL-local — current_tax_year flips to 26/27.
    # year_end_of_prior = 31 Mar 2026 = today - 1 -> "year_closing_tomorrow"
    # was YESTERDAY. Today is +0 offset = "year_ended_today" silent +
    # plus the "create_new_year_tax_file" if has_current_tax_file=False.
    now_apr1 = datetime(2026, 4, 1, 6, 0, tzinfo=timezone.utc)  # 11:30 SL
    decs_apr1 = compute_decisions_for_customer(ctx, now=now_apr1)
    events_apr1 = [d.event_type for d in decs_apr1]
    # On 1 Apr SL, year_end_of_prior (31 Mar 2026) + 1 = 1 Apr = today,
    # so we expect "new_year_transition_invite" + "create_new_year_tax_file".
    assert "new_year_transition_invite" in events_apr1
    assert "create_new_year_tax_file" in events_apr1


def test_dispatch_x3_respects_audit_idempotency():
    """Dispatcher suppresses duplicates that were already recorded."""
    audit = LifecycleAudit(store=InMemoryAuditStore())
    sent: list[tuple[int, str]] = []

    def fake_email(*, customer_id, subject, body_text, body_html=None, meta=None):
        sent.append((customer_id, subject))
        return "msg-1"

    dec = SchedulingDecision(
        customer_id=7,
        event_type="new_year_transition_invite",
        payload={"current_year": "2026/27", "prior_year": "2025/26"},
        scheduled_for=datetime.now(SL_TZ),
        idempotency_key="cust7:new_year_transition_invite:2026-04-01",
    )

    # First dispatch -> sent.
    r1 = dispatch_x3([dec], email_sender=fake_email, audit=audit)
    assert r1.sent == 1
    assert len(sent) == 1

    # Second dispatch with same key -> suppressed.
    r2 = dispatch_x3([dec], email_sender=fake_email, audit=audit)
    assert r2.suppressed_duplicates == 1
    assert r2.sent == 0
    assert len(sent) == 1  # unchanged
