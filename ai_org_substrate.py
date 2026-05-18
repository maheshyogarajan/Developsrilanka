"""
AI-Org Substrate — helper functions for the data layer (Subagent A, 2026-05-18).

Canonical entry points for:
  * seed_initial_orgs() — idempotent seed of 3 council-named orgs + 5 roles each.
  * emit_reputation_event() — write to the APPEND-ONLY ledger.
  * record_payment() — write PaymentEvent + emit the payee-side reputation event.
  * claim_attribution() — write AttributionLedger row (ON CONFLICT no-op).
  * verify_attribution() — set verified_at + emit attribution_verified event.

Council synthesis: G:/My Drive/CEO OS/working files/_cockpit_fiesta/VISIONARY_ECONOMY_COUNCIL_SYNTHESIS.md

Design intent:
  * Best-effort write semantics for reputation events (analytics, not
    transactional). Same as events.emit() — a failed insert is a logged warning.
    BUT: the helpers DO raise on programmer error (unknown axis, missing org),
    because those are bugs, not transient failures.
  * STANDARD_EVENTS is the canonical event_type list. Free-form strings still
    permitted on the model (so new event types don't need a code release), but
    helper validates against the list and warns on unknown.
  * EVENT_AXIS_MAP — the council's 3-axis assignment. If caller doesn't pass
    axis, helper derives from event_type.
"""
import logging
from typing import Optional, Any, Dict

from app import db
from ai_org_models import (
    AIOrg,
    AIOrgRole,
    ReputationEvent,
    PaymentEvent,
    AttributionLedger,
)

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Council canonical: 3 axes, named events per axis
# --------------------------------------------------------------------------- #

AXIS_ECONOMIC = "economic"
AXIS_HUMAN_IMPACT = "human_impact"
AXIS_AI_RELIABILITY = "ai_reliability"

VALID_AXES = {AXIS_ECONOMIC, AXIS_HUMAN_IMPACT, AXIS_AI_RELIABILITY}

# Council-named events per axis (VISIONARY_ECONOMY_COUNCIL_SYNTHESIS.md L37-41).
EVENT_AXIS_MAP: Dict[str, str] = {
    # Economic
    "contract_won": AXIS_ECONOMIC,
    "invoice_paid": AXIS_ECONOMIC,
    "renewal": AXIS_ECONOMIC,
    "cost_saved_verified": AXIS_ECONOMIC,
    # Human impact
    "human_acceptance": AXIS_HUMAN_IMPACT,
    "customer_nps_delta": AXIS_HUMAN_IMPACT,
    "manual_hours_saved_verified": AXIS_HUMAN_IMPACT,
    "escalation_resolved": AXIS_HUMAN_IMPACT,
    # AI reliability (positive)
    "deliverable_accepted": AXIS_AI_RELIABILITY,
    "redteam_pass": AXIS_AI_RELIABILITY,
    "sla_met": AXIS_AI_RELIABILITY,
    # AI reliability (negative — magnitude should be negative when emitted)
    "rollback": AXIS_AI_RELIABILITY,
    "compliance_breach": AXIS_AI_RELIABILITY,
    "hallucination_flag": AXIS_AI_RELIABILITY,
    # Subagent-B-emitted
    "attribution_verified": AXIS_AI_RELIABILITY,
    # ─────────────────────────────────────────────────────────────────────
    # PCSE v1.0 (2026-05-19) — added per Strategist D + council refinement.
    # 4 automation events (pin_check_completed / tin_registration_completed /
    # tax_type_activated / ird_submission_executed) defer to PCSE v1.1.
    # ─────────────────────────────────────────────────────────────────────
    # Comms-channel events (Human Impact — proxy for outreach reach)
    "email_sent": AXIS_HUMAN_IMPACT,
    "whatsapp_sent": AXIS_HUMAN_IMPACT,
    "telegram_sent": AXIS_HUMAN_IMPACT,
    "sms_sent": AXIS_HUMAN_IMPACT,
    # PCSE decision signals
    "proposal_rejected_ceo": AXIS_AI_RELIABILITY,  # magnitude convention: -0.3
    # Computation events (deferred D5 portion that's safe for v1.0 — these
    # only fire on internal computation drafting, not on IRD submission)
    "computation_drafted": AXIS_HUMAN_IMPACT,
    "computation_completed": AXIS_ECONOMIC,
}

