"""
AI-Org Data Substrate — Subagent A (2026-05-18).

The FIRST primitive in the federated AI-org economy. Every future AI-org
transaction (proposals, contracts, deliverables, reputation events, payments,
attribution claims) flows through these 8 tables.

Council synthesis: G:/My Drive/CEO OS/working files/_cockpit_fiesta/VISIONARY_ECONOMY_COUNCIL_SYNTHESIS.md

Design intent:
  * Thin core schema (council mitigation #4) — domain-specific shapes live in
    typed `artifact_payload` JSON blobs referenced by content hash. Avoids the
    McLarens-vs-Lanka.tax data-schema-rigidity bottleneck Gemini flagged.
  * Append-only `reputation_event` ledger (council #5 mitigation against score
    manipulation): enforced via Postgres RULE (DB-level) + SQLAlchemy
    before_update/before_delete event listeners (ORM-level). Belt-and-braces.
  * 3-axis status score (Economic 0.5 / Human Impact 0.3 / AI Reliability 0.2)
    — axis stored on each reputation event so the nightly score engine
    (Subagent C) can read in one indexed scan: (ai_org_id, axis, occurred_at DESC).
  * S3 artifact pointer pattern (artifact_s3_key) for payloads too large for
    JSON columns — same pattern as remittance_models.RemittanceEntry.source_doc_s3_key.
  * Idempotent CREATE TABLE IF NOT EXISTS DDL at module import — same pattern as
    event_models.py (table created via app._ensure_additive_schema) and
    ai_crm._ensure_customer_profiles_table.

Schema-additive pattern:
  (a) Raw `CREATE TABLE IF NOT EXISTS` in `_ensure_ai_org_tables()` runs at
      module import. Safe to call multiple times (guarded by IF NOT EXISTS).
  (b) `db.create_all()` in main.py picks up these models via SQLAlchemy metadata
      when this module is imported (also defensive).

Subagent B (Attribution + Event Ledger) reads `attribution_ledger`.
Subagent C (Score Engine) reads `reputation_event` indexed by
(ai_org_id, axis, occurred_at DESC).
"""
import logging
from datetime import datetime

from sqlalchemy import event as sa_event
from sqlalchemy.exc import InvalidRequestError

from app import db

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 1. ai_org — the orgs themselves
# --------------------------------------------------------------------------- #

class AIOrg(db.Model):
    """An AI organisation. Initially: acquisition_studio, delivery_ops_command,
    compliance_brigade. Later: many.
    """
    __tablename__ = "ai_org"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    purpose = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(16), nullable=False, default="active")

    # Allows org-within-org structures (sub-orgs reporting to parent orgs).
    parent_org_id = db.Column(
        db.Integer,
        db.ForeignKey("ai_org.id", ondelete="SET NULL"),
        nullable=True,
    )

    # The 0-100 composite computed by Subagent C nightly.
    # Default 0 (cold start; no events yet).
    status_score = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    status_band = db.Column(db.String(2), nullable=False, default="C")

    # Per-axis scores. Read in one indexed scan from reputation_event per axis,
    # composited into status_score by Subagent C.
    economic_axis = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    human_impact_axis = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    ai_reliability_axis = db.Column(db.Numeric(5, 2), nullable=False, default=0)

    last_score_computed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<AIOrg {self.slug} score={self.status_score} band={self.status_band}>"


# --------------------------------------------------------------------------- #
# 2. ai_org_role — named roles inside each org
# --------------------------------------------------------------------------- #

