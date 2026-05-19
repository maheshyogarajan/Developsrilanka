"""
Lanka.tax Cross-Sell Models — Wave 3.3 (2026-05-18).

Two append-mostly tables to drive the #1 leading-indicator metric per council #2:
"Lanka.tax Cross-Sell Take Rate > 20% target = 1,120 paid users / $224k ARR floor".

  * LankataxCohort  — saved cohort selector + snapshot of matching user IDs.
                      One row per cohort name. Refreshed by the daily Celery
                      beat; the snapshot is what run_campaign iterates so a
                      mid-day re-query never moves the goalposts on an
                      in-flight campaign.
  * LankataxOutreach — one row per (user, campaign_key, send). Append-only
                       from CEO-OS's perspective: opened_at / clicked_at /
                       converted_at are UPDATED in place on the matching row,
                       but no row is ever deleted. This is the audit trail
                       the take-rate computer reads.

Design intent mirrors ai_crm.py + event_models.py:
  - Belt-and-braces table creation: SQLAlchemy model + raw DDL _ensure_*().
    Runs at module import so Celery worker boot order doesn't matter.
  - ON DELETE for PII cleanup: user_id is SET NULL (we want the aggregate
    take-rate history intact even after a user is purged).
  - Composite indexes for the two hot query patterns:
      cooldown check : (user_id, sent_at DESC)
      take-rate SQL  : (campaign_key, converted_at)
"""
import logging
from datetime import datetime

from app import db

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# LankataxCohort
# --------------------------------------------------------------------------- #

class LankataxCohort(db.Model):
    """A named cohort selector + snapshot of matching user IDs.

    `sql_query` is the selector expression used to build the cohort (raw SQL,
    parameterless — see lankatax_crosssell.py for the canonical 3 cohorts).
    `target_user_ids` is the snapshot taken at the last build_cohort() call;
    `members_count` is derived from len(target_user_ids) for cheap reads.

    One row per cohort name (unique). Updated in-place by build_cohort().
    """
    __tablename__ = "lankatax_cohorts"

    id = db.Column(db.Integer, primary_key=True)

    # Cohort name — short slug, unique. e.g. 'lankatax_existing_clients'.
    name = db.Column(db.String(64), nullable=False, unique=True, index=True)

    # Human-readable description (what does this cohort represent?).
    description = db.Column(db.Text, nullable=True)

    # The cohort selector — raw SQL that returns a single `id` column.
    sql_query = db.Column(db.Text, nullable=False)

    # Snapshot of user IDs at the last build_cohort() call. JSON list of ints.
    # We snapshot (rather than re-query at run_campaign time) so a campaign
    # that takes hours to dispatch hits the same audience the cohort named.
    target_user_ids = db.Column(db.JSON, nullable=False, default=list)

    # Cached count — derived from len(target_user_ids), persisted for fast
    # listing on the admin/cohorts page.
    members_count = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_run_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return (
            f"<LankataxCohort {self.id} {self.name!r} "
            f"members={self.members_count}>"
        )


# --------------------------------------------------------------------------- #
# LankataxOutreach
# --------------------------------------------------------------------------- #

