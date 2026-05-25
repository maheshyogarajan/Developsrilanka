"""tests.perf.test_perf_cache — Tier D6 / D8 unit tests for fiesta.perf_cache.

Standalone — does not import the full FIESTA app. Verifies:
  1. TTL expiry: an entry stored with seconds=N is evicted on read after N+1s.
  2. Explicit invalidation drops the key.
  3. Prefix invalidation drops only matching keys.
  4. The memoize_ttl decorator caches and surfaces hits/misses correctly.
  5. Cache survives None values (distinguishes "cached None" from "no entry").
"""
from __future__ import annotations

import time

import pytest


@pytest.fixture(autouse=True)
def _reset_cache():
    from fiesta import perf_cache
    perf_cache._reset_for_tests()
    yield
    perf_cache._reset_for_tests()


def test_set_then_get_returns_hit():
    from fiesta import perf_cache
    perf_cache.set("k1", "v1", seconds=60)
    hit, value = perf_cache.get("k1")
    assert hit is True
    assert value == "v1"


def test_get_unknown_key_misses():
    from fiesta import perf_cache
    hit, value = perf_cache.get("never_set")
    assert hit is False
    assert value is None


def test_ttl_eviction_on_read():
    from fiesta import perf_cache
    perf_cache.set("expiring", "v", seconds=1)
    # Force-age the entry so we don't actually sleep.
    perf_cache._CACHE["expiring"] = (time.time() - 5, "v")
    hit, value = perf_cache.get("expiring")
    assert hit is False
    assert value is None
    # And the entry is evicted.
    assert "expiring" not in perf_cache._CACHE


def test_invalidate_single_key():
    from fiesta import perf_cache
    perf_cache.set("k", "v", seconds=60)
    perf_cache.invalidate("k")
    hit, _ = perf_cache.get("k")
    assert hit is False


def test_invalidate_prefix_drops_matching_only():
    from fiesta import perf_cache
    perf_cache.set("sub:1", "a", seconds=60)
    perf_cache.set("sub:2", "b", seconds=60)
    perf_cache.set("other:1", "c", seconds=60)
    dropped = perf_cache.invalidate_prefix("sub:")
    assert dropped == 2
    assert perf_cache.get("sub:1") == (False, None)
    assert perf_cache.get("sub:2") == (False, None)
    assert perf_cache.get("other:1") == (True, "c")


def test_invalidate_prefix_empty_string_drops_nothing():
    from fiesta import perf_cache
    perf_cache.set("k", "v", seconds=60)
    assert perf_cache.invalidate_prefix("") == 0
    assert perf_cache.get("k") == (True, "v")


def test_memoize_ttl_caches_call():
    from fiesta import perf_cache

    calls = {"n": 0}

    @perf_cache.memoize_ttl(seconds=60, key_func=lambda x: f"square:{x}")
    def square(x: int) -> int:
        calls["n"] += 1
        return x * x

    assert square(7) == 49
    assert square(7) == 49
    assert square(7) == 49
    assert calls["n"] == 1, "Wrapped fn should run once and cache the rest"


def test_memoize_ttl_distinct_keys_run_independently():
    from fiesta import perf_cache

    calls = {"n": 0}

    @perf_cache.memoize_ttl(seconds=60, key_func=lambda x: f"id:{x}")
    def f(x):
        calls["n"] += 1
        return x

    f(1)
    f(2)
    f(1)
    assert calls["n"] == 2


def test_memoize_ttl_keyfunc_raise_falls_back_to_direct_call():
    from fiesta import perf_cache

    calls = {"n": 0}

    def bad_key(*_a, **_kw):
        raise RuntimeError("boom")

    @perf_cache.memoize_ttl(seconds=60, key_func=bad_key)
    def f(x):
        calls["n"] += 1
        return x

    # Should not raise — falls through to direct call, no cache.
    assert f(5) == 5
    assert f(5) == 5
    assert calls["n"] == 2


def test_cached_none_distinguishable_from_no_entry():
    from fiesta import perf_cache
    perf_cache.set("nullable", None, seconds=60)
    hit, value = perf_cache.get("nullable")
    assert hit is True
    assert value is None


def test_stats_reports_size_and_sample():
    from fiesta import perf_cache
    perf_cache.set("a", 1, seconds=60)
    perf_cache.set("b", 2, seconds=60)
    s = perf_cache.stats()
    assert s["size"] == 2
    assert set(s["keys_sample"]) == {"a", "b"}
