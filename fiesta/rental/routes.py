"""fiesta.rental.routes — G3.4 LKR Rental Income Flask surface (MS4 W3c).

Routes (all login-gated, paywall-tier 'self_file'):

    GET  /income/rental/                                — list this user's rentals
    GET  /income/rental/new                             — add a new rental form
    POST /income/rental/new                             — create + paired Income row
    GET  /income/rental/<id>                            — detail view (gross + deductions)
    POST /income/rental/<id>/edit                       — update metadata or gross rent
    GET  /income/rental/<id>/deduction/new              — add deduction form
    POST /income/rental/<id>/deduction/new              — create deduction
    POST /income/rental/<id>/deduction/<ded_id>/edit    — edit deduction
    POST /income/rental/<id>/deduction/<ded_id>/delete  — delete deduction

LOCAL module — all amounts LKR. Foreign-rental support is Wave-X scope.

Persistence: routes thin-wrap fiesta.tax.rental_lkr.

Provenance: Inventory §G3.4 (Section G — Unification Addendum 2026-05-25).
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

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


from fiesta.tax.rental_lkr import (
    DEDUCTION_CATEGORIES,
    compute_rental_lkr_tax_year,
    delete_rental_deduction,
    edit_rental_deduction,
    get_rental_for_user,
    list_rentals_for_user,
    record_rental_deduction,
    record_rental_income,
)
from fiesta.tax.money import Money


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
bp = Blueprint(
    "fiesta_rental",
    __name__,
    url_prefix="/income/rental",
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
    try:
        return getattr(g, "layout_template", "layout_fiesta.html")
    except Exception:
        return "layout_fiesta.html"


def _parse_lkr_money(
    amount_field: str = "amount",
    date_field: str = "as_of_date",
) -> tuple[Optional[Money], Optional[str]]:
    """Parse an LKR Money block out of request.form.

    LKR-native — fx_rate=1.0, fx_source='lkr_native'.

    Returns ``(money, error)``. If error is non-None, money is None.
    """
    if request is None:
        return None, "no_request_context"
    try:
        amount = Decimal((request.form.get(amount_field) or "0").strip() or "0")
    except InvalidOperation:
        return None, f"Invalid {amount_field}"
    try:
        fx_date_str = (request.form.get(date_field) or "").strip()
        as_of_date = (
            date.fromisoformat(fx_date_str) if fx_date_str else date.today()
        )
    except ValueError:
        return None, f"Invalid {date_field} (expected YYYY-MM-DD)"

    money = Money.lkr(amount=amount, fx_date=as_of_date)
    return money, None


# ---------------------------------------------------------------------------
# GET /income/rental/ — list view
# ---------------------------------------------------------------------------
@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="G3.4", action="rental_list")
def list_view():
    user = _current_user_obj()
    if not user:
        abort(401)
    tax_year_filter = (request.args.get("tax_year") or "").strip() or None
    rentals = list_rentals_for_user(user, tax_year=tax_year_filter)
    return render_template(
        "rental/list.html",
        layout_template=_layout(),
        rentals=rentals,
        tax_year_filter=tax_year_filter,
    )


# ---------------------------------------------------------------------------
# GET/POST /income/rental/new — create new rental
# ---------------------------------------------------------------------------
@bp.route("/new", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="G3.4", action="rental_new_form")
def new_form():
    return render_template(
        "rental/new.html",
        layout_template=_layout(),
        error=None,
        form={},
    )


@bp.route("/new", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="G3.4", action="rental_new_submit")
def new_submit():
    user = _current_user_obj()
    if not user:
        abort(401)

    property_address = (request.form.get("property_address") or "").strip()
    tenant_name = (request.form.get("tenant_name") or "").strip() or None
    period_start_raw = (request.form.get("period_start") or "").strip()
    period_end_raw = (request.form.get("period_end") or "").strip()
    tax_year = (request.form.get("tax_year") or "").strip() or None

    if not property_address:
        return render_template(
            "rental/new.html",
            layout_template=_layout(),
            error="Property address is required",
            form=request.form,
        ), 400

    period_start: Optional[date] = None
    period_end: Optional[date] = None
    try:
        if period_start_raw:
            period_start = date.fromisoformat(period_start_raw)
        if period_end_raw:
            period_end = date.fromisoformat(period_end_raw)
    except ValueError:
        return render_template(
            "rental/new.html",
            layout_template=_layout(),
            error="Invalid date (expected YYYY-MM-DD)",
            form=request.form,
        ), 400

    money, err = _parse_lkr_money(
        amount_field="gross_rent",
        date_field="as_of_date",
    )
    if err or money is None:
        return render_template(
            "rental/new.html",
            layout_template=_layout(),
            error=err or "Invalid gross rent",
            form=request.form,
        ), 400

    try:
        entry = record_rental_income(
            user=user,
            property_address=property_address,
            gross_rent_money=money,
            tenant_name=tenant_name,
            period_start=period_start,
            period_end=period_end,
            tax_year=tax_year,
        )
    except ValueError as exc:
        return render_template(
            "rental/new.html",
            layout_template=_layout(),
            error=str(exc),
            form=request.form,
        ), 400
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("record_rental_income failed: %s", exc)
        return render_template(
            "rental/new.html",
            layout_template=_layout(),
            error=f"Could not save: {exc}",
            form=request.form,
        ), 500

    try:
        flash(
            f"Rental at '{entry.property_address}' saved for {entry.tax_year}.",
            "success",
        )
    except Exception:
        pass
    return redirect(url_for("fiesta_rental.detail", rental_entry_id=entry.id))


# ---------------------------------------------------------------------------
# GET /income/rental/<id> — detail view
# ---------------------------------------------------------------------------
@bp.route("/<int:rental_entry_id>", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="G3.4", action="rental_detail")
def detail(rental_entry_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    entry = get_rental_for_user(user, rental_entry_id)
    if entry is None:
        abort(404)

    summary = compute_rental_lkr_tax_year(user, entry.tax_year)
    this_rental = next(
        (r for r in summary["rentals"] if int(r["entry_id"]) == int(entry.id)),
        None,
    )

    return render_template(
        "rental/detail.html",
        layout_template=_layout(),
        entry=entry,
        rental=this_rental,
        summary=summary,
        deduction_categories=DEDUCTION_CATEGORIES,
    )


# ---------------------------------------------------------------------------
# POST /income/rental/<id>/edit
# ---------------------------------------------------------------------------
@bp.route("/<int:rental_entry_id>/edit", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="G3.4", action="rental_edit_submit")
def edit_submit(rental_entry_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    entry = get_rental_for_user(user, rental_entry_id)
    if entry is None:
        abort(404)

    property_address = (request.form.get("property_address")
                        or entry.property_address).strip()
    tenant_name = (request.form.get("tenant_name") or "").strip() or None
    period_start_raw = (request.form.get("period_start") or "").strip()
    period_end_raw = (request.form.get("period_end") or "").strip()
    tax_year = (request.form.get("tax_year") or entry.tax_year).strip()

    period_start = entry.period_start
    period_end = entry.period_end
    try:
        if period_start_raw:
            period_start = date.fromisoformat(period_start_raw)
        if period_end_raw:
            period_end = date.fromisoformat(period_end_raw)
    except ValueError:
        try:
            flash("Invalid date (expected YYYY-MM-DD)", "error")
        except Exception:
            pass
        return redirect(
            url_for("fiesta_rental.detail", rental_entry_id=entry.id),
        )

    money, err = _parse_lkr_money(
        amount_field="gross_rent",
        date_field="as_of_date",
    )
    if err or money is None:
        try:
            flash(err or "Invalid gross rent", "error")
        except Exception:
            pass
        return redirect(
            url_for("fiesta_rental.detail", rental_entry_id=entry.id),
        )

    try:
        record_rental_income(
            user=user,
            property_address=property_address,
            gross_rent_money=money,
            tenant_name=tenant_name,
            period_start=period_start,
            period_end=period_end,
            tax_year=tax_year,
        )
    except ValueError as exc:
        try:
            flash(str(exc), "error")
        except Exception:
            pass
    return redirect(url_for("fiesta_rental.detail", rental_entry_id=entry.id))


# ---------------------------------------------------------------------------
# GET/POST /income/rental/<id>/deduction/new
# ---------------------------------------------------------------------------
@bp.route("/<int:rental_entry_id>/deduction/new", methods=["GET"])
@login_required
@paywall_required(
    min_tier="self_file", screen_id="G3.4",
    action="rental_deduction_new_form",
)
def deduction_new_form(rental_entry_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    entry = get_rental_for_user(user, rental_entry_id)
    if entry is None:
        abort(404)
    return render_template(
        "rental/deduction_new.html",
        layout_template=_layout(),
        entry=entry,
        deduction_categories=DEDUCTION_CATEGORIES,
        error=None,
        form={},
    )


@bp.route("/<int:rental_entry_id>/deduction/new", methods=["POST"])
@login_required
@paywall_required(
    min_tier="self_file", screen_id="G3.4",
    action="rental_deduction_new_submit",
)
def deduction_new_submit(rental_entry_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    entry = get_rental_for_user(user, rental_entry_id)
    if entry is None:
        abort(404)

    category = (request.form.get("category") or "other").strip()
    description = (request.form.get("description") or "").strip()
    date_incurred_raw = (request.form.get("date_incurred") or "").strip()
    try:
        date_incurred = (
            date.fromisoformat(date_incurred_raw) if date_incurred_raw else None
        )
    except ValueError:
        return render_template(
            "rental/deduction_new.html",
            layout_template=_layout(),
            entry=entry,
            deduction_categories=DEDUCTION_CATEGORIES,
            error="Invalid date_incurred (expected YYYY-MM-DD)",
            form=request.form,
        ), 400

    money, err = _parse_lkr_money(
        amount_field="amount",
        date_field="as_of_date",
    )
    if err or money is None:
        return render_template(
            "rental/deduction_new.html",
            layout_template=_layout(),
            entry=entry,
            deduction_categories=DEDUCTION_CATEGORIES,
            error=err or "Invalid amount",
            form=request.form,
        ), 400

    try:
        record_rental_deduction(
            user=user,
            rental_income_id=entry.id,
            category=category,
            amount_money=money,
            description=description,
            date_incurred=date_incurred,
        )
    except ValueError as exc:
        return render_template(
            "rental/deduction_new.html",
            layout_template=_layout(),
            entry=entry,
            deduction_categories=DEDUCTION_CATEGORIES,
            error=str(exc),
            form=request.form,
        ), 400

    return redirect(url_for("fiesta_rental.detail", rental_entry_id=entry.id))


# ---------------------------------------------------------------------------
# POST /income/rental/<id>/deduction/<ded_id>/edit
# ---------------------------------------------------------------------------
@bp.route(
    "/<int:rental_entry_id>/deduction/<int:deduction_id>/edit",
    methods=["POST"],
)
@login_required
@paywall_required(
    min_tier="self_file", screen_id="G3.4",
    action="rental_deduction_edit",
)
def deduction_edit(rental_entry_id: int, deduction_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    entry = get_rental_for_user(user, rental_entry_id)
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
            return redirect(
                url_for("fiesta_rental.detail", rental_entry_id=entry.id),
            )

    money: Optional[Money] = None
    if (request.form.get("amount") or "").strip():
        money, err = _parse_lkr_money(
            amount_field="amount",
            date_field="as_of_date",
        )
        if err:
            try:
                flash(err, "error")
            except Exception:
                pass
            return redirect(
                url_for("fiesta_rental.detail", rental_entry_id=entry.id),
            )

    try:
        edit_rental_deduction(
            deduction_id=deduction_id,
            amount_money=money,
            category=category,
            description=description,
            date_incurred=date_incurred,
        )
    except ValueError as exc:
        try:
            flash(str(exc), "error")
        except Exception:
            pass
    return redirect(url_for("fiesta_rental.detail", rental_entry_id=entry.id))


# ---------------------------------------------------------------------------
# POST /income/rental/<id>/deduction/<ded_id>/delete
# ---------------------------------------------------------------------------
@bp.route(
    "/<int:rental_entry_id>/deduction/<int:deduction_id>/delete",
    methods=["POST"],
)
@login_required
@paywall_required(
    min_tier="self_file", screen_id="G3.4",
    action="rental_deduction_delete",
)
def deduction_delete(rental_entry_id: int, deduction_id: int):
    user = _current_user_obj()
    if not user:
        abort(401)
    entry = get_rental_for_user(user, rental_entry_id)
    if entry is None:
        abort(404)
    delete_rental_deduction(deduction_id)
    return redirect(url_for("fiesta_rental.detail", rental_entry_id=entry.id))


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------
def register_blueprint(app) -> None:
    """Register the G3.4 LKR rental-income blueprint with the Flask app."""
    app.register_blueprint(bp)
    logger.info(
        "FIESTA MS4 W3c G3.4 rental-income blueprint registered at "
        "/income/rental"
    )


__all__ = ["bp", "register_blueprint"]
