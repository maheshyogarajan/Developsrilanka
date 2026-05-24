"""
perf_monitoring.py — Per-route latency + per-DB-query timing (Tier D3 / E4).

PURPOSE
-------
Cheap, in-process perf instrumentation that complements the deeper Sentry
auto-tracing (10% sample, see ``sentry_init.py``). Three observable surfaces:

1.  **Response headers** on every request — useful for curl debugging and
    for the browser DevTools network panel:
        X-Response-Time-Ms   — total wall-clock duration of the request
        X-DB-Query-Count     — number of SQLAlchemy cursor executions
        X-DB-Time-Ms         — cumulative DB-side execution time

2.  **Slow-request Telegram alert** when total request duration exceeds the
    ``SLOW_REQUEST_THRESHOLD_MS`` env var (default 5000ms). Severity=HIGH.
    Routed through ``ops_alerts.send_alert`` so the existing 10-minute
    dedup window (per ``title, severity``) absorbs storms automatically.

3.  **Admin route ``/healthz/perf``** returning rolling p50/p95/p99 across
    the last 1000 requests, grouped per (method, route-rule). Pure in-memory
    ring buffer — no DB persistence, no time-series store, no Redis.

SCOPE CAPS (per task brief)
---------------------------
- In-memory only; resets on app restart. Anomaly detection / persistence
  is E3's responsibility, not E4's.
- No per-user timing (avoids PII leakage and bloats the ring buffer).
- Threshold-only alerts (5s default) — no rate-of-change or burst logic.
- Sentry's auto perf traces remain the source-of-truth for deep flame
  graphs; this module is the shallow "always on" telemetry layer.

USAGE
-----
    from perf_monitoring import init_perf_monitoring
    init_perf_monitoring(app, db)            # before first request

The init function attaches:
  * Flask ``before_request`` / ``after_request`` hooks (request timing).
  * SQLAlchemy ``before_cursor_execute`` / ``after_cursor_execute`` event
    listeners on ``db.engine`` (DB-query timing).
  * The ``/healthz/perf`` admin-gated route.

DESIGN NOTES
------------
- Per-request state on ``flask.g`` (not module-level) — thread-safe under
  Flask's request context, and naturally torn down by Flask after each
  request.
- SQLAlchemy listeners use ``conn.info`` (per-connection scratch dict) to
  carry the query start timestamp from before-execute to after-execute.
  This works for both sync and Gevent-pooled connection pools.
- Ring buffer is a ``collections.deque(maxlen=RING_BUFFER_SIZE)`` —
  O(1) append, O(N) percentile read. For N=1000 a percentile scan is
  ~50µs on commodity hardware, well within /healthz/perf's budget.
- The buffer holds plain tuples ``(route_rule, method, duration_ms,
  db_count, db_time_ms)`` — ~80 bytes each. 1000 entries ≈ 80 KB.
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Optional

from flask import Flask, g, jsonify, request

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

RING_BUFFER_SIZE = 1000

DEFAULT_SLOW_REQUEST_THRESHOLD_MS = 5000


def _slow_request_threshold_ms() -> int:
    """Read the slow-request threshold from env on every request so ops can
    tune without a redeploy (Fly secrets reload mid-process).
    """
    raw = os.environ.get("SLOW_REQUEST_THRESHOLD_MS")
    if not raw:
        return DEFAULT_SLOW_REQUEST_THRESHOLD_MS
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "perf_monitoring: SLOW_REQUEST_THRESHOLD_MS=%r is not an int — "
            "falling back to %d",
            raw, DEFAULT_SLOW_REQUEST_THRESHOLD_MS,
        )
        return DEFAULT_SLOW_REQUEST_THRESHOLD_MS


# --------------------------------------------------------------------------- #
# Ring buffer
# --------------------------------------------------------------------------- #

_buffer_lock = Lock()
_ring_buffer: "deque[tuple[str, str, float, int, float]]" = deque(maxlen=RING_BUFFER_SIZE)


def _record_sample(
    route_rule: str,
    method: str,
    duration_ms: float,
    db_count: int,
    db_time_ms: float,
) -> None:
    """Append one observation to the ring buffer (thread-safe)."""
    with _buffer_lock:
        _ring_buffer.append((route_rule, method, duration_ms, db_count, db_time_ms))


def _reset_buffer_for_tests() -> None:
    """Test-only hook. Not exported in __all__."""
    with _buffer_lock:
        _ring_buffer.clear()


def _snapshot_buffer() -> list[tuple[str, str, float, int, float]]:
    """Return a shallow copy under the lock (cheap; ≤1000 items)."""
    with _buffer_lock:
        return list(_ring_buffer)


# --------------------------------------------------------------------------- #
# Percentile helper (pure-Python; no numpy dependency)
# --------------------------------------------------------------------------- #

def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolation percentile. Empty input → 0.0."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    k = (len(sorted_values) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = k - lo
    return float(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac)


# --------------------------------------------------------------------------- #
# Flask request hooks
# --------------------------------------------------------------------------- #

def _before_request() -> None:
    """Stamp the request start and zero the DB counters."""
    g.req_start_ts = time.perf_counter()
    g.db_query_count = 0
    g.db_time_seconds = 0.0


def _after_request(response):
    """Stamp duration, write the 3 X- headers, push a sample, and fire a
    slow-request alert if over threshold.

    Never raises — instrumentation failure must NEVER break the response.
    """
    try:
        start = getattr(g, "req_start_ts", None)
        if start is None:
            return response
        duration_s = time.perf_counter() - start
        duration_ms = duration_s * 1000.0
        db_count = int(getattr(g, "db_query_count", 0))
        db_time_ms = float(getattr(g, "db_time_seconds", 0.0)) * 1000.0

        # Headers — formatted to 2dp so curl output stays compact.
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
        response.headers["X-DB-Query-Count"] = str(db_count)
        response.headers["X-DB-Time-Ms"] = f"{db_time_ms:.2f}"

        # Route rule (e.g. "/receipts/<int:id>") is preferred over the raw
        # path for ring-buffer keying: it groups all instances of a
        # parameterised route under one bucket. Fall back to the path for
        # 404s / static / error-handler responses where url_rule is None.
        url_rule = request.url_rule
        route_rule = url_rule.rule if url_rule is not None else request.path
        method = request.method

        _record_sample(route_rule, method, duration_ms, db_count, db_time_ms)

        # Slow-request alert. Imported lazily so test envs without Telegram
        # config / network never pay the import cost on the hot path.
        threshold_ms = _slow_request_threshold_ms()
        if duration_ms >= threshold_ms:
            try:
                from ops_alerts import send_alert
                send_alert(
                    severity="HIGH",
                    title="Slow request",
                    body=(
                        f"{method} {route_rule} took {duration_ms:.0f}ms "
                        f"(threshold {threshold_ms}ms; "
                        f"{db_count} DB queries / {db_time_ms:.0f}ms DB)."
                    ),
                    data={
                        "path": request.path,
                        "route_rule": route_rule,
                        "method": method,
                        "duration_ms": round(duration_ms, 2),
                        "db_query_count": db_count,
                        "db_time_ms": round(db_time_ms, 2),
                        "threshold_ms": threshold_ms,
                        "status_code": response.status_code,
                    },
                )
            except Exception as e:  # pragma: no cover — defensive
                logger.warning("perf_monitoring: slow-request alert failed: %s", e)
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("perf_monitoring._after_request: %s", e)
    return response


# --------------------------------------------------------------------------- #
# SQLAlchemy cursor listeners
# --------------------------------------------------------------------------- #

def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    """Stamp the query start on the connection's per-execution scratch dict."""
    try:
        # Use a stack to handle nested / re-entrant cursor executions safely.
        stack = conn.info.setdefault("_perf_query_starts", [])
        stack.append(time.perf_counter())
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("perf_monitoring._before_cursor_execute: %s", e)


