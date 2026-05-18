"""
AI-Org Substrate tests (Subagent A, 2026-05-18).

Verifies the 8-table data substrate + helper functions against the live Neon DB
(re-using tests/remittance/conftest.py fixtures via tests/ai_run/conftest.py).

Test seed orgs use slug prefix `test_ai_org_subA_` so teardown is unambiguous
and we don't collide with real seeded orgs.
"""
import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.exc import InvalidRequestError


TEST_ORG_SLUG_PREFIX = "test_ai_org_subA_"


def _cleanup_test_orgs(db_session):
    """Purge any test orgs (slug starts with TEST_ORG_SLUG_PREFIX) + their
    cascading rows. reputation_event has APPEND-ONLY rules — we use a
    raw SQL DELETE that bypasses the RULE temporarily by dropping + re-adding
    it (cleaner than leaking test reputation rows into prod analytics).
    """
    from ai_org_models import AIOrg

    test_orgs = AIOrg.query.filter(
        AIOrg.slug.like(f"{TEST_ORG_SLUG_PREFIX}%")
    ).all()
    if not test_orgs:
        return

    test_org_ids = [o.id for o in test_orgs]

    # Toggle off the append-only rules just long enough to purge. We MUST
    # drop no_delete BEFORE deleting ai_org rows — the FK
    # reputation_event_ai_org_id_fkey runs an internal referential-integrity
    # query (`DELETE FROM reputation_event WHERE ai_org_id = ?`) when ai_org
    # rows are deleted, and Postgres aborts the parent DELETE if a RULE
    # rewrites the dependent cascade DELETE to NOTHING ("unexpected result").
    db_session.execute(sql_text("DROP RULE IF EXISTS no_delete ON reputation_event"))
    db_session.execute(sql_text("DROP RULE IF EXISTS no_update ON reputation_event"))
    db_session.commit()
    try:
        db_session.execute(sql_text(
            "DELETE FROM reputation_event WHERE ai_org_id = ANY(:ids)"
        ), {"ids": test_org_ids})
        # PaymentEvent + AttributionLedger + Deliverable + Contract + Proposal
        # cascade automatically via ON DELETE CASCADE on the FK back to ai_org.
        # ai_org_role is also CASCADE. So a simple DELETE on ai_org suffices.
        db_session.execute(sql_text(
            "DELETE FROM ai_org WHERE id = ANY(:ids)"
        ), {"ids": test_org_ids})
        db_session.commit()
    finally:
        # Restore the append-only invariant for prod.
        db_session.execute(sql_text(
            "CREATE OR REPLACE RULE no_update AS ON UPDATE TO reputation_event "
            "DO INSTEAD NOTHING"
        ))
        db_session.execute(sql_text(
            "CREATE OR REPLACE RULE no_delete AS ON DELETE TO reputation_event "
            "DO INSTEAD NOTHING"
        ))
        db_session.commit()


@pytest.fixture
def test_org(db_session):
    """A fresh test AI org. Created with a unique slug per test invocation,
    cleaned up in teardown.
    """
    import uuid
    from ai_org_models import AIOrg

    slug = f"{TEST_ORG_SLUG_PREFIX}{uuid.uuid4().hex[:8]}"
    org = AIOrg(slug=slug, name=f"Test Org {slug}", purpose="pytest")
    db_session.add(org)
    db_session.commit()
    yield org
    _cleanup_test_orgs(db_session)


@pytest.fixture
def test_org_role(db_session, test_org):
    """A role attached to test_org so attribution verifier tests have a valid FK."""
    from ai_org_models import AIOrgRole

    role = AIOrgRole(
        ai_org_id=test_org.id,
        role_slug="test_role",
        role_name="Test Role",
        description="pytest fixture",
        is_red_team=False,
    )
    db_session.add(role)
    db_session.commit()
    return role


# --------------------------------------------------------------------------- #
# Test 1: idempotent DDL
# --------------------------------------------------------------------------- #

def test_ensure_ai_org_tables_idempotent(db_session):
    """Calling _ensure_ai_org_tables twice is safe (no DDL errors)."""
    from ai_org_models import _ensure_ai_org_tables

    _ensure_ai_org_tables()
    _ensure_ai_org_tables()

    # Verify all 8 tables exist via information_schema.
    result = db_session.execute(sql_text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name IN "
        "('ai_org', 'ai_org_role', 'proposal', 'contract', 'deliverable', "
        " 'reputation_event', 'payment_event', 'attribution_ledger')"
    )).fetchall()
    table_names = {row[0] for row in result}
    expected = {
        "ai_org", "ai_org_role", "proposal", "contract", "deliverable",
        "reputation_event", "payment_event", "attribution_ledger",
    }
    assert expected.issubset(table_names), (
        f"missing tables: {expected - table_names}"
    )


