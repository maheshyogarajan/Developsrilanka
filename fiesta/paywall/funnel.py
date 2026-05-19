"""
fiesta.paywall.funnel — paywall funnel analytics.

Two surfaces:

  * ``funnel_summary(user_id)`` -> per-user paywall metrics, surfaced in the
    /admin/funnel admin route (v1.1 placeholder; v1 reads via API only).

  * ``funnel_daily()`` -> system-wide aggregate for the last N days.

Both functions read the canonical ``paywall_event`` + ``paywall_subscription``
tables. They never raise; on any DB failure they return an empty/zeroed dict
so admin views degrade gracefully.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)


def _safe_query():
    """Return (Subscription, PaywallEvent, db) or (None, None, None) on import
    failure. Centralises the lazy import so every function can call it."""
    try:
        from .models import Subscription, PaywallEvent
        from app import db
        return Subscription, PaywallEvent, db
    except Exception as exc:
        log.warning("paywall.funnel: model import failed: %s", exc)
        return None, None, None


def funnel_summary(user_id: int) -> dict:
    """Per-user paywall metrics. Returns:

        {
            "user_id": int,
            "paywall_fired_count": int,
            "conversions": int,
            "conversion_rate": float (0.0-1.0),
            "first_fire_at": iso8601 | None,
            "last_fire_at": iso8601 | None,
            "days_since_first_fire": int | None,
            "screen_id_with_most_fires": str | None,
            "screen_fire_breakdown": dict[str, int],
        }
    """
    Subscription, PaywallEvent, db = _safe_query()
    out = {
        "user_id": user_id,
        "paywall_fired_count": 0,
        "conversions": 0,
        "conversion_rate": 0.0,
        "first_fire_at": None,
        "last_fire_at": None,
        "days_since_first_fire": None,
        "screen_id_with_most_fires": None,
        "screen_fire_breakdown": {},
    }
    if PaywallEvent is None:
        return out

    try:
        rows = (
            PaywallEvent.query
            .filter(PaywallEvent.user_id == user_id)
            .order_by(PaywallEvent.fired_at.asc())
            .all()
        )
        if not rows:
            return out

        out["paywall_fired_count"] = len(rows)
        out["conversions"] = sum(1 for r in rows if r.converted_at is not None)
        out["conversion_rate"] = (
            round(out["conversions"] / out["paywall_fired_count"], 4)
            if out["paywall_fired_count"]
            else 0.0
        )
        first = rows[0].fired_at
        last = rows[-1].fired_at
        out["first_fire_at"] = first.isoformat() if first else None
        out["last_fire_at"] = last.isoformat() if last else None
        if first:
            out["days_since_first_fire"] = (datetime.utcnow() - first).days

        screens = Counter(r.screen_id for r in rows)
        out["screen_fire_breakdown"] = dict(screens)
        if screens:
            out["screen_id_with_most_fires"] = screens.most_common(1)[0][0]
    except Exception as exc:
        log.warning("funnel_summary failed for user_id=%s: %s", user_id, exc)
    return out


def funnel_daily(days: int = 30) -> dict:
    """System-wide funnel metrics over the last ``days`` days.

    Returns:

        {
            "days": int,
            "total_paywall_fires": int,
            "total_conversions": int,
            "conversion_rate": float,
            "average_time_to_conversion_hours": float | None,
            "fires_by_screen": dict[str, int],
            "fires_by_day": dict[YYYY-MM-DD, int],
        }
    """
    Subscription, PaywallEvent, db = _safe_query()
    out = {
        "days": days,
        "total_paywall_fires": 0,
        "total_conversions": 0,
        "conversion_rate": 0.0,
        "average_time_to_conversion_hours": None,
        "fires_by_screen": {},
        "fires_by_day": {},
    }
    if PaywallEvent is None:
        return out

    try:
        since = datetime.utcnow() - timedelta(days=days)
        rows = (
            PaywallEvent.query
            .filter(PaywallEvent.fired_at >= since)
            .all()
        )
        if not rows:
            return out

        out["total_paywall_fires"] = len(rows)
        out["total_conversions"] = sum(
            1 for r in rows if r.converted_at is not None
        )
        out["conversion_rate"] = (
            round(out["total_conversions"] / out["total_paywall_fires"], 4)
            if out["total_paywall_fires"]
            else 0.0
        )

        screens: Counter = Counter()
        days_c: Counter = Counter()
        deltas = []
        for r in rows:
            screens[r.screen_id] += 1
            days_c[r.fired_at.date().isoformat()] += 1
            if r.converted_at and r.fired_at:
                deltas.append(
                    (r.converted_at - r.fired_at).total_seconds() / 3600.0
                )
        out["fires_by_screen"] = dict(screens)
        out["fires_by_day"] = dict(days_c)
        if deltas:
            out["average_time_to_conversion_hours"] = round(
                sum(deltas) / len(deltas), 2
            )
    except Exception as exc:
        log.warning("funnel_daily failed: %s", exc)
    return out


__all__ = ["funnel_summary", "funnel_daily"]
