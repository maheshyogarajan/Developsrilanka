"""tests.perf.test_agreements_latency — Tier D6 / D8 SLO tests.

Verifies the perf-hot-path helpers (`/agreements/service/<id>` +
`/agreements/rental/<id>`) actually use the perf_cache, and that the per-route
3000ms WARNING fires when a route on `/agreements/*` exceeds the SLO budget.

DOES NOT spin up the full FIESTA app (no Neon, no Stripe, no Flask-Login).
The goal here is to assert the architecture is wired correctly + that the
helpers used by the route functions consistently amortise the expensive
calls (the only way the warm-path could fail to drop from 5-6s to <1s is if
the cache plumbing is wrong).

Three test groups:
  1. `_protected_deductions_cached` (service) caches: first call computes,
     subsequent calls within TTL return the cached value without re-running.
  2. `_resolve_property_bundle_cached` + `_rental_protected_deductions_cached`
     (rental) cache equivalently.
  3. The instrumentation WARNING fires for an /agreements/* route over 3000ms.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from unittest import mock

import pytest


# Ensure repo root on path for direct imports without full app bootstrap.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


@pytest.fixture(autouse=True)
def _reset_cache():
    from fiesta import perf_cache
    perf_cache._reset_for_tests()
    yield
    perf_cache._reset_for_tests()


# --------------------------------------------------------------------------- #
# Group 1: service-side hot-path helpers cache as advertised
# --------------------------------------------------------------------------- #


def test_protected_deductions_cached_hits_after_first_call():
    """A second call within the TTL must return the cached value WITHOUT
    re-running `compute_protected_deductions_lkr`."""
    from fiesta.agreements import service_routes

    call_count = {"n": 0}

    def fake_compute(user, sp, *, is_property):
        call_count["n"] += 1
        return 12345

    with mock.patch.object(
        service_routes, "compute_protected_deductions_lkr", side_effect=fake_compute
    ):
        out1 = service_routes._protected_deductions_cached(
            user_id=42, sp_id="sp-A", user_obj=object(), sp_obj=object(),
        )
        out2 = service_routes._protected_deductions_cached(
            user_id=42, sp_id="sp-A", user_obj=object(), sp_obj=object(),
        )

    assert out1 == 12345
    assert out2 == 12345
    assert call_count["n"] == 1, (
        f"Expected compute to run once across 2 cached calls, ran {call_count['n']}"
    )


def test_protected_deductions_cache_keyed_by_sp_id():
    """Two distinct SP ids must run compute independently — no key collision."""
    from fiesta.agreements import service_routes

    call_count = {"n": 0}

    def fake_compute(user, sp, *, is_property):
        call_count["n"] += 1
        return 99

    with mock.patch.object(
        service_routes, "compute_protected_deductions_lkr", side_effect=fake_compute
    ):
        service_routes._protected_deductions_cached(
            user_id=1, sp_id="sp-A", user_obj=None, sp_obj=None,
        )
        service_routes._protected_deductions_cached(
            user_id=1, sp_id="sp-B", user_obj=None, sp_obj=None,
        )

    assert call_count["n"] == 2


def test_protected_deductions_cache_invalidate_drops_entry():
    """invalidate_service_agreement_cache(user, sp) should bust the entry so
    the next call recomputes."""
    from fiesta.agreements import service_routes

    call_count = {"n": 0}

    def fake_compute(user, sp, *, is_property):
        call_count["n"] += 1
        return 7

    with mock.patch.object(
        service_routes, "compute_protected_deductions_lkr", side_effect=fake_compute
    ):
        service_routes._protected_deductions_cached(
            user_id=1, sp_id="sp-A", user_obj=None, sp_obj=None,
        )
        service_routes.invalidate_service_agreement_cache(1, "sp-A")
        service_routes._protected_deductions_cached(
            user_id=1, sp_id="sp-A", user_obj=None, sp_obj=None,
        )

    assert call_count["n"] == 2


def test_protected_deductions_cache_invalidate_all_for_user():
    """invalidate with sp_id=None should drop every entry for the user."""
    from fiesta.agreements import service_routes

    call_count = {"n": 0}

    def fake_compute(user, sp, *, is_property):
        call_count["n"] += 1
        return 0

    with mock.patch.object(
        service_routes, "compute_protected_deductions_lkr", side_effect=fake_compute
    ):
        service_routes._protected_deductions_cached(
            user_id=1, sp_id="A", user_obj=None, sp_obj=None,
        )
        service_routes._protected_deductions_cached(
            user_id=1, sp_id="B", user_obj=None, sp_obj=None,
        )
        # invalidate every SP for user 1
        dropped = service_routes.invalidate_service_agreement_cache(1)
        assert dropped >= 2  # at least the 2 sp_protected_lkr keys

        # next reads should re-compute both
        service_routes._protected_deductions_cached(
            user_id=1, sp_id="A", user_obj=None, sp_obj=None,
        )
        service_routes._protected_deductions_cached(
            user_id=1, sp_id="B", user_obj=None, sp_obj=None,
        )

    assert call_count["n"] == 4


# --------------------------------------------------------------------------- #
# Group 2: rental-side hot-path helpers cache as advertised
# --------------------------------------------------------------------------- #


def test_rental_protected_deductions_cached_hits_after_first_call():
    from fiesta.agreements import rental_routes

    call_count = {"n": 0}

    def fake_compute(user, prop, *, is_property):
        call_count["n"] += 1
        return 55555

    # The helper guards on `_compute_protected_deductions_lkr is None`; force
    # it to our mock + provide a non-None property_obj.
    with mock.patch.object(
        rental_routes, "_compute_protected_deductions_lkr", new=fake_compute
    ):
        out1 = rental_routes._rental_protected_deductions_cached(
            user_id=10, property_id=99, user_obj=object(), property_obj=object(),
        )
        out2 = rental_routes._rental_protected_deductions_cached(
            user_id=10, property_id=99, user_obj=object(), property_obj=object(),
        )

    assert out1 == 55555
    assert out2 == 55555
    assert call_count["n"] == 1


def test_rental_protected_deductions_returns_zero_for_missing_property():
    from fiesta.agreements import rental_routes

    out = rental_routes._rental_protected_deductions_cached(
        user_id=10, property_id=99, user_obj=None, property_obj=None,
    )
    assert out == 0


def test_rental_cache_invalidate_drops_entry():
    from fiesta.agreements import rental_routes

    call_count = {"n": 0}

    def fake_compute(user, prop, *, is_property):
        call_count["n"] += 1
        return 1234

    with mock.patch.object(
        rental_routes, "_compute_protected_deductions_lkr", new=fake_compute
    ):
        rental_routes._rental_protected_deductions_cached(
            user_id=10, property_id=99, user_obj=object(), property_obj=object(),
        )
        rental_routes.invalidate_rental_agreement_cache(10, 99)
        rental_routes._rental_protected_deductions_cached(
            user_id=10, property_id=99, user_obj=object(), property_obj=object(),
        )

    assert call_count["n"] == 2


# --------------------------------------------------------------------------- #
# Group 3: perf_monitoring WARNING fires for /agreements/* breaches
# --------------------------------------------------------------------------- #


@pytest.fixture
def app_with_perf(monkeypatch):
    """Minimal Flask app + perf_monitoring init, no real DB."""
    # Default 5000ms global threshold; agreement-specific WARNING is 3000ms.
    monkeypatch.setenv("SLOW_REQUEST_THRESHOLD_MS", "10000")

    import perf_monitoring
    perf_monitoring._reset_buffer_for_tests()

    from flask import Flask
    app = Flask(__name__)

    # Stub admin_required so /healthz/perf + /admin/perf register cleanly.
    fake_decorators = mock.MagicMock()
    fake_decorators.admin_required = lambda f: f
    monkeypatch.setitem(sys.modules, "fiesta.auth.decorators", fake_decorators)

    perf_monitoring.init_perf_monitoring(app, db=None)

    @app.route("/agreements/service/<sp_id>")
    def fake_agreement(sp_id):
        time.sleep(3.1)  # exceed 3000ms agreement SLO
        return "ok"

    @app.route("/agreements/rental/<int:pid>")
    def fake_rental(pid):
        return "fast ok"

    return app, perf_monitoring


def test_agreement_slo_breach_logs_warning(app_with_perf, caplog):
    app, perf_mod = app_with_perf
    client = app.test_client()

    with caplog.at_level(logging.WARNING, logger="perf_monitoring"):
        resp = client.get("/agreements/service/SP-001")
    assert resp.status_code == 200

    breach_logs = [
        rec for rec in caplog.records
        if "agreement perf SLO breach" in rec.getMessage()
    ]
    assert breach_logs, (
        f"Expected SLO-breach WARNING for /agreements/service/<sp_id>. "
        f"Records: {[r.getMessage() for r in caplog.records]}"
    )
    msg = breach_logs[0].getMessage()
    assert "/agreements/service/" in msg
    assert "GET" in msg


def test_fast_agreement_route_does_not_warn(app_with_perf, caplog):
    app, perf_mod = app_with_perf
    client = app.test_client()

    with caplog.at_level(logging.WARNING, logger="perf_monitoring"):
        resp = client.get("/agreements/rental/42")
    assert resp.status_code == 200

    breach_logs = [
        rec for rec in caplog.records
        if "agreement perf SLO breach" in rec.getMessage()
    ]
    assert not breach_logs, (
        f"Fast route should NOT trigger SLO breach. Got: "
        f"{[r.getMessage() for r in breach_logs]}"
    )


def test_admin_perf_route_returns_cache_stats(app_with_perf):
    app, _ = app_with_perf
    client = app.test_client()

    # Seed a cache entry so the stats show something
    from fiesta import perf_cache
    perf_cache.set("foo", "bar", seconds=60)

    resp = client.get("/admin/perf")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "cache" in body
    assert body["cache"]["size"] >= 1
    assert "agreement_slo_breaches" in body


def test_healthz_perf_includes_response_time_header(app_with_perf):
    """Reaffirm the X-Response-Time-Ms header is on every response, including
    the perf admin route itself."""
    app, _ = app_with_perf
    client = app.test_client()
    resp = client.get("/healthz/perf")
    assert "X-Response-Time-Ms" in resp.headers
