"""tests/secret_rotation_module/test_secret_rotation.py — Tier D5 / F4.

Bucket-threshold contract:
  - days_since < (rotation_days - warn_lead_days): OK    (no alert)
  - (rotation_days - warn_lead_days) <= days_since < rotation_days: WARN (LOW alert)
  - days_since >= rotation_days: URGENT (HIGH alert)

For the canonical 90d / 15d-lead policy that means:
  - 50d  -> ok
  - 76d  -> warn
  - 91d  -> urgent

Two cases here cover all three bucket boundaries by feeding a synthetic
3-entry inventory and asserting both the report shape AND the
send_alert_fn invocation shape (severity, title, recipient counts).

Run:  python -m pytest tests/secret_rotation_module/ -v
"""
from __future__ import annotations

import sys
import textwrap
from datetime import date, timedelta
from pathlib import Path

import pytest

# Make repo root importable (tests/secret_rotation_module/ -> repo root)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _write_inventory(tmp_path: Path, today: date) -> Path:
    """Write a 3-secret YAML where each secret hits exactly one bucket.

    - secret_ok:     last_rotated = today - 50d  -> ok      (50 < 75)
    - secret_warn:   last_rotated = today - 76d  -> warn    (75 <= 76 < 90)
    - secret_urgent: last_rotated = today - 91d  -> urgent  (>= 90)
    """
    ok_date = (today - timedelta(days=50)).isoformat()
    warn_date = (today - timedelta(days=76)).isoformat()
    urgent_date = (today - timedelta(days=91)).isoformat()

    body = textwrap.dedent(f"""
        schema_version: 1
        defaults:
          rotation_days: 90
          warn_lead_days: 15

        secrets:
          - name: SECRET_OK
            store: fly_secrets
            last_rotated: {ok_date}
            rotation_days: 90
            owner: ceo
            description: "Healthy secret, no alert"
            rotation_steps:
              - "step one"
              - "step two"
          - name: SECRET_WARN
            store: fly_secrets
            last_rotated: {warn_date}
            rotation_days: 90
            owner: ceo
            description: "Approaching window"
            rotation_steps:
              - "rotate me soon"
          - name: SECRET_URGENT
            store: fly_secrets
            last_rotated: {urgent_date}
            rotation_days: 90
            owner: ceo
            description: "Past window"
            rotation_steps:
              - "rotate me now"
    """).strip()

    inv_path = tmp_path / "secret_inventory.yaml"
    inv_path.write_text(body, encoding="utf-8")
    return inv_path


def test_check_buckets_at_75d_and_91d_boundaries(tmp_path):
    """check() must classify 50d=ok, 76d=warn, 91d=urgent against a 90d
    rotation policy with the default 15d warn lead.

    This is the core bucket-threshold contract — the heart of the Tier D5
    F4 build. If this breaks, every alert fires at the wrong time.
    """
    import secret_rotation_check as src

    today = date(2026, 6, 1)
    inv = _write_inventory(tmp_path, today)

    report = src.check(inventory_path=inv, today=today)

    assert report.total == 3
    assert len(report.ok) == 1
    assert len(report.warn) == 1
    assert len(report.urgent) == 1

    ok_names = {s.name for s in report.ok}
    warn_names = {s.name for s in report.warn}
    urgent_names = {s.name for s in report.urgent}

    assert ok_names == {"SECRET_OK"}, f"OK bucket: {ok_names}"
    assert warn_names == {"SECRET_WARN"}, f"WARN bucket: {warn_names}"
    assert urgent_names == {"SECRET_URGENT"}, f"URGENT bucket: {urgent_names}"

    # Sanity: days_since computed correctly
    warn_s = report.warn[0]
    assert warn_s.days_since == 76
    assert warn_s.rotation_days == 90

    urgent_s = report.urgent[0]
    assert urgent_s.days_since == 91

    # No parse errors on a clean inventory
    assert report.parse_errors == []


def test_check_and_alert_fires_one_alert_per_nonempty_bucket(tmp_path):
    """check_and_alert() must invoke send_alert ONCE per non-empty WARN/URGENT
    bucket, with severity LOW for WARN, HIGH for URGENT, and the secret
    names included in the data payload.

    This is the alerting-shape contract: the CEO gets ONE consolidated WARN
    + ONE consolidated URGENT message per probe run (not per secret).
    """
    import secret_rotation_check as src

    today = date(2026, 6, 1)
    inv = _write_inventory(tmp_path, today)

    captured: list[dict] = []

    def fake_send_alert(severity, title, body, data=None):
        captured.append({
            "severity": severity,
            "title": title,
            "body": body,
            "data": data or {},
        })
        return {"sent": True, "deduped": False, "reason": None}

    result = src.check_and_alert(
        inventory_path=inv,
        today=today,
        send_alert_fn=fake_send_alert,
    )

    # Two non-empty buckets (WARN + URGENT) → two alerts.
    assert len(captured) == 2
    assert result["checked"] == 3
    assert result["ok"] == 1
    assert result["warn"] == 1
    assert result["urgent"] == 1
    assert result["alerts_sent"] == 2
    assert result["parse_errors"] == []

    by_sev = {a["severity"]: a for a in captured}
    assert "HIGH" in by_sev, f"missing HIGH alert: {[a['severity'] for a in captured]}"
    assert "LOW" in by_sev, f"missing LOW alert: {[a['severity'] for a in captured]}"

    # URGENT alert content checks
    urgent_alert = by_sev["HIGH"]
    assert "URGENT" in urgent_alert["title"]
    assert "SECRET_URGENT" in urgent_alert["data"].get("names", [])
    assert urgent_alert["data"].get("urgent_count") == 1
    assert "SECRET_URGENT" in urgent_alert["body"]

    # WARN alert content checks
    warn_alert = by_sev["LOW"]
    assert "WARN" in warn_alert["title"]
    assert "SECRET_WARN" in warn_alert["data"].get("names", [])
    assert warn_alert["data"].get("warn_count") == 1
    assert "SECRET_WARN" in warn_alert["body"]

    # OK secret must NOT appear in any alert body (no leak).
    for a in captured:
        assert "SECRET_OK" not in a["body"], (
            f"OK secret leaked into {a['severity']} alert body"
        )


def test_check_and_alert_no_alerts_when_all_ok(tmp_path):
    """When every secret is comfortably within window, check_and_alert
    fires NO alerts (bucket-level dedup: we don't send 'all green' noise).
    """
    import secret_rotation_check as src

    today = date(2026, 6, 1)
    body = textwrap.dedent(f"""
        schema_version: 1
        defaults:
          rotation_days: 90
          warn_lead_days: 15

        secrets:
          - name: FRESH_SECRET_1
            last_rotated: {(today - timedelta(days=5)).isoformat()}
            rotation_days: 90
            owner: ceo
          - name: FRESH_SECRET_2
            last_rotated: {(today - timedelta(days=30)).isoformat()}
            rotation_days: 90
            owner: ceo
    """).strip()
    inv = tmp_path / "inv.yaml"
    inv.write_text(body, encoding="utf-8")

    captured: list[dict] = []

    def fake_send_alert(severity, title, body, data=None):
        captured.append({"severity": severity, "title": title})
        return {"sent": True, "deduped": False, "reason": None}

    result = src.check_and_alert(
        inventory_path=inv,
        today=today,
        send_alert_fn=fake_send_alert,
    )

    assert len(captured) == 0
    assert result["alerts_sent"] == 0
    assert result["ok"] == 2
    assert result["warn"] == 0
    assert result["urgent"] == 0
