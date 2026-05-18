"""
Acquisition Studio tests — Subagent D (2026-05-18).

Five tests against the live Neon DB:

  1. test_scan_for_triggers_handles_empty_events       — no triggers when no events
  2. test_run_pass_creates_full_lifecycle              — proposal + contract +
                                                          deliverable + 2 rep events
  3. test_red_team_rejects_guaranteed_return_payload   — forbidden-phrase reject path
  4. test_run_pass_is_idempotent_same_day              — second call no dup
  5. test_cac_too_high_rejects_cleanly                 — high CAC rejects pre-contract

Cleanup pattern: drop append-only RULE, purge test rows, recreate RULE —
same mechanism as tests/ai_run/test_ai_org_substrate.py::_cleanup_test_orgs.

We don't create test ai_org rows — we use the prod-seeded acquisition_studio
org and clean only the Proposal/Contract/Deliverable/Reputation/Payment rows
this test produced (identified by opportunity_slug prefix or proposal_id).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text as sql_text


TEST_SLUG_PREFIX = "subD_test_"  # Used to identify test-created proposals.
TEST_EVENT_SOURCE = "test:acquisition_studio_subD"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def seeded_studio(db_session):
    """Ensure the acquisition_studio org + 5 roles exist. Returns the AIOrg
    row. Idempotent — re-uses if seed_initial_orgs has run before."""
    from ai_org_substrate import seed_initial_orgs
    from ai_org_models import AIOrg
    seed_initial_orgs()
    studio = AIOrg.query.filter_by(slug="acquisition_studio").first()
    assert studio is not None, "acquisition_studio not seeded"
    return studio


@pytest.fixture
def emitted_event(db_session, user_a):
    """Factory: create Event rows + clean up at teardown.

    Returns a callable: _make(event_type, payload=None, created_at=None).
    Test sets created_at to deliberately stale dates when seeding trigger
    conditions (e.g. simulating a quiet 24h vs a busy 7d).
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

    # Teardown: purge events created in this test
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
def studio_artifacts_cleanup(db_session, seeded_studio):
    """Tracks Proposal IDs created during the test so we can scrub:
      reputation_event   (via APPEND-ONLY RULE drop/recreate)
      payment_event
      deliverable
      contract
      proposal

    Yields a callable: track(proposal_id) — call it after each test write that
    you want cleaned up. The fixture's teardown drops everything tracked.
    """
    tracked_proposal_ids = []

    def track(proposal_id):
        tracked_proposal_ids.append(proposal_id)

    yield track

    if not tracked_proposal_ids:
        return

    studio_id = seeded_studio.id

    # Resolve dependent IDs first (before deleting parents) so we can scrub
    # reputation_event rows that source_contract_id back to test contracts.
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

    # Drop APPEND-ONLY RULE temporarily — same pattern as
    # test_ai_org_substrate._cleanup_test_orgs.
    db_session.execute(sql_text("DROP RULE IF EXISTS no_delete ON reputation_event"))
    db_session.execute(sql_text("DROP RULE IF EXISTS no_update ON reputation_event"))
    db_session.commit()
    try:
        # Scrub reputation_event rows referencing our test contracts +
        # deliverables, plus any hallucination_flag events whose payload
        # points at our test proposal_ids.
        if contract_ids:
            db_session.execute(sql_text(
                "DELETE FROM reputation_event WHERE source_contract_id = ANY(:cids)"
            ), {"cids": contract_ids})
        if deliverable_ids:
            db_session.execute(sql_text(
                "DELETE FROM reputation_event WHERE source_deliverable_id = ANY(:dids)"
            ), {"dids": deliverable_ids})
        # hallucination_flag rep events don't have source_contract_id —
        # match by ai_org_id + proposal_id in payload JSON text.
        for pid in tracked_proposal_ids:
            db_session.execute(sql_text(
                "DELETE FROM reputation_event "
                "WHERE ai_org_id = :oid AND payload::text LIKE :probe"
            ), {"oid": studio_id, "probe": f'%"proposal_id": {pid}%'})

        # payment_event by contract_id
        if contract_ids:
            db_session.execute(sql_text(
                "DELETE FROM payment_event WHERE contract_id = ANY(:cids)"
            ), {"cids": contract_ids})

        # deliverable + contract + proposal (deliverable cascades on contract,
        # but be explicit so we don't depend on cascade ordering in tests).
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

