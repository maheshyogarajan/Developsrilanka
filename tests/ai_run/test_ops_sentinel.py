"""
Ops Sentinel tests — Wave 2.4 (2026-05-17).

Validates the self-monitoring + auto-incident response surface:

  1. run_all_checks() returns the expected snapshot shape
  2. check_neon_connection passes against the live test DB (the bedrock check —
     if this is broken every other check would be moot)
  3. log_gemini_cost writes a row with the correct estimated_cost_usd
  4. dispatch_alert emits an Event(event_type='ops_alert') row
  5. /internal/ops/health requires admin (non-admin → 403/redirect)

Fixtures come from tests/ai_run/conftest.py (which re-exports the validated
remittance conftest fixtures + adds an admin_user fixture).

The ops blueprint is registered inline via _ensure_ops_routes_registered() —
the orchestrator wires it into main.py in production; here we register
defensively the same way conftest.py does for revenue_intel.
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest


# --------------------------------------------------------------------------- #
# Helper: register the ops blueprint defensively (mirrors conftest pattern)
# --------------------------------------------------------------------------- #

def _ensure_ops_routes_registered(app):
    """Idempotent. main.py wires the blueprint in production; tests register
    defensively because the ai_run conftest's `app` fixture doesn't know about
    ops_routes (per the Wave 2.4 subagent contract: 'DO NOT touch main.py')."""
    if "ops" not in app.blueprints:
        from ops_routes import register_routes
        register_routes(app)


def _purge_test_artifacts(db_session, user_id):
    """Delete GeminiCostLog + ops_alert Event rows for the test user — keeps
    the live DB tidy across runs."""
    try:
        from gemini_cost_log_model import GeminiCostLog
        GeminiCostLog.query.filter(GeminiCostLog.user_id == user_id).delete()
    except Exception:
        pass
    try:
        from event_models import Event
        # Event rows from dispatch_alert have user_id=NULL (system-internal),
        # but ops_check_completed events do too. Purge by event_type so we
        # don't leave test detritus.
        Event.query.filter(
            Event.event_type.in_(["ops_alert", "ops_check_completed"]),
            Event.source == "cron:ops_sentinel",
        ).delete(synchronize_session=False)
    except Exception:
        pass
    db_session.commit()


# --------------------------------------------------------------------------- #
# Stripe webhook delivery check (v1.0 — Gemini R1 Q6.2)
# --------------------------------------------------------------------------- #

def test_check_stripe_webhook_delivery_quiet_window(app):
    """Below STRIPE_WEBHOOK_MIN_EVENTS, the check stays healthy with
    a 'quiet' message — we don't page on tiny samples."""
    from ops_sentinel import check_stripe_webhook_delivery, STRIPE_WEBHOOK_MIN_EVENTS
    from fiesta.paywall.models import StripeEvent, register_models

    with app.app_context():
        register_models()
        from app import db
        StripeEvent.query.filter(
            StripeEvent.stripe_event_id.like("evt_pytest_quiet_%")
        ).delete(synchronize_session=False)
        db.session.commit()

        for i in range(STRIPE_WEBHOOK_MIN_EVENTS - 1):
            db.session.add(StripeEvent(
                stripe_event_id=f"evt_pytest_quiet_{i}",
                event_type="checkout.session.completed",
                handled=True,
            ))
        db.session.commit()

        result = check_stripe_webhook_delivery()
        assert result["healthy"] is True
        assert "quiet" in (result.get("message") or "").lower()

        StripeEvent.query.filter(
            StripeEvent.stripe_event_id.like("evt_pytest_quiet_%")
        ).delete(synchronize_session=False)
        db.session.commit()


