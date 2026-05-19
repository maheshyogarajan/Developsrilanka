"""
T4 — Wave H council #1: CSRF protection MUST cover the new POST endpoints.

The app-level conftest disables CSRF for the data-logic tests. This file
re-enables it per test (via a fixture) and asserts that POSTs without a
token are rejected. Also, the live curl smoke after each deploy independently
confirms CSRF rejection on /remittance/new and /remittance/import (see
working files/_cockpit_fiesta/STATE.md deploy history).
"""
import pytest

from .conftest import login_as


@pytest.fixture
def csrf_on(app):
    """Temporarily re-enable CSRF for this test."""
    prior = app.config.get("WTF_CSRF_ENABLED")
    app.config["WTF_CSRF_ENABLED"] = True
    yield
    app.config["WTF_CSRF_ENABLED"] = prior


@pytest.mark.usefixtures("csrf_on")
def test_remittance_new_post_without_csrf_rejected(client, db_session, user_a):
    login_as(client, user_a)
    resp = client.post(
        "/remittance/new",
        data={
            "foreign_currency": "USD",
            "foreign_amount": "1000",
            "remittance_date": "2026-03-15",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (400, 403), (
        f"POST without CSRF token should be rejected. Got {resp.status_code}."
    )


@pytest.mark.usefixtures("csrf_on")
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
