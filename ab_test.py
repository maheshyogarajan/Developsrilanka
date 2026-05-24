"""
A/B testing harness — assignment helper.

Tier D5 / E6 (2026-05-24). Companion to ab_test_models.py.

Public API:
  * get_variant(experiment_key) -> str
      Returns the variant label this visitor is assigned to. Falls back
      to 'control' when the experiment is not active. Result is cached
      on `flask.g` for the lifetime of the request — repeat calls within
      the same render cycle are free.
  * register_template_helper(app) -> None
      Wires `{{ ab_variant('experiment_key') }}` into Jinja so templates
      can branch on variant without touching Python.

Assignment is deterministic: SHA-256(experiment_key + ":" + anon_id) %
len(variants). Same visitor + same experiment -> same variant on every
visit, even before the ab_assignment row is persisted. The persisted
row exists for analytics + audit, not for stickiness — stickiness comes
from the hash itself.

Visitor identity:
  * Authenticated user  -> "u{user.id}"
  * Anonymous visitor   -> session_anon_id cookie value
  * No cookie present   -> literal string "anon" (degraded mode, very
    early requests before analytics_beacon_routes has set the cookie;
    all such visitors collide into the same bucket, which is acceptable
    for the trace volume that lands here pre-cookie).

Best-effort write semantics: a failed INSERT (race on the UNIQUE
constraint, transient DB issue) does NOT raise — the deterministic hash
guarantees the same visitor gets the same variant on the next request,
so the row will be written then. Never block render on assignment
persistence.
"""
from __future__ import annotations

import hashlib
import logging

from flask import g, request

from ab_test_models import ABAssignment, ABExperiment
from app import db

log = logging.getLogger(__name__)


def _hash_to_bucket(experiment_key: str, anon_id: str, num_buckets: int) -> int:
    """Deterministic, uniform bucket assignment.

    SHA-256 is overkill for two-way splits but the cost is trivial (~1µs)
    and the wide hash gives us headroom for 256-variant experiments
    without re-considering bucketing math.
    """
    if num_buckets <= 0:
        return 0
    h = hashlib.sha256(f"{experiment_key}:{anon_id}".encode("utf-8")).hexdigest()
    return int(h[:8], 16) % num_buckets


def _get_anon_id() -> str:
    """Resolve the visitor identity string used for hashing.

    Preference order:
      1. Authenticated user -> "u{user.id}" (sticky across devices once
         the user logs in on a new browser).
      2. session_anon_id cookie -> the analytics beacon already sets
         this on first request, so most anonymous visitors have one.
      3. Literal "anon" -> only reachable before the cookie has been
         set (e.g. first byte of first request from a brand-new browser).
    """
    try:
        from flask_login import current_user
        if current_user.is_authenticated:
            return f"u{current_user.id}"
    except Exception:
        # flask_login not initialised (e.g. CLI invocation) or no
        # request context — fall through to cookie path.
        pass

    try:
        cookie_val = request.cookies.get("session_anon_id")
        if cookie_val:
            return cookie_val
    except RuntimeError:
        # Outside request context — caller should not really be hitting
        # this code path, but degrade gracefully.
        pass

    return "anon"


def _current_user_id():
    """Return current_user.id if authenticated, else None.

    Encapsulated so tests that don't have a flask_login context don't
    have to monkey-patch the import directly.
    """
    try:
        from flask_login import current_user
        if current_user.is_authenticated:
            return current_user.id
    except Exception:
        pass
    return None


def get_variant(experiment_key: str) -> str:
    """Return the variant label for the current visitor + experiment.

    Cached on flask.g so {{ ab_variant('foo') }} called 5 times in one
    template hits the DB at most once per request. Fall-back is always
    'control' — never raise from a render path.
    """
    cache_attr = f"_ab_{experiment_key}"
    if hasattr(g, cache_attr):
        return getattr(g, cache_attr)

    variant = "control"
    try:
        exp = ABExperiment.query.filter_by(
            key=experiment_key, status="active",
        ).first()

        if not exp or not exp.variants:
            setattr(g, cache_attr, variant)
            return variant

        user_id = _current_user_id()
        anon_id = _get_anon_id()

        # Check for existing sticky assignment first. Authenticated path
        # uses user_id; anonymous path uses session_anon_id.
        if user_id is not None:
            existing = ABAssignment.query.filter_by(
                experiment_key=experiment_key, user_id=user_id,
            ).first()
        else:
            existing = ABAssignment.query.filter_by(
                experiment_key=experiment_key,
                user_id=None,
                session_anon_id=anon_id,
            ).first()

        if existing:
            variant = existing.variant
        else:
            # Fresh assignment. Hash is deterministic so even if the
            # persist below fails, the next request hashes the same way.
            bucket = _hash_to_bucket(
                experiment_key, anon_id, len(exp.variants),
            )
            variant = exp.variants[bucket]

            try:
                row = ABAssignment(
                    experiment_key=experiment_key,
                    user_id=user_id,
                    session_anon_id=anon_id if user_id is None else None,
                    variant=variant,
                )
                db.session.add(row)
                db.session.commit()
            except Exception as exc:
                # Race on the UNIQUE constraint or transient DB error.
                # Both are recoverable on the next read because the hash
                # is deterministic. Best-effort; never block render.
                db.session.rollback()
                log.debug(
                    "ABAssignment persist deferred (likely race): "
                    "key=%s err=%s",
                    experiment_key, exc,
                )
    except Exception as exc:
        # Catch-all so a broken experiment row, missing table, or DB
        # connectivity issue never causes a template render to fail.
        log.warning(
            "ab_test.get_variant fell back to control for %s: %s",
            experiment_key, exc,
        )
        variant = "control"

    setattr(g, cache_attr, variant)
    return variant


def register_template_helper(app) -> None:
    """Expose {{ ab_variant('experiment_key') }} to Jinja templates.

    Idempotent: safe to call multiple times (later registrations
    override earlier ones with the same callable). Call once at app
    init time from main.py.
    """
    @app.context_processor
    def _inject_ab_variant():
        return {"ab_variant": get_variant}
