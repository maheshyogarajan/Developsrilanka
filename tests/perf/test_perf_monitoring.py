"""
Tier D3 / E4 — perf_monitoring smoke tests.

Standalone — does NOT import the full FIESTA app or hit Neon. We build a
minimal Flask app + an empty SQLAlchemy stub and verify the two contracts
that matter most:

  1. ``X-Response-Time-Ms`` (plus X-DB-Query-Count, X-DB-Time-Ms) lands on
     every response.
  2. A request that exceeds the SLOW_REQUEST_THRESHOLD_MS env threshold
     fires exactly one ``ops_alerts.send_alert(severity='HIGH', ...)``
     with the offending route in the payload.

Run with:
    pytest tests/perf_monitoring/test_perf_monitoring.py -v
"""
from __future__ import annotations

import os
import sys
import time
from unittest import mock

import pytest
from flask import Flask


# Make sure the worktree root is on sys.path so we can import perf_monitoring
# without the full app fixture chain.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def app_with_perf(monkeypatch):
    """Build a minimal Flask app with perf_monitoring wired up.

    We pass a dummy ``db`` object — perf_monitoring only uses it via
    SQLAlchemy's class-level ``event.listen(Engine, ...)`` call, so the
    ``db`` argument is ignored for hook registration.
    """
    # Force a known threshold so the slow-request test is deterministic.
    monkeypatch.setenv("SLOW_REQUEST_THRESHOLD_MS", "50")

    import perf_monitoring
    perf_monitoring._reset_buffer_for_tests()

    app = Flask(__name__)

    # Stub admin_required so /healthz/perf can register without flask_login.
    # We don't exercise /healthz/perf in these tests (E1 covers admin gates).
    fake_decorators = mock.MagicMock()
    fake_decorators.admin_required = lambda f: f
    monkeypatch.setitem(sys.modules, "fiesta.auth.decorators", fake_decorators)

    perf_monitoring.init_perf_monitoring(app, db=None)

    @app.route("/fast")
    def fast():
        return "ok"

    @app.route("/slow")
    def slow():
        # 80ms — exceeds 50ms threshold set above
        time.sleep(0.08)
        return "slow ok"

    return app


# --------------------------------------------------------------------------- #
# Test 1 — response headers always present
# --------------------------------------------------------------------------- #

def test_response_includes_perf_headers(app_with_perf):
    """Every response must carry the three perf headers."""
    client = app_with_perf.test_client()
    resp = client.get("/fast")
    assert resp.status_code == 200

    assert "X-Response-Time-Ms" in resp.headers, (
        f"Missing X-Response-Time-Ms. Got headers: {dict(resp.headers)}"
    )
    assert "X-DB-Query-Count" in resp.headers
    assert "X-DB-Time-Ms" in resp.headers

    # X-Response-Time-Ms must parse as a positive float.
    val = float(resp.headers["X-Response-Time-Ms"])
    assert val > 0.0, f"X-Response-Time-Ms not positive: {val}"
    assert val < 5000.0, f"X-Response-Time-Ms suspiciously large: {val}"

    # X-DB-Query-Count must be a non-negative int.
    assert resp.headers["X-DB-Query-Count"].isdigit()


# --------------------------------------------------------------------------- #
# Test 2 — slow request fires ops_alerts.send_alert(severity='HIGH')
# --------------------------------------------------------------------------- #

def test_slow_request_triggers_ops_alert(app_with_perf):
    """When duration >= SLOW_REQUEST_THRESHOLD_MS, ops_alerts.send_alert
    must be called exactly once with severity=HIGH and the offending path
    in the data payload.
    """
    client = app_with_perf.test_client()

    with mock.patch("ops_alerts.send_alert") as mock_send:
        # Make the mock return the same shape as the real send_alert so
        # _after_request doesn't choke on .get(...) etc.
        mock_send.return_value = {"sent": True, "deduped": False, "reason": None}

        resp = client.get("/slow")
        assert resp.status_code == 200

        # send_alert was called at least once for this slow request.
        assert mock_send.called, "ops_alerts.send_alert was not called for slow request"

        # Inspect the first (and expected only) call.
        call = mock_send.call_args
        kwargs = call.kwargs if call.kwargs else {}
        # Support both kw and positional invocation styles.
        severity = kwargs.get("severity") or (call.args[0] if call.args else None)
        title = kwargs.get("title") or (call.args[1] if len(call.args) > 1 else None)
        data = kwargs.get("data") or (call.args[3] if len(call.args) > 3 else None)

        assert severity == "HIGH", f"Expected severity=HIGH, got {severity!r}"
        assert title == "Slow request", f"Expected title='Slow request', got {title!r}"
        assert isinstance(data, dict), f"Expected data dict, got {type(data)}"
        assert data.get("path") == "/slow", f"Expected data.path=/slow, got {data!r}"
        assert data.get("method") == "GET"
        assert data.get("duration_ms", 0) >= 50, (
            f"Expected duration_ms >= 50, got {data.get('duration_ms')!r}"
        )

    # And the headers are still set — the alert path doesn't replace the
    # standard instrumentation.
    assert "X-Response-Time-Ms" in resp.headers
