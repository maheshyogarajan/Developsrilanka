"""
fiesta.markov.models — Layer 2 Markov tracker schema.

Wave Markov-L2 (post-launch Day-1 task #11, 2026-05-27).

Schema design notes
-------------------
* Append-only. No UPDATE/DELETE in app code (matches the EVENT SPINE
  contract). A purge job may later trim rows older than N months; that's
  the only legitimate writer beyond the state-writer module.
* ``user_id`` is INDEXED + carries a regular FK with no ON DELETE — when a
  test/GDPR purge removes the User, the FK enforces explicit cleanup of
  the history. We intentionally chose NOT to ``SET NULL`` because Layer 2
  is per-user time-series; an orphaned row has no analytical value.
  (Contrast: Event uses SET NULL because aggregate event volume retains
  value even after PII purge.)
* ``state_code`` is VARCHAR(8) — accommodates S00-S37 (v1+v2 catalogue)
  and leaves headroom for future S100+ codes without a migration.
* ``trigger_event`` is VARCHAR(64) — matches event_models.Event.event_type
  cap so any STANDARD_EVENTS slug fits.
* ``previous_state_code`` is nullable: the FIRST row for a user (signup
  or backfill) has no antecedent in the history. All subsequent rows
  inherit the previous state from the most recent existing row.
* ``metadata_json`` is free-form JSON. Keep small (<2 KB) — same
  convention as Event.payload.
* ``__table_args__`` ix_user_state_history_user_created is the workhorse
  index for dwell-time queries:  user_id ASC, created_at ASC.

DDL is also belt-and-braces emitted from app._ensure_additive_schema()
so every entry point (gunicorn, wsgi, celery, pytest) materialises the
table even when SQLAlchemy metadata reflection is delayed.
"""
from __future__ import annotations

from datetime import datetime

from app import db


class UserStateHistory(db.Model):
    """Append-only Markov-state transition log.

    One row per (user, S-state) transition. Consecutive duplicates are
    suppressed by ``record_state_transition`` so a no-op re-entry doesn't
    pollute the time-series.
    """

    __tablename__ = "user_state_history"

    id = db.Column(db.BigInteger, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
        index=True,
    )

    # Current S-state the user has just moved INTO.
    state_code = db.Column(db.String(8), nullable=False)

    # Human-readable label for the state (snapshotted at write time so
    # historical rows survive label edits in fiesta_states_routes).
    state_label = db.Column(db.String(64), nullable=False)

    # The state the user was in immediately before this transition.
    # NULL only for the FIRST row of a user (signup row OR backfill row).
    previous_state_code = db.Column(db.String(8), nullable=True)

    # Event slug (matches STANDARD_EVENTS naming) OR a sentinel string
    # such as 'backfill' (for the migration seeder) or 'submission.<status>'
    # (for in-route submission status transitions that aren't event-driven).
    trigger_event = db.Column(db.String(64), nullable=False)

    # Free-form JSON payload — captured from the emitting event's payload
    # so downstream analytics can answer "what was the income source type
    # when the user crossed into S04?". Keep small.
    metadata_json = db.Column(db.JSON, nullable=True)

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )

    __table_args__ = (
        db.Index(
            "ix_user_state_history_user_created",
            "user_id",
            "created_at",
        ),
    )

    def __repr__(self):  # pragma: no cover - cosmetic
        return (
            f"<UserStateHistory {self.id} u={self.user_id} "
            f"{self.previous_state_code or '-'}->{self.state_code} "
            f"via={self.trigger_event} at={self.created_at}>"
        )


__all__ = ["UserStateHistory"]
