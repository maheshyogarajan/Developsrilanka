"""
Tier C / Wave A — Analytics dashboard (admin-only) — 2026-05-24.

A thin read-only HTML view over the funnel data the client beacon
(`POST /api/event`) writes into `public.events`. Designed for CEO to
measure the four launch acquisition channels (Lanka Devs Discord/forum,
SL Freelancers FB, Fiverr SL FB, IT Twitter X) without spinning up an
external analytics SaaS.

Scope (council-binding):
  * Bare HTML tables. No JS charting library.
  * Filters: date range + channel ONLY. No per-event drilldown,
    no slice-and-dice cube.
  * Read-only. No write ops.
  * Admin-gated (reuses `fiesta.auth.decorators.admin_required` — the
    same gate every other Wave-6 admin page uses).

Routes registered:
  * GET  /admin/analytics             — the dashboard HTML
  * GET  /admin/analytics/export      — CSV download of the underlying
                                        data (same date-range + channel
                                        filters as the HTML view)

The SQL behind every card mirrors `_tier_c_analytics_sql_pack/` —
keeping the two co-evolving is a deliberate choice so CEO can drop
into `flyctl ssh console` and reproduce the dashboard number from
the command line.

Anon-id resolution:
  Both the queries below and the SQL pack use
      COALESCE(events.session_anon_id, payload->>'session_anon_id')
  because the top-level column was only promoted in Tier C2 (2026-05-24)
  and pre-promotion rows still carry it inside the JSON payload only.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from flask import (
    Blueprint,
    Flask,
    Response,
    render_template,
    request,
)
from sqlalchemy import text

from fiesta.auth.decorators import admin_required

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Blueprint
# --------------------------------------------------------------------------- #
analytics_dashboard_bp = Blueprint(
    "analytics_dashboard",
    __name__,
    template_folder="templates",
)


# Funnel order — used by the dashboard cards. Kept in one place so we don't
# accidentally diverge between routes and template.
FUNNEL_EVENTS = [
    "landing_view",
    "cta_click",
    "signup_started",
    "signup_completed",
    "tax_bill_view",
    "audit_view",
    "evidence_uploaded",
    "payment_started",
    "payment_completed",
]

# Date-range presets the dropdown offers. Maps a code -> (label, days).
# `all` is special-cased in `_resolve_range` so we don't accumulate a
# 36500-day filter on the WHERE clause.
RANGE_OPTIONS: List[Tuple[str, str, Optional[int]]] = [
    ("7d", "Last 7 days", 7),
    ("30d", "Last 30 days", 30),
    ("90d", "Last 90 days", 90),
    ("all", "All time", None),
]
DEFAULT_RANGE = "7d"


# --------------------------------------------------------------------------- #
# Filter helpers
# --------------------------------------------------------------------------- #
def _resolve_range(range_code: Optional[str]) -> Tuple[str, Optional[datetime]]:
    """Return (canonical_code, since_datetime_or_None) for the requested
    range. Falls back to DEFAULT_RANGE on any invalid input."""
    if not range_code:
        range_code = DEFAULT_RANGE
    valid = {code for (code, _label, _days) in RANGE_OPTIONS}
    if range_code not in valid:
        range_code = DEFAULT_RANGE
    days = next(d for (code, _, d) in RANGE_OPTIONS if code == range_code)
    if days is None:
        return range_code, None
    return range_code, datetime.utcnow() - timedelta(days=days)


def _resolve_channel(raw: Optional[str]) -> str:
    """Return the cleaned channel filter or 'all' if absent/blank.
    No allowlist — channels are user-supplied via UTM and we don't want
    to drop a real channel just because the operator typo'd it. The HTML
    dropdown is the convenience path; the URL accepts any value."""
    if not raw:
        return "all"
    s = raw.strip()
    if not s:
        return "all"
    return s


# --------------------------------------------------------------------------- #
# Queries — same shape as the SQL pack, parameterised for the date filter.
# --------------------------------------------------------------------------- #
#
# We use raw SQL with bound params rather than ORM expressions because:
#   1. The SQL pack is the operator-facing spec; the dashboard mirrors it
#      verbatim so divergence is impossible.
#   2. The Postgres JSON `->>` operator + regexp_replace channel extraction
#      is awkward through the ORM. Raw SQL is cleaner.
#   3. We never accept user-supplied SQL fragments — only bound params.
# --------------------------------------------------------------------------- #

_CHANNEL_EXPR = """
    COALESCE(
        NULLIF(payload->>'utm_source', ''),
        NULLIF(
            regexp_replace(payload->>'client_referrer',
                           '^https?://([^/]+).*$', '\\1'),
            ''
        ),
        'direct'
    )
