"""
Engagement Engine tests — Wave 3.1 (2026-05-18).

Validates the proactive nudge surface:

  1. evaluate_user fires `inactive_3d` for an activated user whose last event
     is 4 days ago and who has no recent nudge
  2. evaluate_user respects per-pass cooldown — a recent nudge_sent event
     suppresses re-matching
  3. dispatch_nudge emits a `nudge_sent` Event row with the correct payload
  4. dispatch_nudge for an in_app channel rule creates an InAppBanner row
  5. GET /api/in_app_nudges returns the banner; POST .../dismiss makes the
     next GET return an empty list

Fixtures (app, db_session, user_a, login_as) come from tests/ai_run/conftest.py
which re-exports tests/remittance/conftest.py fixtures. We register the
in_app_nudges blueprint defensively here because main.py wiring is the
orchestrator's job (per Wave 3.1 subagent contract: "DO NOT touch main.py").

DB hygiene: tests run against the live Neon DB. Each test creates fresh
user_a rows; an autouse fixture sweeps any leftover InAppBanner +
nudge_* Event rows for pytest_*@fiesta.local accounts before and after each
test so a failed assertion can't leave FK detritus that breaks the next run.
"""
from datetime import datetime, timedelta

import pytest


# --------------------------------------------------------------------------- #
# Defensive blueprint registration — main.py wiring is the orchestrator's job
# --------------------------------------------------------------------------- #

def _ensure_in_app_routes_registered(app):
    """Idempotent. Same pattern as test_ops_sentinel._ensure_ops_routes_registered."""
    if "in_app_nudges" not in app.blueprints:
        from in_app_nudge_routes import register_routes
        register_routes(app)


# --------------------------------------------------------------------------- #
# Hygiene — purge engagement detritus for pytest user rows
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _purge_engagement_rows(app, db_session):
    """Before AND after each test, sweep any InAppBanner / nudge_* Event rows
    belonging to pytest_*@fiesta.local user accounts.

    Mirrors test_ai_crm._purge_ai_crm_rows. Runs even if the test errors
    mid-body because pytest fixture teardown is always invoked. Without
    this, a failed assertion would leave InAppBanner FK rows that the
    user_a fixture's DELETE FROM user can't traverse cleanly.
    """
    NUDGE_EVENT_TYPES = (
        "nudge_sent",
        "nudge_viewed",
        "nudge_dismissed",
        "nudge_clicked",
    )

    def _sweep():
        from sqlalchemy import text as _t
        try:
            with app.app_context():
                ids = [
                    r[0] for r in db_session.execute(
                        _t("""SELECT id FROM "user"
                              WHERE email LIKE 'pytest_%@fiesta.local'""")
                    ).fetchall()
                ]
                if not ids:
                    return
                try:
                    db_session.execute(
                        _t("DELETE FROM in_app_banners WHERE user_id = ANY(:ids)"),
                        {"ids": ids},
                    )
                except Exception:
                    db_session.rollback()
                try:
                    db_session.execute(
                        _t("""DELETE FROM events
                               WHERE user_id = ANY(:ids)
                                 AND event_type = ANY(:types)"""),
                        {"ids": ids, "types": list(NUDGE_EVENT_TYPES)},
                    )
                except Exception:
                    db_session.rollback()
                db_session.commit()
        except Exception:
            try:
                db_session.rollback()
            except Exception:
                pass

    _sweep()
    yield
    _sweep()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _seed_event(db_session, user_id, event_type, created_at=None, payload=None):
    """Insert one Event row directly (bypassing emit() so we control timestamp)."""
    from event_models import Event
    ev = Event(
        event_type=event_type,
        user_id=user_id,
        payload=payload or {},
        source="test",
    )
    if created_at is not None:
        ev.created_at = created_at
    db_session.add(ev)
    db_session.commit()
    return ev


def _ensure_activated_profile(db_session, user_id):
    """Create or update a CustomerProfile with lifecycle_stage='activated' so
    the inactive_* rule's lifecycle guard passes. We write directly instead of
    calling ai_crm.recompute_profile so the test controls the stage value
    explicitly — recompute would infer 'dormant' from the 4-day-old event."""
    from ai_crm import CustomerProfile
    profile = (
        CustomerProfile.query
                       .filter(CustomerProfile.user_id == user_id)
                       .first()
    )
    if profile is None:
        profile = CustomerProfile(
            user_id=user_id,
            lifecycle_stage="activated",
        )
        db_session.add(profile)
    else:
        profile.lifecycle_stage = "activated"
    db_session.commit()
    return profile


# --------------------------------------------------------------------------- #
# 1. evaluate_user fires inactive_3d for activated user, 4 days idle
# --------------------------------------------------------------------------- #

def test_evaluate_user_inactive_3d_fires(app, db_session, user_a):
    """Activated user whose last event is 4d ago, no recent nudge → matches
    `inactive_3d`. (The 4d > 3d threshold + no nudge in the test session.)"""
    from engagement_engine import evaluate_user

    with app.app_context():
        _ensure_activated_profile(db_session, user_a.id)
        _seed_event(
            db_session, user_a.id, "remittance_added",
            created_at=datetime.utcnow() - timedelta(days=4),
        )

        matches = evaluate_user(user_a.id)

        assert "inactive_3d" in matches, (
            f"Activated user idle 4d should match 'inactive_3d', got {matches!r}"
        )


# --------------------------------------------------------------------------- #
# 2. evaluate_user respects per-rule cooldown
# --------------------------------------------------------------------------- #

