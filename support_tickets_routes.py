"""
Support ticket routes — Tier D2 (2026-05-24).

Customer-facing conversation surface for the D2 lightweight ticketing
system (support_models.D2SupportTicket / D2SupportTicketMessage).

Routes (all under url_prefix='/support'):

  POST /api/support/ticket             — JSON. Create a ticket. CSRF-exempt;
                                          guarded by Origin/Referer + auth.
                                          Returns 201 + {ticket_id}.
  GET  /support/tickets                — HTML. The current customer's tickets.
  GET  /support/tickets/<int:ticket_id> — HTML. One ticket + its message thread.
  POST /support/tickets/<int:ticket_id>/reply
                                       — HTML form. Customer adds a message.

Ownership: tickets are owned by `user_id`. A request for a ticket whose
`user_id != current_user.id` aborts 404 (NOT 403 — don't disclose existence).
The /admin queue lives elsewhere (not in scope for D2); the CEO reads the
queue via SQL per the scope cap.

Why a SEPARATE module from the existing `support_routes.py`?
  * `support_routes.py` already owns the `/support` and `/admin/support`
    blueprints for the Wave 3.2 AI Support Copilot (single-shot Q&A). Two
    blueprints with the same name would collide; the D2 work needs its own
    namespace.
  * Routes here share the `/support` URL prefix (different paths — `/tickets`
    vs `''`) so the Wave 3.2 `/support` form keeps working.

CSRF policy:
  * POST /api/support/ticket is JSON and consumed by the widget — same shape
    as POST /api/feedback. CSRF-exempt + Origin-gated.
  * POST /support/tickets/<id>/reply is an HTML form submission — uses the
    app-wide Flask-WTF csrf_token() machinery (template embeds the token).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional
from urllib.parse import urlparse

from flask import (
    Blueprint, Flask, abort, flash, jsonify, redirect,
    render_template, request, url_for,
)
from flask_login import current_user, login_required


log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #
_SUBJECT_MAX = 200
_BODY_MAX = 8_000
_PAYLOAD_MAX_BYTES = 16_384
_COOKIE_NAME = "session_anon_id"


# --------------------------------------------------------------------------- #
# Blueprint — separate name from Wave 3.2's `support_bp` to avoid collision.
# Same url_prefix is fine; different route paths.
# --------------------------------------------------------------------------- #
support_tickets_bp = Blueprint(
    "support_tickets",
    __name__,
    url_prefix="/support",
)


# --------------------------------------------------------------------------- #
# Origin / Referer gate — mirrors feedback_routes._origin_ok.
# --------------------------------------------------------------------------- #
def _allowed_origins() -> set:
    out = set()
    try:
        out.add(request.host_url.rstrip("/"))
    except Exception:
        pass
    extra = os.environ.get("BEACON_ALLOWED_ORIGINS", "")
    for raw in extra.split(","):
        raw = raw.strip()
        if raw:
            out.add(raw.rstrip("/"))
    return out


def _origin_ok() -> bool:
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    allowed = _allowed_origins()

    if origin:
        return origin.rstrip("/") in allowed
    if referer:
        try:
            parsed = urlparse(referer)
            ref_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
            return ref_origin in allowed
        except Exception:
            return False
    ctype = (request.headers.get("Content-Type") or "").lower()
    return ctype.startswith("application/json")


def _current_user_id() -> Optional[int]:
    try:
        if getattr(current_user, "is_authenticated", False):
            return int(current_user.get_id() or 0) or None
    except Exception:
        return None
    return None


def _get_anon_id() -> Optional[str]:
    existing = request.cookies.get(_COOKIE_NAME)
    if existing and len(existing) <= 64:
        return existing
    pre = request.environ.get("fiesta.anon_id")
    if pre and len(pre) <= 64:
        return pre
    return None


def _default_assignee_id() -> Optional[int]:
    """Resolve the CEO user-id from env. None means leave unassigned.

    Reads CEO_USER_ID first (numeric), then SUPPORT_DEFAULT_ASSIGNEE_USER_ID
    as an alias. Lookup-by-email is intentionally avoided here to keep the
    route hot path free of DB joins.
    """
    for key in ("CEO_USER_ID", "SUPPORT_DEFAULT_ASSIGNEE_USER_ID"):
        raw = os.environ.get(key)
        if raw and raw.strip().isdigit():
            try:
                return int(raw.strip()) or None
            except Exception:
                continue
    return None


# --------------------------------------------------------------------------- #
# Shared helper — used by the API view AND by the feedback auto-bridge.
# --------------------------------------------------------------------------- #
def create_ticket_with_seed_message(
    *,
    user_id: Optional[int],
    session_anon_id: Optional[str],
    subject: str,
    body: str,
    category: Optional[str] = None,
    priority: str = "normal",
    seed_author_role: str = "customer",
    seed_author_user_id: Optional[int] = None,
    tags: Optional[list] = None,
) -> Optional[int]:
    """Create a D2SupportTicket + the opening D2SupportTicketMessage row.

    Single transaction; returns the new ticket id, or None on persistence
    failure (caller can decide whether to surface a 5xx or just degrade
    gracefully — the feedback auto-bridge degrades, the API surfaces 503).

    Validation is the CALLER'S responsibility (length caps, allowed category,
    auth check). This helper is the only place that knows the
    ticket+seed-message coupling.
    """
    try:
        from app import db
        from support_models import (
            D2SupportTicket,
            D2SupportTicketMessage,
            TICKET_PRIORITIES,
            TICKET_CATEGORIES,
            MESSAGE_AUTHOR_ROLES,
        )

        # Defensive normalisation — caller may have already validated.
        if priority not in TICKET_PRIORITIES:
            priority = "normal"
        if category and category not in TICKET_CATEGORIES:
            category = None
        if seed_author_role not in MESSAGE_AUTHOR_ROLES:
            seed_author_role = "customer"

        ticket = D2SupportTicket(
            user_id=user_id,
            session_anon_id=session_anon_id,
            subject=subject[:_SUBJECT_MAX],
            body=body[:_BODY_MAX],
            status="open",
            priority=priority,
            category=category,
            assignee_user_id=_default_assignee_id(),
            tags=list(tags) if tags else None,
        )
        db.session.add(ticket)
        db.session.flush()  # populate ticket.id without committing yet

        seed = D2SupportTicketMessage(
            ticket_id=ticket.id,
            author_user_id=seed_author_user_id if seed_author_user_id is not None else user_id,
            author_role=seed_author_role,
            body=body[:_BODY_MAX],
        )
        db.session.add(seed)
        db.session.commit()

        # Best-effort event emit — non-fatal if events.py is unavailable.
        try:
            from events import emit
            emit(
                event_type="support_ticket_created",
                user_id=user_id,
                payload={
                    "ticket_id": ticket.id,
                    "category": category,
                    "priority": priority,
                    "source": seed_author_role,
                },
                source="support_tickets_routes.create_ticket_with_seed_message",
            )
        except Exception as exc:
            log.debug("support_ticket_created emit failed (non-fatal): %s", exc)

        return ticket.id
    except Exception as exc:
        log.warning("create_ticket_with_seed_message failed: %s", exc)
        try:
            from app import db as _db
            _db.session.rollback()
        except Exception:
            pass
        return None


# --------------------------------------------------------------------------- #
# JSON API: POST /api/support/ticket
# --------------------------------------------------------------------------- #
def _build_api_ticket_view(csrf):
    def api_create_ticket():
        # Origin/Referer gate (defence-in-depth alongside CSRF-exempt)
        if not _origin_ok():
            return jsonify({"error": "origin not allowed"}), 403

        # Body parse + size cap
        raw = request.get_data(cache=False, as_text=False) or b""
        if len(raw) > _PAYLOAD_MAX_BYTES:
            return jsonify({"error": "payload too large"}), 413
        try:
            body_json = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            return jsonify({"error": "invalid json"}), 400
        if not isinstance(body_json, dict):
            return jsonify({"error": "json object required"}), 400

        # Auth — anonymous tickets supported, but we still require Origin gate.
        user_id = _current_user_id()
        anon_id = _get_anon_id()
        if user_id is None and not anon_id:
            return jsonify({"error": "authentication required"}), 401

        subject = (body_json.get("subject") or "").strip()
        text = body_json.get("body")
        category = (body_json.get("category") or "").strip().lower() or None
        priority = (body_json.get("priority") or "normal").strip().lower()
        raw_tags = body_json.get("tags") or []

        if not subject:
            return jsonify({"error": "subject required"}), 400
        if len(subject) > _SUBJECT_MAX:
            subject = subject[:_SUBJECT_MAX]
        if not isinstance(text, str) or not text.strip():
            return jsonify({"error": "body required"}), 400
        text = text.strip()
        if len(text) > _BODY_MAX:
            text = text[:_BODY_MAX]

        from support_models import TICKET_PRIORITIES, TICKET_CATEGORIES
        if priority not in TICKET_PRIORITIES:
            return jsonify({
                "error": "priority must be one of: "
                + ", ".join(sorted(TICKET_PRIORITIES))
            }), 400
        if category is not None and category not in TICKET_CATEGORIES:
            return jsonify({
                "error": "category must be one of: "
                + ", ".join(sorted(TICKET_CATEGORIES))
            }), 400

        # Tags — accept list[str], cap each item to 64 chars, cap list to 8 items.
        tags = None
        if isinstance(raw_tags, list):
            cleaned = []
            for t in raw_tags[:8]:
                if isinstance(t, str):
                    t = t.strip().lower()[:64]
                    if t:
                        cleaned.append(t)
            tags = cleaned or None

        new_id = create_ticket_with_seed_message(
            user_id=user_id,
            session_anon_id=anon_id,
            subject=subject,
            body=text,
            category=category,
            priority=priority,
            seed_author_role="customer",
            seed_author_user_id=user_id,
            tags=tags,
        )
        if new_id is None:
            return jsonify({"error": "could not create ticket"}), 503
        return jsonify({"ticket_id": new_id}), 201

    try:
        csrf.exempt(api_create_ticket)
    except Exception as exc:
        log.warning(
            "api/support/ticket: csrf.exempt failed (%s) — endpoint will require token.",
            exc,
        )
    return api_create_ticket


# --------------------------------------------------------------------------- #
# HTML routes (customer surface)
# --------------------------------------------------------------------------- #
@support_tickets_bp.route("/tickets", methods=["GET"])
@login_required
def list_tickets():
    """Render the current customer's tickets, newest first."""
    from support_models import D2SupportTicket

    rows = (
        D2SupportTicket.query
        .filter(D2SupportTicket.user_id == current_user.id)
        .order_by(D2SupportTicket.created_at.desc())
        .limit(100)
        .all()
    )
    return render_template("support/tickets/list.html", tickets=rows)