STANDARD_EVENTS = frozenset(EVENT_AXIS_MAP.keys())


# --------------------------------------------------------------------------- #
# Initial org seeding — council named 3 orgs with 5 roles each
# --------------------------------------------------------------------------- #

INITIAL_ORGS = [
    {
        "slug": "acquisition_studio",
        "name": "Acquisition Studio",
        "purpose": (
            "Generate AI-attributed qualified pipeline for Lanka.tax + FIESTA. "
            "Owns channel strategy, content production, outreach closing, CAC."
        ),
        "roles": [
            ("channel_strategist", "Channel Strategist",
             "Picks channels + budget allocation per campaign slot.", False),
            ("content_operator", "Content Operator",
             "Produces campaign assets (copy, creative, landing pages).", False),
            ("outreach_closer", "Outreach Closer",
             "Direct outbound to qualified leads; closes to paid.", False),
            ("cac_analyst", "CAC Analyst",
             "Tracks per-channel CAC vs target; flags inefficient spend.", False),
            ("red_team", "Embedded Red-Team",
             "Pre-publish review: hallucination + brand-safety + claim accuracy. "
             "Council #6: paid a cut per caught hallucination.", True),
        ],
    },
    {
        "slug": "delivery_ops_command",
        "name": "Delivery Ops Command",
        "purpose": (
            "Execute filings + service delivery within SLA; protect margin. "
            "Owns queue, workflow, SLA monitoring, quality."
        ),
        "roles": [
            ("workflow_orchestrator", "Workflow Orchestrator",
             "Routes work through the right pipeline + tooling.", False),
            ("queue_manager", "Queue Manager",
             "Maintains backlog priority + capacity allocation.", False),
            ("sla_monitor", "SLA Monitor",
             "Flags SLA breach risk before it happens; auto-escalates.", False),
            ("quality_reviewer", "Quality Reviewer",
             "Post-delivery QA + first-pass-completion-rate scoring.", False),
            ("red_team", "Embedded Red-Team",
             "Pre-delivery review: completeness + correctness + customer-fit. "
             "Council #6: paid a cut per caught defect.", True),
        ],
    },
    {
        "slug": "compliance_brigade",
        "name": "Compliance Brigade",
        "purpose": (
            "Prevent regulated stupidity in tax/remittance/KYC; veto authority. "
            "This org IS the global red-team backbone."
        ),
        "roles": [
            ("policy_interpreter", "Policy Interpreter",
             "Maps regulatory changes to operational rules.", False),
            ("filing_validator", "Filing Validator",
             "Pre-submission validation gate on filings.", False),
            ("audit_analyst", "Audit Analyst",
             "Post-submission audit; flags systemic risk patterns.", False),
            ("exception_handler", "Exception Handler",
             "Routes flagged items to human review.", False),
            ("red_team", "Embedded Red-Team",
             "Veto authority on any regulated workflow. Council #6: paid a cut "
             "per caught compliance hallucination.", True),
        ],
    },
]


