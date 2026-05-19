"""
Gemini cost log — per-call AI spend tracker (Wave 2.4 Ops Sentinel, 2026-05-17).

Council #2 sequenced this with the Ops Sentinel because every Gemini-touching
surface in Wave 2+ (CRM classifier, AI Support, CRM recompute, bank-statement
import) needs to flow its spend through one ledger. Without it, "did we just
burn $50 on a flaky retry loop?" is unanswerable in real time and the
cost-anomaly Sentinel check (`check_gemini_cost_24h`) has nothing to read.

Design intent:
  * ONE table — `gemini_cost_log` — append-only, never updated.
  * Best-effort write semantics: log_gemini_cost() (in ops_sentinel.py) MUST
    NEVER raise. AI spend tracking is observational, not transactional.
  * Indexed for the two access patterns the Sentinel + admin dashboards need:
      (user_id, created_at DESC)  -> "per-user spend timeline"
      created_at                  -> "last 24h spend totals"
  * Nullable user_id so we can log system-internal calls (cron recompute, ad-hoc
    backfills) once those surfaces exist.

Schema-additive pattern (mirrors event_models.py + fx_rate_service.py):
  (a) raw CREATE TABLE IF NOT EXISTS in _ensure_gemini_cost_log_table() — runs
      at module import so the table is present whenever any caller imports
      log_gemini_cost.
  (b) db.create_all() in main.py picks up this model via SQLAlchemy metadata
      when this module is imported.

The ORM model below is what application code uses for queries; the raw DDL
guarantees the table is present even if SQLAlchemy metadata reflection is
delayed (gunicorn vs celery boot order, etc).
"""
import logging
from datetime import datetime

from app import db

log = logging.getLogger(__name__)


class GeminiCostLog(db.Model):
    """Append-only per-call Gemini cost log.

    Wave 2.4 (OPS SENTINEL, 2026-05-17). Every Gemini API call from any FIESTA
    surface (remittance_import, ai_support, crm_recompute, ...) writes one row
    here via ops_sentinel.log_gemini_cost(...). The Sentinel then reads this
    table every 5 minutes to detect cost anomalies.
    """
    __tablename__ = "gemini_cost_log"

    id = db.Column(db.Integer, primary_key=True)

    # ON DELETE SET NULL — when a user account is purged (GDPR, test cleanup),
    # we want to keep the aggregate spend history intact for finance reporting
    # but disconnect the row from any PII. Same logic as event_models.Event.
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Free-form model name. Examples observed in production:
    #   gemini-2.5-flash, gemini-2.5-pro, gemini-1.5-flash, gemini-pro
    # Kept as VARCHAR (not enum) so new model SKUs don't require migrations.
    model_name = db.Column(db.String(64), nullable=False)

    # Raw token counts as returned by the Gemini SDK usage_metadata. Integer is
    # fine — Gemini caps requests well below 2^31.
    prompt_tokens = db.Column(db.Integer, nullable=False, default=0)
    completion_tokens = db.Column(db.Integer, nullable=False, default=0)

    # USD-denominated estimate. NUMERIC(10,6) gives us 4 digits of dollars plus
    # 6 of decimals — accurate to a millionth of a dollar, plenty for per-call
    # accounting (typical call is ~$0.0001-$0.01). NOT a hard accounting field;
    # for the real bill, reconcile against Google Cloud invoices.
    estimated_cost_usd = db.Column(db.Numeric(10, 6), nullable=False, default=0)

    # Where the call came from. Convention (mirrors events.source):
    #   remittance_import     -> bank statement → remittance extraction
    #   ai_support            -> Wave 3.2 AI support agent
    #   crm_recompute         -> Wave 2.3 CRM classification refresh
    #   manual                -> ad-hoc CLI / admin tool
    source = db.Column(db.String(64), nullable=True)

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )

    # Composite index — powers the Sentinel's per-user-spend lookup AND the
    # admin /internal/ops/cost-summary "top 10 users by spend" query.
    __table_args__ = (
        db.Index(
            "ix_gemini_cost_user_created_at",
            "user_id",
            db.text("created_at DESC"),
        ),
    )

    def __repr__(self):
        return (
            f"<GeminiCostLog {self.id} {self.model_name} user={self.user_id} "
            f"cost=${self.estimated_cost_usd} at={self.created_at}>"
        )


def _ensure_gemini_cost_log_table():
    """Idempotent. Runs on import; cheap.

    Belt-and-braces: SQLAlchemy's db.create_all() picks up this model via the
    metadata registry when the module is imported, BUT in environments where
    metadata reflection is delayed (e.g. a Celery worker that imports
    ops_sentinel before app.py has finished setting up create_all wiring), the
    raw DDL below guarantees the table exists by the time the first
    log_gemini_cost() call lands.

    Mirrors the pattern in fx_rate_service._ensure_fx_table().
    """
    try:
        from sqlalchemy import text as _sql_text
        from app import app
        with app.app_context():
            db.session.execute(_sql_text("""
                CREATE TABLE IF NOT EXISTS gemini_cost_log (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NULL REFERENCES "user"(id) ON DELETE SET NULL,
                    model_name VARCHAR(64) NOT NULL,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_usd NUMERIC(10, 6) NOT NULL DEFAULT 0,
                    source VARCHAR(64) NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.session.execute(_sql_text("""
                CREATE INDEX IF NOT EXISTS ix_gemini_cost_log_created_at
                    ON gemini_cost_log (created_at)
            """))
            db.session.execute(_sql_text("""
                CREATE INDEX IF NOT EXISTS ix_gemini_cost_log_user_id
                    ON gemini_cost_log (user_id)
            """))
            db.session.execute(_sql_text("""
                CREATE INDEX IF NOT EXISTS ix_gemini_cost_user_created_at
                    ON gemini_cost_log (user_id, created_at DESC)
            """))
            db.session.commit()
    except Exception as e:
        # Best-effort: log and move on. The ORM-side create_all will pick up
        # the table at app boot; this DDL is the defensive belt-and-braces path.
        log.warning("Could not ensure gemini_cost_log table: %s", e)
        try:
            db.session.rollback()
        except Exception:
            pass


# Run the idempotent DDL at module import. Cheap; runs once per process.
# Guarded behind a try/except inside _ensure_gemini_cost_log_table itself, so
# even a totally-broken DB at import time won't kill the module load.
_ensure_gemini_cost_log_table()


__all__ = ["GeminiCostLog", "_ensure_gemini_cost_log_table"]