def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    """Add the elapsed query duration to ``flask.g`` accumulators."""
    try:
        stack = conn.info.get("_perf_query_starts")
        if not stack:
            return
        start = stack.pop()
        elapsed = time.perf_counter() - start
        # Only count if we're inside a Flask request context. Background
        # tasks (Celery jobs that import db) shouldn't pollute request
        # metrics. ``g`` raises RuntimeError if no context — guard with
        # ``has_app_context``.
        from flask import has_request_context
        if has_request_context():
            g.db_query_count = int(getattr(g, "db_query_count", 0)) + 1
            g.db_time_seconds = float(getattr(g, "db_time_seconds", 0.0)) + elapsed
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("perf_monitoring._after_cursor_execute: %s", e)


# --------------------------------------------------------------------------- #
# /healthz/perf admin route
# --------------------------------------------------------------------------- #

def _build_perf_summary() -> dict:
    """Compute the per-route summary used by ``/healthz/perf``."""
    samples = _snapshot_buffer()
    overall_durations: list[float] = []
    by_route: dict[tuple[str, str], list[float]] = defaultdict(list)
    db_count_total = 0
    db_time_total = 0.0

    for route_rule, method, duration_ms, db_count, db_time_ms in samples:
        overall_durations.append(duration_ms)
        by_route[(method, route_rule)].append(duration_ms)
        db_count_total += db_count
        db_time_total += db_time_ms

    routes_out = []
    for (method, rule), durations in sorted(by_route.items()):
        sd = sorted(durations)
        routes_out.append({
            "method": method,
            "route": rule,
            "n": len(sd),
            "p50_ms": round(_percentile(sd, 50), 2),
            "p95_ms": round(_percentile(sd, 95), 2),
            "p99_ms": round(_percentile(sd, 99), 2),
            "max_ms": round(sd[-1], 2),
        })
    # Order by p95 desc — most useful for spotting regressions first.
    routes_out.sort(key=lambda r: r["p95_ms"], reverse=True)

    overall_sorted = sorted(overall_durations)
    return {
        "buffer_size": RING_BUFFER_SIZE,
        "buffer_filled": len(samples),
        "slow_request_threshold_ms": _slow_request_threshold_ms(),
        "overall": {
            "n": len(overall_sorted),
            "p50_ms": round(_percentile(overall_sorted, 50), 2),
            "p95_ms": round(_percentile(overall_sorted, 95), 2),
            "p99_ms": round(_percentile(overall_sorted, 99), 2),
            "db_query_count_total": db_count_total,
            "db_time_ms_total": round(db_time_total, 2),
        },
        "routes": routes_out,
    }