def seed_initial_orgs() -> Dict[str, Any]:
    """Idempotent: ensures the 3 council-named orgs exist with their 5 roles
    each. Safe to call multiple times — uses get-or-create per slug.

    Returns a summary dict: {'orgs': [{'slug':..., 'created': bool, 'role_count': int}, ...]}
    """
    summary: Dict[str, Any] = {"orgs": []}
    try:
        for spec in INITIAL_ORGS:
            existing = AIOrg.query.filter_by(slug=spec["slug"]).first()
            created = False
            if existing is None:
                existing = AIOrg(
                    slug=spec["slug"],
                    name=spec["name"],
                    purpose=spec["purpose"],
                    status="active",
                )
                db.session.add(existing)
                db.session.flush()  # get the id without commit
                created = True

            role_count = 0
            for role_slug, role_name, description, is_red_team in spec["roles"]:
                role_exists = AIOrgRole.query.filter_by(
                    ai_org_id=existing.id,
                    role_slug=role_slug,
                ).first()
                if role_exists is None:
                    db.session.add(AIOrgRole(
                        ai_org_id=existing.id,
                        role_slug=role_slug,
                        role_name=role_name,
                        description=description,
                        is_red_team=is_red_team,
                    ))
                role_count += 1

            summary["orgs"].append({
                "slug": spec["slug"],
                "id": existing.id,
                "created": created,
                "role_count": role_count,
            })
        db.session.commit()
    except Exception as e:
        log.warning(f"seed_initial_orgs failed: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        raise
    return summary


# --------------------------------------------------------------------------- #
# emit_reputation_event — APPEND-ONLY entry point
# --------------------------------------------------------------------------- #

def emit_reputation_event(
    ai_org_id: int,
    event_type: str,
    magnitude: float,
    axis: Optional[str] = None,
    source_contract_id: Optional[int] = None,
    source_deliverable_id: Optional[int] = None,
    attribution_confidence: float = 1.0,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[ReputationEvent]:
    """Append a row to reputation_event. Best-effort write semantics
    (analytics, not transactional) — a transient DB failure is logged + returns
    None rather than raising.

    Raises ValueError on programmer error:
      * axis not in VALID_AXES (and not derivable from event_type)
      * unknown event_type AND axis not supplied
    """
    if axis is None:
        axis = EVENT_AXIS_MAP.get(event_type)
        if axis is None:
            raise ValueError(
                f"emit_reputation_event: event_type={event_type!r} not in "
                f"STANDARD_EVENTS and no axis supplied. Add to EVENT_AXIS_MAP "
                f"or pass axis explicitly."
            )
    if axis not in VALID_AXES:
        raise ValueError(
            f"emit_reputation_event: axis={axis!r} not in {VALID_AXES}"
        )

    if event_type not in STANDARD_EVENTS:
        log.info(
            f"emit_reputation_event: non-standard event_type={event_type!r}; "
            f"consider promoting to STANDARD_EVENTS."
        )

    try:
        ev = ReputationEvent(
            ai_org_id=ai_org_id,
            event_type=event_type,
            magnitude=magnitude,
            axis=axis,
            source_contract_id=source_contract_id,
            source_deliverable_id=source_deliverable_id,
            attribution_confidence=attribution_confidence,
            payload=payload,
        )
        db.session.add(ev)
        db.session.commit()
        return ev
    except Exception as e:
        log.warning(
            f"emit_reputation_event insert failed (org={ai_org_id}, "
            f"event_type={event_type}): {e}"
        )
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


# --------------------------------------------------------------------------- #
# record_payment — money flow + payee-side reputation event
# --------------------------------------------------------------------------- #

def record_payment(
    payer_kind: str,
    payer_ref_id: int,
    payee_kind: str,
    payee_ref_id: int,
    amount_lkr: float,
    reason: str,
    contract_id: Optional[int] = None,
    deliverable_id: Optional[int] = None,
    stripe_payment_intent_id: Optional[str] = None,
) -> Optional[PaymentEvent]:
    """Write a PaymentEvent. When payee is an ai_org, also emit a payee-side
    reputation event:
      * reason='contract_payment' or 'red_team_bounty' or 'renewal' →
        event_type='invoice_paid' (or 'renewal'), magnitude=amount_lkr,
        axis=economic.

    Payer side does NOT get a reputation event — money out is not a
    reputational signal in the council's model.
    """
    try:
        pe = PaymentEvent(
            payer_kind=payer_kind,
            payer_ref_id=payer_ref_id,
            payee_kind=payee_kind,
            payee_ref_id=payee_ref_id,
            amount_lkr=amount_lkr,
            reason=reason,
            contract_id=contract_id,
            deliverable_id=deliverable_id,
            stripe_payment_intent_id=stripe_payment_intent_id,
        )
        db.session.add(pe)
        db.session.commit()
    except Exception as e:
        log.warning(f"record_payment insert failed: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return None

    # Payee-side reputation event (only when payee is an ai_org).
    if payee_kind == "ai_org":
        # Map payment reason to event_type. Default to invoice_paid for
        # contract_payment; renewal reasons map straight through.
        if reason == "renewal":
            ev_type = "renewal"
        elif reason == "red_team_bounty":
            # Red-team bounty is an AI-reliability signal AND economic.
            # Council #6: emit BOTH so score engine credits both axes.
            emit_reputation_event(
                ai_org_id=payee_ref_id,
                event_type="redteam_pass",
                magnitude=1.0,
                source_contract_id=contract_id,
                source_deliverable_id=deliverable_id,
                payload={"bounty_lkr": float(amount_lkr), "reason": reason},
            )
            ev_type = "invoice_paid"
        elif reason == "refund":
            # Negative economic event — magnitude already encodes direction.
            ev_type = "invoice_paid"
            amount_lkr = -abs(amount_lkr)
        else:
            ev_type = "invoice_paid"

        emit_reputation_event(
            ai_org_id=payee_ref_id,
            event_type=ev_type,
            magnitude=float(amount_lkr),
            source_contract_id=contract_id,
            source_deliverable_id=deliverable_id,
            payload={"payment_event_id": pe.id, "reason": reason},
        )

    return pe


# --------------------------------------------------------------------------- #
# claim_attribution / verify_attribution
# --------------------------------------------------------------------------- #

def claim_attribution(
    external_event_type: str,
    external_event_ref_id: int,
    claimed_by_org_id: int,
    attribution_kind: str,
    confidence: float,
    evidence_payload: Optional[Dict[str, Any]] = None,
) -> Optional[AttributionLedger]:
    """Write an AttributionLedger row. UNIQUE constraint on
    (external_event_type, external_event_ref_id, claimed_by_org_id) prevents
    double-claim — if the row already exists, returns the existing row
    rather than creating a duplicate (effectively ON CONFLICT DO NOTHING).
    """
    existing = AttributionLedger.query.filter_by(
        external_event_type=external_event_type,
        external_event_ref_id=external_event_ref_id,
        claimed_by_org_id=claimed_by_org_id,
    ).first()
    if existing is not None:
        log.debug(
            f"claim_attribution: existing claim found "
            f"(id={existing.id}); no-op."
        )
        return existing

    try:
        row = AttributionLedger(
            external_event_type=external_event_type,
            external_event_ref_id=external_event_ref_id,
            claimed_by_org_id=claimed_by_org_id,
            attribution_kind=attribution_kind,
            confidence=confidence,
            evidence_payload=evidence_payload,
        )
        db.session.add(row)
        db.session.commit()
        return row
    except Exception as e:
        # IntegrityError from a race with another writer hitting the unique
        # constraint — re-fetch the winning row and return it.
        log.warning(f"claim_attribution insert collided: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return AttributionLedger.query.filter_by(
            external_event_type=external_event_type,
            external_event_ref_id=external_event_ref_id,
            claimed_by_org_id=claimed_by_org_id,
        ).first()


def verify_attribution(
    attribution_id: int,
    verifier_role_id: int,
) -> Optional[AttributionLedger]:
    """Mark an attribution as verified. Emits an `attribution_verified`
    reputation event on the claimed_by_org for AI reliability axis.
    """
    from datetime import datetime
    row = AttributionLedger.query.get(attribution_id)
    if row is None:
        log.warning(f"verify_attribution: id={attribution_id} not found")
        return None
    try:
        row.verified_at = datetime.utcnow()
        row.verifier_role_id = verifier_role_id
        db.session.commit()
    except Exception as e:
        log.warning(f"verify_attribution update failed: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return None

    emit_reputation_event(
        ai_org_id=row.claimed_by_org_id,
        event_type="attribution_verified",
        magnitude=float(row.confidence or 1.0),
        attribution_confidence=float(row.confidence or 1.0),
        payload={
            "attribution_id": row.id,
            "external_event_type": row.external_event_type,
            "external_event_ref_id": row.external_event_ref_id,
            "verifier_role_id": verifier_role_id,
        },
    )
    return row
