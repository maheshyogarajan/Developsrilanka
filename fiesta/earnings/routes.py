"""fiesta.earnings.routes — Flask blueprint for the S4 'drop statements' screen.

URL prefix: /earnings/*

Endpoints (S4 spec):
  GET    /earnings                       → main upload screen
  POST   /earnings/upload                → multipart upload, save + extract
  GET    /earnings/extraction/<sid>      → extraction progress + entries
  POST   /earnings/confirm/<entry_id>    → customer confirms an extracted entry
  POST   /earnings/edit/<entry_id>       → customer corrects a value
  POST   /earnings/manual                → manual entry (no statement)
  DELETE /earnings/<sid>                 → remove a statement (and its entries)
  GET    /earnings/summary               → totals per currency × category for the year
  GET    /earnings/manual_entry          → manual-entry form (fallback path)

Register via fiesta.earnings.routes.register_routes(app) in main.py.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app import db
from fiesta.earnings.extractor import extract_statement
from fiesta.earnings.models import (
    MAX_FILE_BYTES,
    IncomeCategory,
    IncomeEntry,
    Statement,
    StatementDocType,
    StatementStatus,
    sl_tax_year_for,
)
from fiesta.earnings.to_tax import income_summary_for_tax_year

log = logging.getLogger(__name__)

earnings_bp = Blueprint(
    "earnings",
    __name__,
    url_prefix="/earnings",
    template_folder="../../templates",
)

# Storage root for local file backend. Override via env FIESTA_EARNINGS_UPLOAD_DIR.
_DEFAULT_UPLOAD_ROOT = Path(
    os.environ.get("FIESTA_EARNINGS_UPLOAD_DIR")
    or os.path.join(tempfile.gettempdir(), "fiesta_earnings_uploads")
)


def _ensure_upload_root() -> Path:
    _DEFAULT_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    return _DEFAULT_UPLOAD_ROOT


def _user_can_access(stmt: Statement) -> bool:
    """STRICT ownership: only the uploader sees their own statements."""
    return stmt.user_id == current_user.id


def _user_can_access_entry(entry: IncomeEntry) -> bool:
    return entry.user_id == current_user.id


def _audit(entity_type: str, entity_id: int, action: str, changes: dict) -> None:
    """Lightweight audit row. Best-effort; non-fatal on failure."""
    try:
        from models import AuditLog
        row = AuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            changed_fields=changes,
            user_id=current_user.id if current_user.is_authenticated else None,
            ip_address=(request.headers.get("X-Forwarded-For", request.remote_addr) or "")[:50],
            user_agent=(request.headers.get("User-Agent") or "")[:1000],
        )
        db.session.add(row)
    except Exception as exc:  # pragma: no cover
        log.warning("earnings audit log failed (non-fatal): %s", exc)


def _safe_decimal(raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _safe_date(raw):
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# GET /earnings — main upload screen
# --------------------------------------------------------------------------- #


@earnings_bp.route("", methods=["GET"])
@earnings_bp.route("/", methods=["GET"])
@login_required
def index():
    """Render the 'drop your statements here' screen with the user's uploads.

    X9 F3.1: Earn-in canonical surface split. For sl_foreign_income personas,
    /remittance/* is the authoritative earn-in path (CBSL middle rate,
    inward-remittance evidence, IRD-ready badge). /earnings/* stays for
    non-FIESTA personas (bookkeeping). A sl_foreign_income user landing on
    /earnings is redirected to /remittance/dashboard so they only ever see
    one Earn-in surface; eventually /earnings/* is retired when the
    bookkeeping product is.
    """
    if getattr(current_user, 'persona', None) == 'sl_foreign_income':
        return redirect(url_for('remittance.dashboard'))

    statements = (
        Statement.query
        .filter(Statement.user_id == current_user.id)
        .order_by(Statement.uploaded_at.desc())
        .all()
    )
    tax_year = request.args.get("tax_year") or sl_tax_year_for(date.today())

    doc_types = [(d.value, d.value.replace("_", " ").title()) for d in StatementDocType]

    return render_template(
        "earnings/index.html",
        statements=statements,
        doc_types=doc_types,
        tax_year=tax_year,
        max_mb=int(MAX_FILE_BYTES / (1024 * 1024)),
    )


# --------------------------------------------------------------------------- #
# POST /earnings/upload — multipart upload → save → trigger extraction
# --------------------------------------------------------------------------- #


@earnings_bp.route("/upload", methods=["POST"])
@login_required
def upload():
    uploaded = request.files.get("document")
    doc_type = request.form.get("doc_type") or StatementDocType.BANK_STATEMENT.value
    tax_year = request.form.get("tax_year") or sl_tax_year_for(date.today())
    period_start = _safe_date(request.form.get("period_start"))
    period_end = _safe_date(request.form.get("period_end"))

    if not uploaded or not uploaded.filename:
        flash("Please pick a file to drop in.", "warning")
        return redirect(url_for("earnings.index"))

    # Reject anything not in our doc_type enum.
    try:
        StatementDocType(doc_type)
    except ValueError:
        flash("Unknown document type. Please pick from the list.", "warning")
        return redirect(url_for("earnings.index"))

    # Size guard. Reading the stream length without fully buffering on disk first.
    uploaded.stream.seek(0, os.SEEK_END)
    size_bytes = uploaded.stream.tell()
    uploaded.stream.seek(0)
    if size_bytes == 0:
        flash("That file looks empty. Try saving the PDF again and re-upload.", "warning")
        return redirect(url_for("earnings.index"))
    if size_bytes > MAX_FILE_BYTES:
        flash(
            f"File is larger than {int(MAX_FILE_BYTES / (1024 * 1024))}MB. "
            "Compress the PDF or split it.",
            "warning",
        )
        return redirect(url_for("earnings.index"))

    # Persist file locally — fiesta_earnings_uploads/<user_id>/<sha>_<safe_name>.
    upload_root = _ensure_upload_root()
    user_dir = upload_root / str(current_user.id)
    user_dir.mkdir(parents=True, exist_ok=True)
    raw_bytes = uploaded.read()
    sha = hashlib.sha256(raw_bytes).hexdigest()
    safe_name = "".join(c if c.isalnum() or c in (".", "-", "_") else "_" for c in uploaded.filename)
    final_path = user_dir / f"{sha[:16]}_{safe_name}"
    final_path.write_bytes(raw_bytes)

    stmt = Statement(
        user_id=current_user.id,
        file_path=str(final_path),
        file_name=uploaded.filename,
        file_size_bytes=size_bytes,
        file_sha256=sha,
        storage_backend="local",
        doc_type=doc_type,
        period_start=period_start,
        period_end=period_end,
        status=StatementStatus.UPLOADED.value,
        tax_year=tax_year,
    )
    db.session.add(stmt)
    db.session.flush()  # populate stmt.id for extractor

    _audit("earnings_statement", stmt.id, "INSERT", {
        "doc_type": doc_type, "file_name": uploaded.filename, "size_bytes": size_bytes,
    })

    # Synchronous extraction — keep the request simple. For long-running OCR a
    # future iteration can hand this to Celery; for v1 it's blocking.
    result = extract_statement(stmt, db.session)
    db.session.commit()

    if result["ok"]:
        n = len(result["entries"])
        if n > 0:
            flash(
                f"Got it. We pulled {n} income {'entry' if n == 1 else 'entries'} "
                "from that doc — review them below.",
                "success",
            )
        else:
            flash(
                "Got the document — but we couldn't pull income entries automatically "
                "(this happens with balance-only or assets-only statements). "
                "Add entries manually below.",
                "info",
            )
        return redirect(url_for("earnings.extraction", statement_id=stmt.id))

    # Extraction failed.
    if result["at_attempt_cap"]:
        flash(
            "We tried but couldn't read that document. No worries — "
            "you can type the income in manually.",
            "warning",
        )
        return redirect(url_for("earnings.manual_entry_form"))
    flash(
        f"Extraction failed: {result['failure_reason']}. "
        "Try a clearer copy or pick a different document.",
        "warning",
    )
    return redirect(url_for("earnings.index"))


# --------------------------------------------------------------------------- #
# GET /earnings/extraction/<sid> — review extracted entries
# --------------------------------------------------------------------------- #


@earnings_bp.route("/extraction/<int:statement_id>", methods=["GET"])
@login_required
def extraction(statement_id: int):
    stmt = Statement.query.get_or_404(statement_id)
    if not _user_can_access(stmt):
        abort(403)
    entries = stmt.entries.order_by(IncomeEntry.entry_date).all()
    return render_template(
        "earnings/extraction.html",
        statement=stmt,
        entries=entries,
        categories=[c.value for c in IncomeCategory],
    )


# --------------------------------------------------------------------------- #
# POST /earnings/confirm/<entry_id> — confirm an extracted entry as-is
# --------------------------------------------------------------------------- #


@earnings_bp.route("/confirm/<int:entry_id>", methods=["POST"])
@login_required
def confirm_entry(entry_id: int):
    entry = IncomeEntry.query.get_or_404(entry_id)
    if not _user_can_access_entry(entry):
        abort(403)
    if not entry.confirmed_by_customer:
        entry.confirmed_by_customer = True
        entry.confirmed_at = datetime.utcnow()
        _audit("earnings_income_entry", entry.id, "UPDATE", {"confirmed_by_customer": [False, True]})

    # If all entries for this statement are confirmed → statement → CONFIRMED.
    if entry.statement_id is not None:
        stmt = Statement.query.get(entry.statement_id)
        if stmt and stmt.entries.filter(IncomeEntry.confirmed_by_customer.is_(False)).count() == 0:
            stmt.status = StatementStatus.CONFIRMED.value

    db.session.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True, "entry": entry.to_dict()})
    flash("Confirmed.", "success")
    return redirect(request.referrer or url_for("earnings.index"))


# --------------------------------------------------------------------------- #
# POST /earnings/edit/<entry_id> — customer corrects an extracted value
# --------------------------------------------------------------------------- #


@earnings_bp.route("/edit/<int:entry_id>", methods=["POST"])
@login_required
def edit_entry(entry_id: int):
    entry = IncomeEntry.query.get_or_404(entry_id)
    if not _user_can_access_entry(entry):
        abort(403)

    changes: dict = {}
    new_amount = _safe_decimal(request.form.get("amount"))
    new_currency = (request.form.get("currency") or "").upper().strip()[:3] or None
    new_date = _safe_date(request.form.get("entry_date"))
    new_source = (request.form.get("source") or "").strip() or None
    new_category = request.form.get("category") or None

    if new_category and new_category not in {c.value for c in IncomeCategory}:
        flash(f"Unknown category: {new_category}", "warning")
        return redirect(request.referrer or url_for("earnings.index"))

    # Snapshot original_value JSON before mutating. Append, don't overwrite.
    history = entry.original_value or []
    if not isinstance(history, list):
        history = [history]

    def _record(field, old, new):
        if old != new:
            history.append({
                "field": field,
                "old_value": str(old) if old is not None else None,
                "new_value": str(new) if new is not None else None,
                "edited_at": datetime.utcnow().isoformat(),
            })
            changes[field] = [old, new]

    if new_amount is not None and new_amount != entry.amount:
        _record("amount", float(entry.amount) if entry.amount is not None else None, float(new_amount))
        entry.amount = new_amount
        # Invalidate LKR cache; will be re-resolved on next to_tax aggregation.
        if (new_currency or entry.currency) == "LKR":
            entry.amount_lkr = new_amount
            entry.fx_rate_lkr = Decimal("1")
            entry.fx_rate_source = "lkr_native"
        else:
            entry.amount_lkr = None
            entry.fx_rate_lkr = None
            entry.fx_rate_source = None

    if new_currency and new_currency != entry.currency:
        _record("currency", entry.currency, new_currency)
        entry.currency = new_currency
        if new_currency != "LKR":
            entry.amount_lkr = None
            entry.fx_rate_lkr = None
            entry.fx_rate_source = None

    if new_date and new_date != entry.entry_date:
        _record("entry_date", entry.entry_date.isoformat() if entry.entry_date else None, new_date.isoformat())
        entry.entry_date = new_date
        # If date changed across SL tax year boundary, recompute.
        entry.tax_year = sl_tax_year_for(new_date)

    if new_source is not None and new_source != entry.source:
        _record("source", entry.source, new_source)
        entry.source = new_source

    if new_category and new_category != entry.category:
        _record("category", entry.category, new_category)
        entry.category = new_category

    if history != (entry.original_value or []):
        entry.original_value = history

    # Edit implies customer confirmation.
    if not entry.confirmed_by_customer:
        _record("confirmed_by_customer", False, True)
        entry.confirmed_by_customer = True
        entry.confirmed_at = datetime.utcnow()

    if changes:
        _audit("earnings_income_entry", entry.id, "UPDATE", changes)

    # Re-check statement status if all entries now confirmed.
    if entry.statement_id is not None:
        stmt = Statement.query.get(entry.statement_id)
        if stmt and stmt.entries.filter(IncomeEntry.confirmed_by_customer.is_(False)).count() == 0:
            stmt.status = StatementStatus.CONFIRMED.value

    db.session.commit()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True, "entry": entry.to_dict()})
    flash("Updated.", "success")
    return redirect(request.referrer or url_for("earnings.index"))


# --------------------------------------------------------------------------- #
# GET /earnings/manual_entry, POST /earnings/manual — manual entry fallback
# --------------------------------------------------------------------------- #


@earnings_bp.route("/manual_entry", methods=["GET"])
@login_required
def manual_entry_form():
    return render_template(
        "earnings/manual_entry.html",
        categories=[c.value for c in IncomeCategory],
        tax_year=sl_tax_year_for(date.today()),
    )


@earnings_bp.route("/manual", methods=["POST"])
@login_required
def manual_entry_create():
    entry_date = _safe_date(request.form.get("entry_date")) or date.today()
    currency = (request.form.get("currency") or "LKR").upper().strip()[:3]
    amount = _safe_decimal(request.form.get("amount"))
    source = (request.form.get("source") or "manual").strip()
    category = request.form.get("category") or IncomeCategory.SALARY.value

    if amount is None or amount <= 0:
        flash("Enter a positive amount.", "warning")
        return redirect(url_for("earnings.manual_entry_form"))
    if category not in {c.value for c in IncomeCategory}:
        flash(f"Unknown category: {category}", "warning")
        return redirect(url_for("earnings.manual_entry_form"))

    entry = IncomeEntry(
        user_id=current_user.id,
        statement_id=None,
        entry_date=entry_date,
        currency=currency,
        amount=amount,
        amount_lkr=amount if currency == "LKR" else None,
        fx_rate_lkr=Decimal("1") if currency == "LKR" else None,
        fx_rate_source="lkr_native" if currency == "LKR" else None,
        source=source,
        category=category,
        confirmed_by_customer=True,  # manual entry = customer-asserted
        confirmed_at=datetime.utcnow(),
        tax_year=sl_tax_year_for(entry_date),
    )
    db.session.add(entry)
    db.session.flush()
    _audit("earnings_income_entry", entry.id, "INSERT", {"source": "manual", "category": category})
    db.session.commit()

    flash("Saved.", "success")
    return redirect(url_for("earnings.index"))


# --------------------------------------------------------------------------- #
# DELETE /earnings/<sid> — remove a statement (cascade deletes entries)
# --------------------------------------------------------------------------- #


@earnings_bp.route("/<int:statement_id>", methods=["DELETE", "POST"])
@login_required
def delete_statement(statement_id: int):
    stmt = Statement.query.get_or_404(statement_id)
    if not _user_can_access(stmt):
        abort(403)

    # Allow POST with ?_method=DELETE for HTML form compatibility.
    if request.method == "POST" and request.form.get("_method", "").upper() != "DELETE":
        abort(405)

    # Best-effort file cleanup.
    try:
        if stmt.storage_backend == "local" and stmt.file_path and Path(stmt.file_path).exists():
            Path(stmt.file_path).unlink()
    except OSError as exc:
        log.warning("earnings: could not unlink %s: %s", stmt.file_path, exc)

    _audit("earnings_statement", stmt.id, "DELETE", {"file_name": stmt.file_name})
    db.session.delete(stmt)
    db.session.commit()
    flash("Statement removed.", "success")
    return redirect(url_for("earnings.index"))


# --------------------------------------------------------------------------- #
# GET /earnings/summary — totals per currency × category for the tax year
# --------------------------------------------------------------------------- #


@earnings_bp.route("/summary", methods=["GET"])
@login_required
def summary():
    tax_year = request.args.get("tax_year") or sl_tax_year_for(date.today())
    payload = income_summary_for_tax_year(current_user.id, tax_year)
    # Commit any backfilled amount_lkr / fx_rate fields written by to_tax.
    db.session.commit()

    if request.headers.get("Accept", "").startswith("application/json"):
        # Decimal → str for JSON.
        return jsonify({
            "user_id": payload["user_id"],
            "tax_year": payload["tax_year"],
            "by_category_lkr": {k: str(v) for k, v in payload["by_category_lkr"].items()},
            "by_currency": {k: str(v) for k, v in payload["by_currency"].items()},
            "total_lkr": str(payload["total_lkr"]),
            "entry_count": payload["entry_count"],
            "unconverted_currencies": payload["unconverted_currencies"],
            "fx_warnings": payload["fx_warnings"],
        })

    return render_template(
        "earnings/summary.html",
        tax_year=tax_year,
        payload=payload,
    )


def register_routes(app):
    app.register_blueprint(earnings_bp)
    log.info("Earnings (S4) routes registered at /earnings/*")