# --------------------------------------------------------------------------- #
# Public init
# --------------------------------------------------------------------------- #

def init_perf_monitoring(app: Flask, db) -> None:
    """Wire request hooks, SQLAlchemy listeners, and ``/healthz/perf`` route.

    Idempotent — safe to call once at app creation. Calling twice will
    register duplicate hooks (Flask allows it), so callers should call once.
    """
    # 1) Flask hooks
    app.before_request(_before_request)
    app.after_request(_after_request)

    # 2) SQLAlchemy cursor events. The engine isn't created until the first
    #    app context, so attach the listener via the Engine event system on
    #    the class — fires for every engine that's already / later bound to
    #    the SQLAlchemy instance.
    try:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine
        event.listen(Engine, "before_cursor_execute", _before_cursor_execute)
        event.listen(Engine, "after_cursor_execute", _after_cursor_execute)
    except Exception as e:  # pragma: no cover — defensive
        logger.error("perf_monitoring: SQLAlchemy listener install failed: %s", e)

    # 3) Admin-gated /healthz/perf
    try:
        from fiesta.auth.decorators import admin_required
    except Exception as e:  # pragma: no cover — defensive
        logger.error(
            "perf_monitoring: admin_required import failed (%s); "
            "/healthz/perf will NOT be registered", e,
        )
        return

    @app.route("/healthz/perf")
    @admin_required
    def healthz_perf():
        return jsonify(_build_perf_summary())

    logger.info(
        "perf_monitoring: initialized (buffer=%d, slow_threshold=%dms)",
        RING_BUFFER_SIZE, _slow_request_threshold_ms(),
    )


__all__ = [
    "init_perf_monitoring",
    "RING_BUFFER_SIZE",
    "DEFAULT_SLOW_REQUEST_THRESHOLD_MS",
]
