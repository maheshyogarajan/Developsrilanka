"""
AI-Org Attribution Writer + Audit Harness tests — Subagent B (2026-05-18).

Verifies the 6 attribution rules + dedup + audit confirm/reject behaviour
against the live Neon DB (re-using tests/remittance/conftest.py fixtures via
tests/ai_run/conftest.py).

Test pattern: seed_initial_orgs() in a fixture, run process_event on
hand-crafted Event rows, assert the AttributionLedger + ReputationEvent rows
were written. Cleanup at teardown.
"""
import uuid
import pytest
from sqlalchemy import text as sql_text


TEST_EVENT_SOURCE = "test:ai_org_attribution_subB"


@pytest.fixture
def seeded_orgs(db_session):
    """Ensure the 3 canonical orgs exist for the duration of the test.
    Idempotent — re-uses existing rows if seed_initial_orgs has run before.
    """
    from ai_org_substrate import seed_initial_orgs
    from ai_org_models import AIOrg
    seed_initial_orgs()
    return {
        "acquisition_studio": AIOrg.query.filter_by(slug="acquisition_studio").first(),
        "delivery_ops_command": AIOrg.query.filter_by(slug="delivery_ops_command").first(),
        "compliance_brigade": AIOrg.query.filter_by(slug="compliance_brigade").first(),
    }


@pytest.fixture
def emitted_event(db_session, user_a):
    """Factory: create an Event row and clean it (+ its attributions +
    reputation events) up at test teardown.
    """
    created_event_ids = []

    def _make(event_type, payload=None, user_id=None):
        from event_models import Event
        ev = Event(
            event_type=event_type,
            user_id=user_id if user_id is not None else user_a.id,
            payload=payload or {},
            source=TEST_EVENT_SOURCE,
        )
        db_session.add(ev)
        db_session.commit()
        created_event_ids.append(ev.id)
        return ev

    yield _make

    # Teardown: scrub anything we wrote so we don't pollute prod analytics.
    if not created_event_ids:
        return
    try:
        # Drop the APPEND-ONLY rules to delete test reputation_event rows.
        db_session.execute(sql_text("DROP RULE IF EXISTS no_delete ON reputation_event"))
        db_session.execute(sql_text("DROP RULE IF EXISTS no_update ON reputation_event"))
        db_session.commit()
        # Delete by payload->>'event_id' match (string JSON probe — engine
        # agnostic and safe for the integer-id case).
        for eid in created_event_ids:
            db_session.execute(sql_text(
                "DELETE FROM reputation_event "
                "WHERE payload::text LIKE :probe"
            ), {"probe": f'%"event_id": {eid}%'})
            db_session.execute(sql_text(
                "DELETE FROM attribution_ledger "
                "WHERE external_event_ref_id = :eid"
            ), {"eid": eid})
            db_session.execute(sql_text(
                "DELETE FROM events WHERE id = :eid"
            ), {"eid": eid})
        db_session.commit()
    finally:
        db_session.execute(sql_text(
            "CREATE OR REPLACE RULE no_update AS ON UPDATE TO reputation_event "
            "DO INSTEAD NOTHING"
        ))
        db_session.execute(sql_text(
            "CREATE OR REPLACE RULE no_delete AS ON DELETE TO reputation_event "
            "DO INSTEAD NOTHING"
        ))
        db_session.commit()


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_process_event_signup_with_lankatax_utm_attributes_acquisition(
    seeded_orgs, emitted_event
):
    """signup with utm_source=lankatax → AttributionLedger row claimed by
    acquisition_studio, kind=last-touch, reputation_event=human_acceptance/
    axis=human_impact/magnitude=1."""
    from ai_org_attribution_writer import process_event
    from ai_org_models import AttributionLedger, ReputationEvent

    ev = emitted_event("signup", payload={"utm_source": "lankatax"})
    attribution_ids = process_event(ev)
    assert len(attribution_ids) == 1

    row = AttributionLedger.query.get(attribution_ids[0])
    assert row is not None
    assert row.claimed_by_org_id == seeded_orgs["acquisition_studio"].id
    assert row.attribution_kind == "last-touch"
    assert float(row.confidence) == pytest.approx(0.9)

    rep = ReputationEvent.query.filter_by(
        ai_org_id=seeded_orgs["acquisition_studio"].id,
        event_type="human_acceptance",
    ).order_by(ReputationEvent.id.desc()).first()
    assert rep is not None
    assert rep.axis == "human_impact"
    assert float(rep.magnitude) == pytest.approx(1.0)


def test_process_event_checkout_attributes_with_outreach_lookup(
    seeded_orgs, emitted_event, user_a
):
    """checkout_completed → AttributionLedger row claimed by acquisition_studio
    (the outreach-owner fallback). Magnitude == payload.amount."""
    from ai_org_attribution_writer import process_event
    from ai_org_models import AttributionLedger, ReputationEvent

    ev = emitted_event("checkout_completed", payload={"amount": 4500})
    attribution_ids = process_event(ev)
    assert len(attribution_ids) == 1

    row = AttributionLedger.query.get(attribution_ids[0])
    assert row is not None
    assert row.claimed_by_org_id == seeded_orgs["acquisition_studio"].id
    assert row.attribution_kind == "direct"

    rep = ReputationEvent.query.filter_by(
        ai_org_id=seeded_orgs["acquisition_studio"].id,
        event_type="invoice_paid",
    ).order_by(ReputationEvent.id.desc()).first()
    assert rep is not None
    assert rep.axis == "economic"
    assert float(rep.magnitude) == pytest.approx(4500.0)


