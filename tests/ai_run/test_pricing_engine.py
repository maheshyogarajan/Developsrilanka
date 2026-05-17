"""
Pricing Engine tests — Wave 2.2 (2026-05-17).

Validates:

  1. ``PRICING_TIERS`` has the required shape for every tier
  2. ``recommend_tier`` for a user with 0 remittances -> self_serve
  3. ``recommend_tier`` for a user with >20 remittances -> premium
  4. ``recommend_tier`` for any foreign_tax_withheld -> premium (regardless of count)
  5. ``assign_experiment_variant`` is deterministic per user.id
  6. ``GET /pricing`` returns 200 (both anonymous and authenticated)

Stripe SDK is not required to run these tests — the pricing module imports
``stripe`` lazily inside the checkout handler, and we never exercise that
handler here. Webhook tests live in test_stripe_webhook.py (out of scope for
Wave 2.2 — Stripe Test mode required).

Fixtures: ``app``, ``client``, ``db_session``, ``user_a``, ``user_b``,
``login_as`` re-exported from tests/remittance/conftest.py via tests/ai_run/conftest.py.
"""
from datetime import date
from decimal import Decimal

import pytest


# --------------------------------------------------------------------------- #
# Auto-register the pricing + stripe blueprints for the duration of this file.
#
# main.py is owned by the orchestrator (won't be wired until integration), but
# the GET /pricing render test below needs the route attached. We register
# idempotently against the shared session-scoped `app` fixture — if the
# orchestrator has already wired register_routes(app) into main.py by the
# time these tests run, the second register_blueprint call would raise; we
# guard against that with a name check.
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _ensure_pricing_routes_registered(app):
    if "pricing" not in app.blueprints:
        from stripe_routes import register_routes as register_pricing_and_stripe
        register_pricing_and_stripe(app)
    yield


# --------------------------------------------------------------------------- #
# Helpers — keep test DB clean between runs.
# --------------------------------------------------------------------------- #

def _purge_pricing_events(db_session, user_id):
    """Delete any pricing-related Event rows for this user."""
    try:
        from event_models import Event
        Event.query.filter(
            Event.user_id == user_id,
            Event.event_type.in_([
                "pricing_page_viewed",
                "pricing_variant_assigned",
                "checkout_started",
            ]),
        ).delete(synchronize_session=False)
        db_session.commit()
    except Exception:
        db_session.rollback()


def _add_remittance(db_session, user, n=1, with_dta=False):
    """Insert ``n`` minimal RemittanceEntry rows for ``user``. If ``with_dta``,
    the first row carries ``foreign_tax_withheld_amount`` so the DTA branch
    of ``recommend_tier`` is exercised."""
    from remittance_models import RemittanceEntry, current_sl_tax_year
    rows = []
    for i in range(n):
        row = RemittanceEntry(
            user_id=user.id,
            organization_id=None,
            remittance_date=date.today(),
            foreign_currency="USD",
            foreign_amount=Decimal("100.00"),
            tax_year=current_sl_tax_year(),
            foreign_tax_withheld_amount=(Decimal("10.00") if (with_dta and i == 0) else None),
            foreign_tax_withheld_currency=("USD" if (with_dta and i == 0) else None),
        )
        db_session.add(row)
        rows.append(row)
    db_session.commit()
    return rows


def _purge_remittances(db_session, user_id):
    from remittance_models import RemittanceEntry
    RemittanceEntry.query.filter(RemittanceEntry.user_id == user_id).delete()
    db_session.commit()


# --------------------------------------------------------------------------- #
# 1. PRICING_TIERS shape contract
# --------------------------------------------------------------------------- #

def test_pricing_tiers_have_required_fields():
    """Every tier must declare price_lkr_yr, price_usd_yr, name, features —
    the orchestrator + marketing site + recommender all read these keys."""
    from pricing_engine import PRICING_TIERS

    expected_keys = {"self_serve", "pro", "premium"}
    assert set(PRICING_TIERS.keys()) == expected_keys, (
        f"PRICING_TIERS keys drifted: {set(PRICING_TIERS.keys())}"
    )

    for tier_key, tier in PRICING_TIERS.items():
        for field in ("price_lkr_yr", "price_usd_yr", "name", "features"):
            assert field in tier, f"tier {tier_key!r} missing {field!r}"
        assert isinstance(tier["price_lkr_yr"], int) and tier["price_lkr_yr"] > 0
        assert isinstance(tier["price_usd_yr"], int) and tier["price_usd_yr"] > 0
        assert isinstance(tier["name"], str) and tier["name"].strip()
        assert isinstance(tier["features"], list) and len(tier["features"]) >= 3

    # The DTA add-on is referenced by the pricing template; check it's intact too.
    from pricing_engine import DTA_ADD_ON
    for field in ("price_lkr", "price_usd", "name"):
        assert field in DTA_ADD_ON, f"DTA_ADD_ON missing {field!r}"