"""

_ANON_EXPR = "COALESCE(events.session_anon_id, events.payload->>'session_anon_id')"


def _funnel_overview(since: Optional[datetime], channel: str) -> List[Dict[str, Any]]:
    """One row per funnel event_type with a `count` for the window.

    Returns a list of dicts so the template can iterate without ORM concerns.
    Channel `all` skips the channel filter (saves the WHERE on the hot path).
    """
    from app import db

    sql_parts = ["SELECT event_type, COUNT(*) AS count FROM events"]
    where = ["event_type = ANY(:funnel_events)"]
    params: Dict[str, Any] = {"funnel_events": FUNNEL_EVENTS}

    if since is not None:
        where.append("created_at >= :since")
        params["since"] = since
    if channel != "all":
        where.append(f"{_CHANNEL_EXPR} = :channel")
        params["channel"] = channel

    sql_parts.append("WHERE " + " AND ".join(where))
    sql_parts.append("GROUP BY event_type")
    sql_parts.append("ORDER BY count DESC, event_type")
    sql = " ".join(sql_parts)

    rows = db.session.execute(text(sql), params).fetchall()
    by_type = {r.event_type: r.count for r in rows}

    # Always return every funnel event in canonical order so the card layout
    # is stable even when a step has zero rows.
    return [
        {"event_type": ev, "count": int(by_type.get(ev, 0))}
        for ev in FUNNEL_EVENTS
    ]


def _per_channel_breakout(since: Optional[datetime], channel: str) -> Dict[str, Any]:
    """Return the channel x event matrix for the window.

    Output shape:
        {
            "channels": ["lanka_devs", "fb_freelancers", "direct", ...],
            "rows": [
                {"event_type": "landing_view",
                 "by_channel": {"lanka_devs": 12, "fb_freelancers": 7, ...}},
                ...
            ],
            "totals_by_channel": {"lanka_devs": 47, ...},
        }
    """
    from app import db

    where = ["event_type = ANY(:funnel_events)"]
    params: Dict[str, Any] = {"funnel_events": FUNNEL_EVENTS}
    if since is not None:
        where.append("created_at >= :since")
        params["since"] = since
    if channel != "all":
        where.append(f"{_CHANNEL_EXPR} = :channel")
        params["channel"] = channel

    sql = f"""
        SELECT
            {_CHANNEL_EXPR}    AS channel,
            event_type,
            COUNT(*)            AS count
        FROM   events
        WHERE  {" AND ".join(where)}
        GROUP  BY channel, event_type
        ORDER  BY channel, event_type
    """
    rows = db.session.execute(text(sql), params).fetchall()

    # Build the matrix + channel ordering by total count desc.
    by_channel: Dict[str, Dict[str, int]] = {}
    totals: Dict[str, int] = {}
    for r in rows:
        by_channel.setdefault(r.channel, {})[r.event_type] = int(r.count)
        totals[r.channel] = totals.get(r.channel, 0) + int(r.count)

    sorted_channels = sorted(by_channel.keys(), key=lambda c: (-totals[c], c))

    matrix_rows = []
    for ev in FUNNEL_EVENTS:
        matrix_rows.append({
            "event_type": ev,
            "by_channel": {c: int(by_channel.get(c, {}).get(ev, 0))
                           for c in sorted_channels},
        })

    return {
        "channels": sorted_channels,
        "rows": matrix_rows,
        "totals_by_channel": {c: totals[c] for c in sorted_channels},
    }


def _conversion_per_channel(since: Optional[datetime], channel: str) -> List[Dict[str, Any]]:
    """For each channel, count DISTINCT anon visitors at each milestone +
    conversion % from landing -> signup-completed -> payment-completed."""
    from app import db

    where = ["event_type = ANY(:funnel_events)"]
    params: Dict[str, Any] = {
        "funnel_events": [
            "landing_view",
            "signup_started",
            "signup_completed",
            "payment_started",
            "payment_completed",
        ],
    }
    if since is not None:
        where.append("created_at >= :since")
        params["since"] = since
    if channel != "all":
        where.append(f"{_CHANNEL_EXPR} = :channel")
        params["channel"] = channel

    sql = f"""
        WITH event_with_channel AS (
            SELECT
                event_type,
                {_ANON_EXPR} AS anon_id,
                {_CHANNEL_EXPR} AS channel
            FROM events
            WHERE {" AND ".join(where)}
        )
        SELECT
            channel,
            COUNT(DISTINCT anon_id) FILTER (WHERE event_type = 'landing_view')      AS landed,
            COUNT(DISTINCT anon_id) FILTER (WHERE event_type = 'signup_started')    AS signup_started,
            COUNT(DISTINCT anon_id) FILTER (WHERE event_type = 'signup_completed')  AS signup_completed,
            COUNT(DISTINCT anon_id) FILTER (WHERE event_type = 'payment_started')   AS payment_started,
            COUNT(DISTINCT anon_id) FILTER (WHERE event_type = 'payment_completed') AS payment_completed
        FROM   event_with_channel
        GROUP  BY channel
        ORDER  BY landed DESC NULLS LAST, channel
    """
    rows = db.session.execute(text(sql), params).fetchall()

    out = []
    for r in rows:
        landed = int(r.landed or 0)
        signup_done = int(r.signup_completed or 0)
        paid = int(r.payment_completed or 0)
        pct = lambda numer, denom: (
            round(100.0 * numer / denom, 2) if denom else None
        )
        out.append({
            "channel": r.channel,
            "landed": landed,
            "signup_started": int(r.signup_started or 0),
            "signup_completed": signup_done,
            "payment_started": int(r.payment_started or 0),
            "payment_completed": paid,
            "pct_landing_to_signup": pct(signup_done, landed),
            "pct_signup_to_payment": pct(paid, signup_done),
            "pct_landing_to_payment": pct(paid, landed),
        })
    return out


def _new_users_per_day(since: Optional[datetime], channel: str) -> List[Dict[str, Any]]:
    """Distinct anon visitors per day. If `since` is None ("all time") we
    still cap at the last 30 days to keep the table sane — the SQL pack
    has the unbounded query for operators who want it."""
    from app import db

    effective_since = since if since is not None else datetime.utcnow() - timedelta(days=30)
    where = ["created_at >= :since"]
    params: Dict[str, Any] = {"since": effective_since}
    if channel != "all":
        where.append(f"{_CHANNEL_EXPR} = :channel")
        params["channel"] = channel

    sql = f"""
        WITH per_day AS (
            SELECT
                date_trunc('day', created_at) AS day,
                {_ANON_EXPR}                  AS anon_id
            FROM events
            WHERE {" AND ".join(where)}
        )
        SELECT
            day::date              AS day,
            COUNT(DISTINCT anon_id) AS distinct_visitors
        FROM   per_day
        WHERE  anon_id IS NOT NULL
        GROUP  BY day
        ORDER  BY day DESC
        LIMIT 60
    """
    rows = db.session.execute(text(sql), params).fetchall()
    return [
        {"day": r.day.isoformat() if r.day else None,
         "distinct_visitors": int(r.distinct_visitors or 0)}
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# View — /admin/analytics
# --------------------------------------------------------------------------- #
@analytics_dashboard_bp.route("/admin/analytics", methods=["GET"])
@admin_required
def analytics_dashboard():
    range_code, since = _resolve_range(request.args.get("range"))
    channel = _resolve_channel(request.args.get("channel"))

    try:
        funnel = _funnel_overview(since, channel)
        matrix = _per_channel_breakout(since, channel)
        conversions = _conversion_per_channel(since, channel)
        new_users = _new_users_per_day(since, channel)
        error_message = None
    except Exception as exc:  # pragma: no cover — defensive
        log.exception("analytics_dashboard query failed: %s", exc)
        funnel = []
        matrix = {"channels": [], "rows": [], "totals_by_channel": {}}
        conversions = []
        new_users = []
        error_message = str(exc)

    return render_template(
        "admin/analytics/index.html",
        range_code=range_code,
        range_options=RANGE_OPTIONS,
        channel=channel,
        # Pre-compute the channel dropdown options: 'all' + every channel
        # we saw in this window (matrix.channels). Operators can also type
        # a value directly via the URL.
        channel_options=["all"] + list(matrix["channels"]),
        funnel=funnel,
        matrix=matrix,
        conversions=conversions,
        new_users=new_users,
        error_message=error_message,
    )


# --------------------------------------------------------------------------- #
# View — /admin/analytics/export
# --------------------------------------------------------------------------- #
#
# CSV emits ONE wide table — every per-channel row from every card glued
# together with a `dataset` column so a spreadsheet user can pivot.
# Filters mirror the dashboard. We do NOT stream the full `events` table
# (that's a different concern; cohort export is a v2 dashboard request).
# --------------------------------------------------------------------------- #
@analytics_dashboard_bp.route("/admin/analytics/export", methods=["GET"])
@admin_required
def analytics_dashboard_export():
    range_code, since = _resolve_range(request.args.get("range"))
    channel = _resolve_channel(request.args.get("channel"))

    funnel = _funnel_overview(since, channel)
    matrix = _per_channel_breakout(since, channel)
    conversions = _conversion_per_channel(since, channel)
    new_users = _new_users_per_day(since, channel)

    buf = io.StringIO()
    writer = csv.writer(buf)

    # ---- Header ---- #
    writer.writerow([
        "dataset", "row_label", "channel", "metric", "value",
    ])

    # ---- Funnel overview (one row per event_type) ---- #
    for row in funnel:
        writer.writerow([
            "funnel_overview", row["event_type"], "ALL",
            "count", row["count"],
        ])

    # ---- Channel x event matrix ---- #
    for row in matrix["rows"]:
        for ch in matrix["channels"]:
            writer.writerow([
                "per_channel_event_count", row["event_type"], ch,
                "count", row["by_channel"].get(ch, 0),
            ])

    # ---- Conversion per channel ---- #
    for c in conversions:
        for metric in (
            "landed", "signup_started", "signup_completed",
            "payment_started", "payment_completed",
            "pct_landing_to_signup", "pct_signup_to_payment",
            "pct_landing_to_payment",
        ):
            value = c.get(metric)
            writer.writerow([
                "conversion_per_channel", "", c["channel"],
                metric, "" if value is None else value,
            ])

    # ---- New users per day ---- #
    for row in new_users:
        writer.writerow([
            "new_users_per_day", row["day"], "ALL",
            "distinct_visitors", row["distinct_visitors"],
        ])

    body = buf.getvalue()
    filename = (
        f"fiesta_analytics_{range_code}_"
        f"{channel.replace('/', '_').replace(' ', '_')}_"
        f"{datetime.utcnow().strftime('%Y%m%d')}.csv"
    )
    return Response(
        body,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# --------------------------------------------------------------------------- #
# Public registration hook — called from main.py
# --------------------------------------------------------------------------- #
def register_routes(app: Flask) -> None:
    """Mount the analytics dashboard. Idempotent — skips on re-registration."""
    if "analytics_dashboard" in app.blueprints:
        log.debug("analytics_dashboard blueprint already registered — skipping.")
        return
    app.register_blueprint(analytics_dashboard_bp)
    log.info(
        "Tier-C analytics dashboard registered: "
        "GET /admin/analytics + /admin/analytics/export"
    )


__all__ = [
    "register_routes",
    "FUNNEL_EVENTS",
    "RANGE_OPTIONS",
    "DEFAULT_RANGE",
]
