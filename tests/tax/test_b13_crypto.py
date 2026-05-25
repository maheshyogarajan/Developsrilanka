"""tests/tax/test_b13_crypto.py — MS3 B13 Crypto/CGT classifier tests.

10 tests covering:
  1. record_crypto_acquisition creates an open CryptoPosition with per-share LKR basis
  2. record_crypto_disposal FIFO-matches against open positions (oldest-first)
  3. record_crypto_disposal rejects over-sale BEFORE mutating state
  4. record_crypto_disposal creates AssetDisposal(asset_type='crypto') with correct gain
  5. CSV-style flow: multiple buys + sells produce positions + disposals via the engine
  6. compute_crypto_cgt aggregates per-year gains across assets
  7. compute_crypto_cgt offsets losses within the same year (gain vs loss)
  8. Loss carry-forward to next year (prior-year net loss offsets current-year gain)
  9. Foreign-source crypto invokes the DTAA stub seam (verify call, stub returns None)
 10. DTAA banner flag flips on a foreign-source disposal (UI surface contract)

Run::

    cd C:/Users/mahes/fiesta_phase_a/Developsrilanka_ms3_b13
    python -m pytest tests/tax/test_b13_crypto.py -v
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from fiesta.tax.money import Money


# ---------------------------------------------------------------------------
# 1. record_crypto_acquisition → CryptoPosition + per-share LKR basis
# ---------------------------------------------------------------------------
def test_record_buy_creates_position(session, user):
    from fiesta.tax.models import CryptoPosition
    from fiesta.tax.crypto_cgt import record_crypto_acquisition

    money = Money(
        amount=Decimal("3000.00"),
        currency="USD",
        fx_rate=Decimal("305.00"),
        fx_source="manual",
        fx_date=date(2024, 6, 10),
    )
    position = record_crypto_acquisition(
        user=user,
        asset_identifier="btc",  # lower-case input — must be upper-cased
        acquisition_money=money,
        acquisition_date=date(2024, 6, 10),
        shares=Decimal("0.05"),
        source_country="US",
    )
    assert position.id is not None
    assert position.asset_identifier == "BTC"
    assert position.shares == Decimal("0.05")
    assert position.shares_remaining == Decimal("0.05")
    assert position.acquisition_date == date(2024, 6, 10)
    assert position.source_country == "US"
    assert position.closed_at is None
    # 3000 * 305 = 915,000 LKR total
    assert Decimal(position.acq_amount_lkr) == Decimal("915000.00")
    # 915,000 / 0.05 = 18,300,000.00000000 per share
    assert Decimal(position.acq_amount_lkr_per_share) == Decimal("18300000.00000000")

    # income_sources updated idempotently
    session.refresh(user)
    assert "crypto" in (user.income_sources or [])


# ---------------------------------------------------------------------------
# 2. FIFO matcher pairs oldest-first across multiple lots
# ---------------------------------------------------------------------------
def test_record_sell_matches_fifo_against_positions(session, user):
    from fiesta.tax.crypto_cgt import (
        _fifo_match,
        record_crypto_acquisition,
        record_crypto_disposal,
    )

    # Three buys at different dates + prices (LKR cost basis varies).
    for d, total in [
        (date(2024, 1, 1), Decimal("100000")),   # 0.1 BTC @ LKR 1,000,000/BTC
        (date(2024, 3, 1), Decimal("150000")),   # 0.1 BTC @ LKR 1,500,000/BTC
        (date(2024, 6, 1), Decimal("200000")),   # 0.1 BTC @ LKR 2,000,000/BTC
    ]:
        record_crypto_acquisition(
            user=user,
            asset_identifier="BTC",
            acquisition_money=Money(
                amount=total, currency="LKR",
                fx_rate=Decimal("1.0"), fx_source="lkr_native", fx_date=d,
            ),
            acquisition_date=d,
            shares=Decimal("0.1"),
        )

    # Plan a sale of 0.15 BTC → consumes lot 1 (0.1) + half of lot 2 (0.05).
    plan = _fifo_match(int(user.id), "BTC", Decimal("0.15"))
    assert len(plan) == 2
    pos_a, take_a = plan[0]
    pos_b, take_b = plan[1]
    assert pos_a.acquisition_date == date(2024, 1, 1)
    assert take_a == Decimal("0.1")
    assert pos_b.acquisition_date == date(2024, 3, 1)
    assert take_b == Decimal("0.05")

    # Execute the sell — 0.15 BTC @ LKR 3,000,000/BTC = LKR 450,000 proceeds.
    disposals = record_crypto_disposal(
        user=user,
        asset_identifier="BTC",
        disposal_money=Money(
            amount=Decimal("450000"), currency="LKR",
            fx_rate=Decimal("1.0"), fx_source="lkr_native",
            fx_date=date(2024, 9, 1),
        ),
        disposal_date=date(2024, 9, 1),
        shares_disposed=Decimal("0.15"),
    )
    assert len(disposals) == 2
    # Lot 1: 0.1 BTC; cost = 100,000; proceeds = 0.1 * 3,000,000 = 300,000; gain = 200,000
    assert Decimal(disposals[0].acq_amount_lkr) == Decimal("100000.00")
    assert Decimal(disposals[0].disp_amount_lkr) == Decimal("300000.00")
    assert Decimal(disposals[0].gain_lkr) == Decimal("200000.00")
    # Lot 2: 0.05 BTC; cost = 0.05 * 1,500,000 = 75,000; proceeds = 0.05 * 3,000,000 = 150,000; gain = 75,000
    assert Decimal(disposals[1].acq_amount_lkr) == Decimal("75000.00")
    assert Decimal(disposals[1].disp_amount_lkr) == Decimal("150000.00")
    assert Decimal(disposals[1].gain_lkr) == Decimal("75000.00")
    # Lot 1 fully closed; Lot 2 partial (0.05 remaining).
    from fiesta.tax.models import CryptoPosition
    lots = CryptoPosition.query.filter_by(user_id=user.id).order_by(CryptoPosition.acquisition_date).all()
    assert lots[0].closed_at is not None
    assert lots[0].shares_remaining == Decimal("0E-12")  # 0
    assert lots[1].closed_at is None
    assert lots[1].shares_remaining == Decimal("0.05")
    # Lot 3 fully untouched.
    assert lots[2].shares_remaining == Decimal("0.1")


# ---------------------------------------------------------------------------
# 3. Over-sale is rejected BEFORE any mutation
# ---------------------------------------------------------------------------
def test_record_sell_rejects_oversale(session, user):
    from fiesta.tax.models import AssetDisposal, CryptoPosition
    from fiesta.tax.crypto_cgt import record_crypto_acquisition, record_crypto_disposal

    record_crypto_acquisition(
        user=user, asset_identifier="ETH",
        acquisition_money=Money(
            amount=Decimal("10000"), currency="LKR",
            fx_rate=Decimal("1.0"), fx_source="lkr_native",
            fx_date=date(2024, 5, 1),
        ),
        acquisition_date=date(2024, 5, 1),
        shares=Decimal("2.0"),
    )

    # Try to dispose 5 ETH (have only 2).
    with pytest.raises(ValueError, match="Over-sale"):
        record_crypto_disposal(
            user=user, asset_identifier="ETH",
            disposal_money=Money(
                amount=Decimal("25000"), currency="LKR",
                fx_rate=Decimal("1.0"), fx_source="lkr_native",
                fx_date=date(2024, 8, 1),
            ),
            disposal_date=date(2024, 8, 1),
            shares_disposed=Decimal("5.0"),
        )

    # No state mutation — no AssetDisposal rows; CryptoPosition untouched.
    assert AssetDisposal.query.filter_by(user_id=user.id, asset_type="crypto").count() == 0
    pos = CryptoPosition.query.filter_by(user_id=user.id, asset_identifier="ETH").one()
    assert pos.shares_remaining == Decimal("2.0")
    assert pos.closed_at is None


# ---------------------------------------------------------------------------
# 4. record_crypto_disposal → AssetDisposal with correct gain LKR
# ---------------------------------------------------------------------------
def test_record_sell_creates_asset_disposal_with_correct_gain(session, user):
    from fiesta.tax.models import AssetDisposal
    from fiesta.tax.crypto_cgt import record_crypto_acquisition, record_crypto_disposal

    # Buy 1 BTC at USD 30,000 @ LKR 300/USD = LKR 9,000,000.
    record_crypto_acquisition(
        user=user, asset_identifier="BTC",
        acquisition_money=Money(
            amount=Decimal("30000"), currency="USD",
            fx_rate=Decimal("300.00"), fx_source="manual",
            fx_date=date(2024, 7, 15),
        ),
        acquisition_date=date(2024, 7, 15),
        shares=Decimal("1.0"),
        source_country="US",
    )

    # Sell 1 BTC at USD 50,000 @ LKR 305/USD = LKR 15,250,000.
    disposals = record_crypto_disposal(
        user=user, asset_identifier="BTC",
        disposal_money=Money(
            amount=Decimal("50000"), currency="USD",
            fx_rate=Decimal("305.00"), fx_source="manual",
            fx_date=date(2026, 2, 10),
        ),
        disposal_date=date(2026, 2, 10),
        shares_disposed=Decimal("1.0"),
    )
    assert len(disposals) == 1
    d = disposals[0]
    assert d.asset_type == "crypto"
    assert d.asset_identifier == "BTC"
    assert Decimal(d.acq_amount_lkr) == Decimal("9000000.00")
    assert Decimal(d.disp_amount_lkr) == Decimal("15250000.00")
    assert Decimal(d.gain_lkr) == Decimal("6250000.00")
    # source_country inherited from buy lot (no disposal-side override here)
    assert d.source_country == "US"
    # tax_year derived from disposal_date (2026-02-10 → 25/26).
    assert d.tax_year == "2025/26"
    # Evidence ref points back to the position.
    refs = d.evidence_refs
    assert isinstance(refs, list) and len(refs) >= 1
    assert refs[0]["type"] == "crypto_disposal"
    assert refs[0]["cost_basis_method"] == "FIFO"


# ---------------------------------------------------------------------------
# 5. CSV-style flow via engine (buys + sell that spans multiple lots)
# ---------------------------------------------------------------------------
def test_csv_import_creates_positions_and_disposals(session, user):
    """Simulate what fiesta.crypto.routes.import_submit does after CSV parse:
    chronologically replay buys + sells through the engine. End-state: 2
    open positions (one partially consumed) + 2 AssetDisposal rows (one
    per matched lot).
    """
    from fiesta.tax.models import AssetDisposal, CryptoPosition
    from fiesta.tax.crypto_cgt import (
        record_crypto_acquisition, record_crypto_disposal,
    )

    rows = [
        ("buy", date(2024, 6, 10), Decimal("0.10"), Decimal("6500")),
        ("buy", date(2024, 9, 5), Decimal("0.05"), Decimal("2900")),
        ("buy", date(2025, 2, 1), Decimal("0.05"), Decimal("3200")),
        ("sell", date(2026, 2, 20), Decimal("0.12"), Decimal("10800")),
    ]
    rows.sort(key=lambda r: (r[1], 0 if r[0] == "buy" else 1))
    for typ, d, sh, total_usd in rows:
        money = Money(
            amount=total_usd, currency="USD",
            fx_rate=Decimal("305.00"), fx_source="manual", fx_date=d,
        )
        if typ == "buy":
            record_crypto_acquisition(
                user=user, asset_identifier="BTC",
                acquisition_money=money, acquisition_date=d, shares=sh,
            )
        else:
            record_crypto_disposal(
                user=user, asset_identifier="BTC",
                disposal_money=money, disposal_date=d, shares_disposed=sh,
            )

    positions = CryptoPosition.query.filter_by(user_id=user.id).order_by(CryptoPosition.acquisition_date).all()
    assert len(positions) == 3
    # First lot (0.10) fully consumed; second lot (0.05) fully consumed (0.02 from it + lot 1's 0.10 = 0.12).
    # Wait — actually: sell 0.12, FIFO: lot1 0.10 + lot2 0.02. So lot2 remaining = 0.03.
    assert positions[0].shares_remaining == Decimal("0E-12")
    assert positions[0].closed_at is not None
    assert positions[1].shares_remaining == Decimal("0.03")
    assert positions[1].closed_at is None
    assert positions[2].shares_remaining == Decimal("0.05")
    assert positions[2].closed_at is None

    disposals = AssetDisposal.query.filter_by(user_id=user.id, asset_type="crypto").all()
    assert len(disposals) == 2


# ---------------------------------------------------------------------------
# 6. compute_crypto_cgt aggregates gains across assets in one year
# ---------------------------------------------------------------------------
def test_compute_crypto_cgt_aggregates_gains_lkr(session, user):
    from fiesta.tax.crypto_cgt import (
        compute_crypto_cgt, record_crypto_acquisition, record_crypto_disposal,
    )

    # BTC: buy 1 @ LKR 1M, sell 1 @ LKR 1.5M → gain 500K
    record_crypto_acquisition(
        user=user, asset_identifier="BTC",
        acquisition_money=Money(
            amount=Decimal("1000000"), currency="LKR",
            fx_rate=Decimal("1.0"), fx_source="lkr_native",
            fx_date=date(2025, 5, 1),
        ),
        acquisition_date=date(2025, 5, 1), shares=Decimal("1"),
    )
    record_crypto_disposal(
        user=user, asset_identifier="BTC",
        disposal_money=Money(
            amount=Decimal("1500000"), currency="LKR",
            fx_rate=Decimal("1.0"), fx_source="lkr_native",
            fx_date=date(2025, 11, 1),
        ),
        disposal_date=date(2025, 11, 1), shares_disposed=Decimal("1"),
    )
    # ETH: buy 10 @ LKR 50,000 each, sell 10 @ LKR 70,000 each → gain 200K
    record_crypto_acquisition(
        user=user, asset_identifier="ETH",
        acquisition_money=Money(
            amount=Decimal("500000"), currency="LKR",
            fx_rate=Decimal("1.0"), fx_source="lkr_native",
            fx_date=date(2025, 6, 1),
        ),
        acquisition_date=date(2025, 6, 1), shares=Decimal("10"),
    )
    record_crypto_disposal(
        user=user, asset_identifier="ETH",
        disposal_money=Money(
            amount=Decimal("700000"), currency="LKR",
            fx_rate=Decimal("1.0"), fx_source="lkr_native",
            fx_date=date(2025, 12, 1),
        ),
        disposal_date=date(2025, 12, 1), shares_disposed=Decimal("10"),
    )

    result = compute_crypto_cgt(user, "2025/26")
    assert result["gross_gain_lkr"] == Decimal("700000.00")
    assert result["gross_loss_lkr"] == Decimal("0.00")
    assert result["net_gain_lkr_pre_carry"] == Decimal("700000.00")
    assert result["net_gain_lkr_after_carry"] == Decimal("700000.00")
    assert result["loss_carry_forward_in"] == Decimal("0.00")
    assert result["loss_carry_forward_out"] == Decimal("0.00")
    assert "BTC" in result["by_asset"]
    assert "ETH" in result["by_asset"]
    assert result["by_asset"]["BTC"]["gain_lkr"] == Decimal("500000.00")
    assert result["by_asset"]["ETH"]["gain_lkr"] == Decimal("200000.00")
    # Also accepts S4 (dash) format
    assert compute_crypto_cgt(user, "2025-26")["gross_gain_lkr"] == Decimal("700000.00")


# ---------------------------------------------------------------------------
# 7. Losses offset gains within the SAME tax year
# ---------------------------------------------------------------------------
def test_compute_crypto_cgt_offsets_losses_within_year(session, user):
    from fiesta.tax.crypto_cgt import (
        compute_crypto_cgt, record_crypto_acquisition, record_crypto_disposal,
    )

    # Winning trade: gain 500K
    record_crypto_acquisition(
        user=user, asset_identifier="BTC",
        acquisition_money=Money(
            amount=Decimal("1000000"), currency="LKR",
            fx_rate=Decimal("1.0"), fx_source="lkr_native",
            fx_date=date(2025, 5, 1),
        ),
        acquisition_date=date(2025, 5, 1), shares=Decimal("1"),
    )
    record_crypto_disposal(
        user=user, asset_identifier="BTC",
        disposal_money=Money(
            amount=Decimal("1500000"), currency="LKR",
            fx_rate=Decimal("1.0"), fx_source="lkr_native",
            fx_date=date(2025, 8, 1),
        ),
        disposal_date=date(2025, 8, 1), shares_disposed=Decimal("1"),
    )
    # Losing trade: loss 200K
    record_crypto_acquisition(
        user=user, asset_identifier="ETH",
        acquisition_money=Money(
            amount=Decimal("500000"), currency="LKR",
            fx_rate=Decimal("1.0"), fx_source="lkr_native",
            fx_date=date(2025, 6, 1),
        ),
        acquisition_date=date(2025, 6, 1), shares=Decimal("10"),
    )
    record_crypto_disposal(
        user=user, asset_identifier="ETH",
        disposal_money=Money(
            amount=Decimal("300000"), currency="LKR",
            fx_rate=Decimal("1.0"), fx_source="lkr_native",
            fx_date=date(2025, 12, 1),
        ),
        disposal_date=date(2025, 12, 1), shares_disposed=Decimal("10"),
    )

    result = compute_crypto_cgt(user, "2025/26")
    assert result["gross_gain_lkr"] == Decimal("500000.00")
    assert result["gross_loss_lkr"] == Decimal("200000.00")
    # Net = 500K - 200K = 300K
    assert result["net_gain_lkr_pre_carry"] == Decimal("300000.00")
    assert result["net_gain_lkr_after_carry"] == Decimal("300000.00")
    assert result["loss_carry_forward_out"] == Decimal("0.00")


# ---------------------------------------------------------------------------
# 8. Loss carry-forward to next year — prior-year net loss offsets current gain
# ---------------------------------------------------------------------------
def test_loss_carry_forward_to_next_year(session, user):
    from fiesta.tax.crypto_cgt import (
        compute_crypto_cgt, record_crypto_acquisition, record_crypto_disposal,
    )

    # 2024/25: net loss of 300K (buy 1M, sell 700K).
    record_crypto_acquisition(
        user=user, asset_identifier="BTC",
        acquisition_money=Money(
            amount=Decimal("1000000"), currency="LKR",
            fx_rate=Decimal("1.0"), fx_source="lkr_native",
            fx_date=date(2024, 6, 1),
        ),
        acquisition_date=date(2024, 6, 1), shares=Decimal("1"),
    )
    record_crypto_disposal(
        user=user, asset_identifier="BTC",
        disposal_money=Money(
            amount=Decimal("700000"), currency="LKR",
            fx_rate=Decimal("1.0"), fx_source="lkr_native",
            fx_date=date(2024, 12, 1),
        ),
        disposal_date=date(2024, 12, 1), shares_disposed=Decimal("1"),
    )

    # 2025/26: a winner of 500K (separate buy + sell).
    record_crypto_acquisition(
        user=user, asset_identifier="ETH",
        acquisition_money=Money(
            amount=Decimal("500000"), currency="LKR",
            fx_rate=Decimal("1.0"), fx_source="lkr_native",
            fx_date=date(2025, 6, 1),
        ),
        acquisition_date=date(2025, 6, 1), shares=Decimal("10"),
    )
    record_crypto_disposal(
        user=user, asset_identifier="ETH",
        disposal_money=Money(
            amount=Decimal("1000000"), currency="LKR",
            fx_rate=Decimal("1.0"), fx_source="lkr_native",
            fx_date=date(2025, 11, 1),
        ),
        disposal_date=date(2025, 11, 1), shares_disposed=Decimal("10"),
    )

    # Prior-year alone: 300K loss → cf-out = 300K.
    r_prior = compute_crypto_cgt(user, "2024/25")
    assert r_prior["net_gain_lkr_pre_carry"] == Decimal("-300000.00")
    assert r_prior["loss_carry_forward_out"] == Decimal("300000.00")
    assert r_prior["loss_carry_forward_in"] == Decimal("0.00")

    # Current year: 500K gain absorbed 300K from prior CF → net taxable = 200K.
    r = compute_crypto_cgt(user, "2025/26")
    assert r["gross_gain_lkr"] == Decimal("500000.00")
    assert r["loss_carry_forward_in"] == Decimal("300000.00")
    assert r["net_gain_lkr_pre_carry"] == Decimal("500000.00")
    assert r["net_gain_lkr_after_carry"] == Decimal("200000.00")
    assert r["loss_carry_forward_out"] == Decimal("0.00")


# ---------------------------------------------------------------------------
# 9. Foreign-source crypto calls the DTAA stub seam
# ---------------------------------------------------------------------------
def test_foreign_crypto_calls_dtaa_stub(session, user):
    from fiesta.tax import crypto_cgt as crypto_engine
    from fiesta.tax.crypto_cgt import (
        compute_crypto_cgt, record_crypto_acquisition, record_crypto_disposal,
    )

    # Buy + sell with source_country='US' (Coinbase-style).
    record_crypto_acquisition(
        user=user, asset_identifier="BTC",
        acquisition_money=Money(
            amount=Decimal("20000"), currency="USD",
            fx_rate=Decimal("305.00"), fx_source="manual",
            fx_date=date(2025, 5, 1),
        ),
        acquisition_date=date(2025, 5, 1), shares=Decimal("1"),
        source_country="US",
    )
    record_crypto_disposal(
        user=user, asset_identifier="BTC",
        disposal_money=Money(
            amount=Decimal("35000"), currency="USD",
            fx_rate=Decimal("305.00"), fx_source="manual",
            fx_date=date(2025, 11, 1),
        ),
        disposal_date=date(2025, 11, 1), shares_disposed=Decimal("1"),
    )

    with patch.object(crypto_engine, "apply_foreign_tax_credit",
                      wraps=crypto_engine.apply_foreign_tax_credit) as spy:
        result = compute_crypto_cgt(user, "2025/26")
        # One foreign-source disposal → one seam call.
        assert spy.call_count == 1
        args, _ = spy.call_args
        # First arg = abs(gain_lkr) Decimal; second arg = synthetic income-like object.
        from decimal import Decimal as _D
        assert isinstance(args[0], _D)
        assert getattr(args[1], "source_country", None) == "US"
        assert getattr(args[1], "source_type", None) == "crypto"

    # Stub returns None → no credits accumulated.
    assert result["dtaa_credits"] == []


# ---------------------------------------------------------------------------
# 10. DTAA banner flag flips on foreign-source disposal
# ---------------------------------------------------------------------------
def test_dtaa_banner_on_tax_bill_for_foreign_crypto(session, user):
    from fiesta.tax.crypto_cgt import (
        compute_crypto_cgt, record_crypto_acquisition, record_crypto_disposal,
    )

    # Domestic-source disposal — banner stays False.
    record_crypto_acquisition(
        user=user, asset_identifier="USDC",
        acquisition_money=Money(
            amount=Decimal("100000"), currency="LKR",
            fx_rate=Decimal("1.0"), fx_source="lkr_native",
            fx_date=date(2025, 4, 5),
        ),
        acquisition_date=date(2025, 4, 5), shares=Decimal("1000"),
    )
    record_crypto_disposal(
        user=user, asset_identifier="USDC",
        disposal_money=Money(
            amount=Decimal("105000"), currency="LKR",
            fx_rate=Decimal("1.0"), fx_source="lkr_native",
            fx_date=date(2025, 6, 5),
        ),
        disposal_date=date(2025, 6, 5), shares_disposed=Decimal("1000"),
    )
    r_local = compute_crypto_cgt(user, "2025/26")
    assert r_local["dtaa_deferred"] is False

    # Now add a foreign-source buy+sell — banner flips True.
    record_crypto_acquisition(
        user=user, asset_identifier="ETH",
        acquisition_money=Money(
            amount=Decimal("3000"), currency="USD",
            fx_rate=Decimal("305.00"), fx_source="manual",
            fx_date=date(2025, 7, 1),
        ),
        acquisition_date=date(2025, 7, 1), shares=Decimal("1"),
        source_country="US",
    )
    record_crypto_disposal(
        user=user, asset_identifier="ETH",
        disposal_money=Money(
            amount=Decimal("3500"), currency="USD",
            fx_rate=Decimal("305.00"), fx_source="manual",
            fx_date=date(2025, 12, 1),
        ),
        disposal_date=date(2025, 12, 1), shares_disposed=Decimal("1"),
    )
    r_both = compute_crypto_cgt(user, "2025/26")
    assert r_both["dtaa_deferred"] is True
