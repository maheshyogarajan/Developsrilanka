"""
Proactive Engagement Engine — Wave 3.1 (2026-05-18).

The proactive limb that reaches out to users at the right moment. Fed by the
AI CRM brain's `next_best_action` field + raw event evidence; channels are
SendGrid email + in-app banner ONLY.

CONSTRAINT (council #2, 2026-05-17): NO TELEGRAM. FIESTA has zero Telegram
integration. Telegram is a CEO-OS plane, not a FIESTA plane. Channels here
are strictly 'email' and 'in_app'.

DESIGN

  * Rule registry (NUDGE_RULES) is a module-level list. Each rule is a dict
    with trigger_condition (callable), channel, template_name, cooldown_hours,
    priority. New rules = append a dict.
  * evaluate_user(uid) returns the list of matching rule keys for a user
    (deduplicated against the per-rule cooldown — a recent nudge_sent event
    for that rule key suppresses re-matching).
  * dispatch_nudge(uid, rule_key) sends via the rule's channel(s) and emits
    a 'nudge_sent' event with structured payload.
  * run_engagement_pass() — the Celery beat task — loops over recently
    active users, evaluates each, and dispatches the SINGLE highest-priority
    matching rule (never spam: max one nudge per user per pass).
  * Best-effort throughout. NEVER raises into the caller; failures log and
    return False / [].

The orchestrator wires this into celery_config.app.conf.beat_schedule:

    'engagement_engine-run-pass-hourly': {
        'task': 'engagement_engine.run_pass',
        'schedule': crontab(minute=15),  # every hour at :15
    },

PUBLIC API

    from engagement_engine import (
        NUDGE_RULES,
        evaluate_user,
        dispatch_nudge,
        run_engagement_pass,
    )
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

from app import db

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Engagement pass loop bound: users with at least one event in this window.
ACTIVE_WINDOW_DAYS = 30

# SendGrid sender — reuses the verified address from app.send_invitation_email
# (info@developsrilanka.com is the SendGrid-verified domain for FIESTA).
SENDGRID_FROM_EMAIL = os.environ.get(
    "ENGAGEMENT_FROM_EMAIL", "info@developsrilanka.com"
)
SENDGRID_FROM_NAME = "FIESTA"

# Template directory — sibling of this module.
TEMPLATE_DIR = Path(__file__).resolve().parent / "engagement_templates"

# Default CTA URLs (overridable via env so non-prod environments can point at
# their own host without a code change).
DEFAULT_APP_URL = os.environ.get(
    "ENGAGEMENT_APP_URL", "https://fiesta-mvp.fly.dev"
).rstrip("/")


# --------------------------------------------------------------------------- #
# Helpers — utc clock, event lookups
# --------------------------------------------------------------------------- #

def _utcnow() -> datetime:
    """Hook for tests — kept as a function so tests can monkeypatch it."""
    return datetime.utcnow()


def _latest_event_at(user_id: int) -> Optional[datetime]:
    """Most recent Event.created_at for this user, or None."""
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


def _last_event_of_type(user_id: int, event_type: str) -> Optional[datetime]:
    """Most recent Event of a specific type for this user, or None."""
    try:
        from event_models import Event
        row = (
            Event.query
                 .filter(Event.user_id == user_id,
                         Event.event_type == event_type)
                 .order_by(Event.created_at.desc())
                 .first()
        )
        return row.created_at if row else None
    except Exception as e:
        log.warning("_last_event_of_type(%s, %s) failed: %s", user_id, event_type, e)
        return None


def _has_event_of_type(user_id: int, event_type: str) -> bool:
    """True if the user has ever had at least one Event of this type."""
    return _last_event_of_type(user_id, event_type) is not None


def _last_nudge_sent(user_id: int, rule_key: Optional[str] = None) -> Optional[datetime]:
    """Most recent nudge_sent Event for this user.

    If rule_key is given, only consider nudges with payload.rule_key matching.
    Used for per-rule cooldown enforcement and the global "any nudge in last
    Nh" guard some rules apply.
    """
    try:
        from event_models import Event
        q = (
            Event.query
                 .filter(Event.user_id == user_id,
                         Event.event_type == "nudge_sent")
                 .order_by(Event.created_at.desc())
        )
        if rule_key is None:
            row = q.first()
            return row.created_at if row else None
        # Per-rule cooldown — scan recent rows and match payload.rule_key.
        # Cap at 20 rows to bound the work; cooldown windows we use are <= 14d
        # so a user would have to be unusually nudge-heavy to exceed 20.
        for ev in q.limit(20).all():
            try:
                if (ev.payload or {}).get("rule_key") == rule_key:
                    return ev.created_at
            except Exception:
                # payload is JSON; if it's malformed, skip the row defensively.
                continue
        return None
    except Exception as e:
        log.warning("_last_nudge_sent(%s, %r) failed: %s", user_id, rule_key, e)
        return None


def _user_lifecycle_stage(user_id: int) -> Optional[str]:
    """The brain's current lifecycle_stage for this user, or None if no profile
    row exists yet. Cheap single-row lookup against customer_profiles."""
    try:
        from ai_crm import CustomerProfile
        row = (
            CustomerProfile.query
                          .filter(CustomerProfile.user_id == user_id)
                          .first()
        )
        return row.lifecycle_stage if row else None
    except Exception as e:
        log.warning("_user_lifecycle_stage(%s) failed: %s", user_id, e)
        return None


def _user_has_incomplete_remittance_age(user_id: int, min_age_hours: int) -> bool:
    """True if the user has at least one RemittanceEntry whose
    completeness_status() is 'partial' or 'missing' AND was created more than
    `min_age_hours` ago (we don't want to nudge for an upload they made
    20 minutes ago).
    """
    try:
        from remittance_models import RemittanceEntry
        cutoff = _utcnow() - timedelta(hours=min_age_hours)
        rows = (
            RemittanceEntry.query
                          .filter(RemittanceEntry.user_id == user_id,
                                  RemittanceEntry.created_at <= cutoff)
                          .all()
        )
        for r in rows:
            try:
                status, _label = r.completeness_status()
                if status in ("partial", "missing"):
                    return True
            except Exception:
                continue
        return False
    except Exception as e:
        log.warning(
            "_user_has_incomplete_remittance_age(%s, %sh) failed: %s",
            user_id, min_age_hours, e,
        )
        return False


def _user_email_and_name(user_id: int) -> tuple[Optional[str], str]:
    """(email, friendly_name). Falls back to ('', 'there') for missing data."""
    try:
        from models import User
        u = User.query.get(user_id)
        if not u:
            return None, "there"
        # Some legacy User rows have name=None; we render 'there' which reads
        # acceptably in a salutation ("Hi there,").
        first = (u.name or "").strip().split(" ", 1)[0] or "there"
        return u.email, first
    except Exception as e:
        log.warning("_user_email_and_name(%s) failed: %s", user_id, e)
        return None, "there"


# --------------------------------------------------------------------------- #
# Trigger conditions — each is a callable taking user_id, returning bool.
# Kept as named module-level functions (not lambdas) so the rule registry is
# introspectable and the trigger logic is unit-testable in isolation.
# --------------------------------------------------------------------------- #

def _trigger_inactive_3d(user_id: int) -> bool:
    """User's last event > 3d ago AND no nudge in last 5d AND lifecycle_stage='activated'."""
    last_event = _latest_event_at(user_id)
    if last_event is None:
        # Never had an event → no signal we have their attention to recapture.
        return False
    age = _utcnow() - last_event
    if age < timedelta(days=3):
        return False
    # Global "any nudge in last 5d" guard prevents stacking 3d + 7d for the
    # same user crossing both thresholds in one pass.
    last_nudge = _last_nudge_sent(user_id, rule_key=None)
    if last_nudge is not None and (_utcnow() - last_nudge) < timedelta(days=5):
        return False
    return _user_lifecycle_stage(user_id) == "activated"


