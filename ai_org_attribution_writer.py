"""
AI-Org Attribution Writer — Subagent B (2026-05-18).

For every FIESTA `events` row with revenue or impact, determine which AI org(s)
influenced it and write:
  * AttributionLedger row (via ai_org_substrate.claim_attribution — dedup-safe)
  * ReputationEvent row (via ai_org_substrate.emit_reputation_event)

Council synthesis: G:/My Drive/CEO OS/working files/_cockpit_fiesta/VISIONARY_ECONOMY_COUNCIL_SYNTHESIS.md

Design intent:
  * READ-ONLY against `events` — never updates or deletes Event rows.
  * Rules-driven (ATTRIBUTION_RULES list) so new attribution patterns are
    added by appending dicts, not by editing dispatch logic. Same pattern as
    cross_sell_rules in lankatax_models / ai_crm.
  * Dedup-safe — claim_attribution() relies on Subagent A's UNIQUE constraint
    (external_event_type, external_event_ref_id, claimed_by_org_id). Running
    process_event twice on the same Event row is a no-op.
  * Celery beat task `process_recent` is the orchestrator entry point. The
    orchestrator wires the beat schedule (see register_celery_beat()).
  * Org-slug resolution is cached at module level (orgs are stable).

Subagent C (Score Engine) reads the reputation_event rows this writer creates.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Callable

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Org-slug cache — orgs are stable; cache id lookups.
# --------------------------------------------------------------------------- #

_ORG_ID_CACHE: Dict[str, int] = {}


def _resolve_org_id(slug: str) -> Optional[int]:
    """Resolve an org slug to its id. Cached. Returns None if org not seeded
    (caller should log + skip — never raise during attribution).
    """
    if slug in _ORG_ID_CACHE:
        return _ORG_ID_CACHE[slug]
    try:
        from ai_org_models import AIOrg
        org = AIOrg.query.filter_by(slug=slug).first()
        if org is None:
            log.warning(
                f"_resolve_org_id: org slug={slug!r} not found. "
                f"Has seed_initial_orgs() been called?"
            )
            return None
        _ORG_ID_CACHE[slug] = org.id
        return org.id
    except Exception as e:
        log.warning(f"_resolve_org_id({slug!r}) failed: {e}")
        return None


# --------------------------------------------------------------------------- #
# Claimant resolvers — return an org_id given an event, or None to skip.
# --------------------------------------------------------------------------- #

def _claim_acquisition_studio(event) -> Optional[int]:
    return _resolve_org_id("acquisition_studio")


def _claim_delivery_ops(event) -> Optional[int]:
    return _resolve_org_id("delivery_ops_command")


def _claim_compliance_brigade(event) -> Optional[int]:
    return _resolve_org_id("compliance_brigade")


def _claim_signup_if_lankatax_utm(event) -> Optional[int]:
    """signup event → acquisition_studio iff payload.utm_source == 'lankatax'.
    Returns None otherwise (skip — not AI-org-attributable)."""
    payload = event.payload or {}
    if payload.get("utm_source") == "lankatax":
        return _resolve_org_id("acquisition_studio")
    return None


def _claim_checkout_from_outreach(event) -> Optional[int]:
    """checkout_completed → owner of the most-recent LankataxOutreach for this
    user. Falls back to acquisition_studio if no outreach exists.

    LankataxOutreach rows don't carry an `owner_org_id` field (it's a Wave-2
    cross-sell artifact predating AI-orgs), so for now we attribute ALL
    outreach-conversions to acquisition_studio. The hook is in place for when
    LankataxOutreach gets an owner_org column.
    """
    if event.user_id is None:
        return _resolve_org_id("acquisition_studio")
    try:
        from lankatax_models import LankataxOutreach
        outreach = (
            LankataxOutreach.query
            .filter_by(user_id=event.user_id)
            .order_by(LankataxOutreach.sent_at.desc())
            .first()
        )
        if outreach is not None:
            # Future: if outreach.owner_org_slug exists, route there.
            return _resolve_org_id("acquisition_studio")
    except Exception as e:
        log.debug(f"_claim_checkout_from_outreach lookup failed: {e}")
    return _resolve_org_id("acquisition_studio")


def _claim_support_resolved_by_kind(event) -> Optional[int]:
    """support_resolved → compliance_brigade if escalation_reason mentions
    audit/complaint/IRD-notice; else delivery_ops_command.
    """
    payload = event.payload or {}
    reason = (payload.get("escalation_reason") or "").lower()
    if any(k in reason for k in ("audit", "complaint", "ird notice", "ird_notice")):
        return _resolve_org_id("compliance_brigade")
    return _resolve_org_id("delivery_ops_command")


def _claim_payment_failed(event) -> Optional[int]:
    """payment_failed → delivery_ops_command (reliability ding).
    Future: route to the org that owned the most-recent successful proposal
    for this user, when proposal→user linkage exists.
    """
    return _resolve_org_id("delivery_ops_command")


# --------------------------------------------------------------------------- #
# Magnitude extractors — pull the signed numeric magnitude from an event.
# --------------------------------------------------------------------------- #

def _mag_amount_from_payload(event) -> float:
    """payload.amount (Stripe-paid LKR). Falls back to 0.0 if absent."""
    payload = event.payload or {}
    try:
        return float(payload.get("amount") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _mag_one(event) -> float:
    return 1.0


def _mag_negative_one(event) -> float:
    return -1.0


# --------------------------------------------------------------------------- #
# ATTRIBUTION_RULES — the canonical event→attribution mapping table.
# --------------------------------------------------------------------------- #
#
# Each rule has:
#   trigger_event_type      : matches Event.event_type exactly
#   claimant_org_resolver   : Callable(event) -> Optional[org_id]; None skips
#   attribution_kind        : 'direct' | 'last-touch' | 'contributed' | 'multi-touch'
#   base_confidence         : 0.0-1.0; flows into AttributionLedger.confidence
#                             AND reputation_event.attribution_confidence
#   reputation_event_type   : event_type emitted on reputation_event
#   axis                    : 'economic' | 'human_impact' | 'ai_reliability'
#   magnitude_extractor     : Callable(event) -> float (signed magnitude)
#
# Order matters for rule iteration but not for dedup (UNIQUE constraint).

ATTRIBUTION_RULES: List[Dict[str, Any]] = [
    {
        "trigger_event_type": "signup",
        "claimant_org_resolver": _claim_signup_if_lankatax_utm,
        "attribution_kind": "last-touch",
        "base_confidence": 0.9,
        "reputation_event_type": "human_acceptance",
        "axis": "human_impact",
        "magnitude_extractor": _mag_one,
    },
    {
        "trigger_event_type": "checkout_completed",
        "claimant_org_resolver": _claim_checkout_from_outreach,
        "attribution_kind": "direct",
        "base_confidence": 0.95,
        "reputation_event_type": "invoice_paid",
        "axis": "economic",
        "magnitude_extractor": _mag_amount_from_payload,
    },
    {
        "trigger_event_type": "remittance_ird_ready",
        "claimant_org_resolver": _claim_delivery_ops,
        "attribution_kind": "direct",
        "base_confidence": 0.9,
        "reputation_event_type": "deliverable_accepted",
        "axis": "ai_reliability",
        "magnitude_extractor": _mag_one,
    },
    {
        "trigger_event_type": "support_resolved",
        "claimant_org_resolver": _claim_support_resolved_by_kind,
        "attribution_kind": "direct",
        "base_confidence": 0.85,
        "reputation_event_type": "escalation_resolved",
        "axis": "human_impact",
        "magnitude_extractor": _mag_one,
    },
    {
        "trigger_event_type": "bank_statement_uploaded",
        "claimant_org_resolver": _claim_delivery_ops,
        "attribution_kind": "direct",
        "base_confidence": 0.95,
        "reputation_event_type": "deliverable_accepted",
        "axis": "ai_reliability",
        "magnitude_extractor": _mag_one,
    },
    {
        "trigger_event_type": "payment_failed",
        "claimant_org_resolver": _claim_payment_failed,
        "attribution_kind": "direct",
        "base_confidence": 0.8,
        "reputation_event_type": "rollback",
        "axis": "ai_reliability",
        "magnitude_extractor": _mag_negative_one,
    },
]


# --------------------------------------------------------------------------- #
# Core: process_event — one event → 0..N attribution rows.
# --------------------------------------------------------------------------- #

def process_event(event) -> List[int]:
    """Run every matching attribution rule for an Event. Each rule that
    produces a claimant writes ONE AttributionLedger row + ONE reputation_event.

    Returns the list of attribution_ids created (or re-fetched on dedup hit).
    Empty list if no rule matched OR all matched rules returned None claimant.
    """
    from ai_org_substrate import claim_attribution, emit_reputation_event

    created: List[int] = []
    for rule in ATTRIBUTION_RULES:
        if rule["trigger_event_type"] != event.event_type:
            continue
        try:
            org_id = rule["claimant_org_resolver"](event)
        except Exception as e:
            log.warning(
                f"process_event: resolver raised for event {event.id} "
                f"rule {rule['trigger_event_type']}: {e}"
            )
            continue
        if org_id is None:
            continue

        try:
            magnitude = float(rule["magnitude_extractor"](event))
        except Exception as e:
            log.warning(
                f"process_event: magnitude_extractor raised for event "
                f"{event.id}: {e}"
            )
            magnitude = 0.0

        evidence = {
            "event_id": event.id,
            "event_type": event.event_type,
            "user_id": event.user_id,
            "source": event.source,
            "rule_trigger": rule["trigger_event_type"],
            "rule_reputation_event_type": rule["reputation_event_type"],
            "magnitude": magnitude,
        }

        attribution = claim_attribution(
            external_event_type=event.event_type,
            external_event_ref_id=event.id,
            claimed_by_org_id=org_id,
            attribution_kind=rule["attribution_kind"],
            confidence=rule["base_confidence"],
            evidence_payload=evidence,
        )
        if attribution is None:
            log.warning(
                f"process_event: claim_attribution returned None for "
                f"event={event.id} org={org_id}"
            )
            continue

        # Emit the reputation event regardless of whether the attribution row
        # was just created or already existed — but only ONCE per (event, org).
        # The append-only reputation_event ledger has no unique key on
        # (source_event_id, org), so we guard with a payload-matching query.
        try:
            from ai_org_models import ReputationEvent
            from sqlalchemy import cast, String
            already = (
                ReputationEvent.query
                .filter(ReputationEvent.ai_org_id == org_id)
                .filter(ReputationEvent.event_type == rule["reputation_event_type"])
                .filter(cast(ReputationEvent.payload, String).like(
                    f'%"event_id": {event.id}%'
                ))
                .first()
            )
        except Exception:
            already = None

        if already is None:
            emit_reputation_event(
                ai_org_id=org_id,
                event_type=rule["reputation_event_type"],
                magnitude=magnitude,
                axis=rule["axis"],
                attribution_confidence=rule["base_confidence"],
                payload={
                    "event_id": event.id,
                    "event_type": event.event_type,
                    "attribution_id": attribution.id,
                    "user_id": event.user_id,
                },
            )

        created.append(attribution.id)

    return created


# --------------------------------------------------------------------------- #
# Celery beat task — process recent events.
# --------------------------------------------------------------------------- #

def process_recent_events(since_minutes: int = 15) -> Dict[str, int]:
    """Scan Event rows created in the last `since_minutes` and run process_event
    on each. Idempotent — duplicate runs deduplicate via the UNIQUE constraint
    on AttributionLedger.

    Returns counts: {events_seen, events_matched, attributions_created, errors}.
    """
    counts = {
        "events_seen": 0,
        "events_matched": 0,
        "attributions_created": 0,
        "errors": 0,
    }
    triggers = {r["trigger_event_type"] for r in ATTRIBUTION_RULES}
    cutoff = datetime.utcnow() - timedelta(minutes=since_minutes)

    try:
        from event_models import Event
        rows = (
            Event.query
            .filter(Event.created_at >= cutoff)
            .filter(Event.event_type.in_(list(triggers)))
            .order_by(Event.id.asc())
            .all()
        )
    except Exception as e:
        log.warning(f"process_recent_events query failed: {e}")
        return counts

    counts["events_seen"] = len(rows)
    for row in rows:
        try:
            ids = process_event(row)
            if ids:
                counts["events_matched"] += 1
                counts["attributions_created"] += len(ids)
        except Exception as e:
            log.warning(f"process_recent_events: event {row.id} failed: {e}")
            counts["errors"] += 1

    return counts


# --------------------------------------------------------------------------- #
# Celery wiring — orchestrator integration.
# --------------------------------------------------------------------------- #

try:
    from celery_config import celery_app

    @celery_app.task(name="ai_org_attribution_writer.process_recent")
    def process_recent_task(since_minutes: int = 15) -> Dict[str, int]:
        """Celery task wrapper. Orchestrator schedules this via beat —
        see register_celery_beat() for the schedule entry.
        """
        return process_recent_events(since_minutes=since_minutes)
except Exception:
    # Celery not importable in test/CLI contexts — process_recent_events still
    # callable directly.
    process_recent_task = None  # type: ignore


def register_celery_beat() -> Dict[str, Dict[str, Any]]:
    """Returns the beat-schedule entry. Orchestrator merges this into
    celery_config.celery_app.conf.beat_schedule at integration time.

    Schedule: every 5 minutes (captures freshly emitted events within the
    15-min `since_minutes` window with 10-min safety overlap).
    """
    try:
        from celery.schedules import crontab
        schedule = crontab(minute="*/5")
    except Exception:
        # crontab unavailable — fall back to plain interval.
        schedule = 300  # 5 minutes in seconds
    return {
        "ai_org_attribution-every-5min": {
            "task": "ai_org_attribution_writer.process_recent",
            "schedule": schedule,
            "kwargs": {"since_minutes": 15},
        },
    }


__all__ = [
    "ATTRIBUTION_RULES",
    "process_event",
    "process_recent_events",
    "register_celery_beat",
]
