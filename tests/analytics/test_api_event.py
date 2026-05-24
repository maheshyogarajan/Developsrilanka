"""
Tests for POST /api/event — Sprint 4 Tier B (2026-05-24).

Coverage:

  1. Happy path: known event name + JSON body -> 204, row in events table,
     session_anon_id cookie set on response if not already on the request.
  2. Invalid event name -> 400, no row written.
  3. Origin check (the route's CSRF defence): cross-origin Origin header
     -> 403; missing Origin/Referer with application/json -> 204.
  4. session_anon_id cookie reaches /api/event payload when supplied.

These tests run against the live `events` table (same convention as
tests/remittance/test_events.py) — the cleanup_events fixture rolls back
any new rows on teardown. test_client requests don't open an app context
automatically, so each test wraps DB queries in `with app.app_context()`.
"""
import json

from event_models import Event


# --------------------------------------------------------------------------- #
# 1. Happy path
# --------------------------------------------------------------------------- #
def test_api_event_happy_path_returns_204_and_persists(client, app, cleanup_events):
    """POST a whitelisted event with a JSON body -> 204 + row in `events`."""
    with app.app_context():
        before = Event.query.filter(Event.event_type == "landing_view").count()

    resp = client.post(
        "/api/event",
        data=json.dumps({
            "event": "landing_view",
            "properties": {"surface": "s0_landing", "ab_variant": "control"},
        }),
        content_type="application/json",
        headers={
            "Origin": "http://localhost",  # test_client default host
        },
    )

    assert resp.status_code == 204, f"Expected 204, got {resp.status_code} body={resp.data!r}"
    assert resp.data == b"", "204 responses must have empty body"

    with app.app_context():
        after = Event.query.filter(Event.event_type == "landing_view").count()
        assert after == before + 1, f"Expected 1 new landing_view row, got {after - before}"

        row = (
            Event.query.filter(Event.event_type == "landing_view")
            .order_by(Event.id.desc())
            .first()
        )
        assert row is not None
        assert row.source == "beacon", "beacon endpoint should tag source=beacon"
        assert isinstance(row.payload, dict)
        assert row.payload.get("surface") == "s0_landing"
        assert row.payload.get("ab_variant") == "control"
        # session_anon_id should be present in the payload (server-generated
        # if the request didn't already carry one).
        assert "session_anon_id" in row.payload
        assert len(row.payload["session_anon_id"]) > 0


# --------------------------------------------------------------------------- #
# 2. Invalid event name -> 400
# --------------------------------------------------------------------------- #
def test_api_event_rejects_unknown_event_name(client, app, cleanup_events):
    """An event name outside the whitelist (and not custom:*) returns 400."""
    with app.app_context():
        before = Event.query.count()

    resp = client.post(
        "/api/event",
        data=json.dumps({"event": "admin_promote", "properties": {}}),
        content_type="application/json",
        headers={"Origin": "http://localhost"},
    )

    assert resp.status_code == 400
    body = resp.get_json()
    assert body and "error" in body
    assert "whitelist" in body["error"].lower()

    with app.app_context():
        after = Event.query.count()
        assert after == before, "no row should have been written for a rejected event"


def test_api_event_rejects_custom_event_for_anonymous_user(client, app, cleanup_events):
    """`custom:*` events require authentication; anonymous client -> 400."""
    with app.app_context():
        before = Event.query.count()

    resp = client.post(
        "/api/event",
        data=json.dumps({"event": "custom:experiment_42", "properties": {}}),
        content_type="application/json",
        headers={"Origin": "http://localhost"},
    )

    assert resp.status_code == 400
    body = resp.get_json()
    assert body and "authentication" in body["error"].lower()

    with app.app_context():
        assert Event.query.count() == before


# --------------------------------------------------------------------------- #
# 3. Origin / Referer check (the CSRF defence)
# --------------------------------------------------------------------------- #
def test_api_event_rejects_cross_origin_post(client, app, cleanup_events):
    """An Origin header from an unrelated host returns 403."""
    with app.app_context():
        before = Event.query.count()

    resp = client.post(
        "/api/event",
        data=json.dumps({"event": "landing_view"}),
        content_type="application/json",
        headers={"Origin": "https://attacker.example"},
    )

    assert resp.status_code == 403
    body = resp.get_json()
    assert body and "origin" in body["error"].lower()

    with app.app_context():
        assert Event.query.count() == before


def test_api_event_allows_json_post_with_no_origin_header(client, app, cleanup_events):
    """Some sendBeacon paths strip Origin/Referer; falling back to a JSON
    content-type check should keep these legitimate requests working."""
    with app.app_context():
        before = Event.query.filter(Event.event_type == "landing_view").count()

    resp = client.post(
        "/api/event",
        data=json.dumps({"event": "landing_view", "properties": {}}),
        content_type="application/json",
        # NO Origin or Referer headers
    )

    assert resp.status_code == 204
    with app.app_context():
        after = Event.query.filter(Event.event_type == "landing_view").count()
        assert after == before + 1


