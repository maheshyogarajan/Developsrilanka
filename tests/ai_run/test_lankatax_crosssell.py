"""
Lanka.tax Cross-Sell tests — Wave 3.3.

Validates the cohort + campaign + take-rate + 1-click onboarding stack:

  1. build_cohort persists a row with the expected member count
  2. run_campaign respects the 14d cooldown (one outreach row per user
     even on N successive runs)
  3. compute_take_rate returns the right shape + correct arithmetic on
     seeded data
  4. The /onboarding/lankatax route logs the user in for a valid token
     AND sets attribution + persona
  5. /onboarding/lankatax with an invalid token renders the invalid-link
     template instead of 500

Fixtures (app, client, db_session, user_a, user_b, login_as) come from
tests/ai_run/conftest.py → tests/remittance/conftest.py.

DB hygiene: each test seeds + cleans its own LankataxCohort /
LankataxOutreach / Event rows. The autouse purge fixture below is
defence-in-depth — same pattern as test_ai_crm.py.
"""
from datetime import datetime, timedelta

import pytest

from tests.ai_run.conftest import login_as


# --------------------------------------------------------------------------- #
# Blueprint registration — orchestrator contract says don't touch conftest,
# so we register the lankatax onboarding blueprint at fixture time here.
# Session scope so it runs once per test session.
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="session", autouse=True)
def _register_lankatax_blueprint(app):
    """Ensure the /onboarding/lankatax route is reachable in the test app.

    Because `app` is session-scoped and earlier ai_run tests may have already
    served requests through it (closing the setup window), we temporarily
    flip the internal `_got_first_request` flag so `register_blueprint`
    is accepted, then restore it.
    """
    if "lankatax_onboarding" not in app.blueprints:
        from lankatax_onboarding_routes import register_routes
        prior_first_req = getattr(app, "_got_first_request", False)
        try:
            # Older Flask uses a private flag; newer Flask uses an attribute
            # on the app proper. Both flips are no-ops on the wrong attr.
            app._got_first_request = False
            register_routes(app)
        finally:
            app._got_first_request = prior_first_req
    return app