class AIOrgRole(db.Model):
    """A named role inside an AI org. Council named 5 per org:
    Acquisition Studio: Channel Strategist, Content Operator, Outreach Closer,
                        CAC Analyst, Embedded Red-Team.
    Delivery Ops Command: Workflow Orchestrator, Queue Manager, SLA Monitor,
                          Quality Reviewer, Embedded Red-Team.
    Compliance Brigade: Policy Interpreter, Filing Validator, Audit Analyst,
                        Exception Handler, Embedded Red-Team.
    """
    __tablename__ = "ai_org_role"

    id = db.Column(db.Integer, primary_key=True)
    ai_org_id = db.Column(
        db.Integer,
        db.ForeignKey("ai_org.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_slug = db.Column(db.String(64), nullable=False)
    role_name = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)

    # Council mitigation #6: Red-Team must have skin in the game.
    # This flag lets the score engine + payment helper route bounties.
    is_red_team = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("ai_org_id", "role_slug", name="uq_ai_org_role_slug"),
    )

    def __repr__(self):
        return f"<AIOrgRole org={self.ai_org_id} {self.role_slug}>"


# --------------------------------------------------------------------------- #
# 3. proposal — when one org bids for a contract
# --------------------------------------------------------------------------- #

class Proposal(db.Model):
    """A bid for a contract — from an AI org to another AI org (or directly to
    FIESTA/Lanka.tax). Lookup pattern: opportunity_slug + status + submitted_at DESC
    powers the public-competition view (which orgs bid for opportunity X).
    """
    __tablename__ = "proposal"

    id = db.Column(db.Integer, primary_key=True)
    proposer_org_id = db.Column(
        db.Integer,
        db.ForeignKey("ai_org.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # buyer_kind is polymorphic — buyer_ref_id is opaque except when
    # buyer_kind='ai_org', in which case it FKs ai_org.id (enforced at
    # app-level by ai_org_substrate helpers, not at DB-level — keeps the
    # polymorphism clean).
    buyer_kind = db.Column(db.String(16), nullable=False)
    buyer_ref_id = db.Column(db.Integer, nullable=True)

    opportunity_slug = db.Column(db.String(128), nullable=False, index=True)
    artifact_kind = db.Column(db.String(32), nullable=False)
    # Domain-specific payload (council mitigation #4: thin core schema).
    artifact_payload = db.Column(db.JSON, nullable=True)
    # Content-addressable hash for audit + dedup.
    artifact_sha256 = db.Column(db.String(64), nullable=True)
    # S3 pointer when payload too large for JSON column.
    artifact_s3_key = db.Column(db.String(512), nullable=True)

    quoted_price_lkr = db.Column(db.Numeric(18, 2), nullable=True)
    quoted_eta_days = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(16), nullable=False, default="submitted")

    submitted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    decided_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.Index(
            "ix_proposal_opportunity_status_submitted",
            "opportunity_slug",
            "status",
            db.text("submitted_at DESC"),
        ),
    )

    def __repr__(self):
        return f"<Proposal {self.id} org={self.proposer_org_id} {self.opportunity_slug} {self.status}>"


# --------------------------------------------------------------------------- #
# 4. contract — accepted proposal
# --------------------------------------------------------------------------- #