def _trigger_inactive_7d(user_id: int) -> bool:
    """Same as inactive_3d but >= 7d AND no nudge in last 5d."""
    last_event = _latest_event_at(user_id)
    if last_event is None:
        return False
    age = _utcnow() - last_event
    if age < timedelta(days=7):
        return False
    last_nudge = _last_nudge_sent(user_id, rule_key=None)
    if last_nudge is not None and (_utcnow() - last_nudge) < timedelta(days=5):
        return False
    return _user_lifecycle_stage(user_id) == "activated"


def _trigger_inactive_14d(user_id: int) -> bool:
    """Same as inactive_7d but >= 14d. Same 5d global nudge cooldown."""
    last_event = _latest_event_at(user_id)
    if last_event is None:
        return False
    age = _utcnow() - last_event
    if age < timedelta(days=14):
        return False
    last_nudge = _last_nudge_sent(user_id, rule_key=None)
    if last_nudge is not None and (_utcnow() - last_nudge) < timedelta(days=5):
        return False
    return _user_lifecycle_stage(user_id) == "activated"


def _trigger_checkout_abandoned_30m(user_id: int) -> bool:
    """checkout_started > 30 min ago, no checkout_completed AFTER that start,
    and no nudge of this rule in last 24h."""
    started_at = _last_event_of_type(user_id, "checkout_started")
    if started_at is None:
        return False
    if (_utcnow() - started_at) < timedelta(minutes=30):
        return False
    completed_at = _last_event_of_type(user_id, "checkout_completed")
    # If completed AFTER the most recent start → not abandoned.
    if completed_at is not None and completed_at >= started_at:
        return False
    # Per-rule 24h cooldown — discounting one user every day is enough.
    last_this_rule = _last_nudge_sent(user_id, rule_key="checkout_abandoned_30m")
    if last_this_rule is not None and (_utcnow() - last_this_rule) < timedelta(hours=24):
        return False
    return True


