"""fiesta.assets_liabilities.routes — Flask blueprint for the A&L declaration tracker.

Feature 9 D7-D9 (PLAN_X9_COMPLETION §5).

Routes:
  GET  /fie/al           → list_view   — list all entries + net worth summary
  GET  /fie/al/edit      → edit_view   — add/edit form (progressive disclosure)
  POST /fie/al/edit      → edit_save   — persist assets + liabilities in one txn
  GET  /fie/al/pdf       → download_pdf — IRD-ready PDF (inline application/pdf)
  POST /fie/al/push      → push_to_fa  — FA 5192455 push for Lanka.tax customers

Auth: all routes require @login_required.

Transaction pattern: both AssetEntry and LiabilityEntry rows for a submit
are added in a single db.session.add() + db.session.commit() so they are
atomic. On failure, db.session.rollback() is called before re-raising.

Layout: templates extend `layout_template` (set by app.py before_request via
g.layout_template → 'layout_fiesta.html' for FIESTA persona users).
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flask + extension imports — defensive pattern (mirrors deductions/routes.py)
# ---------------------------------------------------------------------------
try:
    from flask import (
        Blueprint,
        Flask,
        Response,
        flash,
        g,
        jsonify,
        redirect,
        render_template,
        request,
        send_file,
        session,
        url_for,
    )
    _HAS_FLASK = True
except ImportError:  # pragma: no cover
    _HAS_FLASK = False

    class Blueprint:  # type: ignore[no-redef]
        def __init__(self, *a, **kw): pass
        def route(self, *a, **kw):
            def d(fn): return fn
            return d

    class Flask:  # type: ignore[no-redef]
        pass

    def render_template(*a, **kw): return ""  # type: ignore
    def redirect(*a, **kw): return None  # type: ignore
    def url_for(*a, **kw): return "#"  # type: ignore
    def flash(*a, **kw): return None  # type: ignore
    def jsonify(*a, **kw): return {}  # type: ignore
    def send_file(*a, **kw): return None  # type: ignore
    def g(): pass  # type: ignore
    request = None  # type: ignore
    session = {}  # type: ignore

try:
    from flask_login import current_user, login_required
    _HAS_LOGIN = True
except ImportError:  # pragma: no cover
    _HAS_LOGIN = False
    current_user = None  # type: ignore

    def login_required(fn):  # type: ignore
        return fn

try:
    from io import BytesIO
    _HAS_IO = True
except ImportError:
    _HAS_IO = False

# ---------------------------------------------------------------------------
# Blueprint definition
# ---------------------------------------------------------------------------
al_bp = Blueprint(
    "fiesta_al",
    __name__,
    template_folder="../../templates",
    url_prefix="/fie/al",
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _current_user_id() -> int | None:
    """Return the authenticated user's integer ID or None."""
    if not _HAS_LOGIN or current_user is None:
        return None
    try:
        if not getattr(current_user, "is_authenticated", False):
            return None
        return int(getattr(current_user, "id", 0)) or None
    except (TypeError, ValueError):
        return None


def _current_tax_year() -> str:
    """Return active tax year in the A&L STORAGE form ('YYYY/YYYY').

    C5 FIX (Day-0, 2026-05-27): now routes through the canonical
    `fiesta.common.tax_year.active_tax_year(session)` resolver — same source
    the topbar dropdown / savings counter / /tax-bill / /admin/fiesta-states
    read from. Before this fix, /fie/al rendered "Tax Year: 2026/27" while
    the topbar dropdown selected "2025/26": two different years on the same
    page, same request, same user.

    Storage form remains "YYYY/YYYY" (matches models.AssetEntry.tax_year +
    LiabilityEntry.tax_year + every existing row in the DB). The template
    re-formats to short slash for display via active_ty.short_slash().
    """
    try:
        from fiesta.common.tax_year import active_tax_year as _ay
        return _ay(session if session else None).long_slash()
    except Exception:
        pass
    # Defensive fallback chain (only hit if the helper module is unimportable).
    try:
        ty = session.get("tax_year") if session else None
        if ty:
            return str(ty)
    except Exception:
        pass
    try:
        from fiesta.paywall.models import current_sl_tax_year as _csl
        # current_sl_tax_year() returns short slash "YYYY/YY"; expand to long.
        raw = _csl()
        if "/" in raw:
            a, b = raw.split("/", 1)
            if len(b) == 2:
                return f"{a}/{a[:2]}{b}"
            return raw
        return raw
    except Exception:
        return "2025/2026"


def _current_tax_year_display() -> str:
    """Return the active YA in the DISPLAY form ('YYYY/YY') — used by the
    A&L list/form templates' hero. Mirrors the topbar dropdown so the two
    widgets never disagree on the same page.
    """
    try:
        from fiesta.common.tax_year import active_tax_year as _ay
        return _ay(session if session else None).short_slash()
    except Exception:
        pass
    raw = _current_tax_year()
    # Compress "2025/2026" → "2025/26"
    if "/" in raw:
        a, b = raw.split("/", 1)
        if len(b) == 4:
            return f"{a}/{b[-2:]}"
    return raw