def test_evaluate_user_cooldown_respected(app, db_session, user_a):
    """Same setup as test 1, BUT add a `nudge_sent` event 1 day ago for the
    same user. The 5-day global cooldown on inactive_* rules must suppress
    re-matching.
    """
    from engagement_engine import evaluate_user

    with app.app_context():
        _ensure_activated_profile(db_session, user_a.id)
        _seed_event(
            db_session, user_a.id, "remittance_added",
            created_at=datetime.utcnow() - timedelta(days=4),
        )
        # A nudge sent 1 day ago — well inside the 5d cooldown window
        _seed_event(
            db_session, user_a.id, "nudge_sent",
            created_at=datetime.utcnow() - timedelta(days=1),
            payload={"rule_key": "inactive_3d", "channel": "both"},
        )

        matches = evaluate_user(user_a.id)

        assert "inactive_3d" not in matches, (
            f"Cooldown should suppress 'inactive_3d' after a recent nudge_sent, "
            f"got matches={matches!r}"
        )
        assert "inactive_7d" not in matches, (
            f"Global 5d nudge cooldown should also suppress 'inactive_7d', "
            f"got matches={matches!r}"
        )


# --------------------------------------------------------------------------- #
# 3. dispatch_nudge emits a `nudge_sent` Event row
# --------------------------------------------------------------------------- #

def test_dispatch_nudge_emits_event(app, db_session, user_a, monkeypatch):
    """dispatch_nudge writes ONE Event(event_type='nudge_sent') row with
    payload.rule_key set to the dispatched rule. We use the in-app channel
    rule (`persona_set_no_first_remittance`) so the test doesn't need a real
    SendGrid key to count as a success.
    """
    from engagement_engine import dispatch_nudge
    from event_models import Event

    with app.app_context():
        # Sanity: count existing nudge_sent rows for this user (should be 0
        # after the autouse purge, but defence-in-depth).
        before = (
            Event.query
                 .filter(Event.user_id == user_a.id,
                         Event.event_type == "nudge_sent")
                 .count()
        )

        ok = dispatch_nudge(user_a.id, "persona_set_no_first_remittance")
        assert ok, "dispatch_nudge of in-app rule must succeed (banner insert)"

        after_rows = (
            Event.query
                 .filter(Event.user_id == user_a.id,
                         Event.event_type == "nudge_sent")
                 .order_by(Event.created_at.desc())
                 .all()
        )
        assert len(after_rows) == before + 1, (
            f"Expected exactly one new nudge_sent event, got "
            f"before={before} after={len(after_rows)}"
        )
        ev = after_rows[0]
        assert (ev.payload or {}).get("rule_key") == "persona_set_no_first_remittance", (
            f"nudge_sent payload.rule_key must match dispatched rule, "
            f"got {(ev.payload or {}).get('rule_key')!r}"
        )
        assert (ev.payload or {}).get("channel") == "in_app"


# --------------------------------------------------------------------------- #
# 4. dispatch_nudge with channel='in_app' creates an InAppBanner row
# --------------------------------------------------------------------------- #

def test_dispatch_in_app_creates_banner(app, db_session, user_a):
    """Dispatch the `persona_set_no_first_remittance` rule (channel='in_app')
    and assert exactly one InAppBanner row exists for the user, with the
    correct rule_key + non-empty CTA fields.
    """
    from engagement_engine import dispatch_nudge
    from engagement_models import InAppBanner

    with app.app_context():
        ok = dispatch_nudge(user_a.id, "persona_set_no_first_remittance")
        assert ok, "in-app dispatch must succeed"

        rows = (
            InAppBanner.query
                       .filter(InAppBanner.user_id == user_a.id)
                       .all()
        )
        assert len(rows) == 1, (
            f"Expected exactly one InAppBanner for user, got {len(rows)}"
        )
        b = rows[0]
        assert b.rule_key == "persona_set_no_first_remittance"
        assert b.headline, "headline must not be empty"
        assert b.body, "body must not be empty"
        assert b.cta_text, "cta_text must not be empty"
        assert b.cta_url.startswith("http"), (
            f"cta_url should be absolute, got {b.cta_url!r}"
        )
        assert b.dismissed_at is None, "fresh banner must not be pre-dismissed"


# --------------------------------------------------------------------------- #
# 5. GET /api/in_app_nudges returns undismissed; POST dismiss removes it
# --------------------------------------------------------------------------- #

def test_in_app_nudges_returns_undismissed(app, client, db_session, user_a):
    """End-to-end through the HTTP API:
      1. dispatch_nudge creates a banner
      2. GET /api/in_app_nudges returns it
      3. POST .../dismiss soft-deletes it
      4. Next GET returns empty list
    """
    from engagement_engine import dispatch_nudge
    from tests.ai_run.conftest import login_as

    _ensure_in_app_routes_registered(app)

    with app.app_context():
        ok = dispatch_nudge(user_a.id, "persona_set_no_first_remittance")
        assert ok

        login_as(client, user_a)

        # 1. List — should return one banner
        resp = client.get("/api/in_app_nudges")
        assert resp.status_code == 200, (
            f"GET /api/in_app_nudges should be 200, got {resp.status_code}"
        )
        body = resp.get_json()
        assert isinstance(body, dict) and "banners" in body
        assert len(body["banners"]) == 1, (
            f"Should see one banner, got {len(body['banners'])}: {body!r}"
        )
        banner = body["banners"][0]
        assert banner["rule_key"] == "persona_set_no_first_remittance"
        banner_id = banner["id"]

        # 2. Dismiss
        resp = client.post(f"/api/in_app_nudges/{banner_id}/dismiss")
        assert resp.status_code == 200, (
            f"POST .../dismiss should be 200, got {resp.status_code}"
        )
        assert (resp.get_json() or {}).get("ok") is True

        # 3. Re-list — must be empty now
        resp = client.get("/api/in_app_nudges")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["banners"] == [], (
            f"Dismissed banner must not reappear, got {body!r}"
        )