def _trigger_missing_evidence(user_id: int) -> bool:
    """At least one RemittanceEntry with completeness 'partial' or 'missing'
    older than 48h, and no nudge of this rule in last 72h (3 days)."""
    if not _user_has_incomplete_remittance_age(user_id, min_age_hours=48):
        return False
    last_this_rule = _last_nudge_sent(user_id, rule_key="missing_evidence")
    if last_this_rule is not None and (_utcnow() - last_this_rule) < timedelta(hours=72):
        return False
    return True


def _trigger_persona_set_no_first_remittance(user_id: int) -> bool:
    """persona_set event > 24h ago, no remittance_added event ever, no nudge
    of this rule in last 72h."""
    persona_at = _last_event_of_type(user_id, "persona_set")
    if persona_at is None:
        return False
    if (_utcnow() - persona_at) < timedelta(hours=24):
        return False
    if _has_event_of_type(user_id, "remittance_added"):
        return False
    last_this_rule = _last_nudge_sent(
        user_id, rule_key="persona_set_no_first_remittance"
    )
    if last_this_rule is not None and (_utcnow() - last_this_rule) < timedelta(hours=72):
        return False
    return True


# --------------------------------------------------------------------------- #
# NUDGE_RULES — the canonical rule registry
# --------------------------------------------------------------------------- #
#
# Ordering convention: lower `priority` integer = more important. The pass
# dispatcher picks the LOWEST priority matching rule (i.e. most important).
# Tie-break is list order.
#
# Adding a new rule: write the trigger callable above + the template HTML in
# engagement_templates/ + append a dict here. NO db migration required.
# --------------------------------------------------------------------------- #

