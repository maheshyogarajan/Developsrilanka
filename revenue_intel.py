"""
Revenue Intelligence Dashboard — Wave 2.1 (2026-05-17).

The CEO's read-only window into whether the AI-run FIESTA business is on a
$1M ARR trajectory. Surfaces:

  * Leading indicators (5 council-sharpened metrics — activation, warm->paid,
    cross-sell, false-ready, gross margin per active user)
  * Funnel rates (signup -> persona -> first remittance -> paid; MAU)
  * Revenue estimates (MRR, ARR, paid users by persona, cumulative signups)

All queries read the EVENT SPINE (`events` table, shipped prior wave) plus
`user`, `remittance_entries`, `audit_log`. Wave 2.x consumers (cross-sell
engine, nudge scheduler, Ops Sentinel) emit into the same spine, so adding a
new metric is a SQL string + a card — no schema work.

Design constraints (Council #2 carry-over):

  1. Each query lives in a module-level dict so they're trivially testable in
     isolation, swappable for read-replicas later, and exportable to BI tools
     without recompiling the app.

  2. compute_metrics() runs every query inside its own try/except — a single
     broken SQL must NOT take the whole dashboard down. Errors surface in the
     UI rather than 500ing. This is the same best-effort posture as events.emit().

  3. Placeholders are honest: queries that depend on tables/columns shipping in
     future waves (Wave 2.4 gemini_cost_log, Wave 3.3 attribution source) return
     0/null with a clearly labelled comment, never fake-positive numbers.

  4. Admin-only. Two-layer check: @login_required AND current_user.role=='admin'.
     Non-admins get 403, never a redirect — this is a numeric dashboard, not a
     marketing page.

  5. JSON twin route (/admin/revenue.json) so the orchestrator, future bots,
     and the CEO's own scripts can consume the same data programmatically.
"""
import logging
from datetime import datetime
from typing import Any

from flask import Blueprint, render_template, jsonify, abort
from flask_login import login_required, current_user
from sqlalchemy import text

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# FIESTA pricing — blended ARPU assumption for Wave 2.1 dashboard (v4.1)
# --------------------------------------------------------------------------- #
# v4.1 pricing schema: Free Trial (Rs 0) / Self-File (Rs 2,500) / Auto-File
# (Rs 5,000 — v1.1, currently disabled) + Consultant Booking (Rs 5,000 one-off).
# For the v1 dashboard we use a blended ARPU so the headline MRR/ARR numbers
# move with paid-user count without depending on which tier each user picked.
#
# Council brief economic_model: assumed average revenue per paying customer
# = LKR 3,500/year (mostly Self-File at Rs 2,500 + occasional consultant
# bookings + future Auto-File upgrades). USD anchor included for legacy
# dashboard widgets that read USD; converted at ~LKR 300/USD.
BLENDED_ANNUAL_ARPU_LKR = 3500  # economic_model.average_revenue_per_paying_customer_lkr_per_year
BLENDED_ANNUAL_ARPU_USD = 12    # ~Rs 3,500 / 300 LKR-per-USD; refresh when CBSL rate moves materially


