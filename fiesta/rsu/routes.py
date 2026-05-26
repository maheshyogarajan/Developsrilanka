"""fiesta.rsu.routes — B11 RSU classifier Flask surface (MS2 Stage E.1).

Routes (all login-gated):
    GET  /income/rsu                     — vesting + sale history (this user)
    GET  /income/rsu/import              — CSV-style bulk vesting import form
    POST /income/rsu/import              — bulk-create vesting events
    GET  /income/rsu/<vesting_id>/sell   — sale form for one vesting tranche
    POST /income/rsu/<vesting_id>/sell   — record an AssetDisposal

CSV import payload shape (textarea, one row per line, comma-separated):
    Ticker, Vesting Date (YYYY-MM-DD), Shares, FMV per share (USD),
    Source Country (ISO-3166-1 alpha-2, optional — defaults to US)

Example:
    MSFT, 2025-08-15, 12.5, 415.20, US
    GOOG, 2025-09-01, 8, 170.50, US

Persistence: routes thin-wrap fiesta.tax.rsu_engine. The engine owns
transactionality and the seam to apply_foreign_tax_credit.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defensive imports
# ---------------------------------------------------------------------------
try:
    from flask import (
        Blueprint, render_template, request, jsonify, redirect, url_for,
        flash, abort,
    )
    _HAS_FLASK = True
except ImportError:  # pragma: no cover
    _HAS_FLASK = False

    class _Stub:
        def __init__(self, *a, **kw): pass
        def route(self, *a, **kw):
            def deco(fn): return fn
            return deco

    class Blueprint(_Stub):  # type: ignore
        pass

    def render_template(*a, **kw): return ""  # type: ignore
    def jsonify(*a, **kw): return {"_stub": True}  # type: ignore
    def redirect(*a, **kw): return None  # type: ignore
    def url_for(*a, **kw): return "#"  # type: ignore
    def flash(*a, **kw): return None  # type: ignore
    def abort(*a, **kw): return None  # type: ignore
    request = None  # type: ignore

try:
    from flask_login import login_required, current_user
    _HAS_LOGIN = True
except ImportError:  # pragma: no cover
    _HAS_LOGIN = False

    def login_required(fn):  # type: ignore
        return fn
    current_user = None  # type: ignore

try:
    from fiesta.paywall.gate import paywall_required
    _HAS_PAYWALL = True
except ImportError:  # pragma: no cover
    _HAS_PAYWALL = False

    def paywall_required(*args, **kwargs):  # type: ignore
        def deco(fn):
            return fn
        return deco


from fiesta.tax.money import Money
from fiesta.tax.rsu_engine import (
    compute_rsu_tax,
    record_rsu_sale,
    record_rsu_vesting,
)


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
bp = Blueprint(
    "fiesta_rsu",
    __name__,
    url_prefix="/income/rsu",
    template_folder="../../templates",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _current_user_obj():
    if not _HAS_LOGIN or current_user is None:
        return None
    if not getattr(current_user, "is_authenticated", False):
        return None
    return current_user


def _parse_csv_rows(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse a textarea CSV-style payload into structured rows.

    Returns (rows, errors). One error string per malformed line; valid rows
    are kept (partial-success allowed). Header row optional — auto-detected
    by checking if line 1 contains 'ticker' (case-insensitive).
    """
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    if not text or not text.strip():
        return rows, ["No data submitted."]

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return rows, ["No data submitted."]

    # Skip optional header row.
    if "ticker" in lines[0].lower():
        lines = lines[1:]

    for idx, raw in enumerate(lines, start=1):
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) < 4:
            errors.append(
                f"Line {idx}: expected at least 4 comma-separated values "
                f"(ticker, vesting_date, shares, fmv_per_share[, country])"
            )
            continue
        ticker = parts[0].upper()
        try:
            vesting_date = date.fromisoformat(parts[1])
        except ValueError:
            errors.append(f"Line {idx}: invalid vesting date {parts[1]!r}; expected YYYY-MM-DD")
            continue
        try:
            shares = Decimal(parts[2])
        except InvalidOperation:
            errors.append(f"Line {idx}: invalid shares value {parts[2]!r}")
            continue
        if shares <= 0:
            errors.append(f"Line {idx}: shares must be > 0; got {shares}")
            continue
        try:
            fmv_usd = Decimal(parts[3])
        except InvalidOperation:
            errors.append(f"Line {idx}: invalid FMV value {parts[3]!r}")
            continue
        if fmv_usd <= 0:
            errors.append(f"Line {idx}: FMV must be > 0; got {fmv_usd}")
            continue
        source_country = (parts[4].upper() if len(parts) >= 5 and parts[4] else "US")
        rows.append({
            "ticker": ticker,
            "vesting_date": vesting_date,
            "shares": shares,
            "fmv_usd": fmv_usd,
            "source_country": source_country,
        })
    return rows, errors