# --------------------------------------------------------------------------- #
# Test 2: seed_initial_orgs creates the 3 council orgs with 5 roles each
# --------------------------------------------------------------------------- #

def test_seed_initial_orgs_creates_three(db_session):
    """seed_initial_orgs is idempotent + produces the 3 council orgs with 5
    roles each (1 of which is is_red_team=True). Test seeds into PROD slugs —
    safe because seed is idempotent and re-running keeps the same rows.
    """
    from ai_org_substrate import seed_initial_orgs
    from ai_org_models import AIOrg, AIOrgRole

    summary = seed_initial_orgs()

    slugs = {o["slug"] for o in summary["orgs"]}
    assert slugs == {"acquisition_studio", "delivery_ops_command", "compliance_brigade"}

    for spec in summary["orgs"]:
        org = AIOrg.query.filter_by(slug=spec["slug"]).first()
        assert org is not None, f"org {spec['slug']} not in DB after seed"
        roles = AIOrgRole.query.filter_by(ai_org_id=org.id).all()
        assert len(roles) == 5, f"{spec['slug']}: expected 5 roles, got {len(roles)}"
        red_teams = [r for r in roles if r.is_red_team]
        assert len(red_teams) == 1, (
            f"{spec['slug']}: expected exactly 1 red_team role, got {len(red_teams)}"
        )

    # Idempotency: second call must not duplicate roles.
    seed_initial_orgs()
    for spec in summary["orgs"]:
        org = AIOrg.query.filter_by(slug=spec["slug"]).first()
        roles = AIOrgRole.query.filter_by(ai_org_id=org.id).all()
        assert len(roles) == 5, (
            f"{spec['slug']}: idempotency violated; got {len(roles)} roles after 2nd seed"
        )


# --------------------------------------------------------------------------- #
# Test 3: reputation_event APPEND-ONLY (DB rule + ORM listener)
# --------------------------------------------------------------------------- #

def test_reputation_event_append_only(db_session, test_org):
    """ORM-level UPDATE/DELETE raises InvalidRequestError. DB-level
    UPDATE/DELETE is silently no-op'd by the Postgres RULE (we verify both).
    """
    from ai_org_substrate import emit_reputation_event

    ev = emit_reputation_event(
        ai_org_id=test_org.id,
        event_type="invoice_paid",
        magnitude=1000.0,
        payload={"test": "append_only"},
    )
    assert ev is not None
    original_magnitude = float(ev.magnitude)

    # ORM-level UPDATE: SQLAlchemy listener raises.
    ev.magnitude = 9999.0
    with pytest.raises(InvalidRequestError, match="APPEND-ONLY"):
        db_session.commit()
    db_session.rollback()

    # ORM-level DELETE: SQLAlchemy listener raises.
    db_session.delete(ev)
    with pytest.raises(InvalidRequestError, match="APPEND-ONLY"):
        db_session.commit()
    db_session.rollback()

    # DB-level UPDATE: RULE silently no-ops (no error, but no change either).
    db_session.execute(sql_text(
        "UPDATE reputation_event SET magnitude = 7777 WHERE id = :id"
    ), {"id": ev.id})
    db_session.commit()
    db_session.expire_all()
    # Re-fetch — magnitude unchanged.
    refetched = db_session.execute(sql_text(
        "SELECT magnitude FROM reputation_event WHERE id = :id"
    ), {"id": ev.id}).fetchone()
    assert refetched is not None
    assert float(refetched[0]) == original_magnitude, (
        f"DB RULE failed to block UPDATE: magnitude went {original_magnitude} -> {refetched[0]}"
    )

    # DB-level DELETE: RULE silently no-ops.
    db_session.execute(sql_text(
        "DELETE FROM reputation_event WHERE id = :id"
    ), {"id": ev.id})
    db_session.commit()
    still_there = db_session.execute(sql_text(
        "SELECT id FROM reputation_event WHERE id = :id"
    ), {"id": ev.id}).fetchone()
    assert still_there is not None, "DB RULE failed to block DELETE"


# --------------------------------------------------------------------------- #
# Test 4: emit_reputation_event happy path
# --------------------------------------------------------------------------- #

