"""tests/property/test_setup_link.py — D7 property index → setup link (SEV-2).

Defect: /property had no in-page link to /property/setup. The D3 consolidated
property+landlord+rental form was effectively orphaned — a user landing on
/property with zero properties had no on-screen way to discover the setup
flow, so the page dead-ended.

Fix: templates/property/index.html now renders the setup link in BOTH states:
  * Zero properties → big hero CTA inside `.s7-empty-state` panel (primary).
  * One+ properties → a smaller secondary CTA in the header.

Both CTAs share the `data-testid="property-setup-link"` attribute for
robust selection by these tests.

Coverage:
  1. test_property_index_links_to_setup_for_empty_user — primary CTA
     present when user has zero properties; URL = /property/setup.
  2. test_property_index_links_to_setup_for_existing_user — secondary CTA
     still present (and links to /property/setup) when the user has at
     least one property.
  3. test_property_setup_route_accessible_from_property_index — the link's
     href resolves to a real route that responds (any 2xx/3xx is fine; we
     only need to prove the URL is wired and the page is reachable).
  4. test_property_setup_route_endpoint_exists — bonus belt-and-braces:
     url_for('fiesta_property.setup') resolves without BuildError.

Run with:
    python -m pytest tests/property/test_setup_link.py -v
"""
from __future__ import annotations

import re

import pytest

from tests.remittance.conftest import login_as


SETUP_PATH = "/property/setup"
INDEX_PATH = "/property"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _seed_property(db_session, user_id: int):
    """Insert one minimal Property row for the given user. Returns the
    Property instance — caller is responsible for cleanup or relying on
    the user_a teardown cascade."""
    from fiesta.property.models import Property
    p = Property(
        user_id=user_id,
        address_line1="42 Galle Road",
        city="Colombo",
        postcode="00300",
        property_type="apartment",
        purpose="mixed",
        customer_status="tenant",
        total_sqft=900,
        home_office_sqft=200,
    )
    p.recompute_home_office_percentage()
    db_session.add(p)
    db_session.commit()
    return p


def _cleanup_properties(db_session, user_id: int):
    """Delete all properties belonging to a user. Called at test end so the
    user_a teardown doesn't see orphan rows (and to keep tests independent)."""
    from fiesta.property.models import Property
    Property.query.filter(Property.user_id == user_id).delete(
        synchronize_session=False
    )
    db_session.commit()


