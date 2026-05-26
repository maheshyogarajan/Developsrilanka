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


# X9 F6.1: legacy hard-coded constants. KEPT only as fallbacks for environments
# where `fiesta.paywall.models.current_sl_tax_year` cannot be imported.
# Live code paths MUST route through `_default_tax_year_s4()` so the year
# advances on its own at the SL fiscal flip (1 April) and stays aligned with
# what S14 / paywall sees.
_DEFAULT_TAX_YEAR_S5_FALLBACK = "2025/2026"
_DEFAULT_TAX_YEAR_S4_FALLBACK = "2025-26"

# Years the tax engine actually supports (must match canonical_tax_year_enum's
# dict in fiesta/tax_bill/aggregator.py). Newest first. When the SL fiscal flip
# advances past the newest entry here, _default_tax_year_s4 will keep returning
# the most recent SUPPORTED year until IRD publishes new brackets and a new
# entry is added below (and in aggregator.canonical_tax_year_enum + the
# fiesta.tax.types.TaxYear enum).
_SUPPORTED_TAX_YEARS_S4_NEWEST_FIRST = ["2025-26", "2024-25"]


def _most_recent_supported_tax_year_s4() -> str:
    """Newest tax year the engine has brackets for."""
    return _SUPPORTED_TAX_YEARS_S4_NEWEST_FIRST[0]


def _available_years_for_user(user_id: int) -> list[str]:
    """Return tax-years (S4 format, newest first) the user has data for, plus
    always-include the most-recent and previous supported year so a brand-new
    returning customer still sees a switcher with sensible defaults.

    Sources scanned (defensive imports -- a missing module just contributes
    nothing, never raises):
        DeductionClaim.tax_year     (S5 -- "2025/2026")
        RentalAgreement.tax_year    (S5 -- "2025/2026")
        ServiceAgreement.tax_year   (S4 / mixed)
        IncomeEntry.tax_year        (S4 -- "2025-26")

    All values are normalised to S4 via normalise_tax_year_to_s4_format and
    filtered against _SUPPORTED_TAX_YEARS_S4_NEWEST_FIRST so the dropdown can
    never produce an option the engine can't render.
    """
    found: set[str] = set()

    # Always-include: the supported set so a returning customer with no data
    # in a prior year still sees that year in the dropdown (lets them realise
    # there's nothing there yet, vs hiding the option entirely).
    for ty in _SUPPORTED_TAX_YEARS_S4_NEWEST_FIRST:
        found.add(ty)

    def _scan(model_attr_pairs):
        for model_loader, attr in model_attr_pairs:
            try:
                model = model_loader()
                if model is None:
                    continue
                rows = (
                    model.query
                    .with_entities(getattr(model, attr))
                    .filter(model.user_id == user_id)
                    .distinct()
                    .all()
                )
                for (raw,) in rows:
                    if not raw:
                        continue
                    s4 = normalise_tax_year_to_s4_format(raw)
                    if s4 in _SUPPORTED_TAX_YEARS_S4_NEWEST_FIRST:
                        found.add(s4)
            except Exception as exc:  # pragma: no cover -- defensive
                logger.debug("available_years scan failed for %s.%s: %s",
                             model_loader, attr, exc)

    def _load_deduction_claim():
        try:
            from fiesta.deductions.models import DeductionClaim
            return DeductionClaim
        except Exception:
            return None

    def _load_rental_agreement():
        try:
            from fiesta.property.models import RentalAgreement
            return RentalAgreement
        except Exception:
            return None

    def _load_service_agreement():
        try:
            from fiesta.agreements.models import ServiceAgreement
            return ServiceAgreement
        except Exception:
            return None

    def _load_income_entry():
        try:
            from fiesta.earnings.models import IncomeEntry
            return IncomeEntry
        except Exception:
            return None

    _scan([
        (_load_deduction_claim, "tax_year"),
        (_load_rental_agreement, "tax_year"),
        (_load_service_agreement, "tax_year"),
        (_load_income_entry, "tax_year"),
    ])

    # Sort newest first by aligning to the supported-list order (which is
    # already newest first). Anything not in the supported list is excluded
    # above, so this loop is total.
    return [ty for ty in _SUPPORTED_TAX_YEARS_S4_NEWEST_FIRST if ty in found]