# --------------------------------------------------------------------------- #
# Hygiene — sweep test rows before AND after each test
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _purge_lankatax_rows(app, db_session):
    """Same shape as the test_ai_crm purge fixture — guarantees the test
    user_ids don't leave stale LankataxCohort / LankataxOutreach / Event
    rows that would interfere with the next test's cooldown / count
    assertions.
    """
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
                if ids:
                    for tbl in ("lankatax_outreach", "events", "customer_profiles"):
                        try:
                            db_session.execute(
                                _t(f"DELETE FROM {tbl} WHERE user_id = ANY(:ids)"),
                                {"ids": ids},
                            )
                        except Exception:
                            db_session.rollback()
                # Always sweep any pytest_* outreach rows regardless of
                # user_id — the user-delete cascade is SET NULL, so rows
                # can survive earlier teardown with user_id=NULL.
                try:
                    db_session.execute(
                        _t("""DELETE FROM lankatax_outreach
                              WHERE campaign_key LIKE 'pytest_%'""")
                    )
                except Exception:
                    db_session.rollback()
                # Cohort names we create in these tests
                try:
                    db_session.execute(
                        _t("""DELETE FROM lankatax_cohorts
                              WHERE name LIKE 'pytest_%'""")
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

def _seed_outreach(db_session, user_id, campaign_key, sent_at=None,
                   opened_at=None, converted_at=None, channel="email",
                   variant="a"):
    """Insert one LankataxOutreach row with explicit timestamps."""
    from lankatax_models import LankataxOutreach
    row = LankataxOutreach(
        user_id=user_id,
        campaign_key=campaign_key,
        channel=channel,
        variant=variant,
        sent_at=sent_at or datetime.utcnow(),
        opened_at=opened_at,
        converted_at=converted_at,
    )
    db_session.add(row)
    db_session.commit()
    return row


# --------------------------------------------------------------------------- #
# 1. build_cohort persists a row with members_count
# --------------------------------------------------------------------------- #

def test_build_cohort_persists_member_count(app, db_session, user_a, user_b):
    """A SQL selector that returns both pytest users must build a cohort with
    members_count == 2, target_user_ids containing both ids, and a row that
    survives a re-query through the ORM."""
    from lankatax_crosssell import build_cohort
    from lankatax_models import LankataxCohort

    with app.app_context():
        cohort_name = "pytest_cohort_two_users"
        sql = (
            'SELECT id FROM "user" '
            "WHERE email IN ('pytest_user_a@fiesta.local', 'pytest_user_b@fiesta.local')"
        )

        cohort = build_cohort(name=cohort_name, sql_query=sql,
                              description="Two pytest users")

        assert cohort is not None, "build_cohort must return the persisted row"
        assert cohort.name == cohort_name
        assert cohort.members_count == 2, (
            f"Expected 2 members, got {cohort.members_count} "
            f"(ids={cohort.target_user_ids})"
        )
        assert set(cohort.target_user_ids) == {user_a.id, user_b.id}, (
            f"target_user_ids snapshot mismatch: {cohort.target_user_ids}"
        )

        # Re-querying through the ORM returns the same row
        fetched = (
            LankataxCohort.query
                          .filter(LankataxCohort.name == cohort_name)
                          .first()
        )
        assert fetched is not None
        assert fetched.id == cohort.id
        assert fetched.members_count == 2


# --------------------------------------------------------------------------- #
# 2. run_campaign respects the cooldown
# --------------------------------------------------------------------------- #

def test_run_campaign_respects_cooldown(app, db_session, user_a, user_b):
    """Running the same campaign twice within the cooldown window must
    create exactly ONE LankataxOutreach row per (user, campaign_key).

    The second invocation must report skipped_cooldown == 2, sent == 0.
    """
    from lankatax_crosssell import build_cohort, run_campaign
    from lankatax_models import LankataxOutreach

    with app.app_context():
        # Build a 2-user cohort
        sql = (
            'SELECT id FROM "user" '
            "WHERE email IN ('pytest_user_a@fiesta.local', 'pytest_user_b@fiesta.local')"
        )
        cohort = build_cohort(name="pytest_cooldown_cohort", sql_query=sql)
        assert cohort is not None

        campaign_key = "pytest_cooldown_campaign"

        # First run — should send to both
        first = run_campaign(
            cohort_id=cohort.id,
            campaign_key=campaign_key,
            channel="in_app",       # stub channel — no SendGrid dependency
            variant="a",
        )
        assert first["attempted"] == 2
        assert first["sent"] == 2, f"First run should send 2, got {first}"
        assert first["skipped_cooldown"] == 0

        rows_after_first = (
            LankataxOutreach.query
                            .filter(LankataxOutreach.campaign_key == campaign_key)
                            .all()
        )
        assert len(rows_after_first) == 2

        # Second run, immediately — both must be skipped
        second = run_campaign(
            cohort_id=cohort.id,
            campaign_key=campaign_key,
            channel="in_app",
            variant="a",
        )
        assert second["attempted"] == 2
        assert second["sent"] == 0, (
            f"Second run within cooldown should send 0, got {second}"
        )
        assert second["skipped_cooldown"] == 2

        rows_after_second = (
            LankataxOutreach.query
                            .filter(LankataxOutreach.campaign_key == campaign_key)
                            .all()
        )
        assert len(rows_after_second) == 2, (
            f"Cooldown must cap at one row per user, got {len(rows_after_second)}"
        )


# --------------------------------------------------------------------------- #
# 3. compute_take_rate returns the right shape + arithmetic
# --------------------------------------------------------------------------- #

def test_compute_take_rate_returns_dict(app, db_session, user_a, user_b):
    """Seed 2 sends, 1 converted. Take rate = 50.0%. Dict shape covers
    sent/opened/clicked/converted/take_rate_pct."""
    from lankatax_crosssell import compute_take_rate

    with app.app_context():
        campaign_key = "pytest_take_rate_campaign"
        now = datetime.utcnow()

        # 2 sends, 1 with converted_at set
        _seed_outreach(db_session, user_a.id, campaign_key,
                       sent_at=now - timedelta(days=2),
                       opened_at=now - timedelta(days=1),
                       converted_at=now - timedelta(hours=1))
        _seed_outreach(db_session, user_b.id, campaign_key,
                       sent_at=now - timedelta(days=2))

        result = compute_take_rate(campaign_key, lookback_days=30)

        # Shape check — all five keys present with the right types
        assert set(result.keys()) == {
            "sent", "opened", "clicked", "converted", "take_rate_pct"
        }, f"Wrong key set: {result.keys()}"
        assert isinstance(result["sent"], int)
        assert isinstance(result["take_rate_pct"], float)

        # Arithmetic
        assert result["sent"] == 2, f"Expected sent=2, got {result['sent']}"
        assert result["opened"] == 1, f"Expected opened=1, got {result['opened']}"
        assert result["converted"] == 1, (
            f"Expected converted=1, got {result['converted']}"
        )
        assert result["take_rate_pct"] == 50.0, (
            f"Expected take_rate_pct=50.0, got {result['take_rate_pct']}"
        )

        # Zero-shape return for an unknown campaign
        zero = compute_take_rate("pytest_no_such_campaign", lookback_days=30)
        assert zero["sent"] == 0
        assert zero["take_rate_pct"] == 0.0


# --------------------------------------------------------------------------- #
# 4. Onboarding link logs the user in
# --------------------------------------------------------------------------- #

def test_onboarding_link_logs_user_in(app, client, db_session, user_a):
    """A valid signed token on /onboarding/lankatax must:
       - log user_a in (Flask-Login)
       - set persona='sl_foreign_income' if null
       - set CustomerProfile.acquisition_source='lankatax'
       - redirect (302) to /remittance/dashboard
       - mark the matching pre-seeded LankataxOutreach row as opened
    """
    from lankatax_onboarding_routes import generate_token
    from lankatax_models import LankataxOutreach
    from sqlalchemy import text as _t

    with app.app_context():
        # Force persona NULL so we can verify the route sets it
        db_session.execute(
            _t('UPDATE "user" SET persona = NULL WHERE id = :uid'),
            {"uid": user_a.id},
        )
        db_session.commit()
        db_session.expire_all()

        # Seed an outreach row for the campaign so the route has something
        # to mark opened. sent_at = now (slightly in the past).
        campaign_key = "pytest_onboarding_campaign"
        _seed_outreach(
            db_session, user_a.id, campaign_key,
            sent_at=datetime.utcnow() - timedelta(minutes=5),
        )

        token = generate_token(user_a.id, campaign_key)

    # Hit the route — follow_redirects=False so we can assert the 302
    resp = client.get(
        f"/onboarding/lankatax?token={token}"
        f"&utm_source=lankatax&utm_campaign={campaign_key}",
        follow_redirects=False,
    )
    assert resp.status_code in (301, 302), (
        f"Valid token should redirect, got {resp.status_code}. "
        f"Body: {resp.get_data(as_text=True)[:200]}"
    )
    assert "/remittance/dashboard" in resp.headers.get("Location", ""), (
        f"Should redirect to remittance dashboard, got "
        f"Location={resp.headers.get('Location')!r}"
    )

    # Session cookie set → next request should be authenticated. Round-trip
    # via /remittance/dashboard (login_required) and assert it's NOT a
    # 302 to /login.
    dashboard_resp = client.get("/remittance/dashboard", follow_redirects=False)
    assert dashboard_resp.status_code != 302 or "login" not in (
        dashboard_resp.headers.get("Location") or ""
    ), (
        "After onboarding link, user should be logged in and the dashboard "
        "should not redirect to /login. Got "
        f"status={dashboard_resp.status_code} "
        f"location={dashboard_resp.headers.get('Location')!r}"
    )

    # Verify side-effects in the DB. The route writes through a request-scoped
    # session that's distinct from the test's fixture-bound db_session. To
    # guarantee we see the route's committed view (and not a stale snapshot
    # from a long-running transaction on db_session), we open a brand-new
    # connection on the underlying engine for every read here.
    with app.app_context():
        from sqlalchemy import text as _t2
        from app import db as _db

        # End any in-flight transaction on the fixture session so subsequent
        # reads start fresh.
        try:
            db_session.rollback()
        except Exception:
            pass

        with _db.engine.connect() as conn:
            persona_now = conn.execute(
                _t2('SELECT persona FROM "user" WHERE id = :uid'),
                {"uid": user_a.id},
            ).scalar()
            acq_now = conn.execute(
                _t2(
                    "SELECT acquisition_source FROM customer_profiles "
                    "WHERE user_id = :uid"
                ),
                {"uid": user_a.id},
            ).scalar()
            opened_at = conn.execute(
                _t2(
                    "SELECT opened_at FROM lankatax_outreach "
                    "WHERE user_id = :uid AND campaign_key = :ck"
                ),
                {"uid": user_a.id, "ck": campaign_key},
            ).scalar()

        assert persona_now == "sl_foreign_income", (
            f"Persona should be set to sl_foreign_income via the onboarding "
            f"route, got {persona_now!r}"
        )
        assert acq_now == "lankatax", (
            f"CustomerProfile.acquisition_source should be 'lankatax', got "
            f"{acq_now!r}"
        )
        assert opened_at is not None, (
            "Outreach row should have opened_at set after the click"
        )


# --------------------------------------------------------------------------- #
# 5. Invalid token renders the invalid-link page (not 500)
# --------------------------------------------------------------------------- #

def test_onboarding_link_invalid_token_renders_invalid_page(app, client):
    """An obviously invalid token must NOT 500. The invalid-link template
    must render with HTTP 200 (so analytics / link checkers see a valid
    response and we don't burn rep on bounce loops).
    """
    resp = client.get(
        "/onboarding/lankatax?token=clearly-not-a-real-token&utm_source=lankatax",
        follow_redirects=False,
    )
    assert resp.status_code == 200, (
        f"Invalid token should render the invalid-link page with 200, "
        f"got {resp.status_code}"
    )
    body = resp.get_data(as_text=True)
    assert "no longer valid" in body.lower() or "expired" in body.lower(), (
        f"Invalid-link template should explain the link is expired/invalid. "
        f"Body head: {body[:200]!r}"
    )

    # Empty token also goes to the invalid-link page
    resp_empty = client.get("/onboarding/lankatax", follow_redirects=False)
    assert resp_empty.status_code == 200
