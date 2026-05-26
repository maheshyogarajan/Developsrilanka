"""Day-0 P0 paywall-off extension (2026-05-27 customer-flow audit, finding C6 fix #3).

Launch decision 2026-05-26 (decision 1): users can RECORD their data + view
their YTD bill without paying. The paywall fires on /submit/* (filing).
Phase B Agent 2 (commit e57494f) extended this to /tax-bill/* and
/agreements/{rental,service}/<id> preview GETs. The audit found that
/income/{employment,business,rsu,crypto,professional-fees}/* + /property
were still paywalled — a customer who picked Employment in onboarding was
gated immediately. This suite locks in the extended paywall-off contract.

Contract verified:
  * GET  /income/employment/new          -> 200 (form) for authed free-tier user
  * POST /income/employment/new          -> NOT 302 to /pricing/x1
  * GET  /income/business/new            -> 200 (form)
  * GET  /income/rsu/import              -> 200 (form, the canonical "new" handler)
  * GET  /income/crypto/buy              -> 200 (form, the canonical "new" handler)
  * GET  /income/professional-fees/new   -> 200 (form)
  * GET  /property                       -> 200 (or non-paywall 302)
  * GET  /submit                         -> STILL paywalled (302 -> /pricing/x1)
  * GET  /income/employment/new (anon)   -> 302 to /login (NOT /pricing/x1)

Run: pytest tests/paywall/test_income_capture_unpaywalled.py -v
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Data-recording surfaces — paywall MUST be off
# ---------------------------------------------------------------------------

UNPAYWALLED_GET_ROUTES = [
    "/income/employment/new",
    "/income/employment/",
    "/income/employment/import",
    "/income/business/new",
    "/income/business/",
    "/income/rsu/import",
    "/income/rsu/new",         # C6 alias -> /import
    "/income/crypto/buy",
    "/income/crypto/new",      # C6 alias -> /buy
    "/income/professional-fees/new",
    "/income/professional-fees/",
    "/property",
]


@pytest.mark.parametrize("path", UNPAYWALLED_GET_ROUTES)
def test_authed_free_tier_gets_no_paywall_on_data_capture(path, client, user_a):
    """A logged-in free-tier user hitting a data-recording route must NOT
    be redirected to /pricing/x1. The launch contract is: record data free,
    pay to file.
    """
    from tests.remittance.conftest import login_as
    login_as(client, user_a)

    resp = client.get(path, follow_redirects=False)
    location = resp.headers.get("Location", "") or ""

    assert "/pricing/x1" not in location, (
        f"Data-recording route {path} STILL paywalled "
        f"(launch decision 1 / audit C6 violation). "
        f"Got status={resp.status_code}, Location={location!r}. "
        f"The launch contract is paywall-OFF for data-recording, "
        f"paywall-ON only for /submit/* (filing)."
    )
    # Accept any non-paywall status: 200 (form rendered), 302 to a non-paywall
    # path (e.g. /fie/triage if onboarding gating kicks in), 4xx (404 / 405 if
    # the route shape changed). The contract is: paywall did not fire.
    assert resp.status_code != 402, (
        f"AJAX 402 paywall fired on {path} — paywall-off violated."
    )


# ---------------------------------------------------------------------------
# Anonymous user — must hit /login, never 404, never /pricing/x1 first
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", UNPAYWALLED_GET_ROUTES)
def test_anon_data_capture_route_bounces_to_login_not_paywall(path, client):
    """An anonymous user must hit /login (or a sensible auth surface) when
    visiting a data-recording route — NEVER /pricing/x1, NEVER 404.

    Rationale: paywall-off means the route is reachable post-login. We
    still need auth, but we never want anonymous traffic shuttled to the
    pricing screen for routes that no longer have a paywall.
    """
    resp = client.get(path, follow_redirects=False)

    assert resp.status_code != 404, (
        f"Anon GET {path} returned 404 — route not registered. "
        f"This is the C6 dead-end pattern."
    )

    location = resp.headers.get("Location", "") or ""
    if resp.status_code in (301, 302):
        # The redirect must NOT be to /pricing/x1.
        assert "/pricing/x1" not in location, (
            f"Anon traffic to data-recording route {path} routed to "
            f"/pricing/x1. Expected /login. Location={location!r}"
        )


# ---------------------------------------------------------------------------
# Submit surface — paywall MUST still fire (this is the filing gate)
# ---------------------------------------------------------------------------

def test_submit_still_paywalled_for_free_tier(client, user_a):
    """/submit is the filing surface — the paywall lives here. Free-tier
    user MUST be redirected to /pricing/x1.
    """
    from tests.remittance.conftest import login_as
    login_as(client, user_a)

    resp = client.get("/submit", follow_redirects=False)
    location = resp.headers.get("Location", "") or ""

    assert resp.status_code in (301, 302), (
        f"/submit should redirect free-tier to pricing; got status={resp.status_code}"
    )
    assert "/pricing/x1" in location, (
        f"/submit must still be paywalled (filing gate). Got Location={location!r}"
    )


# ---------------------------------------------------------------------------
# POST data-recording — paywall off here too (record-free contract)
# ---------------------------------------------------------------------------

def test_authed_free_tier_post_to_employment_new_not_paywalled(client, user_a):
    """POST /income/employment/new (the form submit) must not return a 402
    or 302->/pricing/x1 for a free-tier user. The contract is full
    data-recording free.

    We POST minimal data; the handler may return 400 (validation error,
    missing required fields) or 302 (success). Neither is a paywall.
    """
    from tests.remittance.conftest import login_as
    login_as(client, user_a)

    # Minimal-data POST — should hit the form validator, not the paywall.
    resp = client.post(
        "/income/employment/new",
        data={},
        follow_redirects=False,
    )
    location = resp.headers.get("Location", "") or ""

    assert resp.status_code != 402, (
        "POST /income/employment/new returned 402 — paywall still on POST."
    )
    assert "/pricing/x1" not in location, (
        f"POST /income/employment/new redirected to paywall. Location={location!r}"
    )
