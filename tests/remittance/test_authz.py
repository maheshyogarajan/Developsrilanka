"""
T1 — Wave H H1 council #1 critical fix: org/user isolation on remittance routes.

User A creates a RemittanceEntry. User B (different user) attempts to GET it.
MUST return 403/404 — never 200. Admin role no longer confers cross-user read
(council #1 verdict: admin needs a separate /admin/remittance/* route, not a
bypass on the user-facing one).
"""
from datetime import date
from decimal import Decimal

import pytest

from .conftest import login_as


def _create_entry(db_session, user):
    from remittance_models import RemittanceEntry, current_sl_tax_year
    e = RemittanceEntry(
        user_id=user.id,
        remittance_date=date(2026, 3, 15),
        foreign_currency="USD",
        foreign_amount=Decimal("1000.00"),
        tax_year=current_sl_tax_year(date(2026, 3, 15)),
    )
    db_session.add(e)
    db_session.commit()
    return e


def test_user_can_read_own_entry(client, db_session, user_a):
    entry = _create_entry(db_session, user_a)
    login_as(client, user_a)
    resp = client.get(f"/remittance/{entry.id}")
    assert resp.status_code == 200, f"Owner read should succeed, got {resp.status_code}"


def test_other_user_cannot_read_my_entry(client, db_session, user_a, user_b):
    entry = _create_entry(db_session, user_a)
    login_as(client, user_b)
    resp = client.get(f"/remittance/{entry.id}")
    assert resp.status_code in (403, 404), (
        f"Cross-user read MUST be denied. Got {resp.status_code} for user_b reading user_a's entry."
    )


def test_admin_cannot_read_other_user_entry_via_user_route(client, db_session, user_a, user_b):
    """Council #1 H1: admin role does NOT confer cross-user read on /remittance/<id>."""
    user_b.role = "admin"
    db_session.commit()
    entry = _create_entry(db_session, user_a)
    login_as(client, user_b)
    resp = client.get(f"/remittance/{entry.id}")
    assert resp.status_code in (403, 404), (
        f"Admin role MUST NOT bypass user isolation on /remittance/<id>. Got {resp.status_code}."
    )


def test_unauthenticated_read_blocked(client, db_session, user_a):
    entry = _create_entry(db_session, user_a)
    # Do NOT log in
    resp = client.get(f"/remittance/{entry.id}", follow_redirects=False)
    # Flask-Login redirects unauthenticated to /login
    assert resp.status_code in (302, 401, 403), (
        f"Unauthenticated read should redirect/deny. Got {resp.status_code}."
    )
