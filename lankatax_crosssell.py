"""
Lanka.tax Cross-Sell Automation — Wave 3.3 (2026-05-18).

The #1 leading-indicator metric per council #2: "Lanka.tax Cross-Sell Take
Rate > 20% target = 1,120 paid users / $224k ARR floor". This module owns:

  1. Cohort building   — runs a SQL selector, snapshots matching user IDs,
                         persists a LankataxCohort row.
  2. Campaign dispatch — sends an email (SendGrid) or in-app banner (stub
                         until the engagement_engine sibling lands), one
                         LankataxOutreach row per recipient, with a 14d
                         per-(user, campaign_key) cooldown.
  3. Take-rate compute — SELECT-aggregate over LankataxOutreach.
  4. Celery beat task  — daily 07:00 UTC pulse that refreshes the 3
                         predefined cohorts and dispatches the highest-value
                         daily campaigns.

DESIGN INTENT (mirrors ai_crm.py + pricing_engine.py conventions):

  * Best-effort writes. send/event failures are logged warnings, never
    raises into a route handler or a Celery task.

  * Idempotent. run_campaign() is safe to call N times in a row: the
    cooldown query ensures a given user gets at most one outreach row per
    campaign_key per 14-day window.

  * One source of truth for cohort definitions — PREDEFINED_COHORTS — used
    by both the daily pulse AND tests.

  * Stripe / SendGrid are NOT a hard import. Tests run with neither; the
    module remains importable and the email path degrades to a logged
    warning + a row with channel='email' (no send) when SENDGRID_API_KEY
    is missing.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from flask import url_for
from sqlalchemy import text as _sql_text

from app import db
from events import emit as emit_event
from lankatax_models import LankataxCohort, LankataxOutreach
from lankatax_onboarding_routes import generate_token

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Knobs the council can tune later from real data
# --------------------------------------------------------------------------- #

COOLDOWN_DAYS = 14
TAKE_RATE_LOOKBACK_DAYS_DEFAULT = 30

# Hard ceiling on rows we'll iterate in a single run_campaign() — guards
# against a runaway cohort selector returning the whole user table.
MAX_RECIPIENTS_PER_RUN = 5000

# Daily pulse decision: only re-fire `lankatax_existing_clients` if no
# member of the cohort has been sent the campaign in this many days.
EXISTING_CLIENTS_REFIRE_DAYS = 14


# --------------------------------------------------------------------------- #
# Predefined cohort selectors — single source of truth
# --------------------------------------------------------------------------- #
#
# Each selector returns a single column `id` of integer user IDs. They are
# raw SQL because:
#   (a) The orchestrator + council want the SQL surface visible (auditable)
#       not buried behind ORM .filter() chains.
#   (b) Some selectors join across tables the ORM doesn't model uniformly
#       (events JSON payload extraction, future Lanka.tax attribution table).
#
# Placeholder note: until Lanka.tax delivers actual cross-org user-mapping
# data, `lankatax_existing_clients` defaults to a "free-trial user older
# than 90 days with no inferred persona" proxy. Refine when the real
# attribution table lands.
#
PREDEFINED_COHORTS: Dict[str, Dict[str, str]] = {
    "lankatax_existing_clients": {
        "description": (
            "Free-trial users created >90 days ago who never set a persona — "
            "proxy for 'Lanka.tax client who never adopted FIESTA'. Refine "
            "once real cross-org attribution data is available."
        ),
        "sql_query": """
            SELECT id FROM "user"
            WHERE email LIKE '%@%'
              AND created_at < (CURRENT_TIMESTAMP - INTERVAL '90 days')
              AND persona IS NULL
              AND subscription_status = 'free_trial'
        """.strip(),
    },
    "lankatax_warm_partial": {
        "description": (
            "sl_foreign_income users with at least one remittance entry but "
            "still on free_trial — the highest-value upsell cohort."
        ),
        "sql_query": """
            SELECT DISTINCT u.id
            FROM "user" u
            JOIN remittance_entries r ON r.user_id = u.id
            WHERE u.persona = 'sl_foreign_income'
              AND u.subscription_status = 'free_trial'
        """.strip(),
    },
    "lankatax_dormant_30d": {
        "description": (
            "Users whose last Event was > 30 days ago. Re-engagement cohort."
        ),
        "sql_query": """
            SELECT u.id
            FROM "user" u
            LEFT JOIN (
                SELECT user_id, MAX(created_at) AS last_event_at
                FROM events
                WHERE user_id IS NOT NULL
                GROUP BY user_id
            ) e ON e.user_id = u.id
            WHERE e.last_event_at IS NOT NULL
              AND e.last_event_at < (CURRENT_TIMESTAMP - INTERVAL '30 days')
        """.strip(),
    },
}


# --------------------------------------------------------------------------- #
# Cohort builder
# --------------------------------------------------------------------------- #

def build_cohort(name: str, sql_query: str, description: Optional[str] = None) -> Optional[LankataxCohort]:
    """Run the SQL, snapshot matching user IDs, upsert the LankataxCohort row.

    Returns the persisted cohort, or None on hard failure.

    Idempotent: calling repeatedly with the same name updates the same row
    (target_user_ids snapshot is refreshed; created_at preserved).
    """
    try:
        rows = db.session.execute(_sql_text(sql_query)).fetchall()
        # Allow selectors that return either a single column (.id) or named.
        ids: List[int] = []
        for r in rows:
            try:
                # SQLAlchemy Row supports indexing by position
                ids.append(int(r[0]))
            except (TypeError, ValueError):
                continue

        cohort = (
            LankataxCohort.query
                          .filter(LankataxCohort.name == name)
                          .first()
        )
        if cohort is None:
            cohort = LankataxCohort(
                name=name[:64],
                description=description,
                sql_query=sql_query,
                target_user_ids=ids,
                members_count=len(ids),
            )
            db.session.add(cohort)
        else:
            cohort.description = description if description is not None else cohort.description
            cohort.sql_query = sql_query
            cohort.target_user_ids = ids
            cohort.members_count = len(ids)
            cohort.last_run_at = datetime.utcnow()

        db.session.commit()

        emit_event(
            "lankatax_cohort_built",
            payload={
                "cohort_name": cohort.name,
                "members_count": cohort.members_count,
            },
            source="cron:lankatax_crosssell.build",
        )
        return cohort
    except Exception as exc:
        log.warning("build_cohort(%s) failed: %s", name, exc)
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


# --------------------------------------------------------------------------- #
# Cooldown check
# --------------------------------------------------------------------------- #

def _in_cooldown(user_id: int, campaign_key: str, cooldown_days: int = COOLDOWN_DAYS) -> bool:
    """True if this (user, campaign) pair was sent within the cooldown window.

    Defence in depth: returns True on query failure so we err on the side
    of NOT double-sending.
    """
    try:
        cutoff = datetime.utcnow() - timedelta(days=cooldown_days)
        existing = (
            LankataxOutreach.query
                            .filter(LankataxOutreach.user_id == user_id,
                                    LankataxOutreach.campaign_key == campaign_key,
                                    LankataxOutreach.sent_at >= cutoff)
                            .first()
        )
        return existing is not None
    except Exception as exc:
        log.warning("_in_cooldown(%s, %s) query failed: %s — treating as in cooldown",
                    user_id, campaign_key, exc)
        return True


# --------------------------------------------------------------------------- #
# Channel dispatchers
# --------------------------------------------------------------------------- #

def _build_deep_link(user_id: int, campaign_key: str) -> str:
    """Return the absolute onboarding URL for this (user, campaign).

    Falls back to a relative URL if called outside a Flask request context
    (the deep link still encodes the token; callers should prefer to invoke
    from within an app context so url_for(_external=True) resolves).
    """
    token = generate_token(user_id, campaign_key)
    try:
        return url_for(
            "lankatax_onboarding.lankatax_onboarding",
            token=token,
            utm_source="lankatax",
            utm_campaign=campaign_key,
            _external=True,
        )
    except Exception:
        # No request/app context — return a relative path so the row still
        # has a recoverable token. Callers in test/CLI contexts handle this.
        return f"/onboarding/lankatax?token={token}&utm_source=lankatax&utm_campaign={campaign_key}"


def _render_email_template(campaign_key: str, variant: Optional[str], user) -> Optional[str]:
    """Render the (campaign_key, variant) email template if available. Returns
    HTML string or None if the template can't be located / rendered.
    """
    from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape

    template_name = f"{campaign_key}_{variant or 'a'}.html"
    try:
        repo_root = os.path.dirname(os.path.abspath(__file__))
        templates_dir = os.path.join(repo_root, "lankatax_email_templates")
        env = Environment(
            loader=FileSystemLoader(templates_dir),
            autoescape=select_autoescape(["html"]),
        )
        tmpl = env.get_template(template_name)
        cta_url = _build_deep_link(user.id, campaign_key)
        return tmpl.render(
            user_name=(getattr(user, "name", None) or "there"),
            cta_url=cta_url,
            tier_recommended="Pro Compliance",
        )
    except TemplateNotFound:
        log.warning("_render_email_template: template not found: %s", template_name)
        return None
    except Exception as exc:
        log.warning("_render_email_template(%s, %s) failed: %s",
                    campaign_key, variant, exc)
        return None


def _send_email(user, campaign_key: str, variant: Optional[str]) -> bool:
    """Send the cross-sell email via SendGrid. Returns True on accepted (2xx),
    False otherwise (missing key, missing template, send error, no email).

    Never raises.
    """
    to_email = getattr(user, "email", None)
    if not to_email:
        log.warning("_send_email: user %s has no email; skipping", getattr(user, "id", None))
        return False

    html = _render_email_template(campaign_key, variant, user)
    if not html:
        return False

    api_key = os.environ.get("SENDGRID_API_KEY")
    if not api_key:
        log.warning(
            "_send_email: SENDGRID_API_KEY missing; recording outreach as "
            "channel=email but skipping live send (campaign=%s user=%s)",
            campaign_key, getattr(user, "id", None),
        )
        return False

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Email, To, Content

        message = Mail(
            from_email=Email("info@developsrilanka.com"),
            to_emails=To(to_email),
            subject="Your Lanka.tax filing just got a lot easier — activate FIESTA",
            html_content=Content("text/html", html),
        )
        message.from_email.name = "FIESTA"
        resp = SendGridAPIClient(api_key).send(message)
        if 200 <= int(getattr(resp, "status_code", 0)) < 300:
            return True
        log.warning(
            "_send_email: non-2xx from SendGrid: %s for user=%s campaign=%s",
            getattr(resp, "status_code", "?"), user.id, campaign_key,
        )
        return False
    except Exception as exc:
        log.warning("_send_email send failed for user=%s campaign=%s: %s",
                    getattr(user, "id", None), campaign_key, exc)
        return False


def _record_in_app(user, campaign_key: str, variant: Optional[str]) -> bool:
    """Record an in-app banner intent. Stub until engagement_engine ships.

    Returns True (we successfully recorded intent) so the LankataxOutreach
    row is still created — once engagement_engine is live, it will read this
    row and render the actual banner.
    """
    log.info(
        "in_app outreach recorded (stub, awaiting engagement_engine): "
        "user=%s campaign=%s variant=%s",
        getattr(user, "id", None), campaign_key, variant,
    )
    return True


# --------------------------------------------------------------------------- #
# Campaign dispatcher
# --------------------------------------------------------------------------- #

def run_campaign(
    cohort_id: int,
    campaign_key: str,
    channel: str = "email",
    variant: Optional[str] = None,
) -> Dict[str, int]:
    """For each user in the cohort: check cooldown, send via channel, persist
    a LankataxOutreach row, emit a lankatax_outreach_sent event.

    Returns:
        {'attempted': N, 'sent': N, 'skipped_cooldown': N, 'errors': N}

    Never raises.
    """
    counts = {"attempted": 0, "sent": 0, "skipped_cooldown": 0, "errors": 0}

    cohort = LankataxCohort.query.get(cohort_id)
    if cohort is None:
        log.warning("run_campaign: cohort %s not found", cohort_id)
        return counts

    user_ids = list(cohort.target_user_ids or [])
    if len(user_ids) > MAX_RECIPIENTS_PER_RUN:
        log.warning(
            "run_campaign: cohort %s has %d users, capping at %d",
            cohort.name, len(user_ids), MAX_RECIPIENTS_PER_RUN,
        )
        user_ids = user_ids[:MAX_RECIPIENTS_PER_RUN]

    if channel not in ("email", "in_app"):
        log.warning("run_campaign: unknown channel %r, defaulting to 'email'", channel)
        channel = "email"

    try:
        from models import User
    except Exception as exc:
        log.warning("run_campaign: cannot import User model: %s", exc)
        return counts

    for uid in user_ids:
        counts["attempted"] += 1
        try:
            if _in_cooldown(uid, campaign_key):
                counts["skipped_cooldown"] += 1
                continue

            user = User.query.get(int(uid))
            if user is None:
                counts["errors"] += 1
                continue

            if channel == "email":
                # Best-effort send. We record the row even if the SendGrid
                # call returns False (missing key, missing template) so the
                # cooldown still applies and we can audit attempts. The
                # event payload carries the send_ok flag.
                send_ok = _send_email(user, campaign_key, variant)
            else:
                send_ok = _record_in_app(user, campaign_key, variant)

            outreach = LankataxOutreach(
                user_id=user.id,
                cohort_id=cohort.id,
                campaign_key=campaign_key[:64],
                channel=channel,
                variant=(variant[:8] if variant else None),
                sent_at=datetime.utcnow(),
            )
            db.session.add(outreach)
            db.session.commit()

            counts["sent"] += 1

            emit_event(
                "lankatax_outreach_sent",
                user_id=user.id,
                payload={
                    "cohort": cohort.name,
                    "campaign_key": campaign_key,
                    "channel": channel,
                    "variant": variant,
                    "send_ok": bool(send_ok),
                },
                source="cron:lankatax_crosssell.run",
            )
        except Exception as exc:
            counts["errors"] += 1
            log.warning(
                "run_campaign: row create/send failed for user=%s campaign=%s: %s",
                uid, campaign_key, exc,
            )
            try:
                db.session.rollback()
            except Exception:
                pass

    log.info("run_campaign(cohort=%s campaign=%s channel=%s): %s",
             cohort.name, campaign_key, channel, counts)
    return counts


# --------------------------------------------------------------------------- #
# Take-rate computation
# --------------------------------------------------------------------------- #

def compute_take_rate(
    campaign_key: str,
    lookback_days: int = TAKE_RATE_LOOKBACK_DAYS_DEFAULT,
) -> Dict[str, float]:
    """Aggregate sends/opens/clicks/conversions for `campaign_key` over the
    last `lookback_days`. Returns:

        {'sent': N, 'opened': N, 'clicked': N, 'converted': N,
         'take_rate_pct': float}

    take_rate_pct = (converted / sent) * 100. Returns 0.0 when sent == 0.
    Never raises — returns the zero-shape dict on any failure.
    """
    zero = {
        "sent": 0, "opened": 0, "clicked": 0, "converted": 0, "take_rate_pct": 0.0,
    }
    try:
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)
        base = LankataxOutreach.query.filter(
            LankataxOutreach.campaign_key == campaign_key,
            LankataxOutreach.sent_at >= cutoff,
        )

        sent = base.count()
        if sent == 0:
            return zero

        opened = base.filter(LankataxOutreach.opened_at.isnot(None)).count()
        clicked = base.filter(LankataxOutreach.clicked_at.isnot(None)).count()
        converted = base.filter(LankataxOutreach.converted_at.isnot(None)).count()

        take_rate_pct = round((converted / sent) * 100.0, 2) if sent else 0.0
        return {
            "sent": int(sent),
            "opened": int(opened),
            "clicked": int(clicked),
            "converted": int(converted),
            "take_rate_pct": float(take_rate_pct),
        }
    except Exception as exc:
        log.warning("compute_take_rate(%s) failed: %s", campaign_key, exc)
        return zero


# --------------------------------------------------------------------------- #
# Cooldown helper for the daily pulse
# --------------------------------------------------------------------------- #

def _cohort_needs_refire(cohort: LankataxCohort, campaign_key: str, refire_days: int) -> bool:
    """True if NO member of the cohort has been sent `campaign_key` in the
    last `refire_days`. Used to gate `lankatax_existing_clients` (the big
    one) so we don't re-spam quickly.
    """
    try:
        user_ids = list(cohort.target_user_ids or [])
        if not user_ids:
            return False
        cutoff = datetime.utcnow() - timedelta(days=refire_days)
        recent = (
            LankataxOutreach.query
                            .filter(LankataxOutreach.campaign_key == campaign_key,
                                    LankataxOutreach.user_id.in_(user_ids),
                                    LankataxOutreach.sent_at >= cutoff)
                            .first()
        )
        return recent is None
    except Exception as exc:
        log.warning("_cohort_needs_refire(%s, %s) failed: %s",
                    cohort.name, campaign_key, exc)
        return False


# --------------------------------------------------------------------------- #
# Daily pulse — Celery beat task
# --------------------------------------------------------------------------- #

def daily_pulse() -> Dict[str, dict]:
    """Refresh the 3 predefined cohorts and dispatch the daily campaigns.

    Steps:
      1. Rebuild each predefined cohort (target_user_ids refreshed).
      2. Fire `lankatax_existing_clients` (variant 'a') if no member has
         been sent it in the last EXISTING_CLIENTS_REFIRE_DAYS days.
      3. Fire `lankatax_warm_partial` daily (highest-value upsell).
      4. Emit `lankatax_take_rate_computed` carrying per-campaign take-rate.

    Returns a small summary dict for the Celery result backend.
    """
    summary: Dict[str, dict] = {}

    # 1. Refresh cohorts
    cohorts: Dict[str, LankataxCohort] = {}
    for name, spec in PREDEFINED_COHORTS.items():
        cohort = build_cohort(
            name=name,
            sql_query=spec["sql_query"],
            description=spec.get("description"),
        )
        if cohort is not None:
            cohorts[name] = cohort
        summary[f"cohort:{name}"] = {
            "members": cohort.members_count if cohort else 0,
            "built_ok": cohort is not None,
        }

    # 2. lankatax_existing_clients — fire only if no recent send to any member
    existing = cohorts.get("lankatax_existing_clients")
    if existing is not None:
        campaign_key = "existing_clients_v1"
        if _cohort_needs_refire(existing, campaign_key, EXISTING_CLIENTS_REFIRE_DAYS):
            counts = run_campaign(
                cohort_id=existing.id,
                campaign_key=campaign_key,
                channel="email",
                variant="a",
            )
            summary[f"campaign:{campaign_key}"] = counts
        else:
            summary[f"campaign:{campaign_key}"] = {
                "skipped": "within refire window",
            }

    # 3. lankatax_warm_partial — fire daily (cooldown still applies per user)
    warm = cohorts.get("lankatax_warm_partial")
    if warm is not None:
        campaign_key = "warm_partial_v1"
        counts = run_campaign(
            cohort_id=warm.id,
            campaign_key=campaign_key,
            channel="email",
            variant="a",
        )
        summary[f"campaign:{campaign_key}"] = counts

    # 4. Take-rate for every campaign we know about
    take_rates = {}
    for campaign_key in ("existing_clients_v1", "warm_partial_v1", "dormant_30d_v1"):
        take_rates[campaign_key] = compute_take_rate(campaign_key)
    summary["take_rates"] = take_rates

    emit_event(
        "lankatax_take_rate_computed",
        payload=take_rates,
        source="cron:lankatax_crosssell.daily_pulse",
    )

    summary["ran_at"] = datetime.utcnow().isoformat()
    log.info("lankatax_crosssell.daily_pulse: %s", summary)
    return summary


# --------------------------------------------------------------------------- #
# Celery wiring — best-effort, mirrors ai_crm.py
# --------------------------------------------------------------------------- #

try:
    from celery_config import app as celery_app

    daily_pulse = celery_app.task(  # type: ignore[assignment]
        name="lankatax_crosssell.daily_pulse"
    )(daily_pulse)
except Exception as _e:
    log.debug("Celery wiring skipped (ok in tests/CLI): %s", _e)


__all__ = [
    "PREDEFINED_COHORTS",
    "COOLDOWN_DAYS",
    "TAKE_RATE_LOOKBACK_DAYS_DEFAULT",
    "MAX_RECIPIENTS_PER_RUN",
    "build_cohort",
    "run_campaign",
    "compute_take_rate",
    "daily_pulse",
]
