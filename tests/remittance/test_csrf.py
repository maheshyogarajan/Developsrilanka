"""
T4 — Wave H council #1: CSRF protection MUST cover the new POST endpoints.

App-level CSRFProtect is registered in app.py; this test confirms that registration
covers the /remittance/* blueprint.
"""
import pytest

from .conftest import login_as


def test_remittance_new_post_without_csrf_rejected(client, db_session, user_a):
    login_as(client, user_a)
    # Send a POST with valid form data but NO csrf_token → CSRFProtect should reject.
    resp = client.post(
        "/remittance/new",
        data={
            "foreign_currency": "USD",
            "foreign_amount": "1000",
            "remittance_date": "2026-03-15",
        },
        follow_redirects=False,
    )
    # CSRFProtect returns 400 by default for missing token.
    assert resp.status_code in (400, 403), (
        f"POST without CSRF token should be rejected. Got {resp.status_code}."
    )


def test_remittance_import_post_without_csrf_rejected(client, db_session, user_a):
    login_as(client, user_a)
    resp = client.post(
        "/remittance/import",
        data={"statement": (b"%PDF-1.7\n", "test.pdf")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code in (400, 403), (
        f"Upload without CSRF token should be rejected. Got {resp.status_code}."
    )
