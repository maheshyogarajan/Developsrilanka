"""
Delivery Ops Command Org Runner — Subagent E (2026-05-18).

The second org in the FIESTA AI-org economy. Where Acquisition Studio (D)
generates pipeline, Delivery Ops Command executes the work + protects margin.

Council canonical (VISIONARY_ECONOMY_COUNCIL_SYNTHESIS.md, Org 2):
  5 roles → 1 lifecycle per job-event:

    Workflow Orchestrator  proposes
        Proposal(status=queued | rejected_cap)
            ↓
    Queue Manager          capacity gate (20 active / org)
            ↓
    SLA Monitor            simulate cycle time → delivered | sla_breach
            ↓
    Quality Reviewer       payload-completeness gate → failed_qc on miss
            ↓
    Red-Team               forbidden-flag gate → failed_qc + hallucination_flag

  Then on delivered/sla_breach (after both gates pass):
    Contract + Deliverable + record_payment (auto invoice_paid)
    + sla_met (delivered only) + deliverable_accepted
    + cost_saved_verified (delivered only, if baseline > quote)
    + rollback (sla_breach only, magnitude 0.5)

  STATUS_REJECTED_CAP and STATUS_FAILED_QC: no contract, no payment.
  FAILED_QC emits rollback (already, from QC step) and optionally
  hallucination_flag (from red-team step). REJECTED_CAP just logs + skips —
  capacity is operational, not reputational (per spec note 8).

Trigger model: ON-EVENT (distinct from D's hourly sweep). Scans FIESTA events
table for trailing-window job events:
  filing_submitted, remittance_ird_ready, bank_statement_uploaded,
  sla_warning (synthetic; may not exist yet in v1 — handled gracefully).

Idempotency: opportunity_slug embeds external_event_id. Re-runs on the same
Event row are no-ops for blocked statuses. REJECTED_CAP + FAILED_QC do NOT
block — capacity may free, or the upstream may fix the payload.

Celery beat entry (orchestrator wires into celery_config):
    'delivery_ops_command_org-run-pass-10min': {
        'task': 'delivery_ops_command_org.run_pass',
        'schedule': crontab(minute='*/10'),  # offset from acquisition's :17
        'kwargs': {'since_minutes': 15},
    }

STATUS string widths: ai_org schemas declare status as VARCHAR(16). All
STATUS_* values used here are <=16 chars; see assert at module load.

All writes flow through ai_org_substrate helpers. The APPEND-ONLY Postgres
RULE blocks direct INSERT to reputation_event anyway, so the helpers are
the only path.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

ORG_SLUG: str = "delivery_ops_command"

# Proposal status values - bounded by VARCHAR(16) on Proposal.status.
STATUS_QUEUED = "queued"              # accepted into queue; capacity available
STATUS_IN_FLIGHT = "in_flight"        # actively being worked
STATUS_DELIVERED = "delivered"        # completed within SLA, QC + Red-Team pass
STATUS_SLA_BREACH = "sla_breach"      # completed but over SLA target
STATUS_FAILED_QC = "failed_qc"        # quality reviewer or red-team rejected
STATUS_REJECTED_CAP = "rejected_cap"  # queue at capacity; not accepted

# Sanity check — VARCHAR(16) cap. If anyone extends this list, this assert
# trips at import.
_ALL_STATUSES = (
    STATUS_QUEUED, STATUS_IN_FLIGHT, STATUS_DELIVERED,
    STATUS_SLA_BREACH, STATUS_FAILED_QC, STATUS_REJECTED_CAP,
)
assert max(len(s) for s in _ALL_STATUSES) <= 16, (
    f"STATUS_* values exceed VARCHAR(16): "
    f"{[(s, len(s)) for s in _ALL_STATUSES if len(s) > 16]}"
)

# Capacity gate — Queue Manager rejects when this many proposals are
# simultaneously in queued/in_flight for the org. v2 will tune against
# measured throughput.
QUEUE_CAPACITY: int = 20

# Recognised event types that trigger a Delivery Ops job. sla_warning is
# synthetic for v1 — handled gracefully if no rows exist in the events table.
RECOGNISED_EVENT_TYPES = (
    "filing_submitted",
    "remittance_ird_ready",
    "bank_statement_uploaded",
    "sla_warning",
)

# Map event_type → internal job_kind. For v1 they're 1:1 except sla_warning
# which is treated as a filing job (escalation back into the pipeline).
EVENT_TO_JOB_KIND: Dict[str, str] = {
    "filing_submitted": "filing_submitted",
    "remittance_ird_ready": "remittance_ird_ready",
    "bank_statement_uploaded": "bank_statement_uploaded",
    "sla_warning": "filing_submitted",  # escalation re-queues as a filing job
}

# Default scan window for the on-event scanner. Celery beat passes
# since_minutes=15 (15-minute trailing window); manual run_pass calls can
# override.
DEFAULT_SINCE_MINUTES: int = 60

# Five role slugs the council named (mirror INITIAL_ORGS in ai_org_substrate).
ROLE_SLUGS = (
    "workflow_orchestrator",
    "queue_manager",
    "sla_monitor",
    "quality_reviewer",
    "red_team",
)


# --------------------------------------------------------------------------- #
# Org-id + role-id resolution (cached — orgs/roles are stable)
# --------------------------------------------------------------------------- #

_ORG_ID_CACHE: Dict[str, int] = {}
_ROLE_ID_CACHE: Dict[str, int] = {}


def _resolve_org_id() -> Optional[int]:
    """Resolve ORG_SLUG → ai_org.id. Cached. Returns None if not seeded."""
    if ORG_SLUG in _ORG_ID_CACHE:
        return _ORG_ID_CACHE[ORG_SLUG]
    try:
        from ai_org_models import AIOrg
        org = AIOrg.query.filter_by(slug=ORG_SLUG).first()
        if org is None:
            log.warning(
                f"_resolve_org_id: org slug={ORG_SLUG!r} not found. "
                f"Run seed_initial_orgs() first."
            )
            return None
        _ORG_ID_CACHE[ORG_SLUG] = org.id
        return org.id
    except Exception as e:
        log.warning(f"_resolve_org_id failed: {e}")
        return None


def _resolve_role_ids() -> Dict[str, int]:
    """Resolve all 5 delivery_ops_command role slugs → ai_org_role.id. Cached.
    Returns empty dict if the org isn't seeded.
    """
    if _ROLE_ID_CACHE:
        return dict(_ROLE_ID_CACHE)
    org_id = _resolve_org_id()
    if org_id is None:
        return {}
    try:
        from ai_org_models import AIOrgRole
        roles = AIOrgRole.query.filter_by(ai_org_id=org_id).all()
        for r in roles:
            if r.role_slug in ROLE_SLUGS:
                _ROLE_ID_CACHE[r.role_slug] = r.id
        return dict(_ROLE_ID_CACHE)
    except Exception as e:
        log.warning(f"_resolve_role_ids failed: {e}")
        return {}


# --------------------------------------------------------------------------- #
# scan_for_jobs — read FIESTA events for trigger events in the trailing window
# --------------------------------------------------------------------------- #

def scan_for_jobs(
    since_minutes: int = DEFAULT_SINCE_MINUTES,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Read the events table (read-only) and return a list of job-triggering
    events in the trailing `since_minutes` window. Each item is a dict:

      {
        "job_kind":             internal kind (mapped from event_type),
        "external_event_id":    events.id,
        "external_event_type":  events.event_type (raw),
        "payload":              events.payload (or {}),
        "received_at":          events.created_at,
      }

    `now` injectable for tests. Defaults to datetime.utcnow().

    Recognised event types: filing_submitted, remittance_ird_ready,
    bank_statement_uploaded, sla_warning. sla_warning may not yet exist as
    an emitted event_type in v1 — the query handles its absence by simply
    returning no rows for that type.
    """
    out: List[Dict[str, Any]] = []
    now = now or datetime.utcnow()
    cutoff = now - timedelta(minutes=since_minutes)

    try:
        from event_models import Event
    except Exception as e:
        log.warning(f"scan_for_jobs: Event model import failed: {e}")
        return out

    try:
        rows = (
            Event.query
            .filter(Event.event_type.in_(RECOGNISED_EVENT_TYPES))
            .filter(Event.created_at >= cutoff)
            .order_by(Event.id.asc())
            .all()
        )
    except Exception as e:
        log.warning(f"scan_for_jobs query failed: {e}")
        return out

    for ev in rows:
        try:
            job_kind = EVENT_TO_JOB_KIND.get(ev.event_type)
            if job_kind is None:
                # Defensive: filter() should have excluded these. Skip if seen.
                continue
            out.append({
                "job_kind": job_kind,
                "external_event_id": ev.id,
                "external_event_type": ev.event_type,
                "payload": dict(ev.payload) if ev.payload else {},
                "received_at": ev.created_at,
            })
        except Exception as e:
            log.warning(f"scan_for_jobs: failed to package event {ev.id!r}: {e}")

    return out


