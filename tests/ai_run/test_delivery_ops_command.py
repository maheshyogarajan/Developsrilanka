"""
Delivery Ops Command tests — Subagent E (2026-05-18).

Seven tests against the live Neon DB:

  1. test_scan_for_jobs_handles_empty_events             — no jobs in empty window
  2. test_run_pass_creates_full_lifecycle_within_sla     — delivered + 4 rep events
  3. test_run_pass_marks_sla_breach_correctly            — breach path
  4. test_quality_review_rejects_incomplete_payload       — failed_qc + rollback
  5. test_red_team_rejects_hallucinated_field            — failed_qc + hallucination_flag
  6. test_capacity_cap_rejects_overflow                   — rejected_cap, no contract
  7. test_run_pass_is_idempotent_per_event_id            — same event twice = no-op

Cleanup pattern: APPEND-ONLY ledger DROP RULE → DELETE → recreate RULE,
mirroring tests/ai_run/test_acquisition_studio.py. Hallucination_flag +
rollback events have no source_contract_id when emitted pre-contract, so we
match those by payload-text probe (proposal_id key).

We do NOT create test ai_org rows — the prod-seeded delivery_ops_command org
is used. Cleanup is scoped to Proposal / Contract / Deliverable / Payment /
Reputation rows the test wrote.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text as sql_text


TEST_SLUG_PREFIX = "subE_test_"
TEST_EVENT_SOURCE = "test:delivery_ops_command_subE"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def seeded_ops_org(db_session):
    """Ensure the delivery_ops_command org + 5 roles exist. Returns the AIOrg
    row. Idempotent — re-uses if seed_initial_orgs has run before."""
    from ai_org_substrate import seed_initial_orgs
    from ai_org_models import AIOrg
    seed_initial_orgs()
    org = AIOrg.query.filter_by(slug="delivery_ops_command").first()
    assert org is not None, "delivery_ops_command not seeded"
    return org


@pytest.fixture
def emitted_event(db_session, user_a):
    """Factory: create Event rows + clean up at teardown.

    Returns a callable: _make(event_type, payload=None, created_at=None).
    """
    created_event_ids = []

    def _make(event_type, payload=None, created_at=None, user_id=None):
        from event_models import Event
        ev = Event(
            event_type=event_type,
            user_id=user_id if user_id is not None else user_a.id,
            payload=payload or {},
            source=TEST_EVENT_SOURCE,
        )
        if created_at is not None:
            ev.created_at = created_at
        db_session.add(ev)
        db_session.commit()
        created_event_ids.append(ev.id)
        return ev

    yield _make

    if not created_event_ids:
        return
    try:
        for eid in created_event_ids:
            db_session.execute(sql_text(
                "DELETE FROM events WHERE id = :eid"
            ), {"eid": eid})
        db_session.commit()
    except Exception:
        db_session.rollback()


@pytest.fixture
def ops_artifacts_cleanup(db_session, seeded_ops_org):
    """Tracks Proposal IDs created during the test so we can scrub:
      reputation_event   (APPEND-ONLY RULE drop/recreate)
      payment_event
      deliverable
      contract
      proposal

    Yields a callable: track(proposal_id) — call it after each test write that
    you want cleaned up.
    """
    tracked_proposal_ids = []

    def track(proposal_id):
        tracked_proposal_ids.append(proposal_id)

    yield track

    if not tracked_proposal_ids:
        return

    org_id = seeded_ops_org.id

    try:
        contract_ids = [r[0] for r in db_session.execute(sql_text(
            "SELECT id FROM contract WHERE proposal_id = ANY(:pids)"
        ), {"pids": tracked_proposal_ids}).fetchall()]
    except Exception:
        contract_ids = []

    try:
        deliverable_ids = [r[0] for r in db_session.execute(sql_text(
            "SELECT id FROM deliverable WHERE contract_id = ANY(:cids)"
        ), {"cids": contract_ids or [-1]}).fetchall()]
    except Exception:
        deliverable_ids = []

    # Drop APPEND-ONLY RULE — same pattern as test_acquisition_studio.
    db_session.execute(sql_text("DROP RULE IF EXISTS no_delete ON reputation_event"))
    db_session.execute(sql_text("DROP RULE IF EXISTS no_update ON reputation_event"))
    db_session.commit()
    try:
        # Scrub reputation_event rows referencing our test contracts +
        # deliverables.
        if contract_ids:
            db_session.execute(sql_text(
                "DELETE FROM reputation_event WHERE source_contract_id = ANY(:cids)"
            ), {"cids": contract_ids})
        if deliverable_ids:
            db_session.execute(sql_text(
                "DELETE FROM reputation_event WHERE source_deliverable_id = ANY(:dids)"
            ), {"dids": deliverable_ids})
        # Pre-contract rep events (rollback / hallucination_flag emitted on
        # failed_qc) have no source_contract_id — match by payload text.
        for pid in tracked_proposal_ids:
            db_session.execute(sql_text(
                "DELETE FROM reputation_event "
                "WHERE ai_org_id = :oid AND payload::text LIKE :probe"
            ), {"oid": org_id, "probe": f'%"proposal_id": {pid}%'})

        if contract_ids:
            db_session.execute(sql_text(
                "DELETE FROM payment_event WHERE contract_id = ANY(:cids)"
            ), {"cids": contract_ids})

        if deliverable_ids:
            db_session.execute(sql_text(
                "DELETE FROM deliverable WHERE id = ANY(:dids)"
            ), {"dids": deliverable_ids})
        if contract_ids:
            db_session.execute(sql_text(
                "DELETE FROM contract WHERE id = ANY(:cids)"
            ), {"cids": contract_ids})
        db_session.execute(sql_text(
            "DELETE FROM proposal WHERE id = ANY(:pids)"
        ), {"pids": tracked_proposal_ids})
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
# Helpers
# --------------------------------------------------------------------------- #

def _track_recent_ops_proposals(db_session, org_id, since, tracker):
    """Find every Proposal the test wrote against the org after `since` and
    pass each id to tracker(). Used after run_pass() which doesn't return ids.
    """
    rows = db_session.execute(sql_text(
        "SELECT id FROM proposal "
        "WHERE proposer_org_id = :oid AND submitted_at >= :since"
    ), {"oid": org_id, "since": since}).fetchall()
    for (pid,) in rows:
        tracker(pid)


def _make_fake_job(
    job_kind="filing_submitted",
    payload_extras=None,
    external_event_id=None,
):
    """Build a synthetic job dict in the shape scan_for_jobs() returns."""
    return {
        "job_kind": job_kind,
        "external_event_id": external_event_id or int(uuid.uuid4().int >> 100),
        "external_event_type": job_kind,
        "payload": payload_extras or {},
        "received_at": datetime.utcnow(),
    }


# --------------------------------------------------------------------------- #
# Test 1: empty events → no jobs
# --------------------------------------------------------------------------- #

def test_scan_for_jobs_handles_empty_events(seeded_ops_org, db_session):
    """scan_for_jobs must return a list (possibly empty), never raise. We use
    a since_minutes=0 query to guarantee no historic events leak in."""
    from delivery_ops_command_org import scan_for_jobs

    far_future = datetime.utcnow() + timedelta(days=365 * 5)
    # since_minutes=1 with `now` 5 years in the future → empty trailing window.
    jobs = scan_for_jobs(since_minutes=1, now=far_future)
    assert isinstance(jobs, list)
    # No historic events at year +5; list must be empty.
    assert jobs == []


# --------------------------------------------------------------------------- #
# Test 2: full lifecycle, delivered within SLA
# --------------------------------------------------------------------------- #

def test_run_pass_creates_full_lifecycle_within_sla(
    seeded_ops_org, db_session, monkeypatch, ops_artifacts_cleanup,
):
    """Force a single filing_submitted job whose simulate_cycle_time stays
    under 48h. Assert: STATUS_DELIVERED proposal + Contract + Deliverable +
    4 rep events (invoice_paid, sla_met, deliverable_accepted, cost_saved_verified).
    """
    from delivery_ops_command_org import run_pass
    import delivery_ops_command_org as doco
    from ai_org_models import (
        Proposal, Contract, Deliverable, ReputationEvent, PaymentEvent,
    )

    ext_event_id = int(uuid.uuid4().int >> 100)
    fake_job = _make_fake_job(
        job_kind="filing_submitted",
        external_event_id=ext_event_id,
        # _force_on_time = guarantees cycle_time well under 48h.
        payload_extras={"_force_on_time": True, "test_run": True},
    )

    monkeypatch.setattr(
        doco, "scan_for_jobs", lambda since_minutes=60, now=None: [fake_job],
    )

    before = datetime.utcnow() - timedelta(seconds=5)
    summary = run_pass()
    _track_recent_ops_proposals(db_session, seeded_ops_org.id, before, ops_artifacts_cleanup)

    assert summary["jobs_seen"] == 1
    assert summary["queued"] == 1
    assert summary["delivered_within_sla"] == 1
    assert summary["delivered_sla_breach"] == 0
    assert summary["failed_qc"] == 0
    assert summary["rejected_capacity"] == 0
    assert summary["errors"] == []

    slug = f"filing_submitted_{ext_event_id}"
    proposal = Proposal.query.filter_by(opportunity_slug=slug).first()
    assert proposal is not None
    assert proposal.status == "delivered"
    payload = proposal.artifact_payload or {}
    assert payload.get("job_kind") == "filing_submitted"
    assert payload.get("sla_target_h") == 48
    assert payload.get("assigned_workflow") == "filing_pipeline_v1"
    assert payload.get("quality_review_decision") == "pass"
    assert payload.get("red_team_decision") == "pass"
    assert payload.get("sla_outcome") == "within_sla"
    assert payload.get("cycle_time_h") is not None
    assert float(payload["cycle_time_h"]) <= 48.0

    contract = Contract.query.filter_by(proposal_id=proposal.id).first()
    assert contract is not None
    assert contract.status == "active"
    assert float(contract.contracted_price_lkr) == 2500.0

    deliverable = Deliverable.query.filter_by(contract_id=contract.id).first()
    assert deliverable is not None
    assert deliverable.accepted is True
    assert deliverable.red_team_pass is True
    assert deliverable.hallucination_flag is False
    assert float(deliverable.quality_score) == pytest.approx(0.90)

    payment = PaymentEvent.query.filter_by(
        contract_id=contract.id, deliverable_id=deliverable.id,
    ).first()
    assert payment is not None
    assert payment.payer_kind == "fiesta_internal"
    assert payment.payee_kind == "ai_org"
    assert payment.payee_ref_id == seeded_ops_org.id
    assert float(payment.amount_lkr) == 2500.0

    # 4 expected rep event types:
    #   invoice_paid (economic, from record_payment)
    #   deliverable_accepted (ai_reliability)
    #   sla_met (ai_reliability)
    #   cost_saved_verified (economic; baseline 4000 - quote 2500 = 1500)
    rep_events = ReputationEvent.query.filter_by(
        source_contract_id=contract.id,
    ).all()
    types = {r.event_type for r in rep_events}
    assert "invoice_paid" in types
    assert "deliverable_accepted" in types
    assert "sla_met" in types
    assert "cost_saved_verified" in types

    invoice = next(r for r in rep_events if r.event_type == "invoice_paid")
    assert invoice.axis == "economic"
    assert float(invoice.magnitude) == 2500.0

    sla_met = next(r for r in rep_events if r.event_type == "sla_met")
    assert sla_met.axis == "ai_reliability"
    assert float(sla_met.magnitude) == pytest.approx(1.0)

    da = next(r for r in rep_events if r.event_type == "deliverable_accepted")
    assert da.axis == "ai_reliability"
    assert float(da.magnitude) == pytest.approx(1.0)

    csv = next(r for r in rep_events if r.event_type == "cost_saved_verified")
    assert csv.axis == "economic"
    # baseline 4000 - quote 2500 = 1500
    assert float(csv.magnitude) == pytest.approx(1500.0)


# --------------------------------------------------------------------------- #
# Test 3: SLA breach
# --------------------------------------------------------------------------- #

def test_run_pass_marks_sla_breach_correctly(
    seeded_ops_org, db_session, monkeypatch, ops_artifacts_cleanup,
):
    """Force cycle_time to exceed SLA. Proposal must end in STATUS_SLA_BREACH
    with deliverable_accepted + rollback rep events; NO sla_met, NO cost_saved_verified.
    """
    from delivery_ops_command_org import run_pass
    import delivery_ops_command_org as doco
    from ai_org_models import Proposal, Contract, ReputationEvent

    ext_event_id = int(uuid.uuid4().int >> 100)
    fake_job = _make_fake_job(
        job_kind="filing_submitted",
        external_event_id=ext_event_id,
        payload_extras={"_force_breach": True, "test_run": True},
    )

    monkeypatch.setattr(
        doco, "scan_for_jobs", lambda since_minutes=60, now=None: [fake_job],
    )

    before = datetime.utcnow() - timedelta(seconds=5)
    summary = run_pass()
    _track_recent_ops_proposals(db_session, seeded_ops_org.id, before, ops_artifacts_cleanup)

    assert summary["jobs_seen"] == 1
    assert summary["queued"] == 1
    assert summary["delivered_within_sla"] == 0
    assert summary["delivered_sla_breach"] == 1
    assert summary["failed_qc"] == 0

    slug = f"filing_submitted_{ext_event_id}"
    proposal = Proposal.query.filter_by(opportunity_slug=slug).first()
    assert proposal is not None
    assert proposal.status == "sla_breach"
    payload = proposal.artifact_payload or {}
    assert payload.get("sla_outcome") == "sla_breach"
    assert float(payload.get("cycle_time_h", 0)) > 48.0
    assert payload.get("quality_review_decision") == "pass"
    assert payload.get("red_team_decision") == "pass"

    # Contract + deliverable + payment still happen (work was accepted).
    contract = Contract.query.filter_by(proposal_id=proposal.id).first()
    assert contract is not None
    assert contract.status == "active"

    rep_events = ReputationEvent.query.filter_by(
        source_contract_id=contract.id,
    ).all()
    types = {r.event_type for r in rep_events}
    assert "invoice_paid" in types
    assert "deliverable_accepted" in types
    # ROLLBACK (magnitude 0.5) IS emitted for sla_breach.
    assert "rollback" in types
    # sla_met + cost_saved_verified MUST NOT be present.
    assert "sla_met" not in types
    assert "cost_saved_verified" not in types

    rollback = next(r for r in rep_events if r.event_type == "rollback")
    assert rollback.axis == "ai_reliability"
    assert float(rollback.magnitude) == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# Test 4: quality reviewer fails on incomplete payload
# --------------------------------------------------------------------------- #

def test_quality_review_rejects_incomplete_payload(
    seeded_ops_org, db_session, monkeypatch, ops_artifacts_cleanup,
):
    """Patch build_workflow_assignment so the workflow assignment is missing
    assigned_workflow → quality_check fails → failed_qc + rollback rep event,
    no contract.
    """
    from delivery_ops_command_org import run_pass
    import delivery_ops_command_org as doco
    import delivery_ops_command_proposals as docp
    from ai_org_models import Proposal, Contract, ReputationEvent

    ext_event_id = int(uuid.uuid4().int >> 100)
    fake_job = _make_fake_job(
        job_kind="filing_submitted",
        external_event_id=ext_event_id,
        payload_extras={"_force_on_time": True, "test_run": True},
    )

    monkeypatch.setattr(
        doco, "scan_for_jobs", lambda since_minutes=60, now=None: [fake_job],
    )
    # Strip assigned_workflow from the workflow assignment so QC fails.
    monkeypatch.setattr(
        docp, "build_workflow_assignment",
        lambda kind: {"stages": ["x"], "owner_role_slug": "workflow_orchestrator"},
    )

    before = datetime.utcnow() - timedelta(seconds=5)
    summary = run_pass()
    _track_recent_ops_proposals(db_session, seeded_ops_org.id, before, ops_artifacts_cleanup)

    assert summary["queued"] == 1
    assert summary["failed_qc"] == 1
    assert summary["delivered_within_sla"] == 0
    assert summary["delivered_sla_breach"] == 0

    slug = f"filing_submitted_{ext_event_id}"
    proposal = Proposal.query.filter_by(opportunity_slug=slug).first()
    assert proposal is not None
    assert proposal.status == "failed_qc"
    payload = proposal.artifact_payload or {}
    assert payload.get("quality_review_decision") == "fail"
    assert "assigned_workflow" in (payload.get("quality_review_reason") or "")

    assert Contract.query.filter_by(proposal_id=proposal.id).count() == 0

    # rollback rep event emitted with quality_review_failed reason
    rollbacks = ReputationEvent.query.filter_by(
        ai_org_id=seeded_ops_org.id,
        event_type="rollback",
    ).all()
    matching = [
        r for r in rollbacks
        if (r.payload or {}).get("proposal_id") == proposal.id
    ]
    assert len(matching) == 1
    assert matching[0].axis == "ai_reliability"
    assert (matching[0].payload or {}).get("reason") == "quality_review_failed"


# --------------------------------------------------------------------------- #
# Test 5: red-team rejects hallucinated_field
# --------------------------------------------------------------------------- #

def test_red_team_rejects_hallucinated_field(
    seeded_ops_org, db_session, monkeypatch, ops_artifacts_cleanup,
):
    """Inject hallucinated_field=True via the upstream event payload. Red-Team
    must reject → STATUS_FAILED_QC + hallucination_flag + rollback rep events,
    no contract.
    """
    from delivery_ops_command_org import run_pass
    import delivery_ops_command_org as doco
    from ai_org_models import Proposal, Contract, ReputationEvent

    ext_event_id = int(uuid.uuid4().int >> 100)
    fake_job = _make_fake_job(
        job_kind="filing_submitted",
        external_event_id=ext_event_id,
        payload_extras={
            "_force_on_time": True,
            "hallucinated_field": True,
            "test_run": True,
        },
    )

    monkeypatch.setattr(
        doco, "scan_for_jobs", lambda since_minutes=60, now=None: [fake_job],
    )

    before = datetime.utcnow() - timedelta(seconds=5)
    summary = run_pass()
    _track_recent_ops_proposals(db_session, seeded_ops_org.id, before, ops_artifacts_cleanup)

    assert summary["queued"] == 1
    assert summary["failed_qc"] == 1
    assert summary["delivered_within_sla"] == 0

    slug = f"filing_submitted_{ext_event_id}"
    proposal = Proposal.query.filter_by(opportunity_slug=slug).first()
    assert proposal is not None
    assert proposal.status == "failed_qc"
    payload = proposal.artifact_payload or {}
    assert payload.get("red_team_decision") == "reject"
    assert "hallucinated_field" in (payload.get("red_team_reason") or "")

    assert Contract.query.filter_by(proposal_id=proposal.id).count() == 0

    # Both rollback AND hallucination_flag rep events.
    rep_for_proposal = ReputationEvent.query.filter_by(
        ai_org_id=seeded_ops_org.id,
    ).all()
    matching = [
        r for r in rep_for_proposal
        if (r.payload or {}).get("proposal_id") == proposal.id
    ]
    types_for_proposal = {r.event_type for r in matching}
    assert "rollback" in types_for_proposal
    assert "hallucination_flag" in types_for_proposal


# --------------------------------------------------------------------------- #
# Test 6: capacity cap rejects overflow
# --------------------------------------------------------------------------- #

def test_capacity_cap_rejects_overflow(
    seeded_ops_org, db_session, monkeypatch, ops_artifacts_cleanup,
):
    """Pre-seed 20 active proposals (status=queued / in_flight), then send one
    more job → STATUS_REJECTED_CAP, no contract, no rep events.
    """
    from delivery_ops_command_org import (
        run_pass, QUEUE_CAPACITY, STATUS_QUEUED, STATUS_IN_FLIGHT,
    )
    import delivery_ops_command_org as doco
    from ai_org_models import Proposal, Contract, ReputationEvent

    # Seed QUEUE_CAPACITY (20) active proposals for this org.
    seeded_ids = []
    for i in range(QUEUE_CAPACITY):
        # Slugs MUST be unique per Proposal (uniqueness comes from how the
        # idempotency check works — same opportunity_slug + blocking status
        # would block re-proposals). We use unique slugs so each takes a slot.
        slug = f"{TEST_SLUG_PREFIX}capseed_{uuid.uuid4().hex[:10]}_{i}"
        p = Proposal(
            proposer_org_id=seeded_ops_org.id,
            buyer_kind="fiesta_internal",
            buyer_ref_id=0,
            opportunity_slug=slug,
            artifact_kind="completed_job",
            artifact_payload={"capseed": True, "i": i},
            quoted_price_lkr=1000,
            quoted_eta_days=2,
            status=STATUS_QUEUED if (i % 2 == 0) else STATUS_IN_FLIGHT,
        )
        db_session.add(p)
        db_session.commit()
        seeded_ids.append(p.id)
        ops_artifacts_cleanup(p.id)

    ext_event_id = int(uuid.uuid4().int >> 100)
    fake_job = _make_fake_job(
        job_kind="filing_submitted",
        external_event_id=ext_event_id,
        payload_extras={"_force_on_time": True, "test_run": True},
    )

    monkeypatch.setattr(
        doco, "scan_for_jobs", lambda since_minutes=60, now=None: [fake_job],
    )

    before = datetime.utcnow() - timedelta(seconds=5)
    summary = run_pass()
    _track_recent_ops_proposals(db_session, seeded_ops_org.id, before, ops_artifacts_cleanup)

    assert summary["jobs_seen"] == 1
    assert summary["rejected_capacity"] == 1
    assert summary["queued"] == 0
    assert summary["delivered_within_sla"] == 0
    assert summary["delivered_sla_breach"] == 0
    assert summary["failed_qc"] == 0

    slug = f"filing_submitted_{ext_event_id}"
    proposal = Proposal.query.filter_by(opportunity_slug=slug).first()
    assert proposal is not None
    assert proposal.status == "rejected_cap"
    payload = proposal.artifact_payload or {}
    assert payload.get("rejection_reason") == "queue_at_capacity"
    assert payload.get("queue_capacity") == QUEUE_CAPACITY

    # No contract, no payment, no rep events for this proposal.
    assert Contract.query.filter_by(proposal_id=proposal.id).count() == 0
    rep_for_proposal = ReputationEvent.query.filter_by(
        ai_org_id=seeded_ops_org.id,
    ).all()
    matching = [
        r for r in rep_for_proposal
        if (r.payload or {}).get("proposal_id") == proposal.id
    ]
    assert matching == []


# --------------------------------------------------------------------------- #
# Test 7: idempotent per external_event_id
# --------------------------------------------------------------------------- #

def test_run_pass_is_idempotent_per_event_id(
    seeded_ops_org, db_session, monkeypatch, ops_artifacts_cleanup,
):
    """Same external_event_id twice → second call must NOT create a second
    Proposal (the first ended in STATUS_DELIVERED which is a blocking status).
    """
    from delivery_ops_command_org import run_pass
    import delivery_ops_command_org as doco
    from ai_org_models import Proposal

    ext_event_id = int(uuid.uuid4().int >> 100)
    fake_job = _make_fake_job(
        job_kind="filing_submitted",
        external_event_id=ext_event_id,
        payload_extras={"_force_on_time": True, "test_run": True},
    )

    monkeypatch.setattr(
        doco, "scan_for_jobs", lambda since_minutes=60, now=None: [fake_job],
    )

    before = datetime.utcnow() - timedelta(seconds=5)
    s1 = run_pass()
    s2 = run_pass()
    _track_recent_ops_proposals(db_session, seeded_ops_org.id, before, ops_artifacts_cleanup)

    assert s1["queued"] == 1
    assert s1["delivered_within_sla"] == 1
    # Second call sees the existing delivered proposal → skips.
    assert s2["queued"] == 0
    assert s2["delivered_within_sla"] == 0
    assert s2["skipped_idempotent"] == 1

    slug = f"filing_submitted_{ext_event_id}"
    count = Proposal.query.filter_by(opportunity_slug=slug).count()
    assert count == 1