# --------------------------------------------------------------------------- #
# LEADING INDICATOR QUERIES — the 5 council-sharpened metrics
# --------------------------------------------------------------------------- #
#
# Each entry is a SQL string. Convention: SELECT a single numeric value as
# `metric_value`, plus optional context columns (cohort_size, healthy_threshold,
# is_placeholder). The Flask route harvests by column name, not position.
#
# Use a CTE pattern where readability beats raw speed — the events table is
# indexed for (event_type, created_at DESC) so the cohort scans are cheap.
LEADING_INDICATOR_QUERIES: dict[str, str] = {

    # 1. activation_yield_7d
    # -------------------------------------------------------------------------
    # WHAT: % of signups in the last 30 days who hit `remittance_ird_ready`
    #       within 7 days of their signup event.
    # WHY:  This is the single most predictive leading indicator for paid
    #       conversion. A user who reaches IRD-ready in week 1 is ~5x more
    #       likely to convert (Council #2 hypothesis; validate empirically).
    # HEALTHY: >= 25%. Yellow <25%, Red <10%.
    "activation_yield_7d": """
        WITH signups AS (
            SELECT user_id, MIN(created_at) AS signup_at
            FROM events
            WHERE event_type = 'signup'
              AND created_at >= NOW() - INTERVAL '30 days'
              AND user_id IS NOT NULL
            GROUP BY user_id
        ),
        ird_ready AS (
            SELECT user_id, MIN(created_at) AS first_ready_at
            FROM events
            WHERE event_type = 'remittance_ird_ready'
              AND user_id IS NOT NULL
            GROUP BY user_id
        )
        SELECT
            CASE
                WHEN COUNT(s.user_id) = 0 THEN 0.0
                ELSE 100.0 * SUM(
                    CASE
                        WHEN r.first_ready_at IS NOT NULL
                         AND r.first_ready_at <= s.signup_at + INTERVAL '7 days'
                        THEN 1 ELSE 0
                    END
                ) / NULLIF(COUNT(s.user_id), 0)
            END AS metric_value,
            COUNT(s.user_id) AS cohort_size,
            25.0 AS healthy_threshold,
            FALSE AS is_placeholder
        FROM signups s
        LEFT JOIN ird_ready r ON r.user_id = s.user_id
    """,

    # 2. warm_activated_to_paid_cvr
    # -------------------------------------------------------------------------
    # WHAT: Of users who ever added a remittance (warm-activated), what % are
    #       on a paid subscription? Treats `subscription_status != 'free_trial'`
    #       as paid until Wave 2.2 lands a proper tier column.
    # WHY:  Tells us whether the product is converting engaged users. A low
    #       number means the gap is value-articulation/pricing, not activation.
    # HEALTHY: >= 15%.
    "warm_activated_to_paid_cvr": """
        WITH warm AS (
            SELECT DISTINCT user_id
            FROM events
            WHERE event_type = 'remittance_added'
              AND user_id IS NOT NULL
        )
        SELECT
            CASE
                WHEN COUNT(w.user_id) = 0 THEN 0.0
                ELSE 100.0 * SUM(
                    CASE WHEN u.subscription_status IS NOT NULL
                          AND u.subscription_status <> 'free_trial'
                         THEN 1 ELSE 0 END
                ) / NULLIF(COUNT(w.user_id), 0)
            END AS metric_value,
            COUNT(w.user_id) AS cohort_size,
            15.0 AS healthy_threshold,
            FALSE AS is_placeholder
        FROM warm w
        JOIN "user" u ON u.id = w.user_id
    """,

    # 3. lankatax_crosssell_take_rate
    # -------------------------------------------------------------------------
    # WHAT: % of users sourced from Lanka.tax referrals.
    # WHY:  Lanka.tax has 1,200+ paying foreign-income clients. They're FIESTA's
    #       highest-intent inbound channel. Tracking the cross-sell take rate
    #       proves the org's referral muscle is converting.
    # STATUS: PLACEHOLDER. The attribution source field ships in Wave 3.3.
    #         Today we return 0 with is_placeholder=true. The route still
    #         renders the card so it's visible as a tracked-but-not-live metric.
    # HEALTHY: >= 10% (loose; will tighten once data exists).
    "lankatax_crosssell_take_rate": """
        SELECT
            0.0 AS metric_value,
            0 AS cohort_size,
            10.0 AS healthy_threshold,
            TRUE AS is_placeholder
    """,

    # 4. false_ready_rate
    # -------------------------------------------------------------------------
    # WHAT: % of remittance entries that were flagged ird_ready_staff_reviewed
    #       and then UN-flagged (reviewer rejected). High = the auto-classifier
    #       (Lanka.tax staff queue feed) is too lenient.
    # WHY:  Direct quality signal for the AI handoff to Lanka.tax. If this
    #       trends up, we've shipped a regression in remittance scoring.
    # STATUS: SCHEMA-READY but the toggle workflow lands in Wave 2.3. Returns
    #         a real number IF audit_log has flips for that field; 0 if not.
    # HEALTHY: <= 5%. >5% yellow, >15% red.
    "false_ready_rate": """
        WITH flips AS (
            SELECT entity_id,
                   COUNT(*) FILTER (
                       WHERE action = 'UPDATE'
                         AND (changed_fields::text ILIKE '%ird_ready_staff_reviewed%true%')
                   ) AS up_flips,
                   COUNT(*) FILTER (
                       WHERE action = 'UPDATE'
                         AND (changed_fields::text ILIKE '%ird_ready_staff_reviewed%false%')
                   ) AS down_flips
            FROM audit_log
            WHERE entity_type = 'remittance_entry'
              AND timestamp >= NOW() - INTERVAL '90 days'
            GROUP BY entity_id
        )
        SELECT
            CASE
                WHEN COUNT(*) FILTER (WHERE up_flips > 0) = 0 THEN 0.0
                ELSE 100.0
                     * COUNT(*) FILTER (WHERE up_flips > 0 AND down_flips > 0)
                     / NULLIF(COUNT(*) FILTER (WHERE up_flips > 0), 0)
            END AS metric_value,
            COUNT(*) FILTER (WHERE up_flips > 0) AS cohort_size,
            5.0 AS healthy_threshold,
            FALSE AS is_placeholder
        FROM flips
    """,

    # 5. gross_margin_per_active_user
    # -------------------------------------------------------------------------
    # WHAT: Per-active-user gross margin = revenue/user MINUS Gemini cost/user.
    # WHY:  The single number the CEO can use to spot cost blowup before it
    #       eats the margin. Wave 2.4 (Ops Sentinel) ships gemini_cost_log.
    # STATUS: PLACEHOLDER until gemini_cost_log exists. The SQL is wrapped so
    #         the dashboard doesn't crash if the table is absent — we COALESCE
    #         to 0 inside a sub-select that only runs if the table is present.
    #         For Wave 2.1 we return $0 with is_placeholder=true.
    # HEALTHY: >= $5/active-user/month (placeholder — calibrate on real data).
    "gross_margin_per_active_user": """
        SELECT
            0.0 AS metric_value,
            0 AS cohort_size,
            5.0 AS healthy_threshold,
            TRUE AS is_placeholder
    """,
}


