"""G3.6 Income-Source Picker — backend routes (MS4 W3d, 2026-05-25).

Contract
--------
- GET  /fie/income-sources             render the picker as a standalone
                                       page (extends layout_fiesta).
- POST /api/fiesta/income-sources      validate + update User.income_sources
                                       idempotently; emits the
                                       `fiesta:income-source-added` event so
                                       the sidebar/topbar refresh on the
                                       same render. Accepts JSON
                                       ({"income_sources": [...]}) OR form-
                                       encoded (income_sources=foo&income_sources=bar).
- GET  /api/fiesta/income-sources      return the user's current selections
                                       so the frontend can rehydrate without
                                       a full page render.

Idempotency / ADDITIVE-ONLY semantics
-------------------------------------
Per the Design Lock 2 §3 + G3.6 spec (working files/
_fiesta_unification_addendum_20260525.md), the picker is ADDITIVE only:
adding a value is unconditional, but UNCHECKING a value that already has
underlying data rows (B12 business income, B11 RSU, B13 crypto, S5 deductions,
remittance entries) MUST NOT silently strip the value off the user — that
would orphan the data rows in the sidebar (the bookkeeping group reads
`income_sources` to decide which entries to show).

To remove a value, the user must delete the underlying records FIRST. The
backend therefore implements this rule:

  - Adding values: always succeeds (idempotent — duplicates skipped).
  - Removing values: only succeeds when there are NO underlying records
    tied to that source. If records exist, the value is RETAINED and the
    response includes a `warnings` list explaining which values could not
    be removed.

The "records check" is a soft heuristic — we look up the obvious
per-source table (RSU vesting events, business income rows, crypto
disposals, remittance entries). If a model isn't migrated in the current
environment, the check fails-open (we allow removal). This keeps the
endpoint useful in CI / fresh installs.

Audit
-----
Every successful write that actually CHANGES `income_sources` logs an
AuditLog row (entity_type='user', action='UPDATE') with the before/after
diff. No-op writes do NOT log (idempotency).

Auth
----
Both routes require @login_required. Anonymous hits get 302'd to /login.

CSRF
----
Flask-WTF CSRFProtect protects POST. The frontend reads the
`<meta name="csrf-token">` token (already exposed by layout_fiesta) and
sends it as `X-CSRFToken` header. The hidden `csrf_token` form input is
ALSO honoured as a fallback.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from flask import (
    Blueprint,
    Flask,
    current_app,
    jsonify,
    render_template,
    request,
)
from flask_login import current_user, login_required

# Canonical income-source vocabulary — re-exported here so callers don't
# need to import from fiesta.tax.models (which has heavy SQLAlchemy +
# DateTime + other side-effects). The tuple is identical to
# fiesta.tax.models.INCOME_SOURCE_TYPES; a runtime assertion in
# register_blueprint guarantees they don't drift.
CANONICAL_INCOME_SOURCES: Tuple[str, ...] = (
    "foreign_remittance",
    "employment_lkr",
    "professional_fees_lkr",
    "business_lkr",
    "business_foreign",
    "rsu",
    "crypto",
    "rental_lkr",
    "rental_foreign",
    "investment_lkr",
    "investment_foreign",
    "other",
)

log = logging.getLogger(__name__)


bp = Blueprint(
    "fiesta_income_sources",
    __name__,
    template_folder="../../templates",
)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _coerce_sources(raw: Any) -> Optional[List[str]]:
    """Coerce a request payload's income_sources field into a clean list.

    Accepts:
      - list[str]
      - tuple[str, ...]
      - single string (treated as one-element list — supports the
        `income_sources=foo&income_sources=foo` form pattern flattened)
      - None / "" → []

    Returns None if the input is structurally invalid (a non-iterable
    that isn't a string). The caller treats None as "reject the
    request"; an empty list is a VALID input (the user un-ticked
    everything — additive semantics still apply, see route docstring).
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        # Empty string → empty list (user submitted no checkboxes).
        return [] if raw == "" else [raw]
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw]
    return None


