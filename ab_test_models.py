"""
A/B testing harness — ORM models.

Tier D5 / E6 (2026-05-24). Council intent: enable self-improving conversion
through config-driven experiments. NO code-deploy needed per test — a new
experiment is inserted as a row in `ab_experiment`, and template authors
read it via `{{ ab_variant('experiment_key') }}` (Jinja helper registered
in `ab_test.register_template_helper`).

Two tables:
  * ab_experiment — declarative config for one experiment. The `variants`
    JSON column is the canonical list of variant names (first entry is
    treated as the implicit control in analytics SQL, but assignment is
    purely hash-based, NOT control-biased). `weights` is optional and
    reserved for future weighted assignment — v1 uses uniform buckets.
  * ab_assignment — sticky assignment per (experiment, visitor). Visitor
    identity is user_id when authenticated, else session_anon_id cookie.
    The UNIQUE constraint pins this; the deterministic hash in
    `ab_test.get_variant` makes the same visitor land in the same bucket
    even on cache miss, so first-write race conditions are silently
    recoverable (next read finds the row that won the race).

Scope:
  * NO admin UI (insert experiments via SQL — see _tier_d5_ab/README.md).
  * NO Bayesian analysis (CEO eyeballs lift from /admin/analytics).
  * NO mutually-exclusive experiment groups (each experiment is
    independent; visitor lands in one bucket per experiment).

The table is created two ways for belt-and-braces parity with event_models:
  (a) raw CREATE TABLE IF NOT EXISTS in migrations/add_ab_tests.py.
  (b) db.create_all() in main.py picks up these models via SQLAlchemy
      metadata when this module is imported.
"""
from datetime import datetime

from app import db


class ABExperiment(db.Model):
    """Declarative config for one A/B experiment.

    A new experiment is created by inserting a row. The application reads
    `status='active'` rows only — once you set `status='concluded'` the
    helper falls back to 'control' for all visitors (sticky assignments
    remain in ab_assignment for audit but stop influencing render).
    """
    __tablename__ = "ab_experiment"

    id = db.Column(db.Integer, primary_key=True)

    # Stable string key used in templates: {{ ab_variant('s0_hero_color') }}
    # Keep snake_case + screen-prefix convention (e.g. 's0_hero_color',
    # 's12_cta_label') so analytics dashboards can group by screen.
    key = db.Column(db.String(64), unique=True, nullable=False)

    # Human-readable name shown in admin views (none yet — reserved).
    name = db.Column(db.String(200), nullable=False)

    # Free-form description: hypothesis, success metric, etc.
    description = db.Column(db.Text, nullable=True)

    # Canonical list of variant labels. First entry is convention-only
    # control; assignment is uniform hash-bucket across the full list.
    # Example: ['control', 'green', 'orange']
    variants = db.Column(db.JSON, nullable=False)

    # Optional weights for future non-uniform assignment. v1 ignores this
    # and uses uniform buckets — kept so analytics + admin UI (when built)
    # can read the intended distribution without a migration.
    weights = db.Column(db.JSON, nullable=True)

    # Lifecycle: 'active' (helper picks variants) / 'paused' (helper
    # returns 'control', existing assignments stay) / 'concluded'
    # (winner_variant should be populated, helper returns 'control').
    status = db.Column(db.String(16), default="active", nullable=False)

    # Free-form metric name the analytics layer aggregates on, e.g.
    # 'payment_completed', 'calculator_used', 'tax_bill_view'. The events
    # table holds the actual conversions; this just labels what the
    # primary north star is for THIS experiment.
    primary_metric = db.Column(db.String(64), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    started_at = db.Column(db.DateTime, nullable=True)
    concluded_at = db.Column(db.DateTime, nullable=True)
    winner_variant = db.Column(db.String(64), nullable=True)

    def __repr__(self):
        return f"<ABExperiment {self.key} status={self.status}>"


class ABAssignment(db.Model):
    """Sticky assignment of one visitor to one variant of one experiment.

    Visitor identity rules:
      * Authenticated  -> user_id populated, session_anon_id NULL.
      * Anonymous      -> user_id NULL, session_anon_id populated.

    UNIQUE (experiment_key, user_id, session_anon_id) — the partial-tuple
    composite means each visitor has at most one row per experiment.
    Race-condition path: if two requests for the same anon visitor hit
    `get_variant` concurrently, the loser's INSERT raises IntegrityError;
    we swallow it (`db.session.rollback()`) and the next read returns the
    winner's row. Because both requests hash to the SAME variant
    (deterministic), the silently-dropped row would have stored the same
    value — no observable inconsistency.
    """
    __tablename__ = "ab_assignment"

    id = db.Column(db.Integer, primary_key=True)

    # Denormalised — we don't FK to ab_experiment.id so the analytics
    # layer can read the assignment even if an experiment row is later
    # deleted (shouldn't happen, but defensive).
    experiment_key = db.Column(db.String(64), nullable=False, index=True)

    # ON DELETE SET NULL — when a user account is purged we want to keep
    # the experiment cohort sizes intact for retrospective analysis but
    # disconnect the row from any PII. Same logic as Event.user_id.
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Pre-auth visitor — pairs with the session_anon_id cookie that the
    # analytics_beacon_routes module already sets on first request. For
    # authenticated users this stays NULL (we key on user_id instead).
    session_anon_id = db.Column(db.String(64), nullable=True, index=True)

    variant = db.Column(db.String(64), nullable=False)

    assigned_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False,
    )

    __table_args__ = (
        db.UniqueConstraint(
            "experiment_key", "user_id", "session_anon_id",
            name="uq_ab_assignment",
        ),
    )

    def __repr__(self):
        identity = (
            f"u{self.user_id}" if self.user_id else f"anon:{self.session_anon_id}"
        )
        return (
            f"<ABAssignment {self.experiment_key} -> {self.variant} "
            f"({identity})>"
        )
