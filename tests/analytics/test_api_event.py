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
