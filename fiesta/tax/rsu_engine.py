"""fiesta.tax.rsu_engine — B11 RSU classifier (MS2 Stage E.1).

Restricted Stock Units (RSU) tax treatment for SL-resident employees of
foreign employers (FAANG-adjacent, common amongst SL devs at AWS / Stripe /
Google / Meta / Microsoft / etc.).

Two distinct taxable events per RSU lifecycle:

  1. VESTING  — At each vesting tranche, the fair-market value (FMV) of the
                shares allotted to the employee is **employment income** in
                the year the vesting occurs.

                IRA citation: Section 5(2)(j) — "the market value of shares
                at the time allotted under an employee share scheme,
                including shares allotted as a result of the exercise of an
                option or right to acquire the shares, reduced by the
                employee's contribution for the shares."

                For typical FAANG RSU grants the employee contribution is
                zero, so the SL taxable amount equals FMV × shares vested.

  2. SALE     — Subsequent disposal of vested shares is a CGT event.
                Capital gain = (sale price LKR − FMV-at-vest LKR) × shares.

                IRA citation: Section 7(2)(b) — "gains from the realisation
                of investment assets as calculated under Chapter IV."

                Loss is allowed (gain_lkr can be negative).

Foreign tax credit (DTAA):
  - Vesting income may already have been taxed at source (US Federal /
    State withholding for US-employer RSUs is common).
  - Sale CGT may attract US long-term / short-term capital gains tax.
  - Both are routed through ``fiesta.tax.credits.apply_foreign_tax_credit``
    which is a Wave-X seam. Pre-Wave-X, this stub returns None and no
    relief is applied — the user pays the full SL liability. The UI surface
    surfaces the DTAA-deferred banner so the user understands relief is
    coming and shouldn't panic about apparent double-tax.

Persistence:
  - One RSUVestingEvent row per tranche (canonical, B11-extensible).
  - One Income row per tranche linked via Income.rsu_vesting_id
    (source_type='rsu', source_country=<origin>).
  - One AssetDisposal row per sale (asset_type='rsu'), referencing the
    vesting event via asset_identifier="RSU:<vesting_id>".

Idempotency:
  - ``record_rsu_vesting`` adds 'rsu' to User.income_sources exactly once.
  - Re-running with the same (user, ticker, vesting_date, shares) does NOT
    de-duplicate at this layer — callers should check before insert if
    duplicate-prevention is required (the upload UI checks).

Provenance: Inventory §B11 + Design Lock 2 §5/§6 (Council 2026-05-25).
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any

from app import db

from fiesta.tax.credits import apply_foreign_tax_credit
from fiesta.tax.models import AssetDisposal, Income, RSUVestingEvent
from fiesta.tax.money import Money

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tax-year derivation (SL Y/A runs 1 April → 31 March)
# ---------------------------------------------------------------------------
def _tax_year_for(d: date) -> str:
    """Return canonical 'YYYY/YY' tax-year string for date ``d``.

    Mirrors the helper used by the M2-001 backfill so RSU vesting + sale
    rows live alongside foreign-remittance rows in the same partition key.
    """
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}/{str(start + 1)[2:]}"


# ---------------------------------------------------------------------------
# Vesting
# ---------------------------------------------------------------------------
def record_rsu_vesting(
    user,
    ticker: str,
    vesting_date: date,
    shares_vested: Decimal,
    fmv_per_share_money: Money,
    source_country: str | None = None,
) -> RSUVestingEvent:
    """Record an RSU vesting event + the paired Income row.

    Side effects (in one transaction):
      1. INSERT rsu_vesting_events row (fair_market_value_money = the
         per-share Money serialised to dict; ticker + source_country
         denormalised onto the row for fast filtering).
      2. INSERT incomes row with source_type='rsu',
         amount=fmv_per_share.amount × shares,
         currency=fmv_per_share.currency, fx_rate / fx_source / fx_date
         copied from fmv_per_share Money, amount_lkr derived, and
         rsu_vesting_id FK = the new vesting event id.
      3. ADD 'rsu' to ``user.income_sources`` if not already present
         (idempotent — survives re-vesting in the same grant).

    Args:
        user:                The User ORM row (must have id + income_sources).
        ticker:              Equity symbol ("MSFT", "GOOG", "META", …).
                             16-char column; longer values truncated.
        vesting_date:        The tranche vesting date (NOT the grant date).
                             Tax-year is derived from this.
        shares_vested:       Decimal — fractional shares supported.
        fmv_per_share_money: Money — FMV of one share at vest, in source
                             currency (typically USD), with fx_rate to LKR
                             at vest date. amount_lkr is derived; the
                             Income row's total LKR is fmv.amount_lkr ×
                             shares quantised to 2dp.
        source_country:      ISO-3166-1 alpha-2 (US, GB, AU, …) — the
                             country whose tax law owns the original
                             vesting event. Drives DTAA lookup at compute
                             time. None for SL-employer RSUs (rare).

    Returns:
        The newly-persisted RSUVestingEvent row (id populated).
    """
    if shares_vested is None:
        raise ValueError("shares_vested is required")
    shares_vested = Decimal(str(shares_vested))
    if shares_vested <= 0:
        raise ValueError(f"shares_vested must be > 0; got {shares_vested}")
    if fmv_per_share_money is None:
        raise ValueError("fmv_per_share_money is required")

    ticker_clean = (ticker or "").strip().upper()[:16]

    # Step 1: RSUVestingEvent row.
    event = RSUVestingEvent(
        user_id=user.id,
        vesting_date=vesting_date,
        fair_market_value_money=fmv_per_share_money.to_dict(),
        ticker=ticker_clean or None,
        source_country=(source_country or None),
    )
    db.session.add(event)
    db.session.flush()  # populate event.id for the Income FK below

    # Step 2: paired Income row.
    total_native = (fmv_per_share_money.amount * shares_vested)
    total_lkr = (fmv_per_share_money.amount_lkr * shares_vested).quantize(Decimal("0.01"))

    income = Income(
        user_id=user.id,
        tax_year=_tax_year_for(vesting_date),
        source_type="rsu",
        amount=total_native,
        currency=fmv_per_share_money.currency,
        fx_rate=fmv_per_share_money.fx_rate,
        fx_source=fmv_per_share_money.fx_source,
        fx_date=fmv_per_share_money.fx_date,
        amount_lkr=total_lkr,
        source_country=(source_country or None),
        evidence_refs=[
            {
                "type": "rsu_vesting_event",
                "ref_id": int(event.id),
                "ticker": ticker_clean,
                "shares": str(shares_vested),
            }
        ],
        rsu_vesting_id=event.id,
    )
    db.session.add(income)

    # Step 3: idempotent income_sources update.
    sources = list(user.income_sources or [])
    if "rsu" not in sources:
        sources.append("rsu")
        user.income_sources = sources
        # Force SQLAlchemy to register the change on a JSON column
        # (in-place mutation isn't detected without flag_modified).
        try:
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(user, "income_sources")
        except Exception:  # pragma: no cover
            pass

    db.session.commit()
    logger.info(
        "RSU vesting recorded: user=%s ticker=%s shares=%s vesting_date=%s "
        "total_lkr=%s source_country=%s vesting_id=%s",
        user.id, ticker_clean, shares_vested, vesting_date, total_lkr,
        source_country, event.id,
    )
    return event


# ---------------------------------------------------------------------------
# Sale
# ---------------------------------------------------------------------------
def record_rsu_sale(
    user,
    vesting_event_id: int,
    sale_date: date,
    sale_price_per_share_money: Money,
    shares_sold: Decimal | None = None,
) -> AssetDisposal:
    """Record an RSU sale → creates one AssetDisposal row.

    The acquisition leg is reconstructed from the RSUVestingEvent's
    persisted FMV (fair_market_value_money JSON) — this is the LKR
    basis the gain is computed against.

    gain_lkr = (sale_price.amount_lkr − vesting_fmv.amount_lkr) × shares_sold

    Args:
        user:                       The User ORM row.
        vesting_event_id:           PK of the RSUVestingEvent.
        sale_date:                  The disposal date.
        sale_price_per_share_money: Money — sale price per share, in source
                                    currency at sale-date FX.
        shares_sold:                Decimal. Default = the vesting event's
                                    full share count (read from the
                                    Income.evidence_refs.shares field).

    Returns:
        The newly-persisted AssetDisposal row.
    """
    event = RSUVestingEvent.query.get(int(vesting_event_id))
    if event is None:
        raise ValueError(f"RSUVestingEvent {vesting_event_id} not found")
    if int(event.user_id) != int(user.id):
        raise ValueError(
            f"RSUVestingEvent {vesting_event_id} belongs to user "
            f"{event.user_id}, not {user.id}"
        )

    # Default shares_sold = the vesting tranche's full count (carried on
    # the linked Income row's evidence_refs payload).
    if shares_sold is None:
        linked_income = (
            Income.query
            .filter_by(rsu_vesting_id=event.id, source_type="rsu")
            .first()
        )
        shares_carried = None
        if linked_income and linked_income.evidence_refs:
            for ref in linked_income.evidence_refs:
                if isinstance(ref, dict) and "shares" in ref:
                    shares_carried = Decimal(str(ref["shares"]))
                    break
        if shares_carried is None:
            raise ValueError(
                "shares_sold not provided and vesting event has no linked "
                "Income.evidence_refs[shares] to derive it from"
            )
        shares_sold = shares_carried
    else:
        shares_sold = Decimal(str(shares_sold))

    if shares_sold <= 0:
        raise ValueError(f"shares_sold must be > 0; got {shares_sold}")

    # Reconstruct vesting Money from the persisted JSON.
    fmv_json = dict(event.fair_market_value_money or {})
    if not fmv_json:
        raise ValueError(
            f"RSUVestingEvent {vesting_event_id} has empty "
            "fair_market_value_money — cannot compute basis"
        )
    acq_amount_per_share = Decimal(str(fmv_json["amount"]))
    acq_amount_lkr_per_share = Decimal(str(fmv_json["amount_lkr"]))
    acq_fx_rate = Decimal(str(fmv_json["fx_rate"]))
    acq_currency = str(fmv_json["currency"])
    acq_fx_source = str(fmv_json["fx_source"])
    acq_fx_date = date.fromisoformat(str(fmv_json["fx_date"]))

    # Totals
    acq_amount_total = (acq_amount_per_share * shares_sold)
    acq_amount_lkr_total = (acq_amount_lkr_per_share * shares_sold).quantize(
        Decimal("0.01")
    )

    disp_amount_total = (sale_price_per_share_money.amount * shares_sold)
    disp_amount_lkr_total = (
        sale_price_per_share_money.amount_lkr * shares_sold
    ).quantize(Decimal("0.01"))

    gain_lkr = (disp_amount_lkr_total - acq_amount_lkr_total).quantize(
        Decimal("0.01")
    )

    disposal = AssetDisposal(
        user_id=user.id,
        tax_year=_tax_year_for(sale_date),
        asset_type="rsu",
        acq_amount=acq_amount_total,
        acq_currency=acq_currency,
        acq_fx_rate=acq_fx_rate,
        acq_fx_source=acq_fx_source,
        acq_fx_date=acq_fx_date,
        acq_amount_lkr=acq_amount_lkr_total,
        disp_amount=disp_amount_total,
        disp_currency=sale_price_per_share_money.currency,
        disp_fx_rate=sale_price_per_share_money.fx_rate,
        disp_fx_source=sale_price_per_share_money.fx_source,
        disp_fx_date=sale_price_per_share_money.fx_date,
        disp_amount_lkr=disp_amount_lkr_total,
        gain_lkr=gain_lkr,
        acquisition_date=event.vesting_date,
        disposal_date=sale_date,
        source_country=event.source_country,
        asset_identifier=f"RSU:{event.id}:{(event.ticker or 'UNKNOWN')}",
        evidence_refs=[
            {
                "type": "rsu_sale",
                "vesting_event_id": int(event.id),
                "ticker": event.ticker,
                "shares_sold": str(shares_sold),
            }
        ],
    )
    db.session.add(disposal)
    db.session.commit()
    logger.info(
        "RSU sale recorded: user=%s ticker=%s vesting=%s shares=%s "
        "sale_date=%s gain_lkr=%s",
        user.id, event.ticker, event.id, shares_sold, sale_date, gain_lkr,
    )
    return disposal


# ---------------------------------------------------------------------------
# Tax computation
# ---------------------------------------------------------------------------
def compute_rsu_tax(user, tax_year: str) -> dict[str, Any]:
    """Compute the RSU tax bill components for ``user`` in ``tax_year``.

    Reads:
      - All Income(source_type='rsu', tax_year=tax_year, user_id=user.id)
        → "vesting income" line item.
      - All AssetDisposal(asset_type='rsu', tax_year=tax_year,
        user_id=user.id) → "CGT" line item.

    For each foreign-source row, calls
    ``apply_foreign_tax_credit(sl_liability_lkr, income)`` to obtain the
    DTAA-adjusted net liability. Pre-Wave-X the stub returns (liability,
    None) so this is a no-op pass-through.

    NOTE: This function does NOT apply the SL bracket schedule — that is
    the tax engine's responsibility downstream (B11 surfaces the LKR
    amounts; the engine computes the tax). We return the inputs the tax
    engine needs + a per-row FTC seam result, leaving total liability to
    the engine.

    Tax-year shape: accepts either 'YYYY/YY' (canonical Income.tax_year) or
    'YYYY-YY' (S4 format used by aggregator). Internally normalises to
    'YYYY/YY' for the DB query.

    Returns dict:
        {
            "tax_year":             "2025/26",
            "vesting_total_lkr":    Decimal,   # sum of Income.amount_lkr
            "vesting_rows":         [dict, …], # per-row breakdown
            "cgt_gain_total_lkr":   Decimal,   # sum of AssetDisposal.gain_lkr
            "cgt_rows":             [dict, …], # per-row breakdown
            "dtaa_credits":         [ForeignTaxCredit, …],  # empty pre-Wave-X
            "dtaa_deferred":        True,      # banner flag
        }
    """
    # Normalise tax_year shape.
    ty = (tax_year or "").replace("-", "/")
    if "/" in ty and len(ty.split("/")[1]) == 4:
        # "2025/2026" → "2025/26"
        head, tail = ty.split("/")
        ty = f"{head}/{tail[2:]}"

    # Vesting income rows.
    vesting_rows = (
        Income.query
        .filter_by(user_id=user.id, source_type="rsu", tax_year=ty)
        .all()
    )
    vesting_total = Decimal("0")
    vesting_dicts: list[dict[str, Any]] = []
    dtaa_credits: list[Any] = []
    for row in vesting_rows:
        vesting_total += Decimal(str(row.amount_lkr))
        # Call the DTAA seam for every foreign-source row, even though the
        # stub returns None pre-Wave-X. The call site IS the seam — this is
        # what Wave-X drops in without rework.
        # We pass an arbitrary indicative liability (the row's amount_lkr)
        # so when the real DTAA engine lands, the credit computation has
        # something to gross-up against. The engine still owns the final
        # bracket-schedule calculation downstream.
        _net, ftc = apply_foreign_tax_credit(
            Decimal(str(row.amount_lkr)), row
        )
        if ftc is not None:
            dtaa_credits.append(ftc)
        vesting_dicts.append({
            "income_id": int(row.id),
            "rsu_vesting_id": int(row.rsu_vesting_id) if row.rsu_vesting_id else None,
            "amount_lkr": Decimal(str(row.amount_lkr)),
            "currency": row.currency,
            "fx_rate": Decimal(str(row.fx_rate)),
            "source_country": row.source_country,
            "fx_date": row.fx_date.isoformat() if row.fx_date else None,
        })

    # CGT rows from AssetDisposal.
    cgt_rows = (
        AssetDisposal.query
        .filter_by(user_id=user.id, asset_type="rsu", tax_year=ty)
        .all()
    )
    cgt_total = Decimal("0")
    cgt_dicts: list[dict[str, Any]] = []
    for row in cgt_rows:
        cgt_total += Decimal(str(row.gain_lkr))
        cgt_dicts.append({
            "disposal_id": int(row.id),
            "asset_identifier": row.asset_identifier,
            "acquisition_date": row.acquisition_date.isoformat() if row.acquisition_date else None,
            "disposal_date": row.disposal_date.isoformat() if row.disposal_date else None,
            "acq_amount_lkr": Decimal(str(row.acq_amount_lkr)),
            "disp_amount_lkr": Decimal(str(row.disp_amount_lkr)),
            "gain_lkr": Decimal(str(row.gain_lkr)),
            "source_country": row.source_country,
        })

    return {
        "tax_year": ty,
        "vesting_total_lkr": vesting_total.quantize(Decimal("0.01")),
        "vesting_rows": vesting_dicts,
        "cgt_gain_total_lkr": cgt_total.quantize(Decimal("0.01")),
        "cgt_rows": cgt_dicts,
        "dtaa_credits": dtaa_credits,
        # Banner flag: True until Wave-X (B9) DTAA engine lands. UI surfaces
        # the "Treaty relief coming in a future update" banner whenever this
        # is True AND there's at least one foreign-source row.
        "dtaa_deferred": any(r.source_country for r in vesting_rows + cgt_rows),
    }


__all__ = [
    "record_rsu_vesting",
    "record_rsu_sale",
    "compute_rsu_tax",
]
