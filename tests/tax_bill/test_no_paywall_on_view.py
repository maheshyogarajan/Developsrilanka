"""tests/tax_bill/test_no_paywall_on_view.py — launch 2026-05-26 paywall-off contract.

Phase B Wave 1 — Fix 3 (Decision 1 implementation, 2026-05-26).

CONTRACT
--------
On the FIESTA launch build, the paywall is OFF on:
  * GET /tax-bill/                              (index redirect)
  * GET /tax-bill/<tax_year>                    (the actual bill view)
  * GET /tax-bill/<tax_year>/breakdown          (JSON dump)
  * GET /agreements/rental/<property_id>        (rental preview)
  * GET /agreements/service/<sp_id>             (service preview)

The paywall stays ON for:
  * GET /submit  /  /submit/<tax_year>          (the filing surface)
  * POST /submit/attest, /submit/.../mark-filed (filing actions)
  * POST /agreements/{rental,service}/generate  (PDF generation)
  * GET  /agreements/{rental,service}/<id>/download  (downloading the PDF)

Rationale: users should be able to RECORD their data and SEE their computed
bill / generated-agreement preview without paying. The paywall fires when
they go to FILE the return on IRD (the /submit surface) or to DOWNLOAD the
generated PDF (the deliverable that has actual sale-able value).

This suite verifies the launch contract:

  01. A free-tier authenticated user can GET /tax-bill/2025-26  (200, not 302).
  02. A free-tier authenticated user can GET /tax-bill/  (302 to the dated bill,
      NOT to /pricing/x1).
  03. A free-tier authenticated user can GET /tax-bill/2025-26/breakdown
      (200 JSON, not 402 / 302).
  04. A free-tier authenticated user GET /submit (or /submit/<ty>) STILL
      hits the paywall (regression guard — fix 3 must not over-correct).
  05. The de-gated tax-bill GETs are still @login_required (anon hits get
      auth-redirect, NOT a 200).

Run:
    python -m pytest tests/tax_bill/test_no_paywall_on_view.py -v
"""
from __future__ import annotations

import pathlib
import sys

import pytest

# Make the worktree root importable when invoked from repo root.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Helper: assert that a response did NOT redirect to /pricing/x1
# ---------------------------------------------------------------------------
def _assert_not_paywalled(resp, path: str):
    """A response is 'paywalled' if it's a 302 to /pricing/* OR a 402.

    We accept ANY other status (200, other 3xx, 404 if downstream record is
    missing, etc.) — the contract under test is solely 'paywall did not fire'.
    """
    location = resp.headers.get("Location", "") or ""
    assert resp.status_code != 402, (
        f"Free-tier GET {path} returned 402 (paywall JSON). Launch contract: "
        f"this route should not be paywalled."
    )
    if resp.status_code in (301, 302):
        assert "/pricing/x1" not in location and "/pricing?" not in location, (
            f"Free-tier GET {path} redirected to pricing ({location}). "
            f"Launch contract: this route should not be paywalled."
        )


# ---------------------------------------------------------------------------
# 01. /tax-bill/<tax_year> is reachable for a free-tier user
# ---------------------------------------------------------------------------
def test_01_free_tier_can_view_dated_tax_bill(app, client, user_a):
    """A logged-in unpaid user GETs /tax-bill/2025-26 and the paywall does
    NOT fire. The downstream route may return 200 (empty bill) or some
    other non-paywall status; we don't care about the body, only that
    the gate didn't intercept."""
    from tests.remittance.conftest import login_as
    login_as(client, user_a)

    resp = client.get("/tax-bill/2025-26", follow_redirects=False)
    _assert_not_paywalled(resp, "/tax-bill/2025-26")


# ---------------------------------------------------------------------------
# 02. /tax-bill/ index redirect: also free for unpaid users
# ---------------------------------------------------------------------------
def test_02_free_tier_can_hit_tax_bill_index_redirect(app, client, user_a):
    """The bare /tax-bill/ route redirects to /tax-bill/<latest_ty>. Neither
    the redirect target NOR the redirect itself should be /pricing/."""
    from tests.remittance.conftest import login_as
    login_as(client, user_a)

    resp = client.get("/tax-bill/", follow_redirects=False)
    _assert_not_paywalled(resp, "/tax-bill/")
    # Must be SOME response (200 or 302 to the dated bill). Any non-2xx/3xx
    # would mean the route broke; flag that.
    assert resp.status_code in (200, 301, 302), (
        f"/tax-bill/ returned unexpected status {resp.status_code}"
    )
    # If we got a 3xx, the Location should be the dated tax-bill URL.
    if resp.status_code in (301, 302):
        location = resp.headers.get("Location", "") or ""
        assert "/tax-bill/" in location, (
            f"/tax-bill/ should redirect to a dated /tax-bill/<ty>; "
            f"got Location={location}"
        )


# ---------------------------------------------------------------------------
# 03. /tax-bill/<ty>/breakdown also free for unpaid users
# ---------------------------------------------------------------------------
def test_03_free_tier_can_get_breakdown_json(app, client, user_a):
    """The JSON-breakdown endpoint backs the audit-pack PDF and tests.
    Same launch logic — viewing the data shouldn't require payment."""
    from tests.remittance.conftest import login_as
    login_as(client, user_a)

    resp = client.get(
        "/tax-bill/2025-26/breakdown",
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )
    _assert_not_paywalled(resp, "/tax-bill/2025-26/breakdown")


# ---------------------------------------------------------------------------
# 04. /submit STILL paywalled (regression guard)
# ---------------------------------------------------------------------------
def test_04_free_tier_still_paywalled_on_submit(app, client, user_a):
    """Regression guard: removing the paywall from /tax-bill must NOT
    accidentally remove it from /submit. /submit is the filing surface and
    remains the paywall trigger per launch decision 2026-05-26."""
    from tests.remittance.conftest import login_as
    login_as(client, user_a)

    resp = client.get("/submit", follow_redirects=False)

    # Submit gate fires either as a 302 to /pricing/x1 (browser) or as a
    # 402 (JSON / AJAX). We sent no Accept: application/json so we expect
    # a 302.
    assert resp.status_code in (301, 302), (
        f"/submit should redirect a free-tier user to the paywall; "
        f"got status={resp.status_code}"
    )
    location = resp.headers.get("Location", "") or ""
    assert "/pricing/x1" in location, (
        f"/submit should redirect to /pricing/x1 for free-tier users; "
        f"got Location={location}"
    )


# ---------------------------------------------------------------------------
# 05. Anon users still bounced off (no paywall removal == no auth removal)
# ---------------------------------------------------------------------------
def test_05_anon_still_required_to_login_on_tax_bill(app, client):
    """Removing @paywall_required must NOT remove @login_required. An anon
    GET /tax-bill/2025-26 should bounce to /login (or /auth), NOT render
    a tax bill as anonymous."""
    resp = client.get("/tax-bill/2025-26", follow_redirects=False)

    # Either 302 to login (Flask-Login default) or 401.
    assert resp.status_code in (301, 302, 401), (
        f"Anon /tax-bill/2025-26 should redirect to login or 401; "
        f"got status={resp.status_code}"
    )
    if resp.status_code in (301, 302):
        location = resp.headers.get("Location", "") or ""
        # Should not be a tax-bill or pricing URL — should be auth.
        assert ("/login" in location.lower()
                or "/auth" in location.lower()), (
            f"Anon /tax-bill/2025-26 should bounce to login; "
            f"got Location={location}"
        )
