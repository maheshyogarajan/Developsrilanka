"""fiesta.compliance.override -- CEO-override hook for compliance warnings.

Customer-side override semantics
--------------------------------
- YELLOW warnings:  customer MAY override by clicking "Proceed anyway".
  The override is logged to the customer's override_history JSON column
  and to the compliance_gate_events table (so analytics can correlate
  override rate per rule). Customer proceeds.

- RED blocks:       customer CANNOT override. The override request is
  routed to a consultant booking (S17, Wave 5 v1.1). For v1.0 (no
  consultant booking shipped), `route_block_to_consultant` returns a
  structured response the UI renders as "Book a consultant" CTA.

Public API
----------
    request_override(customer_id, rule_id, severity, reason)
        -> OverrideOutcome

    route_block_to_consultant(customer_id, rule_id, block_payload)
        -> ConsultantBookingHandoff

    get_override_history(customer_id) -> list[dict]

The functions are intentionally DB-light so they can run in serverless /
edge contexts. SQLite for v1; postgres for production via env-driven
connection swap (same pattern as fiesta.compliance.events).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger("fiesta.compliance.override")


class OverrideOutcome(BaseModel):
    """Result of a customer override request."""

    accepted: bool
    routed_to: Literal["customer_self_serve", "consultant_booking", "denied"]
    message: str
    override_id: str | None = None
    consultant_booking_handoff: dict[str, Any] | None = None


class ConsultantBookingHandoff(BaseModel):
    """Payload the UI uses to deep-link to S17 consultant booking."""

    customer_id: str
    triggering_rule_id: str
    block_summary: str
    suggested_consultant_skills: list[str] = Field(default_factory=list)
    prefill_brief: str = ""


_DB_PATH_ENV = "FIESTA_COMPLIANCE_DB_PATH"
_DEFAULT_DB_PATH = "compliance_gate_events.sqlite3"
_TABLE_CREATED = False


def _get_db_path() -> str:
    return os.environ.get(_DB_PATH_ENV, _DEFAULT_DB_PATH)


def _ensure_override_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS compliance_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            reason TEXT,
            outcome TEXT NOT NULL,
            timestamp_unix REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_compliance_overrides_customer
            ON compliance_overrides (customer_id, timestamp_unix DESC)
        """
    )
    conn.commit()


def request_override(
    customer_id: str,
    rule_id: str,
    severity: Literal["yellow", "red"],
    reason: str = "",
) -> OverrideOutcome:
    """Customer requests an override on a fired rule.

    Args:
        customer_id: Stable customer id.
        rule_id:     The rule_id (matching gate.py rule IDs) being overridden.
        severity:    "yellow" (warning) or "red" (block).
        reason:      Optional free-form text from customer ("It's actually for
                     X reason..."). Stored verbatim for audit.

    Returns:
        OverrideOutcome with `accepted` and `routed_to` set. UI:
          - yellow accepted -> customer proceeds, banner dismissed.
          - red denied      -> render consultant-booking CTA from
                               `consultant_booking_handoff`.
    """
    if severity == "red":
        handoff = route_block_to_consultant(
            customer_id=customer_id,
            rule_id=rule_id,
            block_payload={"reason": reason},
        )
        _log_override(
            customer_id=customer_id,
            rule_id=rule_id,
            severity=severity,
            reason=reason,
            outcome="denied_routed_to_consultant",
        )
        return OverrideOutcome(
            accepted=False,
            routed_to="consultant_booking",
            message=(
                "This issue needs a consultant review -- it's not something "
                "you can override yourself. Book a 30-min slot below."
            ),
            consultant_booking_handoff=handoff.model_dump(),
        )

    # Yellow -- accept the override, persist for audit.
    override_id = f"OVR-{int(time.time() * 1000)}-{customer_id[:8]}"
    _log_override(
        customer_id=customer_id,
        rule_id=rule_id,
        severity=severity,
        reason=reason,
        outcome=f"accepted:{override_id}",
    )
    return OverrideOutcome(
        accepted=True,
        routed_to="customer_self_serve",
        message="Override accepted. You can proceed.",
        override_id=override_id,
    )


def route_block_to_consultant(
    customer_id: str,
    rule_id: str,
    block_payload: dict[str, Any],
) -> ConsultantBookingHandoff:
    """Build a consultant-booking handoff for a red-block override request.

    The UI deep-links to S17 booking with this payload. For v1.0 (no S17
    shipped yet), the UI renders a "book a consultant" CTA whose href is
    Lanka.tax's existing booking page until S17 ships.
    """
    # Skill suggestions per rule -- deterministic, kept here so a new rule
    # in gate.py forces a parallel update here (caught by tests).
    skill_hints: dict[str, list[str]] = {
        "S8-SECTION-195-OVERRIDE-DENIED": ["section 195 related-party", "advisory"],
        "S14-SECTION-195-MISSING": ["section 195 related-party", "filing-prep"],
        "S12-DEDUCTION-RATIO-EXCESSIVE": ["deduction defensibility", "audit-prep"],
        "S14-DEDUCTION-RATIO-FINAL": ["deduction defensibility", "audit-prep"],
        "S2-EMAIL-FORMAT": ["onboarding"],  # unlikely but defined for completeness
    }
    return ConsultantBookingHandoff(
        customer_id=customer_id,
        triggering_rule_id=rule_id,
        block_summary=str(block_payload.get("reason") or rule_id),
        suggested_consultant_skills=skill_hints.get(rule_id, ["general"]),
        prefill_brief=(
            f"Customer hit compliance block at gate rule '{rule_id}'. "
            f"Reason supplied: {block_payload.get('reason') or '(none)'}. "
            "Please review the customer's full case file before the session."
        ),
    )


def get_override_history(customer_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Return recent override events for one customer (newest first)."""
    try:
        with sqlite3.connect(_get_db_path()) as conn:
            _ensure_override_table(conn)
            cur = conn.execute(
                """
                SELECT customer_id, rule_id, severity, reason, outcome, timestamp_unix
                FROM compliance_overrides
                WHERE customer_id = ?
                ORDER BY timestamp_unix DESC
                LIMIT ?
                """,
                (customer_id, limit),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.error("get_override_history failed: %s", exc)
        return []


def _log_override(
    customer_id: str,
    rule_id: str,
    severity: str,
    reason: str,
    outcome: str,
) -> None:
    """Persist an override request. Never raises."""
    try:
        global _TABLE_CREATED
        with sqlite3.connect(_get_db_path()) as conn:
            if not _TABLE_CREATED:
                _ensure_override_table(conn)
                _TABLE_CREATED = True
            conn.execute(
                """
                INSERT INTO compliance_overrides
                    (customer_id, rule_id, severity, reason, outcome, timestamp_unix)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (customer_id, rule_id, severity, reason, outcome, time.time()),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("override log write failed: %s", exc)


__all__ = [
    "OverrideOutcome",
    "ConsultantBookingHandoff",
    "request_override",
    "route_block_to_consultant",
    "get_override_history",
]
