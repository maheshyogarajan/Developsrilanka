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