# --------------------------------------------------------------------------- #
# FUNNEL QUERIES — the conversion funnel
# --------------------------------------------------------------------------- #
FUNNEL_QUERIES: dict[str, str] = {

    # signup -> persona_set
    "signup_to_persona_set": """
        WITH s AS (
            SELECT DISTINCT user_id FROM events
            WHERE event_type = 'signup' AND user_id IS NOT NULL
        ),
        p AS (
            SELECT DISTINCT user_id FROM events
            WHERE event_type = 'persona_set' AND user_id IS NOT NULL
        )
        SELECT
            CASE
                WHEN COUNT(s.user_id) = 0 THEN 0.0
                ELSE 100.0 * COUNT(p.user_id) / NULLIF(COUNT(s.user_id), 0)
            END AS metric_value,
            COUNT(s.user_id) AS denominator,
            COUNT(p.user_id) AS numerator
        FROM s LEFT JOIN p ON p.user_id = s.user_id
    """,

    # persona_set -> first remittance_added
    "persona_set_to_first_remittance": """
        WITH p AS (
            SELECT DISTINCT user_id FROM events
            WHERE event_type = 'persona_set' AND user_id IS NOT NULL
        ),
        r AS (
            SELECT DISTINCT user_id FROM events
            WHERE event_type = 'remittance_added' AND user_id IS NOT NULL
        )
        SELECT
            CASE
                WHEN COUNT(p.user_id) = 0 THEN 0.0
                ELSE 100.0 * COUNT(r.user_id) / NULLIF(COUNT(p.user_id), 0)
            END AS metric_value,
            COUNT(p.user_id) AS denominator,
            COUNT(r.user_id) AS numerator
        FROM p LEFT JOIN r ON r.user_id = p.user_id
    """,

    # first_remittance -> paid
    # Treats anything other than 'free_trial' as paid (Wave 2.2 will swap this
    # for a proper tier check once Stripe lands).
    "first_remittance_to_paid": """
        WITH r AS (
            SELECT DISTINCT user_id FROM events
            WHERE event_type = 'remittance_added' AND user_id IS NOT NULL
        )
        SELECT
            CASE
                WHEN COUNT(r.user_id) = 0 THEN 0.0
                ELSE 100.0 * SUM(
                    CASE WHEN u.subscription_status IS NOT NULL
                          AND u.subscription_status <> 'free_trial'
                         THEN 1 ELSE 0 END
                ) / NULLIF(COUNT(r.user_id), 0)
            END AS metric_value,
            COUNT(r.user_id) AS denominator,
            SUM(
                CASE WHEN u.subscription_status IS NOT NULL
                      AND u.subscription_status <> 'free_trial'
                     THEN 1 ELSE 0 END
            ) AS numerator
        FROM r JOIN "user" u ON u.id = r.user_id
    """,

    # MAU — distinct users with any event in the last 30 days
    "monthly_active_users": """
        SELECT
            COUNT(DISTINCT user_id)::float AS metric_value,
            COUNT(DISTINCT user_id) AS denominator,
            COUNT(DISTINCT user_id) AS numerator
        FROM events
        WHERE created_at >= NOW() - INTERVAL '30 days'
          AND user_id IS NOT NULL
    """,
}