@support_tickets_bp.route("/tickets/<int:ticket_id>", methods=["GET"])
@login_required
def view_ticket(ticket_id):
    """Render one ticket and its conversation thread.

    Ownership: 404 (not 403) if the ticket doesn't belong to current_user —
    don't disclose existence to non-owners.
    """
    from support_models import D2SupportTicket, D2SupportTicketMessage

    ticket = D2SupportTicket.query.get_or_404(ticket_id)
    if ticket.user_id != current_user.id:
        # Staff/admin view is intentionally out of scope for D2; CEO reads
        # the queue via SQL per the scope cap.
        abort(404)

    messages = (
        D2SupportTicketMessage.query
        .filter(D2SupportTicketMessage.ticket_id == ticket.id)
        .order_by(D2SupportTicketMessage.created_at.asc())
        .all()
    )
    return render_template(
        "support/tickets/detail.html",
        ticket=ticket,
        messages=messages,
    )


@support_tickets_bp.route("/tickets/<int:ticket_id>/reply", methods=["POST"])
@login_required
def reply_ticket(ticket_id):
    """Customer adds a message. Ownership-checked. Flips status if needed."""
    from app import db
    from support_models import D2SupportTicket, D2SupportTicketMessage

    ticket = D2SupportTicket.query.get_or_404(ticket_id)
    if ticket.user_id != current_user.id:
        abort(404)
    if ticket.status == "closed":
        flash("This ticket is closed. Please open a new ticket.", "warning")
        return redirect(url_for("support_tickets.view_ticket", ticket_id=ticket.id))

    text = (request.form.get("body") or "").strip()
    if not text:
        flash("Please type a message before sending.", "warning")
        return redirect(url_for("support_tickets.view_ticket", ticket_id=ticket.id))
    if len(text) > _BODY_MAX:
        text = text[:_BODY_MAX]

    msg = D2SupportTicketMessage(
        ticket_id=ticket.id,
        author_user_id=current_user.id,
        author_role="customer",
        body=text,
    )
    db.session.add(msg)

    # If staff/AI had handed the ball back to the customer, flip to 'open'
    # so it re-surfaces in the CEO queue. Otherwise leave the status alone.
    if ticket.status == "awaiting_customer":
        ticket.status = "open"

    db.session.commit()

    try:
        from events import emit
        emit(
            event_type="support_ticket_message",
            user_id=current_user.id,
            payload={
                "ticket_id": ticket.id,
                "author_role": "customer",
                "message_id": msg.id,
            },
            source="support_tickets_routes.reply_ticket",
        )
    except Exception as exc:
        log.debug("support_ticket_message emit failed (non-fatal): %s", exc)

    flash("Reply sent.", "success")
    return redirect(url_for("support_tickets.view_ticket", ticket_id=ticket.id))


# --------------------------------------------------------------------------- #
# Public registration hook
# --------------------------------------------------------------------------- #
def register_routes(app: Flask) -> None:
    """Wire D2 support-ticket routes into the given Flask app. Idempotent."""
    if app.config.get("_FIESTA_SUPPORT_TICKETS_REGISTERED"):
        return
    app.config["_FIESTA_SUPPORT_TICKETS_REGISTERED"] = True

    from app import csrf as _csrf

    # JSON API endpoint — registered as a top-level rule (not in the
    # blueprint) because it lives under /api/, not /support/.
    api_view = _build_api_ticket_view(_csrf)
    app.add_url_rule(
        "/api/support/ticket",
        endpoint="support_ticket_create",
        view_func=api_view,
        methods=["POST"],
    )

    # HTML routes via blueprint (different name from Wave 3.2's support_bp).
    app.register_blueprint(support_tickets_bp)
    log.info(
        "Support tickets (D2) registered: POST /api/support/ticket + "
        "/support/tickets/*"
    )


__all__ = ["register_routes", "create_ticket_with_seed_message"]
