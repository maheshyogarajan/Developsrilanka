"""Tier D5 / E6 — A/B testing harness tests.

3 cases (per task spec):
  1. Deterministic assignment — same (experiment_key, anon_id) hashes to
     the same variant across many calls, and across a population of
     visitors the distribution is roughly uniform.
  2. New visitor with an active experiment gets one of the declared
     variants (not 'control'), and the assignment is persisted to
     ab_assignment.
  3. No active experiment (None returned by query) -> fallback variant
     is 'control', no assignment row written.

DB + flask context are stubbed via mocks so this runs without a live
Postgres or migrations applied — mirrors
tests/dunning/test_dunning_sequence.py and
tests/lifecycle_drip_module/test_lifecycle_drip.py.
"""
from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# --------------------------------------------------------------------------- #
# Helpers — build a fake flask.g + request context the helper expects.
# --------------------------------------------------------------------------- #


class _FakeG:
    """flask.g replacement — plain attribute bag with hasattr/setattr/getattr."""
    pass


class _FakeRequest:
    """Minimal flask.request stub: just .cookies.get('session_anon_id')."""
    def __init__(self, anon_cookie: str | None = None):
        self.cookies = {"session_anon_id": anon_cookie} if anon_cookie else {}

    @property
    def cookies_obj(self):
        # Mirror flask request.cookies API: .get(key)
        bag = self.cookies
        return SimpleNamespace(get=lambda key, default=None: bag.get(key, default))


def _patch_flask(anon_cookie: str | None):
    """Returns the set of patchers to enter for one get_variant call."""
    fake_req = SimpleNamespace(
        cookies=SimpleNamespace(
            get=lambda key, default=None: (
                anon_cookie if key == "session_anon_id" and anon_cookie else default
            ),
        ),
    )
    fake_g = _FakeG()
    return [
        patch("ab_test.g", fake_g),
        patch("ab_test.request", fake_req),
    ]


# --------------------------------------------------------------------------- #
# Test 1: Deterministic assignment + uniform distribution.
# --------------------------------------------------------------------------- #


def test_deterministic_assignment_same_anon_same_variant():
    """Same (experiment_key, anon_id) MUST hash to the same variant on
    every call. Different anon_ids spread roughly uniformly across the
    declared variants."""
    from ab_test import _hash_to_bucket

    variants = ["control", "green", "orange"]
    key = "s0_hero_color"

    # Determinism: 1000 repeats of the same input -> single bucket.
    buckets = {_hash_to_bucket(key, "anon_abc", len(variants)) for _ in range(1000)}
    assert len(buckets) == 1, f"hash drifted across calls: {buckets}"
    assert 0 <= next(iter(buckets)) < 3

    # Uniformity: across 3000 distinct anon ids, each variant gets
    # roughly a third (allow generous tolerance for SHA-256 chunk).
    dist = Counter()
    for i in range(3000):
        b = _hash_to_bucket(key, f"anon_{i}", len(variants))
        dist[variants[b]] += 1
    for v in variants:
        # Expect ~1000 each; allow 800-1200 (20% tolerance).
        assert 800 <= dist[v] <= 1200, (
            f"variant {v} got {dist[v]} (expected ~1000): {dist}"
        )


# --------------------------------------------------------------------------- #
# Test 2: New visitor + active experiment -> one of the declared variants
# and an ab_assignment row is written.
# --------------------------------------------------------------------------- #


def test_new_visitor_active_experiment_persists_assignment():
    """Active experiment + no prior assignment -> get_variant picks a
    declared variant AND writes one ab_assignment row via db.session.add
    + db.session.commit."""
    from ab_test import get_variant

    fake_exp = SimpleNamespace(
        key="s12_cta_label",
        variants=["control", "submit_now", "lodge_now"],
        status="active",
    )

    # ABExperiment.query.filter_by(...).first() -> fake_exp
    fake_exp_query = MagicMock()
    fake_exp_query.filter_by.return_value.first.return_value = fake_exp

    # ABAssignment.query.filter_by(...).first() -> None (no prior)
    fake_assignment_query = MagicMock()
    fake_assignment_query.filter_by.return_value.first.return_value = None

    fake_db = MagicMock()

    with patch("ab_test.ABExperiment", query=fake_exp_query), \
         patch("ab_test.ABAssignment") as MockAssignment, \
         patch("ab_test.db", fake_db), \
         patch("ab_test._current_user_id", return_value=None), \
         patch("ab_test._get_anon_id", return_value="anon_test_user_xyz"), \
         patch("ab_test.g", _FakeG()):

        # Re-route .query on the mocked ABAssignment class too.
        MockAssignment.query = fake_assignment_query

        variant = get_variant("s12_cta_label")

    # 1. Variant came from the declared list (NOT 'control' fallback).
    assert variant in fake_exp.variants
    # The deterministic hash for 'anon_test_user_xyz' must be stable.
    from ab_test import _hash_to_bucket
    expected_bucket = _hash_to_bucket(
        "s12_cta_label", "anon_test_user_xyz", 3,
    )
    assert variant == fake_exp.variants[expected_bucket]

    # 2. ABAssignment was instantiated with the expected kwargs.
    MockAssignment.assert_called_once()
    call_kwargs = MockAssignment.call_args.kwargs
    assert call_kwargs["experiment_key"] == "s12_cta_label"
    assert call_kwargs["user_id"] is None
    assert call_kwargs["session_anon_id"] == "anon_test_user_xyz"
    assert call_kwargs["variant"] == variant

    # 3. db.session.add + commit were called exactly once each.
    fake_db.session.add.assert_called_once()
    fake_db.session.commit.assert_called_once()


# --------------------------------------------------------------------------- #
# Test 3: No active experiment -> fallback 'control', no row written.
# --------------------------------------------------------------------------- #


def test_no_active_experiment_returns_control_no_write():
    """When ABExperiment.query.filter_by(key, status='active').first()
    returns None, get_variant MUST return 'control' and MUST NOT write
    any ab_assignment row."""
    from ab_test import get_variant

    fake_exp_query = MagicMock()
    fake_exp_query.filter_by.return_value.first.return_value = None  # no row

    fake_assignment_query = MagicMock()
    # If anyone tries to query, return None too (defensive).
    fake_assignment_query.filter_by.return_value.first.return_value = None

    fake_db = MagicMock()

    with patch("ab_test.ABExperiment", query=fake_exp_query), \
         patch("ab_test.ABAssignment") as MockAssignment, \
         patch("ab_test.db", fake_db), \
         patch("ab_test._current_user_id", return_value=None), \
         patch("ab_test._get_anon_id", return_value="anon_nothing_active"), \
         patch("ab_test.g", _FakeG()):

        MockAssignment.query = fake_assignment_query
        variant = get_variant("nonexistent_experiment")

    assert variant == "control"
    MockAssignment.assert_not_called()
    fake_db.session.add.assert_not_called()
    fake_db.session.commit.assert_not_called()
