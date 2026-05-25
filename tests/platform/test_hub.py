"""F-Platform-4 + F-Platform-5 — FIESTA hub + persistent savings counter wiring.

Locks the X9 MS1 Stage C1 contract (see PLAN_X9_WIRE_UP.md §F-Platform-4
+ §F-Platform-5 + `_shell_contract.md` Design Lock 1):

  F-Platform-4 (`/` as the FIESTA hub for sl_foreign_income users):
    - GET `/` for a sl_foreign_income user renders `templates/fiesta_home.html`
      which extends `layout_fiesta.html` — same H1 ("Cut your tax bill"),
      same slider, same chips, same big counter.
    - The slider is pre-filled with the user's avg monthly USD remittance
      (3-month avg from RemittanceEntry).
    - The next-step card varies by funnel state:
        no remittances           → "Log your first inward remittance"
        has remittances, no save → "Claim deductions on S5"
        has both                 → "See your tax bill"
    - Legacy bookkeeping users (persona != sl_foreign_income) still get
      the legacy `/scan` redirect path.

  F-Platform-5 (persistent savings counter wired via custom events):
    - static/js/fiesta.js subscribes to the contract-locked names AND now
      drains the <meta name="fiesta-pending-events"> tag on boot, AND
      intercepts fetch() to auto-dispatch on X-Fiesta-Event response header.

These tests live alongside tests/platform/test_shell.py (F-Platform-1's
shell-contract regression) so the platform test suite stays cohesive.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path


def login_as(client, user):
    """Bypass the email/password form. Mirrors test_shell.py's helper."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def _seed_remittances(db_session, user_id: int, count: int = 3,
                      foreign_amount: Decimal = Decimal("2400"),
                      tax_year: str | None = None):
    """Insert `count` USD remittances for the user so the hub slider has
    real data to pre-fill from. Returns the inserted rows so the test
    cleanup can drop them post-assertion.

    `tax_year` defaults to whatever the live `current_sl_tax_year()` helper
    returns (in slash form, then normalised to dash for the DB column) so
    the seeded rows always match the same YA the context processor queries
    for — regardless of when the test runs across the YA tick-over."""
    from remittance_models import RemittanceEntry
    if tax_year is None:
        from fiesta.paywall.models import current_sl_tax_year as _csl
        tax_year = _csl().replace('/', '-')  # context processor accepts both forms
    today = date.today()
    rows = []
    for i in range(count):
        e = RemittanceEntry(
            user_id=user_id,
            remittance_date=today - timedelta(days=30 * (i + 1)),
            foreign_currency="USD",
            foreign_amount=foreign_amount,
            cbsl_rate=Decimal("302.00"),
            cbsl_rate_source="test_seed",
            lkr_amount_cbsl=foreign_amount * Decimal("302.00"),
            tax_year=tax_year,
        )
        db_session.add(e)
        rows.append(e)
    db_session.commit()
    return rows


def _cleanup_remittances(db_session, rows):
    from remittance_models import RemittanceEntry
    ids = [r.id for r in rows if r.id]
    if ids:
        RemittanceEntry.query.filter(RemittanceEntry.id.in_(ids)).delete(
            synchronize_session=False
        )
        db_session.commit()


def _seed_claim(db_session, user_id: int, category_id: str = "telecommunications",
                tax_year: str | None = None,
                estimated_lkr: Decimal = Decimal("60000")):
    """Insert one DeductionClaim row so the chips render is-ticked + the
    hub_compute_tax fiesta_tax_lkr < naive_tax_lkr."""
    from fiesta.deductions.models import DeductionClaim
    if tax_year is None:
        from fiesta.paywall.models import current_sl_tax_year as _csl
        tax_year = _csl().replace('/', '-')
    c = DeductionClaim(
        user_id=user_id,
        tax_year=tax_year,
        category_id=category_id,
        claimed=True,
        estimated_lkr=estimated_lkr,
    )
    db_session.add(c)
    db_session.commit()
    return c


