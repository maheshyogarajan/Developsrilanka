"""
Support Copilot routes — Wave 3.2 (2026-05-18).

User surface:
  GET  /support                       — render ask form
  POST /support                       — answer_question; render answer or escalated
  POST /support/<ticket_id>/csat      — record csat_rating (1-5)

Admin surface:
  GET  /admin/support/queue           — open escalated tickets (oldest first)
  POST /admin/support/<ticket_id>/resolve — record human_answer + resolved_at

Admin gate matches customer_brain_routes._require_admin(): inline abort(403)
rather than the @admin_required decorator. Spec: hard 403 for non-admin, not
redirect-to-index — mirrors the strictness of remittance_routes._user_can_read_entry().

CSRF: routes are CSRF-protected via the app-wide Flask-WTF wiring (templates
emit {{ csrf_token() }} on every form). Tests disable CSRF via app config.
"""
from __future__ import annotations

import logging
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

log = logging.getLogger(__name__)


support_bp = Blueprint("support", __name__, url_prefix="/support")
support_admin_bp = Blueprint("support_admin", __name__, url_prefix="/admin/support")


# --------------------------------------------------------------------------- #
# Admin gate — inline 403 (same pattern as customer_brain_routes)
# --------------------------------------------------------------------------- #

def _require_admin():
    """Inline gate — abort(403) for any non-admin, including unauthenticated.

    Returns None on success. Aborts the request on failure.
    """
    if not current_user.is_authenticated:
        abort(403)
    if not (getattr(current_user, "is_admin", lambda: False)() or
            getattr(current_user, "role", "user") == "admin"):
        abort(403)


# --------------------------------------------------------------------------- #
# User-facing routes
# --------------------------------------------------------------------------- #

@support_bp.route("", methods=["GET"])
@support_bp.route("/", methods=["GET"])
@login_required
def ask():
    """Render the ask-a-question form."""
    return render_template("support/ask.html")


@support_bp.route("", methods=["POST"])
@support_bp.route("/", methods=["POST"])
@login_required
def submit():
    """Handle the question submission.

    Returns the answer page on a successful auto-answer, or the escalated
    page when the copilot escalates to human review.
    """
    question = (request.form.get("question") or "").strip()
    if not question:
        flash("Please type a question before submitting.", "warning")
        return redirect(url_for("support.ask"))

    # Cap input here too — defence-in-depth alongside the cap in
    # support_copilot.answer_question itself.
    if len(question) > 5000:
        flash("That question is very long — please trim it under 5000 characters.", "warning")
        return redirect(url_for("support.ask"))

    from support_copilot import answer_question
    ticket_id, copilot_answer = answer_question(
        user_id=current_user.id,
        question_text=question,
    )

    if ticket_id < 0:
        # Persistence blew up — degrade to an explicit escalation message so
        # the user doesn't get a generic 500.
        flash(
            "We hit a snag drafting an answer — your question has been queued "
            "for our team. We'll reply within 12 hours.",
            "warning",
        )
        return render_template("support/escalated.html",
                               ticket_id=None,
                               escalation_reason="internal_error")

    if copilot_answer is None:
        # Escalated path
        from support_copilot_models import SupportTicket
        ticket = SupportTicket.query.get(ticket_id)
        reason = ticket.escalation_reason if ticket else "unknown"
        return render_template("support/escalated.html",
                               ticket_id=ticket_id,
                               escalation_reason=reason)

    # Auto-answer path — enrich citations with KB metadata for the template.
    enriched_citations = _enrich_citations(copilot_answer.citations)
    return render_template(
        "support/answer.html",
        ticket_id=copilot_answer.ticket_id,
        answer=copilot_answer.answer,
        confidence=copilot_answer.confidence,
        citations=enriched_citations,
    )


@support_bp.route("/<int:ticket_id>/csat", methods=["POST"])
@login_required
def csat(ticket_id):
    """Record a 1-5 CSAT rating. Ignores re-submissions (idempotent — first
    rating wins so we don't let a user spam their score)."""
    from app import db
    from support_copilot_models import SupportTicket

    ticket = SupportTicket.query.get_or_404(ticket_id)
    # Only the owning user can rate their own ticket.
    if ticket.user_id != current_user.id:
        abort(403)

    raw = request.form.get("rating", "").strip()
    try:
        rating = int(raw)
    except ValueError:
        flash("Rating must be a number 1-5.", "warning")
        return redirect(url_for("support.ask"))
    if rating < 1 or rating > 5:
        flash("Rating must be between 1 and 5.", "warning")
        return redirect(url_for("support.ask"))

    if ticket.csat_rating is not None:
        # Already rated — silently no-op (don't reveal that we ignore it,
        # the UI shouldn't show the form twice anyway).
        return redirect(url_for("support.ask"))

    ticket.csat_rating = rating
    db.session.commit()

    # Emit so dashboards see CSAT distribution. Ad-hoc event type (not in
    # STANDARD_EVENTS — per Wave 3.2 contract, do not edit STANDARD_EVENTS).
    try:
        from events import emit
        emit(
            event_type="support_csat_submitted",
            user_id=current_user.id,
            payload={"ticket_id": ticket_id, "rating": rating},
            source="route:support.csat",
        )
    except Exception as e:
        log.warning("csat: emit support_csat_submitted failed: %s", e)

    flash("Thanks for the rating.", "success")
    return redirect(url_for("support.ask"))


