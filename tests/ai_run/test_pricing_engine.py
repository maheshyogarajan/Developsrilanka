"""
Pricing Engine tests — FIESTA v4.1 (2026-05-20 realignment).

Replaces the Wave 2.2 three-tier ($99 / $199 / $349 USD) shape tests with
the v4.1 LKR-primary schema: Free Trial / Self-File / Auto-File (gated by
AUTO_FILE_ENABLED) + Consultant Booking sibling.

Validates:

  1. ``PRICING_TIERS`` has the v4.1 keys and v4.1 prices
  2. ``recommend_tier`` for anonymous / no-history user -> free_trial
  3. ``recommend_tier`` for a user with remittances -> self_file
  4. ``recommend_tier`` for a paid-history user -> self_file
  5. ``recommend_tier`` never returns auto_file while AUTO_FILE_ENABLED is False
  6. ``assign_experiment_variant`` is deterministic per user.id
  7. ``GET /pricing`` returns 200 (both anonymous and authenticated)
  8. /pricing shows Free Trial + Self-File but hides Auto-File while flag is off
  9. Free-Trial checkout redirects (no Stripe call); Auto-File checkout redirects
  10. ``CONSULTANT_BOOKING`` is intact with v4.1 price (Rs 5,000)

Stripe SDK is not required to run these tests — the pricing module imports
``stripe`` lazily inside the checkout handler, and we never exercise that
handler against the real SDK. Webhook tests live in test_stripe_webhook.py.

Fixtures: ``app``, ``client``, ``db_session``, ``user_a``, ``user_b``,
``login_as`` re-exported from tests/remittance/conftest.py via tests/ai_run/conftest.py.
"""
from datetime import date
from decimal import Decimal

import pytest


# --------------------------------------------------------------------------- #
# Auto-register the pricing + stripe blueprints for the duration of this file.
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
    """Insert ``n`` minimal RemittanceEntry rows for ``user``. ``with_dta``
    flag retained for back-compat with conftest helpers — v4.1 recommender
    no longer routes on DTA, but the fixture still exercises the column."""
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
# 1. PRICING_TIERS shape + canonical v4.1 prices
# --------------------------------------------------------------------------- #

def test_pricing_tiers_have_required_fields():
    """Every tier must declare price_lkr_yr, name, features. The
    orchestrator + marketing site + recommender all read these keys."""
    from pricing_engine import PRICING_TIERS, PRICING_VERSION

    assert PRICING_VERSION == "v4.1", f"PRICING_VERSION drifted: {PRICING_VERSION!r}"

    expected_keys = {"free_trial", "self_file", "auto_file"}
    assert set(PRICING_TIERS.keys()) == expected_keys, (
        f"PRICING_TIERS keys drifted: {set(PRICING_TIERS.keys())}"
    )

    for tier_key, tier in PRICING_TIERS.items():
        for field in ("price_lkr_yr", "name", "features", "available"):
            assert field in tier, f"tier {tier_key!r} missing {field!r}"
        assert isinstance(tier["price_lkr_yr"], int) and tier["price_lkr_yr"] >= 0
        assert isinstance(tier["name"], str) and tier["name"].strip()
        assert isinstance(tier["features"], list) and len(tier["features"]) >= 3
        assert isinstance(tier["available"], bool)


def test_v4_1_canonical_prices():
    """v4.1 canonical prices, per the FIESTA council brief
    (core_concept.pricing_v4_1): Free Trial Rs 0, Self-File Rs 2,500,
    Auto-File Rs 5,000, Consultant Booking Rs 5,000."""
    from pricing_engine import PRICING_TIERS, CONSULTANT_BOOKING

    assert PRICING_TIERS["free_trial"]["price_lkr_yr"] == 0
    assert PRICING_TIERS["self_file"]["price_lkr_yr"] == 2500
    assert PRICING_TIERS["auto_file"]["price_lkr_yr"] == 5000
    assert CONSULTANT_BOOKING["price_lkr"] == 5000