def _default_tax_year_s4() -> str:
    """Return the most-recent SUPPORTED SL tax year in S4 form ("YYYY-YY").

    Filters the fiscal-calendar current year through the engine's supported
    set. After 1 April 2026 the fiscal year ticks to 2026-27, but IRD has not
    gazetted 2026-27 brackets yet (and the engine enum doesn't have a Y26_27
    entry). Returning the un-supported year produces an engine_error on S12
    and the user sees "Tax engine unavailable" instead of their bill. Falling
    back to the most recent SUPPORTED year keeps S12 working through the
    bracket-publication lag.

    Also note: 30 November 2026 is the filing deadline for FY 2025-26, so the
    "year you're filing for" right now IS 2025-26, even though we're calendar-
    in FY 2026-27. The most-recent-supported semantics align with that.
    """
    try:
        from fiesta.paywall.models import current_sl_tax_year
        current_s4 = normalise_tax_year_to_s4_format(current_sl_tax_year())
        if current_s4 in _SUPPORTED_TAX_YEARS_S4_NEWEST_FIRST:
            return current_s4
        return _most_recent_supported_tax_year_s4()
    except Exception:  # pragma: no cover -- defensive fallback only
        return _DEFAULT_TAX_YEAR_S4_FALLBACK


def _active_tax_year_s4() -> str:
    """F6.1 FIX (Phase B Wave 1, 2026-05-26) — return the active tax year
    in S4 form, honoring the session override from BUG-B.

    Reads `session['active_tax_year']` first (set by the topbar selector
    via /api/fiesta/active-tax-year). The session value is in Y/Y short
    form (e.g. "2025/26") — we normalise to S4 ("2025-26") and verify
    it's in the engine's supported set before returning. Falls back to
    `_default_tax_year_s4()` (legacy calendar-derived default) if:
      - No session override is set
      - The override doesn't normalise cleanly
      - The override resolves to an unsupported year

    This is the helper S12 confirmation card, S14 walkthrough, and the
    /tax-bill index_redirect should call instead of `_default_tax_year_s4()`
    directly. Before this fix, the index_redirect used the calendar default
    while the topbar selector wrote to the session — so S12 and the
    walkthrough silently disagreed about which year was active.
    """
    if not _HAS_FLASK:
        return _default_tax_year_s4()
    try:
        override = session.get('active_tax_year')
    except Exception:
        override = None
    if override:
        try:
            override_s4 = normalise_tax_year_to_s4_format(override)
            if override_s4 in _SUPPORTED_TAX_YEARS_S4_NEWEST_FIRST:
                return override_s4
        except Exception:
            pass  # fall through to default
    return _default_tax_year_s4()