# --------------------------------------------------------------------------- #
# Admin queue
# --------------------------------------------------------------------------- #

@support_admin_bp.route("/queue", methods=["GET"])
@login_required
def queue():
    """List open escalated tickets oldest-first (FIFO — the user who waited
    longest gets seen first)."""
    _require_admin()

    from support_copilot_models import SupportTicket
    from models import User

    rows = (
        SupportTicket.query
                     .filter(SupportTicket.escalated_to_human.is_(True),
                             SupportTicket.resolved_at.is_(None))
                     .order_by(SupportTicket.created_at.asc())
                     .limit(200)
                     .all()
    )
    # Lift user info in a single batch to avoid N+1.
    user_ids = list({t.user_id for t in rows})
    users = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}

    enriched = [
        {
            "ticket": t,
            "user_email": (users.get(t.user_id).email if users.get(t.user_id) else f"user#{t.user_id}"),
            "user_persona": (users.get(t.user_id).persona if users.get(t.user_id) else None),
        }
        for t in rows
    ]
    return render_template("admin/support_queue.html", tickets=enriched)


@support_admin_bp.route("/<int:ticket_id>/resolve", methods=["POST"])
@login_required
def resolve(ticket_id):
    """Admin posts the human answer + sets resolved_at."""
    _require_admin()

    from app import db
    from support_copilot_models import SupportTicket

    ticket = SupportTicket.query.get_or_404(ticket_id)
    human_answer = (request.form.get("human_answer") or "").strip()
    if not human_answer:
        flash("Please type an answer before resolving.", "warning")
        return redirect(url_for("support_admin.queue"))

    ticket.human_answer = human_answer[:10000]
    ticket.resolved_at = datetime.utcnow()
    db.session.commit()

    # Emit so dashboards see the resolution. Ad-hoc event type.
    try:
        from events import emit
        emit(
            event_type="support_resolved",
            user_id=ticket.user_id,
            payload={"ticket_id": ticket_id, "resolved_by_user_id": current_user.id},
            source="route:support_admin.resolve",
        )
    except Exception as e:
        log.warning("resolve: emit support_resolved failed: %s", e)

    flash(f"Ticket #{ticket_id} resolved.", "success")
    return redirect(url_for("support_admin.queue"))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _enrich_citations(raw_citations):
    """Turn ['cbsl_middle_rate_rule', 'ledger:42'] into UI-friendly dicts.

    Returns:
        [{"label": "Display label", "kind": "kb"|"ledger"|"audit"|"other",
          "id": "<original cite string>", "source_url": "<for kb>",
          "last_verified": "<for kb>", "summary": "<for ledger/audit>"}]
    """
    from support_copilot import _KB_CACHE
    out = []
    for c in (raw_citations or []):
        c = str(c)
        if ":" in c:
            kind, _, ref = c.partition(":")
            kind = kind.strip().lower()
            ref = ref.strip()
            out.append({
                "label": f"{kind} #{ref}",
                "kind": kind,
                "id": c,
                "source_url": "",
                "last_verified": "",
                "summary": "",
            })
        else:
            kb = _KB_CACHE.get(c)
            if kb:
                out.append({
                    "label": kb.get("topic") or c,
                    "kind": "kb",
                    "id": c,
                    "source_url": kb.get("source_url", ""),
                    "last_verified": kb.get("last_verified", ""),
                    "summary": "",
                })
            else:
                out.append({
                    "label": c,
                    "kind": "other",
                    "id": c,
                    "source_url": "",
                    "last_verified": "",
                    "summary": "",
                })
    return out


# --------------------------------------------------------------------------- #
# Blueprint registration — called from main.py (the orchestrator wires it).
# --------------------------------------------------------------------------- #

def register_routes(app):
    """Register both blueprints (user + admin) on the Flask app.

    Same pattern as remittance_routes.register_routes and
    customer_brain_routes.register_routes.
    """
    app.register_blueprint(support_bp)
    app.register_blueprint(support_admin_bp)
    log.info("Support Copilot routes registered at /support/* and /admin/support/*")