# --------------------------------------------------------------------------- #
# Convenience re-exports (so callers can `from delivery_ops_command_org import
# compute_sla_target` without reaching into the helpers module).
# --------------------------------------------------------------------------- #

def compute_sla_target(job_kind: str) -> int:
    """Re-export of delivery_ops_command_proposals.compute_sla_target."""
    from delivery_ops_command_proposals import compute_sla_target as _impl
    return _impl(job_kind)


# --------------------------------------------------------------------------- #
# Idempotency + capacity helpers
# --------------------------------------------------------------------------- #

def _opportunity_slug(job_kind: str, external_event_id: int) -> str:
    """Compose `{job_kind}_{external_event_id}`. event_id-level idempotency:
    re-runs against the same Event row MUST not create a second proposal in
    a blocking status.
    """
    return f"{job_kind}_{external_event_id}"


def _blocking_proposal_exists(org_id: int, opportunity_slug: str) -> bool:
    """Idempotency guard. True if a Proposal with this opportunity_slug already
    exists in a status that should block re-proposal.

    Blocking statuses: queued, in_flight, delivered, sla_breach.
    Non-blocking: rejected_cap (capacity may have freed), failed_qc (downstream
    may have fixed the payload).
    """
    try:
        from ai_org_models import Proposal
        BLOCKING_STATUSES = (
            STATUS_QUEUED, STATUS_IN_FLIGHT,
            STATUS_DELIVERED, STATUS_SLA_BREACH,
        )
        existing = (
            Proposal.query
            .filter_by(
                proposer_org_id=org_id,
                opportunity_slug=opportunity_slug,
            )
            .filter(Proposal.status.in_(BLOCKING_STATUSES))
            .first()
        )
        return existing is not None
    except Exception as e:
        log.warning(f"_blocking_proposal_exists check failed: {e}")
        # Fail-closed for safety — better to skip than double-execute.
        return True


