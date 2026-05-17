"""
AI CRM / Customer Memory — Wave 2.3 brain for every downstream consumer.

Council #2 (Opus + Sonnet + Gemini + GPT, 2026-05-17) sequenced the EVENT SPINE
(Wave 1) FIRST so this module has a uniform stream to read from. AI CRM is the
second consumer (after leading-indicator dashboards) and the first PER-USER one:
every Wave 3 component (Engagement nudges, Support triage, Cross-Sell upsell)
will call into here to know what to do for any given user.

DESIGN PRINCIPLES:

  1. ONE table — `customer_profiles` — one row per user. Updated, not appended
     (unlike `events`). The row IS the brain's current opinion of the user.

  2. Recompute is idempotent. Calling recompute_profile(user_id) N times in
     a row produces the same result (modulo last_recomputed_at). This is
     critical because Wave 3 consumers may trigger recomputes on-event AND
     the Celery beat runs nightly — overlap must be safe.

  3. Best-effort writes. A failed recompute logs a warning and returns the
     stale row (or a fresh-default row for a brand-new user). NEVER raises
     into a route handler.

  4. No new event types invented here. We READ events.py STANDARD_EVENTS;
     we never extend it. The brain is observational, not emissive.

  5. Heuristic scoring (risk + NBA) is intentionally simple in this first
     pass. The point is to have the rails in place. Council can iterate
     the weights from real data once the dashboards (Wave 2.2) are live.

PUBLIC API:

    from ai_crm import (
        CustomerProfile,
        aggregate_user_timeline,
        score_risk,
        pick_next_best_action,
        recompute_profile,
        recompute_all_active_profiles,
    )

The Celery beat schedule entry (added to celery_config.py by the orchestrator,
not by this module) is:

    'ai_crm-recompute-nightly': {
        'task': 'ai_crm.recompute_all_active_profiles',
        'schedule': crontab(hour=2, minute=0),
    },
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Tuple

from app import db

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants — knobs the council can tune later from real data.
# --------------------------------------------------------------------------- #

LIFECYCLE_STAGES = ("signup", "activated", "paid", "churning", "dormant")

# Days-since-last-event thresholds that flip lifecycle_stage / risk components.
DORMANT_DAYS = 30           # >30d → 'dormant' lifecycle + high risk
CHURNING_DAYS = 14          # >14d but <30d → 'churning' lifecycle

# Risk-score component caps. Documented in score_risk()'s docstring.
RISK_CAP_DAYS_SINCE_EVENT = 40       # max points from "days since last event"
RISK_CAP_DAYS_SINCE_REMIT = 30       # max points from "days since last remittance"
RISK_CAP_LIFECYCLE = 20              # max points from lifecycle_stage
RISK_CAP_UNREAD_TICKET = 10          # max points from open support ticket

# Active-user window for the Celery beat task. We only recompute users who
# have done SOMETHING in the last 30 days, to keep the nightly job bounded.
ACTIVE_WINDOW_DAYS = 30

# Cross-sell trigger: free-tier user with > N remittances is a Pro upgrade candidate.
PRO_UPGRADE_REMIT_THRESHOLD = 6

# Timeline cap for the admin per-user view. Newest first.
TIMELINE_LIMIT = 100


# --------------------------------------------------------------------------- #
# ORM model
# --------------------------------------------------------------------------- #

class CustomerProfile(db.Model):
    """The brain's current opinion of one user.

    One row per user. Updated in-place by recompute_profile(). Belt-and-braces
    schema creation: db.create_all() picks this up via SQLAlchemy metadata when
    the module is imported, AND _ensure_customer_profiles_table() runs at
    import time so the table is guaranteed present even if metadata reflection
    is delayed (same pattern as fx_rate_service._ensure_fx_table).
    """
    __tablename__ = "customer_profiles"

    id = db.Column(db.Integer, primary_key=True)

    # ON DELETE CASCADE — when a user account is purged (GDPR, test cleanup),
    # the brain's opinion of them goes with them. Unique because we maintain
    # at most one profile row per user (in-place updates, not append).
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Inferred persona — superset of User.persona (which is the user's
    # self-declared persona). The brain may infer a more specific persona
    # from event patterns (e.g. 'sl_foreign_income_freelance' vs the
    # broader 'sl_foreign_income' the user picked at signup). NULL = no
    # inference yet (cold start).
    persona_inferred = db.Column(db.String(50), nullable=True)

    # Best-guess attribution. 'lankatax', 'organic', 'referral', etc.
    # Free-form for now; promotion to enum awaits a real attribution model.
    acquisition_source = db.Column(db.String(64), nullable=True)

    # One of LIFECYCLE_STAGES. Recomputed from event evidence.
    lifecycle_stage = db.Column(db.String(32), nullable=False, default="signup")

    # 0-100, higher = higher churn risk. Heuristic — see score_risk().
    risk_score = db.Column(db.Integer, nullable=False, default=0)

    # The single recommended next action + a human-readable "because". The
    # reason IS the audit trail — every consumer that surfaces an NBA should
    # surface the reason too, so the CEO can see WHY the brain chose it.
    next_best_action = db.Column(db.String(64), nullable=True)
    next_best_action_reason = db.Column(db.Text, nullable=True)

    # Aggregations refreshed at recompute time.
    last_event_at = db.Column(db.DateTime, nullable=True)
    last_remittance_at = db.Column(db.DateTime, nullable=True)
    lifetime_remittance_count = db.Column(db.Integer, nullable=False, default=0)
    lifetime_remittance_lkr = db.Column(db.Numeric(18, 2), nullable=False, default=0)

    first_seen_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )
    last_recomputed_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )

    def __repr__(self):
        return (
            f"<CustomerProfile user={self.user_id} stage={self.lifecycle_stage} "
            f"risk={self.risk_score} nba={self.next_best_action!r}>"
        )


# --------------------------------------------------------------------------- #
# Idempotent table creation (belt-and-braces, same pattern as fx_rate_service)
# --------------------------------------------------------------------------- #

def _ensure_customer_profiles_table():
    """Idempotent. Runs on import; cheap. Mirrors fx_rate_service._ensure_fx_table.

    The raw DDL is a safety net: db.create_all() in main.py should create the
    table from the SQLAlchemy model, but if metadata reflection is delayed or
    main.py hasn't run yet (e.g. Celery worker boot order), this guarantees the
    table is present before any recompute call hits it.
    """
    try:
        from sqlalchemy import text as _sql_text
        from app import app
        with app.app_context():
            db.session.execute(_sql_text("""
                CREATE TABLE IF NOT EXISTS customer_profiles (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL UNIQUE
                        REFERENCES "user"(id) ON DELETE CASCADE,
                    persona_inferred VARCHAR(50),
                    acquisition_source VARCHAR(64),
                    lifecycle_stage VARCHAR(32) NOT NULL DEFAULT 'signup',
                    risk_score INTEGER NOT NULL DEFAULT 0,
                    next_best_action VARCHAR(64),
                    next_best_action_reason TEXT,
                    last_event_at TIMESTAMP,
                    last_remittance_at TIMESTAMP,
                    lifetime_remittance_count INTEGER NOT NULL DEFAULT 0,
                    lifetime_remittance_lkr NUMERIC(18, 2) NOT NULL DEFAULT 0,
                    first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_recomputed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.session.execute(_sql_text("""
                CREATE INDEX IF NOT EXISTS ix_customer_profiles_user_id
                    ON customer_profiles (user_id)
            """))
            db.session.execute(_sql_text("""
                CREATE INDEX IF NOT EXISTS ix_customer_profiles_lifecycle
                    ON customer_profiles (lifecycle_stage, last_event_at DESC)
            """))
            db.session.commit()
    except Exception as e:
        log.warning("Could not ensure customer_profiles table: %s", e)
        try:
            db.session.rollback()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Aggregation helpers
# --------------------------------------------------------------------------- #

def _utcnow() -> datetime:
    """Hook for tests — kept as a function so tests can monkeypatch it."""
    return datetime.utcnow()


def aggregate_user_timeline(user_id: int) -> List[dict]:
    """Pull a unified per-user timeline from events + remittance_entries +
    audit_log. Returns up to TIMELINE_LIMIT rows ordered newest-first.

    Each row is:
        {"type": "<event|remittance|audit>",
         "at":   <datetime>,
         "summary": "<short human-readable string>",
         "payload": <raw row payload or dict>}

    Used by /admin/customer/<id>. NEVER raises — returns [] on any failure.
    """
    items: List[dict] = []
    try:
        from event_models import Event
        for ev in (
            Event.query
                 .filter(Event.user_id == user_id)
                 .order_by(Event.created_at.desc())
                 .limit(TIMELINE_LIMIT)
                 .all()
        ):
            items.append({
                "type": "event",
                "at": ev.created_at,
                "summary": ev.event_type,
                "payload": ev.payload or {},
            })
    except Exception as e:
        log.warning("aggregate_user_timeline events fetch failed for %s: %s", user_id, e)

    try:
        from remittance_models import RemittanceEntry
        for r in (
            RemittanceEntry.query
                          .filter(RemittanceEntry.user_id == user_id)
                          .order_by(RemittanceEntry.created_at.desc())
                          .limit(TIMELINE_LIMIT)
                          .all()
        ):
            items.append({
                "type": "remittance",
                "at": r.created_at,
                "summary": f"{r.foreign_currency} {r.foreign_amount}  ({r.tax_year})",
                "payload": {
                    "entry_id": r.id,
                    "currency": r.foreign_currency,
                    "amount": str(r.foreign_amount or 0),
                    "lkr_cbsl": str(r.lkr_amount_cbsl or 0),
                    "tax_year": r.tax_year,
                    "completeness": r.completeness_status()[0],
                },
            })
    except Exception as e:
        log.warning("aggregate_user_timeline remittance fetch failed for %s: %s", user_id, e)

    try:
        from models import AuditLog
        for a in (
            AuditLog.query
                    .filter(AuditLog.user_id == user_id)
                    .order_by(AuditLog.timestamp.desc())
                    .limit(TIMELINE_LIMIT)
                    .all()
        ):
            items.append({
                "type": "audit",
                "at": a.timestamp,
                "summary": f"{a.action} {a.entity_type}#{a.entity_id}",
                "payload": a.changed_fields or {},
            })
    except Exception as e:
        log.warning("aggregate_user_timeline audit fetch failed for %s: %s", user_id, e)

    # Sort newest-first, then truncate. We sort here (not in SQL) because we
    # merged three sources with their own native orderings.
    items.sort(key=lambda x: x["at"] or datetime.min, reverse=True)
    return items[:TIMELINE_LIMIT]


# --------------------------------------------------------------------------- #
# Per-user aggregations the scorers depend on
# --------------------------------------------------------------------------- #

def _latest_event_at(user_id: int) -> Optional[datetime]:
    try:
        from event_models import Event
        row = (
            Event.query
                 .filter(Event.user_id == user_id)
                 .order_by(Event.created_at.desc())
                 .first()
        )
        return row.created_at if row else None
    except Exception as e:
        log.warning("_latest_event_at(%s) failed: %s", user_id, e)
        return None


def _event_types_seen(user_id: int) -> set:
    """Distinct event_type values this user has ever generated. Used by NBA."""
    try:
        from event_models import Event
        rows = (
            db.session.query(Event.event_type)
                      .filter(Event.user_id == user_id)
                      .distinct()
                      .all()
        )
        return {r[0] for r in rows}
    except Exception as e:
        log.warning("_event_types_seen(%s) failed: %s", user_id, e)
        return set()


def _remittance_stats(user_id: int) -> Tuple[Optional[datetime], int, Decimal]:
    """(last_remittance_at, lifetime_count, lifetime_lkr_total)."""
    try:
        from sqlalchemy import func
        from remittance_models import RemittanceEntry
        row = (
            db.session.query(
                func.max(RemittanceEntry.created_at),
                func.count(RemittanceEntry.id),
                func.coalesce(func.sum(RemittanceEntry.lkr_amount_cbsl), 0),
            )
            .filter(RemittanceEntry.user_id == user_id)
            .one()
        )
        last_at, cnt, total = row
        total_dec = Decimal(str(total or 0))
        return last_at, int(cnt or 0), total_dec
    except Exception as e:
        log.warning("_remittance_stats(%s) failed: %s", user_id, e)
        return None, 0, Decimal("0")


def _has_open_support_ticket(user_id: int) -> bool:
    """Cheap proxy: any support_message_received event in the last 7 days that
    has NOT been followed by a 'support_resolved' event. We don't yet emit
    'support_resolved' (no support workflow in Wave 2), so today this returns
    True for any recent support_message_received. Wave 3 Support pod will
    replace this with a real ticket query.
    """
    try:
        from event_models import Event
        cutoff = _utcnow() - timedelta(days=7)
        return bool(
            Event.query
                 .filter(Event.user_id == user_id,
                         Event.event_type == "support_message_received",
                         Event.created_at >= cutoff)
                 .first()
        )
    except Exception as e:
        log.warning("_has_open_support_ticket(%s) failed: %s", user_id, e)
        return False


def _user_persona_and_subscription(user_id: int) -> Tuple[Optional[str], Optional[str]]:
    """(self_declared_persona, subscription_status). Falls back to (None, None)."""
    try:
        from models import User
        u = User.query.get(user_id)
        if not u:
            return None, None
        return u.persona, u.subscription_status
    except Exception as e:
        log.warning("_user_persona_and_subscription(%s) failed: %s", user_id, e)
        return None, None


# --------------------------------------------------------------------------- #
# Lifecycle stage
# --------------------------------------------------------------------------- #

def _infer_lifecycle_stage(
    last_event_at: Optional[datetime],
    types_seen: set,
    subscription_status: Optional[str],
    now: Optional[datetime] = None,
) -> str:
    """Heuristic ladder. Newest-evidence-wins:

      dormant   — no event for > DORMANT_DAYS
      churning  — no event for > CHURNING_DAYS (but < DORMANT_DAYS)
      paid      — has a checkout_completed event OR subscription_status not free
      activated — has persona_set OR remittance_added
      signup    — default cold start
    """
    now = now or _utcnow()

    if last_event_at:
        age = now - last_event_at
        if age > timedelta(days=DORMANT_DAYS):
            return "dormant"
        if age > timedelta(days=CHURNING_DAYS):
            return "churning"

    if "checkout_completed" in types_seen:
        return "paid"
    if subscription_status and subscription_status not in (None, "", "free_trial", "free"):
        return "paid"

    if "persona_set" in types_seen or "remittance_added" in types_seen:
        return "activated"

    return "signup"


# --------------------------------------------------------------------------- #
# Risk score
# --------------------------------------------------------------------------- #

def score_risk(user_id: int) -> int:
    """Compute a 0-100 churn-risk score for `user_id`. Higher = higher churn risk.

    Heuristic — sum of capped components:

      A) Days since last event:
            >  DORMANT_DAYS   →  full RISK_CAP_DAYS_SINCE_EVENT (40)
            >  CHURNING_DAYS  →  half RISK_CAP_DAYS_SINCE_EVENT (20)
            else              →  0
         No event ever        →  full RISK_CAP_DAYS_SINCE_EVENT (40)

      B) Days since last remittance (only for users with persona='sl_foreign_income'):
            > 60              →  full RISK_CAP_DAYS_SINCE_REMIT (30)
            > 30              →  half RISK_CAP_DAYS_SINCE_REMIT (15)
            else              →  0
         No remittance ever, persona set → full RISK_CAP_DAYS_SINCE_REMIT (30)

      C) Lifecycle stage:
            dormant           →  full RISK_CAP_LIFECYCLE (20)
            churning          →  half (10)
            else              →  0

      D) Unread support ticket:
            yes               →  full RISK_CAP_UNREAD_TICKET (10)
            no                →  0

    Sum capped at 100. The cap discipline (no single signal can push past
    its cap) keeps the score interpretable and lets the council weight
    individual signals from real data later.

    NEVER raises. Returns 0 on any failure.
    """
    try:
        now = _utcnow()
        score = 0

        # Component A — days since last event
        last_event_at = _latest_event_at(user_id)
        if last_event_at is None:
            score += RISK_CAP_DAYS_SINCE_EVENT
        else:
            age = now - last_event_at
            if age > timedelta(days=DORMANT_DAYS):
                score += RISK_CAP_DAYS_SINCE_EVENT
            elif age > timedelta(days=CHURNING_DAYS):
                score += RISK_CAP_DAYS_SINCE_EVENT // 2

        # Component B — days since last remittance (gated on persona)
        persona, sub_status = _user_persona_and_subscription(user_id)
        if persona == "sl_foreign_income":
            last_remit_at, cnt, _ = _remittance_stats(user_id)
            if cnt == 0:
                score += RISK_CAP_DAYS_SINCE_REMIT
            elif last_remit_at is not None:
                age = now - last_remit_at
                if age > timedelta(days=60):
                    score += RISK_CAP_DAYS_SINCE_REMIT
                elif age > timedelta(days=30):
                    score += RISK_CAP_DAYS_SINCE_REMIT // 2

        # Component C — lifecycle stage
        types_seen = _event_types_seen(user_id)
        stage = _infer_lifecycle_stage(last_event_at, types_seen, sub_status, now=now)
        if stage == "dormant":
            score += RISK_CAP_LIFECYCLE
        elif stage == "churning":
            score += RISK_CAP_LIFECYCLE // 2

        # Component D — unread support ticket
        if _has_open_support_ticket(user_id):
            score += RISK_CAP_UNREAD_TICKET

        return max(0, min(100, int(score)))
    except Exception as e:
        log.warning("score_risk(%s) failed: %s", user_id, e)
        return 0


# --------------------------------------------------------------------------- #
# Next-best-action
# --------------------------------------------------------------------------- #

def pick_next_best_action(user_id: int) -> Tuple[str, str]:
    """Return (action_key, human_reason) — see CLAUDE.md spec for the ladder.

    Order matters (first match wins). Each rung is a specific funnel-stage gap;
    the default ('await_user_action', 'on track') is the no-op state.

    NEVER raises. Returns ('await_user_action', 'on track') on any failure.
    """
    try:
        now = _utcnow()
        types_seen = _event_types_seen(user_id)
        last_event_at = _latest_event_at(user_id)
        persona, sub_status = _user_persona_and_subscription(user_id)
        last_remit_at, remit_cnt, _ = _remittance_stats(user_id)

        # 1) signup → persona missing for 7d  → complete_signup
        # (we treat "no persona_set event ever" as the gap, regardless of when
        #  signup happened — there's no signed cutoff on a missing event)
        if "persona_set" not in types_seen and persona is None:
            return ("complete_signup", "persona_set event missing")

        # 2) persona set but no bank statement → upload_first_statement
        if (
            ("persona_set" in types_seen or persona)
            and "bank_statement_uploaded" not in types_seen
            and remit_cnt == 0
        ):
            return (
                "upload_first_statement",
                "persona set but no bank_statement_uploaded",
            )

        # 3) imported but no remittance → add_first_remittance
        if (
            "bank_statement_uploaded" in types_seen
            and remit_cnt == 0
        ):
            return (
                "add_first_remittance",
                "imported but no remittance_added",
            )

        # 4) free tier + heavy use → upgrade_to_pro
        if (
            sub_status in (None, "", "free_trial", "free")
            and remit_cnt > PRO_UPGRADE_REMIT_THRESHOLD
        ):
            return (
                "upgrade_to_pro",
                "high remittance volume on self-serve",
            )

        # 5) dormant → reengage_dormant
        if last_event_at is not None and (now - last_event_at) > timedelta(days=DORMANT_DAYS):
            return (
                "reengage_dormant",
                "no event in 30+ days",
            )

        return ("await_user_action", "on track")
    except Exception as e:
        log.warning("pick_next_best_action(%s) failed: %s", user_id, e)
        return ("await_user_action", "on track")


# --------------------------------------------------------------------------- #
# Recompute — the write path
# --------------------------------------------------------------------------- #

def recompute_profile(user_id: int) -> Optional[CustomerProfile]:
    """Refresh (or create) the CustomerProfile row for `user_id`. Idempotent.

    Returns the persisted profile, or None on hard failure (DB write blew up).
    """
    try:
        from models import User
        user = User.query.get(user_id)
        if not user:
            log.info("recompute_profile: user %s not found, skipping", user_id)
            return None

        now = _utcnow()

        # Pull the inputs once each (cheap; the helpers each take a short query)
        last_event_at = _latest_event_at(user_id)
        types_seen = _event_types_seen(user_id)
        last_remit_at, remit_cnt, remit_total = _remittance_stats(user_id)
        persona, sub_status = user.persona, user.subscription_status

        lifecycle = _infer_lifecycle_stage(last_event_at, types_seen, sub_status, now=now)
        risk = score_risk(user_id)
        nba_key, nba_reason = pick_next_best_action(user_id)

        # Get-or-create. Unique constraint on user_id means we never have >1 row.
        profile = (
            CustomerProfile.query
                          .filter(CustomerProfile.user_id == user_id)
                          .first()
        )
        if profile is None:
            profile = CustomerProfile(
                user_id=user_id,
                first_seen_at=now,
            )
            db.session.add(profile)

        # The brain may infer a more-specific persona later; for now, mirror
        # the user's self-declared persona as the cold-start inference.
        profile.persona_inferred = persona
        # acquisition_source is left untouched on recompute — attribution model
        # writes it from another path (e.g. UTM capture at signup).
        profile.lifecycle_stage = lifecycle
        profile.risk_score = risk
        profile.next_best_action = nba_key
        profile.next_best_action_reason = nba_reason
        profile.last_event_at = last_event_at
        profile.last_remittance_at = last_remit_at
        profile.lifetime_remittance_count = remit_cnt
        profile.lifetime_remittance_lkr = remit_total
        profile.last_recomputed_at = now

        db.session.commit()
        return profile
    except Exception as e:
        log.warning("recompute_profile(%s) failed: %s", user_id, e)
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


# --------------------------------------------------------------------------- #
# Celery beat task — nightly recompute of active users
# --------------------------------------------------------------------------- #

def _active_user_ids(window_days: int = ACTIVE_WINDOW_DAYS) -> List[int]:
    """User ids with at least one event in the last `window_days`. Bounded so
    the nightly job stays cheap as the user base grows.
    """
    try:
        from event_models import Event
        cutoff = _utcnow() - timedelta(days=window_days)
        rows = (
            db.session.query(Event.user_id)
                      .filter(Event.user_id.isnot(None),
                              Event.created_at >= cutoff)
                      .distinct()
                      .all()
        )
        return [r[0] for r in rows]
    except Exception as e:
        log.warning("_active_user_ids failed: %s", e)
        return []


def recompute_all_active_profiles() -> dict:
    """Recompute CustomerProfile for every user active in the last
    ACTIVE_WINDOW_DAYS days. Returns a small summary dict for the Celery
    result backend.

    Wrapped as a Celery task (see decorator below) — bound to the beat
    schedule entry `ai_crm-recompute-nightly` registered in celery_config.py.
    """
    user_ids = _active_user_ids()
    ok = 0
    failed = 0
    for uid in user_ids:
        result = recompute_profile(uid)
        if result is not None:
            ok += 1
        else:
            failed += 1
    summary = {
        "active_users": len(user_ids),
        "recomputed_ok": ok,
        "recomputed_failed": failed,
        "ran_at": _utcnow().isoformat(),
    }
    log.info("recompute_all_active_profiles: %s", summary)
    return summary


# --------------------------------------------------------------------------- #
# Celery wiring — best-effort; if Celery is unavailable (CLI/test contexts),
# the function above is still directly callable. We never raise on import.
# --------------------------------------------------------------------------- #

try:
    from celery_config import app as celery_app

    # Re-bind the plain function as a registered Celery task. Keeping the
    # plain function callable lets tests call recompute_all_active_profiles()
    # directly without a broker; Celery beat invokes the .delay/.apply_async
    # path through the registered name.
    recompute_all_active_profiles = celery_app.task(  # type: ignore[assignment]
        name="ai_crm.recompute_all_active_profiles"
    )(recompute_all_active_profiles)
except Exception as _e:
    log.debug("Celery wiring skipped (ok in tests/CLI): %s", _e)


# Run schema setup on import (mirrors fx_rate_service pattern).
_ensure_customer_profiles_table()


__all__ = [
    "CustomerProfile",
    "aggregate_user_timeline",
    "score_risk",
    "pick_next_best_action",
    "recompute_profile",
    "recompute_all_active_profiles",
    "LIFECYCLE_STAGES",
    "TIMELINE_LIMIT",
]
