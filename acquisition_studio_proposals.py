"""
Acquisition Studio — proposal building helpers (Subagent D, 2026-05-18).

Pure functions split from acquisition_studio_org so tests can exercise them
without setting up a Flask app context.

Three responsibilities:

  1. build_proposal_for_trigger(trigger) — return artifact_payload dict for a
     given trigger kind. Templated by trigger_kind ∈ {traffic_drop, cac_spike,
     pipeline_shortfall}. New trigger kinds: extend TRIGGER_TEMPLATES dict.

  2. estimate_cac(payload, recent_events) — simple ratio from recent FIESTA
     events (payment_failed / checkout_completed). Returns Decimal in LKR.

  3. red_team_check(payload) — deterministic v1 rule-based check. Returns
     (passed: bool, rejection_reason: str | None). Two rules:
        (a) payload must have a non-empty `channels` list (≥1).
        (b) payload (serialised as JSON) must NOT contain forbidden phrases:
            "guaranteed return", "100% success".
     Phrase scan is case-insensitive. v1 deliberately rule-based — no LLM call;
     the heavier semantic check is a v2 build.

Council line 23 ("CAC ceiling LKR 3,000 per acquisition") and council line 27
("Red-Team is paid a bounty per caught hallucination") shape the constants below.
"""
from __future__ import annotations

import copy
import json
import logging
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Council ceiling — CAC analyst rejects any proposal whose forecast exceeds it.
CAC_CEILING_LKR: Decimal = Decimal("3000")

# Lead-value heuristic — what a typical paid Lanka.tax conversion is worth.
# Used as `quoted_price_lkr` for an internal-buyer (fiesta_internal) proposal.
ESTIMATED_LEAD_VALUE_LKR: Decimal = Decimal("9000")

# Red-team forbidden phrases — case-insensitive substring scan over the
# JSON-serialised payload. Keep the list small + uncontroversial in v1.
FORBIDDEN_PHRASES: Tuple[str, ...] = (
    "guaranteed return",
    "100% success",
)


# --------------------------------------------------------------------------- #
# Trigger -> proposal template
# --------------------------------------------------------------------------- #
#
# Each template returns the channel mix + rationale for a trigger kind. v1
# stays deterministic — no LLM-driven channel choices yet. The orchestrator
# layer (run_pass in acquisition_studio_org) decorates this with the trigger's
# evidence_payload before persisting.

TRIGGER_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "traffic_drop": {
        "rationale": (
            "Signups in trailing 24h are <50% of the trailing 7d average. "
            "Diversified channel push to rebuild top-of-funnel; reduces "
            "dependence on whatever channel softened."
        ),
        "channels": [
            {"channel": "paid_social", "budget_share": 0.40,
             "format": "static + 1 short video, SL/AU diaspora audience"},
            {"channel": "content_seo", "budget_share": 0.30,
             "format": "evergreen blog: 'How to remit to SL without losing 8% to fees'"},
            {"channel": "partner_referral", "budget_share": 0.30,
             "format": "outbound to 5 existing partner accountants for re-share"},
        ],
        "campaign_window_days": 7,
    },
    "cac_spike": {
        "rationale": (
            "payment_failed:checkout_completed ratio in 24h > 2× baseline. "
            "CAC is rising on paid channels. Shift to cheaper acquisition "
            "(referral + organic) while paid_social is paused for re-tune."
        ),
        "channels": [
            {"channel": "partner_referral", "budget_share": 0.60,
             "format": "5 partner-co-branded landing pages + commission bump"},
            {"channel": "content_seo", "budget_share": 0.40,
             "format": "long-form comparison: Lanka.tax vs other 4 SL tax services"},
            # Note: paid_social deliberately omitted while the spike resolves.
        ],
        "campaign_window_days": 7,
    },
    "pipeline_shortfall": {
        "rationale": (
            "Paid conversions in trailing 30d are below the 10-conversion "
            "minimum threshold. Mixed-channel push designed to fill the "
            "pipeline within a 7-day campaign window."
        ),
        "channels": [
            {"channel": "paid_social", "budget_share": 0.35,
             "format": "retargeting pixel pool from last 60d site visitors"},
            {"channel": "partner_referral", "budget_share": 0.35,
             "format": "warm intro push to all dormant partner accountants"},
            {"channel": "content_seo", "budget_share": 0.30,
             "format": "tax-season urgency content (deadline countdown CTAs)"},
        ],
        "campaign_window_days": 7,
    },
}


# --------------------------------------------------------------------------- #
# build_proposal_for_trigger
# --------------------------------------------------------------------------- #

