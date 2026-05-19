"""fiesta.lifecycle.audit_log — append-only lifecycle event ledger.

Why this exists:
  - IRD audits ask "show me the lineage of every invoice, every reminder
    you sent, every transition." A scattered email log + a Celery beat log
    + a SQLAlchemy timestamps column doesn't survive that question.
  - Idempotency dedupe — rollover_scheduler.run_daily_pass checks
    audit.recent_keys(target_id, since) so a worker restart doesn't
    re-send Tuesday's reminders on Wednesday morning.
  - 7-year retention: SL IRD requires records for 5 years after end of
    YoA (IRA s.118). We round up to 7 to cover amendment windows.

Storage model:
  - Append-only. Every action (invoice add/edit/delete, cadence detection,
    reminder send, transition) writes a LifecycleAuditRow.
  - Backing store is pluggable: production uses SQLAlchemy via the
    SqlAlchemyAuditStore adapter (built when this is wired into the
    Flask app); tests use InMemoryAuditStore for speed.
  - Each row is immutable. "Edit invoice X" -> NEW row with event_type
    'invoice.edited', payload {before, after}. Never UPDATE rows.

Export:
  - export_pdf(customer_id, year) renders the per-customer ledger to a
    PDF (skeleton here returns the bytes from a Jinja2 template; real
    PDF generation lives in repo-root pdf_utils.py).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Row + store protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LifecycleAuditRow:
    """One immutable audit entry."""

    id: int
    event_type: str  # e.g. "invoice.created" / "reminder.year_closing_tomorrow"
    actor: str       # "user:123" / "rollover_scheduler" / "system"
    target_id: int   # usually customer_id; for cross-customer events use 0
    idempotency_key: str
    payload: dict
    created_at: datetime  # always UTC
    payload_hash: str

    @staticmethod
    def hash_payload(payload: dict) -> str:
        encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class AuditStore(Protocol):
    """Backing store contract."""

    def append(self, row: LifecycleAuditRow) -> None: ...
    def has_key(self, idempotency_key: str) -> bool: ...
    def recent(
        self,
        *,
        target_id: int,
        since: date,
        event_types: Optional[Iterable[str]] = None,
    ) -> list[LifecycleAuditRow]: ...
    def for_customer_year(
        self, customer_id: int, year_label: str
    ) -> list[LifecycleAuditRow]: ...


# ---------------------------------------------------------------------------
# In-memory store (production: replace with SqlAlchemyAuditStore)
# ---------------------------------------------------------------------------


@dataclass
class InMemoryAuditStore:
    rows: list[LifecycleAuditRow] = field(default_factory=list)
    _next_id: int = field(default=1, init=False)
    _keys: set[str] = field(default_factory=set, init=False)

    def append(self, row: LifecycleAuditRow) -> None:
        if row.idempotency_key in self._keys:
            return  # silent dedupe — store invariant
        # Re-stamp id on append so callers don't have to know.
        new_row = LifecycleAuditRow(
            id=self._next_id,
            event_type=row.event_type,
            actor=row.actor,
            target_id=row.target_id,
            idempotency_key=row.idempotency_key,
            payload=row.payload,
            created_at=row.created_at,
            payload_hash=row.payload_hash,
        )
        self.rows.append(new_row)
        self._keys.add(row.idempotency_key)
        self._next_id += 1

    def has_key(self, idempotency_key: str) -> bool:
        return idempotency_key in self._keys

    def recent(
        self,
        *,
        target_id: int,
        since: date,
        event_types: Optional[Iterable[str]] = None,
    ) -> list[LifecycleAuditRow]:
        wanted = set(event_types) if event_types else None
        return [
            r for r in self.rows
            if r.target_id == target_id
            and r.created_at.date() >= since
            and (wanted is None or r.event_type in wanted)
        ]

    def for_customer_year(
        self, customer_id: int, year_label: str
    ) -> list[LifecycleAuditRow]:
        # Filter by year_label being present in payload OR by created_at
        # falling inside the YoA window. Caller usually passes year_label
        # for invoice ledger exports.
        return [
            r for r in self.rows
            if r.target_id == customer_id
            and (
                r.payload.get("year") == year_label
                or r.payload.get("new_year") == year_label
                or r.payload.get("from_year") == year_label
            )
        ]


# ---------------------------------------------------------------------------
# Public facade
# ---------------------------------------------------------------------------


class LifecycleAudit:
    """High-level facade over the backing store.

    Use this from scheduler / reminders / invoice flows. Never write to
    the store directly — go through `record(...)` so every entry gets a
    payload_hash and idempotency check.
    """

    def __init__(self, store: Optional[AuditStore] = None) -> None:
        self.store: AuditStore = store if store is not None else InMemoryAuditStore()

    def record(
        self,
        *,
        event_type: str,
        actor: str,
        target_id: int,
        idempotency_key: str,
        payload: dict,
        created_at: Optional[datetime] = None,
    ) -> Optional[LifecycleAuditRow]:
        """Append a row. Silently dedupes on idempotency_key."""
        if self.store.has_key(idempotency_key):
            return None
        when = created_at or datetime.now(timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        row = LifecycleAuditRow(
            id=0,  # store assigns
            event_type=event_type,
            actor=actor,
            target_id=target_id,
            idempotency_key=idempotency_key,
            payload=payload,
            created_at=when,
            payload_hash=LifecycleAuditRow.hash_payload(payload),
        )
        self.store.append(row)
        return row

    def was_recorded(self, idempotency_key: str) -> bool:
        return self.store.has_key(idempotency_key)

    def recent_keys(self, *, target_id: int, since: date) -> list[str]:
        return [r.idempotency_key for r in self.store.recent(
            target_id=target_id, since=since
        )]

    def export_customer_year_ledger(
        self, *, customer_id: int, year_label: str
    ) -> dict:
        """Build the data structure for the customer's invoice ledger PDF.

        Returns a JSON-serialisable dict. The real PDF render is done by
        repo-root pdf_utils.py + a Jinja2 template; this method intentionally
        does NOT produce bytes so the lifecycle package stays free of
        ReportLab / WeasyPrint dependencies.
        """
        rows = self.store.for_customer_year(customer_id, year_label)
        return {
            "customer_id": customer_id,
            "year_label": year_label,
            "row_count": len(rows),
            "rows": [
                {
                    "id": r.id,
                    "event_type": r.event_type,
                    "actor": r.actor,
                    "created_at": r.created_at.isoformat(),
                    "payload": r.payload,
                    "payload_hash": r.payload_hash,
                }
                for r in rows
            ],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# Convenience event names — central registry so typos surface in CI rather
# than at runtime ("did we log 'invoice.create' or 'invoice.created'?").
# ---------------------------------------------------------------------------


class EventTypes:
    # Invoice lifecycle
    INVOICE_CREATED = "invoice.created"
    INVOICE_EDITED = "invoice.edited"
    INVOICE_DELETED = "invoice.deleted"
    INVOICE_PAID = "invoice.paid"

    # Cadence detection
    CADENCE_DETECTED = "cadence.detected"
    CADENCE_FLAG_IRREGULAR = "cadence.flag_irregular"
    CADENCE_ABOVE_MARKET = "cadence.above_market"

    # Year-end / transitions
    YEAR_CLOSING_TOMORROW = "lifecycle.year_closing_tomorrow"
    YEAR_ENDED = "lifecycle.year_ended"
    NEW_YEAR_TRANSITION = "lifecycle.new_year_transition_invite"
    NEW_TAX_FILE_CREATED = "lifecycle.create_new_year_tax_file"
    TRANSITION_BLOCKED = "lifecycle.transition_blocked"

    # Reminders
    REMINDER_SENT = "reminder.sent"
    REMINDER_SUPPRESSED = "reminder.suppressed_duplicate"


__all__ = [
    "LifecycleAuditRow",
    "AuditStore",
    "InMemoryAuditStore",
    "LifecycleAudit",
    "EventTypes",
]
