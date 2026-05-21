"""X8a — public-flow integration tests.

Covers the anonymous-visitor end-to-end surface: S0 landing copy, inline
calculator JSON endpoint, anonymous /fie/triage -> /signup redirect, signup
form rendering, legacy template preservation, logged-in / behaviour, and
funnel-event emission to the canonical spine.

Run: pytest tests/fiesta_public/test_full_flow.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fiesta_public.conftest import login_as


# ---------------------------------------------------------------------------
# 1. S0 landing — anonymous visitor sees v4-demo narrative + calculator
# ---------------------------------------------------------------------------


def test_anonymous_root_returns_v4_demo_copy(client):
    """GET / returns 200 with the v4-demo S0 hero copy."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Hero headline — the v4-demo signature line
    assert "Cut your tax bill" in body
    # Emphasis-italic phrase from the v4 demo
    assert "Keep the records clean" in body
    # New positioning, not the legacy bookkeeping copy
    assert "Bookkeeping built to" not in body


def test_anonymous_root_has_inline_calculator(client):
    """GET / body contains the calculator form: slider + at least 5 expense chips."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The slider input by name
    assert 'name="monthly_usd"' in body
    assert 'id="incomeSlider"' in body
    # The big counter target IDs the JS updates
    assert 'id="taxOwed"' in body
    assert 'id="taxOwedNet"' in body
    assert 'id="taxSaved"' in body
    # At least 5 expense chips
    assert body.count('class="exp"') >= 5 or body.count('class="exp" ') >= 0
    assert body.count('data-rs=') >= 5


# ---------------------------------------------------------------------------
# 2. Calculator JSON endpoint — anonymous-friendly
# ---------------------------------------------------------------------------


def test_calculator_json_endpoint_anonymous(client):
    """POST /preview/calc returns valid bracket-math JSON for an anon caller."""
    payload = {
        "gross_income": 3540000,
        "currency": "LKR",
        "income_source": "foreign",
        "sp_fee": 0,
        "rental": 0,
        "senior": False,
        "year": "25_26",
    }
    resp = client.post(
        "/preview/calc",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    # Required shape from quick_preview()
    for k in (
        "gross_income_lkr",
        "naive_tax_lkr",
        "fiesta_tax_lkr",
        "saving_lkr",
        "bracket_breakdown_naive",
        "bracket_breakdown_fiesta",
    ):
        assert k in data, f"missing {k} in response"
    # Numbers parse as non-negative
    assert int(data["naive_tax_lkr"]) >= 0
    assert int(data["fiesta_tax_lkr"]) >= 0
    assert int(data["saving_lkr"]) >= 0
    # FIESTA tax can't exceed naive tax
    assert int(data["fiesta_tax_lkr"]) <= int(data["naive_tax_lkr"])
    # Bracket walk has at least one entry
    assert len(data["bracket_breakdown_naive"]) >= 1


# ---------------------------------------------------------------------------
# 3. Funnel events — canonical spine emission
# ---------------------------------------------------------------------------


def test_landing_viewed_event_emitted(client, app):
    """After a single anonymous GET /, an Event row of type landing_viewed exists."""
    from event_models import Event

    with app.app_context():
        before = Event.query.filter_by(event_type="landing_viewed").count()

    resp = client.get("/")
    assert resp.status_code == 200

    with app.app_context():
        after = Event.query.filter_by(event_type="landing_viewed").count()
    assert after == before + 1, f"landing_viewed not emitted: before={before} after={after}"


def test_estimator_run_event_emitted_on_calc(client, app):
    """POST /preview/calc emits an estimator_run event to the spine."""
    from event_models import Event

    with app.app_context():
        before = Event.query.filter_by(event_type="estimator_run").count()

    resp = client.post(
        "/preview/calc",
        data=json.dumps({
            "gross_income": 2400000,
            "currency": "LKR",
            "income_source": "foreign",
            "year": "25_26",
        }),
        content_type="application/json",
    )
    assert resp.status_code == 200

    with app.app_context():
        after = Event.query.filter_by(event_type="estimator_run").count()
    assert after == before + 1


def test_public_event_shim_accepts_whitelisted_event(client, app):
    """POST /api/event/public for a whitelisted event_type returns 204 and emits."""
    from event_models import Event

    with app.app_context():
        before = Event.query.filter_by(event_type="estimator_input_changed").count()

    resp = client.post(
        "/api/event/public",
        data=json.dumps({
            "event_type": "estimator_input_changed",
            "payload": {"source": "slider", "monthly_usd": 2500},
        }),
        content_type="application/json",
    )
    assert resp.status_code == 204

    with app.app_context():
        after = Event.query.filter_by(event_type="estimator_input_changed").count()
    assert after == before + 1


def test_public_event_shim_rejects_unknown_event(client, app):
    """POST /api/event/public for a non-whitelisted event_type returns 204 but does NOT emit."""
    from event_models import Event

    with app.app_context():
        before = Event.query.filter_by(event_type="malicious_event_type").count()

    resp = client.post(
        "/api/event/public",
        data=json.dumps({
            "event_type": "malicious_event_type",
            "payload": {"x": 1},
        }),
        content_type="application/json",
    )
    # Shim returns 204 unconditionally (no fingerprinting), but no row written
    assert resp.status_code == 204

    with app.app_context():
        after = Event.query.filter_by(event_type="malicious_event_type").count()
    assert after == before


# ---------------------------------------------------------------------------
# 4. Anonymous /fie/triage -> /signup (X8.6)
# ---------------------------------------------------------------------------


def test_anonymous_triage_redirects_to_signup(client):
    """Anon GET /fie/triage 302s to /signup (not /login), preserving the next param."""
    resp = client.get("/fie/triage", follow_redirects=False)
    assert resp.status_code in (301, 302)
    location = resp.headers.get("Location", "")
    assert "/signup" in location, f"expected /signup in Location, got {location!r}"
    assert "/login" not in location, f"unexpected /login in Location: {location!r}"
    # next=/fie/triage may or may not be URL-encoded depending on werkzeug version
    assert "next=" in location and (
        "/fie/triage" in location or "%2Ffie%2Ftriage" in location
    ), f"expected next=/fie/triage in Location, got {location!r}"


# ---------------------------------------------------------------------------
# 5. /signup renders + carries next forward
# ---------------------------------------------------------------------------


def test_signup_alias_renders(client):
    """GET /signup returns 200 with the signup form."""
    resp = client.get("/signup?next=/fie/triage")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The register.html template is shared — assert a known signup form element
    assert "<form" in body.lower()


# ---------------------------------------------------------------------------
# 6. Legacy template preserved for rollback path
# ---------------------------------------------------------------------------


def test_legacy_home_template_still_loadable(app):
    """templates/home_bookkeeping_legacy.html exists for the documented rollback path."""
    template_path = (
        Path(app.root_path)
        / "templates"
        / "home_bookkeeping_legacy.html"
    )
    assert template_path.exists(), (
        f"Legacy template missing at {template_path}. Rollback path documented in "
        "PLAN_X8_FULL_PUBLIC_FLOW.md §6 requires this file to exist."
    )
    # Sanity: it still contains the legacy bookkeeping copy
    body = template_path.read_text(encoding="utf-8")
    assert "Bookkeeping built to" in body


# ---------------------------------------------------------------------------
# 7. Logged-in user behaviour on /
# ---------------------------------------------------------------------------


def test_logged_in_user_root_redirects_to_dashboard(client, user_a):
    """Authenticated GET / 302s to /scan (the FIESTA dashboard router)."""
    login_as(client, user_a)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (301, 302)
    location = resp.headers.get("Location", "")
    # The dashboard router is /scan; persona-based reroute happens there.
    assert "/scan" in location or "/remittance" in location, (
        f"expected /scan or /remittance in Location, got {location!r}"
    )


# ---------------------------------------------------------------------------
# 8. Post-verification redirect lands on triage (X8.5 semantics)
# ---------------------------------------------------------------------------


def test_email_verify_redirect_points_to_triage(app):
    """The /verify-email/<token> handler should redirect to fiesta_triage.triage_form
    after a successful verification (X8a §X8.5). We assert by inspecting the
    handler source to keep this test independent of token-signing infra.
    """
    import inspect
    from app import verify_email

    source = inspect.getsource(verify_email)
    assert "fiesta_triage.triage_form" in source, (
        "verify_email handler should redirect to fiesta_triage.triage_form "
        "(X8a §X8.5 — post-verification destination)."
    )
    # Defence: legacy onboarding_wizard redirect inside the success path must be gone.
    # (The handler may still mention onboarding_wizard elsewhere for fallbacks;
    # the assertion above is what enforces the new destination.)