@bp.route("/", methods=["GET"])
@bp.route("", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S12", action="index_redirect")
def index_redirect():
    """Redirect /tax-bill -> /tax-bill/<active_ty>.

    F6.1 FIX (Phase B Wave 1, 2026-05-26): use `_active_tax_year_s4()`
    so the redirect honors the topbar's session-stored active year. Before
    this fix S12 confirmation + S14 walkthrough redirected to the
    calendar-default year even when the user had explicitly selected a
    different year via the topbar dropdown."""
    return redirect(url_for("fiesta_tax_bill.show_tax_bill",
                            tax_year=_active_tax_year_s4()))


@bp.route("/<tax_year>", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S12", action="show_tax_bill")
def show_tax_bill(tax_year: str):
    """Render the S12 outcome screen."""
    user_id = _current_user_id()
    if not user_id:
        abort(401)

    tax_year_s4 = normalise_tax_year_to_s4_format(tax_year)

    # If the requested year isn't supported by the engine (e.g. user typed
    # 2026-27 manually, or a stale link points there before IRD publishes
    # 26/27 brackets), redirect to the most recent supported year with a
    # flash message so they understand why. Without this, the page renders
    # the "Tax engine unavailable" error block and the user can't see any
    # bill at all.
    if tax_year_s4 not in _SUPPORTED_TAX_YEARS_S4_NEWEST_FIRST:
        try:
            flash(
                f"Tax brackets for {tax_year_s4} have not been gazetted by "
                f"IRD yet. Showing your most recent supported year instead.",
                "info",
            )
        except Exception:  # pragma: no cover
            pass
        return redirect(url_for(
            "fiesta_tax_bill.show_tax_bill",
            tax_year=_most_recent_supported_tax_year_s4(),
        ))

    report = compute_tax_bill(user_id, tax_year)

    # Finalize state is process-local.
    report.is_finalized = _is_finalized(user_id, tax_year_s4)

    # Run X6 gate -- DISPLAY_BILL action.
    gate = run_gate(report, action="display_bill")

    # Tier D4 C4 year-selector: list of years this user has data for (intersected
    # with engine-supported years), so /tax-bill can render a year switcher.
    available_years = _available_years_for_user(user_id)

    return render_template(
        "tax_bill/index.html",
        report=report,
        inputs=report.inputs,
        gate=gate,
        tax_year_s4=tax_year_s4,
        tax_year_display=report.tax_year_s5_format,
        available_years=available_years,
        selected_year=tax_year_s4,
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


def _v2_flag_enabled() -> bool:
    """Resolve the AUDIT_PDF_V2_ENABLED feature flag.

    Honours both `feature_flags.is_feature_enabled` (project convention) and
    direct env-var lookup as a fallback for environments where feature_flags
    is not importable. Default OFF.
    """
    try:
        from feature_flags import is_feature_enabled  # type: ignore
        if is_feature_enabled("AUDIT_PDF_V2_ENABLED"):
            return True
    except Exception:
        pass
    import os
    val = (os.environ.get("AUDIT_PDF_V2_ENABLED") or "").strip().lower()
    return val in ("1", "true", "yes", "on")


@bp.route("/<tax_year>/export", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S12", action="export_audit_pack")
def export_audit_pack(tax_year: str):
    """Generate + stream the audit-pack PDF.

    B14: optional v2 branch. Query param `?v=2` requests the v2 layout
    (per-claim evidence chain + IRA cite text + calculation methodology).
    v2 is gated by the AUDIT_PDF_V2_ENABLED feature flag; when the flag is
    OFF, `?v=2` silently falls back to v1 (no breaking change for the
    existing customer-facing S12 export button).
    """
    user_id = _current_user_id()
    if not user_id:
        abort(401)

    report = compute_tax_bill(user_id, tax_year)
    tax_year_s4 = normalise_tax_year_to_s4_format(tax_year)
    report.is_finalized = _is_finalized(user_id, tax_year_s4)

    # Decide which generator to use.
    requested_v2 = (request.args.get("v") or "").strip() == "2"
    v2_active = requested_v2 and _v2_flag_enabled()

    # Build PDF (lazy import: ReportLab is heavy).
    try:
        if v2_active:
            from .audit_pack_v2 import build_audit_pack_v2 as _builder
        else:
            from .audit_pack import build_audit_pack as _builder
    except Exception as exc:
        logger.exception("audit_pack import failed: %s", exc)
        return jsonify({
            "ok": False,
            "error": f"PDF generator unavailable: {exc}",
        }), 503

    try:
        pdf_bytes = _builder(report)
    except Exception as exc:
        logger.exception("audit_pack build failed (v2=%s): %s", v2_active, exc)
        return jsonify({
            "ok": False,
            "error": f"PDF generation failed: {exc}",
        }), 500

    from io import BytesIO
    suffix = "_v2" if v2_active else ""
    filename = f"FIESTA_AuditPack_{tax_year_s4}_user{user_id}{suffix}.pdf"
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@bp.route("/<tax_year>/compare", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S12", action="yoy_compare")
def yoy_compare(tax_year: str):
    """Tier D5 C3: side-by-side year-over-year comparison.

    Builds a comparison across every year the user has data for (intersected
    with engine-supported years). The `tax_year` URL parameter is the year
    the user currently has open; we use it only to decide which year to
    highlight as `selected_year` in the rendered template.
    """
    user_id = _current_user_id()
    if not user_id:
        abort(401)

    tax_year_s4 = normalise_tax_year_to_s4_format(tax_year)

    # available_years comes back newest-first; YoY wants oldest-first so the
    # delta arrows read left-to-right with time.
    available_newest_first = _available_years_for_user(user_id)
    available_oldest_first = list(reversed(available_newest_first))

    # Defensive import: keep this route healthy even if multi_year_view
    # raises during test scaffolding.
    try:
        from multi_year_view import compute_yoy_comparison
        comparison = compute_yoy_comparison(user_id, available_oldest_first)
        comparison_error = None
    except Exception as exc:
        logger.exception("yoy_compare failed: %s", exc)
        comparison = {
            "user_id": int(user_id),
            "years": available_oldest_first,
            "per_year": [],
            "deltas": [],
        }
        comparison_error = f"{type(exc).__name__}: {exc}"

    return render_template(
        "tax_bill/compare.html",
        user_id=user_id,
        comparison=comparison,
        comparison_error=comparison_error,
        available_years=available_newest_first,
        selected_year=tax_year_s4,
        tax_year_s4=tax_year_s4,
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
