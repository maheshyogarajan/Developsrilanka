"""
yoy_nudges.py — Year-over-year retention nudge scheduler + dispatcher.

Tier D4 / C2 (2026-05-24).

Four nudges driven by Celery beat (see ``tasks/yoy_nudges_run.py`` + the
beat entries appended to ``celery_config.py``):

  1. apr_1_new_year          — fires once on Apr 1 each year. Audience:
                                 every user with at least one paid (active
                                 or expired) Subscription. Subject:
                                 "Let's get you ready for {next_year}".
  2. payment_deadline_30d    — fires once on Sep 1 each year. Audience:
                                 users with a currently-active Subscription
                                 for the current_sl_tax_year. Subject:
                                 30-days-to-pay reminder.
  3. filing_deadline_30d     — fires once on Oct 31 each year. Audience:
                                 same as payment_deadline_30d. Subject:
                                 30-days-to-file reminder.
  4. renewal_30d             — checked DAILY. Audience: users whose active
                                 Subscription expires within the next 30
                                 days. Subject: renewal with referral hook.

v1 scope (per task brief)
-------------------------
* Email send STUBBED via ``_send_stub`` — logs + ops_alerts.send_alert.
* NO SMS.
* NO smart suppression branching (e.g. "skip payment nudge if already
  paid this year"). Per-tax-year dedup via the UNIQUE dedup_key is the
  v1 dedup mechanism; smart suppression is v2.

All four scheduling functions are pure-by-default — they ONLY insert
YoYNudge rows with sent_at=NULL. A separate ``dispatch_pending()`` walks
the table and fires the actual sends (stubbed). The split makes testing
trivial (schedule + assert rows; dispatch + assert send_status).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# Nudge keys — keep in sync with templates/emails/yoy/<key>.html
NUDGE_APR_1 = "apr_1_new_year"
NUDGE_PAYMENT_DEADLINE = "payment_deadline_30d"
NUDGE_FILING_DEADLINE = "filing_deadline_30d"
NUDGE_RENEWAL = "renewal_30d"

ALL_NUDGE_KEYS = (
    NUDGE_APR_1,
    NUDGE_PAYMENT_DEADLINE,
    NUDGE_FILING_DEADLINE,
    NUDGE_RENEWAL,
)


# --------------------------------------------------------------------------- #
# Tax-year helpers — reuse the paywall helpers; if paywall isn't importable
# (test paths that stub the world), fall back to local impl.
# --------------------------------------------------------------------------- #

def _current_sl_tax_year(today: Optional[date] = None) -> str:
    try:
        from fiesta.paywall.models import current_sl_tax_year
        return current_sl_tax_year(today=today)
    except Exception:
        today = today or date.today()
        start_year = today.year if today.month >= 4 else today.year - 1
        return f"{start_year}/{str(start_year + 1)[-2:]}"


def _next_sl_tax_year(today: Optional[date] = None) -> str:
    """Tax year that STARTS on the next Apr 1 (vs the current one)."""
    today = today or date.today()
    cur = _current_sl_tax_year(today=today)
    start_year = int(cur.split("/")[0]) + 1
    return f"{start_year}/{str(start_year + 1)[-2:]}"


# --------------------------------------------------------------------------- #
# Storage helpers
# --------------------------------------------------------------------------- #

def _schedule_one(user_id: int, nudge_key: str, tax_year: str) -> Optional[int]:
    """Insert one YoYNudge row, idempotent on dedup_key. Returns new id, or
    None when already scheduled (or insert failed)."""
    from yoy_models import get_model
    YoYNudge = get_model()
    from app import db

    dedup_key = YoYNudge.make_dedup_key(user_id, nudge_key, tax_year)
    existing = YoYNudge.query.filter_by(dedup_key=dedup_key).first()
    if existing is not None:
        return None

    row = YoYNudge(
        user_id=user_id,
        nudge_key=nudge_key,
        tax_year=tax_year,
        scheduled_at=datetime.utcnow(),
        dedup_key=dedup_key,
        send_status="scheduled",
    )
    try:
        db.session.add(row)
        db.session.commit()
        return row.id
    except Exception as exc:
        log.warning(
            "yoy_nudges._schedule_one: insert failed user=%s key=%s year=%s "
            "(probably race on dedup_key): %s",
            user_id, nudge_key, tax_year, exc,
        )
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


# --------------------------------------------------------------------------- #
# Audience queries
# --------------------------------------------------------------------------- #

def _users_with_any_paid_subscription() -> list[int]:
    """Return distinct user_ids with at least one Subscription row (any
    status, any tax_year). Per v1 scope, the Apr 1 nudge goes to everyone
    who has ever paid — the dedup_key prevents same-year repeats."""
    try:
        from fiesta.paywall import get_models
        Subscription, _, _ = get_models()
        if Subscription is None:
            return []
        from app import db
        rows = db.session.query(Subscription.user_id).distinct().all()
        return [r[0] for r in rows if r[0] is not None]
    except Exception as exc:
        log.warning("yoy_nudges: paywall import failed (%s) — empty audience", exc)
        return []


def _users_with_active_current_year_subscription() -> list[int]:
    """Return distinct user_ids with an active Subscription for the
    current SL tax year."""
    try:
        from fiesta.paywall import get_models
        Subscription, _, _ = get_models()
        if Subscription is None:
            return []
        from app import db
        ty = _current_sl_tax_year()
        rows = (
            db.session.query(Subscription.user_id)
            .filter(Subscription.tax_year == ty)
            .filter(Subscription.status == "active")
            .filter(Subscription.expires_at > datetime.utcnow())
            .distinct()
            .all()
        )
        return [r[0] for r in rows if r[0] is not None]
    except Exception as exc:
        log.warning("yoy_nudges: audience query failed (%s)", exc)
        return []


def _users_with_subscription_expiring_within(days: int) -> list[tuple[int, str]]:
    """Return (user_id, tax_year) pairs whose active Subscription expires
    within ``days``. Tax year is the EXPIRING year (used as the
    dedup_key's tax_year segment so we only nudge once per expiry cycle)."""
    try:
        from fiesta.paywall import get_models
        Subscription, _, _ = get_models()
        if Subscription is None:
            return []
        from app import db
        now = datetime.utcnow()
        cutoff = now + timedelta(days=days)
        rows = (
            db.session.query(Subscription.user_id, Subscription.tax_year)
            .filter(Subscription.status == "active")
            .filter(Subscription.expires_at > now)
            .filter(Subscription.expires_at <= cutoff)
            .distinct()
            .all()
        )
        return [(r[0], r[1]) for r in rows if r[0] is not None]
    except Exception as exc:
        log.warning("yoy_nudges: renewal audience query failed (%s)", exc)
        return []


# --------------------------------------------------------------------------- #
# Public schedulers — called by Celery beat tasks
# --------------------------------------------------------------------------- #

def schedule_apr_1_nudges(today: Optional[date] = None) -> dict:
    """Schedule the Apr 1 "new tax year" nudge for every paid user.

    Tax_year passed is the year that JUST STARTED (i.e. the new one).
    """
    target_year = _next_sl_tax_year(today=today) if today and today.month < 4 \
        else _current_sl_tax_year(today=today)
    # On Apr 1, current_sl_tax_year already returns the new year, so the
    # branch above (today.month < 4) handles dev/manual invocations earlier
    # in the year. Default Apr 1 fire path uses current_sl_tax_year() = new year.

    audience = _users_with_any_paid_subscription()
    scheduled = 0
    skipped = 0
    for uid in audience:
        new_id = _schedule_one(uid, NUDGE_APR_1, target_year)
        if new_id is not None:
            scheduled += 1
        else:
            skipped += 1
    log.info(
        "yoy_nudges.schedule_apr_1_nudges: target_year=%s "
        "audience=%d scheduled=%d skipped=%d",
        target_year, len(audience), scheduled, skipped,
    )
    return {
        "nudge_key": NUDGE_APR_1,
        "target_year": target_year,
        "audience": len(audience),
        "scheduled": scheduled,
        "skipped": skipped,
    }


def schedule_payment_deadline_nudges(today: Optional[date] = None) -> dict:
    """Schedule the Sep 1 (30d before Sep 30) payment-deadline nudge.

    Audience: users with active current-year subscription.
    v1 placeholder: we don't yet check "tax actually owed" — assume yes;
    the dedup_key prevents re-fires.
    """
    target_year = _current_sl_tax_year(today=today)
    audience = _users_with_active_current_year_subscription()
    scheduled = 0
    skipped = 0
    for uid in audience:
        new_id = _schedule_one(uid, NUDGE_PAYMENT_DEADLINE, target_year)
        if new_id is not None:
            scheduled += 1
        else:
            skipped += 1
    log.info(
        "yoy_nudges.schedule_payment_deadline_nudges: target_year=%s "
        "audience=%d scheduled=%d skipped=%d",
        target_year, len(audience), scheduled, skipped,
    )
    return {
        "nudge_key": NUDGE_PAYMENT_DEADLINE,
        "target_year": target_year,
        "audience": len(audience),
        "scheduled": scheduled,
        "skipped": skipped,
    }


def schedule_filing_deadline_nudges(today: Optional[date] = None) -> dict:
    """Schedule the Oct 31 (30d before Nov 30) filing-deadline nudge.

    Same audience as payment deadline.
    """
    target_year = _current_sl_tax_year(today=today)
    audience = _users_with_active_current_year_subscription()
    scheduled = 0
    skipped = 0
    for uid in audience:
        new_id = _schedule_one(uid, NUDGE_FILING_DEADLINE, target_year)
        if new_id is not None:
            scheduled += 1
        else:
            skipped += 1
    log.info(
        "yoy_nudges.schedule_filing_deadline_nudges: target_year=%s "
        "audience=%d scheduled=%d skipped=%d",
        target_year, len(audience), scheduled, skipped,
    )
    return {
        "nudge_key": NUDGE_FILING_DEADLINE,
        "target_year": target_year,
        "audience": len(audience),
        "scheduled": scheduled,
        "skipped": skipped,
    }


def schedule_renewal_nudges(today: Optional[date] = None) -> dict:
    """Schedule the renewal nudge for users whose subscription expires
    within 30 days. Runs DAILY; the dedup_key (per expiring-tax-year)
    means each user gets at most one renewal nudge per expiry cycle.
    """
    pairs = _users_with_subscription_expiring_within(30)
    scheduled = 0
    skipped = 0
    for uid, tax_year in pairs:
        new_id = _schedule_one(uid, NUDGE_RENEWAL, tax_year)
        if new_id is not None:
            scheduled += 1
        else:
            skipped += 1
    log.info(
        "yoy_nudges.schedule_renewal_nudges: audience=%d scheduled=%d skipped=%d",
        len(pairs), scheduled, skipped,
    )
    return {
        "nudge_key": NUDGE_RENEWAL,
        "audience": len(pairs),
        "scheduled": scheduled,
        "skipped": skipped,
    }


# --------------------------------------------------------------------------- #
# Dispatcher — walks pending rows and fires stubbed sends.
# --------------------------------------------------------------------------- #

def _send_stub(user_id: int, nudge_key: str, tax_year: str) -> bool:
    """Stubbed email send. Logs + (best-effort) Telegram ops_alert.

    Always returns True for v1 — caller marks the row as 'stubbed'. When
    a real SendGrid path is wired (parallel A5 work or v2), swap this
    out for the live impl in ``dispatch_pending``.
    """
    log.info(
        "yoy_nudges._send_stub: user=%s key=%s year=%s — STUBBED send "
        "(no email actually dispatched in v1)",
        user_id, nudge_key, tax_year,
    )
    try:
        from ops_alerts import send_alert
        send_alert(
            severity="INFO",
            title=f"yoy_nudge stub fired: {nudge_key}",
            body=(
                f"YoY nudge (v1 stub) — user_id={user_id} "
                f"nudge={nudge_key} tax_year={tax_year}. No email sent."
            ),
            data={
                "user_id": user_id,
                "nudge_key": nudge_key,
                "tax_year": tax_year,
            },
        )
    except Exception as exc:
        log.debug("yoy_nudges._send_stub: ops_alerts unavailable: %s", exc)
    return True


def dispatch_pending(limit: int = 500) -> dict:
    """Walk up to ``limit`` rows with sent_at=NULL and fire the stub send.

    Returns a {sent, errors} summary.
    """
    from yoy_models import get_model
    YoYNudge = get_model()
    from app import db

    sent = 0
    errors = 0
    rows = (
        YoYNudge.query
        .filter(YoYNudge.sent_at.is_(None))
        .filter(YoYNudge.send_status == "scheduled")
        .limit(limit)
        .all()
    )
    for row in rows:
        try:
            ok = _send_stub(row.user_id, row.nudge_key, row.tax_year)
            row.sent_at = datetime.utcnow()
            row.send_status = "stubbed" if ok else "failed"
            if not ok:
                row.send_error = "stub returned False"
                errors += 1
            else:
                sent += 1
            db.session.commit()
        except Exception as exc:
            errors += 1
            log.warning(
                "yoy_nudges.dispatch_pending: row id=%s send failed: %s",
                row.id, exc,
            )
            try:
                row.send_status = "failed"
                row.send_error = str(exc)[:500]
                db.session.commit()
            except Exception:
                db.session.rollback()
    log.info(
        "yoy_nudges.dispatch_pending: examined=%d sent=%d errors=%d",
        len(rows), sent, errors,
    )
    return {"examined": len(rows), "sent": sent, "errors": errors}


__all__ = [
    "NUDGE_APR_1",
    "NUDGE_PAYMENT_DEADLINE",
    "NUDGE_FILING_DEADLINE",
    "NUDGE_RENEWAL",
    "ALL_NUDGE_KEYS",
    "schedule_apr_1_nudges",
    "schedule_payment_deadline_nudges",
    "schedule_filing_deadline_nudges",
    "schedule_renewal_nudges",
    "dispatch_pending",
]