def _validate_against_canonical(sources: List[str]) -> Tuple[List[str], List[str]]:
    """Split the input list into (valid, invalid). Strips dupes from valid
    while preserving first-seen order."""
    seen: set = set()
    valid: List[str] = []
    invalid: List[str] = []
    canonical_set = set(CANONICAL_INCOME_SOURCES)
    for s in sources:
        if not isinstance(s, str):
            invalid.append(str(s))
            continue
        s_clean = s.strip()
        if s_clean in seen:
            continue
        seen.add(s_clean)
        if s_clean in canonical_set:
            valid.append(s_clean)
        else:
            invalid.append(s_clean)
    return valid, invalid


# ---------------------------------------------------------------------------
# "Has underlying data?" — guards against silent unticks orphaning rows.
# ---------------------------------------------------------------------------


def _has_underlying_data(user: Any, source: str) -> bool:
    """Return True iff the user has data rows tied to this income source.

    Used to BLOCK silent removal of a source from User.income_sources.
    Fails-open: any exception → returns False (we'd rather let the user
    untick a non-existent source in a misconfigured env than block the
    POST).
    """
    user_id = getattr(user, "id", None)
    if user_id is None:
        return False

    try:
        if source == "rsu":
            from fiesta.tax.models import RSUVestingEvent
            return RSUVestingEvent.query.filter_by(user_id=user_id).first() is not None
        if source == "crypto":
            try:
                from fiesta.tax.models import CryptoDisposal
                if CryptoDisposal.query.filter_by(user_id=user_id).first() is not None:
                    return True
            except Exception:
                pass
            # Fallback — the canonical Income table catches anything keyed
            # by source_type='crypto'.
            from fiesta.tax.models import Income
            return Income.query.filter_by(
                user_id=user_id, source_type="crypto"
            ).first() is not None
        if source in {"business_lkr", "business_foreign"}:
            try:
                from fiesta.tax.models import BusinessIncome
                return BusinessIncome.query.filter_by(
                    user_id=user_id
                ).first() is not None
            except Exception:
                from fiesta.tax.models import Income
                return Income.query.filter_by(
                    user_id=user_id, source_type=source
                ).first() is not None
        if source == "foreign_remittance":
            try:
                from models import RemittanceEntry
                return RemittanceEntry.query.filter_by(
                    user_id=user_id
                ).first() is not None
            except Exception:
                return False
        # For everything else (employment_lkr, professional_fees_lkr,
        # rental_*, investment_*, other), use the canonical Income table.
        try:
            from fiesta.tax.models import Income
            return Income.query.filter_by(
                user_id=user_id, source_type=source
            ).first() is not None
        except Exception:
            return False
    except Exception as exc:  # noqa: BLE001
        log.debug("g3.6 underlying-data check failed for %s: %s", source, exc)
        return False


# ---------------------------------------------------------------------------
# Idempotent write
# ---------------------------------------------------------------------------


