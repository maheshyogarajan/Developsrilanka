"""fiesta.tax_bill.routes -- Flask blueprint for S12 'Your tax bill'.

Routes:
    GET  /tax-bill                                 redirect to current TY
    GET  /tax-bill/<tax_year>                      main HTML view
    GET  /tax-bill/<tax_year>/breakdown            JSON dump (no engine error -> 200)
    GET  /tax-bill/<tax_year>/export               audit pack PDF
    POST /tax-bill/<tax_year>/finalize             lock the bill (pre-S14)

Login: all routes require @login_required. Customer can only see their own
bill (user_id from current_user).

Finalization model: an in-memory module-level dict per (user_id, tax_year)
flips to True. v1 is process-local (single-worker); v1.1 persists via a
TaxBillFinalization model.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

# Defensive imports -- the module must be importable in headless tests.
try:
    from flask import (
        Blueprint, render_template, request, jsonify, redirect, url_for,
        flash, session, send_file, abort, current_app,
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
    def jsonify(d, **kw): return d  # type: ignore
    def redirect(*a, **kw): return None  # type: ignore
    def url_for(*a, **kw): return "#"  # type: ignore
    def flash(*a, **kw): return None  # type: ignore
    def send_file(*a, **kw): return None  # type: ignore
    def abort(*a, **kw): return None  # type: ignore

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

from .aggregator import (
    normalise_tax_year_to_s4_format,
    normalise_tax_year_to_s5_format,
)
from .compute import compute_tax_bill
from .gate_check import run_gate


# ---------------------------------------------------------------------------
# In-memory finalization state. Process-local; v1.1 -> DB.
# ---------------------------------------------------------------------------
_FINALIZED: dict[tuple[int, str], bool] = {}


def _is_finalized(user_id: int, tax_year_s4: str) -> bool:
    return _FINALIZED.get((int(user_id), tax_year_s4), False)


def _set_finalized(user_id: int, tax_year_s4: str) -> None:
    _FINALIZED[(int(user_id), tax_year_s4)] = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _current_user_id() -> int | None:
    if not _HAS_LOGIN or current_user is None:
        return None
    if not getattr(current_user, "is_authenticated", False):
        return None
    return int(current_user.id)


def _decimal_to_str(o):
    """JSON helper -- Decimal -> string."""
    if isinstance(o, Decimal):
        return str(o)
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if hasattr(o, "model_dump"):
        return o.model_dump()
    raise TypeError(f"not JSON serialisable: {type(o)}")


def _serialise_report(report) -> dict[str, Any]:
    """Serialise a TaxBillReport as a JSON-safe dict (for /breakdown)."""
    inputs = report.inputs

    def _items_to_serialisable(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for it in items:
            out.append({k: (str(v) if isinstance(v, Decimal) else v) for k, v in it.items()})
        return out

    return {
        "user_id": report.user_id,
        "tax_year": report.tax_year_s5_format,
        "is_finalized": report.is_finalized,
        "engine_error": report.engine_error,
        "headline": {
            "gross_income_lkr": str(report.gross_income_lkr),
            "total_deductions_lkr": str(report.total_deductions_lkr),
            "taxable_income_lkr": str(report.taxable_income_lkr),
            "gross_tax_payable_lkr": str(report.gross_tax_payable_lkr),
            "net_tax_payable_lkr": str(report.net_tax_payable_lkr),
            "tax_without_deductions_lkr": str(report.tax_without_deductions_lkr),
            "savings_vs_no_deductions_lkr": str(report.savings_vs_no_deductions_lkr),
        },
        "audit_defensibility": {
            "score": report.audit_defensibility_score,
            "label": report.audit_defensibility_label,
            "components": report.audit_score_components,
        },
        "income": {
            "by_category_lkr": {
                k: str(v) for k, v in (inputs.income_by_category_lkr or {}).items()
            },
            "by_currency": {
                k: str(v) for k, v in (inputs.income_by_currency or {}).items()
            },
            "total_lkr": str(inputs.income_total_lkr),
            "entry_count": inputs.income_entry_count,
            "unconverted_currencies": list(inputs.income_unconverted_currencies),
            "fx_warnings": list(inputs.income_fx_warnings),
        },
        "deductions": {
            "items": _items_to_serialisable(inputs.deductions_itemised),
            "total_lkr": str(inputs.deductions_total_lkr),
            "with_evidence_count": inputs.deductions_with_evidence_count,
            "pending_evidence_count": inputs.deductions_pending_evidence_count,
        },
        "service_providers": _items_to_serialisable(inputs.service_providers),
        "rentals": _items_to_serialisable(inputs.rentals),
        "computation": (
            report.computation_with_deductions.to_dict()
            if report.computation_with_deductions is not None else None
        ),
        "missing_disclosures": list(inputs.missing_disclosures),
        "sp_agreement_mismatches": _items_to_serialisable(
            inputs.sp_agreement_mismatches
        ),
        "sources_loaded": list(inputs.sources_loaded),
        "sources_missing": list(inputs.sources_missing),
    }


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
bp = Blueprint(
    "fiesta_tax_bill",
    __name__,
    url_prefix="/tax-bill",
    template_folder="../../templates",
)


_DEFAULT_TAX_YEAR_S5 = "2025/2026"
_DEFAULT_TAX_YEAR_S4 = "2025-26"


@bp.route("/", methods=["GET"])
@bp.route("", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S12", action="index_redirect")
def index_redirect():
    """Redirect /tax-bill -> /tax-bill/<current_ty>."""
    return redirect(url_for("fiesta_tax_bill.show_tax_bill",
                            tax_year=_DEFAULT_TAX_YEAR_S4))


@bp.route("/<tax_year>", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S12", action="show_tax_bill")
def show_tax_bill(tax_year: str):
    """Render the S12 outcome screen."""
    user_id = _current_user_id()
    if not user_id:
        abort(401)

    tax_year_s4 = normalise_tax_year_to_s4_format(tax_year)
    report = compute_tax_bill(user_id, tax_year)

    # Finalize state is process-local.
    report.is_finalized = _is_finalized(user_id, tax_year_s4)

    # Run X6 gate -- DISPLAY_BILL action.
    gate = run_gate(report, action="display_bill")

    return render_template(
        "tax_bill/index.html",
        report=report,
        inputs=report.inputs,
        gate=gate,
        tax_year_s4=tax_year_s4,
        tax_year_display=report.tax_year_s5_format,
    )


@bp.route("/<tax_year>/breakdown", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S12", action="breakdown_json")
def breakdown_json(tax_year: str):
    """JSON dump of the full computation -- powers the audit-pack PDF + tests."""
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"ok": False, "error": "not authenticated"}), 401

    report = compute_tax_bill(user_id, tax_year)
    tax_year_s4 = normalise_tax_year_to_s4_format(tax_year)
    report.is_finalized = _is_finalized(user_id, tax_year_s4)

    gate = run_gate(report, action="export_pdf")
    payload = _serialise_report(report)
    try:
        payload["gate"] = gate.model_dump()  # type: ignore[union-attr]
    except Exception:
        payload["gate"] = {
            "passed": getattr(gate, "passed", True),
            "warnings": getattr(gate, "warnings", []),
            "blocks": getattr(gate, "blocks", []),
            "recommendations": getattr(gate, "recommendations", []),
            "reasoning_trace": getattr(gate, "reasoning_trace", []),
        }
    return jsonify({"ok": True, **payload})


@bp.route("/<tax_year>/export", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S12", action="export_audit_pack")
def export_audit_pack(tax_year: str):
    """Generate + stream the audit-pack PDF."""
    user_id = _current_user_id()
    if not user_id:
        abort(401)

    report = compute_tax_bill(user_id, tax_year)
    tax_year_s4 = normalise_tax_year_to_s4_format(tax_year)
    report.is_finalized = _is_finalized(user_id, tax_year_s4)

    # Build PDF (lazy import: ReportLab is heavy).
    try:
        from .audit_pack import build_audit_pack
    except Exception as exc:
        logger.exception("audit_pack import failed: %s", exc)
        return jsonify({
            "ok": False,
            "error": f"PDF generator unavailable: {exc}",
        }), 503

    try:
        pdf_bytes = build_audit_pack(report)
    except Exception as exc:
        logger.exception("audit_pack build failed: %s", exc)
        return jsonify({
            "ok": False,
            "error": f"PDF generation failed: {exc}",
        }), 500

    from io import BytesIO
    filename = (
        f"FIESTA_AuditPack_{tax_year_s4}_user{user_id}.pdf"
    )
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@bp.route("/<tax_year>/finalize", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S12", action="finalize")
def finalize(tax_year: str):
    """Lock the bill -- gate must pass first."""
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"ok": False, "error": "not authenticated"}), 401

    report = compute_tax_bill(user_id, tax_year)
    gate = run_gate(report, action="finalize")
    blocks = getattr(gate, "blocks", []) or []
    if blocks:
        return jsonify({
            "ok": False,
            "error": "Bill cannot be finalized while compliance blocks are present.",
            "blocks": blocks,
        }), 400

    tax_year_s4 = normalise_tax_year_to_s4_format(tax_year)
    _set_finalized(user_id, tax_year_s4)
    return jsonify({
        "ok": True,
        "is_finalized": True,
        "tax_year": tax_year_s4,
        "net_tax_payable_lkr": str(report.net_tax_payable_lkr),
    })


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def register_blueprint(app) -> None:
    """Register the S12 tax-bill blueprint with the Flask app."""
    app.register_blueprint(bp)
    logger.info("FIESTA S12 tax-bill blueprint registered at /tax-bill")


__all__ = ["bp", "register_blueprint"]