def _resolve_fx_rate(currency: str, fx_date: date) -> tuple[Decimal, str]:
    """Resolve an FX rate to LKR for ``currency`` on ``fx_date``.

    Returns (rate, source). Pre-Wave-X, we use a conservative fallback
    bracket of 302 LKR/USD (matches the hub-card USD divisor in app.py).
    GBP/EUR/AUD use rough cross-rate multipliers — these are NOT canonical
    and the UI surfaces a "manual override recommended" note. B9 CBSL FX
    feed will replace this with real daily middle-rate data.
    """
    try:
        # Prefer the existing CBSL bridge if available.
        from cbsl_fx_service import get_cbsl_middle_rate  # type: ignore
        rate = get_cbsl_middle_rate(currency.upper(), fx_date)
        if rate:
            return Decimal(str(rate)), "CBSL"
    except Exception:
        pass

    cur = (currency or "").upper()
    fallback_map = {
        "USD": (Decimal("302.00"), "manual"),
        "GBP": (Decimal("385.00"), "manual"),
        "EUR": (Decimal("327.00"), "manual"),
        "AUD": (Decimal("198.00"), "manual"),
        "LKR": (Decimal("1.0"), "lkr_native"),
    }
    rate, src = fallback_map.get(cur, (Decimal("302.00"), "manual"))
    return rate, src


# ---------------------------------------------------------------------------
# GET /income/rsu — history view
# ---------------------------------------------------------------------------
@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def history():
    user = _current_user_obj()
    if not user:
        abort(401)

    from fiesta.tax.models import RSUVestingEvent, AssetDisposal

    vestings = (
        RSUVestingEvent.query
        .filter_by(user_id=user.id)
        .order_by(RSUVestingEvent.vesting_date.desc())
        .all()
    )
    sales = (
        AssetDisposal.query
        .filter_by(user_id=user.id, asset_type="rsu")
        .order_by(AssetDisposal.disposal_date.desc())
        .all()
    )

    return render_template(
        "rsu/history.html",
        vestings=vestings,
        sales=sales,
    )


