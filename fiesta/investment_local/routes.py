"""fiesta.investment_local.routes — G3.5 LOCAL Investment Income Flask
surface (MS4 W3c).

Routes (all login-gated, paywall-tier 'self_file'):

    GET  /income/investments/                  — combined list (FD + dividend + CGT)
    GET  /income/investments/fd/new            — add FD interest form
    POST /income/investments/fd/new            — create FD interest entry
    GET  /income/investments/dividend/new      — add dividend form
    POST /income/investments/dividend/new      — create dividend entry
    GET  /income/investments/cgt/new           — add local CGT disposal form
    POST /income/investments/cgt/new           — create AssetDisposal row

LOCAL module — all amounts LKR. Foreign-source investment income
(foreign FD, foreign dividend, foreign equity CGT) is Wave-X scope and
returns the DTAA-deferred banner.

Persistence: routes thin-wrap fiesta.tax.investment_local.

Provenance: Inventory §G3.5 LOCAL (Section G — Unification Addendum 2026-05-25).
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


from fiesta.tax.investment_local import (
    LOCAL_CGT_ASSET_TYPES,
    compute_investment_local_tax_year,
    list_dividends_for_user,
    list_fd_interest_for_user,
    list_local_cgt_disposals_for_user,
    record_dividend,
    record_fd_interest,
    record_local_cgt_disposal,
)
from fiesta.tax.money import Money


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
bp = Blueprint(
    "fiesta_investment_local",
    __name__,
    url_prefix="/income/investments",
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
    amount_field: str,
    date_field: str = "as_of_date",
) -> tuple[Optional[Money], Optional[str]]:
    """Parse an LKR Money block out of request.form."""
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
    return Money.lkr(amount=amount, fx_date=as_of_date), None


# ---------------------------------------------------------------------------
# GET /income/investments/ — combined list
# ---------------------------------------------------------------------------
@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
@login_required
@paywall_required(
    min_tier="self_file", screen_id="G3.5",
    action="investment_local_list",
)
def list_view():
    user = _current_user_obj()
    if not user:
        abort(401)
    tax_year_filter = (request.args.get("tax_year") or "").strip() or None

    fd_rows = list_fd_interest_for_user(user, tax_year=tax_year_filter)
    div_rows = list_dividends_for_user(user, tax_year=tax_year_filter)
    cgt_rows = list_local_cgt_disposals_for_user(user, tax_year=tax_year_filter)

    summary = None
    if tax_year_filter:
        summary = compute_investment_local_tax_year(user, tax_year_filter)

    return render_template(
        "investment_local/list.html",
        layout_template=_layout(),
        fd_rows=fd_rows,
        div_rows=div_rows,
        cgt_rows=cgt_rows,
        summary=summary,
        tax_year_filter=tax_year_filter,
        local_cgt_asset_types=LOCAL_CGT_ASSET_TYPES,
    )


# ---------------------------------------------------------------------------
# GET/POST /income/investments/fd/new
# ---------------------------------------------------------------------------
@bp.route("/fd/new", methods=["GET"])
@login_required
@paywall_required(
    min_tier="self_file", screen_id="G3.5",
    action="investment_local_fd_new_form",
)
def fd_new_form():
    return render_template(
        "investment_local/fd_new.html",
        layout_template=_layout(),
        error=None,
        form={},
    )


@bp.route("/fd/new", methods=["POST"])
@login_required
@paywall_required(
    min_tier="self_file", screen_id="G3.5",
    action="investment_local_fd_new_submit",
)
def fd_new_submit():
    user = _current_user_obj()
    if not user:
        abort(401)

    bank_name = (request.form.get("bank_name") or "").strip()
    fd_account_ref = (request.form.get("fd_account_ref") or "").strip() or None
    tax_year = (request.form.get("tax_year") or "").strip() or None

    if not bank_name:
        return render_template(
            "investment_local/fd_new.html",
            layout_template=_layout(),
            error="Bank name is required",
            form=request.form,
        ), 400

    principal_money, err = _parse_lkr_money(
        amount_field="principal", date_field="interest_date",
    )
    if err or principal_money is None:
        return render_template(
            "investment_local/fd_new.html",
            layout_template=_layout(),
            error=err or "Invalid principal",
            form=request.form,
        ), 400

    interest_money, err = _parse_lkr_money(
        amount_field="interest", date_field="interest_date",
    )
    if err or interest_money is None:
        return render_template(
            "investment_local/fd_new.html",
            layout_template=_layout(),
            error=err or "Invalid interest",
            form=request.form,
        ), 400

    wht_money = None
    if (request.form.get("wht") or "").strip():
        wht_money, err = _parse_lkr_money(
            amount_field="wht", date_field="interest_date",
        )
        if err:
            return render_template(
                "investment_local/fd_new.html",
                layout_template=_layout(),
                error=err,
                form=request.form,
            ), 400

    try:
        entry = record_fd_interest(
            user=user,
            bank_name=bank_name,
            principal_money=principal_money,
            interest_money=interest_money,
            wht_money=wht_money,
            interest_date=interest_money.fx_date,
            fd_account_ref=fd_account_ref,
            tax_year=tax_year,
        )
    except ValueError as exc:
        return render_template(
            "investment_local/fd_new.html",
            layout_template=_layout(),
            error=str(exc),
            form=request.form,
        ), 400
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("record_fd_interest failed: %s", exc)
        return render_template(
            "investment_local/fd_new.html",
            layout_template=_layout(),
            error=f"Could not save: {exc}",
            form=request.form,
        ), 500

    try:
        flash(
            f"FD interest at '{entry.bank_name}' saved for {entry.tax_year}.",
            "success",
        )
    except Exception:
        pass
    return redirect(
        url_for("fiesta_investment_local.list_view", tax_year=entry.tax_year),
    )


# ---------------------------------------------------------------------------
# GET/POST /income/investments/dividend/new
# ---------------------------------------------------------------------------
@bp.route("/dividend/new", methods=["GET"])
@login_required
@paywall_required(
    min_tier="self_file", screen_id="G3.5",
    action="investment_local_dividend_new_form",
)
def dividend_new_form():
    return render_template(
        "investment_local/dividend_new.html",
        layout_template=_layout(),
        error=None,
        form={},
    )


@bp.route("/dividend/new", methods=["POST"])
@login_required
@paywall_required(
    min_tier="self_file", screen_id="G3.5",
    action="investment_local_dividend_new_submit",
)
def dividend_new_submit():
    user = _current_user_obj()
    if not user:
        abort(401)

    company_name = (request.form.get("company_name") or "").strip()
    tax_year = (request.form.get("tax_year") or "").strip() or None

    if not company_name:
        return render_template(
            "investment_local/dividend_new.html",
            layout_template=_layout(),
            error="Company name is required",
            form=request.form,
        ), 400

    dividend_money, err = _parse_lkr_money(
        amount_field="dividend", date_field="ex_dividend_date",
    )
    if err or dividend_money is None:
        return render_template(
            "investment_local/dividend_new.html",
            layout_template=_layout(),
            error=err or "Invalid dividend",
            form=request.form,
        ), 400

    wht_money = None
    if (request.form.get("wht") or "").strip():
        wht_money, err = _parse_lkr_money(
            amount_field="wht", date_field="ex_dividend_date",
        )
        if err:
            return render_template(
                "investment_local/dividend_new.html",
                layout_template=_layout(),
                error=err,
                form=request.form,
            ), 400

    try:
        entry = record_dividend(
            user=user,
            company_name=company_name,
            dividend_money=dividend_money,
            wht_money=wht_money,
            ex_dividend_date=dividend_money.fx_date,
            tax_year=tax_year,
        )
    except ValueError as exc:
        return render_template(
            "investment_local/dividend_new.html",
            layout_template=_layout(),
            error=str(exc),
            form=request.form,
        ), 400

    try:
        flash(
            f"Dividend from '{entry.company_name}' saved for {entry.tax_year}.",
            "success",
        )
    except Exception:
        pass
    return redirect(
        url_for("fiesta_investment_local.list_view", tax_year=entry.tax_year),
    )


# ---------------------------------------------------------------------------
# GET/POST /income/investments/cgt/new
# ---------------------------------------------------------------------------
@bp.route("/cgt/new", methods=["GET"])
@login_required
@paywall_required(
    min_tier="self_file", screen_id="G3.5",
    action="investment_local_cgt_new_form",
)
def cgt_new_form():
    return render_template(
        "investment_local/cgt_new.html",
        layout_template=_layout(),
        local_cgt_asset_types=LOCAL_CGT_ASSET_TYPES,
        error=None,
        form={},
    )


@bp.route("/cgt/new", methods=["POST"])
@login_required
@paywall_required(
    min_tier="self_file", screen_id="G3.5",
    action="investment_local_cgt_new_submit",
)
def cgt_new_submit():
    user = _current_user_obj()
    if not user:
        abort(401)

    asset_type = (request.form.get("asset_type") or "").strip()
    asset_identifier = (request.form.get("asset_identifier") or "").strip() or None

    try:
        acquisition_date = date.fromisoformat(
            (request.form.get("acquisition_date") or "").strip()
        )
        disposal_date = date.fromisoformat(
            (request.form.get("disposal_date") or "").strip()
        )
    except ValueError:
        return render_template(
            "investment_local/cgt_new.html",
            layout_template=_layout(),
            local_cgt_asset_types=LOCAL_CGT_ASSET_TYPES,
            error="Acquisition + disposal date are required (YYYY-MM-DD)",
            form=request.form,
        ), 400

    acquisition_money, err = _parse_lkr_money(
        amount_field="acquisition_cost",
        date_field="acquisition_date",
    )
    if err or acquisition_money is None:
        return render_template(
            "investment_local/cgt_new.html",
            layout_template=_layout(),
            local_cgt_asset_types=LOCAL_CGT_ASSET_TYPES,
            error=err or "Invalid acquisition cost",
            form=request.form,
        ), 400

    disposal_money, err = _parse_lkr_money(
        amount_field="disposal_proceeds",
        date_field="disposal_date",
    )
    if err or disposal_money is None:
        return render_template(
            "investment_local/cgt_new.html",
            layout_template=_layout(),
            local_cgt_asset_types=LOCAL_CGT_ASSET_TYPES,
            error=err or "Invalid disposal proceeds",
            form=request.form,
        ), 400

    try:
        disposal = record_local_cgt_disposal(
            user=user,
            asset_type=asset_type,
            acquisition_money=acquisition_money,
            disposal_money=disposal_money,
            acquisition_date=acquisition_date,
            disposal_date=disposal_date,
            asset_identifier=asset_identifier,
        )
    except ValueError as exc:
        return render_template(
            "investment_local/cgt_new.html",
            layout_template=_layout(),
            local_cgt_asset_types=LOCAL_CGT_ASSET_TYPES,
            error=str(exc),
            form=request.form,
        ), 400

    try:
        flash(
            f"Capital gain disposal saved (gain Rs {disposal.gain_lkr:,.2f}).",
            "success",
        )
    except Exception:
        pass
    return redirect(
        url_for(
            "fiesta_investment_local.list_view", tax_year=disposal.tax_year,
        ),
    )


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------
def register_blueprint(app) -> None:
    """Register the G3.5 LOCAL investment blueprint with the Flask app."""
    app.register_blueprint(bp)
    logger.info(
        "FIESTA MS4 W3c G3.5 investment-local blueprint registered at "
        "/income/investments"
    )


__all__ = ["bp", "register_blueprint"]
