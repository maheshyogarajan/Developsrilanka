"""tests/self_audit_module/test_self_audit.py — Tier D4 / E5 weekly self-audit.

Two cases (per task brief):
  1. ``generate_weekly_report()`` returns a non-empty markdown string with
     all 5 expected section headers present, even when the DB layer is
     unavailable (each section degrades to its own error marker; the
     report still ships).
  2. The Celery task wrapper ``run_weekly_audit_impl`` builds a report and
     invokes ``ops_alerts.send_alert`` with the expected severity/title
     shape; returns ``{"sent": True, ...}`` when the alert path succeeds.

Run:  python -m pytest tests/self_audit_module/ -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

# Make repo root importable (tests/self_audit_module/ -> repo root)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def test_generate_weekly_report_returns_markdown_with_all_sections(monkeypatch):
    """The function must always return a string. When the DB import fails
    (no Flask app context in this test), every section degrades to an
    error-marker line but the report still ships with all 5 section
    headers. This is the contract that keeps the Monday Telegram message
    from going silent even on a partially-broken backend.
    """
    import self_audit

    # Force `from app import db` inside every section to raise — simulates
    # the production-failure shape where the DB connection pool is down.
    # We do this by stubbing the app module so the import succeeds but the
    # `db` attribute raises when touched. The cleaner path is to make the
    # import itself fail, which we do by removing `app` from sys.modules
    # and injecting a stub that has no `db` attribute.
    fake_app = type(sys)("app")
    # Deliberately do NOT set a `db` attribute. `from app import db` will
    # fail with ImportError, which each section helper catches and degrades.
    monkeypatch.setitem(sys.modules, "app", fake_app)

    report = self_audit.generate_weekly_report()

    # Contract: always a non-empty string with all 5 sections present.
    assert isinstance(report, str)
    assert len(report) > 0
    assert "FIESTA Weekly Self-Audit" in report
    assert "**1. Users**" in report or "**1. [SECTION 1:" in report
    assert "**2. Revenue**" in report or "**2. [SECTION 2:" in report
    assert "**3. Operations**" in report or "**3. [SECTION 3:" in report
    assert "**4. Tech**" in report or "**4. [SECTION 4:" in report
    assert "**5. Funnel" in report or "**5. [SECTION 5:" in report

    # Under the Telegram cap.
    assert len(report) <= self_audit.REPORT_MAX_CHARS


def test_run_weekly_audit_impl_calls_send_alert_and_returns_sent(monkeypatch):
    """The task wrapper must build a report, then call ops_alerts.send_alert
    with severity='INFO' + the canonical title; success path returns sent=True.
    """
    # Patch generate_weekly_report so we don't need a DB.
    fake_report = (
        "FIESTA Weekly Self-Audit\nAs of: 2026-05-24 00:00 UTC\n"
        "----------------------------------------\n\n"
        "**1. Users**\n- Total: 42\n\n"
        "**2. Revenue**\n- MRR estimate: Rs 0\n\n"
        "**3. Operations**\n- Open dunning: 0\n\n"
        "**4. Tech**\n- Sentry: DISABLED\n\n"
        "**5. Funnel (last 7d)**\n- landing_view: 0\n"
    )

    import tasks.weekly_self_audit as wsa
    import self_audit
    import ops_alerts

    monkeypatch.setattr(self_audit, "generate_weekly_report", lambda *a, **k: fake_report)

    # Capture the send_alert call.
    captured = {}

    def fake_send_alert(severity, title, body, data=None):
        captured["severity"] = severity
        captured["title"] = title
        captured["body"] = body
        return {"sent": True, "deduped": False, "reason": None}

    monkeypatch.setattr(ops_alerts, "send_alert", fake_send_alert)

    result = wsa.run_weekly_audit_impl()

    # Telegram payload contract.
    assert captured["severity"] == "INFO"
    assert captured["title"] == "FIESTA Weekly Self-Audit"
    assert captured["body"] == fake_report

    # Return contract.
    assert result["sent"] is True
    assert result["report_built"] is True
    assert result["report_chars"] == len(fake_report)
    assert result["deduped"] is False
