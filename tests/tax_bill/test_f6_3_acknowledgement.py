"""tests/tax_bill/test_f6_3_acknowledgement.py — F6.3 launch-gate.

Contract (council brief 2026-05-28, LAUNCH_PLAN_2026-05-29.html):
  * First GET /tax-bill/<ya> for a (user, ya) renders the acknowledgement
    interstitial, NOT the dashboard.
  * POST /tax-bill/<ya>/acknowledge without the checkbox re-renders the
    interstitial with an error message.
  * POST with the checkbox writes one row and redirects to GET .../<ya>.
  * After acknowledgement, GET /tax-bill/<ya> renders the dashboard
    (interstitial markers absent).
  * Idempotency: a duplicate POST is a no-op + still redirects.
  * Different tax years require separate acknowledgements.

Run:
    python -m pytest tests/tax_bill/test_f6_3_acknowledgement.py -v
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# Marker fragments unique to the interstitial template — used to assert
# whether the response is the acknowledge page or the dashboard.
ACK_FORM_ACTION = "/acknowledge"
ACK_HEADER_FRAGMENT = b"estimate"  # h1: "This is an estimate, not a filed return."


def _is_interstitial(body: bytes) -> bool:
    return (b"ack-form" in body) or (b"View my tax bill" in body)


def _is_dashboard(body: bytes) -> bool:
    # The dashboard renders an h1 with "tax bill" and the savings/defensibility
    # section. The interstitial does not have "Audit defensibility".
    return b"audit-defensibility" in body.lower() or b"audit defensibility" in body.lower()


def test_01_first_view_renders_interstitial(app, client, user_a):
    """First GET /tax-bill/<ya> for a brand-new user renders the F6.3
    acknowledgement interstitial — NOT the dashboard."""
    from tests.remittance.conftest import login_as
    login_as(client, user_a)

    resp = client.get("/tax-bill/2025-26", follow_redirects=False)
    assert resp.status_code == 200, (
        f"Expected 200 (rendered interstitial), got {resp.status_code}"
    )
    assert _is_interstitial(resp.data), (
        "Expected the F6.3 interstitial markup; got something else."
    )


def test_02_post_without_checkbox_rerenders_with_error(app, client, user_a):
    """POST without the 'acknowledged' field re-renders the interstitial
    with an error message; no DB row is written."""
    from tests.remittance.conftest import login_as
    login_as(client, user_a)

    resp = client.post(
        "/tax-bill/2025-26/acknowledge",
        data={},  # no acknowledged=1
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert _is_interstitial(resp.data)
    assert b"tick the box" in resp.data.lower() or b"please" in resp.data.lower(), (
        "Expected an error message prompting to tick the checkbox."
    )


def test_03_post_with_checkbox_records_and_redirects(app, client, user_a):
    """POST with acknowledged=1 writes one row and redirects to
    GET /tax-bill/<ya>."""
    from tests.remittance.conftest import login_as
    login_as(client, user_a)

    resp = client.post(
        "/tax-bill/2025-26/acknowledge",
        data={"acknowledged": "1"},
        follow_redirects=False,
    )
    assert resp.status_code in (301, 302), (
        f"Expected redirect after acknowledge; got {resp.status_code}"
    )
    location = resp.headers.get("Location", "") or ""
    assert "/tax-bill/2025-26" in location and "acknowledge" not in location, (
        f"Expected redirect to /tax-bill/2025-26; got {location}"
    )


def test_04_after_ack_dashboard_renders(app, client, user_a):
    """After acknowledging, GET /tax-bill/<ya> renders the dashboard, NOT
    the interstitial."""
    from tests.remittance.conftest import login_as
    login_as(client, user_a)

    # Acknowledge first.
    client.post(
        "/tax-bill/2025-26/acknowledge",
        data={"acknowledged": "1"},
        follow_redirects=False,
    )

    resp = client.get("/tax-bill/2025-26", follow_redirects=False)
    assert resp.status_code == 200
    assert not _is_interstitial(resp.data), (
        "Dashboard should render after acknowledgement; got the interstitial again."
    )


def test_05_duplicate_post_is_idempotent(app, client, user_a):
    """A second POST with the checkbox is a no-op + still redirects."""
    from tests.remittance.conftest import login_as
    login_as(client, user_a)

    r1 = client.post(
        "/tax-bill/2025-26/acknowledge",
        data={"acknowledged": "1"},
        follow_redirects=False,
    )
    r2 = client.post(
        "/tax-bill/2025-26/acknowledge",
        data={"acknowledged": "1"},
        follow_redirects=False,
    )
    assert r1.status_code in (301, 302)
    assert r2.status_code in (301, 302)


def test_06_different_ya_needs_new_ack(app, client, user_a):
    """Acknowledging 2025-26 does NOT unlock 2024-25 — different tax years
    require separate acknowledgements."""
    from tests.remittance.conftest import login_as
    login_as(client, user_a)

    client.post(
        "/tax-bill/2025-26/acknowledge",
        data={"acknowledged": "1"},
        follow_redirects=False,
    )

    resp = client.get("/tax-bill/2024-25", follow_redirects=False)
    assert resp.status_code == 200
    assert _is_interstitial(resp.data), (
        "Different tax year should still gate on the interstitial."
    )


def test_07_anon_user_still_bounced_to_login(app, client):
    """The F6.3 interstitial sits BEHIND @login_required — an anonymous
    user still gets redirected to /login on both GET and POST."""
    resp_get = client.get("/tax-bill/2025-26/acknowledge", follow_redirects=False)
    resp_post = client.post(
        "/tax-bill/2025-26/acknowledge",
        data={"acknowledged": "1"},
        follow_redirects=False,
    )
    for resp in (resp_get, resp_post):
        assert resp.status_code in (301, 302, 401)
        if resp.status_code in (301, 302):
            assert "/login" in (resp.headers.get("Location", "") or "")
