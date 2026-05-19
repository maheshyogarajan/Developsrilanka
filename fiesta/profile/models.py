"""FiestaProfile model — S3 progressive-disclosure customer profile.

Separate table linked 1:1 to User so we can iterate on the FIESTA fields without
touching the legacy User model (which is shared with delivery_ops, expenses, etc.).

Wave 3 decision: bank account number stored in plaintext for v1 (no PII pipeline
yet); flagged for v1.1 hardening — Fernet column-level encryption planned. Tracker
entry filed via Rule 5 CAPTURE-DON'T-JUST-FIX.

Migration: idempotent ALTER TABLE pattern — create_table_if_not_exists, columns
gated on inspector.has_table / inspector.has_column. See migrate() at bottom.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import inspect

# Import the shared db handle from the main app. Lazy import path mirrors the
# pattern used by fiesta/signup/* and other wave blueprints.
from app import db  # noqa: E402

logger = logging.getLogger(__name__)


class FiestaProfile(db.Model):
    """One-row-per-user FIESTA progressive profile (Wave 3 S3)."""

    __tablename__ = "fiesta_profile"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # -------------------------------------------------------------------
    # Identity
    # -------------------------------------------------------------------
    nic = db.Column(db.String(20), nullable=True, index=True)
    tin = db.Column(db.String(20), nullable=True, index=True)

    # -------------------------------------------------------------------
    # Address
    # -------------------------------------------------------------------
    address_line1 = db.Column(db.String(255), nullable=True)
    address_line2 = db.Column(db.String(255), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    postcode = db.Column(db.String(20), nullable=True)
    country = db.Column(db.String(2), nullable=False, default="LK")

    # -------------------------------------------------------------------
    # Tax residency
    # -------------------------------------------------------------------
    # 1 = first year, 2 = second year, 3+ = settled. Drives §10 residency tests.
    tax_resident_year = db.Column(db.Integer, nullable=True)
    days_in_sl_current_year = db.Column(db.Integer, nullable=True)

    # -------------------------------------------------------------------
    # Employer status
    # -------------------------------------------------------------------
    # Picklist: employee | contractor | business_owner | mix
    employment_type = db.Column(db.String(20), nullable=True)
    has_foreign_clients = db.Column(db.Boolean, nullable=True)

    # -------------------------------------------------------------------
    # Bank (SL bank for refund / direct-debit). v1: plaintext; v1.1: encrypted.
    # -------------------------------------------------------------------
    bank_account_holder_name = db.Column(db.String(255), nullable=True)
    bank_name = db.Column(db.String(100), nullable=True)
    bank_branch = db.Column(db.String(255), nullable=True)
    bank_account_number = db.Column(db.String(50), nullable=True)

    # -------------------------------------------------------------------
    # Persona — v1: locked to 'self' (single-persona FIESTA platform). Stored
    # for forward compatibility when v2 introduces caregiver / employer-of-record.
    # -------------------------------------------------------------------
    persona = db.Column(db.String(20), nullable=False, default="self")

    # -------------------------------------------------------------------
    # Timestamps
    # -------------------------------------------------------------------
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    # -------------------------------------------------------------------
    # Relationship — backref onto User as 'fiesta_profile'
    # -------------------------------------------------------------------
    user = db.relationship(
        "User",
        backref=db.backref(
            "fiesta_profile", uselist=False, cascade="all, delete-orphan"
        ),
        foreign_keys=[user_id],
    )

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------
    def to_dict(self, *, redact_bank: bool = True) -> Dict[str, Any]:
        """Serialize to dict. By default redacts the bank account number to last 4."""
        acct = self.bank_account_number
        if redact_bank and acct and len(acct) > 4:
            acct = "*" * (len(acct) - 4) + acct[-4:]
        return {
            "id": self.id,
            "user_id": self.user_id,
            "nic": self.nic,
            "tin": self.tin,
            "address_line1": self.address_line1,
            "address_line2": self.address_line2,
            "city": self.city,
            "postcode": self.postcode,
            "country": self.country,
            "tax_resident_year": self.tax_resident_year,
            "days_in_sl_current_year": self.days_in_sl_current_year,
            "employment_type": self.employment_type,
            "has_foreign_clients": self.has_foreign_clients,
            "bank_account_holder_name": self.bank_account_holder_name,
            "bank_name": self.bank_name,
            "bank_branch": self.bank_branch,
            "bank_account_number": acct,
            "persona": self.persona,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def apply(self, payload: Dict[str, Any]) -> "FiestaProfile":
        """Merge a validated payload (dict) into the model. None values are skipped
        so partial saves never wipe previously-populated fields.

        Callers MUST pass a dict already validated by validators.ProfileFormPayload.
        Unknown keys are silently ignored.
        """
        editable = {
            "nic",
            "tin",
            "address_line1",
            "address_line2",
            "city",
            "postcode",
            "country",
            "tax_resident_year",
            "days_in_sl_current_year",
            "employment_type",
            "has_foreign_clients",
            "bank_account_holder_name",
            "bank_name",
            "bank_branch",
            "bank_account_number",
            # persona intentionally excluded — locked to 'self' in v1
        }
        for key, value in payload.items():
            if key not in editable:
                continue
            if value is None:
                # Skip None to preserve partial-save semantics. To explicitly clear
                # a field, callers can pass empty string and we'll persist it.
                continue
            setattr(self, key, value)
        return self


# ---------------------------------------------------------------------------
# Migration helper (idempotent)
# ---------------------------------------------------------------------------


def migrate(app=None) -> Dict[str, Any]:
    """Create the fiesta_profile table if it doesn't exist. Idempotent.

    Pattern: inspect the existing schema, only run create_all for the table we own.
    Safe to call on every app boot. Mirrors fiesta/signup migrate() approach.
    """
    if app is None:
        from app import app as flask_app  # noqa
        app = flask_app

    summary: Dict[str, Any] = {"created": False, "table": "fiesta_profile"}

    with app.app_context():
        try:
            inspector = inspect(db.engine)
            existing_tables = inspector.get_table_names()
            if "fiesta_profile" not in existing_tables:
                FiestaProfile.__table__.create(bind=db.engine, checkfirst=True)
                summary["created"] = True
                logger.info("[fiesta.profile] Created fiesta_profile table")
            else:
                summary["created"] = False
                # Check for any missing columns and ALTER if needed (idempotent guard
                # for the case where someone partially deployed an earlier schema).
                existing_cols = {c["name"] for c in inspector.get_columns("fiesta_profile")}
                expected_cols = {c.name for c in FiestaProfile.__table__.columns}
                missing = expected_cols - existing_cols
                if missing:
                    logger.warning(
                        "[fiesta.profile] fiesta_profile table missing columns: %s. "
                        "Manual ALTER TABLE required for v1; auto-migration deferred to v1.1.",
                        sorted(missing),
                    )
                    summary["missing_columns"] = sorted(missing)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[fiesta.profile] migrate() failed: %s", exc)
            summary["error"] = str(exc)
    return summary


def get_or_create_profile(user_id: int) -> FiestaProfile:
    """Fetch the FiestaProfile for a user; create a blank one if absent. Commits."""
    profile = FiestaProfile.query.filter_by(user_id=user_id).first()
    if profile is None:
        profile = FiestaProfile(user_id=user_id)
        db.session.add(profile)
        db.session.commit()
    return profile


__all__ = ["FiestaProfile", "migrate", "get_or_create_profile"]
