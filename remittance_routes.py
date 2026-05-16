"""
Foreign-income remittance routes — for SL foreign-income earners.

Council Wave A 2026-05-16 (FIESTA_USEFULNESS_REVIEW.md). Parallel to
Invoice/Client; does NOT touch the legacy invoicing flow.
"""
import logging
import uuid
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, session
from flask_login import login_required, current_user
from sqlalchemy import desc

from app import db
from remittance_models import RemittanceEntry, current_sl_tax_year, PERSONA_SL_FOREIGN_INCOME
from remittance_import import parse_upload

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


# --------------------------------------------------------------------------- #
# Import flow — "the agent fills the form"
# Built 2026-05-17 (Opus birthday build, IF_I_RAN_FIESTA.md leverage move).
# --------------------------------------------------------------------------- #

# Cap: 8 MB upload. Bank statements are tiny in text-PDF form; scanned PDFs hit
# this limit and surface a friendly message instead of a 413.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


@remittance_bp.route("/import", methods=["GET", "POST"])
@login_required
def import_upload():
    """Step 1 — upload a bank statement (PDF or CSV)."""
    if request.method == "POST":
        f = request.files.get("statement")
        if not f or not f.filename:
            flash("Pick a PDF or CSV bank statement to upload.", "danger")
            return redirect(url_for("remittance.import_upload"))

        file_bytes = f.read()
        if len(file_bytes) == 0:
            flash("Upload was empty.", "danger")
            return redirect(url_for("remittance.import_upload"))
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            flash(f"File is too large (>{MAX_UPLOAD_BYTES // (1024*1024)} MB). For scanned PDFs, split into smaller pages or convert to CSV.", "warning")
            return redirect(url_for("remittance.import_upload"))

        kind, candidates = parse_upload(f.filename, file_bytes)

        if not candidates:
            flash(
                "Couldn't extract any credits from this file. If it's a scanned PDF "
                "(image-only), please export a text PDF or CSV from your bank's portal "
                "and try again. Manual entry still works at /remittance/new.",
                "warning",
            )
            return redirect(url_for("remittance.import_upload"))

        # Stash candidates in session under a fresh import_id so the review page
        # can show them without a re-parse. Session is signed cookie — fine for
        # small payloads, defensive against tampering.
        import_id = str(uuid.uuid4())[:12]
        session[f"import_{import_id}"] = {
            "filename": f.filename,
            "kind": kind,
            "candidates": [
                {**c, "lkr_amount": str(c["lkr_amount"]),
                 "foreign_amount": str(c["foreign_amount"]) if c["foreign_amount"] is not None else None,
                 "implied_rate": str(c["implied_rate"]) if c["implied_rate"] is not None else None}
                for c in candidates
            ],
        }
        log.info("Import %s: user=%s file=%s kind=%s candidates=%d",
                 import_id, current_user.id, f.filename, kind, len(candidates))
        return redirect(url_for("remittance.import_review", import_id=import_id))

    return render_template("remittance/import.html")


@remittance_bp.route("/import/<import_id>/review", methods=["GET"])
@login_required
def import_review(import_id):
    payload = session.get(f"import_{import_id}")
    if not payload:
        flash("Import session expired. Please upload again.", "warning")
        return redirect(url_for("remittance.import_upload"))

    suggested_count = sum(1 for c in payload["candidates"] if c.get("is_foreign_remittance"))
    return render_template(
        "remittance/import_review.html",
        import_id=import_id,
        filename=payload["filename"],
        kind=payload["kind"],
        candidates=payload["candidates"],
        suggested_count=suggested_count,
    )


@remittance_bp.route("/import/<import_id>/confirm", methods=["POST"])
@login_required
def import_confirm(import_id):
    payload = session.get(f"import_{import_id}")
    if not payload:
        flash("Import session expired. Please upload again.", "warning")
        return redirect(url_for("remittance.import_upload"))

    org = current_user.get_default_organization() if hasattr(current_user, "get_default_organization") else None
    created = 0

    # The form sends one set of fields per row. Only rows whose include[<i>] checkbox
    # is present are written. User can override Gemini's date/amount/currency/payer.
    for c in payload["candidates"]:
        idx = c["row_index"]
        if not request.form.get(f"include[{idx}]"):
            continue

        # Resolve fields — form value wins over Gemini's suggestion
        remittance_date_str = request.form.get(f"date[{idx}]") or c.get("txn_date") or ""
        try:
            remittance_date = datetime.strptime(remittance_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            remittance_date = date.today()

        foreign_currency = (request.form.get(f"ccy[{idx}]") or c.get("foreign_currency") or "").strip().upper()[:3]
        if not foreign_currency:
            log.info("Import %s row %s skipped: no currency", import_id, idx)
            continue

        try:
            foreign_amount = Decimal(str(request.form.get(f"famt[{idx}]") or c.get("foreign_amount") or "0").replace(",", ""))
        except InvalidOperation:
            foreign_amount = Decimal("0")
        if foreign_amount <= 0:
            # If no foreign amount given, fall back to lkr_amount as a placeholder
            # (rare, but happens when description lacks the foreign-ccy breakdown).
            try:
                foreign_amount = Decimal(str(c.get("lkr_amount") or "0").replace(",", ""))
                foreign_currency = "LKR"
            except InvalidOperation:
                continue

        try:
            lkr_bank = Decimal(str(request.form.get(f"lkr[{idx}]") or c.get("lkr_amount") or "0").replace(",", ""))
        except InvalidOperation:
            lkr_bank = None

        payer = (request.form.get(f"payer[{idx}]") or c.get("likely_payer") or "").strip()[:255] or None
        country = (request.form.get(f"country[{idx}]") or c.get("source_country_iso2") or "").strip().upper()[:2] or None
        notes = (request.form.get(f"notes[{idx}]") or c.get("description") or "")[:1000] or None

        entry = RemittanceEntry(
            user_id=current_user.id,
            organization_id=org.id if org else None,
            remittance_date=remittance_date,
            foreign_currency=foreign_currency,
            foreign_amount=foreign_amount,
            lkr_amount_bank_rate=lkr_bank,
            source_country=country,
            payer_name=payer,
            tax_year=current_sl_tax_year(remittance_date),
            notes=notes,
        )
        db.session.add(entry)
        created += 1

    db.session.commit()
    session.pop(f"import_{import_id}", None)

    if created == 0:
        flash("No rows were selected — nothing imported.", "warning")
        return redirect(url_for("remittance.import_upload"))

    flash(f"Imported {created} remittance{'s' if created != 1 else ''} from your bank statement.", "success")
    log.info("Import %s confirmed: user=%s created=%d", import_id, current_user.id, created)
    return redirect(url_for("remittance.dashboard"))


def register_routes(app):
    app.register_blueprint(remittance_bp)
    log.info("Remittance routes registered at /remittance/* (incl. /import)")