def build_proposal_for_trigger(trigger: Dict[str, Any]) -> Dict[str, Any]:
    """Return the artifact_payload dict for a given trigger.

    trigger must have:
      trigger_kind:     one of TRIGGER_TEMPLATES keys
      evidence_payload: dict with the measurements that fired the trigger
    """
    kind = trigger.get("trigger_kind")
    template = TRIGGER_TEMPLATES.get(kind)
    if template is None:
        # Unknown trigger — return a minimal payload so the proposal still has
        # a `channels` list (otherwise red_team_check would reject for empty).
        # The caller (run_pass) is responsible for not pulling unknown triggers
        # from scan_for_triggers, so this is defensive only.
        log.warning(
            f"build_proposal_for_trigger: unknown trigger_kind={kind!r}; "
            f"returning defensive fallback payload."
        )
        return {
            "trigger_kind": kind,
            "rationale": "Unknown trigger kind; fallback proposal.",
            "channels": [
                {"channel": "partner_referral", "budget_share": 1.0,
                 "format": "manual outreach pending product-led signal"},
            ],
            "campaign_window_days": 7,
            "evidence": trigger.get("evidence_payload", {}),
            "version": "acquisition_studio_v1",
        }

    # Deep-copy `channels` — the template dicts are shared across callers, and
    # downstream consumers (CAC analyst, red-team review, deliverable artifact)
    # may mutate them. Without this, a mutation would persist into the next
    # build_proposal_for_trigger call.
    return {
        "trigger_kind": kind,
        "rationale": template["rationale"],
        "channels": copy.deepcopy(template["channels"]),
        "campaign_window_days": template["campaign_window_days"],
        "evidence": trigger.get("evidence_payload", {}),
        "version": "acquisition_studio_v1",
    }


# --------------------------------------------------------------------------- #
# estimate_cac
# --------------------------------------------------------------------------- #

def estimate_cac(
    payload: Dict[str, Any],
    recent_events: Iterable[Any],
) -> Decimal:
    """Estimate per-acquisition CAC in LKR from recent FIESTA event data.

    Simple model (v1): if we can compute it, CAC = total_paid_failed_attempts /
    paid_conversions × payload baseline_cpm. If no signal, return a default
    (LKR 1,500 — half the ceiling, so unknown triggers don't auto-reject).

    Inputs:
      payload         the proposal artifact_payload (read for the channel mix)
      recent_events   iterable of Event-like objects with .event_type +
                      .payload (dict-or-None); typically the last 24-48h of
                      payment_failed + checkout_completed.

    The point of v1 isn't precision — it's having a number on the row so
    Subagent E (Delivery Ops) and the scoring engine can see CAC math is
    happening. v2 will replace with per-channel attribution-weighted CAC.
    """
    failed = 0
    completed = 0
    failed_amount = Decimal("0")
    completed_amount = Decimal("0")
    for ev in recent_events:
        et = getattr(ev, "event_type", None)
        ep = getattr(ev, "payload", None) or {}
        try:
            amt = Decimal(str(ep.get("amount") or 0))
        except Exception:
            amt = Decimal("0")
        if et == "payment_failed":
            failed += 1
            failed_amount += amt
        elif et == "checkout_completed":
            completed += 1
            completed_amount += amt

    # No-data fallback — neutral CAC well under the ceiling.
    if completed == 0 and failed == 0:
        return Decimal("1500")

    # If no successful conversions, every failed attempt is pure cost — push
    # CAC above ceiling so the proposal rejects.
    if completed == 0:
        return CAC_CEILING_LKR + Decimal("1")

    # Naive: failure-driven cost-per-acquisition. Each failed checkout is
    # treated as ~LKR 500 wasted touch (conservative; real number wants
    # per-channel ad-spend integration once the cost ingestor exists).
    waste_per_failure = Decimal("500")
    waste = Decimal(failed) * waste_per_failure
    cac = waste / Decimal(completed) if completed > 0 else CAC_CEILING_LKR + Decimal("1")

    # Floor at LKR 500 (avoid laughably low CAC when failed==0).
    if cac < Decimal("500"):
        cac = Decimal("500")
    return cac.quantize(Decimal("1"))


# --------------------------------------------------------------------------- #
# red_team_check
# --------------------------------------------------------------------------- #

def red_team_check(payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Deterministic v1 red-team review.

    Returns (passed, rejection_reason):
      passed=True, rejection_reason=None  → no issues
      passed=False, rejection_reason=str  → reject

    Rules (in order — first failure short-circuits):
      R1: payload['channels'] must exist + be a non-empty list.
      R2: JSON-serialised payload (case-insensitive) must NOT contain any
          phrase in FORBIDDEN_PHRASES.

    v1 is rule-based on purpose — see council line 39 ("Red-Team starts as
    a deterministic gate; promote to LLM review only after we have 50
    confirmed/rejected red-team decisions for grounding").
    """
    # R1: channels list present + non-empty
    channels = payload.get("channels")
    if not isinstance(channels, list) or len(channels) < 1:
        return False, "red_team_R1: payload.channels missing or empty"

    # R2: forbidden phrase scan over the JSON dump
    try:
        haystack = json.dumps(payload, default=str).lower()
    except Exception as e:
        # If we can't serialise the payload, that's also a failure mode.
        return False, f"red_team_R2: payload not JSON-serialisable ({e})"

    for phrase in FORBIDDEN_PHRASES:
        if phrase in haystack:
            return False, f"red_team_R2: forbidden phrase detected ({phrase!r})"

    return True, None


__all__ = [
    "CAC_CEILING_LKR",
    "ESTIMATED_LEAD_VALUE_LKR",
    "FORBIDDEN_PHRASES",
    "TRIGGER_TEMPLATES",
    "build_proposal_for_trigger",
    "estimate_cac",
    "red_team_check",
]
