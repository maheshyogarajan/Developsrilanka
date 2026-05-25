"""tests.cbsl.test_rate_d13 — Tier-D6 minor: CBSL fetch fallback wiring.

D13 — CBSL rate "manual", not live.
    Background: tasks.cbsl_rate_fetch.fetch_today_task IS in
    celery_config.py beat_schedule (07:30 UTC daily). In MS1 staging
    the beat process was not running reliably, so cbsl_rates carried
    stale rows and the public estimator surfaced source='manual' /
    is_ird_defensible=false.

    Fix: ADD a third trigger path — admin-only manual button at
    POST /admin/cron/cbsl_rate_fetch_now. The Celery beat schedule
    and the Flask CLI `flask cbsl-fetch` are unchanged.

These tests verify three independent assertions:
  1. The Celery beat entry for cbsl_rate_fetch is configured (so the
     INTENDED automation path is wired and a deploy-time regression
     would surface here).
  2. The admin trigger blueprint registers a POST endpoint with the
     expected URL.
  3. The blueprint uses admin_required — non-admin callers get blocked.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# --------------------------------------------------------------------------- #
# 1) Celery beat schedule wiring                                               #
# --------------------------------------------------------------------------- #


def test_d13_celery_beat_schedule_includes_cbsl_fetch():
    """The Celery beat schedule must include the daily CBSL fetch task.

    Importing celery_config requires no broker (it just constructs the
    Celery app object + populates app.conf.beat_schedule).
    """
    # Avoid REDIS_URL probe — celery_config gracefully falls through to
    # a sqlite broker for import, which is fine for inspection.
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    import celery_config

    schedule = celery_config.app.conf.beat_schedule
    assert "cbsl-rate-daily-prefetch" in schedule, (
        "Celery beat must schedule the daily CBSL fetch (D13 — confirms the "
        "automation path is wired)"
    )
    entry = schedule["cbsl-rate-daily-prefetch"]
    assert entry["task"] == "tasks.cbsl_rate_fetch.fetch_today_task"


# --------------------------------------------------------------------------- #
# 2) Admin manual-trigger blueprint exposes the documented endpoint            #
# --------------------------------------------------------------------------- #


def test_d13_admin_cron_blueprint_exposes_post_endpoint():
    """fiesta.admin.cron_routes.fiesta_admin_cron_bp must declare both
    GET (form) and POST (trigger) handlers on /admin/cron/cbsl_rate_fetch_now.

    Flask Blueprints don't populate `view_functions` until they're mounted
    on an app, so we register against a throwaway Flask instance and walk
    the app's url_map.
    """
    from flask import Flask
    from fiesta.admin.cron_routes import fiesta_admin_cron_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test"

    @app.route("/login")
    def login():
        return "stub"

    app.register_blueprint(fiesta_admin_cron_bp)

    rules_by_url = {}
    for rule in app.url_map.iter_rules():
        rules_by_url.setdefault(rule.rule, set()).update(rule.methods or set())

    url = "/admin/cron/cbsl_rate_fetch_now"
    assert url in rules_by_url, (
        f"URL {url} must be registered by the cron blueprint (D13)"
    )
    methods = rules_by_url[url]
    assert "POST" in methods, f"POST must be a registered method on {url}"
    assert "GET" in methods, f"GET must be a registered method on {url}"


def test_d13_admin_cron_routes_url_via_test_app():
    """Mount the blueprint on a throwaway Flask app and confirm the URL
    is resolvable + the POST returns 401/403 for anon (not 404)."""
    from flask import Flask
    from fiesta.admin.cron_routes import fiesta_admin_cron_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test"
    app.config["WTF_CSRF_ENABLED"] = False

    # admin_required decorator references url_for('login'); register a stub.
    @app.route("/login")
    def login():
        return "login stub"

    app.register_blueprint(fiesta_admin_cron_bp)

    client = app.test_client()

    # Anonymous → admin_required redirects to login (302) or returns 401 JSON
    resp = client.post(
        "/admin/cron/cbsl_rate_fetch_now",
        headers={"Accept": "application/json"},
    )
    assert resp.status_code in (302, 401), (
        f"Anon caller must be denied (got {resp.status_code}); endpoint "
        "must exist (not 404) and must be auth-gated"
    )


# --------------------------------------------------------------------------- #
# 3) The underlying fetch_and_cache is callable + defensive                    #
# --------------------------------------------------------------------------- #


def test_d13_fetch_and_cache_returns_summary_dict_on_failure():
    """fetch_and_cache must always return a dict and never raise — this is
    the contract the admin trigger + the Celery beat both rely on."""
    from datetime import date
    from tasks.cbsl_rate_fetch import fetch_and_cache

    # Pass a date with currencies that ensure no DB write is attempted in
    # the import-failure path. The function is documented as raising
    # nothing — verify.
    result = fetch_and_cache(date(2024, 1, 1), currencies=["USD"])
    assert isinstance(result, dict)
    for key in ("date", "fetched", "skipped", "failed", "cache_written"):
        assert key in result, f"Summary dict must include `{key}`"
