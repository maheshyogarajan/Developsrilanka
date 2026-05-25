"""fiesta.perf_cache — Tier D6 / D8: process-local TTL cache.

PURPOSE
-------
Cheap, in-memory per-key TTL cache with explicit invalidation. Used by the
hot-path agreement routes (`/agreements/service/<id>`, `/agreements/rental/<id>`)
to absorb 2-3 Neon cross-region roundtrips per request that the existing
`@paywall_required` + `inject_fiesta_hub_context` paths were paying every time.

DESIGN
------
- Per-key TTL via `(expiry_epoch, value)` tuples.
- `memoize_ttl(seconds, key_func)` decorator wraps any pure-ish function;
  call sites pass a function-of-args that returns the cache key so the
  caller controls scoping (per-user, per-tax-year, per-(user,sp) tuple, etc.).
- `invalidate(key)` / `invalidate_prefix(prefix)` for explicit busts after
  state-changing writes (Subscription insert, SP edit, Property edit, etc.).
- Thread-safe via `RLock` (Flask + gunicorn sync workers; FIESTA does not
  use gevent on the agreement paths).
- Per-gunicorn-worker — by design, NOT shared across workers. Acceptable
  for pre-revenue stack (no Redis dep introduced); future migration to a
  shared backend can swap the dict for a Flask-Caching adapter without
  touching call sites.

SCOPE CAPS
----------
- No size cap. The keys we cache are bounded: per-user-id (≤10k unique
  authed users), per-(user,sp)/per-(user,property), per-tax-year. Bounded
  cardinality; ~few hundred KB peak.
- No background sweep. We lazy-evict on read (expired entries are deleted
  on access). For abandoned keys that are never read again, memory holds
  until process restart — bounded by the key cardinality above.
- No metrics. Add via `perf_monitoring._record_sample()` if needed; for now
  the assumption is the cache hit-rate is observable from
  `X-Response-Time-Ms` going from 5-6s warm to <1s warm.

USAGE
-----
    from fiesta.perf_cache import memoize_ttl, invalidate

    @memoize_ttl(seconds=60, key_func=lambda user: f"sub:{user.id}")
    def _effective_tier_cached(user):
        return _expensive_sub_lookup(user)

    # On state change:
    invalidate(f"sub:{user_id}")
"""
from __future__ import annotations

import logging
import time
from functools import wraps
from threading import RLock
from typing import Any, Callable

logger = logging.getLogger(__name__)


# Module-level state — per-process. Tests can reset via `_reset_for_tests()`.
_CACHE: dict[str, tuple[float, Any]] = {}
_LOCK = RLock()


def get(key: str) -> tuple[bool, Any]:
    """Return (hit, value). Lazy-evicts expired entries on miss-or-stale.

    Returned as a 2-tuple rather than `Optional[Value]` so callers can
    distinguish "cached None" from "no entry".
    """
    now = time.time()
    with _LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return (False, None)
        expiry, value = entry
        if expiry < now:
            # Expired — evict and report miss.
            _CACHE.pop(key, None)
            return (False, None)
        return (True, value)


def set(key: str, value: Any, seconds: int) -> None:
    """Store value under key with TTL of `seconds`."""
    expiry = time.time() + max(1, int(seconds))
    with _LOCK:
        _CACHE[key] = (expiry, value)


def invalidate(key: str) -> None:
    """Drop a single key. No-op if absent."""
    with _LOCK:
        _CACHE.pop(key, None)


def invalidate_prefix(prefix: str) -> int:
    """Drop all keys starting with `prefix`. Returns count dropped.

    Use for "all caches related to this user" patterns:
        invalidate_prefix(f"sub:{user_id}")
        invalidate_prefix(f"sp:{user_id}:")
    """
    if not prefix:
        return 0
    with _LOCK:
        keys = [k for k in _CACHE.keys() if k.startswith(prefix)]
        for k in keys:
            _CACHE.pop(k, None)
    return len(keys)


def memoize_ttl(
    seconds: int,
    key_func: Callable[..., str],
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator factory: TTL-cache the wrapped function under `key_func(*args, **kwargs)`.

    `key_func` MUST return a string. If it raises, the call is forwarded
    uncached (defensive — instrumentation never breaks the request).
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            try:
                key = key_func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — never break callers
                logger.debug("perf_cache key_func raised: %s", exc)
                return fn(*args, **kwargs)
            hit, value = get(key)
            if hit:
                return value
            value = fn(*args, **kwargs)
            try:
                set(key, value, seconds)
            except Exception as exc:  # noqa: BLE001
                logger.debug("perf_cache set raised: %s", exc)
            return value
        return wrapped
    return decorator


def stats() -> dict:
    """Return a small dict snapshot for /admin/perf and tests."""
    with _LOCK:
        return {
            "size": len(_CACHE),
            "keys_sample": list(_CACHE.keys())[:20],
        }


def _reset_for_tests() -> None:
    """Test-only: wipe the cache."""
    with _LOCK:
        _CACHE.clear()


__all__ = [
    "get",
    "set",
    "invalidate",
    "invalidate_prefix",
    "memoize_ttl",
    "stats",
]