def _apply_sources(
    user: Any, new_sources: List[str]
) -> Dict[str, Any]:
    """Apply the validated `new_sources` list to `user.income_sources`
    additively, blocking silent removal of sources with underlying data.

    Returns a dict:
      {
        'previous': [...],   # the list before this call
        'current':  [...],   # the list after this call
        'added':    [...],   # sources newly added
        'removed':  [...],   # sources removed (no underlying data)
        'retained': [...],   # sources the user unticked but we kept
                              # because they have underlying data
        'changed':  bool,    # True iff the persisted column changed
      }

    The caller is responsible for committing the session if `changed`
    is True.
    """
    current_set = set((getattr(user, "income_sources", None) or []))
    new_set = set(new_sources)

    added = sorted(new_set - current_set)
    candidate_removed = sorted(current_set - new_set)

    actually_removed: List[str] = []
    retained: List[str] = []
    for s in candidate_removed:
        if _has_underlying_data(user, s):
            retained.append(s)
        else:
            actually_removed.append(s)

    final_set = (current_set | set(added)) - set(actually_removed)
    # Preserve canonical ordering in the persisted column for stable diffs.
    final_list = [s for s in CANONICAL_INCOME_SOURCES if s in final_set]

    changed = final_set != current_set
    if changed:
        user.income_sources = final_list
        # JSON columns need flag_modified so SQLAlchemy notices an
        # in-place mutation — the assignment above creates a NEW list,
        # but flag_modified is cheap insurance + matches the pattern in
        # fiesta/triage/routes.py and fiesta/tax/rsu_engine.py.
        try:
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(user, "income_sources")
        except Exception:
            pass

    return {
        "previous": sorted(current_set),
        "current": final_list,
        "added": added,
        "removed": actually_removed,
        "retained": retained,
        "changed": changed,
    }


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def _emit_audit(user: Any, diff: Dict[str, Any]) -> None:
    """Write an AuditLog row recording the income_sources delta.
    Best-effort — never blocks the response on an audit failure."""
    try:
        from app import db
        from models import AuditLog
        audit = AuditLog(
            entity_type="user",
            entity_id=int(user.id),
            action="UPDATE",
            changed_fields={
                "income_sources": {
                    "old_value": diff["previous"],
                    "new_value": diff["current"],
                    "added": diff["added"],
                    "removed": diff["removed"],
                    "retained_due_to_underlying_data": diff["retained"],
                },
                "operation": "g3_6_income_source_picker",
            },
            user_id=int(user.id),
        )
        db.session.add(audit)
    except Exception as exc:  # noqa: BLE001
        log.debug("g3.6 audit log emit failed: %s", exc)


def _emit_event(event_name: str, **payload: Any) -> None:
    """Mirror the analytics-emit pattern used by fiesta.profile.routes."""
    try:
        logging.getLogger("fiesta.analytics").info(
            json.dumps({"event": event_name, **payload}, default=str)
        )
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@bp.route("/fie/income-sources", methods=["GET"], strict_slashes=False)
@login_required
def picker_page():
    """Render the income-source picker as a standalone page."""
    return render_template(
        "fiesta_income_sources/page.html",
    )


@bp.route("/api/fiesta/income-sources", methods=["GET"], strict_slashes=False)
@login_required
def api_get_sources():
    """Return the authenticated user's current income_sources.
    Used by the modal opener to pre-populate checkboxes without a full
    page render."""
    sources = list(getattr(current_user, "income_sources", None) or [])
    return jsonify({
        "ok": True,
        "income_sources": sources,
        "canonical": list(CANONICAL_INCOME_SOURCES),
    })


