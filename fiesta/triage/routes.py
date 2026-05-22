"""S1 Triage blueprint — Wave 1 (2026-05-20).

The 3 neutral post-signup fact-finds at /fie/triage. Persists answers to
`User.triage_answers` (JSON). Branching downstream is consumed by other screens
(/fie/, S5 deduction chips, S7 property gate); S1 itself only writes.

Routes:
  GET  /fie/triage          — render current question (or first if no progress)
  POST /fie/triage          — record answer for current question, advance
  POST /fie/triage/restart  — clear progress, start over (admin / testing only)

Session state:
  session['triage_state'] = {
      'current': '<question_id>',
      'answers': {qid: value, ...},   # in-progress answers
  }

Once all 3 questions are answered, the final POST:
  1. Validates the full payload (defence-in-depth — each one was validated on
     submission too).
  2. Writes User.triage_answers (with completed_at timestamp).
  3. Clears session['triage_state'].
  4. Redirects to ?next=... if provided AND it's a relative path, else /fie/
     (S0 dashboard).

Auth: @login_required on every route. Anonymous hits get 302'd to /login.

CSRF: Flask-WTF CSRFProtect is initialised globally in app.py. The POST handler
relies on the meta `<meta name="csrf-token">` token that the layout already
exposes; the form posts a hidden `csrf_token` input rendered via `{{ csrf_token() }}`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from flask import (
    Blueprint,
    Flask,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required

from .questions import (
    QUESTION_ORDER,
    QUESTIONS_BY_ID,
    get_question,
    is_multi,
    next_question_id,
)
from .validators import TriageValidationError, validate_answer, validate_full_answers


log = logging.getLogger(__name__)


bp = Blueprint(
    "fiesta_triage",
    __name__,
    url_prefix="/fie",
    template_folder="../../templates",
)


SESSION_KEY = "triage_state"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_state() -> Dict[str, Any]:
    """Return the session triage_state, initialised if missing."""
    state = session.get(SESSION_KEY)
    if not isinstance(state, dict):
        state = {"current": QUESTION_ORDER[0], "answers": {}}
        session[SESSION_KEY] = state
    # Repair any malformed entries — never crash on a corrupt session cookie.
    if state.get("current") not in QUESTIONS_BY_ID:
        # Re-derive 'current' from existing answers
        answered = [q for q in QUESTION_ORDER if q in state.get("answers", {})]
        state["current"] = (
            next_question_id(answered[-1]) if answered else QUESTION_ORDER[0]
        ) or QUESTION_ORDER[-1]
    if not isinstance(state.get("answers"), dict):
        state["answers"] = {}
    session[SESSION_KEY] = state
    return state


def _resolve_current(state: Dict[str, Any]) -> Optional[str]:
    """Return the current question id, skipping ones already answered.

    Returns None if the user has answered all questions in the session (the
    caller should then finalise).
    """
    answers = state.get("answers", {})
    # Walk the canonical order; the next unanswered is the current question.
    for qid in QUESTION_ORDER:
        if qid not in answers:
            return qid
    return None


def _safe_next_url(raw: Optional[str]) -> Optional[str]:
    """Return raw iff it's a relative URL (starts with /) and not protocol-rel.

    Mirrors the helper pattern used in fiesta.profile.routes.save. Prevents
    open-redirect via `?next=https://evil.example.com`.
    """
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return None
    if not raw.startswith("/") or raw.startswith("//"):
        return None
    return raw


def _post_complete_redirect() -> str:
    """Where to send the user after they finish triage.

    Priority:
      1. ?next=... if it's a safe relative path
      2. X9 F2.5 -- if persona == 'sl_foreign_income', send to /
         (which auto-redirects to /remittance/dashboard, the FIESTA hub
         entry for foreign-income earners). Otherwise the legacy /scan
         page via url_for('index').
      3. Fall back to '/'
    """
    nxt = _safe_next_url(request.args.get("next") or request.form.get("next"))
    if nxt:
        return nxt
    try:
        from flask_login import current_user
        if (
            current_user.is_authenticated
            and getattr(current_user, "persona", None) == "sl_foreign_income"
        ):
            return url_for("home")
        return url_for("index")
    except Exception:
        return "/"


def _emit_event(event: str, **payload: Any) -> None:
    """Best-effort analytics emit — mirrors fiesta.profile.routes._emit_event."""
    import json as _json
    try:
        logging.getLogger("fiesta.analytics").info(
            _json.dumps({"event": event, **payload}, default=str)
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@bp.route("/triage", methods=["GET"], strict_slashes=False)
def triage_form():
    """Render the current triage question.

    X8a — anonymous users are redirected to /signup (NOT /login) with
    ?next=/fie/triage preserved. This honours the v4-demo S2 narrative
    where signup is the funnel surface for unauthenticated visitors. The
    legacy @login_required would have 302'd to /login.
    """
    if not current_user.is_authenticated:
        from flask import url_for as _url_for
        return redirect(_url_for("signup", next="/fie/triage"))

    # If the user has already completed triage, don't loop them. Bounce to ?next
    # or the dashboard.
    persisted = getattr(current_user, "triage_answers", None) or {}
    if persisted.get("completed_at"):
        return redirect(_post_complete_redirect())

    state = _get_state()
    current_qid = _resolve_current(state)

    # Edge case: session has all 3 answered but DB didn't get the commit (e.g.
    # the user crashed before the final POST). Render a re-submit screen — show
    # the last question, the in-session answer will be preserved.
    if current_qid is None:
        current_qid = QUESTION_ORDER[-1]

    state["current"] = current_qid
    session[SESSION_KEY] = state

    question = get_question(current_qid)
    progress_idx = QUESTION_ORDER.index(current_qid) + 1
    total = len(QUESTION_ORDER)

    _emit_event(
        "triage_question_viewed",
        user_id=current_user.id,
        question_id=current_qid,
        position=progress_idx,
    )

    # X8a funnel: triage_started fires when the user first lands on Q1.
    # Use a session flag so refreshes don't double-fire.
    if progress_idx == 1 and not session.get("triage_started_emitted"):
        try:
            from events import emit as _emit_spine
            _emit_spine(
                "triage_started",
                user_id=current_user.id,
                payload={"first_question_id": current_qid},
                source="route:triage_form",
            )
            session["triage_started_emitted"] = True
        except Exception as _exc:
            log.debug(f"triage_started emit failed: {_exc}")

    return render_template(
        "triage/index.html",
        question=question,
        question_id=current_qid,
        position=progress_idx,
        total=total,
        already_picked=state["answers"].get(current_qid),
        next_url=request.args.get("next"),
    )


@bp.route("/triage", methods=["POST"], strict_slashes=False)
@login_required
def triage_submit():
    """Record one answer and advance, or finalise if it was the last."""
    state = _get_state()
    current_qid = _resolve_current(state) or QUESTION_ORDER[-1]

    # Allow the client to assert which question they're answering, so a stale
    # back-button tab can't accidentally overwrite a later answer.
    posted_qid = (request.form.get("question_id") or current_qid).strip()
    if posted_qid not in QUESTIONS_BY_ID:
        flash("Unknown question. Please try again.", "danger")
        return redirect(url_for("fiesta_triage.triage_form"))

    # Pull the raw answer based on multi vs single
    if is_multi(posted_qid):
        raw = request.form.getlist(f"answer_{posted_qid}")
        if not raw:
            # Some templates submit a single field with comma-separated values;
            # support that as a fallback.
            combined = (request.form.get(f"answer_{posted_qid}") or "").strip()
            raw = [x.strip() for x in combined.split(",") if x.strip()]
    else:
        raw = request.form.get(f"answer_{posted_qid}") or ""

    # Validate
    try:
        cleaned = validate_answer(posted_qid, raw)
    except TriageValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("fiesta_triage.triage_form"))

    # Store in session
    state["answers"][posted_qid] = cleaned

    # Are we done?
    new_current = _resolve_current(state)
    if new_current is None:
        # Finalise — write to DB
        try:
            final = validate_full_answers(state["answers"])
        except TriageValidationError as exc:
            log.warning(
                "S1 triage: final validation failed for user %s: %s",
                current_user.id,
                exc,
            )
            flash("We hit a problem saving your answers. Let's try that last one again.", "danger")
            # Drop the just-answered question so they can re-try
            state["answers"].pop(posted_qid, None)
            state["current"] = posted_qid
            session[SESSION_KEY] = state
            return redirect(url_for("fiesta_triage.triage_form"))

        final["completed_at"] = datetime.utcnow().isoformat() + "Z"

        try:
            from app import db
            current_user.triage_answers = final
            db.session.add(current_user)
            db.session.commit()
        except Exception as exc:
            log.exception(
                "S1 triage: failed to persist triage_answers for user %s",
                current_user.id,
            )
            try:
                from app import db
                db.session.rollback()
            except Exception:
                pass
            flash(
                "We couldn't save your answers. Please try once more.",
                "danger",
            )
            return redirect(url_for("fiesta_triage.triage_form"))

        # Wipe session state so a future visit doesn't re-show
        session.pop(SESSION_KEY, None)

        _emit_event(
            "triage_completed",
            user_id=current_user.id,
            earning_source=final.get("earning_source"),
            earning_vehicle_count=len(final.get("earning_vehicle") or []),
            filing_history=final.get("filing_history"),
        )

        # X8a funnel: canonical spine emit (the local _emit_event above only
        # writes to the analytics logger; the spine event is what powers the
        # public-flow funnel dashboard).
        try:
            from events import emit as _emit_spine
            _emit_spine(
                "triage_completed",
                user_id=current_user.id,
                payload={
                    "earning_source": final.get("earning_source"),
                    "earning_vehicle_count": len(final.get("earning_vehicle") or []),
                    "filing_history": final.get("filing_history"),
                },
                source="route:triage_submit",
            )
            session.pop("triage_started_emitted", None)
        except Exception as _exc:
            log.debug(f"triage_completed spine emit failed: {_exc}")

        return redirect(_post_complete_redirect())

    # Not done yet — advance to the next question
    state["current"] = new_current
    session[SESSION_KEY] = state

    _emit_event(
        "triage_answer_recorded",
        user_id=current_user.id,
        question_id=posted_qid,
    )

    return redirect(
        url_for("fiesta_triage.triage_form", **(
            {"next": request.form.get("next")} if request.form.get("next") else {}
        ))
    )


@bp.route("/triage/restart", methods=["POST"], strict_slashes=False)
@login_required
def triage_restart():
    """Clear in-progress session AND any persisted answers. Mostly for tests
    and admin retake-from-scratch.
    """
    session.pop(SESSION_KEY, None)
    try:
        from app import db
        current_user.triage_answers = None
        db.session.add(current_user)
        db.session.commit()
    except Exception:
        log.exception("triage_restart: failed to clear triage_answers")
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass

    if request.is_json or request.headers.get("Accept") == "application/json":
        return jsonify({"ok": True, "restarted": True})

    return redirect(url_for("fiesta_triage.triage_form"))


# ---------------------------------------------------------------------------
# Public registration hook — mirrors the fiesta/signup pattern
# ---------------------------------------------------------------------------


def register_routes(app: Flask) -> None:
    """Standard FIESTA blueprint hook called from main.py."""
    if "fiesta_triage" in app.blueprints:
        log.debug("S1 triage blueprint already registered — skipping.")
        return
    app.register_blueprint(bp)
    log.info("S1 triage blueprint registered: /fie/triage")


__all__ = ["bp", "register_routes", "SESSION_KEY"]
