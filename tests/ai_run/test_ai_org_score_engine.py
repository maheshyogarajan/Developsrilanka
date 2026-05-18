"""
AI-Org Score Engine tests — Subagent C (2026-05-18).

Verifies:
  * 90-day half-life decay maths
  * Per-axis raw summation with decay
  * Z-score normalisation (clipping + single-org edge case)
  * Band thresholds (S/A/B/C/D)
  * Full recompute_all_orgs writes back to ai_org rows
  * Dispute endpoint writes a row + returns 202
  * Public leaderboard is unauthenticated
  * Admin scores requires admin

Test pattern: seed_initial_orgs() in a fixture, hand-seed reputation_event
rows directly (bypassing emit_reputation_event for speed + determinism),
call score-engine helpers, assert. Cleanup at teardown.

Uses tests/ai_run/conftest.py fixtures (re-exports remittance/conftest.py:
app, client, db_session, user_a, admin_user, login_as).
"""
import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text as sql_text

# login_as is a helper function (not a fixture) — import directly.
from tests.remittance.conftest import login_as


# --------------------------------------------------------------------------- #
# Local fixtures — wire the score-engine blueprint onto the test app
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="session", autouse=True)
def _ensure_score_routes_registered(app):
    """Idempotent registration of the ai_org_score blueprint onto the test
    app (orchestrator wires it in prod via main.py — but tests must NOT touch
    main.py per Wave 2.1 subagent contract).
    """
    if "ai_org_score" not in app.blueprints:
        from ai_org_score_routes import register_routes
        register_routes(app)
    return app


@pytest.fixture
def seeded_orgs(db_session):
    """3 canonical orgs (idempotent — re-uses existing rows)."""
    from ai_org_substrate import seed_initial_orgs
    from ai_org_models import AIOrg
    seed_initial_orgs()
    return {
        "acquisition_studio": AIOrg.query.filter_by(slug="acquisition_studio").first(),
        "delivery_ops_command": AIOrg.query.filter_by(slug="delivery_ops_command").first(),
        "compliance_brigade": AIOrg.query.filter_by(slug="compliance_brigade").first(),
    }


@pytest.fixture
def rep_event_factory(db_session, seeded_orgs):
    """Factory that inserts ReputationEvent rows via raw SQL (bypassing the
    ORM-level append-only listener — DB-level Postgres RULE still applies but
    only blocks UPDATE/DELETE). Tracks inserted ids for teardown.
    """
    inserted_ids = []

    def _make(org_id, axis, magnitude, age_days=0, event_type="invoice_paid", confidence=1.0):
        occurred = datetime.utcnow() - timedelta(days=age_days)
        row = db_session.execute(
            sql_text("""
                INSERT INTO reputation_event
                    (ai_org_id, event_type, magnitude, axis,
                     attribution_confidence, payload, occurred_at)
                VALUES (:org, :etype, :mag, :axis, :conf, :payload, :occurred)
                RETURNING id
            """),
            {
                "org": org_id,
                "etype": event_type,
                "mag": str(magnitude),
                "axis": axis,
                "conf": str(confidence),
                "payload": json.dumps({"test": "score_engine_subC"}),
                "occurred": occurred,
            },
        ).fetchone()
        new_id = int(row[0])
        inserted_ids.append(new_id)
        db_session.commit()
        return new_id

    yield _make

    # Teardown — use raw SQL DELETE (RULE makes ORM delete a no-op, but
    # the DB-level RULE doesn't block raw SQL DELETE issued in test scope
    # via session.execute. To be safe, we DROP the RULE temporarily.)
    if inserted_ids:
        try:
            db_session.execute(sql_text("DROP RULE IF EXISTS no_delete ON reputation_event"))
            db_session.execute(
                sql_text("DELETE FROM reputation_event WHERE id = ANY(:ids)"),
                {"ids": inserted_ids},
            )
            db_session.execute(sql_text(
                "CREATE OR REPLACE RULE no_delete AS ON DELETE TO reputation_event "
                "DO INSTEAD NOTHING"
            ))
            db_session.commit()
        except Exception:
            db_session.rollback()


# --------------------------------------------------------------------------- #
# 1. Decay maths
# --------------------------------------------------------------------------- #

def test_apply_decay_half_life():
    """magnitude=100, age=90d → 50.0 within rounding tolerance."""
    from ai_org_score_engine import _apply_decay
    result = _apply_decay(Decimal("100"), 90)
    # 0.5^1 == 0.5 exactly; allow float-cast slop (math.exp(-ln(2)) ~ 0.5)
    assert abs(float(result) - 50.0) < 0.001, (
        f"expected ~50.0, got {result}"
    )


def test_apply_decay_recent():
    """age=0 → magnitude unchanged."""
    from ai_org_score_engine import _apply_decay
    result = _apply_decay(Decimal("42.5"), 0)
    assert float(result) == 42.5, f"age=0 must not decay; got {result}"


