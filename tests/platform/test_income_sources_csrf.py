"""tests/platform/test_income_sources_csrf.py — D-N1 polish.

D-N1 (2026-05-27): when Playwright's `requestContext.post()` (or any
non-browser caller) hits POST /api/fiesta/income-sources WITHOUT an
X-CSRFToken header, the request previously appeared to hang while
Flask-WTF's CSRF machinery and the body-parsing path raced. The
endpoint now returns a clean, predictable JSON 400 with
`error: "missing_csrf_token"` BEFORE any DB work, so out-of-process
callers get a deterministic recovery signal.

This test deliberately enables WTF_CSRF_ENABLED on the shared Flask app
for the duration of one assertion (the platform conftest disables CSRF
globally so the rest of the suite can keep using `client.post` directly).

Run:
    cd C:/Users/mahes/fiesta_phase_a/worktrees/dn-polish
    DATABASE_URL=sqlite:///:memory: python -m pytest tests/platform/test_income_sources_csrf.py -v
"""
from __future__ import annotations

import json

import pytest


def login_as(client, user):
    """Bypass the email/password form by setting the Flask-Login cookie."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


# ---------------------------------------------------------------------------
# Fixture: flip CSRF on for the lifetime of one test (then restore).
# Mirrors the pattern used elsewhere in the suite that toggles app config.
# ---------------------------------------------------------------------------


@pytest.fixture
def csrf_enabled_app(app):
    """Temporarily enable WTF_CSRF_ENABLED for the duration of the test."""
    prev = app.config.get("WTF_CSRF_ENABLED", False)
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        yield app
    finally:
        app.config["WTF_CSRF_ENABLED"] = prev


def test_post_returns_clean_400_when_csrf_token_header_missing(
    csrf_enabled_app, client, user_factory
):
    """D-N1 regression: JSON POST without X-CSRFToken must return 400 with
    a predictable JSON shape — NOT hang and NOT return an opaque HTML error
    page.

    Pre-fix: out-of-process callers (Playwright requestContext.post)
    appeared to hang because Flask-WTF's CSRFError handler returned a
    non-JSON body the caller didn't know how to drain.
    Post-fix: clean JSON 400, `error: "missing_csrf_token"`, NO DB
    write attempted, fast (no hang).
    """
    u = user_factory("dn1_csrf_check", persona=None, role="user")
    login_as(client, u)

    resp = client.post(
        "/api/fiesta/income-sources",
        data=json.dumps({"income_sources": ["employment_lkr"]}),
        content_type="application/json",
        # Deliberately NO X-CSRFToken header.
    )

    # Must NOT hang (test-client raises if the request never completes).
    # Must return JSON 400 with the documented shape.
    assert resp.status_code == 400, (
        f"Expected 400 (missing X-CSRFToken), got {resp.status_code}. "
        f"Body: {resp.get_data(as_text=True)[:300]}"
    )
    body = json.loads(resp.get_data(as_text=True))
    assert body.get("ok") is False
    assert body.get("error") == "missing_csrf_token", (
        f"D-N1 contract: error code must be 'missing_csrf_token' so "
        f"clients can switch on it. Got: {body!r}"
    )
    # Human-readable message is present so a developer reading the
    # response in DevTools immediately understands the fix.
    assert "X-CSRFToken" in (body.get("message") or ""), (
        f"D-N1 contract: response message must mention X-CSRFToken so the "
        f"developer knows the fix. Got: {body!r}"
    )


def test_post_accepts_json_with_csrf_token_header(
    csrf_enabled_app, client, user_factory
):
    """Sanity counterpart to the missing-token test: when the header IS
    present (even with a stub value — the real CSRF validation may
    require a session-paired token in production, but the D-N1 gate is
    presence-only and runs FIRST), the request progresses past the
    D-N1 guard. We assert that we no longer get the
    missing_csrf_token shape.
    """
    u = user_factory("dn1_csrf_present", persona=None, role="user")
    login_as(client, u)

    # Fetch a real CSRF token from the app so we pass the full Flask-WTF
    # validation. The /api/csrf-token endpoint isn't guaranteed across
    # versions, so we use the canonical generate_csrf helper instead.
    from flask_wtf.csrf import generate_csrf
    with csrf_enabled_app.test_request_context():
        with client.session_transaction() as _sess:
            # Make sure the session is initialised so generate_csrf can
            # bind the token to it.
            _sess.setdefault("_id", "stub")
        token = generate_csrf()

    resp = client.post(
        "/api/fiesta/income-sources",
        data=json.dumps({"income_sources": ["employment_lkr"]}),
        content_type="application/json",
        headers={"X-CSRFToken": token},
    )

    # The D-N1 guard must NOT fire when the header is present. The
    # request might still legitimately fail Flask-WTF's deeper token
    # validation (different status code / different body), but it must
    # NOT be the D-N1 missing_csrf_token shape.
    body_text = resp.get_data(as_text=True)
    if resp.status_code == 400:
        try:
            body = json.loads(body_text)
            assert body.get("error") != "missing_csrf_token", (
                "D-N1 guard must not fire when X-CSRFToken header is "
                f"present. Body: {body!r}"
            )
        except json.JSONDecodeError:
            # Non-JSON 400 means deeper CSRF validation rejected the
            # token — that's fine, not the D-N1 path.
            pass
