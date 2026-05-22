"""
Foreign-income remittance routes — for SL foreign-income earners.

Council Wave A 2026-05-16 + Wave H hardening 2026-05-17 (FIESTA_HARDENING_PLAN.md).
Parallel to Invoice/Client; does NOT touch the legacy invoicing flow.
"""
import logging
import uuid
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from sqlalchemy import desc

from app import db
from models import AuditLog
from remittance_models import (
    RemittanceEntry, RemittanceImportBatch,
    current_sl_tax_year, PERSONA_SL_FOREIGN_INCOME,
)
from remittance_import import parse_upload, sha256_hex
from fx_rate_service import get_rate as fx_get_rate, store_manual_rate as fx_store_manual
from events import emit as emit_event  # Wave 1 EVENT SPINE 2026-05-17 (council #2)

log = logging.getLogger(__name__)

remittance_bp = Blueprint("remittance", __name__, url_prefix="/remittance")

COMMON_CURRENCIES = ["USD", "GBP", "EUR", "AUD", "CAD", "AED", "SGD", "JPY", "CHF", "NZD"]

# Wave H H6: per-user daily import quota — guard against Gemini cost blowup.
IMPORTS_PER_USER_PER_DAY = 10

# Wave H H2: server-side import batch TTL.
IMPORT_BATCH_TTL_HOURS = 24

# Wave H H8: duplicate-statement detection window.
DUPLICATE_LOOKBACK_DAYS = 7

# Cap: 8 MB upload. Bank statements are tiny in text-PDF form; scanned PDFs hit
# this limit and surface a friendly message instead of a 413.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _decimal_or_none(raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _date_or_today(raw):
    if not raw:
        return date.today()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return date.today()


def _audit(entity_id: int, action: str, changes: dict) -> None:
    """Wave H H7: write an AuditLog row for create/update/delete of a remittance.

    `audit_log` table already exists in Neon (3,078 rows from legacy paths).
    """
    try:
        log_row = AuditLog(
            entity_type="remittance_entry",
            entity_id=entity_id,
            action=action,
            changed_fields=changes,
            user_id=current_user.id if current_user.is_authenticated else None,
            ip_address=(request.headers.get("X-Forwarded-For", request.remote_addr) or "")[:50],
            user_agent=(request.headers.get("User-Agent") or "")[:1000],
        )
        db.session.add(log_row)
    except Exception as e:
        log.warning("AuditLog write failed (non-fatal): %s", e)


def _user_can_read_entry(entry: RemittanceEntry) -> bool:
    """Wave H H1: STRICT ownership check.

    Per council #1: admin role does NOT confer cross-user remittance read. If support
    truly needs to see another user's entries, that must be a separate /admin/remittance/*
    route with explicit superadmin scope.
    """
    return entry.user_id == current_user.id


def _quota_used_today(user_id: int) -> int:
    cutoff = datetime.utcnow() - timedelta(hours=24)
    return (
        RemittanceImportBatch.query
        .filter(RemittanceImportBatch.user_id == user_id,
                RemittanceImportBatch.created_at >= cutoff)
        .count()
    )


def _recent_duplicate(user_id: int, file_hash: str):
    cutoff = datetime.utcnow() - timedelta(days=DUPLICATE_LOOKBACK_DAYS)
    return (
        RemittanceImportBatch.query
        .filter(RemittanceImportBatch.user_id == user_id,
                RemittanceImportBatch.file_sha256 == file_hash,
                RemittanceImportBatch.created_at >= cutoff)
        .order_by(desc(RemittanceImportBatch.created_at))
        .first()
    )


# --------------------------------------------------------------------------- #
# Dashboard + manual entry (Wave A)
# --------------------------------------------------------------------------- #