NUDGE_RULES: List[Dict] = [
    {
        "key": "missing_evidence",
        "name": "Remittance evidence gap",
        "trigger_condition": _trigger_missing_evidence,
        "channel": "email",
        "template_name": "missing_evidence",
        "cooldown_hours": 72,
        "priority": 10,
        "headline": "3 docs short of IRD-ready",
        "body": "One or more of your ledger entries is missing required evidence (source document, SL bank proof, or CBSL rate). Closing those gaps now keeps your reviewer's pass clean.",
        "cta_text": "Review what's missing",
        "cta_path": "/remittance/dashboard",
    },
    {
        "key": "checkout_abandoned_30m",
        "name": "Checkout abandonment",
        "trigger_condition": _trigger_checkout_abandoned_30m,
        "channel": "email",
        "template_name": "checkout_abandoned_30m",
        "cooldown_hours": 24,
        "priority": 20,
        "headline": "Finish your upgrade — 10% off",
        "body": "Your checkout was interrupted. Come back through the link below for 10% off your first year.",
        "cta_text": "Finish my upgrade",
        "cta_path": "/billing/checkout?promo=COMEBACK10",
    },
    {
        "key": "persona_set_no_first_remittance",
        "name": "Persona set but no first remittance",
        "trigger_condition": _trigger_persona_set_no_first_remittance,
        "channel": "in_app",
        "template_name": "persona_set_no_first_remittance",
        "cooldown_hours": 72,
        "priority": 30,
        "headline": "Ready to log your first inward remittance?",
        "body": "You're set up as an SL foreign-income earner. The next step is logging a single inward remittance — type it in or upload a bank statement.",
        "cta_text": "Log my first remittance",
        "cta_path": "/remittance/new",
    },
    {
        "key": "inactive_3d",
        "name": "Inactive 3 days",
        "trigger_condition": _trigger_inactive_3d,
        "channel": "both",
        "template_name": "inactive_3d",
        "cooldown_hours": 120,
        "priority": 40,
        "headline": "Your remittance ledger is waiting",
        "body": "Haven't seen you in 3 days. A quick check-in keeps your tax-year picture current.",
        "cta_text": "Open my ledger",
        "cta_path": "/remittance/dashboard",
    },
    {
        "key": "inactive_7d",
        "name": "Inactive 7 days",
        "trigger_condition": _trigger_inactive_7d,
        "channel": "both",
        "template_name": "inactive_7d",
        "cooldown_hours": 120,
        "priority": 50,
        "headline": "A week away — your ledger needs you",
        "body": "It's been a week. Foreign income earned during the gap still needs to land in your ledger; the longer you wait, the harder the reconstruction.",
        "cta_text": "Catch up on my ledger",
        "cta_path": "/remittance/dashboard",
    },
    {
        "key": "inactive_14d",
        "name": "Inactive 14 days — pre-archive warning",
        "trigger_condition": _trigger_inactive_14d,
        "channel": "both",
        "template_name": "inactive_14d",
        "cooldown_hours": 120,
        "priority": 60,
        "headline": "Two weeks — your ledger will archive at 30 days",
        "body": "Your ledger will be archived at the 30-day mark unless you log a remittance. Sixty seconds prevents the archive.",
        "cta_text": "Keep my ledger active",
        "cta_path": "/remittance/dashboard",
    },
]


def _rule_by_key(rule_key: str) -> Optional[Dict]:
    """Find a rule dict by its `key`. Returns None if not found."""
    for r in NUDGE_RULES:
        if r["key"] == rule_key:
            return r
    return None


# --------------------------------------------------------------------------- #
# evaluate_user — which rules match right now?
# --------------------------------------------------------------------------- #

def evaluate_user(user_id: int) -> List[str]:
    """Return the list of rule keys whose trigger_condition is True for this
    user, after cooldown filtering.

    Cooldown filtering is BOTH inside the trigger callable (per-rule cooldown
    against the same rule_key) AND, for the 'inactive_*' rules, a global "any
    nudge in last 5d" guard to avoid stacking 3d + 7d in the same pass.

    Returns rule keys in NUDGE_RULES list order. Caller (run_engagement_pass)
    is responsible for picking the highest-priority one (lowest `priority`
    integer); evaluate_user returns the full list because tests and ad-hoc
    debugging want to see EVERY matching rule.

    NEVER raises. Returns [] on any failure.
    """
    matches: List[str] = []
    for rule in NUDGE_RULES:
        try:
            if rule["trigger_condition"](user_id):
                matches.append(rule["key"])
        except Exception as e:
            log.warning(
                "evaluate_user(%s): rule %r trigger raised: %s",
                user_id, rule["key"], e,
            )
            continue
    return matches


# --------------------------------------------------------------------------- #
# Channel dispatchers
# --------------------------------------------------------------------------- #

