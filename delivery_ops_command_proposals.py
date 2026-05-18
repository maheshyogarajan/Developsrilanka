"""
Delivery Ops Command — proposal building helpers (Subagent E, 2026-05-18).

Pure functions split from delivery_ops_command_org so tests can exercise them
without setting up a Flask app context. Mirror Subagent D's helper-split pattern.

Five responsibilities:

  1. quote_for_kind(job_kind) - LKR quote per recognised job kind.
  2. compute_sla_target(job_kind) - SLA hours per job kind.
  3. baseline_human_cost(job_kind) - LKR cost a human worker would consume on
     the same job; used to compute cost_saved_verified magnitude.
  4. quality_check(payload) - completeness gate. Required keys present?
  5. red_team_check(payload) - forbidden-flags gate (hallucinated_field,
     claim_inconsistent).
  6. simulate_cycle_time(payload, sla_target_h) - DETERMINISTIC simulator so
     v1 tests don't depend on real workflow infrastructure. Roughly 70/30 split
     under/over SLA, keyed off a stable hash of the payload signature.

Council line ("Delivery Ops sells completed jobs inside the economy; its
leading metric is cycle time + first-pass completion rate") shapes the
defaults below.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants — quotes, SLA targets, baseline human costs per job kind.
# --------------------------------------------------------------------------- #
#
# These tables are the org's economic priors. They're tiny + readable so the
# v1 dashboard can show CEO exactly what numbers shape the cost_saved math.
# When real cost-accounting + workflow timers ship in v2, these get replaced
# with measured values per job kind.

# Quote the buyer (FIESTA internal) pays the org per completed job.
_QUOTE_TABLE: Dict[str, Decimal] = {
    "filing_submitted": Decimal("2500"),
    "remittance_ird_ready": Decimal("1500"),
    "bank_statement_uploaded": Decimal("800"),
}
_QUOTE_DEFAULT: Decimal = Decimal("1000")

# SLA target in hours per recognised job kind.
_SLA_TABLE: Dict[str, int] = {
    "filing_submitted": 48,
    "remittance_ird_ready": 24,
    "bank_statement_uploaded": 12,
}
_SLA_DEFAULT: int = 48

# Baseline cost (LKR) a human worker would consume on the same job. Drives
# cost_saved_verified magnitude. Must exceed the AI quote for the math to be
# positive (else cost_saved is not emitted).
_BASELINE_HUMAN_COST_TABLE: Dict[str, Decimal] = {
    "filing_submitted": Decimal("4000"),
    "remittance_ird_ready": Decimal("3000"),
    "bank_statement_uploaded": Decimal("1500"),
}
_BASELINE_HUMAN_COST_DEFAULT: Decimal = Decimal("2000")

# Required payload keys for the Quality Reviewer gate. Missing any → STATUS_FAILED_QC.
REQUIRED_PAYLOAD_KEYS: Tuple[str, ...] = (
    "external_event_id",
    "job_kind",
    "assigned_workflow",
)

# Forbidden flags the Red-Team scans for in the artifact payload. Either set
# True → STATUS_FAILED_QC + hallucination_flag event.
RED_TEAM_FORBIDDEN_FLAGS: Tuple[str, ...] = (
    "hallucinated_field",
    "claim_inconsistent",
)


# --------------------------------------------------------------------------- #
# quote_for_kind
# --------------------------------------------------------------------------- #

def quote_for_kind(job_kind: str) -> Decimal:
    """Return the LKR quote the org charges for a completed job of this kind.

    Unknown job kinds fall back to _QUOTE_DEFAULT — the runner still records a
    contract so the audit trail captures the work, but the price is a
    conservative placeholder.
    """
    return _QUOTE_TABLE.get(job_kind, _QUOTE_DEFAULT)


# --------------------------------------------------------------------------- #
# compute_sla_target
# --------------------------------------------------------------------------- #

def compute_sla_target(job_kind: str) -> int:
    """Return the SLA target in hours for a given job kind.

    Heuristics:
      filing_submitted        48h
      remittance_ird_ready    24h
      bank_statement_uploaded 12h
      default                 48h
    """
    return _SLA_TABLE.get(job_kind, _SLA_DEFAULT)


# --------------------------------------------------------------------------- #
# baseline_human_cost
# --------------------------------------------------------------------------- #

def baseline_human_cost(job_kind: str) -> Decimal:
    """Return the LKR cost a human worker would consume on the same job.

    Used by the runner to compute `cost_saved_verified` magnitude:
        magnitude = baseline_human_cost(kind) - quote_for_kind(kind)
    If magnitude <= 0 the runner does NOT emit the event (the AI org didn't
    actually save money on this job).
    """
    return _BASELINE_HUMAN_COST_TABLE.get(job_kind, _BASELINE_HUMAN_COST_DEFAULT)


# --------------------------------------------------------------------------- #
# quality_check
# --------------------------------------------------------------------------- #

def quality_check(payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Quality Reviewer gate.

    Returns (passed, missing_key_or_None):
      passed=True,  missing_key=None  → all required keys present + non-empty
      passed=False, missing_key=str   → first missing key wins (deterministic order)

    "Present + non-empty" means the key is in the dict and its value is not
    None and not the empty string. Zero / False / empty list are still valid
    (a workflow with no remaining steps is a valid state).
    """
    for key in REQUIRED_PAYLOAD_KEYS:
        if key not in payload:
            return False, key
        val = payload[key]
        if val is None or (isinstance(val, str) and val == ""):
            return False, key
    return True, None


