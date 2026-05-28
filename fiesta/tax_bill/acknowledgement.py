"""fiesta.tax_bill.acknowledgement — F6.3 launch-gate.

One-time per (user, tax_year) acknowledgement that the figures rendered on
/tax-bill are estimates derived from data the user provided, not a filed
return, and that the user is the responsible filer.

This is distinct from the §195 attestation captured at /submit (see
fiesta/submit/attestation.py). That attestation is a legal signature on a
filed return. THIS acknowledgement is a launch-day-friendly interstitial
that prevents a friend cohort from screenshotting `/tax-bill` and treating
the headline number as a finished tax outcome.

Lifecycle
---------
- A user's first GET /tax-bill/<ya> for a given tax year that lacks an
  acknowledgement row redirects to GET .../acknowledge (which renders the
  interstitial card).
- The form POSTs to .../acknowledge with the checkbox ticked + a
  click-through button. The route writes one row and redirects back.
- Subsequent visits to /tax-bill/<ya> for the same (user, tax_year) skip
  the interstitial.
- Different tax years require separate acknowledgements (so a returning
  customer is re-prompted in a new YA).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Defensive: importable in headless tests without a Flask app context.
try:
    from app import db
    _HAS_DB = True
except Exception:  # pragma: no cover
    _HAS_DB = False
    db = None  # type: ignore


if _HAS_DB:

    class TaxBillAcknowledgement(db.Model):  # type: ignore[misc]
        """One row per (user, tax_year) the user has seen the F6.3 interstitial for."""

        __tablename__ = "tax_bill_acknowledgement"
        __table_args__ = (
            db.UniqueConstraint(
                "user_id",
                "tax_year_s4",
                name="uq_tax_bill_ack_user_year",
            ),
        )

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(
            db.Integer,
            db.ForeignKey("user.id"),
            nullable=False,
            index=True,
        )
        # Canonical "YYYY-YY" form (e.g. "2025-26"). Matches the S4-format
        # used across fiesta.tax_bill.aggregator.normalise_tax_year_to_s4_format.
        tax_year_s4 = db.Column(db.String(8), nullable=False)
        acknowledged_at = db.Column(
            db.DateTime(timezone=True),
            nullable=False,
            default=lambda: datetime.now(timezone.utc),
        )
        client_ip = db.Column(db.String(64), nullable=True)
        user_agent = db.Column(db.String(512), nullable=True)

        def __repr__(self) -> str:  # pragma: no cover
            return (
                f"<TaxBillAcknowledgement user={self.user_id} "
                f"ya={self.tax_year_s4} at={self.acknowledged_at}>"
            )

else:  # pragma: no cover

    class TaxBillAcknowledgement:  # type: ignore[misc]
        pass


def is_acknowledged(user_id: int, tax_year_s4: str) -> bool:
    """True if the user has already acknowledged the interstitial for this YA.

    Wrapped in a try/except so a DB blip never breaks the page render. The
    cost of returning True-on-error would be a missed interstitial; the cost
    of returning False-on-error is one extra acknowledgement click. The
    latter is the safer failure mode (interstitial re-shown), so on error
    we return False.
    """
    if not _HAS_DB:
        return False
    try:
        row = (
            TaxBillAcknowledgement.query
            .filter_by(user_id=int(user_id), tax_year_s4=tax_year_s4)
            .first()
        )
        return row is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "is_acknowledged lookup failed for user=%s ya=%s: %s",
            user_id, tax_year_s4, exc,
        )
        return False


def record_acknowledgement(
    *,
    user_id: int,
    tax_year_s4: str,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> bool:
    """Write one row. Idempotent — a duplicate is a no-op + True.

    Returns True on success (or already-exists). Returns False only on
    unrecoverable DB error.
    """
    if not _HAS_DB:
        return False

    if is_acknowledged(user_id, tax_year_s4):
        return True

    try:
        row = TaxBillAcknowledgement(
            user_id=int(user_id),
            tax_year_s4=tax_year_s4,
            acknowledged_at=datetime.now(timezone.utc),
            client_ip=(client_ip or None),
            user_agent=(user_agent or None),
        )
        db.session.add(row)
        db.session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "record_acknowledgement write failed for user=%s ya=%s: %s",
            user_id, tax_year_s4, exc,
        )
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return False


__all__ = [
    "TaxBillAcknowledgement",
    "is_acknowledged",
    "record_acknowledgement",
]