def _cleanup_claim(db_session, c):
    from fiesta.deductions.models import DeductionClaim
    if c and c.id:
        DeductionClaim.query.filter(DeductionClaim.id == c.id).delete(
            synchronize_session=False
        )
        db_session.commit()


# --------------------------------------------------------------------- #
# 1. /  renders the FIESTA hub for an sl_foreign_income user.
# --------------------------------------------------------------------- #
def test_fiesta_home_renders_for_sl_foreign_income_user(
    app, client, user_factory
):
    user = user_factory(
        "fie_hub_basic",
        persona="sl_foreign_income",
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, user)

    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200, (
        f"sl_foreign_income user on `/` got {resp.status_code}; "
        f"expected 200 (hub render, not 302 to /scan). "
        f"body head={resp.get_data(as_text=True)[:200]!r}"
    )
    body = resp.get_data(as_text=True)

    # Extends layout_fiesta.html → has the shell markers.
    assert 'id="fiesta-savings-counter"' in body, (
        "Hub must render inside layout_fiesta.html (counter element missing)"
    )
    assert "fiesta-main" in body, "fiesta-main wrapper missing"
    assert "fiesta-shell" in body, "fiesta-shell body class missing"

    # Contains the canonical FIESTA H1 (same as S0 landing).
    assert "Cut your tax bill" in body, (
        "Hub must repeat the canonical FIESTA H1 'Cut your tax bill'"
    )

    # Slider, chips row (optional), big counter, next-step card all present.
    assert 'id="incomeSlider"' in body, "slider missing from hub"
    assert 'id="taxOwed"' in body, "big counter taxOwed element missing"
    # The user has zero remittances → funnel_state = no_remittances → the
    # next-step card MUST be rendered (this is the empty-funnel case).
    assert "Next step" in body, "next-step card missing for empty-funnel user"


# --------------------------------------------------------------------- #
# 2. Slider value matches average from RemittanceEntry rows.
# --------------------------------------------------------------------- #
def test_fiesta_home_pre_fills_slider_from_remittances(
    app, client, user_factory, db_session
):
    # MS4 W2 Agent 1 — G1.2 (2026-05-25): the hub funnel-state recommender
    # now reads `income_sources`, not `persona`. Existing assertions about
    # the slider pre-fill + foreign-income cohort behaviour stay correct
    # when income_sources contains 'foreign_remittance'.
    user = user_factory(
        "fie_hub_remit",
        persona="sl_foreign_income",
        income_sources=["foreign_remittance"],
        is_email_verified=True,
        onboarding_completed=True,
    )
    # Seed 3 USD remittances at $2400 ea → slider should pre-fill to 2400.
    rows = _seed_remittances(db_session, user.id, count=3,
                             foreign_amount=Decimal("2400"))

    # Bust the hub context cache so the new remittances are read fresh.
    from app import _invalidate_hub_cache
    _invalidate_hub_cache(user.id)

    login_as(client, user)
    try:
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)

        # The pre-filled slider has value="2400" in the input element
        # (the avg of 3 x $2400 = $2400) — Design Lock contract for the
        # hub_avg_monthly_usd field.
        assert 'value="2400"' in body, (
            "slider not pre-filled with avg monthly USD remittance "
            "(expected value=\"2400\" after seeding 3 x $2400 USD remittances)"
        )
        # The hub data-attribute confirms the source (not the $1000 anon default).
        assert 'data-prefilled-from-remittances="1"' in body, (
            "hub failed to flag the slider as pre-filled from RemittanceEntry"
        )
    finally:
        _cleanup_remittances(db_session, rows)