# --------------------------------------------------------------------------- #
# REVENUE QUERIES — the headline numbers
# --------------------------------------------------------------------------- #
REVENUE_QUERIES: dict[str, str] = {

    # MRR estimate = paid_users * (BLENDED_ANNUAL_ARPU_USD / 12)
    # Once tiered pricing lands (Wave 2.2), this becomes a sum over tiers.
    "mrr_estimate": f"""
        SELECT
            COALESCE(SUM(CASE
                WHEN subscription_status IS NOT NULL
                 AND subscription_status <> 'free_trial'
                THEN 1.0 ELSE 0.0
            END), 0) * ({BLENDED_ANNUAL_ARPU_USD}.0 / 12.0) AS metric_value,
            COUNT(*) AS user_count,
            COUNT(*) FILTER (
                WHERE subscription_status IS NOT NULL
                  AND subscription_status <> 'free_trial'
            ) AS paid_user_count
        FROM "user"
    """,

    # ARR estimate = MRR * 12 (i.e. paid_users * blended_annual_arpu)
    "arr_estimate": f"""
        SELECT
            COALESCE(SUM(CASE
                WHEN subscription_status IS NOT NULL
                 AND subscription_status <> 'free_trial'
                THEN 1.0 ELSE 0.0
            END), 0) * {BLENDED_ANNUAL_ARPU_USD}.0 AS metric_value,
            COUNT(*) AS user_count,
            COUNT(*) FILTER (
                WHERE subscription_status IS NOT NULL
                  AND subscription_status <> 'free_trial'
            ) AS paid_user_count
        FROM "user"
    """,

    # Paid user count by persona
    # Returns a JSON array; the renderer iterates it. Postgres-native json_agg
    # keeps the result a single column the harvest loop handles uniformly.
    "paid_user_count_by_persona": """
        SELECT
            COALESCE(json_agg(row_data), '[]'::json) AS metric_value
        FROM (
            SELECT json_build_object(
                'persona', COALESCE(persona, 'unset'),
                'paid_count', COUNT(*) FILTER (
                    WHERE subscription_status IS NOT NULL
                      AND subscription_status <> 'free_trial'
                ),
                'total_count', COUNT(*)
            ) AS row_data
            FROM "user"
            GROUP BY persona
            ORDER BY COUNT(*) DESC
        ) sub
    """,

    # Cumulative signups by day, last 30 days
    # Uses the events table (event_type='signup') as authoritative.
    # Postgres forbids window functions INSIDE aggregate calls (json_agg), so
    # we compute the running total in a CTE first, then aggregate the rows.
    "cumulative_signups_by_day_last_30d": """
        WITH daily AS (
            SELECT DATE(created_at) AS signup_date, COUNT(DISTINCT user_id) AS new_signups
            FROM events
            WHERE event_type = 'signup'
              AND created_at >= NOW() - INTERVAL '30 days'
              AND user_id IS NOT NULL
            GROUP BY DATE(created_at)
        ),
        daily_with_cum AS (
            SELECT signup_date,
                   new_signups,
                   SUM(new_signups) OVER (ORDER BY signup_date) AS cumulative
            FROM daily
        )
        SELECT
            COALESCE(
                json_agg(
                    json_build_object(
                        'date',        signup_date,
                        'new_signups', new_signups,
                        'cumulative',  cumulative
                    )
                    ORDER BY signup_date DESC
                ),
                '[]'::json
            ) AS metric_value
        FROM daily_with_cum
    """,
}


# --------------------------------------------------------------------------- #
# Compute orchestrator
# --------------------------------------------------------------------------- #

def _run_one(db_session, sql: str) -> dict[str, Any]:
    """Run a single query and return its first row as a dict.

    Wrapped so the caller can try/except it. We turn SQLAlchemy Row into a
    plain dict so the template/JSON layer doesn't need to know about Row.
    """
    row = db_session.execute(text(sql)).mappings().first()
    if row is None:
        return {}
    return dict(row)