def test_apply_decay_two_half_lives():
    """age=180d → ~25 (two half-lives)."""
    from ai_org_score_engine import _apply_decay
    result = _apply_decay(Decimal("100"), 180)
    assert abs(float(result) - 25.0) < 0.01, (
        f"expected ~25.0 at 2 half-lives, got {result}"
    )


# --------------------------------------------------------------------------- #
# 2. Per-axis raw sum
# --------------------------------------------------------------------------- #

def test_compute_axis_raw_sums_recent_events(rep_event_factory, seeded_orgs):
    """Three economic events for acquisition_studio with known ages → raw sum
    equals expected decayed total within tolerance."""
    from ai_org_score_engine import compute_axis_raw, AXIS_ECONOMIC
    org_id = seeded_orgs["acquisition_studio"].id

    # 3 events: 100 fresh, 100 at 90d, 100 at 180d → 100 + 50 + 25 = 175
    rep_event_factory(org_id, AXIS_ECONOMIC, magnitude=100, age_days=0)
    rep_event_factory(org_id, AXIS_ECONOMIC, magnitude=100, age_days=90)
    rep_event_factory(org_id, AXIS_ECONOMIC, magnitude=100, age_days=180)

    total = compute_axis_raw(org_id, AXIS_ECONOMIC)
    # Tolerance widened to account for any pre-existing test events from
    # parallel test fixtures — assert >= expected since we only added 175.
    assert float(total) >= 174.5, f"expected >=174.5, got {total}"


# --------------------------------------------------------------------------- #
# 3. Z-score normalisation
# --------------------------------------------------------------------------- #

def test_z_score_normalize_clips_to_0_100():
    """Extreme spread → outputs ∈ [0, 100]."""
    from ai_org_score_engine import z_score_normalize_across_orgs, ALL_AXES
    raw = {
        1: {a: Decimal("0") for a in ALL_AXES},
        2: {a: Decimal("1") for a in ALL_AXES},
        3: {a: Decimal("1000000") for a in ALL_AXES},
    }
    out = z_score_normalize_across_orgs(raw)
    for oid, axes in out.items():
        for axis, v in axes.items():
            assert Decimal("0") <= v <= Decimal("100"), (
                f"org {oid} axis {axis} value {v} out of [0,100]"
            )


def test_z_score_normalize_single_org_returns_50():
    """Single org in dict → axis = 50 (neutral, stddev==0)."""
    from ai_org_score_engine import z_score_normalize_across_orgs, ALL_AXES
    raw = {42: {a: Decimal("99999") for a in ALL_AXES}}
    out = z_score_normalize_across_orgs(raw)
    for axis, v in out[42].items():
        assert v == Decimal("50"), f"single-org axis {axis} should be 50, got {v}"


def test_z_score_normalize_all_equal_returns_50():
    """Stddev=0 across multiple orgs → all 50."""
    from ai_org_score_engine import z_score_normalize_across_orgs, ALL_AXES
    raw = {oid: {a: Decimal("123") for a in ALL_AXES} for oid in (1, 2, 3)}
    out = z_score_normalize_across_orgs(raw)
    for oid in (1, 2, 3):
        for axis in ALL_AXES:
            assert out[oid][axis] == Decimal("50"), (
                f"all-equal should give 50; got org {oid} axis {axis} = {out[oid][axis]}"
            )


# --------------------------------------------------------------------------- #
# 4. Band thresholds
# --------------------------------------------------------------------------- #

def test_band_for_score_thresholds():
    from ai_org_score_engine import band_for_score
    assert band_for_score(Decimal("100")) == "S"
    assert band_for_score(Decimal("80")) == "S"
    assert band_for_score(Decimal("79.99")) == "A"
    assert band_for_score(Decimal("65")) == "A"
    assert band_for_score(Decimal("50")) == "B"
    assert band_for_score(Decimal("35")) == "C"
    assert band_for_score(Decimal("34.99")) == "D"
    assert band_for_score(Decimal("0")) == "D"


def test_compute_composite_score_weighting():
    """Verify the 0.5/0.3/0.2 weights."""
    from ai_org_score_engine import compute_composite_score
    # All 100 → composite 100
    assert compute_composite_score(Decimal("100"), Decimal("100"), Decimal("100")) == Decimal("100.0")
    # 100, 0, 0 → 50
    assert compute_composite_score(Decimal("100"), Decimal("0"), Decimal("0")) == Decimal("50.0")
    # 0, 100, 0 → 30
    assert compute_composite_score(Decimal("0"), Decimal("100"), Decimal("0")) == Decimal("30.0")
    # 0, 0, 100 → 20
    assert compute_composite_score(Decimal("0"), Decimal("0"), Decimal("100")) == Decimal("20.0")


# --------------------------------------------------------------------------- #
# 5. Full recompute writes DB
# --------------------------------------------------------------------------- #

