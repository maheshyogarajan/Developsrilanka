"""Tests for fiesta.paywall.stripe_config -- key-mode detection + /healthz.

Added 2026-05-20 as part of FIESTA v1 pre-ship deploy-blocker fixes.

Coverage:
  1. validate_stripe_config() reports "missing" mode + non-ready cleanly
     when no env vars set (dev tolerated).
  2. validate_stripe_config() detects "test" vs "live" from key prefix.
  3. validate_stripe_config(strict=True) flags missing-live as an ISSUE.
  4. validate_stripe_config() flags STRIPE_LIVE_WEBHOOK_SECRET / active
     webhook secret mismatch in live mode.
"""
from __future__ import annotations

import os

import pytest

from fiesta.paywall.stripe_config import (
    detect_stripe_mode,
    detect_webhook_mode,
    validate_stripe_config,
)


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #

_STRIPE_ENV_KEYS = (
    "STRIPE_SECRET_KEY",
    "STRIPE_PAYWALL_WEBHOOK_SECRET",
    "STRIPE_WEBHOOK_SECRET",
    "STRIPE_LIVE_WEBHOOK_SECRET",
    "STRIPE_LIVE_KEYS_REQUIRED",
)


@pytest.fixture
def clean_stripe_env(monkeypatch):
    """Strip all Stripe-related env vars so each test starts from a known
    blank state and restores cleanly via monkeypatch."""
    for k in _STRIPE_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    yield monkeypatch


# --------------------------------------------------------------------------- #
# Tests.
# --------------------------------------------------------------------------- #

def test_validate_no_keys_present_returns_missing_with_warnings(clean_stripe_env):
    """Test 1 -- dev environment (no env vars) reports mode=missing,
    ready=False, with warnings (NOT issues) so the app still boots."""
    snap = validate_stripe_config()
    assert snap["mode"] == "missing"
    assert snap["webhook"] == "missing"
    assert snap["ready"] is False
    assert snap["live_required"] is False
    # Warnings reflect dev tolerance; no blocking issues.
    assert len(snap["warnings"]) >= 1
    # Issues list is informational only in non-strict mode.
    # Specifically, "STRIPE_SECRET_KEY not set" should appear in warnings.
    warn_blob = " ".join(snap["warnings"]).lower()
    assert "stripe_secret_key" in warn_blob

    # Helpers concur.
    assert detect_stripe_mode() == "missing"
    assert detect_webhook_mode() == "missing"


def test_validate_test_mode_is_ready_without_strict(clean_stripe_env):
    """Test 2 -- test-mode keys present, strict not required -> ready=True
    and mode='test'. This is the default dev/staging shape."""
    clean_stripe_env.setenv("STRIPE_SECRET_KEY", "sk_test_abc123")
    clean_stripe_env.setenv("STRIPE_PAYWALL_WEBHOOK_SECRET", "whsec_testdef")

    snap = validate_stripe_config()
    assert snap["mode"] == "test"
    assert snap["webhook"] == "configured"
    assert snap["ready"] is True
    assert snap["issues"] == []
    assert detect_stripe_mode() == "test"


def test_validate_strict_mode_blocks_when_only_test_keys_present(
    clean_stripe_env,
):
    """Test 3 -- production deploys set STRIPE_LIVE_KEYS_REQUIRED=1. With
    only test keys present that becomes a blocking ISSUE (ready=False)."""
    clean_stripe_env.setenv("STRIPE_SECRET_KEY", "sk_test_abc123")
    clean_stripe_env.setenv("STRIPE_PAYWALL_WEBHOOK_SECRET", "whsec_testdef")
    clean_stripe_env.setenv("STRIPE_LIVE_KEYS_REQUIRED", "1")

    snap = validate_stripe_config()
    assert snap["live_required"] is True
    assert snap["mode"] == "test"
    assert snap["ready"] is False
    # The mismatch should be in issues, not warnings.
    issue_blob = " ".join(snap["issues"]).lower()
    assert "test key" in issue_blob


def test_validate_live_mode_ready_and_webhook_match(clean_stripe_env):
    """Test 4 -- live keys + explicit STRIPE_LIVE_WEBHOOK_SECRET that
    matches the active webhook secret -> ready=True, live_webhook_match=True.
    Plus the converse: when the explicit live secret does NOT match the
    active one in live mode, it is an ISSUE (likely deploy bug)."""
    # 4a. Matching shape -> ready.
    clean_stripe_env.setenv("STRIPE_SECRET_KEY", "sk_live_realkeyhere")
    clean_stripe_env.setenv(
        "STRIPE_PAYWALL_WEBHOOK_SECRET", "whsec_liveactive"
    )
    clean_stripe_env.setenv(
        "STRIPE_LIVE_WEBHOOK_SECRET", "whsec_liveactive"
    )
    clean_stripe_env.setenv("STRIPE_LIVE_KEYS_REQUIRED", "1")

    snap = validate_stripe_config()
    assert snap["mode"] == "live"
    assert snap["ready"] is True
    assert snap["live_webhook_match"] is True
    assert snap["issues"] == []

    # 4b. Mismatch -> issue, ready=False.
    clean_stripe_env.setenv(
        "STRIPE_LIVE_WEBHOOK_SECRET", "whsec_DIFFERENT_VALUE"
    )
    snap2 = validate_stripe_config()
    assert snap2["mode"] == "live"
    assert snap2["live_webhook_match"] is False
    assert snap2["ready"] is False
    issue_blob = " ".join(snap2["issues"]).lower()
    assert "mismatch" in issue_blob or "does not equal" in issue_blob
