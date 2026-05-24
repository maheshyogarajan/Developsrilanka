"""
yoy_models.py — YoY (year-over-year) retention nudge ORM table.

Tier D4 / C2 (2026-05-24). Tracks scheduled + sent YoY nudges so the same
nudge isn't fired twice in the same year for the same user. Same shape as
A5's hypothetical LifecycleEmail (parallel branch — we use our own table
since the A5 branch isn't merged yet; consolidation can happen later).

Schema rationale
----------------
* nudge_key: short slug (apr_1_new_year / payment_deadline_30d /
             filing_deadline_30d / renewal_30d). Stable identifier
             matching templates/emails/yoy/<key>.html.
* tax_year:  the tax year the nudge applies to (e.g. "2026/27"). For
             renewal nudges this is the EXPIRING year (the year the
             subscription is closing on).
* dedup_key: UNIQUE composite ``f"{user_id}:{nudge_key}:{tax_year}"``.
             Prevents same nudge in same year for same user; also lets
             a single row track schedule + send state.
* scheduled_at / sent_at: nullable timestamps. sent_at NULL = scheduled
             but not yet dispatched (or send stubbed).

Wiring
------
* Registered into the SQLAlchemy metadata via ``register_models()`` —
  same pattern as fiesta.paywall.models. Called from
  ``tasks/yoy_nudges_run.py`` at task fire time so the worker has the
  model loaded; also called eagerly from main.py for the web process.
* Table is created by ``db.create_all()`` in main.py boot, AND by the
  belt-and-braces migration ``migrations/add_yoy_nudges.py``.
"""
from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger(__name__)

YoYNudge = None  # type: ignore[assignment]
_registered = False


def register_models():
    """Define YoYNudge against the live ``db``. Idempotent.

    Returns the YoYNudge class.
    """
    global YoYNudge, _registered
    if _registered:
        return YoYNudge

    from app import db

    class _YoYNudge(db.Model):  # type: ignore[misc, valid-type]
        __tablename__ = "yoy_nudge"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(
            db.Integer, db.ForeignKey("user.id"), nullable=False, index=True,
        )
        nudge_key = db.Column(db.String(64), nullable=False, index=True)
        tax_year = db.Column(db.String(8), nullable=False)  # "2026/27"
        scheduled_at = db.Column(
            db.DateTime, nullable=False, default=datetime.utcnow,
        )
        sent_at = db.Column(db.DateTime, nullable=True)
        # Composite dedup gate. Format: f"{user_id}:{nudge_key}:{tax_year}".
        dedup_key = db.Column(
            db.String(128), nullable=False, unique=True, index=True,
        )
        # Optional send-side tracking
        send_status = db.Column(
            db.String(16), nullable=False, default="scheduled",
        )  # scheduled | sent | failed | stubbed
        send_error = db.Column(db.String(500), nullable=True)

        def __repr__(self):  # pragma: no cover
            return (
                f"<YoYNudge id={self.id} user={self.user_id} "
                f"key={self.nudge_key} year={self.tax_year} "
                f"status={self.send_status}>"
            )

        @staticmethod
        def make_dedup_key(user_id: int, nudge_key: str, tax_year: str) -> str:
            return f"{user_id}:{nudge_key}:{tax_year}"

    YoYNudge = _YoYNudge
    _registered = True
    log.info("yoy_models registered (yoy_nudge)")
    return YoYNudge


def get_model():
    """Lazy accessor — registers if not already registered."""
    if not _registered:
        register_models()
    return YoYNudge


__all__ = ["YoYNudge", "register_models", "get_model"]
