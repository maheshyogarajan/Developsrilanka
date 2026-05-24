"""Tier D4 E3 funnel anomaly tests."""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on sys.path so `import funnel_anomaly` works
# regardless of how pytest is invoked.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import funnel_anomaly  # noqa: E402


# --------------------------------------------------------------------------- #
# Unit tests for detect_anomaly thresholds
# --------------------------------------------------------------------------- #

def test_detect_anomaly_below_threshold_fires():
    """50% drop (well past 30% threshold) MUST flag is_anomaly=True
    with direction='drop'."""
    finding = funnel_anomaly.detect_anomaly(
        key="signup_conversion",
        current=0.05,
        baseline=0.10,
    )
    assert finding["is_anomaly"] is True
    assert finding["direction"] == "drop"
    assert finding["delta_pct"] == pytest.approx(-50.0)
    assert finding["metric"] == "signup_conversion"


def test_detect_anomaly_within_threshold_no_fire():
    """20% drop (below 30% threshold) MUST NOT flag is_anomaly."""
    finding = funnel_anomaly.detect_anomaly(
        key="payment_conversion",
        current=0.08,
        baseline=0.10,
    )
    assert finding["is_anomaly"] is False
    assert finding["direction"] == "drop"
    assert finding["delta_pct"] == pytest.approx(-20.0)


# --------------------------------------------------------------------------- #
# Integration test for run_daily_probe
# --------------------------------------------------------------------------- #

def test_run_daily_probe_calls_alert_only_for_anomalies():
    """run_daily_probe should iterate 5 metrics and call send_alert_fn
    exactly once — for the single anomalous metric only.

    Strategy: mock compute_metric so that 4 metrics return healthy ratios
    and 1 (payment_conversion) returns a 60% drop relative to its baseline.
    """
    db_mock = MagicMock()
    alert_mock = MagicMock()

    # current/baseline pairs keyed by metric. Yesterday's value first, baseline second.
    fixtures = {
        "signup_conversion":   (0.50, 0.50),   # 0% delta — no alert
        "payment_conversion":  (0.10, 0.25),   # -60% delta — ANOMALY
        "landing_to_signup":   (0.30, 0.32),   # ~-6% delta — no alert
        "tax_bill_view_rate":  (0.40, 0.45),   # ~-11% delta — no alert
        "evidence_upload_rate":(0.55, 0.50),   # +10% delta — no alert
    }

    call_log = []

    def fake_compute_metric(db, key, since, until):
        # First call per key returns current (yesterday window), second returns baseline (7-day).
        # We detect which by counting prior calls for this key.
        prior_for_key = sum(1 for k in call_log if k == key)
        call_log.append(key)
        current, baseline = fixtures[key]
        return current if prior_for_key == 0 else baseline

    with patch.object(funnel_anomaly, "compute_metric", side_effect=fake_compute_metric):
        results = funnel_anomaly.run_daily_probe(db_mock, alert_mock)

    # Exactly 5 results, one per metric.
    assert len(results) == 5
    assert {r["metric"] for r in results} == set(funnel_anomaly.METRICS)

    # Exactly one alert call — for payment_conversion drop.
    assert alert_mock.call_count == 1
    call_kwargs = alert_mock.call_args.kwargs
    assert call_kwargs["severity"] == "MEDIUM"
    assert "payment_conversion" in call_kwargs["title"]
    assert "drop" in call_kwargs["title"]
    assert call_kwargs["data"]["metric"] == "payment_conversion"
    assert call_kwargs["data"]["is_anomaly"] is True

    # Sanity: the anomalous result is flagged in results too.
    payment_result = next(r for r in results if r["metric"] == "payment_conversion")
    assert payment_result["is_anomaly"] is True
    assert payment_result["direction"] == "drop"
