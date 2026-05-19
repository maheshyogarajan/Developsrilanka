"""fiesta.submit.models -- DB models for S14 Submit (final filing gate).

Wave 3 Week 5 (2026-05-20).

Lifecycle
---------
A `Submission` row tracks a single customer's intent to file for a given tax
year. Status transitions (canonical lifecycle):

    preparing                  customer is still on S2-S12 (no S14 work yet)
        |
        v
    final-gate-pending         customer arrived at S14; X6 final gate evaluated
        |
        v
    awaiting-attestation       gate passed (or yellow-overridden); waiting on sig
        |
        v
    attested                   signature captured (attestation_signed_at set)
        |
        v
    export-generated           IRD-ready ZIP built (ird_export_generated_at set)
        |
        v
    customer-filed-on-ird      customer self-reports filing (customer_filed_at)
        |
        v  (terminal -- no further state change unless customer "reopens")
    [reopen]                   customer unlocks for edits -> back to preparing
                               (attestation/export invalidated; must redo)

The "abandoned" status is terminal for a Submission row that was started but
never attested; a new Submission row replaces it for the same (user, tax_year)
pair (preserving the audit trail on the abandoned one).

Mutability rules (enforced by routes, not the model)
----------------------------------------------------
- Once status == "attested", upstream data (S3/S4/S5/S6/S7) MUST NOT be edited.
  Routes for upstream screens check `Submission.is_locked_for_upstream_edits()`.
- A "reopen" event sets status back to "preparing" and clears attestation +
  export fields. The PRIOR attestation snapshot is preserved as an audit row
  in `SubmissionAuditEvent` (one row per reopen).

The Submission is the customer-facing record. SF-side `Resolver_Action__c`
gets a corresponding entry per Resolver Change Ledger Rules (P1) for any
upstream side-effects -- but that wiring is done by routes/services, not
the model itself.

§195 + Electronic Transactions Act considerations
-------------------------------------------------
- attestation_text is SNAPSHOTTED at signing time (full text stored as-is)
  rather than regenerated on read. This protects against template churn.
- attestation_signature stores typed-name (used for `signature_name`),
  client IP, and ISO8601 timestamp. Per Electronic Transactions Act 19 of
  2006 the customer's typed name + identifying metadata + intent ("I declare")
  is enough to constitute an electronic signature for non-deed instruments.
- audit_pack_pdf_path references the S12 audit pack (regenerated immediately
  before export so the snapshot matches the attested figures).

Multi-tax-year safety
---------------------
(user_id, tax_year) is UNIQUE for non-abandoned, non-reopened submissions;
the routes enforce this on creation. Tests cover the multi-year case (a
customer simultaneously preparing 25/26 + 26/27 has two separate rows).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app import db


# ---------------------------------------------------------------------------
# Submission -- one row per filing attempt
# ---------------------------------------------------------------------------
class Submission(db.Model):
    """One row per S14 submission attempt."""

    __tablename__ = "submissions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )

    # Tax-year string in canonical "YYYY/YYYY" form (e.g. "2025/2026").
    # For SL fiscal year 1 April -> 31 March, "2025/2026" = 1 Apr 2025-31 Mar 2026.
    tax_year = db.Column(db.String(16), nullable=False, index=True)

    # Lifecycle. See module docstring for transitions.
    status = db.Column(
        db.String(32),
        nullable=False,
        default="preparing",
        index=True,
    )

    # Snapshot of the tax bill at finalization. Frozen on attestation.
    final_tax_payable_lkr = db.Column(db.Numeric(14, 2), nullable=True)
    tax_bill_finalized_at = db.Column(db.DateTime, nullable=True)

    # Attestation. attestation_text is a full snapshot; attestation_signature
    # is JSON: {"signature_name": str, "ip": str, "timestamp_iso": str,
    # "session_id": str|None, "user_agent": str|None}.
    attestation_text = db.Column(db.Text, nullable=True)
    attestation_signature = db.Column(db.Text, nullable=True)  # JSON
    attestation_signed_at = db.Column(db.DateTime, nullable=True)

    # IRD-ready export (the ZIP built by build_export_zip).
    ird_export_generated_at = db.Column(db.DateTime, nullable=True)
    ird_export_zip_path = db.Column(db.String(512), nullable=True)
    ird_export_zip_sha256 = db.Column(db.String(64), nullable=True)

    # S12 audit pack snapshot (regenerated at export time so figures match).
    audit_pack_pdf_path = db.Column(db.String(512), nullable=True)

    # Customer self-reported filing.
    customer_filed_at = db.Column(db.DateTime, nullable=True)
    customer_filed_ack_number = db.Column(db.String(64), nullable=True)

    # Yellow-warning override capture. JSON list of {rule_id, ack_text, ack_at_iso}.
    customer_acknowledged_warnings_json = db.Column(
        db.Text, nullable=False, default="[]"
    )

    # Audit metadata.
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Optional client-side metadata snapshot at gate evaluation time (JSON).
    # Useful for replay / dispute resolution; not authoritative.
    gate_snapshot_json = db.Column(db.Text, nullable=False, default="{}")

    # Relationship to confirmation receipts (customer uploads them later).
    receipts = db.relationship(
        "IrdConfirmationReceipt",
        backref="submission",
        lazy=True,
        cascade="all, delete-orphan",
    )

    # ------------------------------------------------------------------
    # Helpers (pure Python, no DB writes)
    # ------------------------------------------------------------------
    def is_locked_for_upstream_edits(self) -> bool:
        """Return True if upstream screens (S3-S7) MUST refuse edits.

        Locked states are anything from attestation onward, EXCEPT the
        terminal abandoned state (where the customer never proceeded).
        """
        return self.status in {
            "attested",
            "export-generated",
            "customer-filed-on-ird",
        }

    def can_attest(self) -> bool:
        """Return True if attestation can currently be captured."""
        return self.status in {"final-gate-pending", "awaiting-attestation"}

    def can_export(self) -> bool:
        """Return True if export pack can currently be generated."""
        return self.status in {"attested", "export-generated"}

    def can_mark_filed(self) -> bool:
        """Return True if mark-filed can currently fire."""
        return self.status in {"export-generated", "customer-filed-on-ird"}

    def reopen_for_edits(self) -> None:
        """Reopen a locked submission for edits.

        Clears attestation + export fields and resets status to 'preparing'.
        The PRIOR attestation snapshot should already be persisted in the
        SubmissionAuditEvent table by the caller BEFORE calling this method.
        """
        self.status = "preparing"
        self.attestation_text = None
        self.attestation_signature = None
        self.attestation_signed_at = None
        self.ird_export_generated_at = None
        self.ird_export_zip_path = None
        self.ird_export_zip_sha256 = None
        # NB: customer_filed_at/_ack_number are NOT cleared -- if the customer
        # actually filed and we then reopen, the filing fact is historic truth.
        # The customer can choose to file a corrected return next.

    def acknowledged_warnings(self) -> list[dict[str, Any]]:
        """Decode the JSON acknowledged-warnings list."""
        try:
            return json.loads(self.customer_acknowledged_warnings_json or "[]")
        except (TypeError, ValueError):
            return []

    def add_acknowledged_warning(
        self, rule_id: str, ack_text: str, ack_at_iso: str
    ) -> None:
        """Append a yellow-warning ack to the JSON list (in-memory)."""
        items = self.acknowledged_warnings()
        items.append(
            {
                "rule_id": rule_id,
                "ack_text": ack_text,
                "ack_at_iso": ack_at_iso,
            }
        )
        self.customer_acknowledged_warnings_json = json.dumps(items)


# ---------------------------------------------------------------------------
# IrdConfirmationReceipt -- customer-uploaded IRD acknowledgment PDF
# ---------------------------------------------------------------------------
class IrdConfirmationReceipt(db.Model):
    """One row per IRD acknowledgment receipt uploaded by the customer.

    A submission may have MULTIPLE receipts (initial ack + amended return ack)
    so this is one-to-many. The latest receipt's `filed_at` is used for any
    "when was this filed?" query.
    """

    __tablename__ = "ird_confirmation_receipts"

    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(
        db.Integer, db.ForeignKey("submissions.id"), nullable=False, index=True
    )

    # IRD acknowledgment number (free text -- IRD format varies year to year).
    ird_acknowledgment_number = db.Column(db.String(64), nullable=True)

    # Customer-reported filing timestamp. May be earlier than `uploaded_at`.
    filed_at = db.Column(db.DateTime, nullable=False)

    # Who uploaded -- always the submission's user in v1, but we keep the FK
    # explicit for forward compatibility with delegate-uploads (v1.1+).
    uploaded_by_user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False
    )
    uploaded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # Receipt PDF location (filesystem path -- S3 hook reserved as for S8).
    receipt_pdf_path = db.Column(db.String(512), nullable=True)
    receipt_pdf_sha256 = db.Column(db.String(64), nullable=True)
    receipt_pdf_byte_size = db.Column(db.Integer, nullable=False, default=0)


# ---------------------------------------------------------------------------
# SubmissionAuditEvent -- append-only audit log
# ---------------------------------------------------------------------------
class SubmissionAuditEvent(db.Model):
    """Append-only audit log for Submission state transitions.

    Every state transition writes one row here. Reopens MUST snapshot the
    pre-reopen attestation text + signature into `payload_json` so we can
    prove (later) "this is what the customer originally attested to even
    though they reopened and amended later."

    event_type values
    -----------------
        gate-evaluated     final gate fired -- payload has trace
        attestation-signed customer signed -- payload has signature blob
        export-generated   ZIP built -- payload has sha256+path
        mark-filed         customer self-reported -- payload has ack#
        receipt-uploaded   PDF receipt uploaded -- payload has receipt id
        reopen             customer unlocked -- payload has PRIOR attest blob
    """

    __tablename__ = "submission_audit_events"

    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(
        db.Integer, db.ForeignKey("submissions.id"), nullable=False, index=True
    )
    event_type = db.Column(db.String(32), nullable=False)
    event_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    payload_json = db.Column(db.Text, nullable=False, default="{}")
    created_by_ip = db.Column(db.String(64), nullable=True)

    def payload(self) -> dict[str, Any]:
        try:
            return json.loads(self.payload_json or "{}")
        except (TypeError, ValueError):
            return {}


__all__ = [
    "Submission",
    "IrdConfirmationReceipt",
    "SubmissionAuditEvent",
]
