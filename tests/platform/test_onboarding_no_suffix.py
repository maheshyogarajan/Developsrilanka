"""D8 (2026-05-27) — /onboarding (no suffix) routing regression suite.

Pre-D8 behaviour: `@app.route('/onboarding')` was guarded by
`@login_required`, so an anonymous visitor was bounced to
`/login?next=%2Fonboarding`. After login the user landed back at
`/onboarding`, which then ran the G4 router and 302'd them to
`/onboarding/welcome` — wasted hop.

Post-D8 behaviour (this suite):
  - Authenticated user (any persona) GET /onboarding → 302 to
    /onboarding/welcome (existing behaviour, unchanged).
  - Anonymous user GET /onboarding → 302 to /login?next=/onboarding/welcome
    (so the post-login round-trip lands at the right step directly).
  - Anonymous user POST /onboarding → 302 to /login (no `next=` value
    mandated, but the response MUST be a redirect to the login flow;
    we never allow a POST through to the legacy business-org handler
    without auth).

This locks the contract so a future refactor that re-adds the
`@login_required` decorator without thinking about the redirect target
is caught immediately.
"""
from __future__ import annotations

from urllib.parse import urlsplit, parse_qs

from tests.platform.conftest import login_as


def _redirect_target(response):
    """Return (path, next_param) tuple from a 302 Location header."""
    assert response.status_code == 302, (
        f"expected 302, got {response.status_code} "
        f"(body={response.get_data(as_text=True)[:200]!r})"
    )
    location = response.headers.get("Location", "")
    split = urlsplit(location)
    qs = parse_qs(split.query)
    nxt = qs.get("next", [None])[0]
    return split.path, nxt


# --------------------------------------------------------------------------- #
# D8.a — anonymous GET /onboarding lands at /login?next=/onboarding/welcome.
# --------------------------------------------------------------------------- #


def test_d8_anon_get_onboarding_redirects_to_login_with_welcome_next(client):
    """Anonymous GET /onboarding must 302 to /login?next=/onboarding/welcome
    so the post-login destination is the canonical G4 welcome step, not
    the legacy router URL. Pre-D8 the next= value was /onboarding which
    caused a wasted redirect hop after login."""
    resp = client.get("/onboarding", follow_redirects=False)
    path, nxt = _redirect_target(resp)
    assert path == "/login", (
        f"Anonymous GET /onboarding 302'd to {path!r}; expected /login."
    )
    assert nxt == "/onboarding/welcome", (
        f"Anonymous GET /onboarding produced next={nxt!r}; expected "
        "/onboarding/welcome so the post-login round-trip lands at the G4 "
        "welcome step directly (not back at /onboarding's router)."
    )


# --------------------------------------------------------------------------- #
# D8.b — anonymous POST /onboarding gets redirected to login (no write-through).
# --------------------------------------------------------------------------- #


def test_d8_anon_post_onboarding_redirected_to_login(client):
    """Anonymous POST /onboarding must redirect to the login flow — we
    NEVER let a write-path POST through without authentication. The
    Flask-Login unauthorized() helper produces this redirect."""
    resp = client.post(
        "/onboarding",
        data={"business_name": "AnonBypassAttempt"},
        follow_redirects=False,
    )
    assert resp.status_code == 302, (
        f"Anonymous POST /onboarding returned {resp.status_code}; "
        "expected 302 to /login. If this is 200, the write-path is "
        "accepting POSTs from anonymous sessions — a security defect."
    )
    location = resp.headers.get("Location", "")
    assert "/login" in location, (
        f"Anonymous POST /onboarding redirected to {location!r}; expected "
        "the login flow."
    )


# --------------------------------------------------------------------------- #
# D8.c — authenticated GET /onboarding lands at /onboarding/welcome.
# --------------------------------------------------------------------------- #


def test_d8_authed_get_onboarding_redirects_to_welcome(
    app, client, user_factory
):
    """Authenticated user mid-onboarding GET /onboarding must 302 to
    /onboarding/welcome (the G4 unified-flow welcome step). This is the
    pre-D8 behaviour and must be preserved by the manual auth gate."""
    user = user_factory(
        "d8_authed_midflow",
        persona=None,
        is_email_verified=True,
        onboarding_completed=False,
        income_sources=[],
    )
    login_as(client, user)

    resp = client.get("/onboarding", follow_redirects=False)
    path, _nxt = _redirect_target(resp)
    assert path == "/onboarding/welcome", (
        f"Authenticated mid-flow GET /onboarding 302'd to {path!r}; "
        "expected /onboarding/welcome (G4 unified-flow entry)."
    )


def test_d8_authed_sl_foreign_income_get_onboarding_redirects_to_welcome(
    app, client, user_factory
):
    """sl_foreign_income persona with empty income_sources gets the
    same /onboarding/welcome destination as the legacy router — both
    paths converge on the G4 welcome step."""
    user = user_factory(
        "d8_authed_fie_empty",
        persona="sl_foreign_income",
        is_email_verified=True,
        onboarding_completed=False,
        income_sources=[],
    )
    login_as(client, user)

    resp = client.get("/onboarding", follow_redirects=False)
    path, _nxt = _redirect_target(resp)
    assert path == "/onboarding/welcome", (
        f"sl_foreign_income+empty GET /onboarding 302'd to {path!r}; "
        "expected /onboarding/welcome."
    )