def test_emit_reputation_event_writes_row(db_session, test_org):
    """Happy path: emit writes a row with auto-derived axis."""
    from ai_org_substrate import emit_reputation_event, AXIS_AI_RELIABILITY
    from ai_org_models import ReputationEvent

    ev = emit_reputation_event(
        ai_org_id=test_org.id,
        event_type="redteam_pass",
        magnitude=1.0,
    )
    assert ev is not None
    assert ev.axis == AXIS_AI_RELIABILITY
    assert ev.ai_org_id == test_org.id

    # Unknown event_type w/o axis → ValueError.
    with pytest.raises(ValueError, match="not in STANDARD_EVENTS"):
        emit_reputation_event(
            ai_org_id=test_org.id,
            event_type="totally_made_up_event",
            magnitude=1.0,
        )

    # Unknown event_type WITH axis → succeeds + warning logged.
    ev2 = emit_reputation_event(
        ai_org_id=test_org.id,
        event_type="totally_made_up_event",
        magnitude=1.0,
        axis="economic",
    )
    assert ev2 is not None
    assert ev2.axis == "economic"


# --------------------------------------------------------------------------- #
# Test 5: record_payment emits a payee-side reputation event
# --------------------------------------------------------------------------- #

def test_record_payment_emits_payee_reputation(db_session, test_org):
    """Paying an ai_org creates BOTH a PaymentEvent AND a reputation_event."""
    from ai_org_substrate import record_payment, AXIS_ECONOMIC
    from ai_org_models import PaymentEvent, ReputationEvent

    pe = record_payment(
        payer_kind="fiesta",
        payer_ref_id=1,
        payee_kind="ai_org",
        payee_ref_id=test_org.id,
        amount_lkr=50000.0,
        reason="contract_payment",
    )
    assert pe is not None
    assert pe.amount_lkr == 50000

    # Reputation event was emitted on payee side.
    rep = ReputationEvent.query.filter_by(
        ai_org_id=test_org.id,
        event_type="invoice_paid",
    ).all()
    assert len(rep) >= 1
    assert any(float(r.magnitude) == 50000.0 for r in rep)
    assert all(r.axis == AXIS_ECONOMIC for r in rep)


# --------------------------------------------------------------------------- #
# Test 6: claim_attribution UNIQUE per (event, org)
# --------------------------------------------------------------------------- #

def test_claim_attribution_unique_per_org_per_event(db_session, test_org):
    """Second claim for same (external_event, org) returns the existing row,
    does not duplicate. UNIQUE constraint enforces at DB layer."""
    from ai_org_substrate import claim_attribution
    from ai_org_models import AttributionLedger

    row1 = claim_attribution(
        external_event_type="invoice_paid",
        external_event_ref_id=12345,
        claimed_by_org_id=test_org.id,
        attribution_kind="direct",
        confidence=0.92,
        evidence_payload={"source": "first_call"},
    )
    assert row1 is not None
    first_id = row1.id

    row2 = claim_attribution(
        external_event_type="invoice_paid",
        external_event_ref_id=12345,
        claimed_by_org_id=test_org.id,
        attribution_kind="last-touch",  # different — does NOT override existing
        confidence=0.5,
        evidence_payload={"source": "second_call"},
    )
    assert row2 is not None
    assert row2.id == first_id, (
        f"Expected idempotent return of existing row id={first_id}, got id={row2.id}"
    )

    # Only one row exists for this (event, org) tuple.
    count = AttributionLedger.query.filter_by(
        external_event_type="invoice_paid",
        external_event_ref_id=12345,
        claimed_by_org_id=test_org.id,
    ).count()
    assert count == 1


# --------------------------------------------------------------------------- #
# Test 7: verify_attribution sets verified_at + emits event
# --------------------------------------------------------------------------- #

def test_verify_attribution_sets_verified_at(db_session, test_org, test_org_role):
    """verify_attribution stamps verified_at + emits reputation event."""
    from ai_org_substrate import claim_attribution, verify_attribution
    from ai_org_models import ReputationEvent

    row = claim_attribution(
        external_event_type="invoice_paid",
        external_event_ref_id=999888,
        claimed_by_org_id=test_org.id,
        attribution_kind="direct",
        confidence=0.85,
    )
    assert row is not None
    assert row.verified_at is None

    verified = verify_attribution(row.id, verifier_role_id=test_org_role.id)
    assert verified is not None
    assert verified.verified_at is not None
    assert verified.verifier_role_id == test_org_role.id

    # An attribution_verified reputation event was emitted.
    rep = ReputationEvent.query.filter_by(
        ai_org_id=test_org.id,
        event_type="attribution_verified",
    ).all()
    assert any(
        (r.payload or {}).get("attribution_id") == row.id
        for r in rep
    ), "attribution_verified reputation event not emitted"