class LankataxOutreach(db.Model):
    """One row per (user, campaign_key, send).

    The audit trail for cross-sell campaigns. `sent_at` is set at insert;
    opened_at / clicked_at / converted_at are UPDATED in place when the
    matching signal fires (onboarding link hit, conversion event observed).

    Cooldown logic: run_campaign() refuses to insert a new row for the same
    (user_id, campaign_key) if any existing row has `sent_at >= now - 14d`.

    Take-rate SQL: aggregate over (campaign_key, converted_at) — the second
    composite index makes this a cheap range scan.
    """
    __tablename__ = "lankatax_outreach"

    id = db.Column(db.Integer, primary_key=True)

    # FK to user. SET NULL on delete so the aggregate history survives a
    # GDPR / test purge.
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # FK to cohort. SET NULL on cohort delete (we'd lose attribution, but the
    # send/open/click/convert signal is still valuable).
    cohort_id = db.Column(
        db.Integer,
        db.ForeignKey("lankatax_cohorts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Campaign identifier. Convention: snake_case, matches the template
    # filename stem (e.g. 'existing_clients_v1' → template
    # 'lankatax_email_templates/existing_clients_v1_a.html').
    campaign_key = db.Column(db.String(64), nullable=False, index=True)

    # Channel: 'email' (SendGrid) or 'in_app' (engagement_engine banner —
    # stubbed until that sibling ships).
    channel = db.Column(db.String(16), nullable=False, default="email")

    # A/B variant. 'a' / 'b'. Nullable for campaigns without A/B.
    variant = db.Column(db.String(8), nullable=True)

    sent_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    opened_at = db.Column(db.DateTime, nullable=True)
    clicked_at = db.Column(db.DateTime, nullable=True)

    # Set when the user converts to paid (subscription_status flips, or
    # checkout_completed event observed within attribution window).
    converted_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        # Cooldown lookup: "last send to user for ANY campaign". The
        # DESC on sent_at lets the planner reverse-scan without a sort.
        db.Index(
            "ix_lankatax_outreach_user_sent",
            "user_id",
            db.text("sent_at DESC"),
        ),
        # Take-rate SQL: "of N sends for campaign X, how many converted".
        db.Index(
            "ix_lankatax_outreach_campaign_converted",
            "campaign_key",
            "converted_at",
        ),
    )

    def __repr__(self):
        return (
            f"<LankataxOutreach {self.id} user={self.user_id} "
            f"campaign={self.campaign_key!r} channel={self.channel} "
            f"sent_at={self.sent_at}>"
        )


# --------------------------------------------------------------------------- #
# Idempotent table creation (mirrors ai_crm._ensure_customer_profiles_table)
# --------------------------------------------------------------------------- #

def _ensure_lankatax_tables():
    """Idempotent CREATE TABLE + CREATE INDEX. Belt-and-braces alongside
    db.create_all(): guarantees the tables exist even if Celery worker boots
    before main.py has run.
    """
    try:
        from sqlalchemy import text as _sql_text
        from app import app
        with app.app_context():
            db.session.execute(_sql_text("""
                CREATE TABLE IF NOT EXISTS lankatax_cohorts (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(64) NOT NULL UNIQUE,
                    description TEXT,
                    sql_query TEXT NOT NULL,
                    target_user_ids JSON NOT NULL DEFAULT '[]'::json,
                    members_count INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_run_at TIMESTAMP
                )
            """))
            db.session.execute(_sql_text("""
                CREATE INDEX IF NOT EXISTS ix_lankatax_cohorts_name
                    ON lankatax_cohorts (name)
            """))
            db.session.execute(_sql_text("""
                CREATE TABLE IF NOT EXISTS lankatax_outreach (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES "user"(id) ON DELETE SET NULL,
                    cohort_id INTEGER
                        REFERENCES lankatax_cohorts(id) ON DELETE SET NULL,
                    campaign_key VARCHAR(64) NOT NULL,
                    channel VARCHAR(16) NOT NULL DEFAULT 'email',
                    variant VARCHAR(8),
                    sent_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    opened_at TIMESTAMP,
                    clicked_at TIMESTAMP,
                    converted_at TIMESTAMP
                )
            """))
            db.session.execute(_sql_text("""
                CREATE INDEX IF NOT EXISTS ix_lankatax_outreach_user_sent
                    ON lankatax_outreach (user_id, sent_at DESC)
            """))
            db.session.execute(_sql_text("""
                CREATE INDEX IF NOT EXISTS ix_lankatax_outreach_campaign_converted
                    ON lankatax_outreach (campaign_key, converted_at)
            """))
            db.session.execute(_sql_text("""
                CREATE INDEX IF NOT EXISTS ix_lankatax_outreach_campaign_key
                    ON lankatax_outreach (campaign_key)
            """))
            db.session.execute(_sql_text("""
                CREATE INDEX IF NOT EXISTS ix_lankatax_outreach_user_id
                    ON lankatax_outreach (user_id)
            """))
            db.session.commit()
    except Exception as e:
        log.warning("Could not ensure lankatax tables: %s", e)
        try:
            db.session.rollback()
        except Exception:
            pass


# Run schema setup on import (mirrors fx_rate_service / ai_crm pattern).
_ensure_lankatax_tables()


__all__ = [
    "LankataxCohort",
    "LankataxOutreach",
    "_ensure_lankatax_tables",
]