# --------------------------------------------------------------------- #
# 3. Next-step card for an empty (zero-remittance) user → "Log..."
# --------------------------------------------------------------------- #
def test_fiesta_home_next_step_card_for_empty_user(
    app, client, user_factory
):
    # MS4 W2 Agent 1 — G1.2 (2026-05-25): foreign-income cohort identified
    # by income_sources=['foreign_remittance']; the 'no_remittances' funnel
    # state still applies when the cohort flag is set but no remittances
    # have been logged yet.
    user = user_factory(
        "fie_hub_empty",
        persona="sl_foreign_income",
        income_sources=["foreign_remittance"],
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, user)

    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    # Funnel state markup on the card.
    assert 'data-funnel-state="no_remittances"' in body, (
        "empty-funnel user must show data-funnel-state=\"no_remittances\""
    )
    # Recommender copy (verbatim from app.py::inject_fiesta_hub_context).
    assert "Log your first inward remittance" in body, (
        "next-step card copy mismatch for no_remittances state"
    )


# --------------------------------------------------------------------- #
# 4. Next-step card for a "has both" user → "See your tax bill"
# --------------------------------------------------------------------- #
def test_fiesta_home_next_step_card_for_with_deductions_user(
    app, client, user_factory, db_session
):
    # MS4 W2 Agent 1 — G1.2 (2026-05-25): see sibling test for the
    # income_sources rationale.
    user = user_factory(
        "fie_hub_full",
        persona="sl_foreign_income",
        income_sources=["foreign_remittance"],
        is_email_verified=True,
        onboarding_completed=True,
    )
    rows = _seed_remittances(db_session, user.id, count=3,
                             foreign_amount=Decimal("2400"))
    # Bust the cache so the next render sees the fresh remittances + drives
    # hub_projected_savings_lkr > 0.
    from app import _invalidate_hub_cache
    _invalidate_hub_cache(user.id)

    login_as(client, user)
    try:
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)

        # With remittances seeded, the context processor computes a non-zero
        # hub_projected_savings_lkr → funnel_state = "ready_for_bill" → the
        # card MUST advertise "See your tax bill" (per inject_fiesta_hub_context).
        assert 'data-funnel-state="ready_for_bill"' in body, (
            "user with remittances must transition to 'ready_for_bill' state"
        )
        assert "See your tax bill" in body, (
            "next-step copy mismatch for ready_for_bill state"
        )
    finally:
        _cleanup_remittances(db_session, rows)


# --------------------------------------------------------------------- #
# 5. Legacy bookkeeping persona still goes to /scan (not the FIESTA hub).
# --------------------------------------------------------------------- #
# MS4 W2 Agent 1 — G1.2 (2026-05-25): INVERTED by Design Lock 3 §D2.
# Post-G1.2, every authenticated non-admin user renders the FIESTA hub
# regardless of persona. This test asserts the pre-G1.2 contract; W3
# fixture sweep will replace it with `test_authenticated_non_admin_lands_
# on_fiesta_home` (now in tests/platform/test_universal_hub.py).
import pytest as _pt_w2a1


@_pt_w2a1.mark.xfail(
    reason=(
        "Pre-G1.2 contract. Post-G1.2 (Design Lock 3 §D2) every authenticated "
        "non-admin user gets the FIESTA hub; the legacy /scan redirect is gone. "
        "W3 follow-up will rewrite this assertion."
    ),
    strict=True,
)
def test_legacy_persona_still_redirects_to_scan(
    app, client, user_factory
):
    legacy = user_factory(
        "legacy_hub_redirect",
        persona=None,           # legacy bookkeeping persona
        role="user",
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, legacy)

    # `/` for an authenticated non-FIESTA persona MUST 302 to /scan (or
    # something downstream of /scan via the index() persona-aware reroute).
    # Critically: it MUST NOT render the FIESTA hub.
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302, (
        f"legacy persona must be redirected from `/`; got {resp.status_code}"
    )
    # The redirect target is `/scan` (the index() route's url_for).
    location = resp.headers.get("Location", "")
    assert "/scan" in location or location.endswith("/scan"), (
        f"legacy persona must redirect to /scan; got Location={location!r}"
    )

    # The 302 target is /scan — confirming the legacy /scan path took
    # precedence over the FIESTA hub branch. We deliberately don't follow
    # the redirect further: this test user has no organizations, so the
    # downstream /scan handler bounces them to /onboarding (and that can
    # ping-pong in a test context). The redirect target alone is the
    # contract assertion: legacy users hit /scan, not the hub.
    #
    # Belt-and-braces: the 302 response body must NOT contain the hub
    # wrapper data attribute (we never rendered the hub HTML).
    body = resp.get_data(as_text=True)
    assert 'data-fiesta-home="1"' not in body, (
        "legacy persona must NEVER see the FIESTA hub markup"
    )


