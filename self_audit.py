"""
self_audit.py — FIESTA weekly self-audit report generator (Tier D4 / E5).

PURPOSE
-------
Once per week the system writes its own state-of-the-union to the CEO's
Telegram so the CEO doesn't have to ask. Five sections covering Users,
Revenue, Operations, Tech, and the acquisition Funnel. Each section is
capped at ~10 lines so the entire report fits inside ONE Telegram message
(Telegram's hard cap is 4096 chars; we target <=3900 to leave headroom for
the [SEV INFO] header that ops_alerts.send_alert prepends).

PUBLIC API
----------
    generate_weekly_report() -> str

Returns a single markdown-formatted string ready to hand to
``ops_alerts.send_alert(severity='INFO', title=..., body=<return value>)``.

SCOPE CAPS (per task brief)
---------------------------
- Single Telegram message (4096 char cap).
- NO email version, NO web dashboard version (E4 perf has its own admin
  endpoint; D2 admin queue covers tickets in-app).
- Hard-fail-soft: if any section query errors, that section becomes
  ``[SECTION N: error — <reason>]`` and the rest of the report still ships.
  Partial > silent.

DATA SOURCES (one per section — by design)
------------------------------------------
1. Users    -> ``user`` (created_at, subscription_status); count semantics
               documented inline.
2. Revenue  -> ``paywall_subscription`` (active/purchased_at/amount_paid_lkr,
               refunded_at). One-time self_file purchases; MRR is
               annualised one-time revenue / 12.
3. Ops      -> ``paywall_dunning`` (pending), ``d2_support_tickets``
               (open/awaiting_customer + priority), ``support_tickets``
               (AI Q&A copilot — Wave 3.2), ``feedback`` (Tier D4 widget).
4. Tech     -> Sentry on/off flag (no DB-backed error count), perf p95
               from ``perf_monitoring._build_perf_summary()`` ring buffer,
               slow-request alert count from ops_alerts dedup state (best-
               effort, in-process), backup status = "see Tigris bucket"
               (pg_backup writes to S3, no DB ledger).
5. Funnel   -> ``events`` table — landing_view -> signup_started ->
               signup_completed -> payment_completed (same shape as
               analytics_dashboard_routes). Top 3 utm_source channels.

DESIGN NOTES
------------
- Raw SQL via ``db.session.execute(text(...))`` everywhere — same pattern
  the existing analytics_dashboard_routes + ops_probes use. ORM round-trip
  is slower at this query shape (multi-FILTER COUNT) and the SQL pack is
  the operator-facing spec; mirroring it verbatim is the point.
- One round-trip per section (with multiple FILTER aggregates inside the
  SELECT) — keeps the weekly task <500ms even if the DB is in another
  region.
- All time windows are computed once at the top of generate_weekly_report
  so the report has a single, consistent "as of" moment. Subsequent
  re-runs the same week are deterministic on the same dataset.
- The module is import-safe in test contexts: the SQL is executed lazily
  inside each section helper; no module-level DB calls.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Callable

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Telegram hard cap is 4096; ops_alerts.send_alert wraps with header/footer.
# Target a tight cap so the wrapped message stays inside one Telegram POST.
REPORT_MAX_CHARS = 3800

# SELF_FILE one-time price (LKR). Imported lazily to avoid coupling to the
# paywall package at module import time; we fall back to the canonical
# constant 2500 if the import is unavailable in a test context.
_DEFAULT_SELF_FILE_PRICE_LKR = 2500


def _self_file_price_lkr() -> int:
    try:
        from fiesta.paywall.models import SELF_FILE_PRICE_LKR
        return int(SELF_FILE_PRICE_LKR)
    except Exception:
        return _DEFAULT_SELF_FILE_PRICE_LKR


# --------------------------------------------------------------------------- #
# Section helpers
# --------------------------------------------------------------------------- #

def _section_users(now: datetime, t_7d_ago: datetime) -> str:
    """Section 1: total users, new last 7d, paying users, churned (last 7d).

    Definitions:
      - total: every row in ``user``.
      - new_7d: created_at >= t_7d_ago.
      - paying: subscription_status NOT IN ('free_trial', 'trial') — covers
        self_file + auto_file + premium_* tiers. Same convention as
        fiesta/admin/routes.py.
      - churned_7d: paywall_subscription rows where refunded_at >= t_7d_ago
        OR (cancel_at_period_end=true AND current_period_end <= now AND
        status != 'active'). Best-effort — the canonical churn signal is
        webhook-driven; this aggregates what's persisted.
    """
    from app import db
    from sqlalchemy import text as sql_text

    row = db.session.execute(
        sql_text(
            """
            SELECT
              (SELECT COUNT(*) FROM "user")                                              AS total,
              (SELECT COUNT(*) FROM "user" WHERE created_at >= :t7)                       AS new_7d,
              (SELECT COUNT(*) FROM "user"
                 WHERE subscription_status IS NOT NULL
                   AND subscription_status NOT IN ('free_trial', 'trial'))                AS paying
            """
        ),
        {"t7": t_7d_ago},
    ).fetchone()
    total = int(row[0] or 0)
    new_7d = int(row[1] or 0)
    paying = int(row[2] or 0)

    # Churned: refunds in window. Wrapped in try/except so missing
    # paywall_subscription table (clean dev DB) doesn't kill the whole section.
    churned_7d = "?"
    try:
        crow = db.session.execute(
            sql_text(
                """
                SELECT COUNT(*) FROM paywall_subscription
                 WHERE refunded_at IS NOT NULL AND refunded_at >= :t7
                """
            ),
            {"t7": t_7d_ago},
        ).fetchone()
        churned_7d = str(int(crow[0] or 0))
    except Exception as e:
        log.debug("self_audit users: churn query skipped: %s", e)

    lines = [
        "**1. Users**",
        f"- Total: {total}",
        f"- New (last 7d): {new_7d}",
        f"- Paying (active paid subs): {paying}",
        f"- Refunded/churned (last 7d): {churned_7d}",
    ]
    return "\n".join(lines)


def _section_revenue(now: datetime, t_7d_ago: datetime) -> str:
    """Section 2: MRR estimate, paid revenue last 7d, refund count.

    MRR estimate semantics: FIESTA's only paid product (X1) is the
    SELF_FILE one-time annual purchase at Rs 2,500. Industry-standard MRR
    treats a one-time annual purchase as price/12 = Rs ~208 per active
    subscription per month. We compute:
        MRR_estimate = active_self_file_subs * (SELF_FILE_PRICE_LKR / 12)

    Revenue last 7d: SUM(amount_paid_lkr) for paywall_subscription rows
    purchased_at in the window — same as the X1 conversion funnel.

    Refund count last 7d: rows with refunded_at in the window.
    """
    from app import db
    from sqlalchemy import text as sql_text

    price = _self_file_price_lkr()

    try:
        row = db.session.execute(
            sql_text(
                """
                SELECT
                  COUNT(*) FILTER (
                    WHERE status = 'active' AND (expires_at IS NULL OR expires_at >= :now)
                  )                                                                       AS active_subs,
                  COALESCE(SUM(amount_paid_lkr) FILTER (
                    WHERE purchased_at >= :t7
                  ), 0)                                                                   AS revenue_7d_lkr,
                  COUNT(*) FILTER (
                    WHERE refunded_at IS NOT NULL AND refunded_at >= :t7
                  )                                                                       AS refunds_7d
                FROM paywall_subscription
                """
            ),
            {"now": now, "t7": t_7d_ago},
        ).fetchone()
    except Exception as e:
        return (
            "**2. Revenue**\n"
            f"- [section skipped: paywall_subscription not queryable — {e}]"
        )

    active = int(row[0] or 0)
    revenue_7d = int(row[1] or 0)
    refunds_7d = int(row[2] or 0)
    mrr_estimate = int(round((active * price) / 12.0))

    lines = [
        "**2. Revenue**",
        f"- Active paid subs: {active} (self_file @ Rs {price:,})",
        f"- MRR estimate: Rs {mrr_estimate:,} (annualised one-time / 12)",
        f"- Revenue collected last 7d: Rs {revenue_7d:,}",
        f"- Refunds processed last 7d: {refunds_7d}",
    ]
    return "\n".join(lines)


def _section_operations(now: datetime, t_7d_ago: datetime) -> str:
    """Section 3: dunning, support tickets, AI Q&A, feedback.

    All four counts are last-7d windowed except open-state counts (which
    are point-in-time snapshots — what's currently sitting on the queue).
    """
    from app import db
    from sqlalchemy import text as sql_text

    lines = ["**3. Operations**"]

    # 3a. Open dunning rows (point in time)
    try:
        r = db.session.execute(
            sql_text(
                "SELECT COUNT(*) FROM paywall_dunning WHERE state = 'pending'"
            )
        ).fetchone()
        lines.append(f"- Open dunning (failed-payment recovery): {int(r[0] or 0)}")
    except Exception as e:
        lines.append(f"- Open dunning: [error — {e}]")

    # 3b. Support tickets (D2) — current open / awaiting_customer by priority
    try:
        r = db.session.execute(
            sql_text(
                """
                SELECT
                  COUNT(*) FILTER (WHERE status = 'open' AND priority = 'high')           AS open_high,
                  COUNT(*) FILTER (WHERE status = 'open' AND priority = 'normal')         AS open_norm,
                  COUNT(*) FILTER (WHERE status = 'open' AND priority = 'low')            AS open_low,
                  COUNT(*) FILTER (WHERE status = 'awaiting_customer')                    AS awaiting
                FROM d2_support_tickets
                """
            )
        ).fetchone()
        lines.append(
            f"- D2 tickets open: high={int(r[0] or 0)} "
            f"normal={int(r[1] or 0)} low={int(r[2] or 0)} "
            f"awaiting_customer={int(r[3] or 0)}"
        )
    except Exception as e:
        lines.append(f"- D2 tickets: [error — {e}]")

    # 3c. AI Q&A queries last 7d — uses support_copilot SupportTicket rows
    try:
        r = db.session.execute(
            sql_text(
                """
                SELECT
                  COUNT(*)                                              AS total,
                  COUNT(*) FILTER (WHERE escalated_to_human = TRUE)     AS escalated
                FROM support_tickets
                WHERE created_at >= :t7
                """
            ),
            {"t7": t_7d_ago},
        ).fetchone()
        total = int(r[0] or 0)
        esc = int(r[1] or 0)
        lines.append(f"- AI Q&A queries (last 7d): {total} (escalated to human: {esc})")
    except Exception as e:
        lines.append(f"- AI Q&A queries: [error — {e}]")

    # 3d. Feedback last 7d by category
    try:
        r = db.session.execute(
            sql_text(
                """
                SELECT
                  COUNT(*)                                       AS total,
                  COUNT(*) FILTER (WHERE category = 'bug')       AS bugs,
                  COUNT(*) FILTER (WHERE category = 'feature')   AS features,
                  COUNT(*) FILTER (WHERE category = 'confusion') AS confusion,
                  COUNT(*) FILTER (WHERE category = 'praise')    AS praise
                FROM feedback
                WHERE created_at >= :t7
                """
            ),
            {"t7": t_7d_ago},
        ).fetchone()
        lines.append(
            f"- Feedback (last 7d): {int(r[0] or 0)} total "
            f"(bug={int(r[1] or 0)} feature={int(r[2] or 0)} "
            f"confusion={int(r[3] or 0)} praise={int(r[4] or 0)})"
        )
    except Exception as e:
        lines.append(f"- Feedback: [error — {e}]")

    return "\n".join(lines)


def _section_tech(now: datetime, t_7d_ago: datetime) -> str:
    """Section 4: errors (Sentry status), perf p95, slow-request alert count,
    backup status.

    Errors: no DB-backed error log exists in this repo (error_logger.py is
    stderr/file only). Sentry is the canonical source of truth. We report
    whether Sentry is wired (SENTRY_DSN set) — actual error counts are at
    the Sentry dashboard, not queryable from here without an API token.

    Perf p95: pulled from ``perf_monitoring._build_perf_summary()`` ring
    buffer. In-process, in-memory — the value reflects THIS process. In
    practice the report is generated by a Celery worker that doesn't
    serve traffic, so the buffer there is empty; we surface that fact
    transparently rather than fake a number.

    Slow-request alert count: count of entries in ``ops_alerts._dedup_state``
    matching the slow-request alert title. Best-effort, in-process,
    indicative not authoritative.

    Backup status: ``tasks.pg_backup`` ships to S3/Tigris, no DB ledger.
    We surface configuration health (env vars present) and point at the
    bucket for the actual file list.
    """
    lines = ["**4. Tech**"]

    # 4a. Sentry wiring
    sentry_dsn = os.environ.get("SENTRY_DSN", "").strip()
    if sentry_dsn:
        lines.append("- Sentry: WIRED (errors at Sentry dashboard)")
    else:
        lines.append("- Sentry: DISABLED (SENTRY_DSN unset) — errors only in logs")

    # 4b. Perf p95 from in-process ring buffer
    try:
        from perf_monitoring import _build_perf_summary
        summary = _build_perf_summary()
        overall = summary.get("overall", {})
        n = int(overall.get("n", 0))
        p95 = float(overall.get("p95_ms", 0.0))
        if n > 0:
            lines.append(f"- Perf p95: {p95:.0f}ms across last {n} samples (this process)")
        else:
            lines.append("- Perf p95: 0 samples in this process (Celery worker, no traffic)")
    except Exception as e:
        lines.append(f"- Perf p95: [error — {e}]")

    # 4c. Slow-request alert count — peek into ops_alerts dedup state.
    try:
        from ops_alerts import _dedup_state, _dedup_lock
        with _dedup_lock:
            slow_count = sum(
                1 for (title, _sev) in _dedup_state.keys()
                if "slow request" in title.lower()
            )
        lines.append(f"- Slow-request alerts seen this process: {slow_count} (dedup-state count)")
    except Exception as e:
        lines.append(f"- Slow-request alerts: [error — {e}]")

    # 4d. Backup status — env wiring only (no DB ledger).
    backup_envs = ["BACKUP_S3_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
    missing = [k for k in backup_envs if not os.environ.get(k)]
    if not missing:
        lines.append("- Backup: env wired — see Tigris bucket for run history")
    else:
        lines.append(f"- Backup: WARN — missing env: {', '.join(missing)}")

    return "\n".join(lines)


def _section_funnel(now: datetime, t_7d_ago: datetime) -> str:
    """Section 5: landing_view -> signup_started -> signup_completed ->
    payment_completed counts (last 7d) with conversion %s. Top 3 channels.

    Channel extraction mirrors analytics_dashboard_routes._CHANNEL_EXPR
    (utm_source first, then host of client_referrer, then 'direct').
    Stage counts are distinct anon_id within window, same as the dashboard.
    """
    from app import db
    from sqlalchemy import text as sql_text

    lines = ["**5. Funnel (last 7d)**"]

    # 5a. Stage counts
    try:
        row = db.session.execute(
            sql_text(
                """
                SELECT
                  COUNT(DISTINCT COALESCE(session_anon_id, payload->>'session_anon_id'))
                    FILTER (WHERE event_type = 'landing_view')      AS landed,
                  COUNT(DISTINCT COALESCE(session_anon_id, payload->>'session_anon_id'))
                    FILTER (WHERE event_type = 'signup_started')    AS started,
                  COUNT(DISTINCT COALESCE(session_anon_id, payload->>'session_anon_id'))
                    FILTER (WHERE event_type = 'signup_completed')  AS signed_up,
                  COUNT(DISTINCT COALESCE(session_anon_id, payload->>'session_anon_id'))
                    FILTER (WHERE event_type = 'payment_completed') AS paid
                FROM events
                WHERE created_at >= :t7
                """
            ),
            {"t7": t_7d_ago},
        ).fetchone()
        landed = int(row[0] or 0)
        started = int(row[1] or 0)
        signed_up = int(row[2] or 0)
        paid = int(row[3] or 0)

        def pct(num: int, den: int) -> str:
            return f"{(100.0 * num / den):.1f}%" if den else "n/a"

        lines.append(f"- landing_view: {landed}")
        lines.append(f"- signup_started: {started} ({pct(started, landed)} of landed)")
        lines.append(f"- signup_completed: {signed_up} ({pct(signed_up, started)} of started)")
        lines.append(f"- payment_completed: {paid} ({pct(paid, signed_up)} of signed_up)")
    except Exception as e:
        lines.append(f"- [stage counts error — {e}]")

    # 5b. Top 3 channels by utm_source last 7d
    try:
        rows = db.session.execute(
            sql_text(
                """
                SELECT
                  COALESCE(NULLIF(payload->>'utm_source', ''), 'direct') AS channel,
                  COUNT(DISTINCT COALESCE(session_anon_id, payload->>'session_anon_id')) AS visitors
                FROM events
                WHERE created_at >= :t7
                  AND event_type = 'landing_view'
                GROUP BY channel
                ORDER BY visitors DESC
                LIMIT 3
                """
            ),
            {"t7": t_7d_ago},
        ).fetchall()
        if rows:
            channels = ", ".join(f"{r[0]}={int(r[1] or 0)}" for r in rows)
            lines.append(f"- Top 3 channels (visitors): {channels}")
        else:
            lines.append("- Top 3 channels: no landing events in window")
    except Exception as e:
        lines.append(f"- Top 3 channels: [error — {e}]")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

_SECTIONS: list[tuple[int, Callable[[datetime, datetime], str]]] = [
    (1, _section_users),
    (2, _section_revenue),
    (3, _section_operations),
    (4, _section_tech),
    (5, _section_funnel),
]


def generate_weekly_report(now: datetime | None = None) -> str:
    """Build the weekly self-audit markdown string.

    Each section runs in a try/except so a single failing query degrades
    that section to ``[SECTION N: error — ...]`` and the rest of the
    report still ships. Section helpers themselves swallow per-query
    errors and emit partial section text; this outer guard is the
    safety-net for catastrophic failures (db.session blew up, app
    context missing, etc.).
    """
    now = now or datetime.utcnow()
    t_7d_ago = now - timedelta(days=7)

    header = (
        "FIESTA Weekly Self-Audit\n"
        f"As of: {now.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"Window: last 7 days ({t_7d_ago.strftime('%Y-%m-%d')} -> {now.strftime('%Y-%m-%d')})\n"
        "----------------------------------------"
    )

    parts: list[str] = [header]
    for n, fn in _SECTIONS:
        try:
            parts.append(fn(now, t_7d_ago))
        except Exception as e:
            log.exception("self_audit: section %d failed", n)
            parts.append(f"**{n}. [SECTION {n}: error — {e}]**")

    text = "\n\n".join(parts)
    if len(text) > REPORT_MAX_CHARS:
        # Truncate hard with a marker so the CEO knows the report was
        # too big for a single Telegram message. Reduces the chance the
        # last section gets silently chopped by Telegram's own limit.
        text = text[: REPORT_MAX_CHARS - 30] + "\n\n...[truncated for Telegram]"
    return text


__all__ = [
    "generate_weekly_report",
    "REPORT_MAX_CHARS",
]
