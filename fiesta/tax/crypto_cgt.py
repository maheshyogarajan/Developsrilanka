"""fiesta.tax.crypto_cgt — B13 Crypto / CGT engine (MS3).

Sri Lanka capital-gains-tax treatment for cryptocurrency holdings.

Tax treatment (IRA Chapter IV, Sections 36 + 37):
  - **Crypto is an "asset" for §36** ("amount by which the sum of the
    consideration received for the asset … exceeds the cost of the
    asset … at the time of realisation"). There is no separate digital-asset
    schedule in the consolidated 2017 Act + 2025 amendments — gains on
    realisation are taxed as investment-asset gains under §7(2)(b) per
    Chapter IV.
  - **Realisation event = disposal** (sale, swap, gift, conversion to FIAT
    or to another crypto). Holding (mark-to-market unrealised) is NOT a
    realisation; we do not tax unrealised gains.
  - **Cost basis = §37(1)(a) "expenditure incurred in acquiring the asset"**
    plus §37(1)(c) incidental costs (exchange fees, brokerage). FIFO
    matching of acquisition lots to disposal lots is the default
    convention.
  - **Loss treatment = §36(2)** ("loss … cost exceeds consideration").
    Losses on crypto disposals offset gains in the same year; net loss
    carries forward to future years (capped per IRA Chapter IV — this
    iteration does NOT enforce the carry-forward cap; that's the engine's
    Phase-3 concern).
  - **CGT rate**: governed by the engine's bracket schedule (Phase 1 engine
    rolls CGT into ``other_lkr``); ``compute_crypto_cgt`` returns the LKR
    base + per-disposal evidence, leaving the rate application to the engine.

DTAA (Wave-X seam):
  Foreign-source crypto (e.g. Coinbase-held BTC for an SL-resident) may
  attract treaty relief under capital-gains articles (US-SL Article 13).
  ``apply_foreign_tax_credit(...)`` is the seam — pre-Wave-X stub returns
  None, so no relief is applied; ``dtaa_deferred`` flag on the return shape
  flips a UI banner so users see the relief is coming.

FIFO matching (Council 2026-05-25):
  When a disposal of N shares of asset X is recorded:
    1. Query open positions for (user_id, asset_identifier=X) ordered by
       acquisition_date ASC.
    2. Consume oldest-first: position[i].shares_remaining is decremented;
       each consumption produces one AssetDisposal row with that lot's
       acq_amount_lkr_per_share as cost basis.
    3. Over-sale (sum_shares_remaining < N) raises ValueError BEFORE any
       state mutation (transactional safety).
    4. Partial last-lot consumption: emit one AssetDisposal row for the
       partial slice, update shares_remaining accordingly.

TODO(B13 v1.1): Support LIFO + specific-identification cost basis. The
positions ledger already permits both — only the matcher needs replacing.
For now: FIFO is hard-coded; a future ``cost_basis_method`` config column on
the user (or per-import override) will pick the matcher.

Provenance: Inventory §B13, Design Lock 2 §5/§6/§8, IRA Sections 7(2)(b),
36, 37 (verified via mcp__ira__get_section, 2026-05-25).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from app import db

from fiesta.tax.credits import apply_foreign_tax_credit
from fiesta.tax.models import AssetDisposal, CryptoPosition, Income
from fiesta.tax.money import Money

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tax-year derivation (SL Y/A runs 1 April → 31 March) — mirrors rsu_engine.
# ---------------------------------------------------------------------------
def _tax_year_for(d: date) -> str:
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}/{str(start + 1)[2:]}"


# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------
def record_crypto_acquisition(
    user,
    asset_identifier: str,
    acquisition_money: Money,
    acquisition_date: date,
    shares: Decimal,
    source_country: Optional[str] = None,
    evidence_refs: Optional[list[dict[str, Any]]] = None,
) -> CryptoPosition:
    """Record a crypto acquisition (buy) → opens a new CryptoPosition lot.

    Acquisition is NOT a taxable realisation event — §36(1) requires a
    disposal. This function only writes to the positions ledger so FIFO has
    something to match against later.

    Side effect: adds 'crypto' to ``user.income_sources`` if not already
    present (idempotent — survives many buys).

    Args:
        user:                The User ORM row (must have id + income_sources).
        asset_identifier:    "BTC" / "ETH" / "SOL" / "USDC" / etc. Stored
                             upper-cased + 16-char-truncated.
        acquisition_money:   Money — total LKR-equivalent acquisition cost
                             (e.g. 0.05 BTC × $30,000/BTC × 305 LKR/USD =
                             LKR 457,500). amount_lkr is derived in Money.
        acquisition_date:    The buy date — also used for FIFO ordering.
        shares:              Decimal — total shares acquired (fractional OK).
                             Crypto often has 8+ decimal-place precision.
        source_country:      ISO-3166-1 alpha-2 country code of the exchange
                             custodian (US for Coinbase, GB for Kraken UK,
                             SL for Bitsila, etc.). Drives DTAA seam at
                             compute time. None for ambiguous custody.
        evidence_refs:       Optional list of evidence pointers (e.g.
                             [{"type":"csv_import","ref_id":42}]).

    Returns:
        The newly-persisted CryptoPosition row (id populated).
    """
    if shares is None:
        raise ValueError("shares is required")
    shares = Decimal(str(shares))
    if shares <= 0:
        raise ValueError(f"shares must be > 0; got {shares}")
    if acquisition_money is None:
        raise ValueError("acquisition_money is required")

    asset_clean = (asset_identifier or "").strip().upper()[:16]
    if not asset_clean:
        raise ValueError("asset_identifier is required")

    per_share_lkr = (
        Decimal(str(acquisition_money.amount_lkr)) / shares
    ).quantize(Decimal("0.00000001"))

    position = CryptoPosition(
        user_id=user.id,
        asset_identifier=asset_clean,
        acquisition_date=acquisition_date,
        shares=shares,
        shares_remaining=shares,
        acq_amount=acquisition_money.amount,
        acq_currency=acquisition_money.currency,
        acq_fx_rate=acquisition_money.fx_rate,
        acq_fx_source=acquisition_money.fx_source,
        acq_fx_date=acquisition_money.fx_date,
        acq_amount_lkr=acquisition_money.amount_lkr,
        acq_amount_lkr_per_share=per_share_lkr,
        source_country=(source_country or None),
        evidence_refs=(evidence_refs or []),
    )
    db.session.add(position)
    db.session.flush()  # populate position.id

    # Idempotent income_sources update — 'crypto' marker for sidebar visibility.
    sources = list(user.income_sources or [])
    if "crypto" not in sources:
        sources.append("crypto")
        user.income_sources = sources
        try:
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(user, "income_sources")
        except Exception:  # pragma: no cover
            pass

    db.session.commit()
    logger.info(
        "Crypto acquisition recorded: user=%s asset=%s shares=%s "
        "acquisition_date=%s total_lkr=%s source_country=%s position_id=%s",
        user.id, asset_clean, shares, acquisition_date,
        acquisition_money.amount_lkr, source_country, position.id,
    )
    return position


# ---------------------------------------------------------------------------
# FIFO matcher (internal — exposed for tests)
# ---------------------------------------------------------------------------
def _fifo_match(
    user_id: int,
    asset_identifier: str,
    shares_to_dispose: Decimal,
) -> list[tuple[CryptoPosition, Decimal]]:
    """Return the open-position lots (oldest first) needed to cover
    ``shares_to_dispose`` of ``asset_identifier`` for ``user_id``.

    Each tuple is ``(position, shares_to_consume_from_this_lot)``. The sum
    of the second elements equals ``shares_to_dispose``.

    Raises:
        ValueError: if open positions cover < ``shares_to_dispose``
                    (over-sale). NO mutation occurs in that case.

    Note: this returns lazily — no DB writes yet. ``record_crypto_disposal``
    consumes the returned plan and writes both the AssetDisposal rows and
    the shares_remaining decrements in a single commit.
    """
    open_positions = (
        CryptoPosition.query
        .filter(
            CryptoPosition.user_id == user_id,
            CryptoPosition.asset_identifier == asset_identifier,
            CryptoPosition.shares_remaining > 0,
        )
        .order_by(CryptoPosition.acquisition_date.asc(), CryptoPosition.id.asc())
        .all()
    )

    available = sum(
        (Decimal(str(p.shares_remaining)) for p in open_positions),
        Decimal("0"),
    )
    if available < shares_to_dispose:
        raise ValueError(
            f"Over-sale: tried to dispose {shares_to_dispose} of "
            f"{asset_identifier!r} but only {available} shares are open "
            f"(across {len(open_positions)} lots)"
        )

    plan: list[tuple[CryptoPosition, Decimal]] = []
    remaining = shares_to_dispose
    for pos in open_positions:
        if remaining <= 0:
            break
        avail = Decimal(str(pos.shares_remaining))
        take = avail if avail <= remaining else remaining
        plan.append((pos, take))
        remaining -= take
    return plan


# ---------------------------------------------------------------------------
# Disposal
# ---------------------------------------------------------------------------
def record_crypto_disposal(
    user,
    asset_identifier: str,
    disposal_money: Money,
    disposal_date: date,
    shares_disposed: Decimal,
    cost_basis_method: str = "FIFO",
    evidence_refs: Optional[list[dict[str, Any]]] = None,
) -> list[AssetDisposal]:
    """Record a crypto disposal (sell/swap) → creates one or more
    AssetDisposal rows via FIFO matching against open CryptoPosition lots.

    A single disposal can pair against multiple acquisition lots (e.g. sell
    1 BTC that was assembled from 3 buys of 0.4 + 0.4 + 0.2 BTC each). One
    AssetDisposal row is created PER LOT consumed, so the audit trail keeps
    per-lot cost basis intact.

    gain_lkr_per_lot = (disp_amount_lkr_per_share - acq_amount_lkr_per_share) × shares_consumed

    Per-lot disposal proceeds are pro-rated by share-count from the full
    disposal_money (so a 1 BTC sale at LKR 9,000,000 disposing against two
    lots of 0.4 + 0.6 BTC produces disp_amount_lkr of 3,600,000 + 5,400,000).

    Side effect: adds 'crypto' to ``user.income_sources`` if not already
    present (idempotent — harmless re-add).

    Args:
        user:               The User ORM row.
        asset_identifier:   "BTC" / "ETH" / etc. (case-insensitive; matched
                            against CryptoPosition.asset_identifier exactly
                            after uppercasing).
        disposal_money:     Money — TOTAL disposal proceeds (across all
                            shares_disposed). Pro-rata by share-count for
                            per-lot recording.
        disposal_date:      The sell date — tax-year derivation +
                            holding-period audit.
        shares_disposed:    Decimal — total shares being disposed.
        cost_basis_method:  Currently only "FIFO" is supported. Reserved
                            for future "LIFO" / "specific_id" expansion.
                            Raises ValueError if anything else passed.
        evidence_refs:      Optional evidence pointers added to every lot's
                            AssetDisposal row (e.g. CSV-import provenance).

    Returns:
        list[AssetDisposal] — one row per lot consumed (in FIFO order).

    Raises:
        ValueError if over-sale, invalid args, or unsupported method.
    """
    if shares_disposed is None:
        raise ValueError("shares_disposed is required")
    shares_disposed = Decimal(str(shares_disposed))
    if shares_disposed <= 0:
        raise ValueError(f"shares_disposed must be > 0; got {shares_disposed}")
    if disposal_money is None:
        raise ValueError("disposal_money is required")
    if cost_basis_method != "FIFO":
        # TODO(B13 v1.1): support LIFO + specific-identification.
        raise ValueError(
            f"cost_basis_method={cost_basis_method!r} not supported; "
            "only 'FIFO' available in B13 v1.0"
        )

    asset_clean = (asset_identifier or "").strip().upper()[:16]
    if not asset_clean:
        raise ValueError("asset_identifier is required")

    # Build the FIFO plan BEFORE any mutation — raises on over-sale cleanly.
    plan = _fifo_match(int(user.id), asset_clean, shares_disposed)

    # Derive per-share disposal LKR for pro-rata splitting across lots.
    disp_per_share_lkr = (
        Decimal(str(disposal_money.amount_lkr)) / shares_disposed
    ).quantize(Decimal("0.00000001"))
    disp_per_share_native = (
        Decimal(str(disposal_money.amount)) / shares_disposed
    ).quantize(Decimal("0.00000001"))

    tax_year = _tax_year_for(disposal_date)

    created: list[AssetDisposal] = []
    for pos, take in plan:
        acq_per_share_lkr = Decimal(str(pos.acq_amount_lkr_per_share))
        acq_per_share_native = (
            Decimal(str(pos.acq_amount)) / Decimal(str(pos.shares))
        )

        acq_amount_lot = (acq_per_share_native * take).quantize(Decimal("0.0001"))
        acq_amount_lkr_lot = (acq_per_share_lkr * take).quantize(Decimal("0.01"))

        disp_amount_lot = (disp_per_share_native * take).quantize(Decimal("0.0001"))
        disp_amount_lkr_lot = (disp_per_share_lkr * take).quantize(Decimal("0.01"))

        gain_lkr_lot = (disp_amount_lkr_lot - acq_amount_lkr_lot).quantize(
            Decimal("0.01")
        )

        disposal = AssetDisposal(
            user_id=user.id,
            tax_year=tax_year,
            asset_type="crypto",
            acq_amount=acq_amount_lot,
            acq_currency=pos.acq_currency,
            acq_fx_rate=pos.acq_fx_rate,
            acq_fx_source=pos.acq_fx_source,
            acq_fx_date=pos.acq_fx_date,
            acq_amount_lkr=acq_amount_lkr_lot,
            disp_amount=disp_amount_lot,
            disp_currency=disposal_money.currency,
            disp_fx_rate=disposal_money.fx_rate,
            disp_fx_source=disposal_money.fx_source,
            disp_fx_date=disposal_money.fx_date,
            disp_amount_lkr=disp_amount_lkr_lot,
            gain_lkr=gain_lkr_lot,
            acquisition_date=pos.acquisition_date,
            disposal_date=disposal_date,
            # Source-country precedence: disposal-side custodian wins if
            # provided (sell-side jurisdiction owns the realisation event);
            # otherwise fall back to the acquisition-side custodian.
            source_country=(
                getattr(disposal_money, "source_country", None)
                or pos.source_country
            ),
            asset_identifier=asset_clean,
            evidence_refs=[
                {
                    "type": "crypto_disposal",
                    "position_id": int(pos.id),
                    "asset": asset_clean,
                    "shares_from_lot": str(take),
                    "cost_basis_method": "FIFO",
                    "acq_date": pos.acquisition_date.isoformat(),
                },
                *(evidence_refs or []),
            ],
        )
        db.session.add(disposal)
        created.append(disposal)

        # Decrement the lot's remaining shares; close it if exhausted.
        new_remaining = (Decimal(str(pos.shares_remaining)) - take).quantize(
            Decimal("0.000000000001")
        )
        pos.shares_remaining = new_remaining
        if new_remaining <= 0:
            pos.closed_at = datetime.utcnow()

    # Ensure 'crypto' marker on user.income_sources (idempotent).
    sources = list(user.income_sources or [])
    if "crypto" not in sources:
        sources.append("crypto")
        user.income_sources = sources
        try:
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(user, "income_sources")
        except Exception:  # pragma: no cover
            pass

    db.session.commit()
    logger.info(
        "Crypto disposal recorded: user=%s asset=%s shares_disposed=%s "
        "disposal_date=%s lots_consumed=%s total_gain_lkr=%s",
        user.id, asset_clean, shares_disposed, disposal_date,
        len(created),
        sum((Decimal(str(d.gain_lkr)) for d in created), Decimal("0")),
    )
    return created


# ---------------------------------------------------------------------------
# Loss carry-forward (stored in user.income_sources adjacent meta — but we
# keep a deterministic helper that derives carry-forward from the
# AssetDisposal history without any extra schema)
# ---------------------------------------------------------------------------
def _compute_loss_carry_forward(user_id: int, tax_year: str) -> Decimal:
    """Sum NET CRYPTO LOSSES from all prior tax years for ``user_id``.

    Each prior tax year's net is max(0, -sum(gain_lkr)) — only the loss
    portion carries forward; a year that nets to a gain has its tax paid
    in that year and adds nothing to carry-forward.

    For the v1.0 implementation we do NOT apply IRA Chapter IV caps on
    carry-forward duration (those depend on Chapter IV §38-§40 detail that
    Phase-3 of the engine will model). v1.0 carries forward indefinitely;
    Phase-3 will cap.

    Returns:
        Decimal >= 0; the LKR amount available to offset gains in
        ``tax_year``.
    """
    ty_canonical = (tax_year or "").replace("-", "/")
    # All prior tax years' net.
    prior_rows = (
        AssetDisposal.query
        .filter(
            AssetDisposal.user_id == user_id,
            AssetDisposal.asset_type == "crypto",
            AssetDisposal.tax_year != ty_canonical,
        )
        .all()
    )
    # Group by tax_year.
    by_year: dict[str, Decimal] = {}
    for r in prior_rows:
        # Only count years STRICTLY before the current one (lexically:
        # 'YYYY/YY' sorts correctly because the first 4 chars are the
        # opening calendar year).
        if r.tax_year < ty_canonical:
            by_year[r.tax_year] = by_year.get(r.tax_year, Decimal("0")) + Decimal(str(r.gain_lkr))
    carry = Decimal("0")
    for year_net in by_year.values():
        if year_net < 0:
            carry += -year_net  # accumulate the loss magnitude
    return carry.quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Aggregate compute
# ---------------------------------------------------------------------------
def compute_crypto_cgt(user, tax_year: str) -> dict[str, Any]:
    """Compute the crypto CGT bill components for ``user`` in ``tax_year``.

    Reads:
      - All AssetDisposal(asset_type='crypto', tax_year=tax_year,
        user_id=user.id) → realised gains/losses for the year.
      - All prior-year AssetDisposal(asset_type='crypto') → derive
        loss carry-forward via ``_compute_loss_carry_forward``.

    For each foreign-source disposal, calls ``apply_foreign_tax_credit(...)``
    at the DTAA seam. Pre-Wave-X the stub returns None — no relief applied
    but the call site IS the seam (Wave-X drops the real treaty matrix in
    without rework here).

    Args:
        user:     The User ORM row.
        tax_year: Either 'YYYY/YY' (canonical AssetDisposal.tax_year) or
                  'YYYY-YY' (S4 aggregator format). Normalised internally.

    Returns:
        dict:
            {
                "tax_year":                 "2025/26",
                "disposals":                [dict, …],   # per-lot rows
                "gross_gain_lkr":           Decimal,     # sum of all positive gains
                "gross_loss_lkr":           Decimal,     # sum of all losses (positive number)
                "net_gain_lkr_pre_carry":   Decimal,     # gross gain - gross loss
                "loss_carry_forward_in":    Decimal,     # from prior years
                "net_gain_lkr_after_carry": Decimal,     # may be 0 if losses absorb
                "loss_carry_forward_out":   Decimal,     # carry to NEXT year if still negative
                "by_asset":                 {asset: {...}},  # per-asset breakdown
                "dtaa_credits":             [],         # empty pre-Wave-X
                "dtaa_deferred":            bool,       # banner flag
                "open_positions_summary":   [...]       # for sidebar / portfolio UI
            }
    """
    ty = (tax_year or "").replace("-", "/")
    if "/" in ty and len(ty.split("/")[1]) == 4:
        head, tail = ty.split("/")
        ty = f"{head}/{tail[2:]}"

    cgt_rows = (
        AssetDisposal.query
        .filter_by(user_id=user.id, asset_type="crypto", tax_year=ty)
        .order_by(AssetDisposal.disposal_date.asc(), AssetDisposal.id.asc())
        .all()
    )

    gross_gain = Decimal("0")
    gross_loss = Decimal("0")
    by_asset: dict[str, dict[str, Any]] = {}
    disposal_dicts: list[dict[str, Any]] = []
    dtaa_credits: list[Any] = []

    for row in cgt_rows:
        gain = Decimal(str(row.gain_lkr))
        if gain >= 0:
            gross_gain += gain
        else:
            gross_loss += -gain

        # DTAA seam — call for every foreign-source disposal even though the
        # stub returns None pre-Wave-X. This is the call site that Wave-X
        # drops a real engine into.
        if row.source_country:
            # Build a synthetic Income-shaped object for the seam (the
            # function only reads source_country + source_type, both of
            # which we set here). This keeps the call signature stable
            # whether we pass an Income or an AssetDisposal — Wave-X will
            # accept a Union or refactor as appropriate.
            class _DisposalAsIncome:
                source_country = row.source_country
                source_type = "crypto"
            _net, ftc = apply_foreign_tax_credit(
                gain.copy_abs(), _DisposalAsIncome()
            )
            if ftc is not None:
                dtaa_credits.append(ftc)

        asset = row.asset_identifier or "UNKNOWN"
        bucket = by_asset.setdefault(asset, {
            "asset": asset,
            "gain_lkr": Decimal("0"),
            "loss_lkr": Decimal("0"),
            "net_lkr": Decimal("0"),
            "rows": 0,
        })
        bucket["net_lkr"] += gain
        if gain >= 0:
            bucket["gain_lkr"] += gain
        else:
            bucket["loss_lkr"] += -gain
        bucket["rows"] += 1

        disposal_dicts.append({
            "disposal_id": int(row.id),
            "asset_identifier": asset,
            "acquisition_date": row.acquisition_date.isoformat() if row.acquisition_date else None,
            "disposal_date": row.disposal_date.isoformat() if row.disposal_date else None,
            "acq_amount_lkr": Decimal(str(row.acq_amount_lkr)),
            "disp_amount_lkr": Decimal(str(row.disp_amount_lkr)),
            "gain_lkr": gain,
            "source_country": row.source_country,
            "evidence_refs": row.evidence_refs or [],
        })

    net_pre_carry = (gross_gain - gross_loss).quantize(Decimal("0.01"))

    # Carry-forward IN from prior years (loss magnitude).
    cf_in = _compute_loss_carry_forward(int(user.id), ty)

    if net_pre_carry > 0:
        # Apply incoming carry-forward against the gain.
        offset = min(net_pre_carry, cf_in)
        net_after = (net_pre_carry - offset).quantize(Decimal("0.01"))
        cf_out = Decimal("0")  # offset absorbed; no carry-forward this year
    else:
        # Net loss in the year → no offset applied; carry the full prior CF
        # PLUS this year's loss out.
        net_after = Decimal("0")
        cf_out = (cf_in + (-net_pre_carry)).quantize(Decimal("0.01"))

    # Open positions summary (no taxation; informational for UI).
    open_rows = (
        CryptoPosition.query
        .filter(
            CryptoPosition.user_id == user.id,
            CryptoPosition.shares_remaining > 0,
        )
        .all()
    )
    open_summary: list[dict[str, Any]] = []
    by_pos_asset: dict[str, dict[str, Any]] = {}
    for p in open_rows:
        a = p.asset_identifier
        agg = by_pos_asset.setdefault(a, {
            "asset": a,
            "shares_open": Decimal("0"),
            "cost_basis_lkr": Decimal("0"),
            "lots": 0,
        })
        rem = Decimal(str(p.shares_remaining))
        agg["shares_open"] += rem
        agg["cost_basis_lkr"] += (
            Decimal(str(p.acq_amount_lkr_per_share)) * rem
        ).quantize(Decimal("0.01"))
        agg["lots"] += 1
    open_summary = list(by_pos_asset.values())

    return {
        "tax_year": ty,
        "disposals": disposal_dicts,
        "gross_gain_lkr": gross_gain.quantize(Decimal("0.01")),
        "gross_loss_lkr": gross_loss.quantize(Decimal("0.01")),
        "net_gain_lkr_pre_carry": net_pre_carry,
        "loss_carry_forward_in": cf_in,
        "net_gain_lkr_after_carry": net_after,
        "loss_carry_forward_out": cf_out,
        "by_asset": by_asset,
        "dtaa_credits": dtaa_credits,
        "dtaa_deferred": any(r.source_country for r in cgt_rows),
        "open_positions_summary": open_summary,
    }


__all__ = [
    "record_crypto_acquisition",
    "record_crypto_disposal",
    "compute_crypto_cgt",
    "_fifo_match",  # exposed for tests
    "_compute_loss_carry_forward",  # exposed for tests
    "_tax_year_for",
]
