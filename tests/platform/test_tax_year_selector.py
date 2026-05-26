"""BUG-B regression suite (Phase B Wave 1, 2026-05-26).

Locks the topbar tax-year selector contract:

  1. POST /api/fiesta/active-tax-year with JSON {tax_year: "2024/25"}
     returns 204 No Content and sets session['active_tax_year']="2024/25".
  2. Subsequent GET / renders with the selected year as the active YA
     (the dropdown's selected <option> matches the session value).
  3. Anonymous users can also POST (the dropdown also exists on anon
     surfaces); the session-key write is the only side effect.
  4. Form-encoded POST also works (defensive parser).
  5. Invalid year (free-text, garbage) is rejected 400.
  6. Year-form normalisation: long forms like "2024/2025" and S4 forms
     like "2024-25" collapse to the canonical short Y/Y form "2024/25"
     in the session.

Mirrors fixture conventions from tests/platform/test_universal_hub.py
(shared `client`, `user_factory`, `db_session`).
"""
from __future__ import annotations

import json

import pytest


def login_as(client, user):
    """Bypass the email/password form (mirrors the universal-hub helper)."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


# ---------------------------------------------------------------------------
# POST /api/fiesta/active-tax-year
# ---------------------------------------------------------------------------


def test_post_active_tax_year_sets_session_and_returns_204(client):
    """Canonical JSON POST writes session['active_tax_year'] + returns 204."""
    resp = client.post(
        "/api/fiesta/active-tax-year",
        data=json.dumps({"tax_year": "2024/25"}),
        content_type="application/json",
    )
    assert resp.status_code == 204, (
        f"expected 204, got {resp.status_code}: {resp.get_data(as_text=True)!r}"
    )
    with client.session_transaction() as sess:
        assert sess.get("active_tax_year") == "2024/25"


def test_post_active_tax_year_accepts_form_encoded(client):
    """Defensive parser: form-encoded body also works."""
    resp = client.post(
        "/api/fiesta/active-tax-year",
        data={"tax_year": "2023/24"},
    )
    assert resp.status_code == 204
    with client.session_transaction() as sess:
        assert sess.get("active_tax_year") == "2023/24"


def test_post_active_tax_year_normalises_long_form(client):
    """Long YYYY/YYYY form collapses to canonical Y/Y short form."""
    resp = client.post(
        "/api/fiesta/active-tax-year",
        data=json.dumps({"tax_year": "2024/2025"}),
        content_type="application/json",
    )
    assert resp.status_code == 204
    with client.session_transaction() as sess:
        assert sess.get("active_tax_year") == "2024/25"


def test_post_active_tax_year_normalises_s4_form(client):
    """S4 hyphen form ("YYYY-YY") collapses to Y/Y short form."""
    resp = client.post(
        "/api/fiesta/active-tax-year",
        data=json.dumps({"tax_year": "2024-25"}),
        content_type="application/json",
    )
    assert resp.status_code == 204
    with client.session_transaction() as sess:
        assert sess.get("active_tax_year") == "2024/25"


def test_post_active_tax_year_rejects_garbage(client):
    """Free-text / unrecognised forms are rejected with 400 — the session
    key is never written from untrusted input."""
    resp = client.post(
        "/api/fiesta/active-tax-year",
        data=json.dumps({"tax_year": "lol-not-a-year"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    with client.session_transaction() as sess:
        assert sess.get("active_tax_year") is None


def test_post_active_tax_year_rejects_missing_field(client):
    """Empty body / no tax_year field → 400."""
    resp = client.post(
        "/api/fiesta/active-tax-year",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Server-side rendering picks up the selected year
# ---------------------------------------------------------------------------


def test_home_renders_selected_year_after_post(client, user_factory):
    """After a successful POST, GET / shows the new YA as the selected
    <option> in the topbar dropdown. This is the end-to-end proof that
    the selector is no longer dead."""
    u = user_factory("ya_select_user", persona=None, role="user")
    login_as(client, u)
    # Flip to 2024/25.
    resp_post = client.post(
        "/api/fiesta/active-tax-year",
        data=json.dumps({"tax_year": "2024/25"}),
        content_type="application/json",
    )
    assert resp_post.status_code == 204
    # Render the hub. The topbar's <option value="2024/25" selected>
    # marker proves the resolver honored the session value.
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Either the <option ... selected> marker OR the YA pill text both
    # serve as proof; we check the option attribute which is the most
    # explicit.
    assert 'value="2024/25"' in body, "2024/25 option missing from select"
    # The selected attribute should be on the 2024/25 option, not the
    # default 2025/26 one. We do a loose substring check that handles
    # rendering whitespace variations.
    assert (
        '<option value="2024/25" selected' in body
        or 'value="2024/25" selected>' in body
        or 'value="2024/25"  selected' in body
    ), "2024/25 option should be marked selected after session write"


def test_home_falls_back_to_default_without_session_value(client, user_factory):
    """No session override → resolver falls back to current_sl_tax_year().

    The exact default depends on the fiscal calendar at test time (the
    SL year flips on 1 April). We assert the dropdown is rendered and
    the resolver returns SOMETHING — not None, not an empty string —
    when no session override is set. This is the negative half of the
    "selector wired up" contract: the resolver still respects its
    legacy fallback when the session is empty."""
    u = user_factory("ya_default_user", persona=None, role="user")
    login_as(client, u)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="fiesta-ya-select"' in body
    # No session override → no "active_tax_year" key written.
    with client.session_transaction() as sess:
        assert sess.get("active_tax_year") is None
    # Sanity: the in-process resolver returns a non-empty string.
    from app import inject_fiesta_hub_context
    # Need a request context to read session via flask globals.
    with client.application.test_request_context("/"):
        ctx = inject_fiesta_hub_context()
        resolved = ctx["current_sl_tax_year"]()
        assert resolved, "resolver returned empty"
        assert isinstance(resolved, str)
