"""
S2 Signup tests — Wave 1 (2026-05-20).

Covers:
  HAPPY PATH
    1. test_signup_form_renders
    2. test_signup_happy_path_creates_user_with_tos_and_privacy
    3. test_signup_redirects_to_verify_email_reminder_when_logged_in

  VALIDATION / EDGE CASES
    4. test_signup_rejects_short_password
    5. test_signup_rejects_password_without_digit
    6. test_signup_rejects_password_without_symbol
    7. test_signup_rejects_password_without_mixed_case
    8. test_signup_rejects_password_mismatch
    9. test_signup_rejects_malformed_email
   10. test_signup_rejects_missing_tos_acceptance
   11. test_signup_rejects_missing_privacy_acceptance
   12. test_signup_rejects_duplicate_email

  SECURITY
   13. test_signup_rejects_sql_injection_in_email
   14. test_password_complexity_helper_xss_payload
   15. test_legal_pages_render_with_draft_banner

These tests use the live Neon DB fixtures (same approach as the existing
remittance + ai_run suites). Each test cleans up via the autouse
`_cleanup_s2_signup_users` fixture in conftest.py.

Run with:
    cd /c/Users/mahes/fiesta_replit_source/DevelopSriLanka
    python -m pytest tests/auth/test_signup.py -v
"""
from __future__ import annotations

import uuid

import pytest

from tests.auth.conftest import SIGNUP_TEST_PREFIX


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _fresh_email() -> str:
    """A new random email for each test to avoid duplicate-email collisions."""
    return f"{SIGNUP_TEST_PREFIX}{uuid.uuid4().hex[:8]}@fiesta.local"


def _valid_password() -> str:
    return "Test123!Strong"  # 14 chars, digit, symbol, mixed case


def _post_signup(client, **overrides):
    """POST /signup with sensible defaults, allowing overrides per-test."""
    email = overrides.pop("email", _fresh_email())
    payload = {
        "email": email,
        "password": overrides.pop("password", _valid_password()),
        "confirm_password": overrides.pop("confirm_password", None),
        "accept_tos": overrides.pop("accept_tos", "1"),
        "accept_privacy": overrides.pop("accept_privacy", "1"),
    }
    # If caller didn't override confirm, mirror the password.
    if payload["confirm_password"] is None:
        payload["confirm_password"] = payload["password"]
    payload.update(overrides)
    # Drop None values so absent fields look like an unchecked checkbox.
    payload = {k: v for k, v in payload.items() if v is not None}
    return email, client.post("/signup", data=payload, follow_redirects=False)


# --------------------------------------------------------------------------- #
# HAPPY PATH
# --------------------------------------------------------------------------- #
def test_signup_form_renders(client):
    """GET /signup returns 200 with the FIESTA-branded form copy."""
    resp = client.get("/signup")
    assert resp.status_code == 200, resp.data[:200]
    body = resp.data.decode("utf-8")
    assert "Let's get you set up" in body
    assert "Start my free trial" in body
    assert "No NIC, no TIN, no PIN at signup" in body
    # Trust strip + jurisdiction-neutral framing.
    assert "Sri Lankan tax law" in body
    # ToS + Privacy checkbox labels with DRAFT tag.
    assert "Terms of Service" in body
    assert "Privacy Policy" in body
    assert "DRAFT" in body


def test_signup_happy_path_creates_user_with_tos_and_privacy(client, db_session):
    """Happy path: POST creates user, persists ToS/Privacy version, logs in,
    redirects to email-verification reminder."""
    from models import User
    from fiesta.signup.version import TOS_VERSION, PRIVACY_VERSION

    email, resp = _post_signup(client)
    assert resp.status_code in (301, 302), resp.data[:200]
    # Should redirect to verify-email reminder.
    assert "verify-email-reminder" in resp.headers["Location"] or \
           "verify_email_reminder" in resp.headers["Location"]

    user = User.query.filter_by(email=email).first()
    assert user is not None
    assert user.password_hash, "password should be hashed"
    assert not user.password_hash.startswith("Test123"), "raw password leaked into hash"
    assert user.tos_accepted_version == TOS_VERSION
    assert user.privacy_accepted_version == PRIVACY_VERSION
    assert user.tos_accepted_at is not None
    assert user.privacy_accepted_at is not None
    assert user.persona == "self"
    assert user.role == "user"
    assert user.subscription_status == "free_trial"


def test_signup_redirects_to_verify_email_reminder_when_logged_in(client, db_session):
    """After signup, an authenticated session is established (so the user
    sees /verify-email-reminder, not /login)."""
    email, resp = _post_signup(client)
    assert resp.status_code in (301, 302)

    # Verify Flask-Login session is set.
    with client.session_transaction() as sess:
        assert sess.get("_user_id") is not None, (
            "Expected Flask-Login session to be populated after signup"
        )


# --------------------------------------------------------------------------- #
# VALIDATION
# --------------------------------------------------------------------------- #
def test_signup_rejects_short_password(client, db_session):
    """Password under 12 chars is rejected."""
    from models import User
    email, resp = _post_signup(client, password="Sh0rt!", confirm_password="Sh0rt!")
    # Expect 302 back to /signup with a flash; user NOT created.
    assert resp.status_code in (301, 302)
    assert "/signup" in resp.headers["Location"]
    assert User.query.filter_by(email=email).first() is None


def test_signup_rejects_password_without_digit(client, db_session):
    """Password without a digit is rejected."""
    from models import User
    email, resp = _post_signup(
        client,
        password="StrongPasswordNoDigit!",
        confirm_password="StrongPasswordNoDigit!",
    )
    assert resp.status_code in (301, 302)
    assert "/signup" in resp.headers["Location"]
    assert User.query.filter_by(email=email).first() is None


