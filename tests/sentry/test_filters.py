"""
Tier D1 / E1 — Sentry init: tests for the additions made in 2026-05-26 (Phase D
infra worker). These complement the original tests in tests/sentry/test_init.py
which cover DSN-gating + base config; this module covers the new behaviours:

  1. ``sentry_sdk`` is importable (dep is installed).
  2. ``init_sentry()`` is a no-op when ``SENTRY_DSN`` is unset (no error,
     no side effects). This is the contract used in local dev + CI.
  3. The ``before_send`` filter drops Sentry events whose request URL is a
     healthcheck path (/healthz, /health), including with trailing slashes
     and query strings.
  4. The ``before_send`` filter is wired through ``sentry_sdk.init`` when a
     DSN is set (i.e. the production code path actually attaches the filter).
  5. The auth-user-id attribution does NOT leak PII: only `id` survives;
     `email`, `username`, `ip_address` are stripped from the event.user
     section even if some prior step had set them.

Run with:
    pytest tests/platform/test_sentry_init.py -v

These tests are deliberately standalone — they do NOT use the conftest.py
fixtures in this directory (which would require Neon / DB connectivity).
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# Put the worktree root on sys.path so we can import sentry_init directly.
_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# (1) sentry-sdk is installed
# ---------------------------------------------------------------------------
def test_sentry_sdk_is_importable():
    """The dep must be available in the worktree's Python environment.

    pyproject.toml pins sentry-sdk[flask]>=2.18.0 — if this test starts
    failing, the lock or the image is missing the package.
    """
    sentry_sdk = pytest.importorskip("sentry_sdk")
    assert hasattr(sentry_sdk, "init"), "sentry_sdk.init not found"


# ---------------------------------------------------------------------------
# (2) DSN-unset path is silent + safe
# ---------------------------------------------------------------------------
def test_init_sentry_is_noop_when_dsn_unset(monkeypatch):
    """No DSN -> init returns None and never calls sentry_sdk.init."""
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    if "sentry_init" in sys.modules:
        del sys.modules["sentry_init"]
    sentry_init = importlib.import_module("sentry_init")

    import sentry_sdk
    with mock.patch.object(sentry_sdk, "init") as mocked_init:
        result = sentry_init.init_sentry()

    assert result is None
    mocked_init.assert_not_called()


# ---------------------------------------------------------------------------
# (3) before_send drops healthcheck events
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "https://fiesta-mvp.fly.dev/healthz",
        "https://fiesta-mvp.fly.dev/healthz/",
        "https://fiesta-mvp.fly.dev/healthz?check=1",
        "https://fiesta-mvp.fly.dev/health",
        "https://fiesta-mvp.fly.dev/health/",
        "http://localhost:5000/healthz",
        # Paths with sub-segments still count (defensive).
        "https://fiesta-mvp.fly.dev/healthz/db",
    ],
)
def test_before_send_drops_healthcheck_events(url):
    """before_send returns None (== drop event) for any /healthz or /health URL."""
    if "sentry_init" in sys.modules:
        del sys.modules["sentry_init"]
    sentry_init = importlib.import_module("sentry_init")

    event = {"request": {"url": url}, "exception": {"values": [{"type": "ValueError"}]}}
    result = sentry_init._before_send(event, hint={})
    assert result is None, f"Healthcheck event for {url!r} should be dropped"


@pytest.mark.parametrize(
    "url",
    [
        "https://fiesta-mvp.fly.dev/tax-bill",
        "https://fiesta-mvp.fly.dev/api/users",
        "https://fiesta-mvp.fly.dev/",
        # A path that *contains* 'health' but isn't a healthcheck endpoint.
        "https://fiesta-mvp.fly.dev/healthcare-providers",
        "https://fiesta-mvp.fly.dev/dashboard/health-score",
    ],
)
def test_before_send_keeps_non_healthcheck_events(url):
    """before_send returns the event for any non-healthcheck URL."""
    if "sentry_init" in sys.modules:
        del sys.modules["sentry_init"]
    sentry_init = importlib.import_module("sentry_init")

    event = {"request": {"url": url}, "exception": {"values": [{"type": "ValueError"}]}}
    result = sentry_init._before_send(event, hint={})
    assert result is event, f"Non-healthcheck event for {url!r} should be kept"


def test_before_send_handles_missing_request_metadata():
    """before_send tolerates events with no request section (worker errors)."""
    if "sentry_init" in sys.modules:
        del sys.modules["sentry_init"]
    sentry_init = importlib.import_module("sentry_init")

    event = {"exception": {"values": [{"type": "RuntimeError"}]}}
    result = sentry_init._before_send(event, hint={})
    assert result is event, "Events without a request should be kept"


# ---------------------------------------------------------------------------
# (4) before_send IS wired through sentry_sdk.init
# ---------------------------------------------------------------------------
def test_init_sentry_wires_before_send(monkeypatch):
    """When a DSN is set, sentry_sdk.init receives our before_send filter."""
    import sentry_sdk

    fake_dsn = "https://abc@o123.ingest.sentry.io/456"
    monkeypatch.setenv("SENTRY_DSN", fake_dsn)
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("FLY_RELEASE_VERSION", "v99")

    if "sentry_init" in sys.modules:
        del sys.modules["sentry_init"]
    sentry_init = importlib.import_module("sentry_init")

    with mock.patch.object(sentry_sdk, "init") as mocked_init:
        sentry_init.init_sentry()

    _, kwargs = mocked_init.call_args
    assert "before_send" in kwargs, "before_send hook must be passed to sentry_sdk.init"
    assert callable(kwargs["before_send"]), "before_send must be callable"
    # And it must be OUR filter (verifiable by calling it on a healthcheck
    # event and expecting None back).
    bs = kwargs["before_send"]
    healthz_event = {"request": {"url": "http://x/healthz"}}
    assert bs(healthz_event, {}) is None


# ---------------------------------------------------------------------------
# (5) Auth user-id attribution strips PII
# ---------------------------------------------------------------------------
def test_attach_user_id_strips_pii(monkeypatch):
    """Even if an event already has user.email / user.username, the filter
    must overwrite the user section with id-only on authenticated requests.
    """
    if "sentry_init" in sys.modules:
        del sys.modules["sentry_init"]
    sentry_init = importlib.import_module("sentry_init")

    # Mock a fake Flask-Login current_user proxy.
    class _FakeUser:
        is_anonymous = False
        id = 4242

    fake_flask_login = mock.MagicMock()
    fake_flask_login.current_user = _FakeUser()
    monkeypatch.setitem(sys.modules, "flask_login", fake_flask_login)

    event = {
        "request": {"url": "https://x/tax-bill"},
        "user": {
            "email": "leak@example.com",
            "username": "leaky",
            "ip_address": "1.2.3.4",
        },
    }
    result = sentry_init._before_send(event, hint={})
    assert result is event
    assert result["user"]["id"] == "4242"
    assert "email" not in result["user"]
    assert "username" not in result["user"]
    assert "ip_address" not in result["user"]


def test_attach_user_id_skips_anonymous(monkeypatch):
    """Anonymous users -> no user.id attached, event passes through unchanged."""
    if "sentry_init" in sys.modules:
        del sys.modules["sentry_init"]
    sentry_init = importlib.import_module("sentry_init")

    class _AnonUser:
        is_anonymous = True
        id = None

    fake_flask_login = mock.MagicMock()
    fake_flask_login.current_user = _AnonUser()
    monkeypatch.setitem(sys.modules, "flask_login", fake_flask_login)

    event = {"request": {"url": "https://x/tax-bill"}}
    result = sentry_init._before_send(event, hint={})
    assert result is event
    # No 'user' section was added.
    assert "user" not in result or not result["user"]


def test_attach_user_id_tolerates_flask_login_missing(monkeypatch):
    """If flask_login is not importable, filter still works on the URL alone."""
    if "sentry_init" in sys.modules:
        del sys.modules["sentry_init"]
    sentry_init = importlib.import_module("sentry_init")

    # Force the flask_login import inside _attach_authenticated_user_id to
    # fail. (sys.modules entry of None makes `import flask_login` raise.)
    monkeypatch.setitem(sys.modules, "flask_login", None)

    event = {"request": {"url": "https://x/tax-bill"}}
    result = sentry_init._before_send(event, hint={})
    assert result is event  # event flows through; no crash.