def _render_template(template_name: str, user_name: str, cta_url: str,
                     custom_payload: Optional[dict] = None) -> Optional[str]:
    """Read engagement_templates/<template_name>.html and substitute the
    {{user_name}}, {{cta_url}}, and any {{custom_payload.*}} placeholders.

    Returns the rendered HTML string, or None on file read failure.

    Intentionally avoids Jinja2 to keep dependency surface tiny — these
    templates are simple {{var}} substitution. If we later need conditionals
    or loops, swap to render_template_string with the existing Jinja env.
    """
    try:
        path = TEMPLATE_DIR / f"{template_name}.html"
        html = path.read_text(encoding="utf-8")
        html = html.replace("{{user_name}}", user_name or "there")
        html = html.replace("{{cta_url}}", cta_url)
        # custom_payload substitution: {{key}} → str(value) for top-level keys
        if custom_payload:
            for k, v in custom_payload.items():
                html = html.replace("{{" + str(k) + "}}", str(v))
        return html
    except Exception as e:
        log.warning("_render_template(%r) failed: %s", template_name, e)
        return None


def _send_email_via_sendgrid(to_email: str, subject: str, html_content: str) -> bool:
    """Best-effort SendGrid send. Returns True on success.

    Mirrors the SendGrid usage in app.send_invitation_email (around line 3625):
    same SDK, same env var, same verified sender address. We DON'T reuse that
    function directly because it's tightly coupled to friend-invitation
    semantics (flash messages, template rendering with `sender` param,
    sendgrid_logger calls). The minimal path here is cleaner for a cron-driven
    nudge.

    Never raises into the caller.
    """
    api_key = os.environ.get("SENDGRID_API_KEY")
    if not api_key:
        log.warning(
            "_send_email_via_sendgrid: SENDGRID_API_KEY not set — skipping send to %s",
            to_email,
        )
        return False
    if not to_email:
        log.warning("_send_email_via_sendgrid: empty recipient — skipping")
        return False
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail

        message = Mail(
            from_email=SENDGRID_FROM_EMAIL,
            to_emails=to_email.strip().lower(),
            subject=subject,
            html_content=html_content,
        )
        # Friendly display name — same pattern as app.send_invitation_email.
        message.from_email.name = SENDGRID_FROM_NAME

        sg = SendGridAPIClient(api_key)
        resp = sg.send(message)
        status = getattr(resp, "status_code", None)
        if status and 200 <= status < 300:
            return True
        log.warning(
            "_send_email_via_sendgrid: unexpected status %r for %s",
            status, to_email,
        )
        return False
    except Exception as e:
        log.warning("_send_email_via_sendgrid(%s) failed: %s", to_email, e)
        return False


def _create_in_app_banner(user_id: int, rule: Dict, cta_url: str) -> Optional[int]:
    """Insert one InAppBanner row. Returns the new row id, or None on failure."""
    try:
        from engagement_models import InAppBanner
        row = InAppBanner(
            user_id=user_id,
            rule_key=rule["key"],
            headline=rule.get("headline", rule.get("name", rule["key"]))[:255],
            body=rule.get("body", ""),
            cta_text=rule.get("cta_text", "Open FIESTA")[:64],
            cta_url=cta_url[:512],
        )
        db.session.add(row)
        db.session.commit()
        return row.id
    except Exception as e:
        log.warning(
            "_create_in_app_banner(user=%s, rule=%r) failed: %s",
            user_id, rule.get("key"), e,
        )
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


# --------------------------------------------------------------------------- #
# dispatch_nudge — the public write path
# --------------------------------------------------------------------------- #

