"""
Acquisition Studio Org Runner — Subagent D (2026-05-18).

The first end-to-end test of whether the AI-org economy can generate
AI-attributed paid conversions on the substrate Subagents A/B/C shipped.

Council canonical (VISIONARY_ECONOMY_COUNCIL_SYNTHESIS.md, Org 1):
  5 roles → 1 deliverable lifecycle per fired trigger:

    Channel Strategist  proposes
        Proposal(status=submitted)
            ↓
    CAC Analyst         evaluates cac_forecast
        Proposal.artifact_payload['cac_forecast'] = X
        status="ready_contract"  OR  "reject_cac_high"
            ↓
    Red Team            reviews payload
        status="reject_red_team"  (+ hallucination_flag rep event)
        OR pass through
            ↓
    Outreach Closer     creates Contract + Deliverable
        Contract(status=active) + Deliverable(accepted=True)
        record_payment(reason=contract_payment)   → invoice_paid rep event
        emit deliverable_accepted rep event

    NOTE on status string widths: ai_org schemas declare status as VARCHAR(16).
    All status values used here MUST be ≤ 16 chars. See STATUS_* constants.

Idempotency: scan emits one opportunity_slug per (trigger_kind, iso_date).
Re-runs on the same day skip already-active opportunity_slugs.

All writes go through ai_org_substrate helpers — direct INSERT to
reputation_event is rejected by the APPEND-ONLY Postgres RULE anyway, so the
helpers ARE the only path. Money flow: payer_kind='fiesta_internal',
payer_ref_id=0 (representing the FIESTA business unit funding the campaign).

Celery beat entry (orchestrator wires this into celery_config):
    'acquisition_studio_org-run-pass-hourly': {
        'task': 'acquisition_studio_org.run_pass',
        'schedule': crontab(minute=17),  # hourly @ :17
    }
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

STUDIO_SLUG: str = "acquisition_studio"

# Proposal status values — bounded to <=16 chars by the VARCHAR(16) on
# Proposal.status (ai_org_models.py L165). Same constraint applies to
# contract.status and ai_org.status.
STATUS_SUBMITTED = "submitted"            # initial state after Channel Strategist
STATUS_READY_CONTRACT = "ready_contract"  # CAC analyst passed
STATUS_REJECT_CAC = "reject_cac_high"     # CAC analyst rejected
STATUS_REJECT_RED = "reject_red_team"     # Red-team rejected
STATUS_ACCEPTED = "accepted"              # Outreach Closer signed contract

# Scan windows + thresholds — council line 26 ("rescan hourly; trigger only
# when the underlying signal is anomalous relative to its trailing baseline").
SIGNUP_24H_WINDOW = timedelta(hours=24)
SIGNUP_7D_WINDOW = timedelta(days=7)
SIGNUP_DROP_RATIO = 0.5  # fire if 24h count < 50% of trailing 7d daily avg

CAC_24H_WINDOW = timedelta(hours=24)
CAC_BASELINE_WINDOW = timedelta(days=7)
CAC_SPIKE_RATIO = 2.0  # fire if 24h failure ratio > 2x 7d baseline

PIPELINE_WINDOW = timedelta(days=30)
PIPELINE_MIN_CONVERSIONS = 10

# Five role slugs the council named for this org (mirror INITIAL_ORGS in
# ai_org_substrate.py). Cached resolution returns id per slug.
ROLE_SLUGS = (
    "channel_strategist",
    "content_operator",
    "outreach_closer",
    "cac_analyst",
    "red_team",
)


# --------------------------------------------------------------------------- #
# Org-id + role-id resolution (cached — orgs/roles are stable)
# --------------------------------------------------------------------------- #

_ORG_ID_CACHE: Dict[str, int] = {}
_ROLE_ID_CACHE: Dict[str, int] = {}  # role_slug -> ai_org_role.id (for studio)


def _resolve_org_id() -> Optional[int]:
    """Resolve STUDIO_SLUG → ai_org.id. Cached. Returns None if not seeded."""
    if STUDIO_SLUG in _ORG_ID_CACHE:
        return _ORG_ID_CACHE[STUDIO_SLUG]
    try:
        from ai_org_models import AIOrg
        org = AIOrg.query.filter_by(slug=STUDIO_SLUG).first()
        if org is None:
            log.warning(
                f"_resolve_org_id: org slug={STUDIO_SLUG!r} not found. "
                f"Run seed_initial_orgs() first."
            )
            return None
        _ORG_ID_CACHE[STUDIO_SLUG] = org.id
        return org.id
    except Exception as e:
        log.warning(f"_resolve_org_id failed: {e}")
        return None


def _resolve_role_ids() -> Dict[str, int]:
    """Resolve all 5 acquisition-studio role slugs → ai_org_role.id. Cached.
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
# scan_for_triggers — read FIESTA events for anomalous patterns
# --------------------------------------------------------------------------- #