# ---------------------------------------------------------------------------
# GET/POST /income/rsu/import — bulk vesting import
# ---------------------------------------------------------------------------
@bp.route("/import", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def import_form():
    return render_template("rsu/import.html", created=None, errors=None, raw="")


@bp.route("/import", methods=["POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def import_submit():
    user = _current_user_obj()
    if not user:
        abort(401)

    raw = (request.form.get("csv_rows") or "").strip()
    rows, parse_errors = _parse_csv_rows(raw)

    created: list[dict[str, Any]] = []
    create_errors: list[str] = list(parse_errors)

    for row in rows:
        try:
            fx_rate, fx_source = _resolve_fx_rate("USD", row["vesting_date"])
            fmv = Money(
                amount=row["fmv_usd"],
                currency="USD",
                fx_rate=fx_rate,
                fx_source=fx_source,
                fx_date=row["vesting_date"],
            )
            event = record_rsu_vesting(
                user=user,
                ticker=row["ticker"],
                vesting_date=row["vesting_date"],
                shares_vested=row["shares"],
                fmv_per_share_money=fmv,
                source_country=row["source_country"],
            )
            created.append({
                "vesting_id": event.id,
                "ticker": event.ticker,
                "vesting_date": event.vesting_date.isoformat(),
                "shares": str(row["shares"]),
                "fmv_lkr_per_share": str(fmv.amount_lkr),
                "total_lkr": str((fmv.amount_lkr * row["shares"]).quantize(Decimal("0.01"))),
                "source_country": event.source_country,
            })
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("RSU import row failed: %s", row)
            create_errors.append(f"{row.get('ticker','?')} {row.get('vesting_date','?')}: {exc}")

    # Compute projected SL tax-bill impact for this batch.
    projected = None
    if created:
        # Use the tax-year of the earliest vesting row for the projection.
        try:
            ty = rows[0]["vesting_date"]
            from fiesta.tax.rsu_engine import _tax_year_for
            tax_year_str = _tax_year_for(ty)
            projected = compute_rsu_tax(user, tax_year_str)
        except Exception as exc:  # pragma: no cover
            logger.warning("compute_rsu_tax projection failed: %s", exc)

    return render_template(
        "rsu/import.html",
        created=created,
        errors=create_errors,
        projected=projected,
        raw=raw if create_errors else "",
    )


# ---------------------------------------------------------------------------
# GET/POST /income/rsu/<vesting_id>/sell — sale form
# ---------------------------------------------------------------------------
@bp.route("/<int:vesting_id>/sell", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def sell_form(vesting_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)

    from fiesta.tax.models import RSUVestingEvent
    event = RSUVestingEvent.query.get(vesting_id)
    if event is None or int(event.user_id) != int(user.id):
        abort(404)

    return render_template("rsu/sell.html", event=event, error=None, disposal=None)


@bp.route("/<int:vesting_id>/sell", methods=["POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def sell_submit(vesting_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)

    from fiesta.tax.models import RSUVestingEvent
    event = RSUVestingEvent.query.get(vesting_id)
    if event is None or int(event.user_id) != int(user.id):
        abort(404)

    try:
        sale_date = date.fromisoformat(request.form.get("sale_date", "").strip())
    except ValueError:
        return render_template("rsu/sell.html", event=event,
                               error="Invalid sale date (expected YYYY-MM-DD)",
                               disposal=None), 400

    try:
        sale_price = Decimal(request.form.get("sale_price_per_share", "").strip())
    except InvalidOperation:
        return render_template("rsu/sell.html", event=event,
                               error="Invalid sale price",
                               disposal=None), 400

    shares_input = request.form.get("shares_sold", "").strip()
    shares_sold = None
    if shares_input:
        try:
            shares_sold = Decimal(shares_input)
        except InvalidOperation:
            return render_template("rsu/sell.html", event=event,
                                   error="Invalid shares count",
                                   disposal=None), 400

    fx_rate, fx_source = _resolve_fx_rate("USD", sale_date)
    sale_money = Money(
        amount=sale_price,
        currency="USD",
        fx_rate=fx_rate,
        fx_source=fx_source,
        fx_date=sale_date,
    )

    try:
        disposal = record_rsu_sale(
            user=user,
            vesting_event_id=event.id,
            sale_date=sale_date,
            sale_price_per_share_money=sale_money,
            shares_sold=shares_sold,
        )
    except Exception as exc:
        logger.exception("RSU sale failed for vesting %s: %s", vesting_id, exc)
        return render_template("rsu/sell.html", event=event,
                               error=str(exc),
                               disposal=None), 400

    return render_template("rsu/sell.html", event=event, error=None,
                           disposal=disposal)


# ---------------------------------------------------------------------------
# C6 Day-0 fix (2026-05-27) — /income/rsu/new alias
# ---------------------------------------------------------------------------
# The income-source picker offers "RSU / equity compensation" and the
# customer-flow audit (CUSTOMER_FLOW_AUDIT_2026-05-26, finding C6) called
# /income/rsu/new a 404. The canonical entry point for RSU is the bulk
# import form at /income/rsu/import. We alias /new -> /import so any
# downstream link generator that follows the /income/<source>/new
# convention (matching /income/employment/new + /income/business/new)
# resolves cleanly.
@bp.route("/new", methods=["GET"])
@login_required
def new_alias():
    """C6 alias: /income/rsu/new -> /income/rsu/import (302).

    Paywall is intentionally NOT applied here — launch decision 1
    (2026-05-26) says users can record data without paying. The downstream
    /import handler is the canonical surface; if it is paywalled, that
    decision is still honoured there.
    """
    from flask import redirect as _redirect
    return _redirect("/income/rsu/import", code=302)


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------
def register_blueprint(app) -> None:
    """Register the B11 RSU blueprint with the Flask app."""
    app.register_blueprint(bp)
    logger.info("FIESTA B11 RSU blueprint registered at /income/rsu")


__all__ = ["bp", "register_blueprint"]
