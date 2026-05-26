"""Phase B Wave 1 — auth-surface regression suite.

Locks two contracts shipped in `fix/auth-surface`:

  F1.5  /login, /register, /signup all render with the FIESTA design
        tokens applied. The contract is the `fiesta-tokenized` marker
        class on the page wrapper PLUS at least one FIESTA token
        reference (--paper / --clay / --ink / --forest / --display)
        appearing in the response body. login + register inherit the
        tokens via layout.html → fiesta-hub-design.css; signup loads
        fiesta-tokens.css + fiesta-hub-design.css explicitly (because
        empty_layout.html is the parent and does not pre-load them).

  F1.6  the `?next=` deep-link target survives the email-login POST.
        GET /login?next=/some/path renders a hidden `next` input
        carrying the value. POST /email_login with action=login +
        a same-origin `next` redirects to that path; an off-origin
        `next` (https:// or // or \\ or :// in path) is rejected and
        the default-index redirect path is taken.

Why these are kept as filesystem + HTTP tests:
  - Filesystem assertions (token + marker class) survive even when the
    DB fixture exhibits the FK-teardown noise documented in
    test_g5_visual_unification.py.
  - HTTP assertions (next-param survival) need the real Flask app +
    user_factory; they piggyback on the same `client` fixture all
    F-Platform-* tests use.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest
from werkzeug.security import generate_password_hash


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATES = _REPO_ROOT / "templates"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

# Token names that the FIESTA design system loads through fiesta-tokens.css
# and fiesta-hub-design.css. We assert one of them appears in each auth
# template — the marker class is the structural contract, the token
# reference is the visual binding contract.
_FIESTA_TOKEN_HINTS = (
    "--paper",
    "--clay",
    "--ink",
    "--forest",
    "--display",
)


def _read_template(rel_path: str) -> str:
    return (_TEMPLATES / rel_path).read_text(encoding="utf-8", errors="ignore")


def _redirect_path(response) -> str:
    """Extract the path component of a 302 Location header."""
    assert response.status_code == 302, (
        f"expected 302, got {response.status_code} "
        f"(body={response.get_data(as_text=True)[:200]!r})"
    )
    location = response.headers.get("Location", "")
    return urlsplit(location).path


def _has_fiesta_token_reference(body: str) -> bool:
    return any(token in body for token in _FIESTA_TOKEN_HINTS)


# --------------------------------------------------------------------------- #
# F1.5 — design-token contracts (filesystem + HTTP rendering)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "rel_path",
    [
        "login.html",
        "register.html",
        "signup.html",
    ],
)
def test_f1_5_auth_template_carries_fiesta_marker_class(rel_path):
    """Each of the 3 auth templates emits the `fiesta-tokenized` marker
    class on its top-level wrapper. The marker is the structural contract
    asserted at runtime; removing it would silently uncouple the auth
    surface from the design system."""
    src = _read_template(rel_path)
    assert "fiesta-tokenized" in src, (
        f"{rel_path} is missing the `fiesta-tokenized` marker class. "
        "If the F1.5 design-token binding was removed, restore it; the "
        "marker is the contract — see tests/platform/test_auth_surface.py."
    )


@pytest.mark.parametrize(
    "rel_path",
    [
        "login.html",
        "register.html",
        "signup.html",
    ],
)
def test_f1_5_auth_template_references_fiesta_tokens(rel_path):
    """Each auth template references at least one FIESTA design token
    name (--paper / --clay / --ink / --forest / --display) somewhere in
    its source. This is the visual-binding contract — even if styling
    moves to an external file later, the template should still bind to
    the token names."""
    src = _read_template(rel_path)
    assert _has_fiesta_token_reference(src), (
        f"{rel_path} does not reference any FIESTA design token "
        f"({', '.join(_FIESTA_TOKEN_HINTS)}). The visual-continuity "
        "contract with landing + hub is broken."
    )


def test_f1_5_signup_loads_fiesta_token_stylesheet():
    """signup.html extends `empty_layout.html`, which does NOT load the
    FIESTA design-token CSS. To stay on the design system, signup.html
    must include `fiesta-tokens.css` (and `fiesta-hub-design.css` for
    legacy aliases) inline in the content block."""
    src = _read_template("signup.html")
    assert "fiesta-tokens.css" in src, (
        "signup.html must load static/css/fiesta-tokens.css inline so "
        "it inherits the FIESTA design tokens. Parent empty_layout.html "
        "does not load them."
    )
    assert "fiesta-hub-design.css" in src, (
        "signup.html must also load static/css/fiesta-hub-design.css so "
        "the legacy --paper / --clay / --ink aliases referenced inline "
        "resolve to colour values."
    )


def test_f1_5_login_get_renders_with_fiesta_marker(client):
    r = client.get("/login")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "fiesta-tokenized" in body, (
        "GET /login must render with the `fiesta-tokenized` marker class."
    )
    assert _has_fiesta_token_reference(body), (
        "GET /login response must reference at least one FIESTA design "
        f"token ({', '.join(_FIESTA_TOKEN_HINTS)})."
    )


def test_f1_5_register_get_renders_with_fiesta_marker(client):
    r = client.get("/register")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "fiesta-tokenized" in body, (
        "GET /register must render with the `fiesta-tokenized` marker class."
    )
    assert _has_fiesta_token_reference(body), (
        "GET /register response must reference at least one FIESTA token."
    )


def test_f1_5_signup_get_renders_with_fiesta_marker(client):
    r = client.get("/signup")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "fiesta-tokenized" in body, (
        "GET /signup must render with the `fiesta-tokenized` marker class."
    )
    assert _has_fiesta_token_reference(body), (
        "GET /signup response must reference at least one FIESTA token "
        "(via the loaded fiesta-tokens.css / inline s2-* fallback chain)."
    )


# --------------------------------------------------------------------------- #
# F1.6 — `next` URL param survival through email-login POST
# --------------------------------------------------------------------------- #


def test_f1_6_login_get_with_next_emits_hidden_field(client):
    """GET /login?next=/remittance/new must render a hidden form field
    carrying the safelisted `next` value, so the POST handler can read
    it from `request.form`."""
    r = client.get("/login?next=/remittance/new")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # The hidden input is emitted from login.html via `{{ login_next or '' }}`.
    # Match flexibly on `name="next"` + the value.
    assert 'name="next"' in body, (
        "GET /login must emit a hidden `next` input so the value can be "
        "carried into the POST."
    )
    assert "/remittance/new" in body, (
        "GET /login?next=/remittance/new must echo the safelisted value "
        "into the hidden form field."
    )


def test_f1_6_login_get_rejects_offorigin_next(client):
    """An off-origin `next` (https://evil.com) must NOT make it into the
    hidden form field — the GET handler safelist drops it before render."""
    r = client.get("/login?next=https://evil.com")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "evil.com" not in body, (
        "GET /login must NOT echo off-origin `next` into the hidden input."
    )


def test_f1_6_email_login_post_redirects_to_next(client, user_factory):
    """Valid creds + same-origin `next` → POST /email_login 302s to the
    `next` path, not the default index."""
    user = user_factory(
        "f1_6_redirect",
        is_email_verified=True,
        onboarding_completed=True,
    )
    # The factory sets a known dummy password ("pytest-pw-not-real").
    # We need a real working password for the POST to authenticate, so
    # set it explicitly and commit before the test acts.
    from app import db
    user.password_hash = generate_password_hash("test-password-f1-6")
    db.session.commit()

    r = client.post(
        "/email_login",
        data={
            "email": user.email,
            "password": "test-password-f1-6",
            "action": "login",
            "next": "/remittance/new",
        },
        follow_redirects=False,
    )
    assert _redirect_path(r) == "/remittance/new", (
        "POST /email_login with same-origin next must redirect there, "
        f"got Location={r.headers.get('Location')!r}"
    )


def test_f1_6_email_login_post_rejects_offorigin_next(client, user_factory):
    """Off-origin `next` (https://evil.com) is rejected; redirect falls
    through to the default destination (index, not the external URL)."""
    user = user_factory(
        "f1_6_offorigin",
        is_email_verified=True,
        onboarding_completed=True,
    )
    from app import db
    user.password_hash = generate_password_hash("test-password-f1-6")
    db.session.commit()

    r = client.post(
        "/email_login",
        data={
            "email": user.email,
            "password": "test-password-f1-6",
            "action": "login",
            "next": "https://evil.com",
        },
        follow_redirects=False,
    )
    location = r.headers.get("Location", "")
    path = urlsplit(location).path
    # The redirect MUST NOT be the external URL.
    assert "evil.com" not in location, (
        f"Off-origin `next` was honoured — open-redirect vuln. "
        f"Location={location!r}"
    )
    # And the host part must be the test server, not evil.com.
    assert urlsplit(location).netloc in ("", "localhost", "127.0.0.1", "localhost.localdomain"), (
        f"Off-origin `next` produced an off-host Location header: {location!r}"
    )
    # The fallback should be the index (`url_for('index')` → /scan in this app).
    # We don't lock the exact fallback path — just assert it's NOT evil.
    assert path != "" and not path.startswith("//"), (
        f"Fallback path looks malformed: {path!r}"
    )


def test_f1_6_email_login_post_rejects_protocol_relative_next(client, user_factory):
    """Protocol-relative `next` (//evil.com/path) is a classic open-redirect
    vector. Safelist must drop it."""
    user = user_factory(
        "f1_6_proto_rel",
        is_email_verified=True,
        onboarding_completed=True,
    )
    from app import db
    user.password_hash = generate_password_hash("test-password-f1-6")
    db.session.commit()

    r = client.post(
        "/email_login",
        data={
            "email": user.email,
            "password": "test-password-f1-6",
            "action": "login",
            "next": "//evil.com/path",
        },
        follow_redirects=False,
    )
    location = r.headers.get("Location", "")
    assert "evil.com" not in location, (
        f"Protocol-relative `next` was honoured — open-redirect vuln. "
        f"Location={location!r}"
    )


def test_f1_6_email_login_post_root_next_redirects_to_root(client, user_factory):
    """next=/ is a valid same-origin redirect — must be honoured."""
    user = user_factory(
        "f1_6_root",
        is_email_verified=True,
        onboarding_completed=True,
    )
    from app import db
    user.password_hash = generate_password_hash("test-password-f1-6")
    db.session.commit()

    r = client.post(
        "/email_login",
        data={
            "email": user.email,
            "password": "test-password-f1-6",
            "action": "login",
            "next": "/",
        },
        follow_redirects=False,
    )
    assert _redirect_path(r) == "/", (
        f"POST /email_login with next=/ must redirect to /, "
        f"got Location={r.headers.get('Location')!r}"
    )
