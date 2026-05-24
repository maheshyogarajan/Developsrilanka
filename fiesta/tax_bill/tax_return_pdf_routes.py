"""fiesta.tax_bill.tax_return_pdf_routes -- IRD-ready PDF download route.

Tier D2-bpdf (2026-05-24). Exposes:

    GET /tax-bill/<tax_year>/return.pdf

Serves the standalone IRD-ready tax return PDF that a customer can MANUALLY
file with IRD. Bypasses the IRD-portal automation gates so FIESTA can ship
revenue without them.

This blueprint is REGISTERED INDEPENDENTLY from fiesta.tax_bill.routes (the
S12 blueprint at /tax-bill) so the download is decoupled from the existing
audit-pack / breakdown / finalize routes. Same url_prefix to keep URLs tidy.

Auth: @login_required. Customer can only download their own bill.
Paywall: same `self_file` tier as the rest of /tax-bill — if you can see
your tax bill, you can download the IRD return PDF.
"""
from __future__ import annotations

import logging
from io import BytesIO

logger = logging.getLogger(__name__)


# Defensive imports — keep the module importable in headless tests.
try:
    from flask import Blueprint, send_file, jsonify, abort
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

    def send_file(*a, **kw): return None  # type: ignore
    def jsonify(d, **kw): return d  # type: ignore
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

from .compute import compute_tax_bill
from .tax_return_pdf import render_tax_return_pdf, filename_for


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
# NOTE: separate name from the existing `fiesta_tax_bill` blueprint so Flask
# does not complain about double registration. Same url_prefix so the URL
# space stays /tax-bill/*.
bp = Blueprint(
    "fiesta_tax_bill_return_pdf",
    __name__,
    url_prefix="/tax-bill",
)


def _current_user_id() -> int | None:
    if not _HAS_LOGIN or current_user is None:
        return None
    if not getattr(current_user, "is_authenticated", False):
        return None
    return int(current_user.id)


@bp.route("/<tax_year>/return.pdf", methods=["GET"])
@login_required
@paywall_required(
    min_tier="self_file",
    screen_id="S12",
    action="download_ird_return_pdf",
)
def download_return_pdf(tax_year: str):
    """Generate + stream the IRD-ready tax return PDF for the current user."""
    user_id = _current_user_id()
    if not user_id:
        abort(401)

    report = compute_tax_bill(user_id, tax_year)
    if report.engine_error:
        # Engine couldn't compute -- surface JSON error, not a broken PDF.
        return jsonify({
            "ok": False,
            "error": (
                "Tax engine did not produce a computation; cannot generate "
                f"IRD return PDF. Detail: {report.engine_error}"
            ),
        }), 503

    try:
        pdf_bytes = render_tax_return_pdf(report)
    except ImportError as exc:
        logger.exception("ReportLab missing for IRD return PDF: %s", exc)
        return jsonify({
            "ok": False,
            "error": f"PDF generator unavailable: {exc}",
        }), 503
    except Exception as exc:
        logger.exception("IRD return PDF build failed: %s", exc)
        return jsonify({
            "ok": False,
            "error": f"PDF generation failed: {exc}",
        }), 500

    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename_for(report),
    )


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def register_blueprint(app) -> None:
    """Register the IRD return PDF download blueprint with the Flask app."""
    app.register_blueprint(bp)
    logger.info(
        "FIESTA Tier-D2-bpdf IRD return PDF route registered at "
        "/tax-bill/<tax_year>/return.pdf"
    )


__all__ = ["bp", "register_blueprint"]