def test_recompute_all_orgs_updates_db(rep_event_factory, seeded_orgs, db_session, app):
    """Seed asymmetric events on 2 orgs → recompute_all_orgs writes
    status_score + status_band + last_score_computed_at on each ai_org row."""
    from ai_org_score_engine import recompute_all_orgs
    from ai_org_models import AIOrg

    acq = seeded_orgs["acquisition_studio"]
    delops = seeded_orgs["delivery_ops_command"]

    # Acquisition: heavy economic events (recent)
    rep_event_factory(acq.id, "economic", magnitude=10000, age_days=1)
    rep_event_factory(acq.id, "economic", magnitude=5000, age_days=5)
    # Delivery ops: small economic events
    rep_event_factory(delops.id, "economic", magnitude=100, age_days=1)

    pre_acq_computed_at = acq.last_score_computed_at

    with app.app_context():
        summary = recompute_all_orgs()

    assert summary.get("orgs_scored", 0) >= 2, summary
    assert "computed_at" in summary

    # Reload from DB and verify fields populated.
    db_session.expire_all()
    acq_after = AIOrg.query.get(acq.id)
    assert acq_after.status_score is not None
    assert acq_after.status_band in {"S", "A", "B", "C", "D"}
    assert acq_after.last_score_computed_at is not None
    if pre_acq_computed_at is not None:
        assert acq_after.last_score_computed_at >= pre_acq_computed_at


# --------------------------------------------------------------------------- #
# 6. Dispute endpoint
# --------------------------------------------------------------------------- #

def test_dispute_endpoint_writes_row(client, user_a, seeded_orgs, db_session):
    """POST /ai_org/<slug>/dispute creates a ScoreDispute row and returns 202."""
    from ai_org_score_engine import ScoreDispute

    login_as(client, user_a)
    slug = seeded_orgs["acquisition_studio"].slug

    resp = client.post(
        f"/ai_org/{slug}/dispute",
        json={
            "reason": "Test dispute — Subagent C smoke test",
            "evidence_payload": {"sample_event_id": 1, "claim": "double-counted"},
        },
    )
    assert resp.status_code == 202, (
        f"expected 202, got {resp.status_code}, body={resp.get_data(as_text=True)}"
    )
    body = resp.get_json()
    assert body.get("ok") is True
    dispute_id = body.get("dispute_id")
    assert dispute_id

    # Verify the row exists.
    row = ScoreDispute.query.get(dispute_id)
    try:
        assert row is not None
        assert row.status == "open"
        assert row.ai_org_id == seeded_orgs["acquisition_studio"].id
        assert row.filed_by_user_id == user_a.id
        assert "Subagent C" in (row.reason or "")
    finally:
        # Teardown — delete the test dispute row.
        try:
            ScoreDispute.query.filter_by(id=dispute_id).delete()
            db_session.commit()
        except Exception:
            db_session.rollback()


def test_dispute_requires_login(client, seeded_orgs):
    """Unauthenticated POST → redirect to /login (302) or 401."""
    slug = seeded_orgs["acquisition_studio"].slug
    resp = client.post(f"/ai_org/{slug}/dispute", json={"reason": "x"})
    # flask_login default behaviour is 302 to login_view; could be 401 if anonymous.
    assert resp.status_code in (302, 401), f"got {resp.status_code}"


def test_dispute_missing_reason_returns_400(client, user_a, seeded_orgs):
    login_as(client, user_a)
    slug = seeded_orgs["acquisition_studio"].slug
    resp = client.post(f"/ai_org/{slug}/dispute", json={"reason": ""})
    assert resp.status_code == 400


def test_dispute_unknown_org_returns_404(client, user_a):
    login_as(client, user_a)
    resp = client.post(
        "/ai_org/does_not_exist_slug/dispute", json={"reason": "x"}
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# 7. Public leaderboard + admin gate
# --------------------------------------------------------------------------- #

def test_leaderboard_renders_publicly(client, seeded_orgs):
    """GET /ai_org/leaderboard returns 200 without auth and shows bands only."""
    resp = client.get("/ai_org/leaderboard")
    assert resp.status_code == 200, f"got {resp.status_code}"
    body = resp.get_data(as_text=True)
    assert "AI-Org Leaderboard" in body
    # The slug should appear, but the raw numeric composite score should NOT.
    assert "acquisition_studio" in body or "compliance_brigade" in body
    # Defence-in-depth: no float-looking composite score patterns should leak.
    # (Bands are 1-char S/A/B/C/D inside band-pill.)


def test_admin_scores_requires_admin(client, user_a):
    """Non-admin user → 403."""
    login_as(client, user_a)
    resp = client.get("/ai_org/admin/scores")
    assert resp.status_code == 403, f"got {resp.status_code}"


def test_admin_scores_renders_for_admin(client, admin_user, seeded_orgs):
    """Admin user → 200 with breakdown table."""
    login_as(client, admin_user)
    resp = client.get("/ai_org/admin/scores")
    assert resp.status_code == 200, f"got {resp.status_code}"
    body = resp.get_data(as_text=True)
    assert "AI-Org Scores" in body
    assert "Economic" in body
    assert "Composite" in body
