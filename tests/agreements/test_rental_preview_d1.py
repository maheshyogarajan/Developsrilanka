"""Defect D1 (SEV-1) regression tests — Rental preview blank body.

CEO's reproduction (T3 / generate_crash report, 2026-05-22):
    POST /property/new with property_type='house', purpose='mixed',
    total_sqft=5555, home_office_sqft=2222 (40%), customer_status='tenant'
    -> GET /agreements/rental/<property_id> returned 200 but the
    response body contained only chrome (breadcrumb + DRAFT banner).
    The 4 conditional Jinja blocks (preview, rental_form_context,
    history_url, protected_deductions_lkr) all evaluated False because
    the route only passed property_id / protected_deductions_lkr.

Fix shipped 2026-05-23 (459bb47): rental_routes.preview() now enriches
the context with property, preview dict, rental_form_context, and
history_url. Tier D-Perf 2026-05-25 hardening (this file): regression
guard + friendly fallback when the Property model is unavailable.

These tests use the live Flask test client (same fixtures as the
remittance + paywall suites) and require an active Self-File
subscription so the @paywall_required wrapper passes through.
"""
from __future__ import annotations

import pytest

# Re-export shared fixtures so pytest discovers them.
from tests.remittance.conftest import (  # noqa: F401
    app as _base_app,
    client,
    db_session,
    login_as,
    _make_user,
)
from tests.paywall.conftest import (  # noqa: F401
    app,
    user_a,
    user_b,
    subscription_factory,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_property(db_session, user_id, *, purpose="mixed", property_type="house",
                   total_sqft=5555, home_office_sqft=2222,
                   customer_status="tenant"):
    """Insert a Property row matching CEO's reproduced shape (T3)."""
    from fiesta.property.models import Property
    from app import db
    p = Property(
        user_id=user_id,
        address_line1="14 Galle Road",
        city="Colombo 03",
        postcode="00300",
        property_type=property_type,
        purpose=purpose,
        customer_status=customer_status,
        total_sqft=total_sqft,
        home_office_sqft=home_office_sqft,
    )
    p.recompute_home_office_percentage()
    db.session.add(p)
    db.session.commit()
    return p


def _delete_property(property_id):
    """Best-effort teardown — drops the Property row created by the test
    so the user_a teardown FK chain doesn't trip."""
    try:
        from fiesta.property.models import Property
        from app import db
        Property.query.filter(Property.id == property_id).delete()
        db.session.commit()
    except Exception:
        from app import db
        db.session.rollback()


# --------------------------------------------------------------------------- #
# Test 1 — CEO repro: mixed-purpose property renders all 4 blocks
# --------------------------------------------------------------------------- #


def test_mixed_purpose_property_renders_rental_preview(
    app, client, db_session, user_a, subscription_factory
):
    """CEO's reproduced case: home_office 40%, 5555 sqft, mixed purpose,
    house, tenant -> /agreements/rental/<id> renders body, NOT chrome-only.

    Asserts the four template gates evaluate True (preview, form-pane,
    history strip, page content) and the 'protects Rs X' strip OR the
    Generate PDF button is present.
    """
    subscription_factory(user_a)
    login_as(client, user_a)
    prop = _make_property(
        db_session, user_a.id,
        purpose="mixed", property_type="house",
        total_sqft=5555, home_office_sqft=2222,
        customer_status="tenant",
    )
    try:
        resp = client.get(f"/agreements/rental/{prop.id}")
        assert resp.status_code == 200, (
            f"Expected 200 OK; got {resp.status_code}. "
            f"Body head: {resp.get_data(as_text=True)[:500]}"
        )
        body = resp.get_data(as_text=True)

        # Anti-blank-page guards: at least ONE of the four conditional
        # body blocks must render. The original D1 symptom was that
        # ALL four were absent, leaving chrome-only.
        assert "Generate PDF" in body, (
            "Generate PDF button missing — rental_form_context block "
            "did not render (D1 regression)."
        )
        # The preview document block — renders 'Parties', 'Property',
        # 'Terms' headers when `preview` dict is populated.
        assert "Parties" in body and "Property" in body and "Terms" in body, (
            "Preview document headers missing — `preview` ctx block did "
            "not render (D1 regression)."
        )
        # The history strip — only renders when property + history_url set.
        assert "View history of generated agreements" in body, (
            "History strip missing — property/history_url not passed (D1 regression)."
        )
        # NOT the blank-fallback friendly message.
        assert "rental-blank-fallback" not in body, (
            "Saw the blank-fallback alert when prop SHOULD have resolved — "
            "regression in bundle helper."
        )
    finally:
        _delete_property(prop.id)


# --------------------------------------------------------------------------- #
# Test 2 — Calculator unit test: protected_deductions != None for mixed
# --------------------------------------------------------------------------- #


def test_protected_deductions_handles_mixed_purpose_property(app, db_session, user_a):
    """The compute_protected_deductions_lkr helper used by the rental
    preview must return an int (0 or positive) — never None — regardless
    of purpose value. The defect-log hypothesis was the calculator
    returning None for purpose='mixed'.
    """
    from fiesta.agreements.helpers import compute_protected_deductions_lkr
    from fiesta.property.models import Property
    from app import db

    p = Property(
        user_id=user_a.id,
        address_line1="14 Galle Road",
        city="Colombo 03",
        property_type="house",
        purpose="mixed",
        customer_status="tenant",
        total_sqft=5555,
        home_office_sqft=2222,
    )
    p.recompute_home_office_percentage()
    db.session.add(p)
    db.session.commit()
    try:
        val = compute_protected_deductions_lkr(user_a, p, is_property=True)
        # The Property has no monthly_rent_lkr column on it (rent lives on
        # RentalAgreement). The helper returns 0 in that case — that's
        # correct + non-None.
        assert isinstance(val, int), (
            f"compute_protected_deductions_lkr must return int; got {type(val)}={val}"
        )
        assert val >= 0
    finally:
        Property.query.filter(Property.id == p.id).delete()
        db.session.commit()


# --------------------------------------------------------------------------- #
# Test 3 — Friendly blank-fallback when property is None
# --------------------------------------------------------------------------- #


def test_blank_fallback_renders_friendly_message(
    app, client, user_a, subscription_factory, monkeypatch
):
    """When the Property model resolves prop=None but doesn't 404 (e.g.
    Property model import failed at startup, or cache helper silently
    returned None), the route MUST render a friendly alert message
    pointing the user back to property edit — NOT the blank chrome-only
    page from D1.
    """
    from fiesta.agreements import rental_routes

    subscription_factory(user_a)
    login_as(client, user_a)

    # Force the abort path NOT to fire by simulating _Property is None
    # (the import-failed scenario). Bundle helper returns all-None dict.
    monkeypatch.setattr(rental_routes, "_Property", None)

    def _fake_bundle(property_id, user_id):
        return {"property": None, "landlord": None, "rental": None}
    monkeypatch.setattr(
        rental_routes, "_resolve_property_bundle_cached", _fake_bundle
    )

    resp = client.get("/agreements/rental/99999")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "rental-blank-fallback" in body, (
        "Friendly fallback alert missing — D1 regression would render blank chrome."
    )
    assert "couldn't compute the rental preview" in body
    assert "Edit property #99999" in body