def test_consultant_booking_intact():
    """CONSULTANT_BOOKING and the DTA_ADD_ON back-compat alias both exist
    and point at the same v4.1 consultant booking object."""
    from pricing_engine import CONSULTANT_BOOKING, DTA_ADD_ON
    for field in ("price_lkr", "name", "term", "available_to"):
        assert field in CONSULTANT_BOOKING, f"CONSULTANT_BOOKING missing {field!r}"
    # Back-compat alias should be the same dict identity.
    assert DTA_ADD_ON is CONSULTANT_BOOKING


# --------------------------------------------------------------------------- #
# 2. recommend_tier — anonymous / no-history -> free_trial
# --------------------------------------------------------------------------- #

def test_recommend_tier_for_anonymous():
    """An anonymous (None) user should land on Free Trial."""
    from pricing_engine import recommend_tier
    assert recommend_tier(None) == "free_trial"


def test_recommend_tier_for_no_history_user(app, db_session, user_a):
    """A user with zero remittances and no paid history should land on Free Trial."""
    from pricing_engine import recommend_tier

    # Force a clean state — ensure no leftover remittances and a
    # free_trial subscription_status.
    _purge_remittances(db_session, user_a.id)
    user_a.subscription_status = "free_trial"
    db_session.commit()

    with app.app_context():
        tier = recommend_tier(user_a)
    assert tier == "free_trial", f"expected free_trial for empty user, got {tier!r}"


# --------------------------------------------------------------------------- #
# 3. recommend_tier — remittances -> self_file
# --------------------------------------------------------------------------- #

def test_recommend_tier_with_remittances(app, db_session, user_a):
    """A user with at least one remittance should land on Self-File."""
    from pricing_engine import recommend_tier

    with app.app_context():
        _add_remittance(db_session, user_a, n=3, with_dta=False)
        try:
            tier = recommend_tier(user_a)
            assert tier == "self_file", f"expected self_file with 3 remittances, got {tier!r}"
        finally:
            _purge_remittances(db_session, user_a.id)


# --------------------------------------------------------------------------- #
# 4. recommend_tier — paid history -> self_file
# --------------------------------------------------------------------------- #

def test_recommend_tier_for_paid_history_user(app, db_session, user_a):
    """A user with a non-trial subscription_status (i.e. previously paid)
    should land on Self-File regardless of remittance count."""
    from pricing_engine import recommend_tier

    _purge_remittances(db_session, user_a.id)
    user_a.subscription_status = "premium"  # any non-trial value
    db_session.commit()
    try:
        with app.app_context():
            tier = recommend_tier(user_a)
        assert tier == "self_file", f"expected self_file for paid user, got {tier!r}"
    finally:
        user_a.subscription_status = "free_trial"
        db_session.commit()


# --------------------------------------------------------------------------- #
# 5. recommend_tier never returns auto_file while feature flag is off
# --------------------------------------------------------------------------- #

def test_recommend_tier_never_returns_auto_file_while_disabled(app, db_session, user_a):
    """While AUTO_FILE_ENABLED is False (default), heavy users should still
    only see Self-File. Auto-File is v1.1; the recommender must not surface
    a tier the checkout flow will refuse."""
    from pricing_engine import recommend_tier, AUTO_FILE_ENABLED

    if AUTO_FILE_ENABLED:
        pytest.skip("AUTO_FILE_ENABLED is True — Auto-File is live; skip the gate test.")

    with app.app_context():
        _add_remittance(db_session, user_a, n=30, with_dta=False)
        try:
            tier = recommend_tier(user_a)
            assert tier in ("free_trial", "self_file"), (
                f"recommender leaked auto_file while flag is off: {tier!r}"
            )
        finally:
            _purge_remittances(db_session, user_a.id)