# --------------------------------------------------------------------- #
# 6. JS file listens for and dispatches the locked event names.
# --------------------------------------------------------------------- #
def test_savings_counter_js_refreshes_on_remittance_event():
    """String assertions against static/js/fiesta.js to lock in the
    F-Platform-5 wiring contract:

      (a) every contract-locked event name in `EVENT_NAMES` triggers a
          forced refetch (`wireSavingsEvents`).
      (b) the boot path drains the `<meta name="fiesta-pending-events">`
          tag so redirect-driven writes refresh the counter on landing.
      (c) the fetch interceptor reads `X-Fiesta-Event` response headers
          and auto-dispatches the matching event for AJAX writes.
    """
    js_path = Path(__file__).resolve().parents[2] / "static" / "js" / "fiesta.js"
    js = js_path.read_text(encoding="utf-8")

    # (a) Every contract-locked event name is in the listener array.
    for name in (
        "fiesta:remittance-added",
        "fiesta:deduction-toggled",
        "fiesta:sp-added",
        "fiesta:property-added",
        "fiesta:income-source-added",
        "fiesta:savings-counter-refresh",
    ):
        assert f"'{name}'" in js, (
            f"static/js/fiesta.js missing contract event listener: {name!r}"
        )

    # The wireSavingsEvents() function force-refetches on each event.
    assert "wireSavingsEvents" in js, "wireSavingsEvents() function missing"
    assert "fetchSavings({ force: true })" in js, (
        "event listeners must force-refetch (cache is stale by definition)"
    )

    # (b) Pending-events drain on boot.
    assert "drainPendingEvents" in js, (
        "drainPendingEvents() missing — redirect-survived events won't fire"
    )
    assert 'fiesta-pending-events' in js, (
        "meta[name=fiesta-pending-events] consumer not wired"
    )

    # (c) Fetch wrapper reads X-Fiesta-Event header.
    assert "wireFetchInterceptor" in js, "fetch interceptor function missing"
    assert "X-Fiesta-Event" in js, "X-Fiesta-Event response header not consumed"

    # Boot wires both new paths.
    assert "wireFetchInterceptor()" in js
    assert "drainPendingEvents()" in js


# --------------------------------------------------------------------- #
# 7. Server-side: queue_fiesta_event surfaces in the layout meta tag.
# --------------------------------------------------------------------- #
def test_queued_event_renders_into_meta_tag(app, client, user_factory):
    """Round-trip the F-Platform-5 redirect path: queue an event in the
    session, then GET a hub page and assert the <meta> tag is emitted with
    the queued event name. This is what fiesta.js drains on boot."""
    user = user_factory(
        "fie_evtmeta",
        persona="sl_foreign_income",
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, user)

    # Manually inject a pending event into the session (this is what
    # remittance_routes.new()/property/setup() do server-side).
    with client.session_transaction() as sess:
        sess["_fiesta_pending_events"] = ["remittance-added"]

    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    # The layout's <meta> renders the fully-qualified event name.
    assert 'name="fiesta-pending-events"' in body, (
        "layout_fiesta.html missing the F-Platform-5 pending-events meta tag"
    )
    assert "fiesta:remittance-added" in body, (
        "queued event 'remittance-added' did not surface in the meta content"
    )

    # And the session was drained — the next GET should NOT carry the meta.
    resp2 = client.get("/", follow_redirects=False)
    body2 = resp2.get_data(as_text=True)
    assert "fiesta:remittance-added" not in body2, (
        "context processor must drain pending events (single-use semantics)"
    )