@remittance_bp.route("/dashboard")
@login_required
def dashboard():
    tax_year = request.args.get("tax_year") or current_sl_tax_year()

    entries = (
        RemittanceEntry.query
        .filter_by(user_id=current_user.id, tax_year=tax_year)
        .order_by(desc(RemittanceEntry.remittance_date), desc(RemittanceEntry.id))
        .all()
    )

    total_foreign_by_ccy: dict = {}
    total_lkr_cbsl = Decimal("0")
    total_lkr_bank = Decimal("0")
    ird_ready_count = 0
    evidence_ready_count = 0
    for e in entries:
        total_foreign_by_ccy.setdefault(e.foreign_currency, Decimal("0"))
        total_foreign_by_ccy[e.foreign_currency] += (e.foreign_amount or Decimal("0"))
        if e.lkr_amount_cbsl is not None:
            total_lkr_cbsl += e.lkr_amount_cbsl
        if e.lkr_amount_bank_rate is not None:
            total_lkr_bank += e.lkr_amount_bank_rate
        status_code = e.completeness_status()[0]
        if status_code == "ird_ready":
            ird_ready_count += 1
        elif status_code == "evidence_ready":
            evidence_ready_count += 1

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
        evidence_ready_count=evidence_ready_count,
        common_currencies=COMMON_CURRENCIES,
    )


@remittance_bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
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

        cbsl_rate = _decimal_or_none(request.form.get("cbsl_rate"))
        cbsl_rate_source = (request.form.get("cbsl_rate_source") or "").strip()[:255] or None

        if not foreign_currency or foreign_amount is None or foreign_amount <= 0:
            flash("Foreign currency and a positive amount are required.", "danger")
            return redirect(url_for("remittance.new"))

        # Wave B1: if user didn't supply a CBSL rate, try the FX service.
        # Frozen-at-entry: whatever we end up with is written into the record;
        # the record never re-fetches.
        rate_source_label = None
        if cbsl_rate is None or cbsl_rate <= 0:
            fx = fx_get_rate(foreign_currency, remittance_date)
            if fx is not None:
                cbsl_rate = fx.value
                rate_source_label = fx.source        # 'cbsl' | 'cbsl_cached' | 'ecb_proxy'
        else:
            # User-supplied — cache it for future lookups + tag as manual
            fx_store_manual(foreign_currency, remittance_date, cbsl_rate)
            rate_source_label = "manual"

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
            cbsl_rate_source=cbsl_rate_source or rate_source_label,
            cbsl_rate_captured_at=datetime.utcnow() if cbsl_rate else None,
            lkr_amount_cbsl=lkr_amount_cbsl,
            rate_entered_manually=(rate_source_label == "manual"),
            source_country=source_country,
            payer_name=payer_name,
            foreign_tax_withheld_amount=foreign_tax_amount,
            foreign_tax_withheld_currency=foreign_tax_ccy,
            tax_year=current_sl_tax_year(remittance_date),
            notes=notes,
        )
        db.session.add(entry)
        db.session.flush()
        _audit(entry.id, "INSERT", {
            "source": "manual",
            "foreign_currency": foreign_currency,
            "foreign_amount": str(foreign_amount),
            "tax_year": entry.tax_year,
        })
        db.session.commit()
        log.info("Remittance entry %s created (manual) by user %s", entry.id, current_user.id)

        # Wave 1 EVENT SPINE: per-entry analytics event + IRD-ready badge event
        # if all evidence is captured. Best-effort, never raises.
        emit_event(
            "remittance_added",
            user_id=current_user.id,
            organization_id=org.id if org else None,
            payload={
                "entry_id": entry.id,
                "currency": foreign_currency,
                "amount": str(foreign_amount),
                "tax_year": entry.tax_year,
                "cbsl_rate_source": rate_source_label,
                "via": "manual",
            },
            source="route:remittance.new",
        )
        try:
            _status_code, _ = entry.completeness_status()
            if _status_code in ("ird_ready", "evidence_ready"):
                emit_event(
                    "remittance_ird_ready",
                    user_id=current_user.id,
                    organization_id=org.id if org else None,
                    payload={"entry_id": entry.id, "status": _status_code},
                    source="route:remittance.new",
                )
        except Exception as _ev_err:
            log.debug("emit(remittance_ird_ready) failed for entry %s: %s", entry.id, _ev_err)

        flash("Remittance entry saved.", "success")
        return redirect(url_for("remittance.detail", entry_id=entry.id))

    # D5: pre-load today's CBSL rate for the default currency (USD) so the form
    # can populate the rate field on page load without a JS round-trip.
    # The JS auto-fill (below) will supersede this on currency/date change.
    _initial_rate = None
    _initial_rate_source = None
    try:
        from tasks.cbsl_rate_fetch import get_cbsl_rate as _get_cbsl
        _fx = _get_cbsl(COMMON_CURRENCIES[0], date.today())  # USD
        if _fx is None:
            _fx = fx_get_rate(COMMON_CURRENCIES[0], date.today())
        if _fx is not None:
            _initial_rate = str(_fx.value)
            _initial_rate_source = _fx.label_for_ui
    except Exception as _pre_err:
        log.debug("new() pre-load cbsl rate failed: %s", _pre_err)

    return render_template(
        "remittance/new.html",
        common_currencies=COMMON_CURRENCIES,
        today=date.today().isoformat(),
        initial_cbsl_rate=_initial_rate,
        initial_cbsl_rate_source=_initial_rate_source,
    )


