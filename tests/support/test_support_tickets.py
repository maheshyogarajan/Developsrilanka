"""
Tests for D2 support-ticket system (2026-05-24).

3 cases required by the task brief:

  1. test_create_ticket_via_api_persists_with_seed_message
     POST /api/support/ticket -> 201 + 1 ticket + 1 seed message in DB.

  2. test_view_ticket_ownership_check_rejects_other_user
     GET /support/tickets/<id> for a ticket NOT owned by current_user -> 404.

  3. test_reply_adds_message_and_flips_status
     POST /support/tickets/<id>/reply by the owner adds a message; if the
     ticket was 'awaiting_customer', status flips back to 'open'.

Bonus coverage (cheap):

  4. test_feedback_widget_bug_auto_bridges_to_ticket
     POST /api/feedback with category=bug also creates a D2 ticket. This is
     the D4->D2 bridge that closes the "drop a note -> have a conversation"
     loop.
"""
import json

from tests.support.conftest import login_as  # type: ignore


# --------------------------------------------------------------------------- #
# 1. POST /api/support/ticket — happy path
# --------------------------------------------------------------------------- #
def test_create_ticket_via_api_persists_with_seed_message(client, app, user_a):
    """A valid POST creates a D2SupportTicket row + a seed message row."""
    login_as(client, user_a)

    from support_models import D2SupportTicket, D2SupportTicketMessage

    with app.app_context():
        tickets_before = D2SupportTicket.query.filter(
            D2SupportTicket.user_id == user_a.id
        ).count()

    resp = client.post(
        "/api/support/ticket",
        data=json.dumps({
            "subject": "Can't export receipts",
            "body": "When I click Export the page hangs at 90%.",
            "category": "bug",
            "priority": "high",
            "tags": ["receipts", "export"],
        }),
        content_type="application/json",
        headers={"Origin": "http://localhost"},
    )

    assert resp.status_code == 201, (
        f"Expected 201, got {resp.status_code}, body={resp.data!r}"
    )
    body = resp.get_json()
    assert body and "ticket_id" in body and isinstance(body["ticket_id"], int)

    with app.app_context():
        tickets_after = D2SupportTicket.query.filter(
            D2SupportTicket.user_id == user_a.id
        ).count()
        assert tickets_after == tickets_before + 1

        t = D2SupportTicket.query.get(body["ticket_id"])
        assert t is not None
        assert t.user_id == user_a.id
        assert t.subject == "Can't export receipts"
        assert "page hangs" in t.body
        assert t.status == "open"
        assert t.priority == "high"
        assert t.category == "bug"
        assert t.tags and "receipts" in t.tags and "export" in t.tags

        # Seed message must exist and quote the body.
        msgs = (
            D2SupportTicketMessage.query
            .filter(D2SupportTicketMessage.ticket_id == t.id)
            .all()
        )
        assert len(msgs) == 1
        assert msgs[0].author_role == "customer"
        assert msgs[0].author_user_id == user_a.id
        assert "page hangs" in msgs[0].body


# --------------------------------------------------------------------------- #
# 2. Ownership check — non-owner gets 404 (not 403)
# --------------------------------------------------------------------------- #
def test_view_ticket_ownership_check_rejects_other_user(app, user_a, user_b):
    """user_b must not be able to view user_a's ticket — server returns 404
    (not 403; we don't disclose ticket existence to non-owners).

    Why call the view function directly via a test request context instead
    of going through client.get?  Flask-Login's `current_user` resolution
    caches in `g._login_user` per-request, and Werkzeug's test-client cookie
    jar makes it hard to deterministically swap users WITHIN one pytest run
    (we observed `current_user` resolving to user_a even with session
    `_user_id=user_b`).  Using `login_user()` inside an explicit request
    context puts the right user into Flask-Login's local without depending
    on the cookie-roundtrip path — and exercises the SAME `current_user`
    comparison the view uses in production.
    """
    from flask import url_for
    from flask_login import login_user
    from werkzeug.exceptions import NotFound

    # Bootstrap user_a's ticket via direct DB write (skip the API roundtrip
    # so this test isolates the ownership-check logic).
    from app import db
    from support_models import D2SupportTicket, D2SupportTicketMessage

    with app.app_context():
        ticket = D2SupportTicket(
            user_id=user_a.id,
            subject="Private question",
            body="I'd rather user_b not see this.",
            status="open",
            priority="normal",
            category="other",
        )
        db.session.add(ticket)
        db.session.flush()
        db.session.add(D2SupportTicketMessage(
            ticket_id=ticket.id,
            author_user_id=user_a.id,
            author_role="customer",
            body="I'd rather user_b not see this.",
        ))
        db.session.commit()
        ticket_id = ticket.id

    # Import the view function so we can invoke it under a controlled
    # request context with a deterministic current_user.
    from support_tickets_routes import view_ticket

    # First: user_a (owner) — should render 200.
    with app.test_request_context(f"/support/tickets/{ticket_id}"):
        login_user(user_a)
        owner_response = view_ticket(ticket_id)
        # render_template returns a string; Flask wraps to a Response, but a
        # direct view-function call returns the raw string. The fact that it
        # returns at all (no abort) is what we care about.
        assert owner_response is not None
        assert "Private question" in str(owner_response)

    # Second: user_b (non-owner) — must abort(404).
    with app.test_request_context(f"/support/tickets/{ticket_id}"):
        login_user(user_b)
        raised = False
        try:
            view_ticket(ticket_id)
        except NotFound:
            raised = True
        assert raised, (
            "view_ticket should abort(404) for a non-owner; "
            f"user_a={user_a.id}, user_b={user_b.id}, ticket_id={ticket_id}"
        )


