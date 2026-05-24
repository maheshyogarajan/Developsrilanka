"""Tier D4 E3: funnel anomaly detection — 5 metrics, daily probe."""
import logging
from datetime import datetime, timedelta
from sqlalchemy import text

logger = logging.getLogger(__name__)

METRICS = [
    "signup_conversion",      # signup_completed / signup_started
    "payment_conversion",     # payment_completed / signup_completed
    "landing_to_signup",      # signup_started / landing_view
    "tax_bill_view_rate",     # tax_bill_view / signup_completed
    "evidence_upload_rate",   # evidence_uploaded / signup_completed
]

NUMERATOR = {
    "signup_conversion": "signup_completed",
    "payment_conversion": "payment_completed",
    "landing_to_signup": "signup_started",
    "tax_bill_view_rate": "tax_bill_view",
    "evidence_upload_rate": "evidence_uploaded",
}
DENOMINATOR = {
    "signup_conversion": "signup_started",
    "payment_conversion": "signup_completed",
    "landing_to_signup": "landing_view",
    "tax_bill_view_rate": "signup_completed",
    "evidence_upload_rate": "signup_completed",
}


def _count_event(db, event_type, since, until):
    """Count distinct sessions firing event_type between since (inclusive) and until (exclusive)."""
    sql = text("""
        SELECT COUNT(DISTINCT COALESCE(session_anon_id, payload->>'session_anon_id'))
        FROM events
        WHERE event_type = :et AND created_at >= :since AND created_at < :until
    """)
    return db.session.execute(sql, {"et": event_type, "since": since, "until": until}).scalar() or 0


def compute_metric(db, key, since, until):
    num = _count_event(db, NUMERATOR[key], since, until)
    den = _count_event(db, DENOMINATOR[key], since, until)
    if den == 0:
        return None  # undefined ratio
    return num / den


def compute_baseline(db, key, days=7):
    """7-day rolling baseline ending YESTERDAY (excludes today/current period)."""
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since = today_start - timedelta(days=days)
    until = today_start  # exclude today
    return compute_metric(db, key, since, until)


def detect_anomaly(key, current, baseline, threshold_pct=30):
    if current is None or baseline is None or baseline == 0:
        return {"metric": key, "current": current, "baseline": baseline,
                "delta_pct": None, "is_anomaly": False, "direction": "undefined"}
    delta_pct = ((current - baseline) / baseline) * 100
    direction = "drop" if delta_pct < 0 else "spike"
    is_anomaly = (delta_pct <= -threshold_pct) or (delta_pct >= 100)  # 30% drop OR 100% spike
    return {"metric": key, "current": current, "baseline": baseline,
            "delta_pct": delta_pct, "is_anomaly": is_anomaly, "direction": direction}


def run_daily_probe(db, send_alert_fn):
    """Iterate 5 metrics, alert on each anomaly. send_alert_fn = ops_alerts.send_alert."""
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)

    results = []
    for key in METRICS:
        current = compute_metric(db, key, yesterday_start, today_start)
        baseline = compute_baseline(db, key, days=7)
        finding = detect_anomaly(key, current, baseline)
        results.append(finding)
        if finding["is_anomaly"]:
            send_alert_fn(
                severity="MEDIUM",
                title=f"Funnel anomaly: {key} {finding['direction']}",
                body=f"{key}: current={current:.3f}, baseline={baseline:.3f}, delta={finding['delta_pct']:+.1f}%",
                data=finding,
            )
    return results
