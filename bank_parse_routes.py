"""bank_parse_routes — canonical bank-statement parse UI (MS2 E.1 / B8 full).

Mounts the canonical bank-parse flow at ``/remittance/import/parse``,
``/remittance/import/parse/<id>/review`` and
``/remittance/import/parse/<id>/confirm``.

DISTINCT from the legacy ``/remittance/import`` flow (which writes only
to ``RemittanceEntry`` + ``RemittanceImportBatch``). The legacy flow
remains the default; this canonical flow writes the schema-correct
``ParsedBankStatement`` + ``Income(source_type='foreign_remittance',
bank_parse_id=...)`` rows per Design Lock 2 §4.

BANK_PARSE_ENABLED feature flag controls behaviour:
  - false (default): GET shows a "request access — beta" notice; POST
    redirects without invoking Gemini. Safe to deploy.
  - true:            full pipeline active.

Approval gate: existing remittance_routes.import_upload daily quota +
duplicate detection do NOT apply to this parallel flow. We have our own
dedup (sha256 file hash + user_id) and rely on Gemini cost monitoring
for runaway-cost protection.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort,
    jsonify,
)
from flask_login import login_required, current_user

from app import db, invalidate_savings_projection, queue_fiesta_event
from events import emit as emit_event
from fiesta.tax.bank_parse import (
    bank_parse_enabled,
    confirm_parse,
    ConfirmedRowInput,
    MAX_UPLOAD_BYTES,
    parse_file,
)
from fiesta.tax.models import ParsedBankStatement
from fx_rate_service import get_rate as fx_get_rate

log = logging.getLogger(__name__)

bank_parse_bp = Blueprint(
    "bank_parse",
    __name__,
    url_prefix="/remittance/import/parse",
)


# --------------------------------------------------------------------------- #
# Upload + parse — POST creates the ParsedBankStatement row
# --------------------------------------------------------------------------- #

@bank_parse_bp.route("", methods=["GET", "POST"])
@bank_parse_bp.route("/", methods=["GET", "POST"])
@login_required
def upload():
    """Canonical Income upload page.

    BANK_PARSE_ENABLED=false → form is disabled; show the beta notice.
    """
    enabled = bank_parse_enabled()

    if request.method == "POST":
        if not enabled:
            flash(
                "Bank statement parsing is in beta. Please request access.",
                "info",
            )
            return redirect(url_for("bank_parse.upload"))

        f = request.files.get("statement")
        if not f or not f.filename:
            flash("Pick a PDF, JPG, or PNG bank statement to upload.", "danger")
            return redirect(url_for("bank_parse.upload"))

        file_bytes = f.read()
        if len(file_bytes) == 0:
            flash("Upload was empty.", "danger")
            return redirect(url_for("bank_parse.upload"))
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            mb = MAX_UPLOAD_BYTES // (1024 * 1024)
            flash(
                f"File is too large (>{mb} MB). "
                "Split the statement or export a smaller range.",
                "warning",
            )
            return redirect(url_for("bank_parse.upload"))

        try:
            result = parse_file(
                user_id=current_user.id,
                file_bytes=file_bytes,
                filename=f.filename,
            )
        except ValueError as exc:
            # Magic-byte mismatch / unsupported kind / oversized
            flash(
                "That file doesn't look like a PDF, JPG, or PNG. "
                f"Detail: {exc}",
                "danger",
            )
            return redirect(url_for("bank_parse.upload"))
        except Exception as exc:
            log.exception("parse_file failed for user=%s", current_user.id)
            flash(
                "Couldn't parse that file. Please try again or use manual "
                "entry at /remittance/new.",
                "danger",
            )
            return redirect(url_for("bank_parse.upload"))

        pbs = result.parsed_bank_statement

        # Event spine: signals the import. Frontend savings counter will
        # refresh after confirm, not after upload.
        emit_event(
            "bank_statement_parsed",
            user_id=current_user.id,
            payload={
                "parsed_bank_statement_id": int(pbs.id),
                "rows_extracted": result.rows_extracted,
                "deduplicated": result.deduplicated,
            },
            source="route:bank_parse.upload",
        )

        if result.deduplicated:
            flash(
                f"This statement was already parsed earlier. "
                f"Reusing {result.rows_extracted} extracted rows.",
                "info",
            )
        elif result.rows_extracted == 0:
            flash(
                "Couldn't extract any inward foreign remittances from this "
                "file. If it's a scanned PDF, try a clearer image or use "
                "manual entry at /remittance/new.",
                "warning",
            )
        else:
            flash(
                f"Extracted {result.rows_extracted} candidate remittance "
                f"row{'s' if result.rows_extracted != 1 else ''}. Review below.",
                "success",
            )

        return redirect(url_for("bank_parse.review", parse_id=pbs.id))

    return render_template(
        "bank_parse/upload.html",
        enabled=enabled,
        max_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
    )


# --------------------------------------------------------------------------- #
# Review — show parsed rows, allow user edits + selection
# --------------------------------------------------------------------------- #

def _load_pbs(parse_id: int) -> ParsedBankStatement:
    pbs = ParsedBankStatement.query.get_or_404(parse_id)
    if pbs.user_id != current_user.id:
        abort(404)  # don't reveal existence
    return pbs


@bank_parse_bp.route("/<int:parse_id>/review", methods=["GET"])
@login_required
def review(parse_id):
    pbs = _load_pbs(parse_id)
    payload = pbs.raw_text or {}
    rows = payload.get("rows", [])

    # CBSL rate lookup for each row (so the review screen pre-populates a
    # rate field per row). Best-effort; on failure leave blank for user.
    rows_with_rates = []
    for r in rows:
        rate_value = None
        rate_source = None
        try:
            ccy = r.get("currency")
            d_str = r.get("date")
            if ccy and d_str:
                d = datetime.strptime(d_str, "%Y-%m-%d").date()
                fx = fx_get_rate(ccy, d)
                if fx is not None:
                    rate_value = str(fx.value)
                    rate_source = getattr(fx, "label_for_ui", None) or fx.source
        except Exception as exc:
            log.debug("review rate-lookup row %s: %s", r.get("row_index"), exc)
        rr = dict(r)
        rr["cbsl_rate"] = rate_value
        rr["cbsl_rate_source"] = rate_source
        rows_with_rates.append(rr)

    return render_template(
        "bank_parse/review.html",
        pbs=pbs,
        rows=rows_with_rates,
        filename=payload.get("filename", "uploaded file"),
        kind=payload.get("kind", "unknown"),
        row_count=len(rows_with_rates),
    )


# --------------------------------------------------------------------------- #
# Confirm — create Income + RemittanceEntry rows
# --------------------------------------------------------------------------- #

@bank_parse_bp.route("/<int:parse_id>/confirm", methods=["POST"])
@login_required
def confirm(parse_id):
    pbs = _load_pbs(parse_id)
    if pbs.status not in {"parsed", "reviewed"}:
        flash(f"Cannot confirm — parse is in status '{pbs.status}'.", "warning")
        return redirect(url_for("bank_parse.review", parse_id=parse_id))

    payload = pbs.raw_text or {}
    raw_rows = payload.get("rows", [])

    inputs: list[ConfirmedRowInput] = []
    for r in raw_rows:
        idx = r.get("row_index")
        if idx is None:
            continue
        inputs.append(ConfirmedRowInput(
            row_index=int(idx),
            include=bool(request.form.get(f"include[{idx}]")),
            date=request.form.get(f"date[{idx}]") or r.get("date", ""),
            amount=request.form.get(f"amount[{idx}]") or r.get("amount", ""),
            currency=(request.form.get(f"currency[{idx}]")
                      or r.get("currency", "")).strip().upper(),
            sender=request.form.get(f"sender[{idx}]") or r.get("sender"),
            source_country=(request.form.get(f"country[{idx}]") or "").strip().upper() or None,
            cbsl_rate=request.form.get(f"cbsl_rate[{idx}]"),
            narration=r.get("narration"),
            swift_code=r.get("swift_code"),
        ))

    org = (current_user.get_default_organization()
           if hasattr(current_user, "get_default_organization") else None)

    # FX lookup callback: prefer the existing fx_rate_service so CBSL
    # rates flow consistently with manual entry + legacy import.
    def _fx_lookup(ccy: str, d: date):
        try:
            fx = fx_get_rate(ccy, d)
            return fx.value if fx else None
        except Exception:
            return None

    result = confirm_parse(
        parsed_bank_statement_id=pbs.id,
        user_id=current_user.id,
        rows=inputs,
        organization_id=(org.id if org else None),
        fx_lookup=_fx_lookup,
    )

    # Event spine: emit one per Income created so analytics matches the
    # legacy import flow's per-row events.
    for inc_id in result.income_ids:
        emit_event(
            "remittance_added",
            user_id=current_user.id,
            organization_id=(org.id if org else None),
            payload={
                "income_id": inc_id,
                "via": "bank_parse",
                "parsed_bank_statement_id": int(pbs.id),
            },
            source="route:bank_parse.confirm",
        )

    # Savings counter refresh (Design Lock 1 + F-Platform-5).
    try:
        invalidate_savings_projection(current_user.id)
        queue_fiesta_event("remittance-added")
    except Exception as exc:
        log.debug("F-Platform-5 event queue failed: %s", exc)

    if result.income_created == 0:
        msg = "No rows imported (none selected or all skipped as invalid)."
        if result.skipped_invalid:
            msg += f" {result.skipped_invalid} skipped (missing FX rate or bad data)."
        flash(msg, "warning")
        return redirect(url_for("bank_parse.review", parse_id=parse_id))

    msg = (
        f"Created {result.income_created} canonical income row"
        f"{'s' if result.income_created != 1 else ''} from the parse"
    )
    if result.skipped_invalid:
        msg += f" ({result.skipped_invalid} skipped as invalid)"
    flash(msg + ".", "success")
    log.info(
        "bank_parse.confirm: pbs=%s user=%s created=%d skipped_invalid=%d",
        pbs.id, current_user.id, result.income_created, result.skipped_invalid,
    )
    return redirect(url_for("remittance.dashboard"))


# --------------------------------------------------------------------------- #
# Status JSON (light, for any AJAX polling future)
# --------------------------------------------------------------------------- #

@bank_parse_bp.route("/<int:parse_id>/status.json", methods=["GET"])
@login_required
def status_json(parse_id):
    pbs = _load_pbs(parse_id)
    payload = pbs.raw_text or {}
    return jsonify({
        "id": int(pbs.id),
        "status": pbs.status,
        "row_count": len(payload.get("rows", [])),
        "parsed_at": pbs.parsed_at.isoformat() if pbs.parsed_at else None,
    })


def register_routes(app):
    app.register_blueprint(bank_parse_bp)
    log.info(
        "Bank-parse routes registered at /remittance/import/parse/* "
        "(BANK_PARSE_ENABLED=%s)",
        bank_parse_enabled(),
    )