# --------------------------------------------------------------------------- #
# 3. Reply adds a message + flips status
# --------------------------------------------------------------------------- #
def test_reply_adds_message_and_flips_status(client, app, user_a):
    """Owner reply on awaiting_customer ticket -> message added + status='open'."""
    from app import db
    from support_models import D2SupportTicket, D2SupportTicketMessage

    # Bootstrap a ticket in 'awaiting_customer' state directly via the helper.
    login_as(client, user_a)
    create_resp = client.post(
        "/api/support/ticket",
        data=json.dumps({
            "subject": "Follow-up needed",
            "body": "Initial question.",
            "category": "confusion",
        }),
        content_type="application/json",
        headers={"Origin": "http://localhost"},
    )
    assert create_resp.status_code == 201
    ticket_id = create_resp.get_json()["ticket_id"]

    with app.app_context():
        t = D2SupportTicket.query.get(ticket_id)
        t.status = "awaiting_customer"
        db.session.commit()
        msgs_before = D2SupportTicketMessage.query.filter(
            D2SupportTicketMessage.ticket_id == ticket_id
        ).count()

    # Customer replies via the HTML form route.
    reply_resp = client.post(
        f"/support/tickets/{ticket_id}/reply",
        data={"body": "Here's the extra info you asked for."},
    )
    # Form route redirects on success (302), not 200.
    assert reply_resp.status_code in (302, 303), (
        f"Expected redirect, got {reply_resp.status_code}, body={reply_resp.data!r}"
    )

    with app.app_context():
        msgs_after = D2SupportTicketMessage.query.filter(
            D2SupportTicketMessage.ticket_id == ticket_id
        ).count()
        assert msgs_after == msgs_before + 1

        latest = (
            D2SupportTicketMessage.query
            .filter(D2SupportTicketMessage.ticket_id == ticket_id)
            .order_by(D2SupportTicketMessage.created_at.desc())
            .first()
        )
        assert latest is not None
        assert latest.author_role == "customer"
        assert latest.author_user_id == user_a.id
        assert "extra info" in latest.body

        # Status should flip back to 'open' so it re-surfaces in the CEO queue.
        t_after = D2SupportTicket.query.get(ticket_id)
        assert t_after.status == "open"


# --------------------------------------------------------------------------- #
# 4. (Bonus) D4 -> D2 auto-bridge
# --------------------------------------------------------------------------- #
def test_feedback_widget_bug_auto_bridges_to_ticket(client, app, user_a):
    """POST /api/feedback with category=bug also creates a D2 ticket."""
    from support_models import D2SupportTicket

    login_as(client, user_a)

    with app.app_context():
        tickets_before = D2SupportTicket.query.filter(
            D2SupportTicket.user_id == user_a.id
        ).count()

    resp = client.post(
        "/api/feedback",
        data=json.dumps({
            "category": "bug",
            "body": "Login redirects me in a loop.",
            "url": "https://fiesta.test/login",
        }),
        content_type="application/json",
        headers={
            "Origin": "http://localhost",
            "User-Agent": "Mozilla/5.0 (Test)",
        },
    )
    assert resp.status_code == 204, (
        f"Expected 204 from /api/feedback, got {resp.status_code}"
    )

    with app.app_context():
        tickets_after = D2SupportTicket.query.filter(
            D2SupportTicket.user_id == user_a.id
        ).count()
        assert tickets_after == tickets_before + 1

        t = (
            D2SupportTicket.query
            .filter(D2SupportTicket.user_id == user_a.id)
            .order_by(D2SupportTicket.created_at.desc())
            .first()
        )
        assert t is not None
        assert "Login redirects me in a loop" in t.body
        assert t.category == "bug"
        # Bug -> high priority per the bridge policy.
        assert t.priority == "high"
        # Bridge writes a from_feedback_widget tag for triage.
        assert t.tags and "from_feedback_widget" in t.tags
