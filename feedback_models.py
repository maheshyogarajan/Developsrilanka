"""
Feedback model — Sprint 4 Tier D4 (2026-05-24).

In-app feedback widget closes the continuous-improvement loop: any user
(authenticated or anonymous) can submit a category-tagged note from a
floating button on every page. Submissions land in this `feedback` table
where the CEO can query them directly.

Why a dedicated table (not the events spine)?

  * Feedback is read-heavy in a different shape than the funnel events.
    The CEO reads it linearly by created_at; the events table is queried
    by (event_type, created_at) and (user_id, created_at).
  * The body field is free text up to a few KB and may contain anything
    the user types — keeping it out of the analytics spine avoids
    polluting funnel aggregates with prose.
  * Different retention policy candidate: events may be downsampled or
    aggregated; feedback rows are kept verbatim as long as the user
    consents.

Schema-additive belt-and-braces:
  (a) ORM model below (used by application code for queries).
  (b) Raw `CREATE TABLE IF NOT EXISTS feedback (...)` runs at every entry
      point via app._ensure_additive_schema() — see app.py.
  (c) The migration `migrations/add_feedback_table.py` provides an
      explicit idempotent upgrade path that ops can run on demand.

Identity:
  * `user_id` is a nullable FK so anonymous submissions persist (ON DELETE
    SET NULL preserves the row when an account is purged).
  * `session_anon_id` mirrors the analytics-beacon cookie so anonymous
    feedback can be correlated with the same browser's event funnel.
"""
from datetime import datetime

from app import db


# --------------------------------------------------------------------------- #
# Allowed categories — the contract between the JS widget and this table.
# Mirrored as a CHECK constraint at the DB layer (see migration + additive
# schema) so a malformed client can't pollute the dropdown analytics later.
# --------------------------------------------------------------------------- #
FEEDBACK_CATEGORIES = frozenset({
    "bug",          # something is broken
    "feature",      # I want this to do X
    "confusion",    # I don't understand what's happening
    "praise",       # this worked / I liked it
    "other",        # catch-all
})


class Feedback(db.Model):
    """User-submitted feedback note.

    Tier D4 (2026-05-24). Floating-button widget on every page -> POST
    /api/feedback -> this row. CEO queries via
    `SELECT * FROM feedback ORDER BY created_at DESC LIMIT 50;`.
    """
    __tablename__ = "feedback"

    id = db.Column(db.Integer, primary_key=True)

    # Nullable FK: anonymous users can submit feedback too. ON DELETE SET NULL
    # so we keep the row's aggregate value after an account purge.
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # The session_anon_id cookie value (mirrors analytics_beacon_routes).
    # Lets us link anonymous feedback to that visitor's funnel events.
    session_anon_id = db.Column(db.String(64), nullable=True, index=True)

    # One of FEEDBACK_CATEGORIES. Stored as a short VARCHAR rather than a
    # native enum so adding a new category later is a one-line code change
    # plus a CHECK-constraint update, not a full enum migration.
    category = db.Column(db.String(32), nullable=False)

    # The user's actual message. Free text. Capped at the route layer to
    # ~4 KB so we don't accept arbitrary blobs.
    body = db.Column(db.Text, nullable=False)

    # The page the user was on when they submitted (request.referrer or the
    # client-supplied `url` from the widget). Useful so the CEO knows the
    # context without asking "where were you?".
    url_at_submit = db.Column(db.String(512), nullable=True)

    # Browser UA — useful for "is this a mobile-only bug?" triage.
    user_agent = db.Column(db.Text, nullable=True)

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )

    def __repr__(self):
        return (
            f"<Feedback {self.id} {self.category} user={self.user_id} "
            f"at={self.created_at}>"
        )


__all__ = ["Feedback", "FEEDBACK_CATEGORIES"]