def test_check_stripe_webhook_delivery_alerts_on_failure_streak(app):
    """80% failure rate must fail the check + message must name a webhook
    endpoint so the operator knows where to look."""
    from ops_sentinel import check_stripe_webhook_delivery
    from fiesta.paywall.models import StripeEvent, register_models

    with app.app_context():
        register_models()
        from app import db
        StripeEvent.query.filter(
            StripeEvent.stripe_event_id.like("evt_pytest_fail_%")
        ).delete(synchronize_session=False)
        db.session.commit()

        for i in range(8):
            db.session.add(StripeEvent(
                stripe_event_id=f"evt_pytest_fail_{i}",
                event_type="checkout.session.completed",
                handled=False,
                handler_error="simulated handler crash for ops_sentinel test",
            ))
        for i in range(2):
            db.session.add(StripeEvent(
                stripe_event_id=f"evt_pytest_fail_ok_{i}",
                event_type="checkout.session.completed",
                handled=True,
            ))
        db.session.commit()

        result = check_stripe_webhook_delivery()
        assert result["healthy"] is False
        msg = result.get("message") or ""
        assert "/webhooks/stripe/paywall" in msg or "consultant" in msg, (
            f"Alert message should name a webhook endpoint; got: {msg}"
        )

        StripeEvent.query.filter(
            StripeEvent.stripe_event_id.like("evt_pytest_fail_%")
        ).delete(synchronize_session=False)
        db.session.commit()


def test_stripe_webhook_check_registered_in_HEALTH_CHECKS():
    from ops_sentinel import HEALTH_CHECKS
    assert "stripe_webhook_delivery" in HEALTH_CHECKS


# --------------------------------------------------------------------------- #
# 1. run_all_checks() returns a dict with the expected shape
# --------------------------------------------------------------------------- #

def test_run_all_checks_returns_dict(app):
    """Top-level shape contract — the Celery beat task, the /internal/ops/health
    route, and the future Sentry/PagerDuty wiring all key off this exact
    structure. A drift here breaks every downstream consumer.
    """
    from ops_sentinel import run_all_checks, HEALTH_CHECKS

    with app.app_context():
        snapshot = run_all_checks()

    # Top-level keys
    for key in ("ran_at", "overall_healthy", "unhealthy_count", "checks"):
        assert key in snapshot, f"missing top-level key {key!r}"

    # Types
    assert isinstance(snapshot["ran_at"], str)
    assert isinstance(snapshot["overall_healthy"], bool)
    assert isinstance(snapshot["unhealthy_count"], int)
    assert isinstance(snapshot["checks"], dict)

    # Every registered check must appear in the result
    for check_name in HEALTH_CHECKS.keys():
        assert check_name in snapshot["checks"], (
            f"check {check_name!r} missing from snapshot — registry/run drift"
        )

    # Each check result has the uniform {healthy, value, threshold, message} shape
    for name, result in snapshot["checks"].items():
        assert "healthy" in result, f"{name}: missing 'healthy' key"
        assert "message" in result, f"{name}: missing 'message' key"
        assert isinstance(result["healthy"], bool), (
            f"{name}: healthy must be bool, got {type(result['healthy'])!r}"
        )

    # unhealthy_count agrees with the per-check truth values
    expected_unhealthy = sum(
        1 for r in snapshot["checks"].values() if not r.get("healthy")
    )
    assert snapshot["unhealthy_count"] == expected_unhealthy, (
        f"unhealthy_count drift: header={snapshot['unhealthy_count']} "
        f"actual={expected_unhealthy}"
    )
    assert snapshot["overall_healthy"] == (expected_unhealthy == 0)


# --------------------------------------------------------------------------- #
# 2. check_neon_connection passes against the live test DB
# --------------------------------------------------------------------------- #

def test_check_neon_connection_passes(app):
    """The bedrock check — every other check leans on a working DB connection.
    Run it inside the test app context (which has DATABASE_URL wired)."""
    from ops_sentinel import check_neon_connection

    with app.app_context():
        result = check_neon_connection()

    assert result["healthy"] is True, (
        f"Neon SELECT 1 should pass against the test DB. Got: {result!r}"
    )
    assert result["value"] == 1
    assert "OK" in result["message"]


# --------------------------------------------------------------------------- #
# 3. log_gemini_cost writes a row with correct estimated_cost_usd
# --------------------------------------------------------------------------- #