def scan_for_triggers(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Read the events table (read-only) and return a list of triggered
    opportunities. Each item is a dict with:

      trigger_kind:      one of 'traffic_drop', 'cac_spike', 'pipeline_shortfall'
      evidence_payload:  measurements that fired the trigger

    `now` injectable for tests. Defaults to datetime.utcnow().

    v1 deliberately does NOT scan for 'campaign_slot_opened' Event rows —
    EVENT_AXIS_MAP would need a new entry for `campaign_proposed` and the
    upstream emitter doesn't exist yet. Adding when the slot-opening signal
    ships from the cockpit.
    """
    triggers: List[Dict[str, Any]] = []
    now = now or datetime.utcnow()

    try:
        from event_models import Event
    except Exception as e:
        log.warning(f"scan_for_triggers: Event model import failed: {e}")
        return triggers

    # ---- (1) traffic_drop ---------------------------------------------------
    try:
        last_24h_cutoff = now - SIGNUP_24H_WINDOW
        last_7d_cutoff = now - SIGNUP_7D_WINDOW
        signups_24h = (
            Event.query
            .filter(Event.event_type == "signup")
            .filter(Event.created_at >= last_24h_cutoff)
            .count()
        )
        signups_7d = (
            Event.query
            .filter(Event.event_type == "signup")
            .filter(Event.created_at >= last_7d_cutoff)
            .count()
        )
        daily_avg_7d = signups_7d / 7.0 if signups_7d > 0 else 0.0
        # Fire only when the trailing baseline is non-trivial — avoid
        # cold-start oscillation when there's no data.
        if daily_avg_7d >= 1.0 and signups_24h < (daily_avg_7d * SIGNUP_DROP_RATIO):
            triggers.append({
                "trigger_kind": "traffic_drop",
                "evidence_payload": {
                    "signups_24h": signups_24h,
                    "signups_7d": signups_7d,
                    "daily_avg_7d": round(daily_avg_7d, 2),
                    "drop_ratio_observed": round(
                        signups_24h / daily_avg_7d, 3
                    ) if daily_avg_7d > 0 else None,
                    "drop_threshold": SIGNUP_DROP_RATIO,
                    "scanned_at": now.isoformat(),
                },
            })
    except Exception as e:
        log.warning(f"scan_for_triggers traffic_drop check failed: {e}")

    # ---- (2) cac_spike ------------------------------------------------------
    try:
        cac_24h_cutoff = now - CAC_24H_WINDOW
        cac_baseline_cutoff = now - CAC_BASELINE_WINDOW

        failed_24h = (
            Event.query
            .filter(Event.event_type == "payment_failed")
            .filter(Event.created_at >= cac_24h_cutoff)
            .count()
        )
        completed_24h = (
            Event.query
            .filter(Event.event_type == "checkout_completed")
            .filter(Event.created_at >= cac_24h_cutoff)
            .count()
        )
        failed_7d = (
            Event.query
            .filter(Event.event_type == "payment_failed")
            .filter(Event.created_at >= cac_baseline_cutoff)
            .count()
        )
        completed_7d = (
            Event.query
            .filter(Event.event_type == "checkout_completed")
            .filter(Event.created_at >= cac_baseline_cutoff)
            .count()
        )

        # ratio = failures per successful checkout. Higher = worse CAC.
        ratio_24h = (failed_24h / completed_24h) if completed_24h > 0 else None
        ratio_baseline = (failed_7d / completed_7d) if completed_7d > 0 else None

        # Fire only when baseline is established (>=2 conversions in trailing
        # 7d to avoid div-by-zero noise) AND 24h ratio is >2x baseline.
        if (
            ratio_24h is not None
            and ratio_baseline is not None
            and completed_7d >= 2
            and ratio_baseline > 0
            and ratio_24h > (ratio_baseline * CAC_SPIKE_RATIO)
        ):
            triggers.append({
                "trigger_kind": "cac_spike",
                "evidence_payload": {
                    "failed_24h": failed_24h,
                    "completed_24h": completed_24h,
                    "failed_7d": failed_7d,
                    "completed_7d": completed_7d,
                    "ratio_24h": round(ratio_24h, 3),
                    "ratio_baseline": round(ratio_baseline, 3),
                    "spike_threshold": CAC_SPIKE_RATIO,
                    "scanned_at": now.isoformat(),
                },
            })
    except Exception as e:
        log.warning(f"scan_for_triggers cac_spike check failed: {e}")

    # ---- (3) pipeline_shortfall --------------------------------------------
    try:
        pipeline_cutoff = now - PIPELINE_WINDOW
        paid_conversions_30d = (
            Event.query
            .filter(Event.event_type == "checkout_completed")
            .filter(Event.created_at >= pipeline_cutoff)
            .count()
        )
        if paid_conversions_30d < PIPELINE_MIN_CONVERSIONS:
            triggers.append({
                "trigger_kind": "pipeline_shortfall",
                "evidence_payload": {
                    "paid_conversions_30d": paid_conversions_30d,
                    "threshold": PIPELINE_MIN_CONVERSIONS,
                    "scanned_at": now.isoformat(),
                },
            })
    except Exception as e:
        log.warning(f"scan_for_triggers pipeline_shortfall check failed: {e}")

    return triggers


def _recent_economic_events(now: Optional[datetime] = None) -> List[Any]:
    """Pull recent payment_failed + checkout_completed events for the CAC
    estimator. Read-only against the events table. Bounded list (last 7d).
    """
    now = now or datetime.utcnow()
    try:
        from event_models import Event
        cutoff = now - timedelta(days=7)
        rows = (
            Event.query
            .filter(Event.event_type.in_(["payment_failed", "checkout_completed"]))
            .filter(Event.created_at >= cutoff)
            .order_by(Event.id.desc())
            .limit(500)
            .all()
        )
        return list(rows)
    except Exception as e:
        log.warning(f"_recent_economic_events query failed: {e}")
        return []


# --------------------------------------------------------------------------- #
# run_pass — the orchestrator entry point.
# --------------------------------------------------------------------------- #

def _opportunity_slug(trigger_kind: str, when: Optional[datetime] = None) -> str:
    """Compose `{trigger_kind}-{YYYY-MM-DD}`. Day-level idempotency: re-runs
    inside the same UTC day MUST skip already-handled triggers.
    """
    when = when or datetime.utcnow()
    return f"{trigger_kind}-{when.strftime('%Y-%m-%d')}"


def _active_proposal_exists(org_id: int, opportunity_slug: str) -> bool:
    """Idempotency guard. True if a Proposal with this opportunity_slug already
    exists for the org in a non-terminal status (submitted / ready_for_contract
    / accepted / active). Rejected-status rows do not block re-proposal on a
    later day because the slug includes iso_date.
    """
    try:
        from ai_org_models import Proposal
        BLOCKING_STATUSES = (
            STATUS_SUBMITTED, STATUS_READY_CONTRACT, STATUS_ACCEPTED, "active",
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
        log.warning(f"_active_proposal_exists check failed: {e}")
        # Fail-closed for safety — better to skip a slot than double-execute.
        return True


def run_pass(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Single orchestrator pass: scan triggers, run 5-role lifecycle per
    triggered slot. Idempotent same-day. Returns summary dict.

    Wired to Celery beat (acquisition_studio_org.run_pass — see
    register_celery_beat()). Safe to call multiple times.
    """
    from app import app as flask_app
    summary: Dict[str, Any] = {
        "triggers_seen": 0,
        "proposals_submitted": 0,
        "contracts_signed": 0,
        "deliverables_completed": 0,
        "red_team_rejections": 0,
        "cac_rejections": 0,
        "skipped_idempotent": 0,
        "errors": [],
    }

    # Lazy import — Flask app context handles SQLAlchemy session.
    with flask_app.app_context():
        try:
            from app import db
            from ai_org_models import Proposal, Contract, Deliverable
            from ai_org_substrate import (
                emit_reputation_event,
                record_payment,
            )
            from acquisition_studio_proposals import (
                CAC_CEILING_LKR,
                ESTIMATED_LEAD_VALUE_LKR,
                build_proposal_for_trigger,
                estimate_cac,
                red_team_check,
            )
        except Exception as e:
            log.exception(f"run_pass imports failed: {e}")
            summary["errors"].append(f"import: {type(e).__name__}: {e}")
            return summary

        org_id = _resolve_org_id()
        if org_id is None:
            summary["errors"].append("acquisition_studio org not seeded")
            return summary

        role_ids = _resolve_role_ids()
        red_team_role_id = role_ids.get("red_team")
        # If red_team role is missing, we still run — deliverable.red_team_reviewer_role_id
        # is nullable. But we log this.
        if red_team_role_id is None:
            log.warning("run_pass: red_team role not resolvable; reviewer_role_id=NULL on deliverable")

        triggers = scan_for_triggers(now=now)
        summary["triggers_seen"] = len(triggers)
        recent_events = _recent_economic_events(now=now) if triggers else []

        for trigger in triggers:
            try:
                kind = trigger["trigger_kind"]
                slug = _opportunity_slug(kind, when=now)

                # ---- IDEMPOTENCY GATE ----
                if _active_proposal_exists(org_id, slug):
                    summary["skipped_idempotent"] += 1
                    continue

                # ---- 1. Channel Strategist proposes ----
                artifact_payload = build_proposal_for_trigger(trigger)
                proposal = Proposal(
                    proposer_org_id=org_id,
                    buyer_kind="fiesta_internal",
                    buyer_ref_id=0,
                    opportunity_slug=slug,
                    artifact_kind="campaign_plan",
                    artifact_payload=artifact_payload,
                    quoted_price_lkr=ESTIMATED_LEAD_VALUE_LKR,
                    quoted_eta_days=7,
                    status=STATUS_SUBMITTED,
                )
                db.session.add(proposal)
                db.session.commit()
                summary["proposals_submitted"] += 1

                # ---- 2. CAC Analyst evaluates ----
                cac_forecast = estimate_cac(artifact_payload, recent_events)
                # Decimal → float in JSON-safe way; store both forecast + decision.
                new_payload = dict(artifact_payload)
                new_payload["cac_forecast"] = float(cac_forecast)
                new_payload["cac_ceiling"] = float(CAC_CEILING_LKR)
                new_payload["cac_analyst_decision"] = (
                    "pass" if cac_forecast <= CAC_CEILING_LKR else "reject"
                )
                proposal.artifact_payload = new_payload

                if cac_forecast > CAC_CEILING_LKR:
                    proposal.status = STATUS_REJECT_CAC
                    proposal.decided_at = datetime.utcnow()
                    db.session.commit()
                    summary["cac_rejections"] += 1
                    continue
                else:
                    proposal.status = STATUS_READY_CONTRACT
                    db.session.commit()

                # ---- 3. Red-Team review ----
                rt_passed, rt_reason = red_team_check(new_payload)
                if not rt_passed:
                    proposal.status = STATUS_REJECT_RED
                    proposal.decided_at = datetime.utcnow()
                    # Log rejection reason into the payload for audit.
                    rt_payload = dict(new_payload)
                    rt_payload["red_team_decision"] = "reject"
                    rt_payload["red_team_reason"] = rt_reason
                    proposal.artifact_payload = rt_payload
                    db.session.commit()
                    summary["red_team_rejections"] += 1

                    # Emit a hallucination_flag rep event (AI-reliability axis,
                    # negative magnitude — sends the signal the council intended).
                    emit_reputation_event(
                        ai_org_id=org_id,
                        event_type="hallucination_flag",
                        magnitude=1.0,
                        payload={
                            "proposal_id": proposal.id,
                            "opportunity_slug": slug,
                            "trigger_kind": kind,
                            "red_team_reason": rt_reason,
                        },
                    )
                    continue

                # Mark red-team pass on the proposal payload (audit trail).
                final_payload = dict(new_payload)
                final_payload["red_team_decision"] = "pass"
                proposal.artifact_payload = final_payload
                db.session.commit()

                # ---- 4. Outreach Closer — Contract + Deliverable ----
                contract = Contract(
                    proposal_id=proposal.id,
                    proposer_org_id=org_id,
                    buyer_kind="fiesta_internal",
                    buyer_ref_id=0,
                    terms_payload={
                        "from_proposal": proposal.id,
                        "opportunity_slug": slug,
                    },
                    contracted_price_lkr=ESTIMATED_LEAD_VALUE_LKR,
                    milestone_count=1,
                    status="active",
                )
                db.session.add(contract)
                db.session.commit()
                summary["contracts_signed"] += 1

                proposal.status = STATUS_ACCEPTED
                proposal.decided_at = datetime.utcnow()
                db.session.commit()

                deliverable = Deliverable(
                    contract_id=contract.id,
                    proposer_org_id=org_id,
                    milestone_number=1,
                    artifact_kind="campaign_plan_v1",
                    artifact_payload=final_payload,
                    accepted=True,
                    acceptor_kind="fiesta_internal",
                    acceptor_ref_id=0,
                    accepted_at=datetime.utcnow(),
                    red_team_pass=True,
                    red_team_reviewer_role_id=red_team_role_id,
                    hallucination_flag=False,
                    quality_score=Decimal("0.85"),
                    delivered_at=datetime.utcnow(),
                )
                db.session.add(deliverable)
                db.session.commit()
                summary["deliverables_completed"] += 1

                # ---- 5. Money + reputation events ----
                # record_payment auto-emits the payee-side invoice_paid
                # event on the economic axis.
                record_payment(
                    payer_kind="fiesta_internal",
                    payer_ref_id=0,
                    payee_kind="ai_org",
                    payee_ref_id=org_id,
                    amount_lkr=float(ESTIMATED_LEAD_VALUE_LKR),
                    reason="contract_payment",
                    contract_id=contract.id,
                    deliverable_id=deliverable.id,
                )

                # Explicit deliverable_accepted rep event (AI-reliability axis).
                # record_payment only handles the economic side; we want the
                # ai_reliability axis credited too.
                emit_reputation_event(
                    ai_org_id=org_id,
                    event_type="deliverable_accepted",
                    magnitude=1.0,
                    source_contract_id=contract.id,
                    source_deliverable_id=deliverable.id,
                    payload={
                        "opportunity_slug": slug,
                        "trigger_kind": kind,
                        "quality_score": 0.85,
                    },
                )

            except Exception as e:
                log.exception(
                    f"run_pass: failed processing trigger {trigger!r}: {e}"
                )
                summary["errors"].append(
                    f"{trigger.get('trigger_kind')}: {type(e).__name__}: {e}"
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

    @celery_app.task(name="acquisition_studio_org.run_pass")
    def run_pass_task() -> Dict[str, Any]:
        """Celery task wrapper. Orchestrator schedules this via beat —
        see register_celery_beat() for the schedule entry.
        """
        log.info("acquisition_studio_org.run_pass: start")
        result = run_pass()
        log.info(f"acquisition_studio_org.run_pass: done {result}")
        return result
except Exception:
    # Celery not importable in test/CLI contexts — run_pass still callable.
    run_pass_task = None  # type: ignore


def register_celery_beat() -> Dict[str, Dict[str, Any]]:
    """Beat-schedule entry. Orchestrator merges into celery_config.app.conf.beat_schedule.

    Schedule: hourly at minute :17 (offset from other AI-org tasks at :05 and
    :03 to spread load). One pass per hour is plenty for the trigger windows
    (24h / 7d / 30d).
    """
    try:
        from celery.schedules import crontab
        schedule = crontab(minute=17)
    except Exception:
        schedule = 3600  # 1 hour fallback
    return {
        "acquisition_studio_org-run-pass-hourly": {
            "task": "acquisition_studio_org.run_pass",
            "schedule": schedule,
            "kwargs": {},
        },
    }


__all__ = [
    "STUDIO_SLUG",
    "STATUS_SUBMITTED",
    "STATUS_READY_CONTRACT",
    "STATUS_REJECT_CAC",
    "STATUS_REJECT_RED",
    "STATUS_ACCEPTED",
    "scan_for_triggers",
    "run_pass",
    "register_celery_beat",
    "_resolve_org_id",
    "_resolve_role_ids",
]