@remittance_bp.route("/<int:entry_id>")
@login_required
def detail(entry_id):
    entry = RemittanceEntry.query.get_or_404(entry_id)
    if not _user_can_read_entry(entry):
        abort(403)
    return render_template("remittance/detail.html", entry=entry)


# --------------------------------------------------------------------------- #
# Import flow — server-side storage (Wave H, H2)
# --------------------------------------------------------------------------- #

@remittance_bp.route("/import", methods=["GET", "POST"])
@login_required
def import_upload():
    if request.method == "POST":
        # H6: per-user daily quota
        used = _quota_used_today(current_user.id)
        if used >= IMPORTS_PER_USER_PER_DAY:
            flash(
                f"Daily import limit reached ({IMPORTS_PER_USER_PER_DAY} statements per 24 h). "
                "Please come back tomorrow or use manual entry.",
                "warning",
            )
            return redirect(url_for("remittance.import_upload"))

        f = request.files.get("statement")
        if not f or not f.filename:
            flash("Pick a PDF or CSV bank statement to upload.", "danger")
            return redirect(url_for("remittance.import_upload"))

        file_bytes = f.read()
        if len(file_bytes) == 0:
            flash("Upload was empty.", "danger")
            return redirect(url_for("remittance.import_upload"))
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            flash(
                f"File is too large (>{MAX_UPLOAD_BYTES // (1024*1024)} MB). "
                "Split it into smaller pages or export a CSV from your bank's portal.",
                "warning",
            )
            return redirect(url_for("remittance.import_upload"))

        # H8: duplicate detection
        file_hash = sha256_hex(file_bytes)
        dup = _recent_duplicate(current_user.id, file_hash)
        if dup and not request.form.get("allow_duplicate"):
            flash(
                f"This exact file was already uploaded on "
                f"{dup.created_at.strftime('%Y-%m-%d %H:%M UTC')}. "
                "Re-upload only if you mean to (tick the duplicate-OK box).",
                "warning",
            )
            return render_template("remittance/import.html", duplicate_hash=file_hash)

        # H4: file-content validation. parse_upload returns kind=None if rejected.
        kind, candidates = parse_upload(f.filename, file_bytes)
        if kind is None:
            flash(
                "That file doesn't look like a PDF or CSV bank statement. "
                "Please upload a text-PDF from your bank's portal or a CSV export.",
                "danger",
            )
            return redirect(url_for("remittance.import_upload"))

        if not candidates:
            flash(
                "Couldn't extract any credits from this file. If it's a scanned PDF "
                "(image-only), please export a text PDF or CSV from your bank's portal "
                "and try again. Manual entry still works at /remittance/new.",
                "warning",
            )
            return redirect(url_for("remittance.import_upload"))

        # H2: server-side storage instead of session cookie. The candidate list
        # is JSON-serialised; Decimals → strings for round-trip safety.
        serialised = [
            {**c,
             "lkr_amount": str(c["lkr_amount"]),
             "foreign_amount": str(c["foreign_amount"]) if c["foreign_amount"] is not None else None,
             "implied_rate": str(c["implied_rate"]) if c["implied_rate"] is not None else None}
            for c in candidates
        ]
        import_id = str(uuid.uuid4())[:12]
        batch = RemittanceImportBatch(
            import_id=import_id,
            user_id=current_user.id,
            filename=f.filename[:512],
            kind=kind,
            candidates=serialised,
            file_sha256=file_hash,
            expires_at=datetime.utcnow() + timedelta(hours=IMPORT_BATCH_TTL_HOURS),
        )
        db.session.add(batch)
        db.session.commit()

        log.info(
            "Import %s created (user=%s file=%s kind=%s candidates=%d quota_used=%d/%d)",
            import_id, current_user.id, f.filename, kind, len(candidates),
            used + 1, IMPORTS_PER_USER_PER_DAY,
        )
        return redirect(url_for("remittance.import_review", import_id=import_id))

    return render_template("remittance/import.html")


