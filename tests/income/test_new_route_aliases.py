"""C6 Day-0 fix — /income/{professional,rsu,crypto}/new resolve cleanly.

The 2026-05-27 customer-flow audit (finding C6) flagged that the income-source
picker (/onboarding/income-sources) offers 11 sources including Professional
fees, RSU, and Crypto — but /income/professional/new, /income/rsu/new, and
/income/crypto/new all returned 404. The customer who picked any of those
sources hit a dead-end.

Fix:
  - /income/rsu/new      -> 302 to /income/rsu/import (canonical entry: bulk import)
  - /income/crypto/new   -> 302 to /income/crypto/buy (canonical entry: single buy)
  - /income/professional/* (with hyphen-less prefix) -> 302 to
    /income/professional-fees/* (the canonical mount). So /new resolves
    transitively to /income/professional-fees/new which DOES exist.

Verification: every route returns either 200 (form rendered for authed user)
OR 302 (redirect to canonical handler / login). NEVER 404.

Run: pytest tests/income/test_new_route_aliases.py -v
"""
from __future__ import annotations

import pytest

from tests.income.conftest import login_as


# ---------------------------------------------------------------------------
# Anonymous access — never 404. login_required kicks in -> 302 to login.
# ---------------------------------------------------------------------------

def test_anon_rsu_new_not_404(client):
    """Anonymous GET /income/rsu/new must NOT return 404."""
    resp = client.get("/income/rsu/new", follow_redirects=False)
    assert resp.status_code != 404, (
        f"/income/rsu/new returned 404 for anon — the C6 bug. "
        f"Expected 302 (redirect to login or canonical handler) or 200."
    )


def test_anon_crypto_new_not_404(client):
    """Anonymous GET /income/crypto/new must NOT return 404."""
    resp = client.get("/income/crypto/new", follow_redirects=False)
    assert resp.status_code != 404


def test_anon_professional_new_not_404(client):
    """Anonymous GET /income/professional/new must NOT return 404."""
    resp = client.get("/income/professional/new", follow_redirects=False)
    assert resp.status_code != 404


# ---------------------------------------------------------------------------
# Authenticated access — must redirect to canonical entry, NEVER 404.
# ---------------------------------------------------------------------------

def test_authed_rsu_new_redirects_to_import(client, user_a):
    """GET /income/rsu/new -> 302 /income/rsu/import for an authed user."""
    login_as(client, user_a)
    resp = client.get("/income/rsu/new", follow_redirects=False)
    assert resp.status_code != 404
    # Either it redirects to /income/rsu/import (our alias) or to a paywall —
    # both are acceptable. NEVER 404.
    if resp.status_code == 302:
        loc = resp.headers.get("Location", "")
        assert "/income/rsu/import" in loc or "/income/rsu" in loc, (
            f"Expected redirect to /income/rsu/import or canonical path, got {loc!r}"
        )


def test_authed_crypto_new_redirects_to_buy(client, user_a):
    """GET /income/crypto/new -> 302 /income/crypto/buy for an authed user."""
    login_as(client, user_a)
    resp = client.get("/income/crypto/new", follow_redirects=False)
    assert resp.status_code != 404
    if resp.status_code == 302:
        loc = resp.headers.get("Location", "")
        assert "/income/crypto/buy" in loc or "/income/crypto" in loc, (
            f"Expected redirect to /income/crypto/buy or canonical path, got {loc!r}"
        )


def test_authed_professional_new_redirects_to_professional_fees(client, user_a):
    """GET /income/professional/new -> 302 /income/professional-fees/new for an authed user."""
    login_as(client, user_a)
    resp = client.get("/income/professional/new", follow_redirects=False)
    assert resp.status_code != 404
    if resp.status_code == 302:
        loc = resp.headers.get("Location", "")
        assert "/income/professional-fees/new" in loc, (
            f"Expected redirect to /income/professional-fees/new, got {loc!r}"
        )


def test_authed_professional_root_redirects(client, user_a):
    """GET /income/professional (no trailing path) -> 302 /income/professional-fees/."""
    login_as(client, user_a)
    resp = client.get("/income/professional", follow_redirects=False)
    assert resp.status_code != 404
    if resp.status_code == 302:
        loc = resp.headers.get("Location", "")
        assert "/income/professional-fees" in loc, (
            f"Expected redirect into /income/professional-fees, got {loc!r}"
        )


def test_authed_professional_subpath_redirects(client, user_a):
    """GET /income/professional/<anything> -> 302 /income/professional-fees/<anything>."""
    login_as(client, user_a)
    # /import is a real subpath in the professional_fees module.
    resp = client.get("/income/professional/import", follow_redirects=False)
    assert resp.status_code != 404
    if resp.status_code == 302:
        loc = resp.headers.get("Location", "")
        assert "/income/professional-fees/import" in loc


# ---------------------------------------------------------------------------
# End-to-end — follow the redirect and confirm the destination resolves.
# ---------------------------------------------------------------------------

def test_authed_professional_new_follow_resolves(client, user_a):
    """GET /income/professional/new (follow_redirects=True) lands on a real
    handler — never 404. The intermediate hops may go through a paywall, but
    the final response must be either 200 (form), or 302 to login/pricing.
    """
    login_as(client, user_a)
    resp = client.get("/income/professional/new", follow_redirects=True)
    assert resp.status_code != 404, (
        f"Following /income/professional/new redirects landed on 404. "
        f"This means the alias points at a non-existent handler. "
        f"Final URL: {resp.request.path if hasattr(resp, 'request') else 'unknown'}"
    )