def test_process_event_remittance_ird_ready_attributes_delivery_ops(
    seeded_orgs, emitted_event
):
    from ai_org_attribution_writer import process_event
    from ai_org_models import AttributionLedger, ReputationEvent

    ev = emitted_event("remittance_ird_ready", payload={"remittance_id": 1234})
    attribution_ids = process_event(ev)
    assert len(attribution_ids) == 1

    row = AttributionLedger.query.get(attribution_ids[0])
    assert row.claimed_by_org_id == seeded_orgs["delivery_ops_command"].id

    rep = ReputationEvent.query.filter_by(
        ai_org_id=seeded_orgs["delivery_ops_command"].id,
        event_type="deliverable_accepted",
    ).order_by(ReputationEvent.id.desc()).first()
    assert rep is not None
    assert rep.axis == "ai_reliability"
    assert float(rep.magnitude) == pytest.approx(1.0)


def test_process_event_dedup_safe(seeded_orgs, emitted_event):
    """Running process_event twice on the same Event must NOT create
    duplicate AttributionLedger rows (UNIQUE constraint from Subagent A).
    """
    from ai_org_attribution_writer import process_event
    from ai_org_models import AttributionLedger

    ev = emitted_event("remittance_ird_ready")
    first = process_event(ev)
    second = process_event(ev)
    assert len(first) == 1
    assert len(second) == 1
    # Same attribution_id both times (dedup-by-fetch from claim_attribution).
    assert first[0] == second[0]

    rows = AttributionLedger.query.filter_by(
        external_event_type="remittance_ird_ready",
        external_event_ref_id=ev.id,
        claimed_by_org_id=seeded_orgs["delivery_ops_command"].id,
    ).all()
    assert len(rows) == 1


def test_audit_decision_confirm_calls_verify(seeded_orgs, emitted_event):
    """audit_decision('confirm', ...) → verified_at gets set + reputation
    event of type 'attribution_verified' emitted."""
    from ai_org_attribution_writer import process_event
    from ai_org_audit_harness import audit_decision, DECISION_CONFIRM
    from ai_org_models import AttributionLedger, AIOrgRole, ReputationEvent

    ev = emitted_event("remittance_ird_ready")
    attribution_ids = process_event(ev)
    attribution_id = attribution_ids[0]

    # Pick any red-team role as verifier.
    role = AIOrgRole.query.filter_by(
        ai_org_id=seeded_orgs["compliance_brigade"].id,
        role_slug="red_team",
    ).first()
    assert role is not None

    result = audit_decision(
        attribution_id=attribution_id,
        decision=DECISION_CONFIRM,
        verifier_role_id=role.id,
        notes="looks right",
    )
    assert result["ok"] is True

    row = AttributionLedger.query.get(attribution_id)
    assert row.verified_at is not None
    assert row.verifier_role_id == role.id

    rep = ReputationEvent.query.filter_by(
        ai_org_id=seeded_orgs["delivery_ops_command"].id,
        event_type="attribution_verified",
    ).order_by(ReputationEvent.id.desc()).first()
    assert rep is not None


def test_audit_decision_reject_emits_rollback_reputation(
    seeded_orgs, emitted_event
):
    """audit_decision('reject', ...) → audit_status='rejected' + rollback
    reputation event with magnitude=-1.0 against the wrongly-claiming org."""
    from ai_org_attribution_writer import process_event
    from ai_org_audit_harness import audit_decision, DECISION_REJECT
    from ai_org_models import AIOrgRole, ReputationEvent
    from sqlalchemy import text as sql_text
    from app import db

    ev = emitted_event("remittance_ird_ready")
    attribution_ids = process_event(ev)
    attribution_id = attribution_ids[0]

    role = AIOrgRole.query.filter_by(
        ai_org_id=seeded_orgs["compliance_brigade"].id,
        role_slug="red_team",
    ).first()
    assert role is not None

    result = audit_decision(
        attribution_id=attribution_id,
        decision=DECISION_REJECT,
        verifier_role_id=role.id,
        notes="wrong org",
    )
    assert result["ok"] is True

    # audit_status='rejected'
    status_row = db.session.execute(sql_text(
        "SELECT audit_status FROM attribution_ledger WHERE id = :id"
    ), {"id": attribution_id}).fetchone()
    assert status_row is not None
    assert status_row[0] == "rejected"

    # Rollback reputation event emitted.
    rep = ReputationEvent.query.filter_by(
        ai_org_id=seeded_orgs["delivery_ops_command"].id,
        event_type="rollback",
    ).order_by(ReputationEvent.id.desc()).first()
    assert rep is not None
    assert rep.axis == "ai_reliability"
    assert float(rep.magnitude) == pytest.approx(-1.0)