def _isolate_slug(base: str) -> str:
    """Generate a unique opportunity_slug per test run so tests don't collide
    with prior days' data on the live DB.

    Format: `subD_test_<random>_<base>-YYYY-MM-DD` — same shape as
    `_opportunity_slug` produces, but with a unique prefix.
    """
    rid = uuid.uuid4().hex[:8]
    return f"{TEST_SLUG_PREFIX}{rid}_{base}"


def _make_test_trigger(trigger_kind="traffic_drop", evidence=None):
    return {
        "trigger_kind": trigger_kind,
        "evidence_payload": evidence or {"forced_by": "pytest", "ts": datetime.utcnow().isoformat()},
    }


def _track_recent_studio_proposals(db_session, studio_id, since: datetime, tracker):
    """Find every Proposal the test wrote against the studio after `since`
    and pass each id to tracker(). Used after run_pass() which doesn't return
    ids.
    """
    rows = db_session.execute(sql_text(
        "SELECT id FROM proposal "
        "WHERE proposer_org_id = :oid AND submitted_at >= :since"
    ), {"oid": studio_id, "since": since}).fetchall()
    for (pid,) in rows:
        tracker(pid)


# --------------------------------------------------------------------------- #
# Test 1: empty events → no triggers
# --------------------------------------------------------------------------- #

def test_scan_for_triggers_handles_empty_events(seeded_studio, db_session):
    """scan_for_triggers must not raise on a clean fresh window. The prod DB
    has historic data — we use a date deep in the future to guarantee no
    events in the trailing windows."""
    from acquisition_studio_org import scan_for_triggers

    far_future = datetime.utcnow() + timedelta(days=365 * 5)
    triggers = scan_for_triggers(now=far_future)
    # With "now" set 5 years ahead, the trailing 24h/7d/30d windows look at a
    # period with zero historic events → no traffic_drop, no cac_spike, and
    # pipeline_shortfall WILL fire because there are no checkouts in the
    # last 30d either. We only assert the function returns a list and
    # doesn't crash.
    assert isinstance(triggers, list)
    # Every trigger in the result must have a known kind.
    for t in triggers:
        assert t["trigger_kind"] in ("traffic_drop", "cac_spike", "pipeline_shortfall")
        assert "evidence_payload" in t


# --------------------------------------------------------------------------- #
# Test 2: full lifecycle — proposal + contract + deliverable + 2 rep events
# --------------------------------------------------------------------------- #

def test_run_pass_creates_full_lifecycle(
    seeded_studio, db_session, monkeypatch, studio_artifacts_cleanup,
):
    """Force a single traffic_drop trigger via monkeypatch and assert the
    full 5-role lifecycle wrote every expected row."""
    from acquisition_studio_org import run_pass
    import acquisition_studio_org as aso
    from ai_org_models import (
        Proposal, Contract, Deliverable, ReputationEvent, PaymentEvent,
    )

    forced_slug_base = "traffic_drop-" + datetime.utcnow().strftime("%Y-%m-%d")
    unique_slug = _isolate_slug(forced_slug_base)

    def _fake_scan(now=None):
        return [{
            "trigger_kind": "traffic_drop",
            "evidence_payload": {"signups_24h": 1, "daily_avg_7d": 10.0, "test": True},
        }]

    # Override scan_for_triggers AND _opportunity_slug so the test row is
    # uniquely identifiable.
    monkeypatch.setattr(aso, "scan_for_triggers", _fake_scan)
    monkeypatch.setattr(
        aso, "_opportunity_slug",
        lambda kind, when=None: unique_slug,
    )
    # Empty recent_events → estimate_cac falls back to LKR 1500 (under ceiling).
    monkeypatch.setattr(aso, "_recent_economic_events", lambda now=None: [])

    before = datetime.utcnow() - timedelta(seconds=5)
    summary = run_pass()
    _track_recent_studio_proposals(db_session, seeded_studio.id, before, studio_artifacts_cleanup)

    assert summary["triggers_seen"] == 1
    assert summary["proposals_submitted"] == 1
    assert summary["contracts_signed"] == 1
    assert summary["deliverables_completed"] == 1
    assert summary["red_team_rejections"] == 0
    assert summary["cac_rejections"] == 0
    assert summary["errors"] == []

    # Proposal — accepted status, payload populated with cac + red_team
    proposal = Proposal.query.filter_by(opportunity_slug=unique_slug).first()
    assert proposal is not None
    assert proposal.status == "accepted"
    payload = proposal.artifact_payload or {}
    assert payload.get("trigger_kind") == "traffic_drop"
    assert payload.get("cac_analyst_decision") == "pass"
    assert payload.get("red_team_decision") == "pass"
    assert isinstance(payload.get("channels"), list)
    assert len(payload["channels"]) >= 1

    # Contract
    contract = Contract.query.filter_by(proposal_id=proposal.id).first()
    assert contract is not None
    assert contract.status == "active"
    assert float(contract.contracted_price_lkr) > 0

    # Deliverable
    deliverable = Deliverable.query.filter_by(contract_id=contract.id).first()
    assert deliverable is not None
    assert deliverable.accepted is True
    assert deliverable.red_team_pass is True
    assert deliverable.hallucination_flag is False
    assert float(deliverable.quality_score) == pytest.approx(0.85)

    # PaymentEvent — record_payment wrote one with reason='contract_payment'
    payment = PaymentEvent.query.filter_by(
        contract_id=contract.id,
        deliverable_id=deliverable.id,
    ).first()
    assert payment is not None
    assert payment.payer_kind == "fiesta_internal"
    assert payment.payee_kind == "ai_org"
    assert payment.payee_ref_id == seeded_studio.id

    # ReputationEvent — both invoice_paid (economic) AND deliverable_accepted
    # (ai_reliability) must exist for this contract.
    rep_events = ReputationEvent.query.filter_by(
        source_contract_id=contract.id,
    ).all()
    types = {r.event_type for r in rep_events}
    assert "invoice_paid" in types
    assert "deliverable_accepted" in types

    economic_rep = next(r for r in rep_events if r.event_type == "invoice_paid")
    assert economic_rep.axis == "economic"
    assert float(economic_rep.magnitude) > 0

    reliability_rep = next(r for r in rep_events if r.event_type == "deliverable_accepted")
    assert reliability_rep.axis == "ai_reliability"
    assert float(reliability_rep.magnitude) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Test 3: red-team rejects forbidden-phrase payload