class Contract(db.Model):
    """An accepted proposal becomes a contract. terms_payload may supersede
    proposal.artifact_payload (negotiated terms).
    """
    __tablename__ = "contract"

    id = db.Column(db.Integer, primary_key=True)
    proposal_id = db.Column(
        db.Integer,
        db.ForeignKey("proposal.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Denormalised for query speed — proposer_org_id is the canonical
    # "who did the work" key for score recompute lookups.
    proposer_org_id = db.Column(
        db.Integer,
        db.ForeignKey("ai_org.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    buyer_kind = db.Column(db.String(16), nullable=False)
    buyer_ref_id = db.Column(db.Integer, nullable=True)

    terms_payload = db.Column(db.JSON, nullable=True)
    terms_sha256 = db.Column(db.String(64), nullable=True)
    terms_s3_key = db.Column(db.String(512), nullable=True)

    contracted_price_lkr = db.Column(db.Numeric(18, 2), nullable=False)
    milestone_count = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(16), nullable=False, default="active")

    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.Index(
            "ix_contract_org_status_completed",
            "proposer_org_id",
            "status",
            db.text("completed_at DESC"),
        ),
    )

    def __repr__(self):
        return f"<Contract {self.id} org={self.proposer_org_id} {self.status}>"


# --------------------------------------------------------------------------- #
# 5. deliverable — what got produced under a contract
# --------------------------------------------------------------------------- #

class Deliverable(db.Model):
    """A milestone deliverable under a contract. Red-Team review gate
    (red_team_pass) is the council #6 mitigation — Red-Team must catch
    hallucinations BEFORE the acceptor sees them.
    """
    __tablename__ = "deliverable"

    id = db.Column(db.Integer, primary_key=True)
    contract_id = db.Column(
        db.Integer,
        db.ForeignKey("contract.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalised — same reason as Contract.proposer_org_id.
    proposer_org_id = db.Column(
        db.Integer,
        db.ForeignKey("ai_org.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    milestone_number = db.Column(db.Integer, nullable=False, default=1)

    artifact_kind = db.Column(db.String(32), nullable=False)
    artifact_payload = db.Column(db.JSON, nullable=True)
    artifact_sha256 = db.Column(db.String(64), nullable=True)
    artifact_s3_key = db.Column(db.String(512), nullable=True)

    # NULL = pending acceptor review; TRUE = accepted; FALSE = rejected/rework.
    accepted = db.Column(db.Boolean, nullable=True)
    acceptor_kind = db.Column(db.String(16), nullable=True)
    acceptor_ref_id = db.Column(db.Integer, nullable=True)
    accepted_at = db.Column(db.DateTime, nullable=True)

    # Red-Team gate — NULL = not yet reviewed.
    red_team_pass = db.Column(db.Boolean, nullable=True)
    red_team_reviewer_role_id = db.Column(
        db.Integer,
        db.ForeignKey("ai_org_role.id", ondelete="SET NULL"),
        nullable=True,
    )
    hallucination_flag = db.Column(db.Boolean, nullable=False, default=False)
    quality_score = db.Column(db.Numeric(3, 2), nullable=True)  # 0-1

    delivered_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index("ix_deliverable_contract_milestone", "contract_id", "milestone_number"),
    )

    def __repr__(self):
        return f"<Deliverable {self.id} contract={self.contract_id} m={self.milestone_number}>"


# --------------------------------------------------------------------------- #
# 6. reputation_event — APPEND-ONLY ledger
# --------------------------------------------------------------------------- #

class ReputationEvent(db.Model):
    """APPEND-ONLY ledger. Every status-axis-feeding signal lands here.

    Council #5 mitigation against score gaming: this table is the un-modifiable
    record. The DB enforces it via Postgres RULE (no_update / no_delete) AND
    the ORM enforces it via SQLAlchemy event listeners (raise on update/delete).
    Belt-and-braces — either layer alone would prevent the regression.

    Read pattern (Subagent C nightly score recompute):
        SELECT magnitude, attribution_confidence, occurred_at
          FROM reputation_event
         WHERE ai_org_id = :org AND axis = :axis
         ORDER BY occurred_at DESC;
    The composite index (ai_org_id, axis, occurred_at DESC) makes this
    a single reverse-scan with no sort step.
    """
    __tablename__ = "reputation_event"

    id = db.Column(db.Integer, primary_key=True)
    ai_org_id = db.Column(
        db.Integer,
        db.ForeignKey("ai_org.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Named events from council synthesis. Free-form VARCHAR (not enum) so new
    # event types can be added without a migration; STANDARD_EVENTS in
    # ai_org_substrate.py is the canonical list.
    event_type = db.Column(db.String(64), nullable=False)
    magnitude = db.Column(db.Numeric(18, 4), nullable=False)
    # 'economic' | 'human_impact' | 'ai_reliability'.
    axis = db.Column(db.String(16), nullable=False)

    source_contract_id = db.Column(
        db.Integer,
        db.ForeignKey("contract.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_deliverable_id = db.Column(
        db.Integer,
        db.ForeignKey("deliverable.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Council mitigation #4 — confidence multiplier on score.
    attribution_confidence = db.Column(db.Numeric(3, 2), nullable=False, default=1.0)
    payload = db.Column(db.JSON, nullable=True)

    occurred_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    __table_args__ = (
        db.Index(
            "ix_reputation_event_org_axis_occurred",
            "ai_org_id",
            "axis",
            db.text("occurred_at DESC"),
        ),
    )

    def __repr__(self):
        return (
            f"<ReputationEvent {self.id} org={self.ai_org_id} {self.event_type} "
            f"axis={self.axis} mag={self.magnitude}>"
        )


# SQLAlchemy event listeners — raise on attempted UPDATE/DELETE of any
# ReputationEvent row. Belt-and-braces with the Postgres RULE below.
@sa_event.listens_for(ReputationEvent, "before_update")
def _block_reputation_event_update(mapper, connection, target):
    raise InvalidRequestError(
        f"ReputationEvent is APPEND-ONLY; UPDATE blocked at ORM layer "
        f"(id={target.id}, org={target.ai_org_id}). Council #5 mitigation."
    )


@sa_event.listens_for(ReputationEvent, "before_delete")
def _block_reputation_event_delete(mapper, connection, target):
    raise InvalidRequestError(
        f"ReputationEvent is APPEND-ONLY; DELETE blocked at ORM layer "
        f"(id={target.id}, org={target.ai_org_id}). Council #5 mitigation."
    )


# --------------------------------------------------------------------------- #
# 7. payment_event — money flows between orgs
# --------------------------------------------------------------------------- #

class PaymentEvent(db.Model):
    """Money flow between an AI org and another party (org / FIESTA /
    Lanka.tax / external). Council mitigation #6: Red-Team can be paid a
    cut via reason='red_team_bounty'.
    """
    __tablename__ = "payment_event"

    id = db.Column(db.Integer, primary_key=True)
    payer_kind = db.Column(db.String(16), nullable=False)
    payer_ref_id = db.Column(db.Integer, nullable=False)
    payee_kind = db.Column(db.String(16), nullable=False)
    payee_ref_id = db.Column(db.Integer, nullable=False)

    amount_lkr = db.Column(db.Numeric(18, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="LKR")

    reason = db.Column(db.String(64), nullable=False)
    contract_id = db.Column(
        db.Integer,
        db.ForeignKey("contract.id", ondelete="SET NULL"),
        nullable=True,
    )
    deliverable_id = db.Column(
        db.Integer,
        db.ForeignKey("deliverable.id", ondelete="SET NULL"),
        nullable=True,
    )
    stripe_payment_intent_id = db.Column(db.String(128), nullable=True)

    status = db.Column(db.String(16), nullable=False, default="settled")
    occurred_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index("ix_payment_event_payer", "payer_ref_id", db.text("occurred_at DESC")),
        db.Index("ix_payment_event_payee", "payee_ref_id", db.text("occurred_at DESC")),
    )

    def __repr__(self):
        return (
            f"<PaymentEvent {self.id} {self.payer_kind}:{self.payer_ref_id} "
            f"-> {self.payee_kind}:{self.payee_ref_id} {self.amount_lkr} {self.currency}>"
        )


# --------------------------------------------------------------------------- #
# 8. attribution_ledger — link external outcomes to AI orgs
# --------------------------------------------------------------------------- #

class AttributionLedger(db.Model):
    """Links external outcomes (Lanka.tax/FIESTA invoice_paid, NPS deltas) back
    to the AI org responsible. UNIQUE prevents double-claim by the same org.

    The external_event_ref_id is opaque app-level — typically FK-equivalent to
    events.id from event_models.Event, but kept loose so external systems
    (Lanka.tax SF, FIESTA Stripe webhooks) can claim attribution against any
    event_type schema.
    """
    __tablename__ = "attribution_ledger"

    id = db.Column(db.Integer, primary_key=True)
    external_event_type = db.Column(db.String(64), nullable=False)
    external_event_ref_id = db.Column(db.Integer, nullable=False)

    claimed_by_org_id = db.Column(
        db.Integer,
        db.ForeignKey("ai_org.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 'direct' | 'contributed' | 'last-touch' | 'multi-touch'.
    attribution_kind = db.Column(db.String(32), nullable=False)
    confidence = db.Column(db.Numeric(3, 2), nullable=False, default=1.0)
    evidence_payload = db.Column(db.JSON, nullable=True)

    verified_at = db.Column(db.DateTime, nullable=True)
    verifier_role_id = db.Column(
        db.Integer,
        db.ForeignKey("ai_org_role.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            "external_event_type",
            "external_event_ref_id",
            "claimed_by_org_id",
            name="uq_attribution_per_org_per_event",
        ),
    )

    def __repr__(self):
        return (
            f"<AttributionLedger {self.id} org={self.claimed_by_org_id} "
            f"event={self.external_event_type}:{self.external_event_ref_id}>"
        )


# --------------------------------------------------------------------------- #
# Idempotent DDL (belt-and-braces, same pattern as event_models / ai_crm)
# --------------------------------------------------------------------------- #

def _ensure_ai_org_tables():
    """Idempotent CREATE TABLE IF NOT EXISTS for all 8 AI-org tables + the
    APPEND-ONLY rules on reputation_event. Safe to call multiple times.

    Runs at module import. Same pattern as:
      * app._ensure_additive_schema (events table)
      * ai_crm._ensure_customer_profiles_table (customer_profiles)
      * fx_rate_service._ensure_fx_table
    """
    try:
        from sqlalchemy import text as _sql_text
        from app import app
        with app.app_context():
            # 1. ai_org
            db.session.execute(_sql_text("""
                CREATE TABLE IF NOT EXISTS ai_org (
                    id SERIAL PRIMARY KEY,
                    slug VARCHAR(64) UNIQUE NOT NULL,
                    name VARCHAR(128) NOT NULL,
                    purpose TEXT,
                    status VARCHAR(16) NOT NULL DEFAULT 'active',
                    parent_org_id INTEGER REFERENCES ai_org(id) ON DELETE SET NULL,
                    status_score NUMERIC(5,2) NOT NULL DEFAULT 0,
                    status_band VARCHAR(2) NOT NULL DEFAULT 'C',
                    economic_axis NUMERIC(5,2) NOT NULL DEFAULT 0,
                    human_impact_axis NUMERIC(5,2) NOT NULL DEFAULT 0,
                    ai_reliability_axis NUMERIC(5,2) NOT NULL DEFAULT 0,
                    last_score_computed_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.session.execute(_sql_text(
                "CREATE INDEX IF NOT EXISTS ix_ai_org_slug ON ai_org (slug)"
            ))

            # 2. ai_org_role
            db.session.execute(_sql_text("""
                CREATE TABLE IF NOT EXISTS ai_org_role (
                    id SERIAL PRIMARY KEY,
                    ai_org_id INTEGER NOT NULL REFERENCES ai_org(id) ON DELETE CASCADE,
                    role_slug VARCHAR(64) NOT NULL,
                    role_name VARCHAR(128) NOT NULL,
                    description TEXT,
                    is_red_team BOOLEAN NOT NULL DEFAULT FALSE,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_ai_org_role_slug UNIQUE (ai_org_id, role_slug)
                )
            """))
            db.session.execute(_sql_text(
                "CREATE INDEX IF NOT EXISTS ix_ai_org_role_org ON ai_org_role (ai_org_id)"
            ))

            # 3. proposal
            db.session.execute(_sql_text("""
                CREATE TABLE IF NOT EXISTS proposal (
                    id SERIAL PRIMARY KEY,
                    proposer_org_id INTEGER NOT NULL REFERENCES ai_org(id) ON DELETE CASCADE,
                    buyer_kind VARCHAR(16) NOT NULL,
                    buyer_ref_id INTEGER,
                    opportunity_slug VARCHAR(128) NOT NULL,
                    artifact_kind VARCHAR(32) NOT NULL,
                    artifact_payload JSON,
                    artifact_sha256 VARCHAR(64),
                    artifact_s3_key VARCHAR(512),
                    quoted_price_lkr NUMERIC(18,2),
                    quoted_eta_days INTEGER,
                    status VARCHAR(16) NOT NULL DEFAULT 'submitted',
                    submitted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    decided_at TIMESTAMP
                )
            """))
            db.session.execute(_sql_text(
                "CREATE INDEX IF NOT EXISTS ix_proposal_proposer ON proposal (proposer_org_id)"
            ))
            db.session.execute(_sql_text(
                "CREATE INDEX IF NOT EXISTS ix_proposal_opportunity ON proposal (opportunity_slug)"
            ))
            db.session.execute(_sql_text(
                "CREATE INDEX IF NOT EXISTS ix_proposal_opportunity_status_submitted "
                "ON proposal (opportunity_slug, status, submitted_at DESC)"
            ))

            # 4. contract
            db.session.execute(_sql_text("""
                CREATE TABLE IF NOT EXISTS contract (
                    id SERIAL PRIMARY KEY,
                    proposal_id INTEGER REFERENCES proposal(id) ON DELETE SET NULL,
                    proposer_org_id INTEGER NOT NULL REFERENCES ai_org(id) ON DELETE CASCADE,
                    buyer_kind VARCHAR(16) NOT NULL,
                    buyer_ref_id INTEGER,
                    terms_payload JSON,
                    terms_sha256 VARCHAR(64),
                    terms_s3_key VARCHAR(512),
                    contracted_price_lkr NUMERIC(18,2) NOT NULL,
                    milestone_count INTEGER NOT NULL DEFAULT 1,
                    status VARCHAR(16) NOT NULL DEFAULT 'active',
                    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP
                )
            """))
            db.session.execute(_sql_text(
                "CREATE INDEX IF NOT EXISTS ix_contract_proposer ON contract (proposer_org_id)"
            ))
            db.session.execute(_sql_text(
                "CREATE INDEX IF NOT EXISTS ix_contract_org_status_completed "
                "ON contract (proposer_org_id, status, completed_at DESC)"
            ))

            # 5. deliverable
            db.session.execute(_sql_text("""
                CREATE TABLE IF NOT EXISTS deliverable (
                    id SERIAL PRIMARY KEY,
                    contract_id INTEGER NOT NULL REFERENCES contract(id) ON DELETE CASCADE,
                    proposer_org_id INTEGER NOT NULL REFERENCES ai_org(id) ON DELETE CASCADE,
                    milestone_number INTEGER NOT NULL DEFAULT 1,
                    artifact_kind VARCHAR(32) NOT NULL,
                    artifact_payload JSON,
                    artifact_sha256 VARCHAR(64),
                    artifact_s3_key VARCHAR(512),
                    accepted BOOLEAN,
                    acceptor_kind VARCHAR(16),
                    acceptor_ref_id INTEGER,
                    accepted_at TIMESTAMP,
                    red_team_pass BOOLEAN,
                    red_team_reviewer_role_id INTEGER REFERENCES ai_org_role(id) ON DELETE SET NULL,
                    hallucination_flag BOOLEAN NOT NULL DEFAULT FALSE,
                    quality_score NUMERIC(3,2),
                    delivered_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.session.execute(_sql_text(
                "CREATE INDEX IF NOT EXISTS ix_deliverable_contract ON deliverable (contract_id)"
            ))
            db.session.execute(_sql_text(
                "CREATE INDEX IF NOT EXISTS ix_deliverable_proposer ON deliverable (proposer_org_id)"
            ))
            db.session.execute(_sql_text(
                "CREATE INDEX IF NOT EXISTS ix_deliverable_contract_milestone "
                "ON deliverable (contract_id, milestone_number)"
            ))

            # 6. reputation_event (APPEND-ONLY)
            db.session.execute(_sql_text("""
                CREATE TABLE IF NOT EXISTS reputation_event (
                    id SERIAL PRIMARY KEY,
                    ai_org_id INTEGER NOT NULL REFERENCES ai_org(id) ON DELETE CASCADE,
                    event_type VARCHAR(64) NOT NULL,
                    magnitude NUMERIC(18,4) NOT NULL,
                    axis VARCHAR(16) NOT NULL,
                    source_contract_id INTEGER REFERENCES contract(id) ON DELETE SET NULL,
                    source_deliverable_id INTEGER REFERENCES deliverable(id) ON DELETE SET NULL,
                    attribution_confidence NUMERIC(3,2) NOT NULL DEFAULT 1.0,
                    payload JSON,
                    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.session.execute(_sql_text(
                "CREATE INDEX IF NOT EXISTS ix_reputation_event_org_axis_occurred "
                "ON reputation_event (ai_org_id, axis, occurred_at DESC)"
            ))
            # APPEND-ONLY invariant: Postgres RULES make UPDATE/DELETE silent no-ops
            # at the DB layer. Belt-and-braces with the SQLAlchemy event listeners
            # which raise on attempted ORM-level update/delete.
            db.session.execute(_sql_text(
                "CREATE OR REPLACE RULE no_update AS ON UPDATE TO reputation_event "
                "DO INSTEAD NOTHING"
            ))
            db.session.execute(_sql_text(
                "CREATE OR REPLACE RULE no_delete AS ON DELETE TO reputation_event "
                "DO INSTEAD NOTHING"
            ))

            # 7. payment_event
            db.session.execute(_sql_text("""
                CREATE TABLE IF NOT EXISTS payment_event (
                    id SERIAL PRIMARY KEY,
                    payer_kind VARCHAR(16) NOT NULL,
                    payer_ref_id INTEGER NOT NULL,
                    payee_kind VARCHAR(16) NOT NULL,
                    payee_ref_id INTEGER NOT NULL,
                    amount_lkr NUMERIC(18,2) NOT NULL,
                    currency VARCHAR(3) NOT NULL DEFAULT 'LKR',
                    reason VARCHAR(64) NOT NULL,
                    contract_id INTEGER REFERENCES contract(id) ON DELETE SET NULL,
                    deliverable_id INTEGER REFERENCES deliverable(id) ON DELETE SET NULL,
                    stripe_payment_intent_id VARCHAR(128),
                    status VARCHAR(16) NOT NULL DEFAULT 'settled',
                    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.session.execute(_sql_text(
                "CREATE INDEX IF NOT EXISTS ix_payment_event_payer "
                "ON payment_event (payer_ref_id, occurred_at DESC)"
            ))
            db.session.execute(_sql_text(
                "CREATE INDEX IF NOT EXISTS ix_payment_event_payee "
                "ON payment_event (payee_ref_id, occurred_at DESC)"
            ))

            # 8. attribution_ledger
            db.session.execute(_sql_text("""
                CREATE TABLE IF NOT EXISTS attribution_ledger (
                    id SERIAL PRIMARY KEY,
                    external_event_type VARCHAR(64) NOT NULL,
                    external_event_ref_id INTEGER NOT NULL,
                    claimed_by_org_id INTEGER NOT NULL REFERENCES ai_org(id) ON DELETE CASCADE,
                    attribution_kind VARCHAR(32) NOT NULL,
                    confidence NUMERIC(3,2) NOT NULL DEFAULT 1.0,
                    evidence_payload JSON,
                    verified_at TIMESTAMP,
                    verifier_role_id INTEGER REFERENCES ai_org_role(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_attribution_per_org_per_event
                        UNIQUE (external_event_type, external_event_ref_id, claimed_by_org_id)
                )
            """))
            db.session.execute(_sql_text(
                "CREATE INDEX IF NOT EXISTS ix_attribution_ledger_claimed_by "
                "ON attribution_ledger (claimed_by_org_id)"
            ))

            db.session.commit()
    except Exception as e:
        log.warning(f"Could not ensure ai_org tables: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass


# Run idempotent DDL at module import — same belt-and-braces pattern as
# event_models / ai_crm / fx_rate_service.
_ensure_ai_org_tables()
