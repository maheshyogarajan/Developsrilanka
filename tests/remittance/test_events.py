"""
EVENT SPINE tests — Wave 1 2026-05-17 (council #2 unanimous).

Validates the irreducible foundation for AI-run FIESTA analytics:

  1. emit() inserts a row
  2. user_id FK is respected
  3. JSON payload round-trips
  4. emit() NEVER raises on DB failure (best-effort contract)
  5. Request context (ip, user-agent, session) is captured when present
  6. The two composite indexes that power Wave 2 dashboards exist on the table

Test fixtures come from conftest.py (app, db_session, user_a). The fixture
imports `main` so the events table is created (db.create_all + _ensure_additive_schema).
"""
from sqlalchemy import text

from events import emit, STANDARD_EVENTS


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _purge_events(db_session, user_id):
    """Delete any Event rows for this user — keeps the live DB clean between
    test runs. The Event row points at user.id via ON DELETE SET NULL so
    deleting the user only nulls the FK; we want the rows gone outright."""
    from event_models import Event
    Event.query.filter(Event.user_id == user_id).delete()
    db_session.commit()


# --------------------------------------------------------------------------- #
# 1. emit() inserts a row
# --------------------------------------------------------------------------- #

def test_emit_inserts_row(app, db_session, user_a):
    from event_models import Event
    before = Event.query.filter(Event.user_id == user_a.id).count()

    with app.test_request_context("/"):
        new_id = emit("signup", user_id=user_a.id, source="test")

    assert new_id is not None, "emit() should return the new Event.id"
    after = Event.query.filter(Event.user_id == user_a.id).count()
    assert after == before + 1, f"Expected 1 new row, got {after - before}"

    row = Event.query.get(new_id)
    assert row is not None
    assert row.event_type == "signup"
    assert row.user_id == user_a.id
    assert row.source == "test"
    assert row.created_at is not None

    _purge_events(db_session, user_a.id)


# --------------------------------------------------------------------------- #
# 2. user_id FK linkage
# --------------------------------------------------------------------------- #

def test_emit_with_user_id_links_user(app, db_session, user_a):
    from event_models import Event

    with app.test_request_context("/"):
        new_id = emit("email_verified", user_id=user_a.id, source="test")

    row = Event.query.get(new_id)
    assert row.user_id == user_a.id, "user_id FK should be persisted"
    # Sanity: the row's user_id resolves to the test user.
    from models import User
    fetched = User.query.get(row.user_id)
    assert fetched is not None
    assert fetched.email == user_a.email

    _purge_events(db_session, user_a.id)


# --------------------------------------------------------------------------- #
# 3. JSON payload round-trip
# --------------------------------------------------------------------------- #

def test_emit_with_payload_serializes_json(app, db_session, user_a):
    from event_models import Event

    payload = {
        "currency": "USD",
        "amount": "1234.56",
        "tax_year": "2025-26",
        "ird_ready": True,
        "row_count": 7,
        "nested": {"a": 1, "b": ["x", "y"]},
        "nullable": None,
    }
    with app.test_request_context("/"):
        new_id = emit(
            "remittance_added",
            user_id=user_a.id,
            payload=payload,
            source="test",
        )

    row = Event.query.get(new_id)
    assert row.payload == payload, f"JSON round-trip mismatch: {row.payload!r}"

    _purge_events(db_session, user_a.id)


# --------------------------------------------------------------------------- #
# 4. emit() failure does NOT raise (best-effort contract)
# --------------------------------------------------------------------------- #

def test_emit_failure_does_not_raise(app, db_session, user_a, monkeypatch):
    """If the DB write blows up for any reason, emit() must return None and
    not propagate. Analytics is observational — a broken event MUST NOT break
    the caller's user-facing flow."""
    from app import db as real_db

    # Force the session add to raise. We patch the session instance method.
    def _explode(*args, **kwargs):
        raise RuntimeError("simulated DB failure")

    original_add = real_db.session.add
    monkeypatch.setattr(real_db.session, "add", _explode)

    with app.test_request_context("/"):
        # Should not raise.
        result = emit("signup", user_id=user_a.id, source="test")

    assert result is None, "emit() must return None on failure, not raise"

    # Restore — pytest's monkeypatch auto-reverts, but we re-bind defensively
    # so subsequent assertions in this test work cleanly.
    monkeypatch.setattr(real_db.session, "add", original_add)

    # Sanity: the session is still usable after the rollback.
    from event_models import Event
    count = Event.query.filter(Event.user_id == user_a.id).count()
    # No event was written (the failure was before commit).
    assert count == 0


