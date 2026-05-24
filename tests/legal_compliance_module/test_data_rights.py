"""F5 GDPR + SL PDPA compliance baseline -- behavioural tests.

Three scenarios per the Tier D5 scope cap:

  1. Privacy page renders (legal/privacy route, 200, contains 'Privacy Policy'
     and the PDPA reference).
  2. Data export returns the authed user's data (auth required; anon -> 401/302;
     authed -> 200, JSON attachment, contains the user's id).
  3. Delete requires the confirmation token (POST without token -> 400;
     POST with token -> 200 + soft-delete fields set + email anonymised).
"""

import json

import pytest

from tests.legal_compliance_module.conftest import login_as


def test_privacy_page_renders(client):
    """STEP 6 #1: privacy_policy.html renders and references the PDPA."""
    res = client.get("/legal/privacy")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "Privacy Policy" in body
    # The PDPA reference is the load-bearing compliance line on the page;
    # if it disappears, the legal team needs to know.
    assert "Personal Data Protection Act" in body


def test_data_export_anon_blocked(client):
    """STEP 6 #2 (anon arm): /api/me/data-export refuses unauthenticated requests."""
    res = client.get("/api/me/data-export", follow_redirects=False)
    assert res.status_code in (
        302,
        401,
    ), f"anon export should not 200, got {res.status_code}"


def test_data_export_requires_auth_and_returns_user_data(client, user_a):
    """STEP 6 #2: /api/me/data-export returns the authed user's row as JSON."""
    login_as(client, user_a)
    res = client.get("/api/me/data-export")
    assert res.status_code == 200, res.get_data(as_text=True)[:500]
    assert res.mimetype == "application/json"
    assert "attachment" in res.headers.get("Content-Disposition", "")

    payload = json.loads(res.get_data(as_text=True))
    assert payload["export_metadata"]["user_id"] == user_a.id
    assert payload["user"]["id"] == user_a.id
    assert payload["user"]["email"] == user_a.email
    # Empty lists are still required keys for downstream tooling.
    for key in (
        "subscriptions",
        "deduction_claims",
        "rental_agreements",
        "service_providers",
        "events",
    ):
        assert key in payload, f"export missing '{key}' section"
        assert isinstance(payload[key], list)


def test_data_delete_requires_confirmation_token(client, user_a, db_session):
    """STEP 6 #3: /api/me/delete refuses without token, accepts with token."""
    from models import User

    login_as(client, user_a)

    # No token -> 400 confirmation_required, user row untouched.
    bad = client.post("/api/me/delete", data={})
    assert bad.status_code == 400
    body = bad.get_json()
    assert body["error"] == "confirmation_required"
    refreshed = User.query.get(user_a.id)
    assert refreshed.deleted_at is None
    assert refreshed.email == user_a.email

    # GET /account/data mints a token into session + rendered form.
    page = client.get("/account/data")
    assert page.status_code == 200, page.get_data(as_text=True)[:500]
    # Snip the token out of the rendered hidden input. The template wraps the
    # attributes across multiple lines; use a regex tolerant of whitespace.
    html = page.get_data(as_text=True)
    import re
    match = re.search(
        r'name="confirmation_token"\s+value="([^"]+)"',
        html,
    )
    assert match, "delete form should embed a confirmation token"
    token = match.group(1)
    assert token

    # POST with the right token -> soft-delete succeeds.
    original_email = user_a.email
    user_id = user_a.id
    ok = client.post(
        "/api/me/delete",
        data={"confirmation_token": token},
    )
    assert ok.status_code == 200, ok.get_data(as_text=True)[:500]
    body = ok.get_json()
    assert body["status"] == "deleted"
    assert body["user_id"] == user_id

    # Row mutated as advertised.
    after = User.query.get(user_id)
    assert after is not None, "soft-delete should NOT remove the row"
    assert after.deleted_at is not None
    assert after.email != original_email
    assert after.email.startswith("deleted_user_")
    assert after.email.endswith("@deleted.fiesta")
    assert after.name.startswith("deleted_user_")
    assert after.password_hash is None
    assert after.subscription_status == "cancelled"