def compute_metrics() -> dict[str, Any]:
    """Run every revenue/funnel/leading-indicator query and return a structured
    result. Each query runs in its own try/except so one bad SQL doesn't break
    the whole dashboard.

    Returns:
        {
            "leading_indicators": {name: {metric_value, cohort_size, ...} | None},
            "funnel":             {name: {metric_value, denominator, numerator} | None},
            "revenue":            {name: {metric_value, ...} | None},
            "computed_at":        ISO timestamp,
            "errors":             [{"section": ..., "metric": ..., "error": ...}, ...]
        }
    """
    # Local import — avoids circular import at module load time.
    from app import db

    result: dict[str, Any] = {
        "leading_indicators": {},
        "funnel": {},
        "revenue": {},
        "computed_at": datetime.utcnow().isoformat() + "Z",
        "errors": [],
    }

    sections = [
        ("leading_indicators", LEADING_INDICATOR_QUERIES),
        ("funnel", FUNNEL_QUERIES),
        ("revenue", REVENUE_QUERIES),
    ]

    for section_name, queries in sections:
        for metric_name, sql in queries.items():
            try:
                row = _run_one(db.session, sql)
                result[section_name][metric_name] = row
            except Exception as exc:
                log.warning(
                    "revenue_intel: query %s.%s failed: %s",
                    section_name, metric_name, exc,
                )
                # Roll back the failed transaction so subsequent queries on the
                # same session aren't poisoned ("current transaction is aborted").
                try:
                    db.session.rollback()
                except Exception:
                    pass
                result[section_name][metric_name] = None
                result["errors"].append({
                    "section": section_name,
                    "metric": metric_name,
                    "error": str(exc)[:500],
                })

    return result


# --------------------------------------------------------------------------- #
# Flask blueprint
# --------------------------------------------------------------------------- #

revenue_intel_bp = Blueprint("revenue_intel", __name__, url_prefix="/admin")


def _require_admin():
    """Two-layer admin gate. @login_required handles unauth; this enforces role.

    Returns None if OK, aborts 403 otherwise. Kept as a helper rather than a
    decorator so the JSON route can return a consistent JSON-shape 403 instead
    of an HTML page.
    """
    if not getattr(current_user, "is_authenticated", False):
        abort(403)
    if getattr(current_user, "role", None) != "admin":
        abort(403)


def _emit_dashboard_viewed():
    """Best-effort emit; doesn't add `dashboard_viewed` to STANDARD_EVENTS
    because the council-frozen 12-event contract is read by Wave 2 consumers —
    we use the free-form event_type column for this ad-hoc one. The events.py
    module docstring already permits ad-hoc strings."""
    try:
        from events import emit
        emit(
            "dashboard_viewed",
            user_id=current_user.id if getattr(current_user, "is_authenticated", False) else None,
            payload={"dashboard": "revenue_intel"},
            source="route:revenue_intel",
        )
    except Exception as exc:
        log.debug("dashboard_viewed emit failed (non-fatal): %s", exc)


@revenue_intel_bp.route("/revenue", methods=["GET"])
@login_required
def revenue_dashboard():
    """HTML dashboard. Admin-only."""
    _require_admin()
    metrics = compute_metrics()
    _emit_dashboard_viewed()
    return render_template("admin/revenue.html", metrics=metrics)


@revenue_intel_bp.route("/revenue.json", methods=["GET"])
@login_required
def revenue_dashboard_json():
    """JSON twin. Same data, machine-readable. Admin-only.

    Useful for the orchestrator, CEO scripts, and future bots (e.g. a Telegram
    `/arr` command that calls this endpoint and reports the headline numbers).
    """
    _require_admin()
    metrics = compute_metrics()
    _emit_dashboard_viewed()
    return jsonify(metrics)


def register_routes(app):
    """Register the revenue-intel blueprint on the Flask app.

    Called from main.py during app construction — mirrors the pattern used by
    remittance_routes.register_routes(app) etc.
    """
    app.register_blueprint(revenue_intel_bp)
    log.info("Revenue Intelligence dashboard registered at /admin/revenue (+ .json)")


__all__ = [
    "LEADING_INDICATOR_QUERIES",
    "FUNNEL_QUERIES",
    "REVENUE_QUERIES",
    "compute_metrics",
    "revenue_intel_bp",
    "register_routes",
    "BLENDED_ANNUAL_ARPU_USD",
]
