"""
Event Spine — the irreducible foundation for AI-run FIESTA analytics.

Council #2 (Opus + Sonnet + Gemini + GPT, 2026-05-17) unanimously sequenced this
first because every Wave 2+ component (AI CRM, leading-indicator dashboards,
cross-sell engine, nudge scheduler, ad-spend optimisation) needs a uniform
event stream to read from.

Design intent:
  * ONE table — `events` — append-only, never updated, never deleted by app code.
  * Best-effort write semantics: emit() must NEVER raise. Analytics is
    observational, not transactional. A failed insert is a logged warning,
    not a 500 to the user.
  * Indexed for the two access patterns the dashboards need:
      (event_type, created_at DESC)  -> "show me last N events of type X"
      (user_id, created_at DESC)     -> "per-user timeline aggregation"
  * Nullable user_id so we can log unauth signals (ad clicks, anonymous form
    starts) once those surfaces exist.

Schema-additive pattern: the table is created two ways for belt-and-braces:
  (a) raw CREATE TABLE IF NOT EXISTS in app._ensure_additive_schema() — runs at
      every entry point (gunicorn, wsgi, celery) at boot.
  (b) db.create_all() in main.py picks up this model via SQLAlchemy metadata
      when this module is imported.

The ORM model below is what application code uses for queries; the raw DDL
guarantees the table is present even if SQLAlchemy metadata reflection is
delayed.
"""
from datetime import datetime

from app import db


class Event(db.Model):
    """Append-only analytics event.

    Wave 1 (EVENT SPINE, 2026-05-17). Council #2 unanimous: every product
    signal — signup, persona set, bank upload, remittance added, IRD-ready,
    checkout, support contact, nudge, idea — funnels here before any
    downstream consumer (dashboard, AI CRM, scheduler) reads it.
    """
    __tablename__ = "events"

    id = db.Column(db.Integer, primary_key=True)

    # The event type — kept as a free-form VARCHAR (not an enum) so new event
    # types can be added in code without a migration. Use STANDARD_EVENTS in
    # events.py as the canonical list; ad-hoc strings are permitted but
    # should be promoted to the constant once the pattern stabilises.
    event_type = db.Column(db.String(64), nullable=False, index=True)

    # ON DELETE SET NULL — when a user account is purged (GDPR, test cleanup),
    # we want to keep the aggregate event history intact for analytics but
    # disconnect the row from any PII. Same logic for organization.
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organization.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Free-form JSON payload. Keep it small — these rows accumulate fast.
    # Convention: keys are snake_case; values are JSON-serialisable scalars
    # (str/int/float/bool/None). No nested binary, no large blobs.
    payload = db.Column(db.JSON, nullable=True)

    # Where the event was emitted from. Convention:
    #   route:<blueprint>.<endpoint>   e.g. 'route:remittance.new'
    #   webhook:<provider>             e.g. 'webhook:stripe'
    #   cron:<job_name>                e.g. 'cron:cross_sell'
    #   ai:<agent>                     e.g. 'ai:crm_classifier'
    source = db.Column(db.String(32), nullable=True)

    # Flask session id (if request context available) — lets us correlate a
    # signup -> persona_set -> first remittance funnel even across the
    # authentication boundary.
    session_id = db.Column(db.String(64), nullable=True)

    # Best-effort IP + UA capture from the request context. Useful for ad
    # attribution + anomaly detection. Falls back to NULL outside request scope.
    ip_address = db.Column(db.String(50), nullable=True)
    user_agent = db.Column(db.Text, nullable=True)

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )

    # Composite indexes — designed for the two leading-indicator query
    # patterns the Wave 2 dashboards run:
    #   "last N events of type X, newest first"   -> (event_type, created_at)
    #   "per-user timeline, newest first"         -> (user_id, created_at)
    # postgresql_using='btree' is the default; we declare desc on created_at
    # so the planner can reverse-scan without a sort.
    __table_args__ = (
        db.Index(
            "ix_events_type_created_at",
            "event_type",
            db.text("created_at DESC"),
        ),
        db.Index(
            "ix_events_user_created_at",
            "user_id",
            db.text("created_at DESC"),
        ),
    )

    def __repr__(self):
        return f"<Event {self.id} {self.event_type} user={self.user_id} at={self.created_at}>"