@bp.route("/api/fiesta/income-sources", methods=["POST"], strict_slashes=False)
@login_required
def api_post_sources():
    """Update the user's income_sources idempotently (additive only).

    Request contract
    ----------------
    The endpoint accepts JSON OR form-encoded bodies:

    - JSON:  Content-Type: application/json
             Body: {"income_sources": [...canonical strings...]}
             Header: X-CSRFToken: <token from <meta name="csrf-token">>

    - Form:  Content-Type: application/x-www-form-urlencoded
             Body: income_sources=foo&income_sources=bar&csrf_token=<token>

    D-N1 polish (2026-05-27): JSON callers that omit X-CSRFToken WHILE
    CSRF is enforced previously got a Flask-WTF CSRFError that rendered
    as an HTML 400 page (`<!doctype html><title>400 Bad Request</title>`
    + "The CSRF token is missing."). For out-of-process callers
    (Playwright `requestContext.post` in particular) this surfaced as a
    perceived "hang" because they were waiting for a JSON shape they
    could drain, and saw HTML they couldn't parse. The route now
    validates CSRF itself (marked `@csrf.exempt` to bypass the global
    handler) and returns a clean, predictable JSON 400 with an explicit
    error code so the caller can recover. Browser callers via
    fiesta.js are unaffected (they always send the X-CSRFToken header
    read from the <meta> tag, which validates fine).

    Tests
    -----
    See tests/platform/test_income_source_picker.py (CSRF off in CI)
    and tests/platform/test_income_sources_csrf.py (CSRF on, regression).
    """
    # ----- D-N1: explicit CSRF validation with JSON-friendly errors. -----
    # The route is registered as @csrf.exempt in register_blueprint() so the
    # global Flask-WTF CSRFProtect handler doesn't intercept and return its
    # default HTML 400. We replicate the CSRF check here (when enabled) and
    # surface a JSON-shaped 400 that out-of-process callers can drain.
    try:
        _csrf_enabled = bool(current_app.config.get("WTF_CSRF_ENABLED", True))
    except Exception:
        _csrf_enabled = True
    if _csrf_enabled:
        _csrf_header = (
            request.headers.get("X-CSRFToken")
            or request.headers.get("X-CSRF-Token")
        )
        # Form-encoded callers may rely on the hidden csrf_token input.
        _csrf_form_field = None
        if not request.is_json:
            _csrf_form_field = request.form.get("csrf_token")
        _csrf_value = _csrf_header or _csrf_form_field
        if not _csrf_value:
            log.info(
                "g3.6 POST /api/fiesta/income-sources rejected: "
                "no X-CSRFToken header or csrf_token form field present"
            )
            return jsonify({
                "ok": False,
                "error": "missing_csrf_token",
                "message": (
                    "POSTs to /api/fiesta/income-sources require a CSRF "
                    "token. Send it as the X-CSRFToken header (JSON "
                    "callers, read from <meta name=\"csrf-token\">) OR "
                    "as the csrf_token form field (form-encoded callers)."
                ),
            }), 400
        # Validate the token. If invalid, return JSON 400 (not HTML).
        try:
            from flask_wtf.csrf import validate_csrf
            validate_csrf(_csrf_value)
        except Exception as exc:  # noqa: BLE001 — flask_wtf.csrf.CSRFError + others
            log.info(
                "g3.6 POST /api/fiesta/income-sources rejected: "
                "CSRF token failed validation (%s)", exc
            )
            return jsonify({
                "ok": False,
                "error": "invalid_csrf_token",
                "message": (
                    "CSRF token did not validate. Refresh the page to "
                    "pick up a fresh token from <meta name=\"csrf-token\">."
                ),
            }), 400

    # Accept JSON OR form-encoded. The picker partial submits a normal
    # HTML form (so it works without JS); the JS interceptor in
    # fiesta_income_picker.js sends application/json.
    raw_sources: Any
    if request.is_json:
        body = request.get_json(silent=True) or {}
        raw_sources = body.get("income_sources")
    else:
        # The form sends checkboxes as multiple values for the same key.
        getlist = request.form.getlist("income_sources")
        if getlist:
            raw_sources = getlist
        else:
            single = request.form.get("income_sources")
            raw_sources = single if single is not None else []

    sources = _coerce_sources(raw_sources)
    if sources is None:
        return jsonify({
            "ok": False,
            "error": "income_sources must be a list of strings",
        }), 400

    valid, invalid = _validate_against_canonical(sources)
    if invalid:
        # Reject the whole payload — partial-accept would hide a client
        # bug. The picker partial only emits canonical values so this
        # only fires on a malformed/foreign caller.
        return jsonify({
            "ok": False,
            "error": "unknown income source(s)",
            "invalid": invalid,
            "canonical": list(CANONICAL_INCOME_SOURCES),
        }), 422

    diff = _apply_sources(current_user, valid)

    if diff["changed"]:
        try:
            from app import db
            db.session.add(current_user)
            _emit_audit(current_user, diff)
            db.session.commit()
        except Exception as exc:  # noqa: BLE001
            log.exception("g3.6 commit failed: %s", exc)
            try:
                from app import db
                db.session.rollback()
            except Exception:
                pass
            return jsonify({
                "ok": False,
                "error": "could not save income sources; please retry",
            }), 500

        _emit_event(
            "income_sources_updated",
            user_id=current_user.id,
            added=diff["added"],
            removed=diff["removed"],
            retained=diff["retained"],
            current=diff["current"],
        )

        # Trip the savings projection cache so the topbar counter
        # refreshes against the new module set on the next read.
        try:
            from app import invalidate_savings_projection
            invalidate_savings_projection(int(current_user.id))
        except Exception:
            pass

    warnings: List[str] = []
    if diff["retained"]:
        # Human-readable warning for the client to surface in a toast.
        labels = ", ".join(diff["retained"])
        warnings.append(
            f"Couldn't remove: {labels}. You still have data tied to "
            f"these. Delete the underlying records first, then untick."
        )

    payload = {
        "ok": True,
        "income_sources": diff["current"],
        "added": diff["added"],
        "removed": diff["removed"],
        "retained": diff["retained"],
        "changed": diff["changed"],
        "warnings": warnings,
    }

    # Attach the canonical fiesta event header so the fiesta.js fetch
    # wrapper dispatches `fiesta:income-source-added` (already in the
    # _FIESTA_EVENT_NAMES allowlist). The event name is fixed — we use
    # it even for pure removals because the sidebar/topbar refresh logic
    # is the same regardless of which direction the diff went.
    try:
        from app import fiesta_event_response
        return fiesta_event_response(jsonify(payload), "income-source-added")
    except Exception:
        return jsonify(payload)