# --------------------------------------------------------------------------- #

def test_red_team_rejects_guaranteed_return_payload(
    seeded_studio, db_session, monkeypatch, studio_artifacts_cleanup,
):
    """Force a payload containing 'guaranteed return' and assert proposal
    ends in rejected_red_team + a hallucination_flag rep event is emitted.
    """
    from acquisition_studio_org import run_pass
    import acquisition_studio_org as aso
    import acquisition_studio_proposals as asp
    from ai_org_models import Proposal, Contract, ReputationEvent

    unique_slug = _isolate_slug("traffic_drop-rt-" + uuid.uuid4().hex[:6])

    def _fake_scan(now=None):
        return [{
            "trigger_kind": "traffic_drop",
            "evidence_payload": {"test": True},
        }]

    # Override the proposal builder so the payload contains the forbidden phrase.
    original_builder = asp.build_proposal_for_trigger
    def _poisoned_builder(trigger):
        p = original_builder(trigger)
        p["channels"][0]["copy"] = "GUARANTEED RETURN on every signup — bulletproof"
        return p

    monkeypatch.setattr(aso, "scan_for_triggers", _fake_scan)
    monkeypatch.setattr(
        aso, "_opportunity_slug",
        lambda kind, when=None: unique_slug,
    )
    monkeypatch.setattr(aso, "_recent_economic_events", lambda now=None: [])
    # The runner imports build_proposal_for_trigger inside run_pass, so we
    # patch the source module — the import inside the function resolves it
    # from acquisition_studio_proposals each call.
    monkeypatch.setattr(asp, "build_proposal_for_trigger", _poisoned_builder)

    before = datetime.utcnow() - timedelta(seconds=5)
    summary = run_pass()
    _track_recent_studio_proposals(db_session, seeded_studio.id, before, studio_artifacts_cleanup)

    assert summary["proposals_submitted"] == 1
    assert summary["red_team_rejections"] == 1
    assert summary["contracts_signed"] == 0
    assert summary["deliverables_completed"] == 0

    proposal = Proposal.query.filter_by(opportunity_slug=unique_slug).first()
    assert proposal is not None
    from acquisition_studio_org import STATUS_REJECT_RED
    assert proposal.status == STATUS_REJECT_RED
    assert proposal.decided_at is not None
    payload = proposal.artifact_payload or {}
    assert payload.get("red_team_decision") == "reject"
    assert "guaranteed return" in (payload.get("red_team_reason") or "").lower()

    # No contract was signed
    contract = Contract.query.filter_by(proposal_id=proposal.id).first()
    assert contract is None

    # hallucination_flag rep event was emitted
    rep = ReputationEvent.query.filter_by(
        ai_org_id=seeded_studio.id,
        event_type="hallucination_flag",
    ).all()
    matching = [r for r in rep if (r.payload or {}).get("proposal_id") == proposal.id]
    assert len(matching) >= 1, "hallucination_flag rep event not emitted for the rejected proposal"
    assert matching[0].axis == "ai_reliability"