# --------------------------------------------------------------------------- #
# 4. session_anon_id cookie handling
# --------------------------------------------------------------------------- #
def test_api_event_uses_anon_cookie_when_supplied(client, app, cleanup_events):
    """If the request carries session_anon_id, the event row should record
    that exact value (not mint a new one)."""
    fixed_anon = "test_anon_id_abcdef0123456789"
    client.set_cookie(
        "session_anon_id", fixed_anon,
        domain="localhost",
    )

    resp = client.post(
        "/api/event",
        data=json.dumps({"event": "cta_click", "properties": {"cta_id": "x"}}),
        content_type="application/json",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 204

    with app.app_context():
        row = (
            Event.query.filter(Event.event_type == "cta_click")
            .order_by(Event.id.desc())
            .first()
        )
        assert row is not None
        assert row.payload.get("session_anon_id") == fixed_anon


def test_after_request_sets_anon_cookie_when_absent(client, app):
    """Any normal request without the cookie should come back with one."""
    client.delete_cookie("session_anon_id", domain="localhost")
    resp = client.get("/")

    # Find the cookie in Set-Cookie headers (Werkzeug's test client exposes
    # them via response.headers.get_all).
    set_cookies = resp.headers.get_all("Set-Cookie")
    has_anon = any("session_anon_id=" in c for c in set_cookies)
    assert has_anon, f"Expected session_anon_id in Set-Cookie. Got: {set_cookies!r}"


# --------------------------------------------------------------------------- #
# 5. Tier C2 — top-level session_anon_id column + dual-read fallback
# --------------------------------------------------------------------------- #
def test_beacon_writes_top_level_session_anon_id_column(client, app, cleanup_events):
    """Tier C2: a new beacon hit must populate the indexed top-level
    `events.session_anon_id` column, not just the payload JSON. Proves the
    dual-write path from analytics_beacon_routes -> events.emit -> Event."""
    fixed_anon = "tierc2_topcol_aaaaaaaaaaaaaaaa"
    client.set_cookie("session_anon_id", fixed_anon, domain="localhost")

    resp = client.post(
        "/api/event",
        data=json.dumps({"event": "signup_started", "properties": {}}),
        content_type="application/json",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 204

    with app.app_context():
        row = (
            Event.query.filter(Event.event_type == "signup_started")
            .order_by(Event.id.desc())
            .first()
        )
        assert row is not None
        # Top-level column populated (the whole point of Tier C2).
        assert row.session_anon_id == fixed_anon, (
            f"Expected top-level session_anon_id={fixed_anon!r}, "
            f"got {row.session_anon_id!r}"
        )
        # Payload still carries it too (transitional dual-write — existing
        # analytics consumers reading payload['session_anon_id'] keep working).
        assert row.payload.get("session_anon_id") == fixed_anon
        # Dual-read property surfaces the canonical value.
        assert row.anon_id == fixed_anon


def test_dual_read_fallback_for_payload_only_legacy_row(app, cleanup_events):
    """Pre-Tier-C2 rows were written with session_anon_id INSIDE payload only
    and the top-level column NULL. The Event.anon_id property must transparently
    fall back to the payload value so any consumer that switches to .anon_id
    sees the canonical id regardless of row vintage."""
    from app import db

    legacy_anon = "legacy_payload_only_zzzzzzzzzzzz"
    with app.app_context():
        legacy = Event(
            event_type="landing_view",
            user_id=None,
            payload={"session_anon_id": legacy_anon, "surface": "s0_legacy"},
            source="beacon",
            session_anon_id=None,  # legacy: top-level column NULL
        )
        db.session.add(legacy)
        db.session.commit()
        legacy_id = legacy.id

        # Read it back and exercise the dual-read property.
        roundtrip = Event.query.get(legacy_id)
        assert roundtrip is not None
        assert roundtrip.session_anon_id is None, "fixture must leave top-level NULL"
        assert roundtrip.payload.get("session_anon_id") == legacy_anon
        # The property is the contract — falls back to payload.
        assert roundtrip.anon_id == legacy_anon


# --------------------------------------------------------------------------- #
# 6. Tier D2 F8 — organization_id population from current_user
# --------------------------------------------------------------------------- #
def _make_user_with_org(db, suffix, *, with_org=True, is_default=True):
    """Create a User (+ optional Organization + OrganizationUser membership).
    Returns (user, organization_or_None). Caller deletes everything in teardown.
    """
    from datetime import datetime, timedelta
    from werkzeug.security import generate_password_hash
    from models import User, Organization, OrganizationUser, UserRole

    u = User(
        email=f"pytest_f8_{suffix}@fiesta.local",
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name=f"Pytest F8 {suffix}",
        role="user",
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
    )
    db.session.add(u)
    db.session.commit()

    org = None
    if with_org:
        org = Organization(name=f"Pytest F8 Org {suffix}")
        db.session.add(org)
        db.session.commit()
        membership = OrganizationUser(
            user_id=u.id,
            organization_id=org.id,
            role=UserRole.OWNER.value,
            is_default=is_default,
        )
        db.session.add(membership)
        db.session.commit()
    return u, org


def _delete_user_with_org(db, user, org):
    """Teardown helper — clear OrganizationUser + Organization + User."""
    from models import User, Organization, OrganizationUser
    OrganizationUser.query.filter(OrganizationUser.user_id == user.id).delete()
    if org is not None:
        Organization.query.filter(Organization.id == org.id).delete()
    User.query.filter(User.id == user.id).delete()
    db.session.commit()


def test_authenticated_beacon_populates_organization_id(client, app, cleanup_events):
    """Tier D2 F8: an authenticated user's default organization id must land
    on Event.organization_id (the top-level FK column) for every beacon hit.

    Proves the dual path: _current_organization_id() lifts the org via
    User.get_default_organization() -> emit(organization_id=...) -> column.
    """
    from app import db
    with app.app_context():
        user, org = _make_user_with_org(db, "auth_org_populates")
        user_id, org_id = user.id, org.id
    try:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True

        resp = client.post(
            "/api/event",
            data=json.dumps({
                "event": "audit_view",
                "properties": {"surface": "s2_audit"},
            }),
            content_type="application/json",
            headers={"Origin": "http://localhost"},
        )
        assert resp.status_code == 204, f"got {resp.status_code} body={resp.data!r}"

        with app.app_context():
            row = (
                Event.query.filter(
                    Event.event_type == "audit_view",
                    Event.user_id == user_id,
                )
                .order_by(Event.id.desc())
                .first()
            )
            assert row is not None, "beacon row should have been written"
            assert row.user_id == user_id, "user_id should be populated from session"
            assert row.organization_id == org_id, (
                f"Expected Event.organization_id={org_id} (the user's default org), "
                f"got {row.organization_id!r}"
            )
    finally:
        with app.app_context():
            from models import User
            u_reload = User.query.get(user_id)
            from models import Organization
            o_reload = Organization.query.get(org_id) if org_id else None
            _delete_user_with_org(db, u_reload, o_reload)


def test_anonymous_beacon_leaves_organization_id_null(client, app, cleanup_events):
    """Tier D2 F8: an anonymous request has no current_user, so the beacon
    must write Event.organization_id IS NULL (no fabricated default org)."""
    # Belt-and-braces: ensure no session cookie carries over from another test.
    with client.session_transaction() as sess:
        sess.clear()

    resp = client.post(
        "/api/event",
        data=json.dumps({
            "event": "landing_view",
            "properties": {"surface": "s0_landing"},
        }),
        content_type="application/json",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 204, f"got {resp.status_code} body={resp.data!r}"

    with app.app_context():
        row = (
            Event.query.filter(Event.event_type == "landing_view")
            .order_by(Event.id.desc())
            .first()
        )
        assert row is not None
        assert row.user_id is None, (
            f"Anonymous request should leave user_id NULL, got {row.user_id!r}"
        )
        assert row.organization_id is None, (
            f"Anonymous request must NOT fabricate an org_id; "
            f"got Event.organization_id={row.organization_id!r}"
        )


def test_query_by_top_level_session_anon_id_uses_index_path(client, app, cleanup_events):
    """A direct WHERE on the top-level column must return the row — proves the
    index path is reachable (this is the query shape Wave-2 dashboards will
    use; payload->>'session_anon_id' is no longer required)."""
    fixed_anon = "tierc2_index_qqqqqqqqqqqqqqqqqq"
    client.set_cookie("session_anon_id", fixed_anon, domain="localhost")

    resp = client.post(
        "/api/event",
        data=json.dumps({"event": "cta_click", "properties": {"cta_id": "hero"}}),
        content_type="application/json",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 204

    with app.app_context():
        # The query Wave-2 dashboards will run: single-column index probe.
        rows = (
            Event.query.filter(Event.session_anon_id == fixed_anon)
            .order_by(Event.created_at.desc())
            .all()
        )
        assert len(rows) >= 1
        assert all(r.session_anon_id == fixed_anon for r in rows)
        # And the one we just wrote is in there.
        assert any(r.event_type == "cta_click" for r in rows)
