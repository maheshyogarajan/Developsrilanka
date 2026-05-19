"""
Engagement Engine models — Wave 3.1 (2026-05-18).

ONE table — `in_app_banners` — one row per dispatched in-app nudge. The
engagement engine writes here when a rule's channel is 'in_app' (or 'both');
the front-end polls /api/in_app_nudges to render undismissed rows.

DESIGN INTENT (matches the customer_profiles / events pattern):

  * SQLAlchemy model is the application-code surface (queries, relations).
  * A raw CREATE TABLE IF NOT EXISTS in _ensure_in_app_banners_table()
    runs at import time as a belt-and-braces safety net for boot-order
    edge cases (Celery worker boots before main.py db.create_all runs).
  * Composite index (user_id, dismissed_at, created_at DESC) is the
    primary read path: "give me the undismissed banners for user X,
    newest first" hits a single index scan.
  * dismissed_at NULL = banner is live; non-NULL = dismissed (we soft-delete
    so the audit trail survives).

NOTE — Telegram is deliberately not a channel option (council #2 constraint:
FIESTA has no Telegram integration; that's a CEO-OS plane). Channels are
'email' (SendGrid) and 'in_app' (this table).
"""
from __future__ import annotations

import logging
from datetime import datetime

from app import db

log = logging.getLogger(__name__)


class InAppBanner(db.Model):
    """One dispatched in-app nudge. Rendered as a dismissible banner in the
    layout.html shell when the front-end polls /api/in_app_nudges.

    Lifecycle:
      created_at set on insert     →  banner is live, returned by GET /api/in_app_nudges
      dismissed_at set by user     →  banner hidden from GET; row retained for audit
    """
    __tablename__ = "in_app_banners"

    id = db.Column(db.Integer, primary_key=True)

    # ON DELETE CASCADE — when a user account is purged, their banner
    # history goes with them. Symmetric with CustomerProfile + RemittanceEntry
    # which both CASCADE on user delete.
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The nudge rule key from engagement_engine.NUDGE_RULES (e.g. 'inactive_3d').
    # Kept as VARCHAR not enum so new rules can be added in code without a migration.
    rule_key = db.Column(db.String(64), nullable=False)

    # Display content — the rule's template fills these in at dispatch time.
    headline = db.Column(db.String(255), nullable=False)
    body = db.Column(db.Text, nullable=False)
    cta_text = db.Column(db.String(64), nullable=False)
    cta_url = db.Column(db.String(512), nullable=False)

    dismissed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )

    # Composite index — primary read path is
    # "WHERE user_id = X AND dismissed_at IS NULL ORDER BY created_at DESC".
    # NULL sorts last in btree by default (ASC) and first (DESC), but we
    # filter to NULL-only so the planner uses this for the equality + sort.
    __table_args__ = (
        db.Index(
            "ix_in_app_banners_user_dismissed_created",
            "user_id",
            "dismissed_at",
            db.text("created_at DESC"),
        ),
    )

    def __repr__(self):
        live = "live" if self.dismissed_at is None else f"dismissed@{self.dismissed_at}"
        return (
            f"<InAppBanner id={self.id} user={self.user_id} rule={self.rule_key!r} "
            f"{live}>"
        )


def _ensure_in_app_banners_table():
    """Idempotent. Runs on module import; cheap. Mirrors the pattern in
    ai_crm._ensure_customer_profiles_table and fx_rate_service._ensure_fx_table.

    The raw DDL is a safety net: db.create_all() in main.py creates the table
    from the SQLAlchemy model, but if metadata reflection is delayed (e.g.
    Celery worker boot order beats main.py), this guarantees the table is
    present before any engagement dispatch hits it.
    """
    try:
        from sqlalchemy import text as _sql_text
        from app import app
        with app.app_context():
            db.session.execute(_sql_text("""
                CREATE TABLE IF NOT EXISTS in_app_banners (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL
                        REFERENCES "user"(id) ON DELETE CASCADE,
                    rule_key VARCHAR(64) NOT NULL,
                    headline VARCHAR(255) NOT NULL,
                    body TEXT NOT NULL,
                    cta_text VARCHAR(64) NOT NULL,
                    cta_url VARCHAR(512) NOT NULL,
                    dismissed_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.session.execute(_sql_text("""
                CREATE INDEX IF NOT EXISTS ix_in_app_banners_user_id
                    ON in_app_banners (user_id)
            """))
            db.session.execute(_sql_text("""
                CREATE INDEX IF NOT EXISTS ix_in_app_banners_user_dismissed_created
                    ON in_app_banners (user_id, dismissed_at, created_at DESC)
            """))
            db.session.commit()
    except Exception as e:
        log.warning("Could not ensure in_app_banners table: %s", e)
        try:
            db.session.rollback()
        except Exception:
            pass


# Run schema setup on import (mirrors ai_crm + fx_rate_service patterns).
_ensure_in_app_banners_table()


__all__ = ["InAppBanner", "_ensure_in_app_banners_table"]