def _load_batch(import_id: str) -> RemittanceImportBatch:
    """Wave H H1 + H2: load and authorise an import batch.

    404 if not found OR if it belongs to a different user (no leak).
    410-feel ("expired") if past TTL.
    """
    batch = RemittanceImportBatch.query.filter_by(import_id=import_id).first()
    if batch is None or batch.user_id != current_user.id:
        abort(404)
    if batch.expires_at and batch.expires_at < datetime.utcnow():
        abort(410)
    return batch


@remittance_bp.route("/import/<import_id>/review", methods=["GET"])
@login_required
def import_review(import_id):
    try:
        batch = _load_batch(import_id)
    except Exception:
        flash("Import session not found or expired. Please upload again.", "warning")
        return redirect(url_for("remittance.import_upload"))

    suggested = sum(1 for c in batch.candidates if c.get("is_foreign_remittance"))
    return render_template(
        "remittance/import_review.html",
        import_id=batch.import_id,
        filename=batch.filename,
        kind=batch.kind,
        candidates=batch.candidates,
        suggested_count=suggested,
    )


@remittance_bp.route("/import/<import_id>/confirm", methods=["POST"])
@login_required
def import_confirm(import_id):
    try:
        batch = _load_batch(import_id)
    except Exception:
        flash("Import session not found or expired. Please upload again.", "warning")
        return redirect(url_for("remittance.import_upload"))

    org = current_user.get_default_organization() if hasattr(current_user, "get_default_organization") else None
    created = 0
    skipped_ambiguous = 0

    for c in batch.candidates:
        idx = c["row_index"]
        if not request.form.get(f"include[{idx}]"):
            continue

        remittance_date_str = request.form.get(f"date[{idx}]") or c.get("txn_date") or ""
        try:
            remittance_date = datetime.strptime(remittance_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            remittance_date = date.today()

        foreign_currency = (request.form.get(f"ccy[{idx}]") or c.get("foreign_currency") or "").strip().upper()[:3]
        foreign_amount = _decimal_or_none(request.form.get(f"famt[{idx}]") or c.get("foreign_amount"))

        # Wave H H3 (council #1 fix #1 + #10): if the foreign amount is missing or
        # the currency is missing, SKIP. Do NOT fall back to LKR=LKR. Bad data is
        # worse than no data; the ledger feeds tax compute.
        if not foreign_currency or foreign_amount is None or foreign_amount <= 0:
            skipped_ambiguous += 1
            log.info("Import %s row %s skipped: ambiguous (ccy=%r famt=%r)",
                     import_id, idx, foreign_currency, foreign_amount)
            continue

        lkr_bank = _decimal_or_none(request.form.get(f"lkr[{idx}]") or c.get("lkr_amount"))
        payer = (request.form.get(f"payer[{idx}]") or c.get("likely_payer") or "").strip()[:255] or None
        country = (request.form.get(f"country[{idx}]") or c.get("source_country_iso2") or "").strip().upper()[:2] or None
        notes = (request.form.get(f"notes[{idx}]") or c.get("description") or "")[:1000] or None

        # Wave B1: auto-populate CBSL rate from FX service at import time.
        # Frozen-at-entry per record. Source label preserved.
        fx = fx_get_rate(foreign_currency, remittance_date)
        cbsl_rate = fx.value if fx else None
        cbsl_source = fx.source if fx else None
        lkr_cbsl = (foreign_amount * cbsl_rate).quantize(Decimal("0.01")) if cbsl_rate else None

        entry = RemittanceEntry(
            user_id=current_user.id,
            organization_id=org.id if org else None,
            remittance_date=remittance_date,
            foreign_currency=foreign_currency,
            foreign_amount=foreign_amount,
            lkr_amount_bank_rate=lkr_bank,
            cbsl_rate=cbsl_rate,
            cbsl_rate_source=cbsl_source,
            cbsl_rate_captured_at=datetime.utcnow() if cbsl_rate else None,
            lkr_amount_cbsl=lkr_cbsl,
            rate_entered_manually=False,
            source_country=country,
            payer_name=payer,
            tax_year=current_sl_tax_year(remittance_date),
            notes=notes,
        )
        db.session.add(entry)
        db.session.flush()
        _audit(entry.id, "INSERT", {
            "source": f"import:{batch.import_id}",
            "foreign_currency": foreign_currency,
            "foreign_amount": str(foreign_amount),
            "cbsl_rate_source": cbsl_source,
            "row_index": idx,
        })
        # Wave 1 EVENT SPINE: per-row analytics event. We emit BEFORE the
        # batch commit so that even if commit fails partway, the analytics
        # rows we did get reflect what was attempted. emit() is best-effort.
        emit_event(
            "remittance_added",
            user_id=current_user.id,
            organization_id=org.id if org else None,
            payload={
                "entry_id": entry.id,
                "currency": foreign_currency,
                "amount": str(foreign_amount),
                "tax_year": entry.tax_year,
                "cbsl_rate_source": cbsl_source,
                "via": "import",
                "import_id": batch.import_id,
                "row_index": idx,
            },
            source="route:remittance.import_confirm",
        )
        created += 1

    batch.consumed_at = datetime.utcnow()
    db.session.commit()

    # Wave 1 EVENT SPINE: bank-statement summary event. Single fire per
    # /import/<id>/confirm regardless of row count. Feeds Gemini-cost monitor
    # and import-quality KPI dashboards (council #2 2026-05-17).
    emit_event(
        "bank_statement_uploaded",
        user_id=current_user.id,
        organization_id=org.id if org else None,
        payload={
            "import_id": batch.import_id,
            "filename": batch.filename,
            "kind": batch.kind,
            "created": created,
            "skipped_ambiguous": skipped_ambiguous,
        },
        source="route:remittance.import_confirm",
    )

    if created == 0:
        flash("No rows were imported (none selected or all skipped as ambiguous).", "warning")
        return redirect(url_for("remittance.import_upload"))

    msg = f"Imported {created} remittance{'s' if created != 1 else ''} from your bank statement."
    if skipped_ambiguous:
        msg += f" {skipped_ambiguous} row{'s' if skipped_ambiguous != 1 else ''} skipped as ambiguous (no foreign amount/currency)."
    flash(msg, "success")
    log.info("Import %s confirmed: user=%s created=%d skipped_ambiguous=%d",
             import_id, current_user.id, created, skipped_ambiguous)
    return redirect(url_for("remittance.dashboard"))


# --------------------------------------------------------------------------- #
# D5 / F-Feature-3.7 — CBSL auto-rate JSON endpoint
# --------------------------------------------------------------------------- #

@remittance_bp.route("/api/cbsl-rate")
@login_required
def cbsl_rate_api():
    """Return the cached CBSL middle rate for a currency + date.

    GET /remittance/api/cbsl-rate?currency=USD&date=2026-05-22

    Response (JSON):
        {
          "found":    true,
          "value":    "324.7184",          # Decimal string, LKR per 1 unit foreign
          "source":   "cbsl_cached",       # cbsl | cbsl_cached | ecb_proxy | manual
          "is_ird_defensible": true,
          "rate_date": "2026-05-22",       # may differ from requested date on non-trading days
          "label":    "Verified CBSL rate (cached)"
        }
    or:
        { "found": false }
    """
    from flask import jsonify
    from tasks.cbsl_rate_fetch import get_cbsl_rate

    currency = (request.args.get("currency") or "").strip().upper()[:3]
    date_str = (request.args.get("date") or "").strip()

    if not currency or currency == "LKR":
        return jsonify({"found": False}), 200

    if date_str:
        try:
            from datetime import datetime as _dt
            req_date = _dt.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "invalid date format, expected YYYY-MM-DD"}), 400
    else:
        req_date = date.today()

    fx = get_cbsl_rate(currency, req_date)

    if fx is None:
        # Fallback: try the full tiered path (live CBSL fetch or ecb_proxy).
        try:
            fx = fx_get_rate(currency, req_date)
        except Exception as exc:
            log.warning("cbsl_rate_api: fx_get_rate raised: %s", exc)
            fx = None

    if fx is None:
        return jsonify({"found": False}), 200

    return jsonify({
        "found": True,
        "value": str(fx.value),
        "source": fx.source,
        "is_ird_defensible": fx.is_ird_defensible,
        "rate_date": fx.rate_date.isoformat(),
        "label": fx.label_for_ui,
    }), 200


def register_routes(app):
    app.register_blueprint(remittance_bp)
    log.info("Remittance routes registered at /remittance/* (incl. hardened /import)")
