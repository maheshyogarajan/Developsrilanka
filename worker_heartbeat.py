"""
Worker heartbeat — silent-failure detector (v18.3, 2026-05-18).

A periodic check that fires every 30 min. If NO AI-org Celery task has
succeeded in the last 60 min, send a Telegram alert to the CEO chat
(1813046950) via the bot token from Fly secrets or local .env.

This catches the silent-failure pattern we hit 2026-05-15 → 2026-05-18:
worker process up, beat scheduler scheduling, every task silently failing
on `Working outside of application context` + ModuleNotFoundError.
The substrate looked clean and we lost 3 days before discovering it.

Activity is sampled from the latest of three "real work happened" signals:
  - attribution_ledger.created_at  (Subagent B writes one per matched event)
  - reputation_event.occurred_at   (any subagent emits these)
  - ai_org.last_score_computed_at  (Subagent C nightly)

If the freshest is older than 60 min outside the quiet window (22:00-07:00
UTC), Telegram fires. Inside the quiet window we log only — no alerts at
night.

Telegram is sent via raw POST so we don't drag heavy MCP into the worker.
Bot token resolution order:
  1. TELEGRAM_BOT_TOKEN env var (Fly secret in prod)
  2. /etc/secrets/TELEGRAM_BOT_TOKEN file (Fly secrets file mount)
  3. ~/.claude/channels/telegram/.env (local dev, canonical plugin store)
If none resolve, log loudly and skip — DO NOT crash the worker.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


CEO_CHAT_ID = "1813046950"
QUIET_WINDOW_START_UTC = dt_time(22, 0)  # 22:00 UTC
QUIET_WINDOW_END_UTC = dt_time(7, 0)     # 07:00 UTC
STALE_THRESHOLD_MINUTES = 60


# --------------------------------------------------------------------------- #
# Token resolution
# --------------------------------------------------------------------------- #

def _resolve_telegram_token() -> Optional[str]:
    """Resolve the Telegram bot token from the first source that yields a
    non-empty string. Returns None if nothing resolves — caller logs + skips.
    """
    # 1. Env var (Fly secret)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token.strip()

    # 2. Fly secrets file mount
    fly_path = Path("/etc/secrets/TELEGRAM_BOT_TOKEN")
    try:
        if fly_path.exists():
            content = fly_path.read_text(encoding="utf-8").strip()
            if content:
                return content
    except Exception as e:
        log.debug(f"_resolve_telegram_token: /etc/secrets read failed: {e}")

    # 3. Local plugin store (~/.claude/channels/telegram/.env)
    home_env = Path.home() / ".claude" / "channels" / "telegram" / ".env"
    try:
        if home_env.exists():
            for line in home_env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "TELEGRAM_BOT_TOKEN":
                    return v.strip().strip('"').strip("'")
    except Exception as e:
        log.debug(f"_resolve_telegram_token: home .env read failed: {e}")

    return None


# --------------------------------------------------------------------------- #
# Activity probe — latest "real work happened" timestamp
# --------------------------------------------------------------------------- #

def _latest_activity_minutes_ago() -> Optional[float]:
    """Return how many minutes have elapsed since the freshest AI-org activity
    timestamp across attribution_ledger, reputation_event, and
    ai_org.last_score_computed_at. Returns None if no rows exist anywhere
    (fresh DB / brand-new deploy — caller treats this as "don't alert yet").
    """
    try:
        from app import db
        from sqlalchemy import text as sql_text

        # Single round-trip query — MAX across the three sources. NULLs on
        # any individual source are tolerated (GREATEST drops NULLs in PG
        # only when wrapped in COALESCE, so use a UNION ALL + MAX instead).
        row = db.session.execute(sql_text(
            """
            SELECT MAX(ts) AS latest FROM (
                SELECT MAX(created_at) AS ts FROM attribution_ledger
                UNION ALL
                SELECT MAX(occurred_at) AS ts FROM reputation_event
                UNION ALL
                SELECT MAX(last_score_computed_at) AS ts FROM ai_org
            ) sources
            """
        )).fetchone()
        latest = row[0] if row else None
    except Exception as e:
        log.warning(f"_latest_activity_minutes_ago: query failed: {e}")
        return None

    if latest is None:
        return None

    # Postgres returns naive datetime in UTC for these columns; compare in UTC.
    now = datetime.utcnow()
    delta = now - latest
    return delta.total_seconds() / 60.0


# --------------------------------------------------------------------------- #
# Quiet-window check
# --------------------------------------------------------------------------- #

def _in_quiet_window(now: Optional[datetime] = None) -> bool:
    """Return True if `now` (UTC) falls in 22:00-07:00 UTC. The CEO is not
    woken inside this window — we log instead.
    """
    now = now or datetime.utcnow()
    t = now.time()
    # The window wraps midnight, so it's "before end OR after start".
    return t >= QUIET_WINDOW_START_UTC or t < QUIET_WINDOW_END_UTC


# --------------------------------------------------------------------------- #
# Telegram raw send
# --------------------------------------------------------------------------- #

def _send_telegram(text: str) -> bool:
    """POST to https://api.telegram.org/bot<TOKEN>/sendMessage. Returns True
    on 200, False on any failure (logged). NEVER raises.
    """
    token = _resolve_telegram_token()
    if not token:
        log.error(
            "worker_heartbeat: TELEGRAM_BOT_TOKEN not resolvable from env / "
            "/etc/secrets / ~/.claude — alert skipped (token missing)"
        )
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": CEO_CHAT_ID,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = resp.status == 200
            if not ok:
                log.warning(
                    f"worker_heartbeat: Telegram returned status={resp.status}"
                )
            return ok
    except Exception as e:
        log.error(f"worker_heartbeat: Telegram POST failed: {e}")
        return False


# --------------------------------------------------------------------------- #
# Public entrypoint — also wrapped as a Celery task below
# --------------------------------------------------------------------------- #

def check_and_alert_impl() -> dict:
    """Core logic. Returns a status dict for logging / testability. Never
    raises. Telegram-on-error is best-effort.
    """
    summary = {
        "checked_at": datetime.utcnow().isoformat(),
        "in_quiet_window": _in_quiet_window(),
        "minutes_since_activity": None,
        "alert_sent": False,
        "skipped_reason": None,
    }
    minutes_ago = _latest_activity_minutes_ago()
    summary["minutes_since_activity"] = minutes_ago

    if minutes_ago is None:
        # Brand-new DB or query failure — don't alert. We'd rather have a
        # missed alert than wake the CEO during a fresh-DB seed window.
        summary["skipped_reason"] = "no_activity_data_yet"
        log.info(f"worker_heartbeat: skipped — {summary['skipped_reason']}")
        return summary

    if minutes_ago < STALE_THRESHOLD_MINUTES:
        summary["skipped_reason"] = "activity_within_threshold"
        log.info(
            f"worker_heartbeat: OK — latest activity {minutes_ago:.1f} min "
            f"ago (threshold {STALE_THRESHOLD_MINUTES})"
        )
        return summary

    if summary["in_quiet_window"]:
        summary["skipped_reason"] = "quiet_window_22_07_utc"
        log.warning(
            f"worker_heartbeat: STALE but quiet — latest activity "
            f"{minutes_ago:.1f} min ago, suppressed by quiet window"
        )
        return summary

    # We're stale AND outside the quiet window. Fire the alert.
    alert_text = (
        "⚠ FIESTA AI-org worker heartbeat alert\n\n"
        f"Latest AI-org activity: {minutes_ago:.0f} minutes ago "
        f"(threshold {STALE_THRESHOLD_MINUTES} min).\n"
        "Expected: at least one task succeeded in the last 60 min.\n\n"
        "Suspects:\n"
        "  - Worker process down (check `flyctl status --app fiesta-mvp`)\n"
        "  - Beat scheduler down\n"
        "  - All tasks failing (check `flyctl logs --app fiesta-mvp | grep -i error`)\n"
        "  - DB unreachable from worker\n\n"
        "Investigate immediately."
    )
    sent = _send_telegram(alert_text)
    summary["alert_sent"] = sent
    return summary


# --------------------------------------------------------------------------- #
# Celery task wiring
# --------------------------------------------------------------------------- #

try:
    from celery_config import app as celery_app

    @celery_app.task(name="worker_heartbeat.check_and_alert")
    def check_and_alert() -> dict:
        """Celery task — the v18.3 beat schedule fires this every 30 min."""
        return check_and_alert_impl()
except Exception:
    # Celery not importable in test/CLI contexts — check_and_alert_impl is
    # still callable directly.
    check_and_alert = None  # type: ignore


__all__ = [
    "check_and_alert",
    "check_and_alert_impl",
    "_latest_activity_minutes_ago",
    "_in_quiet_window",
    "_resolve_telegram_token",
]