def _current_active_count(org_id: int) -> int:
    """Count Proposals for the org with status in (queued, in_flight). This is
    the Queue Manager's capacity input.

    Counts current snapshot — NOT cumulative. As proposals move to delivered /
    sla_breach / failed_qc / rejected_cap they leave the active count.
    """
    try:
        from ai_org_models import Proposal
        ACTIVE = (STATUS_QUEUED, STATUS_IN_FLIGHT)
        return (
            Proposal.query
            .filter_by(proposer_org_id=org_id)
            .filter(Proposal.status.in_(ACTIVE))
            .count()
        )
    except Exception as e:
        log.warning(f"_current_active_count failed: {e}")
        # Fail-closed: assume at capacity. Drops jobs we can't measure rather
        # than risking unbounded queue growth.
        return QUEUE_CAPACITY


# --------------------------------------------------------------------------- #
# run_pass — orchestrator entry point.
# --------------------------------------------------------------------------- #

def run_pass(
    since_minutes: int = DEFAULT_SINCE_MINUTES,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Single orchestrator pass: scan the trailing-window job events, run the
    5-role lifecycle per recognised event. Idempotent per external_event_id.
    Returns summary dict.

    Wired to Celery beat via register_celery_beat(). Safe to call multiple times.
    """
    from app import app as flask_app
    summary: Dict[str, Any] = {
        "jobs_seen": 0,
        "queued": 0,
        "delivered_within_sla": 0,
        "delivered_sla_breach": 0,
        "failed_qc": 0,
        "rejected_capacity": 0,
        "skipped_idempotent": 0,
        "errors": [],
    }

    with flask_app.app_context():
        try:
            from app import db
            from ai_org_models import Proposal, Contract, Deliverable
            from ai_org_substrate import (
                emit_reputation_event,
                record_payment,
            )
            from delivery_ops_command_proposals import (
                quote_for_kind,
                baseline_human_cost,
                quality_check,
                red_team_check,
                simulate_cycle_time,
                build_workflow_assignment,
                compute_sla_target as _compute_sla_target_impl,
            )
        except Exception as e:
            log.exception(f"run_pass imports failed: {e}")
            summary["errors"].append(f"import: {type(e).__name__}: {e}")
            return summary

        org_id = _resolve_org_id()
        if org_id is None:
            summary["errors"].append(f"{ORG_SLUG} org not seeded")
            return summary

        role_ids = _resolve_role_ids()
        red_team_role_id = role_ids.get("red_team")
        if red_team_role_id is None:
            log.warning(
                "run_pass: red_team role not resolvable; reviewer_role_id=NULL on deliverable"
            )

        jobs = scan_for_jobs(since_minutes=since_minutes, now=now)
        summary["jobs_seen"] = len(jobs)

        for job in jobs:
            try:
                job_kind = job["job_kind"]
                external_event_id = job["external_event_id"]
                external_event_type = job["external_event_type"]
                event_payload = job.get("payload") or {}
                slug = _opportunity_slug(job_kind, external_event_id)

                # ---- IDEMPOTENCY GATE ----
                if _blocking_proposal_exists(org_id, slug):
                    summary["skipped_idempotent"] += 1
                    continue

                # ---- 1. QUEUE MANAGER — capacity gate ----
                # Re-check capacity inside the loop because we may have added
                # rows earlier in this same pass.
                active_count = _current_active_count(org_id)
                if active_count >= QUEUE_CAPACITY:
                    # No contract, no rep event — capacity rejection is
                    # operational signal (per spec note 8). Just record the
                    # Proposal at REJECTED_CAP for the audit trail.
                    rej_payload = {
                        "external_event_id": external_event_id,
                        "external_event_type": external_event_type,
                        "job_kind": job_kind,
                        "assigned_workflow": None,
                        "rejection_reason": "queue_at_capacity",
                        "active_count_at_decision": active_count,
                        "queue_capacity": QUEUE_CAPACITY,
                        "evidence": event_payload,
                    }
                    proposal = Proposal(
                        proposer_org_id=org_id,
                        buyer_kind="fiesta_internal",
                        buyer_ref_id=0,
                        opportunity_slug=slug,
                        artifact_kind="completed_job",
                        artifact_payload=rej_payload,
                        quoted_price_lkr=quote_for_kind(job_kind),
                        quoted_eta_days=max(
                            1, math.ceil(_compute_sla_target_impl(job_kind) / 24.0),
                        ),
                        status=STATUS_REJECTED_CAP,
                    )
                    proposal.decided_at = datetime.utcnow()
                    db.session.add(proposal)
                    db.session.commit()
                    summary["rejected_capacity"] += 1
                    continue

                # ---- 2. WORKFLOW ORCHESTRATOR — propose ----
                sla_target_h = _compute_sla_target_impl(job_kind)
                workflow_assignment = build_workflow_assignment(job_kind)
                # build_workflow_assignment already deep-copies — merge keys
                # alongside.
                artifact_payload: Dict[str, Any] = {
                    "external_event_id": external_event_id,
                    "external_event_type": external_event_type,
                    "job_kind": job_kind,
                    "sla_target_h": sla_target_h,
                    "evidence": event_payload,
                }
                artifact_payload.update(workflow_assignment)
                # Propagate red-team / hallucination flags from the upstream
                # event payload if present — lets test fixtures + real future
                # signals drive the gate.
                for flag in ("hallucinated_field", "claim_inconsistent"):
                    if flag in event_payload:
                        artifact_payload[flag] = event_payload[flag]
                # Propagate test-only cycle-time forcing flags.
                for flag in ("_force_breach", "_force_on_time"):
                    if flag in event_payload:
                        artifact_payload[flag] = event_payload[flag]

                quote = quote_for_kind(job_kind)
                eta_days = max(1, math.ceil(sla_target_h / 24.0))

                proposal = Proposal(
                    proposer_org_id=org_id,
                    buyer_kind="fiesta_internal",
                    buyer_ref_id=0,
                    opportunity_slug=slug,
                    artifact_kind="completed_job",
                    artifact_payload=artifact_payload,
                    quoted_price_lkr=quote,
                    quoted_eta_days=eta_days,
                    status=STATUS_QUEUED,
                )
                db.session.add(proposal)
                db.session.commit()
                summary["queued"] += 1

                # ---- 3. SLA MONITOR — simulate execution + classify ----
                cycle_h = simulate_cycle_time(artifact_payload, sla_target_h)
                # Transient state: in_flight while simulated work happens.
                proposal.status = STATUS_IN_FLIGHT
                # Update payload with cycle time for downstream visibility.
                payload_now = dict(proposal.artifact_payload or {})
                payload_now["cycle_time_h"] = float(cycle_h)
                payload_now["sla_outcome"] = (
                    "within_sla" if cycle_h <= Decimal(sla_target_h) else "sla_breach"
                )
                proposal.artifact_payload = payload_now
                db.session.commit()

                # ---- 4. QUALITY REVIEWER ----
                qc_passed, missing_key = quality_check(payload_now)
                if not qc_passed:
                    qc_payload = dict(payload_now)
                    qc_payload["quality_review_decision"] = "fail"
                    qc_payload["quality_review_reason"] = (
                        f"missing or empty required key: {missing_key!r}"
                    )
                    proposal.artifact_payload = qc_payload
                    proposal.status = STATUS_FAILED_QC
                    proposal.decided_at = datetime.utcnow()
                    db.session.commit()
                    summary["failed_qc"] += 1

                    # Emit rollback rep event (ai_reliability axis).
                    emit_reputation_event(
                        ai_org_id=org_id,
                        event_type="rollback",
                        magnitude=1.0,
                        payload={
                            "proposal_id": proposal.id,
                            "opportunity_slug": slug,
                            "job_kind": job_kind,
                            "external_event_id": external_event_id,
                            "reason": "quality_review_failed",
                            "missing_key": missing_key,
                        },
                    )
                    continue

                # ---- 5. RED-TEAM REVIEW ----
                rt_passed, rt_reason = red_team_check(payload_now)
                if not rt_passed:
                    rt_payload = dict(payload_now)
                    rt_payload["red_team_decision"] = "reject"
                    rt_payload["red_team_reason"] = rt_reason
                    proposal.artifact_payload = rt_payload
                    proposal.status = STATUS_FAILED_QC
                    proposal.decided_at = datetime.utcnow()
                    db.session.commit()
                    summary["failed_qc"] += 1

                    # Emit rollback (consistent with QC fail) + hallucination_flag.
                    emit_reputation_event(
                        ai_org_id=org_id,
                        event_type="rollback",
                        magnitude=1.0,
                        payload={
                            "proposal_id": proposal.id,
                            "opportunity_slug": slug,
                            "job_kind": job_kind,
                            "external_event_id": external_event_id,
                            "reason": "red_team_rejected",
                            "red_team_reason": rt_reason,
                        },
                    )
                    emit_reputation_event(
                        ai_org_id=org_id,
                        event_type="hallucination_flag",
                        magnitude=1.0,
                        payload={
                            "proposal_id": proposal.id,
                            "opportunity_slug": slug,
                            "job_kind": job_kind,
                            "external_event_id": external_event_id,
                            "red_team_reason": rt_reason,
                        },
                    )
                    continue

                # ---- 6. CLASSIFY OUTCOME — delivered vs sla_breach ----
                within_sla = cycle_h <= Decimal(sla_target_h)
                final_payload = dict(payload_now)
                final_payload["quality_review_decision"] = "pass"
                final_payload["red_team_decision"] = "pass"

                if within_sla:
                    proposal.status = STATUS_DELIVERED
                    summary["delivered_within_sla"] += 1
                else:
                    proposal.status = STATUS_SLA_BREACH
                    summary["delivered_sla_breach"] += 1
                proposal.decided_at = datetime.utcnow()
                proposal.artifact_payload = final_payload
                db.session.commit()

                # ---- 7. CONTRACT + DELIVERABLE + PAYMENT ----
                contract = Contract(
                    proposal_id=proposal.id,
                    proposer_org_id=org_id,
                    buyer_kind="fiesta_internal",
                    buyer_ref_id=0,
                    terms_payload={
                        "from_proposal": proposal.id,
                        "opportunity_slug": slug,
                        "job_kind": job_kind,
                        "sla_target_h": sla_target_h,
                        "sla_outcome": "within_sla" if within_sla else "sla_breach",
                    },
                    contracted_price_lkr=quote,
                    milestone_count=1,
                    status="active",
                )
                db.session.add(contract)
                db.session.commit()

                deliverable_payload = dict(final_payload)
                deliverable_payload["cycle_time_h"] = float(cycle_h)
                deliverable = Deliverable(
                    contract_id=contract.id,
                    proposer_org_id=org_id,
                    milestone_number=1,
                    artifact_kind="completed_job",
                    artifact_payload=deliverable_payload,
                    accepted=True,
                    acceptor_kind="fiesta_internal",
                    acceptor_ref_id=0,
                    accepted_at=datetime.utcnow(),
                    red_team_pass=True,
                    red_team_reviewer_role_id=red_team_role_id,
                    hallucination_flag=False,
                    quality_score=Decimal("0.90"),
                    delivered_at=datetime.utcnow(),
                )
                db.session.add(deliverable)
                db.session.commit()

                # record_payment auto-emits invoice_paid on the economic axis.
                record_payment(
                    payer_kind="fiesta_internal",
                    payer_ref_id=0,
                    payee_kind="ai_org",
                    payee_ref_id=org_id,
                    amount_lkr=float(quote),
                    reason="contract_payment",
                    contract_id=contract.id,
                    deliverable_id=deliverable.id,
                )

                # deliverable_accepted always (work was accepted, just maybe late).
                emit_reputation_event(
                    ai_org_id=org_id,
                    event_type="deliverable_accepted",
                    magnitude=1.0,
                    source_contract_id=contract.id,
                    source_deliverable_id=deliverable.id,
                    payload={
                        "opportunity_slug": slug,
                        "job_kind": job_kind,
                        "external_event_id": external_event_id,
                        "cycle_time_h": float(cycle_h),
                        "sla_target_h": sla_target_h,
                        "quality_score": 0.90,
                    },
                )

                if within_sla:
                    # sla_met — ai_reliability axis.
                    emit_reputation_event(
                        ai_org_id=org_id,
                        event_type="sla_met",
                        magnitude=1.0,
                        source_contract_id=contract.id,
                        source_deliverable_id=deliverable.id,
                        payload={
                            "opportunity_slug": slug,
                            "job_kind": job_kind,
                            "external_event_id": external_event_id,
                            "cycle_time_h": float(cycle_h),
                            "sla_target_h": sla_target_h,
                        },
                    )

                    # cost_saved_verified — economic axis. Only emit when
                    # positive (baseline_human_cost > quote means we actually
                    # saved money vs human delivery).
                    baseline = baseline_human_cost(job_kind)
                    saving = baseline - quote
                    if saving > 0:
                        emit_reputation_event(
                            ai_org_id=org_id,
                            event_type="cost_saved_verified",
                            magnitude=float(saving),
                            source_contract_id=contract.id,
                            source_deliverable_id=deliverable.id,
                            payload={
                                "opportunity_slug": slug,
                                "job_kind": job_kind,
                                "external_event_id": external_event_id,
                                "baseline_human_cost_lkr": float(baseline),
                                "ai_quote_lkr": float(quote),
                                "saving_lkr": float(saving),
                            },
                        )
                else:
                    # sla_breach — rollback (ai_reliability, magnitude 0.5).
                    # No sla_met. No cost_saved_verified (margin eroded).
                    emit_reputation_event(
                        ai_org_id=org_id,
                        event_type="rollback",
                        magnitude=0.5,
                        source_contract_id=contract.id,
                        source_deliverable_id=deliverable.id,
                        payload={
                            "proposal_id": proposal.id,
                            "opportunity_slug": slug,
                            "job_kind": job_kind,
                            "external_event_id": external_event_id,
                            "reason": "sla_breach",
                            "cycle_time_h": float(cycle_h),
                            "sla_target_h": sla_target_h,
                        },
                    )

            except Exception as e:
                log.exception(
                    f"run_pass: failed processing job {job!r}: {e}"
                )
                summary["errors"].append(
                    f"{job.get('job_kind')}/{job.get('external_event_id')}: "
                    f"{type(e).__name__}: {e}"
                )
                try:
                    db.session.rollback()
                except Exception:
                    pass

    return summary


# --------------------------------------------------------------------------- #
# Celery wiring — orchestrator integration.
# --------------------------------------------------------------------------- #

try:
    from celery_config import app as celery_app

    @celery_app.task(name="delivery_ops_command_org.run_pass")
    def run_pass_task(since_minutes: int = 15) -> Dict[str, Any]:
        """Celery task wrapper. Orchestrator schedules this via beat — see
        register_celery_beat() for the schedule entry. Default since_minutes=15
        matches the 10-min beat cadence with a small overlap to absorb skew.
        """
        log.info(
            f"delivery_ops_command_org.run_pass: start (since_minutes={since_minutes})"
        )
        result = run_pass(since_minutes=since_minutes)
        log.info(f"delivery_ops_command_org.run_pass: done {result}")
        return result
except Exception:
    # Celery not importable in test/CLI contexts — run_pass still callable.
    run_pass_task = None  # type: ignore


def register_celery_beat() -> Dict[str, Dict[str, Any]]:
    """Beat-schedule entry. Orchestrator merges into celery_config.app.conf.beat_schedule.

    Schedule: every 10 minutes (event-driven org, faster cadence than
    Acquisition Studio's hourly). The crontab(minute='*/10') pattern fires at
    :00, :10, :20, :30, :40, :50 — offset from acquisition_studio at :17.
    """
    try:
        from celery.schedules import crontab
        schedule = crontab(minute="*/10")
    except Exception:
        schedule = 600  # 10-minute fallback
    return {
        "delivery_ops_command_org-run-pass-10min": {
            "task": "delivery_ops_command_org.run_pass",
            "schedule": schedule,
            "kwargs": {"since_minutes": 15},
        },
    }


__all__ = [
    "ORG_SLUG",
    "STATUS_QUEUED",
    "STATUS_IN_FLIGHT",
    "STATUS_DELIVERED",
    "STATUS_SLA_BREACH",
    "STATUS_FAILED_QC",
    "STATUS_REJECTED_CAP",
    "QUEUE_CAPACITY",
    "RECOGNISED_EVENT_TYPES",
    "scan_for_jobs",
    "compute_sla_target",
    "run_pass",
    "register_celery_beat",
    "_resolve_org_id",
    "_resolve_role_ids",
]