def test_signup_rejects_password_without_symbol(client, db_session):
    """Password without a symbol is rejected."""
    from models import User
    email, resp = _post_signup(
        client,
        password="StrongPassword123",
        confirm_password="StrongPassword123",
    )
    assert resp.status_code in (301, 302)
    assert "/signup" in resp.headers["Location"]
    assert User.query.filter_by(email=email).first() is None


def test_signup_rejects_password_without_mixed_case(client, db_session):
    """Password without mixed case is rejected."""
    from models import User
    email, resp = _post_signup(
        client,
        password="all-lower-case-123!",
        confirm_password="all-lower-case-123!",
    )
    assert resp.status_code in (301, 302)
    assert "/signup" in resp.headers["Location"]
    assert User.query.filter_by(email=email).first() is None


def test_signup_rejects_password_mismatch(client, db_session):
    """Password + confirm must match."""
    from models import User
    email, resp = _post_signup(
        client,
        password="Test123!Strong",
        confirm_password="Test123!Different",
    )
    assert resp.status_code in (301, 302)
    assert "/signup" in resp.headers["Location"]
    assert User.query.filter_by(email=email).first() is None


def test_signup_rejects_malformed_email(client, db_session):
    """Malformed emails are rejected before reaching DB."""
    from models import User
    # 'not-an-email' has no @, fails regex.
    payload = {
        "email": "not-an-email",
        "password": _valid_password(),
        "confirm_password": _valid_password(),
        "accept_tos": "1",
        "accept_privacy": "1",
    }
    resp = client.post("/signup", data=payload, follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert "/signup" in resp.headers["Location"]
    # Nothing got persisted under the prefix.
    assert User.query.filter(User.email == "not-an-email").first() is None


def test_signup_rejects_missing_tos_acceptance(client, db_session):
    """Missing ToS checkbox = no account created."""
    from models import User
    # accept_tos=None tells _post_signup to drop the key entirely.
    email, resp = _post_signup(client, accept_tos=None)
    assert resp.status_code in (301, 302)
    assert "/signup" in resp.headers["Location"]
    assert User.query.filter_by(email=email).first() is None


def test_signup_rejects_missing_privacy_acceptance(client, db_session):
    """Missing Privacy checkbox = no account created."""
    from models import User
    email, resp = _post_signup(client, accept_privacy=None)
    assert resp.status_code in (301, 302)
    assert "/signup" in resp.headers["Location"]
    assert User.query.filter_by(email=email).first() is None


def test_signup_rejects_duplicate_email(client, db_session):
    """Second signup with the same email is rejected and routes the user to
    /login (not /signup)."""
    from models import User

    email, resp1 = _post_signup(client)
    assert resp1.status_code in (301, 302)
    assert User.query.filter_by(email=email).first() is not None

    # Second attempt with same email. Build payload manually to lock the email.
    payload = {
        "email": email,
        "password": _valid_password(),
        "confirm_password": _valid_password(),
        "accept_tos": "1",
        "accept_privacy": "1",
    }
    resp2 = client.post("/signup", data=payload, follow_redirects=False)
    assert resp2.status_code in (301, 302)
    # Should bounce to /login (not /signup) per UX.
    assert "/login" in resp2.headers["Location"]
    # Still exactly one user.
    assert User.query.filter_by(email=email).count() == 1


# --------------------------------------------------------------------------- #
# SECURITY
# --------------------------------------------------------------------------- #
def test_signup_rejects_sql_injection_in_email(client, db_session):
    """SQL-injection-shaped email is rejected by the regex (defense in depth
    on top of SQLAlchemy parameterisation)."""
    from models import User
    payload = {
        "email": "robert'; DROP TABLE users;--",
        "password": _valid_password(),
        "confirm_password": _valid_password(),
        "accept_tos": "1",
        "accept_privacy": "1",
    }
    resp = client.post("/signup", data=payload, follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert "/signup" in resp.headers["Location"]
    # Sanity: User table still exists and is queryable.
    assert User.query.filter(User.email.like(f"{SIGNUP_TEST_PREFIX}%")).count() == 0


def test_password_complexity_helper_xss_payload():
    """Server-side complexity check treats an XSS-style password as just a
    string: complexity rules are applied, no HTML parsing happens. This guards
    against template-side accidental rendering of password content."""
    from fiesta.signup.password import check_complexity, score_password

    xss = "<script>alert(1)</script>"
    # 25 chars, has digit? no. has symbol? yes. mixed case? no (no uppercase + lower mix).
    ok, errs = check_complexity(xss)
    assert not ok, "raw XSS payload should fail complexity (no digit / no mixed case)"
    # Score returns a stable shape, no exceptions.
    score = score_password(xss)
    assert score["bucket"] in ("weak", "fair", "strong")
    assert "has_symbol" in score


def test_legal_pages_render_with_draft_banner(client):
    """GET /terms and /privacy return 200 and surface the DRAFT banner so a
    customer can never miss that these are pre-counsel-review documents."""
    for path, expected_title in [
        ("/terms", "Terms of Service"),
        ("/privacy", "Privacy Policy"),
    ]:
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"
        body = resp.data.decode("utf-8")
        assert expected_title in body
        # Draft banner content (set in fiesta.signup.version.TOS_IS_DRAFT).
        assert "DRAFT" in body
        assert "Lanka.tax legal review" in body
        # Feedback email surfaced.
        assert "mahesh@yogarajan.com" in body