def test_log_gemini_cost_writes_row(app, db_session, user_a):
    """Validates the helper that every Gemini-touching surface (Wave 2.3 CRM,
    Wave 3.2 Support, remittance_import) will call after every API call.

    Math check: gemini-2.5-flash at $0.075 / $0.30 per 1M tokens with
    (1_000_000, 1_000_000) tokens → $0.375 exactly.
    """
    from ops_sentinel import log_gemini_cost
    from gemini_cost_log_model import GeminiCostLog

    before = GeminiCostLog.query.filter(GeminiCostLog.user_id == user_a.id).count()

    with app.app_context():
        new_id = log_gemini_cost(
            user_id=user_a.id,
            model_name="gemini-2.5-flash",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            source="pytest",
        )

    assert new_id is not None, "log_gemini_cost() must return the new row id on success"

    after = GeminiCostLog.query.filter(GeminiCostLog.user_id == user_a.id).count()
    assert after == before + 1, f"expected 1 new row, got {after - before}"

    row = GeminiCostLog.query.get(new_id)
    assert row is not None
    assert row.user_id == user_a.id
    assert row.model_name == "gemini-2.5-flash"
    assert row.prompt_tokens == 1_000_000
    assert row.completion_tokens == 1_000_000
    assert row.source == "pytest"

    # 1M input @ $0.075 + 1M output @ $0.30 = $0.375 exactly
    expected = Decimal("0.375000")
    assert Decimal(row.estimated_cost_usd) == expected, (
        f"cost math drift: expected {expected}, got {row.estimated_cost_usd}"
    )

    _purge_test_artifacts(db_session, user_a.id)


# --------------------------------------------------------------------------- #
# 4. dispatch_alert emits an Event row with event_type='ops_alert'
# --------------------------------------------------------------------------- #

def test_dispatch_alert_emits_event(app, db_session, user_a):
    """dispatch_alert MUST write a visible-to-Event-Spine record so Wave 2
    dashboards can consume operational alerts alongside product events.
    """
    from ops_sentinel import dispatch_alert
    from event_models import Event

    fake_check_name = "pytest_synthetic_check"
    fake_result = {
        "healthy": False,
        "value": 42,
        "threshold": "<= 10",
        "message": "pytest synthetic — please ignore",
    }

    # Use a request context so emit() can lift defaults without barfing.
    with app.test_request_context("/"):
        new_id = dispatch_alert(fake_check_name, fake_result)

    assert new_id is not None, "dispatch_alert must return the new Event.id on success"

    row = Event.query.get(new_id)
    assert row is not None
    assert row.event_type == "ops_alert", (
        f"expected event_type='ops_alert', got {row.event_type!r}"
    )
    assert row.source == "cron:ops_sentinel", (
        f"expected source='cron:ops_sentinel', got {row.source!r}"
    )
    # Payload round-trip — every field the alert carried should be present
    assert row.payload is not None
    assert row.payload.get("check_name") == fake_check_name
    assert row.payload.get("healthy") is False
    assert "pytest synthetic" in (row.payload.get("message") or "")

    # Cleanup
    Event.query.filter(Event.id == new_id).delete()
    db_session.commit()
    _purge_test_artifacts(db_session, user_a.id)


# --------------------------------------------------------------------------- #
# 5. /internal/ops/health requires admin
# --------------------------------------------------------------------------- #

def test_internal_health_requires_admin(app, client, user_a):
    """A logged-in non-admin user MUST NOT see the ops health dashboard.

    decorators.admin_required flashes + redirects to index() on missing role,
    so we accept any of 302 (redirect to /) or 403 (if the decorator is ever
    upgraded to return-403). Either way, the JSON snapshot body MUST NOT be
    present.
    """
    _ensure_ops_routes_registered(app)

    from tests.remittance.conftest import login_as
    login_as(client, user_a)

    resp = client.get("/internal/ops/health", follow_redirects=False)

    # admin_required uses redirect(url_for('index')) so 302 is the expected
    # outcome today; we tolerate 403 in case the decorator is hardened later.
    assert resp.status_code in (302, 403), (
        f"non-admin must be denied — got {resp.status_code} {resp.data[:200]!r}"
    )

    # Even on 302, the response body must NOT contain the JSON snapshot keys
    body = resp.get_data(as_text=True) or ""
    assert "overall_healthy" not in body, (
        "non-admin response leaked health snapshot keys — security regression"
    )
    assert "unhealthy_count" not in body