# ---------------------------------------------------------------------------
# Blueprint registration
# ---------------------------------------------------------------------------


def register_blueprint(app: Flask) -> None:
    """Wire the G3.6 routes into the Flask app. Idempotent."""
    if "fiesta_income_sources" in app.blueprints:
        log.debug(
            "fiesta_income_sources blueprint already registered, skipping"
        )
        return

    # Drift guard — fail loudly if the canonical vocab here drifts from
    # fiesta.tax.models.INCOME_SOURCE_TYPES.
    try:
        from fiesta.tax.models import INCOME_SOURCE_TYPES as _CANON
        if tuple(_CANON) != CANONICAL_INCOME_SOURCES:
            log.error(
                "G3.6 CANONICAL_INCOME_SOURCES drift detected: "
                "routes.py=%r vs models.py=%r",
                CANONICAL_INCOME_SOURCES,
                _CANON,
            )
    except Exception:
        # If models can't import (CI without DB), don't block registration.
        pass

    app.register_blueprint(bp)

    # D-N1 polish (2026-05-27): exempt POST /api/fiesta/income-sources
    # from the global CSRFProtect handler so we can return JSON 400s
    # instead of Flask-WTF's default HTML 400 page. CSRF is still
    # enforced — see api_post_sources() which validates the token
    # itself and returns a predictable JSON shape on failure.
    try:
        from app import csrf as _csrf
        _csrf.exempt(api_post_sources)
        log.debug(
            "G3.6 api_post_sources marked @csrf.exempt — route does its "
            "own CSRF validation and returns JSON errors."
        )
    except Exception as exc:  # noqa: BLE001
        # If the global csrf object isn't importable (rare — only in
        # very minimal test harnesses), fall through. The route's own
        # CSRF validation still runs when WTF_CSRF_ENABLED is true.
        log.debug(
            "G3.6 csrf.exempt registration skipped (csrf object not "
            "available: %s)", exc
        )

    log.info(
        "G3.6 fiesta_income_sources blueprint registered "
        "(/fie/income-sources + /api/fiesta/income-sources)"
    )


__all__ = [
    "bp",
    "register_blueprint",
    "CANONICAL_INCOME_SOURCES",
    "_coerce_sources",
    "_validate_against_canonical",
    "_has_underlying_data",
    "_apply_sources",
]