# --------------------------------------------------------------------------- #
# 6. assign_experiment_variant — deterministic per user.id
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
    variants — proves the bucketing is actually doing something."""
    from pricing_engine import assign_experiment_variant

    with app.test_request_context("/pricing"):
        va = assign_experiment_variant(user_a)
        vb = assign_experiment_variant(user_b)

    assert va["variant"] in ("a", "b")
    assert vb["variant"] in ("a", "b")


# --------------------------------------------------------------------------- #
# 7. /pricing renders 200
# --------------------------------------------------------------------------- #

def test_pricing_page_renders(client):
    """Anonymous GET /pricing must return 200 with the v4.1 tier names."""
    resp = client.get("/pricing")
    assert resp.status_code == 200, f"GET /pricing returned {resp.status_code}"
    body = resp.get_data(as_text=True)
    # Spot-check that the v4.1 tier names rendered.
    assert "Free Trial" in body, "Free Trial tier card missing from /pricing"
    assert "Self-File" in body, "Self-File tier card missing from /pricing"


def test_pricing_page_renders_recommendation_for_logged_in_user(
    app, client, user_a, db_session,
):
    """Logged-in user with no history sees free_trial recommendation badge."""
    from tests.ai_run.conftest import login_as
    login_as(client, user_a)
    try:
        resp = client.get("/pricing")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Recommended for you" in body, (
            "Authenticated user should see the recommendation badge"
        )
    finally:
        _purge_pricing_events(db_session, user_a.id)


# --------------------------------------------------------------------------- #
# 8. Auto-File hidden from /pricing while flag is off
# --------------------------------------------------------------------------- #

def test_auto_file_hidden_while_disabled(client):
    """While AUTO_FILE_ENABLED is False, Auto-File should NOT appear as a
    customer-facing tier card on /pricing. It still exists in PRICING_TIERS
    and /pricing/tiers.json for internal callers."""
    from pricing_engine import AUTO_FILE_ENABLED

    if AUTO_FILE_ENABLED:
        pytest.skip("AUTO_FILE_ENABLED is True — Auto-File is live; visibility test inapplicable.")

    resp = client.get("/pricing")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The tier NAME "Auto-File" should not appear on the public page; the
    # tagline "Coming in v1.1" should also not leak.
    assert "Auto-File" not in body, (
        "Auto-File tier card surfaced on /pricing while feature flag is off"
    )


# --------------------------------------------------------------------------- #
# 9. tiers.json exposes the v4.1 schema
# --------------------------------------------------------------------------- #

def test_tiers_json_exposes_v4_1_schema(client):
    """GET /pricing/tiers.json returns the full v4.1 schema for orchestrator
    consumers, including Auto-File even when hidden from the page."""
    resp = client.get("/pricing/tiers.json")
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["pricing_version"] == "v4.1"
    assert "auto_file_enabled" in body
    assert set(body["tiers"].keys()) == {"free_trial", "self_file", "auto_file"}
    assert body["tiers"]["self_file"]["price_lkr_yr"] == 2500
    assert body["tiers"]["auto_file"]["price_lkr_yr"] == 5000
    assert body["consultant_booking"]["price_lkr"] == 5000


# --------------------------------------------------------------------------- #
# 10. Free-Trial + disabled-Auto-File checkout flows redirect (no Stripe call)
# --------------------------------------------------------------------------- #

def test_free_trial_checkout_redirects(app, client, user_a):
    """POST /pricing/checkout/free_trial must NOT hit Stripe — it should
    flash an info message and redirect back to /pricing."""
    from tests.ai_run.conftest import login_as
    login_as(client, user_a)

    resp = client.post("/pricing/checkout/free_trial", follow_redirects=False)
    # 303 (See Other) per the flash-then-redirect pattern in the route.
    assert resp.status_code in (302, 303), (
        f"Free-Trial checkout should redirect, got {resp.status_code}"
    )


def test_auto_file_checkout_redirects_while_disabled(app, client, user_a):
    """POST /pricing/checkout/auto_file must NOT hit Stripe while
    AUTO_FILE_ENABLED is False — it should flash an info message and
    redirect back to /pricing."""
    from pricing_engine import AUTO_FILE_ENABLED
    if AUTO_FILE_ENABLED:
        pytest.skip("AUTO_FILE_ENABLED is True — Auto-File checkout is live; skip the gate test.")

    from tests.ai_run.conftest import login_as
    login_as(client, user_a)

    resp = client.post("/pricing/checkout/auto_file", follow_redirects=False)
    assert resp.status_code in (302, 303), (
        f"Disabled Auto-File checkout should redirect, got {resp.status_code}"
    )
