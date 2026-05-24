"""tests/ops_alerts/test_send_alert.py — covers send_alert(), the
dedup window, and the missing-token no-op path.

Run: python -m pytest tests/ops_alerts/ -v
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

# Make repo root importable (tests/ops_alerts/ -> repo root)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import ops_alerts  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_dedup_and_env(monkeypatch):
    """Reset dedup state + ensure no real token leaks from the dev shell."""
    ops_alerts._reset_dedup_state_for_tests()
    # Default: pretend token is present, so tests exercise the send path.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-xyz")
    monkeypatch.setenv("TELEGRAM_OPS_CHAT_ID", "1813046950")
    # Block the home-dir fallback from picking up a real prod token.
    monkeypatch.setattr(
        ops_alerts.Path, "home",
        classmethod(lambda cls: Path("/nonexistent/test-home")),
        raising=False,
    )
    yield
    ops_alerts._reset_dedup_state_for_tests()


def _captured_text(post_mock) -> str:
    """Pull the message text from a call to _post_to_telegram(token, chat_id, text)."""
    assert post_mock.call_count >= 1, "expected _post_to_telegram to be called"
    args, _ = post_mock.call_args
    return args[2]


def test_send_alert_formats_message_and_returns_sent_true():
    with mock.patch.object(ops_alerts, "_post_to_telegram", return_value=True) as p:
        result = ops_alerts.send_alert(
            severity="HIGH",
            title="Healthz failing",
            body="GET /healthz returned 503 for 3 consecutive checks.",
            data={"last_status": 503, "consecutive_failures": 3},
        )

    assert result == {"sent": True, "deduped": False, "reason": None}
    text = _captured_text(p)
    assert "[SEV HIGH]" in text
    assert "Healthz failing" in text
    assert "GET /healthz returned 503" in text
    assert '"last_status": 503' in text
    assert '"consecutive_failures": 3' in text
    # Token + chat_id forwarded
    args, _ = p.call_args
    assert args[0] == "test-token-xyz"
    assert args[1] == "1813046950"


def test_dedup_suppresses_second_send_within_window():
    with mock.patch.object(ops_alerts, "_post_to_telegram", return_value=True) as p:
        first = ops_alerts.send_alert("HIGH", "DupTitle", "first body")
        second = ops_alerts.send_alert("HIGH", "DupTitle", "second body")
        # Different severity bypasses dedup (different key)
        third = ops_alerts.send_alert("MEDIUM", "DupTitle", "third body")

    assert first == {"sent": True, "deduped": False, "reason": None}
    assert second == {"sent": False, "deduped": True, "reason": "deduped"}
    assert third == {"sent": True, "deduped": False, "reason": None}
    # Only first + third actually POSTed
    assert p.call_count == 2


def test_dedup_releases_after_window_elapses():
    fake_now = [1_000_000.0]

    def _patched_check(title, severity, now=None):
        return ops_alerts._dedup_check_and_record.__wrapped__(  # type: ignore[attr-defined]
            title, severity, now=fake_now[0]
        ) if hasattr(ops_alerts._dedup_check_and_record, "__wrapped__") else None

    # Simpler: drive time forward by manipulating the dedup state directly
    # via the public API of the module (record at t0, then advance > window).
    with mock.patch.object(ops_alerts, "_post_to_telegram", return_value=True) as p:
        ops_alerts.send_alert("LOW", "WindowTest", "body")  # records ts=now()
        # Manually rewind the recorded ts to simulate window elapsing.
        with ops_alerts._dedup_lock:
            for key in list(ops_alerts._dedup_state.keys()):
                ops_alerts._dedup_state[key] -= (ops_alerts.DEDUP_WINDOW_SECONDS + 1)
        ops_alerts.send_alert("LOW", "WindowTest", "body again")

    assert p.call_count == 2


def test_missing_token_returns_no_op_without_posting(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    # Also make sure _resolve_telegram_token's filesystem fallbacks find nothing.
    with mock.patch.object(ops_alerts.Path, "exists", return_value=False), \
         mock.patch.object(ops_alerts, "_post_to_telegram", return_value=True) as p:
        result = ops_alerts.send_alert("CRITICAL", "NoToken", "body")

    assert result == {"sent": False, "deduped": False, "reason": "token_missing"}
    assert p.call_count == 0, "must not POST when token missing"


def test_invalid_severity_is_coerced_to_unknown():
    with mock.patch.object(ops_alerts, "_post_to_telegram", return_value=True) as p:
        result = ops_alerts.send_alert("BANANA", "WeirdSev", "body")

    assert result["sent"] is True
    text = _captured_text(p)
    assert "[SEV UNKNOWN]" in text


def test_http_error_returns_sent_false_with_reason():
    with mock.patch.object(ops_alerts, "_post_to_telegram", return_value=False):
        result = ops_alerts.send_alert("HIGH", "HTTPFail", "body")

    assert result == {"sent": False, "deduped": False, "reason": "http_error"}
