"""
Tests for /admin/fiesta-states — the FIESTA-side Markov tracker admin view.

Coverage:
  1. Admin GET /admin/fiesta-states/ returns 200, renders the template.
  2. Non-admin GET /admin/fiesta-states/ returns 403 (admin gate enforced).
  3. Anonymous GET /admin/fiesta-states/ redirects to login.
  4. Pure derive_state_for_user(): a fresh user with no data → S00.
  5. Pure derive_state_for_user(): user with active subscription but no
     profile → S01.
  6. Pure derive_state_for_user(): user with complete profile, no income → S03.
  7. Pure derive_state_for_user(): user with income_count > 0 → S04 (or higher).
  8. Pure derive_state_for_user(): user with attested Submission → S12.
  9. JSON endpoint /admin/fiesta-states/data returns 200 with the
     state_distribution dict.

The pure-function tests do NOT need a DB — they pass mock inputs straight
into ``derive_state_for_user``.

The HTTP tests reuse the validated fixtures from tests/fiesta_admin/conftest.py
which speak to the live Neon DB.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

# Pull the validated app + admin_user + non_admin_user + login_as fixtures.
from tests.fiesta_admin.conftest import (  # noqa: F401
    app,
    client,
    db_session,
    admin_user,
    non_admin_user,
    login_as,
)


# --------------------------------------------------------------------------- #
# Pure unit tests — no DB required.
# --------------------------------------------------------------------------- #
def _bare_user(income_sources=None):
    """Build a minimal stand-in for the User model. Only attributes the
    state-derivation function reads need to be present."""
    return SimpleNamespace(
        id=12345,
        income_sources=income_sources or [],
    )


def _bare_profile(nic="123456789V", city="Colombo",
                  bank_account_number="0012345678"):
    return SimpleNamespace(
        nic=nic,
        city=city,
        bank_account_number=bank_account_number,
    )


def _bare_submission(status, final_tax_payable_lkr=None):
    return SimpleNamespace(
        status=status,
        final_tax_payable_lkr=final_tax_payable_lkr,
    )


def test_state_pure_fresh_user_is_s00():
    """A user with no profile, no payment, no data → S00."""
    from fiesta.admin.fiesta_states_routes import derive_state_for_user
    state = derive_state_for_user(_bare_user())
    assert state == "S00"


def test_state_pure_paid_no_profile_is_s01():
    """User with active subscription but no profile → S01."""
    from fiesta.admin.fiesta_states_routes import derive_state_for_user
    state = derive_state_for_user(
        _bare_user(),
        profile=None,
        has_active_subscription=True,
    )
    assert state == "S01"


def test_state_pure_profile_complete_no_income_is_s03():
    """Profile complete, no income evidence → S03 (docs collecting)."""
    from fiesta.admin.fiesta_states_routes import derive_state_for_user
    state = derive_state_for_user(
        _bare_user(),
        profile=_bare_profile(),
        has_active_subscription=True,
    )
    assert state == "S03"


def test_state_pure_profile_incomplete_nic_blank():
    """Profile row exists but NIC is empty → not 'complete' → S01."""
    from fiesta.admin.fiesta_states_routes import derive_state_for_user
    state = derive_state_for_user(
        _bare_user(),
        profile=_bare_profile(nic=""),
        has_active_subscription=True,
    )
    assert state == "S01"


def test_state_pure_income_row_is_s04_or_higher():
    """At least one income row → at least S04."""
    from fiesta.admin.fiesta_states_routes import derive_state_for_user
    state = derive_state_for_user(
        _bare_user(),
        profile=_bare_profile(),
        has_active_subscription=True,
        income_count=1,
    )
    # S04 if no employment proxy, no bank, no remittance-with-doc.
    assert state == "S04"


def test_state_pure_employment_income_is_s05():
    """Employment income proxy fires S05."""
    from fiesta.admin.fiesta_states_routes import derive_state_for_user
    state = derive_state_for_user(
        _bare_user(),
        profile=_bare_profile(),
        has_active_subscription=True,
        income_count=1,
        employment_income_count=1,
    )
    assert state == "S05"


def test_state_pure_bank_statement_is_s06():
    """Bank statement uploaded → S06."""
    from fiesta.admin.fiesta_states_routes import derive_state_for_user
    state = derive_state_for_user(
        _bare_user(),
        profile=_bare_profile(),
        has_active_subscription=True,
        income_count=1,
        employment_income_count=1,
        bank_statement_count=1,
    )
    assert state == "S06"


def test_state_pure_remittance_with_doc_is_s07():
    """Foreign remittance with attached source doc → S07."""
    from fiesta.admin.fiesta_states_routes import derive_state_for_user
    state = derive_state_for_user(
        _bare_user(),
        profile=_bare_profile(),
        has_active_subscription=True,
        income_count=2,
        employment_income_count=1,
        bank_statement_count=1,
        remittance_count=1,
        remittance_with_doc_count=1,
    )
    assert state == "S07"


def test_state_pure_assets_or_liabilities_is_s09():
    """An AssetEntry or LiabilityEntry row → S09."""
    from fiesta.admin.fiesta_states_routes import derive_state_for_user
    state = derive_state_for_user(
        _bare_user(),
        profile=_bare_profile(),
        has_active_subscription=True,
        income_count=2,
        asset_or_liability_count=1,
    )
    assert state == "S09"


def test_state_pure_submission_preparing_with_tax_bill_is_s10():
    """Submission row in 'preparing' with a tax bill snapshot → S10."""
    from fiesta.admin.fiesta_states_routes import derive_state_for_user
    sub = _bare_submission("preparing", final_tax_payable_lkr=12345)
    state = derive_state_for_user(
        _bare_user(),
        profile=_bare_profile(),
        has_active_subscription=True,
        income_count=2,
        submission=sub,
    )
    assert state == "S10"


def test_state_pure_submission_preparing_no_tax_bill_falls_through():
    """Submission in 'preparing' WITHOUT a tax bill snapshot — falls through
    to the upstream classification (NOT S10)."""
    from fiesta.admin.fiesta_states_routes import derive_state_for_user
    sub = _bare_submission("preparing", final_tax_payable_lkr=None)
    state = derive_state_for_user(
        _bare_user(),
        profile=_bare_profile(),
        has_active_subscription=True,
        income_count=2,
        employment_income_count=1,
        submission=sub,
    )
    # falls through to S05 (employment income proxy)
    assert state == "S05"


def test_state_pure_submission_attested_is_s12():
    """Submission in 'attested' → S12."""
    from fiesta.admin.fiesta_states_routes import derive_state_for_user
    sub = _bare_submission("attested")
    state = derive_state_for_user(
        _bare_user(),
        profile=_bare_profile(),
        has_active_subscription=True,
        income_count=2,
        submission=sub,
    )
    assert state == "S12"


def test_state_pure_submission_filed_is_s14():
    """Submission in 'customer-filed-on-ird' → S14 (terminal)."""
    from fiesta.admin.fiesta_states_routes import derive_state_for_user
    sub = _bare_submission("customer-filed-on-ird")
    state = derive_state_for_user(
        _bare_user(),
        profile=_bare_profile(),
        has_active_subscription=True,
        income_count=2,
        submission=sub,
    )
    assert state == "S14"


# --------------------------------------------------------------------------- #
# HTTP / Flask integration tests — these speak to the live Neon DB.
# --------------------------------------------------------------------------- #
def test_admin_get_returns_200_and_renders(app, client, admin_user, login_as):
    """An admin GET /admin/fiesta-states/ returns 200 and renders content."""
    # Bust the module cache so a previous test run's payload doesn't shadow.
    from fiesta.admin.fiesta_states_routes import invalidate_cache
    invalidate_cache()

    login_as(client, admin_user)
    resp = client.get("/admin/fiesta-states/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "FIESTA Markov States" in body
    # All 15 v1 states should be present
    for n in range(0, 15):
        code = f"S{n:02d}"
        assert code in body, f"state code {code} missing from rendered page"


def test_admin_get_no_slash_also_works(app, client, admin_user, login_as):
    """The route registers both '' and '/' — confirm the no-slash form too."""
    from fiesta.admin.fiesta_states_routes import invalidate_cache
    invalidate_cache()

    login_as(client, admin_user)
    # follow_redirects=True so trailing-slash redirect (if any) lands.
    resp = client.get("/admin/fiesta-states", follow_redirects=True)
    assert resp.status_code == 200


def test_non_admin_gets_403(app, client, non_admin_user, login_as):
    """A non-admin logged-in user gets 403 (HTML or JSON)."""
    login_as(client, non_admin_user)
    resp = client.get("/admin/fiesta-states/")
    assert resp.status_code == 403


def test_anonymous_redirected_to_login(app, client):
    """An anonymous user gets a 302 to login (admin_required behaviour)."""
    resp = client.get("/admin/fiesta-states/", follow_redirects=False)
    # admin_required: anonymous → redirect to login
    assert resp.status_code in (301, 302)


def test_json_endpoint_returns_state_distribution(app, client, admin_user, login_as):
    """GET /admin/fiesta-states/data returns the state_distribution JSON."""
    from fiesta.admin.fiesta_states_routes import invalidate_cache
    invalidate_cache()

    login_as(client, admin_user)
    resp = client.get("/admin/fiesta-states/data?nocache=1")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert "state_distribution" in payload
    # All 15 v1 states must be keys in the distribution dict (even at zero).
    for n in range(0, 15):
        code = f"S{n:02d}"
        assert code in payload["state_distribution"], (
            f"state code {code} missing from JSON state_distribution"
        )
    # Total users >= 0; type is int
    assert isinstance(payload["total_users"], int)
    assert payload["total_users"] >= 0
