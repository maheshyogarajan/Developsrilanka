"""fiesta.deductions.routes — Flask blueprint for S5 "Reduce your tax".

Routes:
    GET  /reduce-tax                    — main 10-card screen
    POST /reduce-tax/claim/<cat>        — claim a category (toggle on)
    POST /reduce-tax/unclaim/<cat>      — unclaim (toggle off)
    GET  /reduce-tax/estimate           — JSON: total deduction + saving
    GET  /reduce-tax/legal/<cat>        — legal-basis modal content (IRA section)

CSRF: Flask-WTF expects a token. The base layout exposes csrf_token() —
the JS posts use the meta tag from layout.html.

Auth: All routes require @login_required.

Persistence: DeductionClaim rows in fiesta_deduction_claim table.

Tax year: read from session['tax_year'] (set by S0 triage), default to
the active tax year from catalog.yaml.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

# Defensive imports — Flask may not be installed in pure-unit-test environments
# (e.g. CI machines that only run the deductions logic tests). When Flask is
# missing we install no-op stand-ins so the module still imports.
try:
    from flask import (
        Blueprint, render_template, request, jsonify, redirect, url_for,
        flash, session,
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
    def jsonify(*a, **kw): return {"_stub": True}  # type: ignore
    def redirect(*a, **kw): return None  # type: ignore
    def url_for(*a, **kw): return "#"  # type: ignore
    def flash(*a, **kw): return None  # type: ignore

    class _SessionStub(dict):
        def get(self, k, default=None): return None
    session = _SessionStub()  # type: ignore
    request = None  # type: ignore

try:
    from flask_login import login_required, current_user
    _HAS_LOGIN = True
except ImportError:  # pragma: no cover
    _HAS_LOGIN = False

    def login_required(fn):  # type: ignore
        return fn
    current_user = None  # type: ignore

try:
    from app import db
    _HAS_DB = True
except Exception:  # pragma: no cover
    _HAS_DB = False
    db = None  # type: ignore

from .catalog_loader import load_catalog, get_category, get_caps
from .estimate import (
    estimate_saving, marginal_rate_for_income, per_card_saving_range,
)
from .personalize import recommended_deductions

# ---------------------------------------------------------------------------
# Blueprint.
# ---------------------------------------------------------------------------
deductions_bp = Blueprint(
    "fiesta_deductions",
    __name__,
    url_prefix="/reduce-tax",
    template_folder="../../templates",
)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _current_user_id() -> int | None:
    if not _HAS_LOGIN or current_user is None:
        return None
    try:
        if not getattr(current_user, "is_authenticated", False):
            return None
        return int(getattr(current_user, "id", 0)) or None
    except (TypeError, ValueError):
        return None


def _resolve_tax_year() -> str:
    """Tax year from session, fall back to catalog default."""
    try:
        ty = session.get("tax_year")
        if ty:
            return str(ty)
    except RuntimeError:
        # No session (unit-test call path)
        pass
    return load_catalog().get("tax_year", "2025/2026")


def _resolve_income_lkr() -> Decimal:
    """Customer income estimate — read from session (set by S0 / S2)."""
    try:
        v = session.get("estimated_income_lkr")
        if v is not None:
            return Decimal(str(v))
    except (RuntimeError, InvalidOperation):
        pass
    # No income known yet — show 0 (the cards still render with neutral hints).
    return Decimal("0")


def _user_profile() -> dict[str, Any]:
    """Best-effort profile dict from session — for personalize.py."""
    try:
        return dict(session.get("fiesta_profile") or {})
    except RuntimeError:
        return {}


def _user_income_summary() -> dict[str, Any]:
    try:
        return dict(session.get("fiesta_income_summary") or {})
    except RuntimeError:
        return {}


def _load_user_claims(user_id: int, tax_year: str) -> dict[str, Any]:
    """Read DeductionClaim rows -> {category_id: claim_dict}.

    Returns an empty dict if the DB is unavailable or there are no claims.
    """
    if not _HAS_DB or user_id is None:
        return {}
    try:
        from .models import DeductionClaim
        rows = (
            DeductionClaim.query
            .filter_by(user_id=user_id, tax_year=tax_year)
            .all()
        )
        return {r.category_id: r.to_dict() for r in rows}
    except Exception:  # pragma: no cover
        logger.exception("Failed to load DeductionClaim rows")
        return {}


# ---------------------------------------------------------------------------
# GET /reduce-tax — main screen.
# ---------------------------------------------------------------------------
@deductions_bp.route("/", methods=["GET"])
@login_required
def index():
    """Render the 10-card S5 screen."""
    user_id = _current_user_id()
    tax_year = _resolve_tax_year()
    income = _resolve_income_lkr()

    # Personalized ordering
    profile = _user_profile()
    income_summary = _user_income_summary()
    categories = recommended_deductions(profile, income_summary)

    # User's current claims (so we know which cards are toggled on)
    current_claims = _load_user_claims(user_id, tax_year) if user_id else {}

    # Per-card saving range hint (at customer's marginal rate)
    for cat in categories:
        rng = per_card_saving_range(income, cat.get("typical_lkr_range") or [0, 0])
        cat["_saving_range"] = rng
        # Pre-fill claim state for the template
        claim = current_claims.get(cat["id"])
        cat["_is_claimed"] = bool(claim and claim.get("claimed"))
        cat["_estimated_lkr"] = (
            Decimal(claim["estimated_lkr"]) if claim and claim.get("estimated_lkr") else None
        )

    # Running tally for the top of the page
    claim_dicts = [c for c in current_claims.values() if c.get("claimed")]
    summary = estimate_saving(claim_dicts, income)

    return render_template(
        "deductions/index.html",
        categories=categories,
        summary=summary,
        income=income,
        tax_year=tax_year,
        marginal_rate=marginal_rate_for_income(income),
    )


# ---------------------------------------------------------------------------
# POST /reduce-tax/claim/<category_id>
# ---------------------------------------------------------------------------
@deductions_bp.route("/claim/<category_id>", methods=["POST"])
@login_required
def claim(category_id: str):
    """Claim a deduction category. Body (form or JSON): {estimated_lkr: number}."""
    if not _HAS_DB:
        return jsonify({"ok": False, "error": "DB unavailable"}), 503

    cat = get_category(category_id)
    if not cat:
        return jsonify({"ok": False, "error": f"Unknown category: {category_id}"}), 404

    user_id = _current_user_id()
    if not user_id:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401

    tax_year = _resolve_tax_year()

    # Pull estimated_lkr from JSON OR form. Optional — customer may tick
    # without entering an amount yet.
    payload = request.get_json(silent=True) or {}
    estimated_raw = payload.get("estimated_lkr") or request.form.get("estimated_lkr")
    estimated_lkr: Decimal | None = None
    if estimated_raw not in (None, ""):
        try:
            estimated_lkr = Decimal(str(estimated_raw))
            if estimated_lkr < 0:
                return jsonify({"ok": False, "error": "estimated_lkr must be >= 0"}), 400
        except InvalidOperation:
            return jsonify({"ok": False, "error": "estimated_lkr must be numeric"}), 400

    notes = (payload.get("notes") or request.form.get("notes") or "")[:1024] or None

    from .models import DeductionClaim
    try:
        claim_row = (
            DeductionClaim.query
            .filter_by(user_id=user_id, tax_year=tax_year, category_id=category_id)
            .first()
        )
        if claim_row is None:
            claim_row = DeductionClaim(
                user_id=user_id,
                tax_year=tax_year,
                category_id=category_id,
                claimed=True,
                notes=notes,
            )
            if estimated_lkr is not None:
                claim_row.estimated_lkr = estimated_lkr
            db.session.add(claim_row)
        else:
            claim_row.claimed = True
            claim_row.updated_at = datetime.utcnow()
            if estimated_lkr is not None:
                claim_row.estimated_lkr = estimated_lkr
            if notes is not None:
                claim_row.notes = notes
        db.session.commit()
        # Sprint 3 (perf): bust the per-user hub cache so the topbar's
        # "you're saving" pill picks up the new claim on the next render.
        try:
            from app import _invalidate_hub_cache
            _invalidate_hub_cache(user_id)
        except Exception:
            pass
        return jsonify({"ok": True, "claim": claim_row.to_dict()})
    except Exception as exc:
        db.session.rollback()
        logger.exception("Claim insert/update failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# POST /reduce-tax/unclaim/<category_id>
# ---------------------------------------------------------------------------
@deductions_bp.route("/unclaim/<category_id>", methods=["POST"])
@login_required
def unclaim(category_id: str):
    if not _HAS_DB:
        return jsonify({"ok": False, "error": "DB unavailable"}), 503

    user_id = _current_user_id()
    if not user_id:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401

    tax_year = _resolve_tax_year()
    from .models import DeductionClaim
    try:
        claim_row = (
            DeductionClaim.query
            .filter_by(user_id=user_id, tax_year=tax_year, category_id=category_id)
            .first()
        )
        if claim_row is None:
            return jsonify({"ok": True, "noop": True})
        claim_row.claimed = False
        claim_row.updated_at = datetime.utcnow()
        db.session.commit()
        # Sprint 3 (perf): bust the per-user hub cache so the topbar updates.
        try:
            from app import _invalidate_hub_cache
            _invalidate_hub_cache(user_id)
        except Exception:
            pass
        return jsonify({"ok": True, "claim": claim_row.to_dict()})
    except Exception as exc:
        db.session.rollback()
        logger.exception("Unclaim failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /reduce-tax/estimate — live JSON for the running tally
# ---------------------------------------------------------------------------
@deductions_bp.route("/estimate", methods=["GET"])
@login_required
def estimate():
    user_id = _current_user_id()
    tax_year = _resolve_tax_year()
    income = _resolve_income_lkr()
    if not user_id:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    claims = list(_load_user_claims(user_id, tax_year).values())
    result = estimate_saving([c for c in claims if c.get("claimed")], income)
    # Convert Decimals -> strings for JSON
    return jsonify({
        "ok": True,
        "tax_year": tax_year,
        "income_lkr": str(result["income_lkr"]),
        "marginal_rate": str(result["marginal_rate"]),
        "total_deduction_lkr": str(result["total_deduction_lkr"]),
        "estimated_saving_lkr": str(result["estimated_saving_lkr"]),
        "tax_before_lkr": str(result["tax_before_lkr"]),
        "tax_after_lkr": str(result["tax_after_lkr"]),
        "deduction_cap_applied": result["deduction_cap_applied"],
        "breakdown": [
            {
                "category_id": b["category_id"],
                "claimed_lkr": str(b["claimed_lkr"]),
                "after_cap_lkr": str(b["after_cap_lkr"]),
                "saving_lkr": str(b["saving_lkr"]),
                "cap_note": b["cap_note"],
            }
            for b in result["breakdown"]
        ],
    })


# ---------------------------------------------------------------------------
# GET /reduce-tax/legal/<category_id> — IRA section modal content
# ---------------------------------------------------------------------------
@deductions_bp.route("/legal/<category_id>", methods=["GET"])
@login_required
def legal(category_id: str):
    cat = get_category(category_id)
    if not cat:
        return jsonify({"ok": False, "error": f"Unknown category: {category_id}"}), 404
    return jsonify({
        "ok": True,
        "category_id": cat["id"],
        "name": cat["name"],
        "ira_section": cat.get("ira_section"),
        "ira_section_long": cat.get("ira_section_long"),
        "plain_english_description": cat.get("plain_english_description"),
        "eligibility_criteria": cat.get("eligibility_criteria", []),
        "evidence_required": cat.get("evidence_required", []),
    })


# ---------------------------------------------------------------------------
# Registration helper (called from main.py).
# ---------------------------------------------------------------------------
def register_blueprint(app):
    """Register the deductions blueprint with the Flask app."""
    app.register_blueprint(deductions_bp)
    logger.info("FIESTA S5 deductions blueprint registered at /reduce-tax")
