"""fiesta.crypto.routes — B13 Crypto/CGT Flask surface (MS3).

Routes (all login-gated):
    GET  /income/crypto                  — positions + disposals history
    GET  /income/crypto/positions        — open-positions view (one per asset)
    GET  /income/crypto/buy              — manual buy form
    POST /income/crypto/buy              — record one acquisition
    GET  /income/crypto/sell             — manual sell form
    POST /income/crypto/sell             — record one disposal (FIFO)
    GET  /income/crypto/import           — CSV bulk import form
    POST /income/crypto/import           — process CSV rows

CSV import payload shape (textarea, one row per line, comma-separated):

    Type, Date (YYYY-MM-DD), Asset, Shares, Price per share, Total, Currency, Exchange

Where:
    Type     = Buy | Sell
    Asset    = symbol (BTC | ETH | SOL | USDC | …)
    Shares   = Decimal (fractional ok)
    Price    = per-share price in `Currency`
    Total    = TOTAL spent/received in `Currency` (e.g. shares × price)
    Currency = ISO-4217 (USD | LKR | …)
    Exchange = free-text custodian (Coinbase | Binance | Kraken | …)

Either `Price per share` OR `Total` may be omitted (we derive the missing
one from the other). If both present, `Total` wins as the canonical money
amount (covers exchange fees that make per-share × shares != total).

Persistence: routes thin-wrap fiesta.tax.crypto_cgt — the engine owns
transactionality, FIFO matching, and the DTAA seam.

Provenance: Inventory §B13, Design Lock 2 §5/§6/§8.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defensive imports (match the RSU routes pattern)
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
from fiesta.tax.crypto_cgt import (
    compute_crypto_cgt,
    record_crypto_acquisition,
    record_crypto_disposal,
    _tax_year_for,
)


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
bp = Blueprint(
    "fiesta_crypto",
    __name__,
    url_prefix="/income/crypto",
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


def _resolve_fx_rate(currency: str, fx_date: date) -> tuple[Decimal, str]:
    """Resolve an FX rate to LKR — mirrors the helper in fiesta.rsu.routes.

    Pre-Wave-X, conservative fallback (USD=302, GBP=385, EUR=327, AUD=198).
    Wave-X B9 CBSL feed will replace this.
    """
    try:
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


def _money_from_total(
    total: Decimal,
    currency: str,
    on_date: date,
) -> Money:
    """Build a Money from a total amount in `currency` on `on_date`."""
    fx_rate, fx_source = _resolve_fx_rate(currency, on_date)
    return Money(
        amount=total,
        currency=currency.upper() if currency else "LKR",
        fx_rate=fx_rate,
        fx_source=fx_source,
        fx_date=on_date,
    )


# ---------------------------------------------------------------------------
# GET /income/crypto — history view (positions + disposals)
# ---------------------------------------------------------------------------
@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def history():
    user = _current_user_obj()
    if not user:
        abort(401)

    from fiesta.tax.models import AssetDisposal, CryptoPosition

    positions = (
        CryptoPosition.query
        .filter_by(user_id=user.id)
        .order_by(CryptoPosition.acquisition_date.desc())
        .all()
    )
    disposals = (
        AssetDisposal.query
        .filter_by(user_id=user.id, asset_type="crypto")
        .order_by(AssetDisposal.disposal_date.desc())
        .all()
    )

    # Current tax year summary panel.
    from datetime import date as _date
    current_ty = _tax_year_for(_date.today())
    try:
        summary = compute_crypto_cgt(user, current_ty)
    except Exception as exc:  # pragma: no cover
        logger.exception("crypto summary failed: %s", exc)
        summary = None

    return render_template(
        "crypto/history.html",
        positions=positions,
        disposals=disposals,
        summary=summary,
        current_tax_year=current_ty,
    )


# ---------------------------------------------------------------------------
# GET /income/crypto/positions — open-positions view
# ---------------------------------------------------------------------------
@bp.route("/positions", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def positions():
    user = _current_user_obj()
    if not user:
        abort(401)

    from fiesta.tax.models import CryptoPosition

    open_rows = (
        CryptoPosition.query
        .filter(
            CryptoPosition.user_id == user.id,
            CryptoPosition.shares_remaining > 0,
        )
        .order_by(
            CryptoPosition.asset_identifier.asc(),
            CryptoPosition.acquisition_date.asc(),
        )
        .all()
    )

    # Aggregate per asset.
    by_asset: dict[str, dict[str, Any]] = {}
    for p in open_rows:
        a = p.asset_identifier
        agg = by_asset.setdefault(a, {
            "asset": a,
            "shares_open": Decimal("0"),
            "cost_basis_lkr": Decimal("0"),
            "lots": [],
        })
        rem = Decimal(str(p.shares_remaining))
        agg["shares_open"] += rem
        agg["cost_basis_lkr"] += (
            Decimal(str(p.acq_amount_lkr_per_share)) * rem
        ).quantize(Decimal("0.01"))
        agg["lots"].append(p)

    return render_template(
        "crypto/positions.html",
        by_asset=by_asset,
    )


# ---------------------------------------------------------------------------
# GET/POST /income/crypto/buy — single-acquisition form
# ---------------------------------------------------------------------------
@bp.route("/buy", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def buy_form():
    return render_template("crypto/buy.html", error=None, position=None)


@bp.route("/buy", methods=["POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def buy_submit():
    user = _current_user_obj()
    if not user:
        abort(401)

    asset = (request.form.get("asset", "") or "").strip().upper()
    if not asset:
        return render_template("crypto/buy.html",
                               error="Asset is required (e.g. BTC, ETH).",
                               position=None), 400

    try:
        acq_date = date.fromisoformat(request.form.get("acquisition_date", "").strip())
    except ValueError:
        return render_template("crypto/buy.html",
                               error="Invalid acquisition date (expected YYYY-MM-DD).",
                               position=None), 400

    try:
        shares = Decimal(request.form.get("shares", "").strip())
    except InvalidOperation:
        return render_template("crypto/buy.html",
                               error="Invalid shares value.",
                               position=None), 400
    if shares <= 0:
        return render_template("crypto/buy.html",
                               error="Shares must be > 0.",
                               position=None), 400

    try:
        total = Decimal(request.form.get("total", "").strip())
    except InvalidOperation:
        return render_template("crypto/buy.html",
                               error="Invalid total amount.",
                               position=None), 400
    if total <= 0:
        return render_template("crypto/buy.html",
                               error="Total must be > 0.",
                               position=None), 400

    currency = (request.form.get("currency", "USD") or "USD").upper()
    source_country = (request.form.get("source_country", "") or "").upper() or None

    money = _money_from_total(total, currency, acq_date)

    try:
        position = record_crypto_acquisition(
            user=user,
            asset_identifier=asset,
            acquisition_money=money,
            acquisition_date=acq_date,
            shares=shares,
            source_country=source_country,
        )
    except Exception as exc:
        logger.exception("Crypto buy failed: %s", exc)
        return render_template("crypto/buy.html",
                               error=str(exc),
                               position=None), 400

    return render_template("crypto/buy.html", error=None, position=position)


# ---------------------------------------------------------------------------
# GET/POST /income/crypto/sell — single-disposal form
# ---------------------------------------------------------------------------
@bp.route("/sell", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def sell_form():
    user = _current_user_obj()
    if not user:
        abort(401)

    # Open-positions context — populate dropdown of assets the user actually holds.
    from fiesta.tax.models import CryptoPosition
    open_assets = (
        db_session_query_open_assets(user.id)
        if False else
        [
            row[0]
            for row in (
                CryptoPosition.query
                .with_entities(CryptoPosition.asset_identifier)
                .filter(
                    CryptoPosition.user_id == user.id,
                    CryptoPosition.shares_remaining > 0,
                )
                .distinct()
                .all()
            )
        ]
    )
    return render_template("crypto/sell.html",
                           open_assets=open_assets,
                           error=None,
                           disposals=None)


def db_session_query_open_assets(user_id):  # pragma: no cover - reserved seam
    return []


@bp.route("/sell", methods=["POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def sell_submit():
    user = _current_user_obj()
    if not user:
        abort(401)

    asset = (request.form.get("asset", "") or "").strip().upper()
    if not asset:
        return render_template("crypto/sell.html",
                               open_assets=[],
                               error="Asset is required.",
                               disposals=None), 400

    try:
        disp_date = date.fromisoformat(request.form.get("disposal_date", "").strip())
    except ValueError:
        return render_template("crypto/sell.html",
                               open_assets=[],
                               error="Invalid disposal date (expected YYYY-MM-DD).",
                               disposals=None), 400

    try:
        shares = Decimal(request.form.get("shares", "").strip())
    except InvalidOperation:
        return render_template("crypto/sell.html",
                               open_assets=[],
                               error="Invalid shares value.",
                               disposals=None), 400
    if shares <= 0:
        return render_template("crypto/sell.html",
                               open_assets=[],
                               error="Shares must be > 0.",
                               disposals=None), 400

    try:
        total = Decimal(request.form.get("total", "").strip())
    except InvalidOperation:
        return render_template("crypto/sell.html",
                               open_assets=[],
                               error="Invalid total amount.",
                               disposals=None), 400
    if total <= 0:
        return render_template("crypto/sell.html",
                               open_assets=[],
                               error="Total must be > 0.",
                               disposals=None), 400

    currency = (request.form.get("currency", "USD") or "USD").upper()
    money = _money_from_total(total, currency, disp_date)

    try:
        disposals = record_crypto_disposal(
            user=user,
            asset_identifier=asset,
            disposal_money=money,
            disposal_date=disp_date,
            shares_disposed=shares,
            cost_basis_method="FIFO",
        )
    except ValueError as exc:
        # Over-sale or bad method.
        return render_template("crypto/sell.html",
                               open_assets=[],
                               error=str(exc),
                               disposals=None), 400
    except Exception as exc:
        logger.exception("Crypto sell failed: %s", exc)
        return render_template("crypto/sell.html",
                               open_assets=[],
                               error=str(exc),
                               disposals=None), 400

    return render_template("crypto/sell.html",
                           open_assets=[],
                           error=None,
                           disposals=disposals)


# ---------------------------------------------------------------------------
# GET /income/crypto/import — CSV bulk import form
# ---------------------------------------------------------------------------
@bp.route("/import", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def import_form():
    return render_template("crypto/import.html",
                           created_buys=None, created_sells=None,
                           errors=None, raw="", projected=None)


def _parse_csv_rows(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse a textarea CSV-style payload into structured rows.

    Header row optional — auto-detected if line 1 contains 'type' or 'asset'.
    Returns (rows, errors). Partial-success allowed: bad rows go to errors,
    good rows go to rows.
    """
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    if not text or not text.strip():
        return rows, ["No data submitted."]

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return rows, ["No data submitted."]

    first_lower = lines[0].lower()
    if ("type" in first_lower and "asset" in first_lower) or first_lower.startswith("type,"):
        lines = lines[1:]

    for idx, raw in enumerate(lines, start=1):
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) < 5:
            errors.append(
                f"Line {idx}: expected at least 5 comma-separated values "
                f"(type, date, asset, shares, price/total, [currency], [exchange])"
            )
            continue
        type_str = parts[0].lower()
        if type_str not in ("buy", "sell"):
            errors.append(f"Line {idx}: type must be 'Buy' or 'Sell'; got {parts[0]!r}")
            continue
        try:
            row_date = date.fromisoformat(parts[1])
        except ValueError:
            errors.append(f"Line {idx}: invalid date {parts[1]!r}; expected YYYY-MM-DD")
            continue
        asset = parts[2].upper()
        if not asset:
            errors.append(f"Line {idx}: asset required")
            continue
        try:
            shares = Decimal(parts[3])
        except InvalidOperation:
            errors.append(f"Line {idx}: invalid shares {parts[3]!r}")
            continue
        if shares <= 0:
            errors.append(f"Line {idx}: shares must be > 0; got {shares}")
            continue

        # Slot 4 is "Price per share" OR "Total". Slot 5 is the OTHER one
        # (or blank). Slot 6 is currency. Slot 7 is exchange.
        # Disambiguation: if BOTH 4 and 5 are numeric → 4=price/share, 5=total.
        # If only 4 numeric → treat as total. (Most CSVs export both.)
        price = None
        total = None
        try:
            price = Decimal(parts[4])
        except InvalidOperation:
            errors.append(f"Line {idx}: invalid price/total at slot 5: {parts[4]!r}")
            continue

        if len(parts) >= 6 and parts[5]:
            try:
                total = Decimal(parts[5])
            except InvalidOperation:
                # Not numeric → treat parts[5] as currency, derive total.
                total = (price * shares).quantize(Decimal("0.0001"))

        if total is None:
            total = (price * shares).quantize(Decimal("0.0001"))

        # Currency slot: 7 if total was a number, else 6.
        if len(parts) >= 7 and parts[6]:
            currency = parts[6].upper()
        elif len(parts) >= 6 and parts[5] and total is not None and total != (price * shares):
            # If parts[5] really was currency and not a total
            try:
                Decimal(parts[5])
                currency = "USD"
            except InvalidOperation:
                currency = parts[5].upper()
        else:
            currency = "USD"

        exchange = parts[7] if len(parts) >= 8 else (parts[6] if len(parts) >= 7 and not currency else "")

        rows.append({
            "type": type_str,
            "date": row_date,
            "asset": asset,
            "shares": shares,
            "price": price,
            "total": total,
            "currency": currency,
            "exchange": (exchange or "").strip(),
        })
    return rows, errors