def _extract_setup_link(html: str) -> str | None:
    """Return the href of the first element carrying data-testid=
    "property-setup-link", or None if absent."""
    # Tolerant regex — single OR double quotes around the testid value; any
    # ordering of attributes is fine.
    m = re.search(
        r'<a[^>]*?href=["\']([^"\']+)["\'][^>]*?data-testid=["\']property-setup-link["\']'
        r'|<a[^>]*?data-testid=["\']property-setup-link["\'][^>]*?href=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    if not m:
        return None
    return m.group(1) or m.group(2)


# --------------------------------------------------------------------------- #
# 1. Empty state — primary CTA present
# --------------------------------------------------------------------------- #
def test_property_index_links_to_setup_for_empty_user(
    app, client, user_a, subscription_factory, db_session
):
    """Zero properties → /property must render a setup link AND the
    empty-state hero panel (primary CTA emphasis)."""
    subscription_factory(
        user_a, days_until_expiry=30,
        stripe_payment_intent_id=f"pi_pytest_d7_empty_{user_a.id}",
    )
    login_as(client, user_a)
    # Belt-and-braces: ensure no leftover rows from prior test runs.
    _cleanup_properties(db_session, user_a.id)

    resp = client.get(INDEX_PATH, follow_redirects=False)
    assert resp.status_code == 200, (
        f"GET {INDEX_PATH} expected 200, got {resp.status_code} "
        f"body={resp.data[:200]!r}"
    )
    body = resp.data.decode("utf-8", errors="replace")

    # The setup link MUST be present and point to /property/setup.
    href = _extract_setup_link(body)
    assert href is not None, (
        "No element with data-testid='property-setup-link' on empty /property. "
        "The D7 fix requires the link to be reachable from the index."
    )
    assert href.endswith(SETUP_PATH), (
        f"property-setup-link href={href!r}, expected to end with {SETUP_PATH!r}"
    )

    # Empty-state panel + primary CTA class signal — proves the visual
    # emphasis is in place (not just any old link).
    assert "s7-empty-state" in body, (
        "Empty-state panel missing on /property when user has 0 properties"
    )
    assert "s7-header-cta--primary" in body, (
        "Primary CTA class missing — empty-state link must be visually primary"
    )


# --------------------------------------------------------------------------- #
# 2. Populated state — secondary CTA still present
# --------------------------------------------------------------------------- #
def test_property_index_links_to_setup_for_existing_user(
    app, client, user_a, subscription_factory, db_session
):
    """One+ properties → /property still has the setup link (so the user
    can add another), but visually as a secondary CTA, not the hero."""
    subscription_factory(
        user_a, days_until_expiry=30,
        stripe_payment_intent_id=f"pi_pytest_d7_full_{user_a.id}",
    )
    login_as(client, user_a)
    # Reset, then seed one property.
    _cleanup_properties(db_session, user_a.id)
    _seed_property(db_session, user_a.id)
    try:
        resp = client.get(INDEX_PATH, follow_redirects=False)
        assert resp.status_code == 200, (
            f"GET {INDEX_PATH} expected 200, got {resp.status_code} "
            f"body={resp.data[:200]!r}"
        )
        body = resp.data.decode("utf-8", errors="replace")

        href = _extract_setup_link(body)
        assert href is not None, (
            "property-setup-link missing on populated /property. Setup must "
            "stay reachable so users can add a second/third property."
        )
        assert href.endswith(SETUP_PATH), (
            f"property-setup-link href={href!r}, expected {SETUP_PATH!r}"
        )

        # Secondary CTA class signal; empty-state hero panel must NOT appear.
        assert "s7-header-cta--secondary" in body, (
            "Secondary CTA class missing — when user has properties, the "
            "setup link should be visually de-emphasised (not hero)."
        )
        assert "s7-empty-state" not in body, (
            "Empty-state panel rendered for user WITH properties — should "
            "only appear when properties list is empty."
        )
    finally:
        _cleanup_properties(db_session, user_a.id)


# --------------------------------------------------------------------------- #
# 3. The link actually resolves — no broken-URL regression
# --------------------------------------------------------------------------- #
def test_property_setup_route_accessible_from_property_index(
    app, client, user_a, subscription_factory, db_session
):
    """Follow the href from /property to /property/setup. The destination
    must respond with a non-error status (2xx/3xx are both fine — we are
    only verifying the wiring, not the form's full contract)."""
    subscription_factory(
        user_a, days_until_expiry=30,
        stripe_payment_intent_id=f"pi_pytest_d7_link_{user_a.id}",
    )
    login_as(client, user_a)
    _cleanup_properties(db_session, user_a.id)

    idx = client.get(INDEX_PATH, follow_redirects=False)
    body = idx.data.decode("utf-8", errors="replace")
    href = _extract_setup_link(body)
    assert href is not None, "no property-setup-link to follow"

    target = client.get(href, follow_redirects=False)
    assert target.status_code < 500, (
        f"Following {href} from /property returned {target.status_code} — "
        f"the link from /property to /property/setup is broken. "
        f"body={target.data[:200]!r}"
    )
    # Specifically reject 404 — that would mean the route is unwired even
    # though the template emits the link.
    assert target.status_code != 404, (
        f"{href} returned 404 — /property/setup route is missing or the "
        f"template is linking to the wrong URL."
    )


# --------------------------------------------------------------------------- #
# 4. Belt + braces: url_for resolves
# --------------------------------------------------------------------------- #
def test_property_setup_route_endpoint_exists(app):
    """url_for('fiesta_property.setup') must resolve cleanly. Catches the
    case where someone renames the blueprint endpoint without updating
    templates/property/index.html — the template would BuildError at render
    time, which is harder to diagnose than this targeted assertion."""
    from flask import url_for
    with app.test_request_context("/"):
        try:
            url = url_for("fiesta_property.setup")
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"url_for('fiesta_property.setup') raised {type(exc).__name__}: "
                f"{exc}. templates/property/index.html will BuildError."
            )
        assert url.endswith(SETUP_PATH), (
            f"url_for('fiesta_property.setup') returned {url!r}, "
            f"expected to end with {SETUP_PATH!r}"
        )