def _parse_cents(raw: str | None) -> int:
    """Parse a LKR string (e.g. '1234567.89') to integer cents. Returns 0 on error."""
    if not raw:
        return 0
    try:
        return int((Decimal(raw.replace(",", "").strip()) * 100).to_integral_value())
    except InvalidOperation:
        return 0


def _parse_date(raw: str | None) -> date | None:
    """Parse YYYY-MM-DD string to date. Returns None on error."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _user_display_name() -> str:
    """Return display name for the current user (for PDF header)."""
    if not _HAS_LOGIN or current_user is None:
        return "Unknown"
    for attr in ("full_name", "name", "username", "email"):
        val = getattr(current_user, attr, None)
        if val:
            return str(val)
    return "Unknown"


def _user_nic() -> str:
    """Return NIC for the current user if stored on the model."""
    if not _HAS_LOGIN or current_user is None:
        return ""
    return str(getattr(current_user, "nic", None) or "")


# ---------------------------------------------------------------------------
# Route helpers — group + total utilities
# ---------------------------------------------------------------------------

def _group_by_category(entries: list) -> dict[str, list]:
    """Group a list of model instances by their .category attribute."""
    grouped: dict[str, list] = {}
    for e in entries:
        cat = getattr(e, "category", "other")
        grouped.setdefault(cat, []).append(e)
    return grouped


def _sum_cents(entries: list, field: str) -> int:
    return sum(int(getattr(e, field, 0) or 0) for e in entries)


def _cents_to_lkr_decimal(cents: int) -> Decimal:
    return (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# D7 — GET /fie/al  (list view)
# ---------------------------------------------------------------------------
@al_bp.route("", methods=["GET"])
@al_bp.route("/", methods=["GET"])
@login_required
def list_view():
    """A&L declaration list — all entries grouped by category + net worth."""
    user_id = _current_user_id()
    tax_year = _current_tax_year()

    assets = []
    liabilities = []
    db_error = None

    try:
        from app import db
        from fiesta.assets_liabilities.models import AssetEntry, LiabilityEntry

        assets = (
            db.session.query(AssetEntry)
            .filter_by(user_id=user_id, tax_year=tax_year)
            .order_by(AssetEntry.category, AssetEntry.id)
            .all()
        )
        liabilities = (
            db.session.query(LiabilityEntry)
            .filter_by(user_id=user_id, tax_year=tax_year)
            .order_by(LiabilityEntry.category, LiabilityEntry.id)
            .all()
        )
    except Exception as exc:
        logger.error("al list_view: DB query failed: %s", exc)
        db_error = str(exc)

    total_assets_cents = _sum_cents(assets, "value_lkr_cents")
    total_liab_cents = _sum_cents(liabilities, "balance_lkr_cents")
    net_worth_cents = total_assets_cents - total_liab_cents

    # Check SF match for D9 push button visibility
    sf_match_present = False
    try:
        from fiesta.assets_liabilities.fa_push import _find_sf_customer_by_email
        user_email = getattr(current_user, "email", "") or ""
        if user_email:
            sf_match_present = _find_sf_customer_by_email(user_email) is not None
    except Exception:
        sf_match_present = False

    layout_template = getattr(g, "layout_template", "layout_fiesta.html")

    return render_template(
        "assets_liabilities/list.html",
        layout_template=layout_template,
        # `tax_year` (long-slash, storage form) preserved for any caller
        # that still reads it; `tax_year_display` (short-slash) is what the
        # template should show to the user so it matches the topbar.
        tax_year=tax_year,
        tax_year_display=_current_tax_year_display(),
        assets=assets,
        liabilities=liabilities,
        assets_by_cat=_group_by_category(assets),
        liabilities_by_cat=_group_by_category(liabilities),
        total_assets_lkr=_cents_to_lkr_decimal(total_assets_cents),
        total_liabilities_lkr=_cents_to_lkr_decimal(total_liab_cents),
        net_worth_lkr=_cents_to_lkr_decimal(net_worth_cents),
        net_worth_positive=net_worth_cents >= 0,
        sf_match_present=sf_match_present,
        db_error=db_error,
    )


# ---------------------------------------------------------------------------
# D7 — GET /fie/al/edit  (form view)
# ---------------------------------------------------------------------------
@al_bp.route("/edit", methods=["GET"])
@login_required
def edit_view():
    """Add / edit form for A&L entries — progressive disclosure with <details>."""
    user_id = _current_user_id()
    tax_year = _current_tax_year()

    edit_asset_id = request.args.get("asset_id", type=int)
    edit_liability_id = request.args.get("liability_id", type=int)
    prefill_asset = None
    prefill_liability = None

    try:
        from app import db
        from fiesta.assets_liabilities.models import (
            AssetEntry, LiabilityEntry,
            ASSET_CATEGORIES, LIABILITY_CATEGORIES,
        )
        if edit_asset_id:
            prefill_asset = (
                db.session.query(AssetEntry)
                .filter_by(id=edit_asset_id, user_id=user_id)
                .first()
            )
        if edit_liability_id:
            prefill_liability = (
                db.session.query(LiabilityEntry)
                .filter_by(id=edit_liability_id, user_id=user_id)
                .first()
            )
    except Exception as exc:
        logger.error("al edit_view: DB query failed: %s", exc)
        ASSET_CATEGORIES = ()
        LIABILITY_CATEGORIES = ()

    try:
        from fiesta.assets_liabilities.models import ASSET_CATEGORIES, LIABILITY_CATEGORIES
    except Exception:
        ASSET_CATEGORIES = ()
        LIABILITY_CATEGORIES = ()

    layout_template = getattr(g, "layout_template", "layout_fiesta.html")

    return render_template(
        "assets_liabilities/form.html",
        layout_template=layout_template,
        tax_year=tax_year,
        tax_year_display=_current_tax_year_display(),
        asset_categories=ASSET_CATEGORIES,
        liability_categories=LIABILITY_CATEGORIES,
        prefill_asset=prefill_asset,
        prefill_liability=prefill_liability,
    )


# ---------------------------------------------------------------------------
# D7 — POST /fie/al/edit  (save)
# ---------------------------------------------------------------------------
@al_bp.route("/edit", methods=["POST"])
@login_required
def edit_save():
    """Persist asset and/or liability entries in a single transaction."""
    user_id = _current_user_id()
    if not user_id:
        flash("Session expired. Please log in again.", "error")
        return redirect(url_for("fiesta_al.list_view"))

    tax_year = _current_tax_year()
    form = request.form

    try:
        from app import db
        from fiesta.assets_liabilities.models import AssetEntry, LiabilityEntry

        added = 0

        # ---- Asset block -----------------------------------------------
        asset_action = form.get("asset_action", "").strip()   # "add" or "edit"
        asset_id = form.get("asset_id", type=int)
        if asset_action in ("add", "edit") and form.get("asset_description", "").strip():
            if asset_action == "edit" and asset_id:
                row = (
                    db.session.query(AssetEntry)
                    .filter_by(id=asset_id, user_id=user_id)
                    .first()
                )
                if row is None:
                    flash("Asset entry not found.", "error")
                    return redirect(url_for("fiesta_al.edit_view"))
            else:
                row = AssetEntry(user_id=user_id, tax_year=tax_year)
                db.session.add(row)

            row.category = form.get("asset_category", "other").strip()
            row.description = form.get("asset_description", "").strip()[:512]
            row.value_lkr_cents = _parse_cents(form.get("asset_value_lkr"))
            row.acquired_date = _parse_date(form.get("asset_acquired_date"))
            row.evidence_ref = (form.get("asset_evidence_ref") or "").strip()[:256] or None
            added += 1

        # ---- Liability block --------------------------------------------
        liab_action = form.get("liability_action", "").strip()
        liab_id = form.get("liability_id", type=int)
        if liab_action in ("add", "edit") and form.get("liability_description", "").strip():
            if liab_action == "edit" and liab_id:
                lrow = (
                    db.session.query(LiabilityEntry)
                    .filter_by(id=liab_id, user_id=user_id)
                    .first()
                )
                if lrow is None:
                    flash("Liability entry not found.", "error")
                    return redirect(url_for("fiesta_al.edit_view"))
            else:
                lrow = LiabilityEntry(user_id=user_id, tax_year=tax_year)
                db.session.add(lrow)

            lrow.category = form.get("liability_category", "other").strip()
            lrow.description = form.get("liability_description", "").strip()[:512]
            lrow.lender = (form.get("liability_lender") or "").strip()[:256] or None
            lrow.balance_lkr_cents = _parse_cents(form.get("liability_balance_lkr"))
            lrow.original_amount_lkr_cents = _parse_cents(form.get("liability_original_amount_lkr")) or None
            lrow.due_date = _parse_date(form.get("liability_due_date"))
            added += 1

        if added == 0:
            flash("No entries submitted — please fill in at least one form section.", "warning")
            return redirect(url_for("fiesta_al.edit_view"))

        db.session.commit()
        flash(f"{added} entr{'y' if added == 1 else 'ies'} saved.", "success")

    except Exception as exc:
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        logger.error("al edit_save: DB error: %s", exc)
        flash("Failed to save entries — please try again.", "error")
        return redirect(url_for("fiesta_al.edit_view"))

    return redirect(url_for("fiesta_al.list_view"))


# ---------------------------------------------------------------------------
# D8 — GET /fie/al/pdf  (PDF download)
# ---------------------------------------------------------------------------
@al_bp.route("/pdf", methods=["GET"])
@login_required
def download_pdf():
    """Generate and return IRD-compliant A&L declaration PDF.

    Query params:
      tax_year : override (default: session tax year)
    """
    user_id = _current_user_id()
    tax_year = request.args.get("tax_year") or _current_tax_year()

    try:
        from app import db
        from fiesta.assets_liabilities.models import AssetEntry, LiabilityEntry
        from fiesta.assets_liabilities.pdf import generate_al_pdf

        assets = (
            db.session.query(AssetEntry)
            .filter_by(user_id=user_id, tax_year=tax_year)
            .order_by(AssetEntry.category, AssetEntry.id)
            .all()
        )
        liabilities = (
            db.session.query(LiabilityEntry)
            .filter_by(user_id=user_id, tax_year=tax_year)
            .order_by(LiabilityEntry.category, LiabilityEntry.id)
            .all()
        )

        pdf_bytes = generate_al_pdf(
            user_id=user_id,
            user_name=_user_display_name(),
            user_nic=_user_nic(),
            tax_year=tax_year,
            assets=assets,
            liabilities=liabilities,
        )

        safe_year = tax_year.replace("/", "-")
        filename = f"AL_Declaration_{safe_year}.pdf"
        return send_file(
            BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=False,
            download_name=filename,
        )

    except Exception as exc:
        logger.error("al download_pdf: failed: %s", exc)
        flash(f"PDF generation failed: {exc}", "error")
        return redirect(url_for("fiesta_al.list_view"))


# ---------------------------------------------------------------------------
# D9 — POST /fie/al/push  (FA 5192455 push)
# ---------------------------------------------------------------------------
@al_bp.route("/push", methods=["POST"])
@login_required
def push_to_fa():
    """POST A&L data to FA 5192455 for Lanka.tax-linked customers.

    On success, writes fa_submission_id to all AssetEntry rows for the
    current user + tax_year. Returns JSON for the HTMX/fetch button.
    """
    user_id = _current_user_id()
    tax_year = _current_tax_year()

    try:
        from app import db
        from fiesta.assets_liabilities.models import AssetEntry, LiabilityEntry
        from fiesta.assets_liabilities.fa_push import push_to_fa_5192455, build_al_data

        assets = (
            db.session.query(AssetEntry)
            .filter_by(user_id=user_id, tax_year=tax_year)
            .all()
        )
        liabilities = (
            db.session.query(LiabilityEntry)
            .filter_by(user_id=user_id, tax_year=tax_year)
            .all()
        )

        al_data = build_al_data(
            user_name=_user_display_name(),
            user_nic=_user_nic(),
            tax_year=tax_year,
            assets=assets,
            liabilities=liabilities,
        )

        result = push_to_fa_5192455(user=current_user, al_data=al_data)

        if result.get("success") and result.get("submission_id"):
            # Persist fa_submission_id on all AssetEntry rows for this user+year
            sub_id = result["submission_id"]
            for a in assets:
                a.fa_submission_id = sub_id
            db.session.commit()
            logger.info(
                "al push_to_fa: FA 5192455 pushed; submission_id=%s user=%s", sub_id, user_id
            )
            if request.is_json or request.headers.get("HX-Request"):
                return jsonify({"status": "ok", "submission_id": sub_id})
            flash(f"Successfully pushed to Lanka.tax filing system (ref: {sub_id}).", "success")
        else:
            reason = result.get("reason", "unknown")
            if request.is_json or request.headers.get("HX-Request"):
                return jsonify({"status": "skipped" if result.get("skipped") else "error",
                                "reason": reason})
            if result.get("skipped"):
                flash(f"Push skipped: {reason}", "info")
            else:
                flash(f"Push failed: {reason}", "error")

    except Exception as exc:
        logger.error("al push_to_fa: unexpected error: %s", exc)
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        if request.is_json or request.headers.get("HX-Request"):
            return jsonify({"status": "error", "reason": str(exc)}), 500
        flash(f"Push to Lanka.tax failed: {exc}", "error")

    return redirect(url_for("fiesta_al.list_view"))


# ---------------------------------------------------------------------------
# Registration helper (called from main.py — same pattern as deductions)
# ---------------------------------------------------------------------------
def register_routes(app: Flask) -> None:
    """Register the A&L blueprint with the Flask app."""
    if "fiesta_al" in app.blueprints:
        logger.debug("fiesta_al blueprint already registered — skipping.")
        return
    app.register_blueprint(al_bp)
    logger.info("FIESTA Feature 9 A&L blueprint registered at /fie/al")
