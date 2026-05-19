"""fiesta.delivery_ops.automation_runner — SL adapter for IRD automations.

This is the architecturally-correct v1 of the automation_runner, replacing
the v0.1 Python port at G:/My Drive/CEO OS/working files/automation_runner/
which classified PIN states locally. v1 follows the council #2 synthesis
(2026-05-19 §3) + PCSE Strategist D §3.1 / §9: insert a Processing_task__c
into the Lanka.tax SF org with the exact payload contract; the existing
DataSciLT/IRD-System-hosting Lambda + Dimuth Docker poller pick it up and
execute the right Playwright automation.

This adapter does NOT:
  - run Playwright itself,
  - poll for completion (caller's job, see status_handle in return value),
  - call the AWS API Gateway endpoint (no token, no IAM role — per
    reference_run_automation_screenflow.md the supported path is PT insert),
  - port any of the 6 individual IRD_*_AUTOMATION repos.

What it DOES:
  1. Validate inputs (customer_id, automation_type allowlist).
  2. Optional Phase Gate Y guard (live mode only).
  3. Resolve Customer__c row → Contact__c + Relationship_Manager.
  4. Per Resolver Rule P1: create Resolver_Change__c BEFORE the PT insert.
  5. Insert Processing_task__c with the dispatcher payload contract.
  6. Return a polling handle {processing_task_id, dispatched_to,
     expected_completion_minutes}.

automation_type → Processing_task_type__c (PROVED via the 6 ALF flows in
working files/knowledge/flow_writer_map.json + IRD-System-hosting/lambda_function.py):

  PIN_REQUEST         → "PIN Creation"                     (AWS Lambda, ~3-5 min)
  TEMP_PIN_RESET      → "Permanent PIN Activation"         (Docker, ~1 min)
  LOGIN_CHECK         → "IRD Credential Verification"      (Docker, ~1-1.5 min)
  TAX_YEARS_CHECK     → "Tax Filing Requirement Validation" (Docker, ~1-2 min)
  PAYMENT_INFO        → "Tax Payment Validation"           (AWS Lambda, ~9 min)
  DIN_COLLECTION      → "DIN Collection"                   (Docker, listener may be paused)

API:
    invoke_sl_automation(*, customer_id, automation_type, dry_run=True,
                         trace_id=None, sf_client=None,
                         phase_gate_reader=None) -> dict
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Constants — payload contract source: PCSE Strategist D §3.1 +
# memory/reference_run_automation_screenflow.md +
# IRD-System-hosting/lambda_function.py (normalize_pin_type confirms PIN
# Creation routes to PinRequest container; the SF PT type strings are
# emitted by the 6 ALF flows per flow_writer_map.json).
# ---------------------------------------------------------------------------

# automation_type code → SF Processing_task_type__c picklist value.
# These string values are the listener's dispatch key. Do NOT change without
# diffing against IRD-System-hosting/automations.py and the 6 ALF flows.
AUTOMATION_TYPE_MAP = {
    "PIN_REQUEST":     "PIN Creation",
    "TEMP_PIN_RESET":  "Permanent PIN Activation",
    "LOGIN_CHECK":     "IRD Credential Verification",
    "TAX_YEARS_CHECK": "Tax Filing Requirement Validation",
    "PAYMENT_INFO":    "Tax Payment Validation",
    "DIN_COLLECTION":  "DIN Collection",
}

# Routing target — informational, drives expected_completion_minutes hint.
# Source: IRD-System-hosting/lambda_function.py routes ONLY PinRequest +
# Paymentinfo* + ReturnFiling_* to ECS; the other 3 fall through to Dimuth's
# Docker poller per the README.
AUTOMATION_ROUTING = {
    "PIN_REQUEST":     {"dispatched_to": "aws_lambda", "expected_minutes": 5},
    "PAYMENT_INFO":    {"dispatched_to": "aws_lambda", "expected_minutes": 9},
    "TEMP_PIN_RESET":  {"dispatched_to": "docker_poller", "expected_minutes": 2},
    "LOGIN_CHECK":     {"dispatched_to": "docker_poller", "expected_minutes": 2},
    "TAX_YEARS_CHECK": {"dispatched_to": "docker_poller", "expected_minutes": 2},
    "DIN_COLLECTION":  {"dispatched_to": "docker_poller_unverified",
                        "expected_minutes": None},
}

# System bot Tax_System_Employee Id — tells the listener "AI-originated, run
# automation now" (vs staff-originated, which routes differently).
# Source: memory/reference_run_automation_screenflow.md + reference_tax_system_employee_ids.md
SYSTEM_BOT_TSE_ID = "a17OX00003Cc3mQYAR"

# SF customer Id basic shape gate (15 or 18 char, alphanumeric). Not a full
# checksum — the SF query will reject malformed ids regardless.
_SF_ID_RE = re.compile(r"^[a-zA-Z0-9]{15}(?:[a-zA-Z0-9]{3})?$")

# Phase Gate Y file (shared canonical location with CEO-OS resolver).
# In FIESTA-only deployments without the CEO-OS filesystem mount, callers
# should inject a custom phase_gate_reader. The default tries the canonical
# path, then falls back to env SUPABASE-style flag.
_PHASE_GATE_FILE_CANONICAL = pathlib.Path(
    "G:/My Drive/CEO OS/working files/_audit/phase_gate_y_state.json"
)
_PHASE_GATE_VALID_STAMPERS_PREFIX = ("CEO via Telegram",)
_PHASE_GATE_MAX_AGE_DAYS = 7


class AutomationRunnerError(Exception):
    """Raised on programmer errors (bad automation_type, bad inputs)."""


# ---------------------------------------------------------------------------
# Default SF client — wraps fiesta.integrations.sf_auth.SFRestClient.
# Pulled into a thin shim so tests can inject a fake without importing the
# whole sf_auth chain.
# ---------------------------------------------------------------------------

def _default_sf_client():
    from fiesta.integrations.sf_auth import SFRestClient
    return SFRestClient()


def _default_phase_gate_reader() -> tuple:
    """Read Phase Gate Y state from canonical file.

    Returns (active: bool, reason: str). Mirrors the v0.1 runner contract +
    the send_adapter._phase_gate_y_active() shape.
    """
    if not _PHASE_GATE_FILE_CANONICAL.exists():
        return False, (
            f"Phase Gate Y not stamped (no {_PHASE_GATE_FILE_CANONICAL.name} "
            "at canonical path; inject a custom phase_gate_reader if running "
            "outside CEO-OS filesystem)"
        )
    try:
        state = json.loads(_PHASE_GATE_FILE_CANONICAL.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"Phase Gate Y state file unreadable: {e}"
    if not state.get("active"):
        return False, "Phase Gate Y stamp present but active=false"
    stamped_by = state.get("stamped_by", "")
    if not any(stamped_by.startswith(p) for p in _PHASE_GATE_VALID_STAMPERS_PREFIX):
        return False, (
            f"Phase Gate Y REJECTED: stamped_by={stamped_by!r} not in allowlist. "
            "Only CEO Telegram stamps are honored (B-004)."
        )
    stamped_at = state.get("stamped_at")
    if not stamped_at:
        return False, "Phase Gate Y stamp missing stamped_at"
    try:
        ts = datetime.fromisoformat(stamped_at.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).days
        if age > _PHASE_GATE_MAX_AGE_DAYS:
            return False, (
                f"Phase Gate Y stamp {age}d old "
                f"(>{_PHASE_GATE_MAX_AGE_DAYS}d max)"
            )
    except Exception as e:
        return False, f"Phase Gate Y stamped_at invalid: {e}"
    return True, f"Phase Gate Y active (stamped {stamped_at} by {stamped_by})"


# ---------------------------------------------------------------------------
# Pure helpers (easy to unit-test, no I/O)
# ---------------------------------------------------------------------------

def _validate_customer_id(customer_id: str) -> Optional[str]:
    """Returns error string on failure, None on pass."""
    if not customer_id or not isinstance(customer_id, str):
        return "customer_id required (non-empty str)"
    if not _SF_ID_RE.match(customer_id):
        return (f"customer_id {customer_id!r} does not match SF Id shape "
                "(15 or 18 alphanumeric chars)")
    return None


def build_processing_task_payload(
    *,
    customer_id: str,
    customer_name: str,
    contact_id: Optional[str],
    relationship_manager_id: Optional[str],
    automation_type: str,
    due_date: Optional[str] = None,
) -> dict:
    """Build the Processing_task__c insert payload.

    Payload contract source: PCSE Strategist D §3.1 +
    memory/reference_run_automation_screenflow.md §"Worked example".

    Required:    Subject__c, Status__c, Processing_task_type__c,
                 Client_name__c, Primary_processsing_person__c, Due_date__c
    Best-effort: Contact__c, Relationship_Manager__c (omitted when null —
                 the listener tolerates null per SF flow defaults)
    """
    if automation_type not in AUTOMATION_TYPE_MAP:
        raise AutomationRunnerError(
            f"unknown automation_type {automation_type!r}; "
            f"expected one of {sorted(AUTOMATION_TYPE_MAP)}"
        )
    pt_type = AUTOMATION_TYPE_MAP[automation_type]
    subject = f"{pt_type} - {customer_name}"
    if due_date is None:
        due_date = (date.today() + timedelta(days=1)).isoformat()

    payload = {
        "Subject__c":                    subject,
        "Status__c":                     "Open",
        "Processing_task_type__c":       pt_type,
        "Client_name__c":                customer_id,
        "Primary_processsing_person__c": SYSTEM_BOT_TSE_ID,
        "Due_date__c":                   due_date,
    }
    if contact_id:
        payload["Contact__c"] = contact_id
    if relationship_manager_id:
        payload["Relationship_Manager__c"] = relationship_manager_id
    return payload


def build_resolver_change_payload(
    *,
    automation_type: str,
    customer_id: str,
    trace_id: str,
) -> dict:
    """Build the Resolver_Change__c payload (Rule P1).

    Reversible__c = false per D §3.3: IRD-side effects (PIN creation,
    payment-info pulls, etc.) cannot be rolled back from SF.
    """
    pt_type = AUTOMATION_TYPE_MAP[automation_type]
    return {
        "Target_Object__c":      "Processing_task__c",
        "Target_Field__c":       "Processing_task_type__c",
        "Old_Value__c":          "",
        "New_Value__c":          pt_type,
        "Old_Value_Type__c":     "string",
        "New_Value_Type__c":     "string",
        "Change_Type__c":        "automation_invoke",
        "Status__c":             "pending",
        "Reversible__c":         False,
        "Step_Order__c":         1,
        # Customer linkage + trace for cross-reference.
        # Customer__c on Resolver_Change__c is optional in many orgs; the
        # adapter omits it to avoid schema-mismatch errors. The Client_name__c
        # on the PT row is the canonical join key.
    }


# ---------------------------------------------------------------------------
# Pending-actions log (RECOVER-ON-FAILURE per CLAUDE.md Rule 3)
# ---------------------------------------------------------------------------

_PENDING_ACTIONS_DIR_CANDIDATES = [
    pathlib.Path("G:/My Drive/CEO OS/working files/pending_actions"),
    pathlib.Path("./pending_actions"),
]


def _log_pending_action(entry: dict) -> None:
    """Best-effort write to pending_actions/. Never raises."""
    for d in _PENDING_ACTIONS_DIR_CANDIDATES:
        try:
            d.mkdir(parents=True, exist_ok=True)
            fp = d / f"automation_runner_{entry.get('trace_id', 'noid')}.json"
            fp.write_text(json.dumps(entry, default=str, indent=2),
                          encoding="utf-8")
            return
        except Exception:
            continue


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def invoke_sl_automation(
    *,
    customer_id: str,
    automation_type: str,
    dry_run: bool = True,
    trace_id: Optional[str] = None,
    sf_client: Any = None,
    phase_gate_reader: Optional[Callable[[], tuple]] = None,
) -> dict:
    """Insert a Processing_task__c row for the SL IRD listener to consume.

    Args:
        customer_id: Salesforce Customer__c.Id (15 or 18 chars).
        automation_type: one of PIN_REQUEST | TEMP_PIN_RESET | LOGIN_CHECK |
            TAX_YEARS_CHECK | PAYMENT_INFO | DIN_COLLECTION.
        dry_run: True (default) means NO SF writes. Returns would_insert
            payload only. False requires Phase Gate Y active.
        trace_id: optional caller-supplied trace id. Defaults to a uuid4.
        sf_client: DI seam for tests. Must expose .query(soql) and
            .post(sobject, body) returning dict.
        phase_gate_reader: DI seam for tests. Callable -> (active, reason).

    Returns:
        dict with keys:
          ok                          (bool)
          processing_task_id          (str | None)
          resolver_change_id          (str | None)
          dry_run                     (bool)
          would_insert                (dict — the PT payload)
          trace_id                    (str)
          dispatched_to               (str — aws_lambda | docker_poller | ...)
          expected_completion_minutes (int | None)
          errors                      (list[str])
          actions_taken               (list[str])
    """
    trace_id = trace_id or f"ar-{uuid.uuid4().hex[:12]}"
    result: dict = {
        "ok": False,
        "processing_task_id": None,
        "resolver_change_id": None,
        "dry_run": dry_run,
        "would_insert": {},
        "trace_id": trace_id,
        "dispatched_to": None,
        "expected_completion_minutes": None,
        "errors": [],
        "actions_taken": [],
    }

    # --- INPUT VALIDATION ---
    cust_err = _validate_customer_id(customer_id)
    if cust_err:
        result["errors"].append(cust_err)
        return result
    if automation_type not in AUTOMATION_TYPE_MAP:
        result["errors"].append(
            f"automation_type {automation_type!r} not allowed; "
            f"expected one of {sorted(AUTOMATION_TYPE_MAP)}"
        )
        return result

    routing = AUTOMATION_ROUTING[automation_type]
    result["dispatched_to"] = routing["dispatched_to"]
    result["expected_completion_minutes"] = routing["expected_minutes"]

    # --- PHASE GATE Y (live mode only) ---
    if not dry_run:
        reader = phase_gate_reader or _default_phase_gate_reader
        try:
            active, reason = reader()
        except Exception as e:
            result["errors"].append(f"phase_gate_reader_raised: {e}")
            result["actions_taken"].append("phase_gate_y_error")
            return result
        if not active:
            result["errors"].append(f"live mode refused: {reason}")
            result["actions_taken"].append("phase_gate_y_blocked")
            return result
        result["actions_taken"].append(f"phase_gate_y_ok: {reason}")

    client = sf_client or _default_sf_client()

    # --- READ CUSTOMER (for Contact__c + RM lookup + name in subject) ---
    soql = (
        "SELECT Id, Name, Contact__c, Assigned_Relationship_Manager__c "
        f"FROM Customer__c WHERE Id = '{customer_id}'"
    )
    try:
        q = client.query(soql)
    except Exception as e:
        result["errors"].append(f"sf_query_failed: {e}")
        return result
    if q.get("error"):
        result["errors"].append(f"sf_query_http_error: {q}")
        return result
    records = q.get("records") or []
    if not records:
        result["errors"].append(f"no Customer__c found with Id={customer_id}")
        return result
    rec = records[0]
    cust_name = rec.get("Name") or customer_id
    contact_id = rec.get("Contact__c")
    rm_id = rec.get("Assigned_Relationship_Manager__c")
    result["actions_taken"].append(f"read_customer:{customer_id}")

    # --- BUILD PT PAYLOAD ---
    try:
        pt_payload = build_processing_task_payload(
            customer_id=customer_id,
            customer_name=cust_name,
            contact_id=contact_id,
            relationship_manager_id=rm_id,
            automation_type=automation_type,
        )
    except AutomationRunnerError as e:
        result["errors"].append(str(e))
        return result
    result["would_insert"] = pt_payload

    # --- DRY-RUN SHORT CIRCUIT ---
    if dry_run:
        result["actions_taken"].append("dry_run_no_write")
        result["ok"] = True
        return result

    # --- LIVE: Rule P1 — Resolver_Change__c BEFORE the SF write ---
    rc_payload = build_resolver_change_payload(
        automation_type=automation_type,
        customer_id=customer_id,
        trace_id=trace_id,
    )
    rc_resp = client.post("Resolver_Change__c", rc_payload)
    if rc_resp.get("error") or not rc_resp.get("id"):
        result["errors"].append(f"resolver_change_failed: {rc_resp}")
        result["actions_taken"].append("resolver_change_aborted_write")
        _log_pending_action({
            "trace_id": trace_id,
            "stage": "resolver_change",
            "automation_type": automation_type,
            "customer_id": customer_id,
            "would_insert": pt_payload,
            "rc_payload": rc_payload,
            "rc_resp": rc_resp,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        return result
    rc_id = rc_resp["id"]
    result["resolver_change_id"] = rc_id
    result["actions_taken"].append(f"resolver_change_created:{rc_id}")

    # --- LIVE: Insert Processing_task__c ---
    pt_resp = client.post("Processing_task__c", pt_payload)
    if pt_resp.get("error") or not pt_resp.get("id"):
        result["errors"].append(f"pt_insert_failed: {pt_resp}")
        result["actions_taken"].append("pt_insert_failed")
        _log_pending_action({
            "trace_id": trace_id,
            "stage": "pt_insert",
            "automation_type": automation_type,
            "customer_id": customer_id,
            "would_insert": pt_payload,
            "resolver_change_id": rc_id,
            "pt_resp": pt_resp,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        return result

    result["processing_task_id"] = pt_resp["id"]
    result["actions_taken"].append(f"pt_inserted:{pt_resp['id']}")
    result["ok"] = True
    return result