# --------------------------------------------------------------------------- #
# 5. Request context (ip + user_agent + session) is lifted automatically
# --------------------------------------------------------------------------- #

def test_emit_captures_request_context(app, db_session, user_a):
    from event_models import Event

    fake_ua = "Mozilla/5.0 (pytest-event-spine)"
    fake_ip = "203.0.113.42"

    with app.test_request_context(
        "/",
        headers={
            "User-Agent": fake_ua,
            "X-Forwarded-For": fake_ip,
        },
    ):
        from flask import session as flask_session
        flask_session["session_id"] = "pytest-session-abc"
        new_id = emit("nudge_sent", user_id=user_a.id, source="test")

    row = Event.query.get(new_id)
    assert row.ip_address == fake_ip, f"ip_address not captured (got {row.ip_address!r})"
    assert row.user_agent == fake_ua, f"user_agent not captured (got {row.user_agent!r})"
    assert row.session_id == "pytest-session-abc", (
        f"session_id not captured (got {row.session_id!r})"
    )

    _purge_events(db_session, user_a.id)


# --------------------------------------------------------------------------- #
# 6. Composite indexes exist on the table (powers Wave 2 dashboard queries)
# --------------------------------------------------------------------------- #

def test_event_indexes_exist(app, db_session):
    """The (event_type, created_at DESC) and (user_id, created_at DESC) composite
    indexes MUST exist — they're what makes the leading-indicator SQL queries
    in Wave 2 (AI CRM, dashboards) fast on the live DB at scale.

    Queries pg_indexes (PostgreSQL system catalog)."""
    rows = db_session.execute(text(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'events'"
    )).fetchall()
    index_names = {r[0] for r in rows}

    assert "ix_events_type_created_at" in index_names, (
        f"Composite index (event_type, created_at DESC) missing. Found: {index_names}"
    )
    assert "ix_events_user_created_at" in index_names, (
        f"Composite index (user_id, created_at DESC) missing. Found: {index_names}"
    )


# --------------------------------------------------------------------------- #
# 7. STANDARD_EVENTS contract — Wave 2 consumers depend on this exact list
# --------------------------------------------------------------------------- #

def test_standard_events_contract():
    """The standard event types are the contract every Wave 2 consumer
    (AI CRM, leading-indicator dashboards, scheduler, ad-spend optimiser)
    will read. Snapshot test — if this fails, you broke the contract.

    Markov-L2 (2026-05-27) added 4 funnel-progression signals
    (profile_complete, al_completed, tax_bill_computed, tax_bill_finalized)
    consumed by fiesta.markov.state_writer to populate
    user_state_history. They are append-only additions; nothing in the
    original 12 was removed."""
    expected = {
        # Wave 1 — original 12 (event spine).
        "signup",
        "email_verified",
        "persona_set",
        "bank_statement_uploaded",
        "remittance_added",
        "remittance_ird_ready",
        "checkout_started",
        "checkout_completed",
        "payment_failed",
        "support_message_received",
        "nudge_sent",
        "idea_submitted",
        # Markov-L2 — funnel-progression additions (2026-05-27).
        "profile_complete",
        "al_completed",
        "tax_bill_computed",
        "tax_bill_finalized",
    }
    assert set(STANDARD_EVENTS) == expected, (
        f"STANDARD_EVENTS drift: missing={expected - set(STANDARD_EVENTS)} "
        f"unexpected={set(STANDARD_EVENTS) - expected}"
    )
    assert len(STANDARD_EVENTS) == 16, "STANDARD_EVENTS must have 16 entries"