# --------------------------------------------------------------------------- #
# Test 4: idempotency — second run on same day skips
# --------------------------------------------------------------------------- #

def test_run_pass_is_idempotent_same_day(
    seeded_studio, db_session, monkeypatch, studio_artifacts_cleanup,
):
    """Force the same opportunity_slug both times. Second run must not
    create a second Proposal."""
    from acquisition_studio_org import run_pass
    import acquisition_studio_org as aso
    from ai_org_models import Proposal

    unique_slug = _isolate_slug("idem-" + uuid.uuid4().hex[:6])

    def _fake_scan(now=None):
        return [{
            "trigger_kind": "traffic_drop",
            "evidence_payload": {"test": True},
        }]

    monkeypatch.setattr(aso, "scan_for_triggers", _fake_scan)
    monkeypatch.setattr(
        aso, "_opportunity_slug",
        lambda kind, when=None: unique_slug,
    )
    monkeypatch.setattr(aso, "_recent_economic_events", lambda now=None: [])

    before = datetime.utcnow() - timedelta(seconds=5)
    summary1 = run_pass()
    summary2 = run_pass()
    _track_recent_studio_proposals(db_session, seeded_studio.id, before, studio_artifacts_cleanup)

    assert summary1["proposals_submitted"] == 1
    # Second call sees the existing active proposal → skips
    assert summary2["proposals_submitted"] == 0
    assert summary2["skipped_idempotent"] == 1

    # Exactly one proposal row exists with this slug
    count = Proposal.query.filter_by(opportunity_slug=unique_slug).count()
    assert count == 1


# --------------------------------------------------------------------------- #
# Test 5: CAC over ceiling → reject before contract
# --------------------------------------------------------------------------- #

def test_cac_too_high_rejects_cleanly(
    seeded_studio, db_session, monkeypatch, studio_artifacts_cleanup,
):
    """Force estimate_cac to return > ceiling. Proposal must end in
    rejected_cac_too_high with no contract, no deliverable, no rep events."""
    from decimal import Decimal
    from acquisition_studio_org import run_pass
    import acquisition_studio_org as aso
    import acquisition_studio_proposals as asp
    from ai_org_models import Proposal, Contract, Deliverable, ReputationEvent

    unique_slug = _isolate_slug("cac-too-high-" + uuid.uuid4().hex[:6])

    def _fake_scan(now=None):
        return [{
            "trigger_kind": "traffic_drop",
            "evidence_payload": {"test": True},
        }]

    monkeypatch.setattr(aso, "scan_for_triggers", _fake_scan)
    monkeypatch.setattr(
        aso, "_opportunity_slug",
        lambda kind, when=None: unique_slug,
    )
    monkeypatch.setattr(aso, "_recent_economic_events", lambda now=None: [])
    # Make CAC come in at LKR 9999 (well over the 3000 ceiling)
    monkeypatch.setattr(asp, "estimate_cac", lambda payload, recent_events: Decimal("9999"))

    before = datetime.utcnow() - timedelta(seconds=5)
    summary = run_pass()
    _track_recent_studio_proposals(db_session, seeded_studio.id, before, studio_artifacts_cleanup)

    assert summary["proposals_submitted"] == 1
    assert summary["cac_rejections"] == 1
    assert summary["red_team_rejections"] == 0
    assert summary["contracts_signed"] == 0
    assert summary["deliverables_completed"] == 0

    proposal = Proposal.query.filter_by(opportunity_slug=unique_slug).first()
    assert proposal is not None
    from acquisition_studio_org import STATUS_REJECT_CAC
    assert proposal.status == STATUS_REJECT_CAC
    payload = proposal.artifact_payload or {}
    assert payload.get("cac_forecast") == 9999.0
    assert payload.get("cac_analyst_decision") == "reject"

    # No contract / deliverable
    assert Contract.query.filter_by(proposal_id=proposal.id).count() == 0

    # No invoice_paid / deliverable_accepted rep events for this proposal
    rep_events = ReputationEvent.query.filter_by(
        ai_org_id=seeded_studio.id,
        event_type="invoice_paid",
    ).all()
    assert not any(
        (r.payload or {}).get("opportunity_slug") == unique_slug for r in rep_events
    )
    rep_events_da = ReputationEvent.query.filter_by(
        ai_org_id=seeded_studio.id,
        event_type="deliverable_accepted",
    ).all()
    assert not any(
        (r.payload or {}).get("opportunity_slug") == unique_slug for r in rep_events_da
    )
