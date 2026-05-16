"""
Foreign-income remittance routes — for SL foreign-income earners.

Council Wave A 2026-05-16 (FIESTA_USEFULNESS_REVIEW.md). Parallel to
Invoice/Client; does NOT touch the legacy invoicing flow.
"""
import logging
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from sqlalchemy import desc

from app import db
from remittance_models import RemittanceEntry, current_sl_tax_year, PERSONA_SL_FOREIGN_INCOME

log = logging.getLogger(__name__)

remittance_bp = Blueprint("remittance", __name__, url_prefix="/remittance")


# Common currencies SL foreign-income earners receive. Order = display order.
COMMON_CURRENCIES = ["USD", "GBP", "EUR", "AUD", "CAD", "AED", "SGD", "JPY", "CHF", "NZD"]


def _decimal_or_none(raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return None


def _date_or_today(raw):
    if not raw:
        return date.today()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return date.today()


@remittance_bp.route("/dashboard")
@login_required
def dashboard():
    """SL foreign-income earner's home — list remittances by tax year, show running totals."""
    tax_year = request.args.get("tax_year") or current_sl_tax_year()

    entries = (
        RemittanceEntry.query
        .filter_by(user_id=current_user.id, tax_year=tax_year)
        .order_by(desc(RemittanceEntry.remittance_date), desc(RemittanceEntry.id))
        .all()
    )

    total_foreign_by_ccy = {}
    total_lkr_cbsl = Decimal("0")
    total_lkr_bank = Decimal("0")
    ird_ready_count = 0
    for e in entries:
        total_foreign_by_ccy.setdefault(e.foreign_currency, Decimal("0"))
        total_foreign_by_ccy[e.foreign_currency] += (e.foreign_amount or Decimal("0"))
        if e.lkr_amount_cbsl is not None:
            total_lkr_cbsl += e.lkr_amount_cbsl
        if e.lkr_amount_bank_rate is not None:
            total_lkr_bank += e.lkr_amount_bank_rate
        if e.completeness_status()[0] == "ird_ready":
            ird_ready_count += 1

    # Tax-year selector list (current + 4 prior)
    cy_start = int(current_sl_tax_year().split("-")[0])
    tax_years = [f"{y}-{str(y + 1)[2:]}" for y in range(cy_start, cy_start - 5, -1)]

    return render_template(
        "remittance/dashboard.html",
        entries=entries,
        tax_year=tax_year,
        tax_years=tax_years,
        total_foreign_by_ccy=total_foreign_by_ccy,
        total_lkr_cbsl=total_lkr_cbsl,
        total_lkr_bank=total_lkr_bank,
        ird_ready_count=ird_ready_count,
        common_currencies=COMMON_CURRENCIES,
    )


@remittance_bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    """Create a new remittance entry."""
    if request.method == "POST":
        remittance_date = _date_or_today(request.form.get("remittance_date"))
        foreign_currency = (request.form.get("foreign_currency") or "").strip().upper()[:3]
        foreign_amount = _decimal_or_none(request.form.get("foreign_amount"))
        lkr_amount_bank_rate = _decimal_or_none(request.form.get("lkr_amount_bank_rate"))
        source_country = (request.form.get("source_country") or "").strip().upper()[:2] or None
        payer_name = (request.form.get("payer_name") or "").strip()[:255] or None
        notes = (request.form.get("notes") or "").strip() or None

        foreign_tax_amount = _decimal_or_none(request.form.get("foreign_tax_withheld_amount"))
        foreign_tax_ccy = (request.form.get("foreign_tax_withheld_currency") or "").strip().upper()[:3] or None

        # Manual CBSL rate path (Sonnet's manual-fallback flag) — Wave A allows manual entry only.
        # Wave B will add CBSL API auto-lookup.
        cbsl_rate = _decimal_or_none(request.form.get("cbsl_rate"))
        cbsl_rate_source = (request.form.get("cbsl_rate_source") or "").strip()[:255] or None

        if not foreign_currency or foreign_amount is None or foreign_amount <= 0:
            flash("Foreign currency and amount are required.", "danger")
            return redirect(url_for("remittance.new"))

        lkr_amount_cbsl = None
        if cbsl_rate and cbsl_rate > 0:
            lkr_amount_cbsl = (foreign_amount * cbsl_rate).quantize(Decimal("0.01"))

        org = current_user.get_default_organization() if hasattr(current_user, "get_default_organization") else None

        entry = RemittanceEntry(
            user_id=current_user.id,
            organization_id=org.id if org else None,
            remittance_date=remittance_date,
            foreign_currency=foreign_currency,
            foreign_amount=foreign_amount,
            lkr_amount_bank_rate=lkr_amount_bank_rate,
            cbsl_rate=cbsl_rate,
            cbsl_rate_source=cbsl_rate_source or ("manual entry" if cbsl_rate else None),
            cbsl_rate_captured_at=datetime.utcnow() if cbsl_rate else None,
            lkr_amount_cbsl=lkr_amount_cbsl,
            rate_entered_manually=cbsl_rate is not None,
            source_country=source_country,
            payer_name=payer_name,
            foreign_tax_withheld_amount=foreign_tax_amount,
            foreign_tax_withheld_currency=foreign_tax_ccy,
            tax_year=current_sl_tax_year(remittance_date),
            notes=notes,
        )
        db.session.add(entry)
        db.session.commit()
        log.info("Remittance entry %s created by user %s", entry.id, current_user.id)
        flash("Remittance entry saved.", "success")
        return redirect(url_for("remittance.detail", entry_id=entry.id))

    return render_template(
        "remittance/new.html",
        common_currencies=COMMON_CURRENCIES,
        today=date.today().isoformat(),
    )


@remittance_bp.route("/<int:entry_id>")
@login_required
def detail(entry_id):
    """Show a single remittance entry."""
    entry = RemittanceEntry.query.get_or_404(entry_id)
    if entry.user_id != current_user.id and current_user.role != "admin":
        abort(403)
    return render_template("remittance/detail.html", entry=entry)


def register_routes(app):
    app.register_blueprint(remittance_bp)
    log.info("Remittance routes registered at /remittance/*")