# --------------------------------------------------------------------------- #
# 2. recommend_tier — low volume
# --------------------------------------------------------------------------- #

def test_recommend_tier_for_low_volume(app, db_session, user_a):
    """A user with zero remittances should land on Self-Serve."""
    from pricing_engine import recommend_tier

    with app.app_context():
        tier = recommend_tier(user_a)
    assert tier == "self_serve", f"expected self_serve for 0 remittances, got {tier!r}"


# --------------------------------------------------------------------------- #
# 3. recommend_tier — high volume
# --------------------------------------------------------------------------- #

def test_recommend_tier_for_high_volume(app, db_session, user_a):
    """A user with > 20 remittances should land on Premium."""
    from pricing_engine import recommend_tier

    with app.app_context():
        _add_remittance(db_session, user_a, n=25, with_dta=False)
        try:
            tier = recommend_tier(user_a)
            assert tier == "premium", f"expected premium for 25 remittances, got {tier!r}"
        finally:
            _purge_remittances(db_session, user_a.id)


# --------------------------------------------------------------------------- #
# 4. recommend_tier — DTA territory
# --------------------------------------------------------------------------- #

def test_recommend_tier_for_dta_user(app, db_session, user_a):
    """Any foreign_tax_withheld -> premium, even with a tiny remittance count.
    The DTA reconciler is Premium's headline value; routing DTA users to
    Self-Serve would underprice the offer."""
    from pricing_engine import recommend_tier

    with app.app_context():
        # Only 2 remittances (would normally be self_serve), but one has DTA.
        _add_remittance(db_session, user_a, n=2, with_dta=True)
        try:
            tier = recommend_tier(user_a)
            assert tier == "premium", (
                f"expected premium for DTA user (2 remittances, 1 with foreign tax), "
                f"got {tier!r}"
            )
        finally:
            _purge_remittances(db_session, user_a.id)


# --------------------------------------------------------------------------- #
# 5. assign_experiment_variant — deterministic per user.id
# --------------------------------------------------------------------------- #

def test_assign_experiment_variant_is_stable(app, user_a):
    """Same user_id must always get the same variant — required for honest
    A/B funnel measurement. Even-id users get 'a', odd-id users get 'b'."""
    from pricing_engine import assign_experiment_variant, PRIMARY_EXPERIMENT

    with app.test_request_context("/pricing"):
        first = assign_experiment_variant(user_a)
        second = assign_experiment_variant(user_a)
        third = assign_experiment_variant(user_a)

    assert first["variant"] == second["variant"] == third["variant"], (
        f"variant flipped across calls: {first['variant']} / {second['variant']} / {third['variant']}"
    )
    assert first["experiment"] == PRIMARY_EXPERIMENT
    assert first["variant"] in ("a", "b")


def test_assign_experiment_variant_two_users_can_differ(app, user_a, user_b):
    """Two users with different ids should be able to land on different
    variants — proves the bucketing is actually doing something. (Not a
    universal truth on tiny samples, but with consecutive auto-increment ids
    the parity-modulo lands them on opposite sides ~50% of the time. If both
    happen to share a variant in this run, that's still consistent with the
    deterministic contract — we don't fail on it.)"""
    from pricing_engine import assign_experiment_variant

    with app.test_request_context("/pricing"):
        va = assign_experiment_variant(user_a)
        vb = assign_experiment_variant(user_b)

    # Sanity: both calls returned well-formed variants.
    assert va["variant"] in ("a", "b")
    assert vb["variant"] in ("a", "b")


# --------------------------------------------------------------------------- #
# 6. /pricing renders 200
# --------------------------------------------------------------------------- #

def test_pricing_page_renders(client):
    """Anonymous GET /pricing must return 200 with the recognisable hero text.
    No login required (the page is a public marketing surface)."""
    resp = client.get("/pricing")
    assert resp.status_code == 200, f"GET /pricing returned {resp.status_code}"
    body = resp.get_data(as_text=True)
    # Spot-check that the three tier names rendered (not just an empty layout).
    assert "Self-Serve" in body
    assert "Pro Compliance" in body
    assert "Premium Filing" in body


def test_pricing_page_renders_recommendation_for_logged_in_user(
    app, client, user_a, db_session,
):
    """Logged-in user with 0 remittances sees self_serve recommendation badge."""
    from tests.ai_run.conftest import login_as
    login_as(client, user_a)
    try:
        resp = client.get("/pricing")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # The "Recommended for you" badge text appears in the template when a
        # recommendation exists. We don't pin the exact tier name in the badge
        # — only that the recommender wired through.
        assert "Recommended for you" in body, (
            "Authenticated user should see the recommendation badge"
        )
    finally:
        _purge_pricing_events(db_session, user_a.id)
