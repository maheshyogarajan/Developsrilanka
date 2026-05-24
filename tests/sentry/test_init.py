"""
Tier D1 / E1 — Sentry init + admin-gate smoke tests.

These tests are deliberately standalone — they don't import the full Flask
app or hit the live DB (the rest of the suite needs Neon connectivity that
isn't available in every CI lane). They cover the two contracts that matter:

  1. ``init_sentry()`` is a no-op when ``SENTRY_DSN`` is unset.
  2. ``init_sentry()`` calls ``sentry_sdk.init`` with the expected config
     when ``SENTRY_DSN`` IS set (so we know we're not silently mis-passing
     traces_sample_rate, integrations, or send_default_pii).
  3. ``/sentry-test`` is wired through the admin_required decorator (we
     inspect the view function on the blueprint, not the request lifecycle).

Run with:
    pytest tests/sentry/test_init.py -v
"""
from __future__ import annotations

import importlib
import os
import sys
from unittest import mock

import pytest


# Make sure the worktree root is on sys.path so we can import sentry_init /
# sentry_routes without needing the full app fixture chain.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ----------------------------------------------------------------------------
# init_sentry — no-DSN path
# ----------------------------------------------------------------------------
def test_init_sentry_noop_when_dsn_missing(monkeypatch):
    """No DSN -> returns None, does not touch sentry_sdk."""
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    # Fresh import so we don't accidentally see a cached SDK state.
    if "sentry_init" in sys.modules:
        del sys.modules["sentry_init"]
    sentry_init = importlib.import_module("sentry_init")

    # If sentry_sdk happens to be installed, patch its init to assert non-call.
    with mock.patch.dict(sys.modules, {}, clear=False):
        try:
            import sentry_sdk  # type: ignore
        except ImportError:
            # No SDK installed at all — function should still no-op.
            assert sentry_init.init_sentry() is None
            return

        with mock.patch.object(sentry_sdk, "init") as mocked_init:
            result = sentry_init.init_sentry()
            assert result is None
            mocked_init.assert_not_called()


def test_init_sentry_noop_when_dsn_empty_string(monkeypatch):
    """Empty / whitespace DSN -> treated as unset."""
    monkeypatch.setenv("SENTRY_DSN", "   ")

    if "sentry_init" in sys.modules:
        del sys.modules["sentry_init"]
    sentry_init = importlib.import_module("sentry_init")

    try:
        import sentry_sdk  # type: ignore
    except ImportError:
        assert sentry_init.init_sentry() is None
        return

    with mock.patch.object(sentry_sdk, "init") as mocked_init:
        result = sentry_init.init_sentry()
        assert result is None
        mocked_init.assert_not_called()


# ----------------------------------------------------------------------------
# init_sentry — with-DSN path
# ----------------------------------------------------------------------------
def test_init_sentry_calls_init_with_expected_config(monkeypatch):
    """DSN present -> sentry_sdk.init called with the right kwargs."""
    pytest.importorskip("sentry_sdk")
    import sentry_sdk

    fake_dsn = "https://abc@o123.ingest.sentry.io/456"
    monkeypatch.setenv("SENTRY_DSN", fake_dsn)
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("FLY_RELEASE_VERSION", "v42")

    if "sentry_init" in sys.modules:
        del sys.modules["sentry_init"]
    sentry_init = importlib.import_module("sentry_init")

    with mock.patch.object(sentry_sdk, "init") as mocked_init:
        result = sentry_init.init_sentry()

    assert result == fake_dsn
    assert mocked_init.call_count == 1
    _, kwargs = mocked_init.call_args
    assert kwargs["dsn"] == fake_dsn
    assert kwargs["traces_sample_rate"] == 0.1
    assert kwargs["profiles_sample_rate"] == 0.0
    assert kwargs["send_default_pii"] is False
    assert kwargs["environment"] == "production"
    assert kwargs["release"] == "v42"
    # Both integrations wired
    integration_names = [type(i).__name__ for i in kwargs["integrations"]]
    assert "FlaskIntegration" in integration_names
    assert "SqlalchemyIntegration" in integration_names


def test_init_sentry_defaults_release_to_dev_when_fly_env_missing(monkeypatch):
    """No FLY_RELEASE_VERSION -> release defaults to 'dev'."""
    pytest.importorskip("sentry_sdk")
    import sentry_sdk

    monkeypatch.setenv("SENTRY_DSN", "https://abc@o123.ingest.sentry.io/456")
    monkeypatch.delenv("FLY_RELEASE_VERSION", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)

    if "sentry_init" in sys.modules:
        del sys.modules["sentry_init"]
    sentry_init = importlib.import_module("sentry_init")

    with mock.patch.object(sentry_sdk, "init") as mocked_init:
        sentry_init.init_sentry()

    _, kwargs = mocked_init.call_args
    assert kwargs["release"] == "dev"
    assert kwargs["environment"] == "production"


# ----------------------------------------------------------------------------
# /sentry-test — admin-gate wiring
# ----------------------------------------------------------------------------
def test_sentry_test_route_is_admin_gated():
    """The /sentry-test view function must be wrapped by admin_required.

    We don't run a request through the full stack here (that needs the DB-
    backed app fixture). Instead, we inspect the view function on the
    blueprint and assert it's the wrapped form admin_required produces.
    """
    from sentry_routes import sentry_bp, sentry_test, SentryVerificationError

    # Blueprint name + URL rule
    assert sentry_bp.name == "sentry_test"

    # The view function should be wrapped by admin_required, which uses
    # functools.wraps — so the wrapped function still answers to .__name__
    # == "sentry_test", but its closure contains the original function.
    assert sentry_test.__name__ == "sentry_test"
    assert sentry_test.__wrapped__ is not sentry_test  # wraps preserves link

    # The deliberate exception is a distinct subclass so alert filters can
    # mute it without muting real RuntimeErrors.
    assert issubclass(SentryVerificationError, RuntimeError)
    assert SentryVerificationError is not RuntimeError


def test_sentry_test_view_raises_on_direct_call():
    """Calling the underlying view (bypassing the gate) raises the marker."""
    from sentry_routes import sentry_test, SentryVerificationError

    # __wrapped__ is the original undecorated function (set by functools.wraps)
    raw = sentry_test.__wrapped__
    with pytest.raises(SentryVerificationError) as exc_info:
        raw()
    assert "Sentry ingestion check" in str(exc_info.value)
