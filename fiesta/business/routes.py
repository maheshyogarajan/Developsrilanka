"""fiesta.business.routes — B12 Business Income Flask surface (MS3 Stage E.1).

Routes (all login-gated, paywall-tier 'self_file'):

    GET  /income/business/                      — list this user's businesses
    GET  /income/business/new                   — add a new business form
    POST /income/business/new                   — create + paired Income row
    GET  /income/business/<id>                  — detail view: gross + expenses
                                                  + computed taxable profit
    POST /income/business/<id>/edit             — update business metadata
                                                  or gross receipts
    GET  /income/business/<id>/expense/new      — add expense form
    POST /income/business/<id>/expense/new      — create expense
    POST /income/business/<id>/expense/<exp_id>/edit — edit expense
    POST /income/business/<id>/expense/<exp_id>/delete — delete expense

Currency support: LKR + foreign (USD/GBP/EUR/AUD with manual or CBSL FX
rate). Foreign business → source_country required for DTAA seam routing.

Persistence: routes thin-wrap fiesta.tax.business_income. The engine
owns transactionality + idempotency + the apply_foreign_tax_credit seam.

Provenance: Inventory §B12.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defensive imports
# ---------------------------------------------------------------------------
try:
    from flask import (
        Blueprint, render_template, request, redirect, url_for,
        flash, abort, g,
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
    def redirect(*a, **kw): return None  # type: ignore
    def url_for(*a, **kw): return "#"  # type: ignore
    def flash(*a, **kw): return None  # type: ignore
    def abort(*a, **kw): return None  # type: ignore
    request = None  # type: ignore
    g = None  # type: ignore

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


from fiesta.tax.business_income import (
    BUSINESS_TYPES,
    EXPENSE_CATEGORIES,
    add_business_expense,
    compute_business_tax,
    delete_business_expense,
    edit_business_expense,
    get_business_for_user,
    list_businesses_for_user,
    record_business_income,
)
from fiesta.tax.money import Money


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
bp = Blueprint(
    "fiesta_business",
    __name__,
    url_prefix="/income/business",
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


def _layout() -> str:
    """Return the layout template per FIESTA shell contract."""
    try:
        return getattr(g, "layout_template", "layout_fiesta.html")
    except Exception:
        return "layout_fiesta.html"


def _resolve_fx_rate(currency: str, fx_date: date) -> tuple[Decimal, str]:
    """Resolve an FX rate to LKR for ``currency`` on ``fx_date``.

    Returns (rate, source). Matches the RSU route's resolver — CBSL when
    available, fallback bracket otherwise. The B9 CBSL FX feed will own
    this once it lands; until then the fallback keeps the foreign-currency
    flow usable in dev/test.
    """
    cur = (currency or "").upper()
    if cur == "LKR":
        return Decimal("1.0"), "lkr_native"
    try:
        from cbsl_fx_service import get_cbsl_middle_rate  # type: ignore
        rate = get_cbsl_middle_rate(cur, fx_date)
        if rate:
            return Decimal(str(rate)), "CBSL"
    except Exception:
        pass
    fallback_map = {
        "USD": (Decimal("302.00"), "manual"),
        "GBP": (Decimal("385.00"), "manual"),
        "EUR": (Decimal("327.00"), "manual"),
        "AUD": (Decimal("198.00"), "manual"),
    }
    rate, src = fallback_map.get(cur, (Decimal("302.00"), "manual"))
    return rate, src


def _parse_money_form(
    amount_field: str = "amount",
    currency_field: str = "currency",
    date_field: str = "as_of_date",
    fx_rate_field: str = "fx_rate",
    fx_source_field: str = "fx_source",
) -> tuple[Optional[Money], Optional[str]]:
    """Parse a Money block out of request.form.

    Returns ``(money, error)``. If error is non-None, ``money`` is None and
    the caller should re-render the form with the message.
    """
    if request is None:
        return None, "no_request_context"
    try:
        amount = Decimal((request.form.get(amount_field) or "0").strip() or "0")
    except InvalidOperation:
        return None, f"Invalid {amount_field}"
    currency = (request.form.get(currency_field) or "LKR").upper().strip()
    try:
        fx_date_str = (request.form.get(date_field) or "").strip()
        as_of_date = date.fromisoformat(fx_date_str) if fx_date_str else date.today()
    except ValueError:
        return None, f"Invalid {date_field} (expected YYYY-MM-DD)"

    fx_rate_raw = (request.form.get(fx_rate_field) or "").strip()
    fx_source_raw = (request.form.get(fx_source_field) or "").strip()
    if fx_rate_raw:
        try:
            fx_rate = Decimal(fx_rate_raw)
        except InvalidOperation:
            return None, f"Invalid {fx_rate_field}"
        fx_source = fx_source_raw or "manual"
    else:
        fx_rate, fx_source = _resolve_fx_rate(currency, as_of_date)

    money = Money(
        amount=amount,
        currency=currency,
        fx_rate=fx_rate,
        fx_source=fx_source,
        fx_date=as_of_date,
    )
    return money, None


# ---------------------------------------------------------------------------
# GET /income/business/ — list view
# ---------------------------------------------------------------------------
@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="B12", action="business_list")
def list_view():
    user = _current_user_obj()
    if not user:
        abort(401)
    tax_year_filter = (request.args.get("tax_year") or "").strip() or None
    businesses = list_businesses_for_user(user, tax_year=tax_year_filter)
    return render_template(
        "business/list.html",
        layout_template=_layout(),
        businesses=businesses,
        tax_year_filter=tax_year_filter,
        business_types=BUSINESS_TYPES,
    )


# ---------------------------------------------------------------------------
# GET/POST /income/business/new — create new business
# ---------------------------------------------------------------------------
@bp.route("/new", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="B12", action="business_new_form")
def new_form():
    return render_template(
        "business/new.html",
        layout_template=_layout(),
        business_types=BUSINESS_TYPES,
        error=None,
        form={},
    )


@bp.route("/new", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="B12", action="business_new_submit")
def new_submit():
    user = _current_user_obj()
    if not user:
        abort(401)

    business_name = (request.form.get("business_name") or "").strip()
    business_type = (request.form.get("business_type") or "sole_prop").strip()
    source_country = (request.form.get("source_country") or "").strip().upper() or None
    tax_year = (request.form.get("tax_year") or "").strip() or None

    if not business_name:
        return render_template(
            "business/new.html",
            layout_template=_layout(),
            business_types=BUSINESS_TYPES,
            error="Business name is required",
            form=request.form,
        ), 400

    money, err = _parse_money_form(
        amount_field="gross_receipts",
        currency_field="currency",
        date_field="as_of_date",
    )
    if err or money is None:
        return render_template(
            "business/new.html",
            layout_template=_layout(),
            business_types=BUSINESS_TYPES,
            error=err or "Invalid gross receipts",
            form=request.form,
        ), 400

    try:
        entry = record_business_income(
            user=user,
            gross_receipts_money=money,
            business_name=business_name,
            business_type=business_type,
            source_country=source_country,
            tax_year=tax_year,
        )
    except ValueError as exc:
        return render_template(
            "business/new.html",
            layout_template=_layout(),
            business_types=BUSINESS_TYPES,
            error=str(exc),
            form=request.form,
        ), 400
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("record_business_income failed: %s", exc)
        return render_template(
            "business/new.html",
            layout_template=_layout(),
            business_types=BUSINESS_TYPES,
            error=f"Could not save: {exc}",
            form=request.form,
        ), 500

    try:
        flash(f"Business '{entry.business_name}' saved for {entry.tax_year}.", "success")
    except Exception:
        pass
    return redirect(url_for("fiesta_business.detail", business_entry_id=entry.id))


# ---------------------------------------------------------------------------
# GET /income/business/<id> — detail view
# ---------------------------------------------------------------------------
@bp.route("/<int:business_entry_id>", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="B12", action="business_detail")
def detail(business_entry_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    entry = get_business_for_user(user, business_entry_id)
    if entry is None:
        abort(404)

    summary = compute_business_tax(user, entry.tax_year)
    this_business = next(
        (b for b in summary["businesses"] if int(b["entry_id"]) == int(entry.id)),
        None,
    )

    return render_template(
        "business/detail.html",
        layout_template=_layout(),
        entry=entry,
        business=this_business,
        summary=summary,
        expense_categories=EXPENSE_CATEGORIES,
    )


# ---------------------------------------------------------------------------
# POST /income/business/<id>/edit — update gross or metadata
# ---------------------------------------------------------------------------
@bp.route("/<int:business_entry_id>/edit", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="B12", action="business_edit_submit")
def edit_submit(business_entry_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    entry = get_business_for_user(user, business_entry_id)
    if entry is None:
        abort(404)

    business_name = (request.form.get("business_name") or entry.business_name).strip()
    business_type = (request.form.get("business_type") or entry.business_type).strip()
    source_country = (
        request.form.get("source_country") or (entry.source_country or "")
    ).strip().upper() or None
    tax_year = (request.form.get("tax_year") or entry.tax_year).strip()

    money, err = _parse_money_form(
        amount_field="gross_receipts",
        currency_field="currency",
        date_field="as_of_date",
    )
    if err or money is None:
        try:
            flash(err or "Invalid gross receipts", "error")
        except Exception:
            pass
        return redirect(url_for("fiesta_business.detail", business_entry_id=entry.id))

    try:
        record_business_income(
            user=user,
            gross_receipts_money=money,
            business_name=business_name,
            business_type=business_type,
            source_country=source_country,
            tax_year=tax_year,
        )
    except ValueError as exc:
        try:
            flash(str(exc), "error")
        except Exception:
            pass
    return redirect(url_for("fiesta_business.detail", business_entry_id=entry.id))


# ---------------------------------------------------------------------------
# GET/POST /income/business/<id>/expense/new
# ---------------------------------------------------------------------------
@bp.route("/<int:business_entry_id>/expense/new", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="B12", action="business_expense_new_form")
def expense_new_form(business_entry_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    entry = get_business_for_user(user, business_entry_id)
    if entry is None:
        abort(404)
    return render_template(
        "business/expense_new.html",
        layout_template=_layout(),
        entry=entry,
        expense_categories=EXPENSE_CATEGORIES,
        error=None,
        form={},
    )


@bp.route("/<int:business_entry_id>/expense/new", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="B12", action="business_expense_new_submit")
def expense_new_submit(business_entry_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    entry = get_business_for_user(user, business_entry_id)
    if entry is None:
        abort(404)

    category = (request.form.get("category") or "other").strip()
    description = (request.form.get("description") or "").strip()
    date_incurred_raw = (request.form.get("date_incurred") or "").strip()
    try:
        date_incurred = date.fromisoformat(date_incurred_raw) if date_incurred_raw else None
    except ValueError:
        return render_template(
            "business/expense_new.html",
            layout_template=_layout(),
            entry=entry,
            expense_categories=EXPENSE_CATEGORIES,
            error="Invalid date_incurred (expected YYYY-MM-DD)",
            form=request.form,
        ), 400

    money, err = _parse_money_form(
        amount_field="amount",
        currency_field="currency",
        date_field="as_of_date",
    )
    if err or money is None:
        return render_template(
            "business/expense_new.html",
            layout_template=_layout(),
            entry=entry,
            expense_categories=EXPENSE_CATEGORIES,
            error=err or "Invalid amount",
            form=request.form,
        ), 400

    try:
        add_business_expense(
            business_entry_id=entry.id,
            expense_money=money,
            category=category,
            description=description,
            date_incurred=date_incurred,
        )
    except ValueError as exc:
        return render_template(
            "business/expense_new.html",
            layout_template=_layout(),
            entry=entry,
            expense_categories=EXPENSE_CATEGORIES,
            error=str(exc),
            form=request.form,
        ), 400

    return redirect(url_for("fiesta_business.detail", business_entry_id=entry.id))


# ---------------------------------------------------------------------------
# POST /income/business/<id>/expense/<exp_id>/edit
# ---------------------------------------------------------------------------
@bp.route(
    "/<int:business_entry_id>/expense/<int:expense_id>/edit",
    methods=["POST"],
)
@login_required
@paywall_required(min_tier="self_file", screen_id="B12", action="business_expense_edit")
def expense_edit(business_entry_id: int, expense_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    entry = get_business_for_user(user, business_entry_id)
    if entry is None:
        abort(404)

    category = (request.form.get("category") or "").strip() or None
    description = request.form.get("description")
    date_incurred_raw = (request.form.get("date_incurred") or "").strip()
    date_incurred: Optional[date] = None
    if date_incurred_raw:
        try:
            date_incurred = date.fromisoformat(date_incurred_raw)
        except ValueError:
            try:
                flash("Invalid date_incurred", "error")
            except Exception:
                pass
            return redirect(url_for("fiesta_business.detail", business_entry_id=entry.id))

    money: Optional[Money] = None
    if (request.form.get("amount") or "").strip():
        money, err = _parse_money_form(
            amount_field="amount",
            currency_field="currency",
            date_field="as_of_date",
        )
        if err:
            try:
                flash(err, "error")
            except Exception:
                pass
            return redirect(url_for("fiesta_business.detail", business_entry_id=entry.id))

    try:
        edit_business_expense(
            expense_id=expense_id,
            expense_money=money,
            category=category,
            description=description,
            date_incurred=date_incurred,
        )
    except ValueError as exc:
        try:
            flash(str(exc), "error")
        except Exception:
            pass
    return redirect(url_for("fiesta_business.detail", business_entry_id=entry.id))


# ---------------------------------------------------------------------------
# POST /income/business/<id>/expense/<exp_id>/delete
# ---------------------------------------------------------------------------
@bp.route(
    "/<int:business_entry_id>/expense/<int:expense_id>/delete",
    methods=["POST"],
)
@login_required
@paywall_required(min_tier="self_file", screen_id="B12", action="business_expense_delete")
def expense_delete(business_entry_id: int, expense_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    entry = get_business_for_user(user, business_entry_id)
    if entry is None:
        abort(404)
    delete_business_expense(expense_id)
    return redirect(url_for("fiesta_business.detail", business_entry_id=entry.id))


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------
def register_blueprint(app) -> None:
    """Register the B12 business-income blueprint with the Flask app."""
    app.register_blueprint(bp)
    logger.info(
        "FIESTA MS3 E.1 B12 business-income blueprint registered at /income/business"
    )


__all__ = ["bp", "register_blueprint"]