def dispatch_nudge(user_id: int, rule_key: str) -> bool:
    """Send the nudge via the rule's channel(s); emit a `nudge_sent` event;
    return True if at least one channel succeeded.

    The nudge_sent event carries:
        {
          "rule_key": "<rule_key>",
          "channel":  "email" | "in_app" | "both",
          "template": "<template_name>",
          "email_sent": bool,
          "banner_id": <int or null>,
        }

    NEVER raises.
    """
    rule = _rule_by_key(rule_key)
    if rule is None:
        log.warning("dispatch_nudge: unknown rule_key %r", rule_key)
        return False

    email, friendly_name = _user_email_and_name(user_id)
    cta_url = DEFAULT_APP_URL + rule.get("cta_path", "/")

    channel = rule.get("channel", "email")
    email_sent = False
    banner_id: Optional[int] = None

    if channel in ("email", "both"):
        if email:
            html = _render_template(
                template_name=rule["template_name"],
                user_name=friendly_name,
                cta_url=cta_url,
            )
            if html:
                subject = rule.get("headline", "A note from FIESTA")
                email_sent = _send_email_via_sendgrid(email, subject, html)
            else:
                log.warning(
                    "dispatch_nudge: render failed for rule=%r user=%s",
                    rule_key, user_id,
                )
        else:
            log.warning(
                "dispatch_nudge: no email on user %s — skipping email channel",
                user_id,
            )

    if channel in ("in_app", "both"):
        banner_id = _create_in_app_banner(user_id, rule, cta_url)

    # Emit the nudge_sent event regardless of partial success — observability
    # over both successful sends AND partial failures matters for tuning.
    try:
        from events import emit
        emit(
            event_type="nudge_sent",
            user_id=user_id,
            payload={
                "rule_key": rule_key,
                "channel": channel,
                "template": rule["template_name"],
                "email_sent": bool(email_sent),
                "banner_id": banner_id,
            },
            source="cron:engagement_engine",
        )
    except Exception as e:
        log.warning(
            "dispatch_nudge: emit nudge_sent failed for user=%s rule=%r: %s",
            user_id, rule_key, e,
        )

    # Success = at least one channel landed something.
    return bool(email_sent) or banner_id is not None


# --------------------------------------------------------------------------- #
# run_engagement_pass — the Celery beat task body
# --------------------------------------------------------------------------- #

def _active_user_ids(window_days: int = ACTIVE_WINDOW_DAYS) -> List[int]:
    """User ids with at least one event in the last `window_days`. Same
    pattern as ai_crm._active_user_ids — bounds the per-pass workload."""
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


def _pick_highest_priority(rule_keys: List[str]) -> Optional[str]:
    """Among `rule_keys`, return the one with the lowest `priority` integer.
    Ties broken by NUDGE_RULES list order (stable sort)."""
    if not rule_keys:
        return None
    rules = [r for r in NUDGE_RULES if r["key"] in rule_keys]
    if not rules:
        return None
    rules.sort(key=lambda r: r.get("priority", 999))
    return rules[0]["key"]


def run_engagement_pass() -> dict:
    """Loop over active users; for each, dispatch the SINGLE highest-priority
    matching rule (never more than one nudge per user per pass).

    Returns a summary dict for the Celery result backend / log line.
    NEVER raises.
    """
    user_ids = _active_user_ids()
    evaluated = 0
    dispatched = 0
    failed = 0
    skipped_no_match = 0

    for uid in user_ids:
        evaluated += 1
        try:
            matches = evaluate_user(uid)
            if not matches:
                skipped_no_match += 1
                continue
            chosen = _pick_highest_priority(matches)
            if chosen is None:
                skipped_no_match += 1
                continue
            ok = dispatch_nudge(uid, chosen)
            if ok:
                dispatched += 1
            else:
                failed += 1
        except Exception as e:
            log.warning("run_engagement_pass: user %s raised: %s", uid, e)
            failed += 1

    summary = {
        "active_users": len(user_ids),
        "evaluated": evaluated,
        "dispatched": dispatched,
        "failed": failed,
        "skipped_no_match": skipped_no_match,
        "ran_at": _utcnow().isoformat(),
    }
    log.info("run_engagement_pass: %s", summary)
    return summary


# --------------------------------------------------------------------------- #
# Celery wiring — best-effort. The plain function above stays callable from
# tests/CLI; Celery beat invokes the registered task by name.
# --------------------------------------------------------------------------- #

try:
    from celery_config import app as celery_app

    run_engagement_pass = celery_app.task(  # type: ignore[assignment]
        name="engagement_engine.run_pass"
    )(run_engagement_pass)
except Exception as _e:  # pragma: no cover — celery is always present in prod
    log.debug("Celery wiring skipped (ok in tests/CLI): %s", _e)


__all__ = [
    "NUDGE_RULES",
    "evaluate_user",
    "dispatch_nudge",
    "run_engagement_pass",
    "TEMPLATE_DIR",
]
