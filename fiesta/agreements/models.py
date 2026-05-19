"""fiesta.agreements.models -- DB models for generated Service Agreements (S8) and (later) Rental Agreements (S9).

Wave 3 (2026-05-20). Per the S8 dispatch brief + G.1.3 v0.1 proposal.

Storage model
-------------
- ServiceAgreement row per generation. NOT mutable -- regenerating creates a
  new row (different reference_id, different sha256). Audit trail = the whole
  table.
- PDF artefact stored either as a filesystem blob (pdf_path) OR an S3 key
  (pdf_s3_key); both columns nullable so deploys can pick. Hash (sha256)
  stored separately so the artefact's integrity can be re-verified at any
  later moment.
- §195 disclosure state is recorded as a triple:
    sec195_disclosure_applied  : bool (clause WAS rendered into the PDF)
    sec195_default_was_on      : bool (the detector said "default ON")
    sec195_override_reason     : optional text -- when customer marked the
                                 deal arm's-length, the justification text
                                 they typed. Note: the override does NOT
                                 suppress the disclosure clause in the PDF
                                 (clause still ships), but it is captured for
                                 audit defence.
"""
from __future__ import annotations

from datetime import datetime
from app import db


class ServiceAgreement(db.Model):
    """One row per generated Service Agreement PDF (S8 Wave 3)."""

    __tablename__ = "service_agreements"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    # ServiceProvider is a future S6 model; until that lands, we accept the
    # opaque external id (string) so this table is forward-compatible without
    # depending on a class that may not exist yet on every branch.
    service_provider_id = db.Column(db.String(64), nullable=False, index=True)

    # Identity + provenance.
    reference_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
    template_version = db.Column(db.String(16), nullable=False, default="v0.1-draft")
    generated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    generated_by_ip = db.Column(db.String(64), nullable=True)

    # Customer + counterparty snapshot at generation time (JSON text -- the
    # PDF is the canonical artefact; this is for searchability + replay).
    customer_snapshot_json = db.Column(db.Text, nullable=False, default="{}")
    sp_snapshot_json = db.Column(db.Text, nullable=False, default="{}")
    parameters_snapshot_json = db.Column(db.Text, nullable=False, default="{}")

    # Agreement parameters.
    governing_law_variant = db.Column(db.String(1), nullable=False, default="A")
    fee_structure_variant = db.Column(db.String(1), nullable=False, default="A")
    ip_variant = db.Column(db.String(1), nullable=False, default="A")
    renewal_variant = db.Column(db.String(1), nullable=False, default="A")
    currency = db.Column(db.String(8), nullable=False, default="LKR")
    term_start = db.Column(db.Date, nullable=True)
    term_end = db.Column(db.Date, nullable=True)
    monthly_fee_lkr = db.Column(db.Numeric(14, 2), nullable=True)

    # PDF artefact.
    pdf_path = db.Column(db.String(512), nullable=True)
    pdf_s3_key = db.Column(db.String(512), nullable=True)
    pdf_sha256 = db.Column(db.String(64), nullable=False)
    pdf_byte_size = db.Column(db.Integer, nullable=False, default=0)

    # Signature state.
    customer_signature_status = db.Column(
        db.String(32), nullable=False, default="unsigned"
    )
    sp_signature_status = db.Column(
        db.String(32), nullable=False, default="unsigned"
    )
    customer_signed_at = db.Column(db.DateTime, nullable=True)
    sp_signed_at = db.Column(db.DateTime, nullable=True)

    # §195 disclosure audit trail.
    sec195_disclosure_applied = db.Column(db.Boolean, nullable=False, default=False)
    sec195_default_was_on = db.Column(db.Boolean, nullable=False, default=False)
    sec195_override_reason = db.Column(db.Text, nullable=True)
    sec195_confidence = db.Column(db.Float, nullable=True)
    sec195_signals_json = db.Column(db.Text, nullable=True)

    # X6 compliance gate snapshot at generation time.
    gate_passed = db.Column(db.Boolean, nullable=False, default=True)
    gate_warnings_count = db.Column(db.Integer, nullable=False, default=0)
    gate_blocks_count = db.Column(db.Integer, nullable=False, default=0)
    gate_trace_json = db.Column(db.Text, nullable=True)

    # Lifecycle.
    is_draft_preview = db.Column(db.Boolean, nullable=False, default=False)
    superseded_by_id = db.Column(
        db.Integer, db.ForeignKey("service_agreements.id"), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover -- debug only
        return (
            f"<ServiceAgreement id={self.id} ref={self.reference_id} "
            f"user_id={self.user_id} sp={self.service_provider_id} "
            f"sec195={self.sec195_disclosure_applied}>"
        )


__all__ = ["ServiceAgreement"]