# --------------------------------------------------------------------------- #
# red_team_check
# --------------------------------------------------------------------------- #

def red_team_check(payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Red-Team gate. Deterministic v1 rule: payload must not have any
    forbidden flag set to True.

    Returns (passed, rejection_reason):
      passed=True,  reason=None  → no forbidden flags asserted
      passed=False, reason=str   → first forbidden flag that's True (deterministic)

    v1 is rule-based on purpose — same precedent as Subagent D's red_team_check.
    Promote to LLM review when enough flagged/cleared events accumulate to
    ground a model.
    """
    for flag in RED_TEAM_FORBIDDEN_FLAGS:
        if payload.get(flag) is True:
            return False, f"red_team: forbidden flag {flag!r}=True in payload"
    return True, None


# --------------------------------------------------------------------------- #
# simulate_cycle_time
# --------------------------------------------------------------------------- #

def simulate_cycle_time(payload: Dict[str, Any], sla_target_h: int) -> Decimal:
    """Deterministic v1 cycle-time simulator.

    Real workflow timers ship in v2 — until then we need a stable, hash-based
    number so tests can force on-time vs SLA-breach outcomes by tweaking the
    payload. The formula:

      1. Compute SHA256 of the payload (JSON-sorted, default=str), interpret
         the first 8 hex chars as an int.
      2. Modulo 100 → percentile in [0, 99].
      3. If percentile < 70 (≈70% of jobs):
            cycle_time = sla_target_h * (percentile / 100.0)   -- under SLA
         Else (≈30% of jobs):
            cycle_time = sla_target_h * (1.0 + (percentile - 70) / 30.0 * 0.5)
            -- over SLA, by 0..50% breach
      4. Round to 2dp Decimal.

    The 70/30 split is the council's target first-pass rate; v2 will tune
    against measured data.

    Test hook: callers can force breach by setting payload['_force_breach']=True
    or force on-time by setting payload['_force_on_time']=True. These keys are
    inspected first and short-circuit the hash math.
    """
    # Test-only forcing hooks — short-circuit the simulation.
    if payload.get("_force_breach") is True:
        # 25% over SLA.
        return (Decimal(sla_target_h) * Decimal("1.25")).quantize(Decimal("0.01"))
    if payload.get("_force_on_time") is True:
        # Half of SLA target.
        return (Decimal(sla_target_h) * Decimal("0.5")).quantize(Decimal("0.01"))

    try:
        canon = json.dumps(payload, sort_keys=True, default=str)
    except Exception:
        canon = repr(payload)
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 100  # 0..99

    if bucket < 70:
        # Under SLA: 0..70% of target.
        ratio = bucket / 100.0
        cycle_h = sla_target_h * ratio
        if cycle_h < 0.1:
            cycle_h = 0.1  # floor — even instant work takes some measurable time
    else:
        # Over SLA: 1.0x..1.5x target.
        overshoot = (bucket - 70) / 30.0  # 0..1
        cycle_h = sla_target_h * (1.0 + overshoot * 0.5)

    return Decimal(str(cycle_h)).quantize(Decimal("0.01"))


# --------------------------------------------------------------------------- #
# build_workflow_assignment - returns a deep-copied template per call.
# --------------------------------------------------------------------------- #
#
# Subagent D's report flagged shared template mutation — handing the same dict
# reference out to multiple callers lets test N mutate it, leaking into test
# N+1. We deepcopy at the boundary.

_WORKFLOW_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "filing_submitted": {
        "assigned_workflow": "filing_pipeline_v1",
        "stages": ["validate", "compute", "submit_ird", "confirm"],
        "owner_role_slug": "workflow_orchestrator",
    },
    "remittance_ird_ready": {
        "assigned_workflow": "remittance_pipeline_v1",
        "stages": ["fetch_ird_doc", "verify", "register_with_cbsl"],
        "owner_role_slug": "workflow_orchestrator",
    },
    "bank_statement_uploaded": {
        "assigned_workflow": "bank_statement_pipeline_v1",
        "stages": ["parse", "categorise", "reconcile"],
        "owner_role_slug": "workflow_orchestrator",
    },
}

_WORKFLOW_DEFAULT: Dict[str, Any] = {
    "assigned_workflow": "generic_pipeline_v1",
    "stages": ["triage", "execute", "verify"],
    "owner_role_slug": "workflow_orchestrator",
}


def build_workflow_assignment(job_kind: str) -> Dict[str, Any]:
    """Return a fresh deep copy of the workflow assignment template for the
    given job kind. Always deep-copied to prevent shared-state mutation across
    callers (D's report pitfall #2).
    """
    template = _WORKFLOW_TEMPLATES.get(job_kind, _WORKFLOW_DEFAULT)
    return copy.deepcopy(template)


__all__ = [
    "REQUIRED_PAYLOAD_KEYS",
    "RED_TEAM_FORBIDDEN_FLAGS",
    "quote_for_kind",
    "compute_sla_target",
    "baseline_human_cost",
    "quality_check",
    "red_team_check",
    "simulate_cycle_time",
    "build_workflow_assignment",
]