def _exchange_to_country(exchange: str) -> str | None:
    """Heuristic mapping of exchange name → ISO-3166-1 alpha-2 source country.

    Used to populate source_country for DTAA seam. Conservative — returns
    None for anything we don't recognise (caller will leave source_country
    as None and the disposal won't trigger the DTAA banner).
    """
    if not exchange:
        return None
    e = exchange.lower()
    if any(k in e for k in ("coinbase", "kraken us", "gemini")):
        return "US"
    if "binance.us" in e:
        return "US"
    if "binance" in e:
        return None  # multi-jurisdiction; unknown
    if any(k in e for k in ("bitsila", "lanka", "ceylon")):
        return "LK"
    if "kraken" in e and "uk" in e:
        return "GB"
    if "bitstamp" in e:
        return None  # EU multi
    return None


@bp.route("/import", methods=["POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def import_submit():
    user = _current_user_obj()
    if not user:
        abort(401)

    raw = (request.form.get("csv_rows") or "").strip()
    rows, parse_errors = _parse_csv_rows(raw)

    created_buys: list[dict[str, Any]] = []
    created_sells: list[dict[str, Any]] = []
    create_errors: list[str] = list(parse_errors)

    # IMPORTANT: process in date order, then within a date Buy before Sell.
    # FIFO is order-sensitive; if a CSV mixes buys + sells in random order
    # we must replay in chronological order so the matcher sees a valid
    # cumulative state.
    def _row_sort_key(r):
        return (r["date"], 0 if r["type"] == "buy" else 1)
    rows_sorted = sorted(rows, key=_row_sort_key)

    for row in rows_sorted:
        try:
            money = _money_from_total(row["total"], row["currency"], row["date"])
            source_country = _exchange_to_country(row.get("exchange", ""))

            if row["type"] == "buy":
                pos = record_crypto_acquisition(
                    user=user,
                    asset_identifier=row["asset"],
                    acquisition_money=money,
                    acquisition_date=row["date"],
                    shares=row["shares"],
                    source_country=source_country,
                    evidence_refs=[{
                        "type": "csv_import",
                        "exchange": row.get("exchange") or None,
                    }],
                )
                created_buys.append({
                    "position_id": pos.id,
                    "asset": pos.asset_identifier,
                    "acquisition_date": pos.acquisition_date.isoformat(),
                    "shares": str(row["shares"]),
                    "total_lkr": str(money.amount_lkr),
                    "currency": money.currency,
                    "source_country": source_country or "",
                })
            else:
                disps = record_crypto_disposal(
                    user=user,
                    asset_identifier=row["asset"],
                    disposal_money=money,
                    disposal_date=row["date"],
                    shares_disposed=row["shares"],
                    cost_basis_method="FIFO",
                    evidence_refs=[{
                        "type": "csv_import",
                        "exchange": row.get("exchange") or None,
                    }],
                )
                total_gain = sum(
                    (Decimal(str(d.gain_lkr)) for d in disps), Decimal("0")
                ).quantize(Decimal("0.01"))
                created_sells.append({
                    "asset": row["asset"],
                    "disposal_date": row["date"].isoformat(),
                    "shares": str(row["shares"]),
                    "total_lkr": str(money.amount_lkr),
                    "currency": money.currency,
                    "gain_lkr": str(total_gain),
                    "lots_matched": len(disps),
                })
        except ValueError as exc:
            # Over-sale or bad method — surface to UI.
            create_errors.append(
                f"{row.get('type','?').title()} {row.get('asset','?')} "
                f"{row.get('date','?')}: {exc}"
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Crypto import row failed: %s", row)
            create_errors.append(
                f"{row.get('asset','?')} {row.get('date','?')}: {exc}"
            )

    # Projected current-year impact.
    projected = None
    if created_buys or created_sells:
        try:
            ty = rows_sorted[-1]["date"] if rows_sorted else None
            if ty:
                projected = compute_crypto_cgt(user, _tax_year_for(ty))
        except Exception as exc:  # pragma: no cover
            logger.warning("compute_crypto_cgt projection failed: %s", exc)

    return render_template(
        "crypto/import.html",
        created_buys=created_buys,
        created_sells=created_sells,
        errors=create_errors,
        projected=projected,
        raw=raw if create_errors else "",
    )


# ---------------------------------------------------------------------------
# C6 Day-0 fix (2026-05-27) — /income/crypto/new alias
# ---------------------------------------------------------------------------
# The income-source picker offers "Crypto holdings + disposals" and the
# customer-flow audit (CUSTOMER_FLOW_AUDIT_2026-05-26, finding C6) called
# /income/crypto/new a 404. The canonical entry point for a new crypto
# acquisition is /income/crypto/buy. We alias /new -> /buy so any
# downstream link generator that follows the /income/<source>/new
# convention (matching /income/employment/new + /income/business/new)
# resolves cleanly.
@bp.route("/new", methods=["GET"])
@login_required
def new_alias():
    """C6 alias: /income/crypto/new -> /income/crypto/buy (302).

    Paywall is intentionally NOT applied here — launch decision 1
    (2026-05-26) says users can record data without paying. The downstream
    /buy handler is the canonical surface.
    """
    from flask import redirect as _redirect
    return _redirect("/income/crypto/buy", code=302)


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------
def register_blueprint(app) -> None:
    """Register the B13 Crypto blueprint with the Flask app."""
    app.register_blueprint(bp)
    logger.info("FIESTA B13 Crypto/CGT blueprint registered at /income/crypto")


__all__ = ["bp", "register_blueprint"]
