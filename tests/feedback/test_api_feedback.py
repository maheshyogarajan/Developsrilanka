"""
Tests for POST /api/feedback — Sprint 4 Tier D4 (2026-05-24).

Coverage:
  1. Happy path: valid category + body -> 204, row persists, fields captured.
  2. Missing category -> 400, no row written.
  3. Empty / missing body -> 400, no row written.
  4. Invalid category -> 400, no row written.
  5. Cross-origin POST -> 403.
"""
import json

from feedback_models import Feedback


# --------------------------------------------------------------------------- #
# 1. Happy path
# --------------------------------------------------------------------------- #
def test_api_feedback_happy_path_returns_204_and_persists(client, app, cleanup_feedback):
    """POST a valid category + body -> 204 + row in `feedback`."""
    with app.app_context():
        before = Feedback.query.count()

    resp = client.post(
        "/api/feedback",
        data=json.dumps({
            "category": "bug",
            "body": "The audit page crashes when I click 'next'.",
            "url": "https://fiesta.test/audit",
        }),
        content_type="application/json",
        headers={
            "Origin": "http://localhost",
            "User-Agent": "Mozilla/5.0 (Test)",
        },
    )

    assert resp.status_code == 204, (
        f"Expected 204, got {resp.status_code} body={resp.data!r}"
    )
    assert resp.data == b"", "204 responses must have empty body"

    with app.app_context():
        after = Feedback.query.count()
        assert after == before + 1

        row = Feedback.query.order_by(Feedback.id.desc()).first()
        assert row is not None
        assert row.category == "bug"
        assert "audit page crashes" in row.body
        assert row.url_at_submit == "https://fiesta.test/audit"
        assert row.user_agent and "Mozilla" in row.user_agent


# --------------------------------------------------------------------------- #
# 2. Missing category -> 400
# --------------------------------------------------------------------------- #
def test_api_feedback_missing_category_rejected(client, app, cleanup_feedback):
    """A POST without `category` returns 400 and writes no row."""
    with app.app_context():
        before = Feedback.query.count()

    resp = client.post(
        "/api/feedback",
        data=json.dumps({"body": "Something."}),
        content_type="application/json",
        headers={"Origin": "http://localhost"},
    )

    assert resp.status_code == 400
    body = resp.get_json()
    assert body and "category" in body["error"].lower()

    with app.app_context():
        assert Feedback.query.count() == before


# --------------------------------------------------------------------------- #
# 3. Empty body -> 400
# --------------------------------------------------------------------------- #
def test_api_feedback_empty_body_rejected(client, app, cleanup_feedback):
    """A POST with a known category but empty body returns 400."""
    with app.app_context():
        before = Feedback.query.count()

    resp = client.post(
        "/api/feedback",
        data=json.dumps({"category": "praise", "body": "   "}),
        content_type="application/json",
        headers={"Origin": "http://localhost"},
    )

    assert resp.status_code == 400
    body = resp.get_json()
    assert body and "body" in body["error"].lower()

    with app.app_context():
        assert Feedback.query.count() == before


# --------------------------------------------------------------------------- #
# 4. Invalid category -> 400
# --------------------------------------------------------------------------- #
def test_api_feedback_invalid_category_rejected(client, app, cleanup_feedback):
    """A POST with a category outside the allowed set returns 400."""
    with app.app_context():
        before = Feedback.query.count()

    resp = client.post(
        "/api/feedback",
        data=json.dumps({"category": "complaint", "body": "x"}),
        content_type="application/json",
        headers={"Origin": "http://localhost"},
    )

    assert resp.status_code == 400
    body = resp.get_json()
    assert body and "category" in body["error"].lower()

    with app.app_context():
        assert Feedback.query.count() == before


# --------------------------------------------------------------------------- #
# 5. Cross-origin POST -> 403
# --------------------------------------------------------------------------- #
def test_api_feedback_rejects_cross_origin_post(client, app, cleanup_feedback):
    """An Origin from an unrelated host returns 403 and writes no row."""
    with app.app_context():
        before = Feedback.query.count()

    resp = client.post(
        "/api/feedback",
        data=json.dumps({"category": "bug", "body": "x"}),
        content_type="application/json",
        headers={"Origin": "https://attacker.example"},
    )

    assert resp.status_code == 403
    body = resp.get_json()
    assert body and "origin" in body["error"].lower()

    with app.app_context():
        assert Feedback.query.count() == before
