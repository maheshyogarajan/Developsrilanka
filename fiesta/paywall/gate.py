"""
fiesta.paywall.gate — ``@paywall_required`` decorator + tier-checking helpers.

Apply ``@paywall_required(min_tier='self_file', screen_id='S6')`` to any
Flask view that should be gated behind the Self-File paywall. The decorator:

  1. Loads the current user (Flask-Login).
  2. Resolves the user's effective tier from the most recent active
     ``Subscription`` row (free_trial by default).
  3. If tier rank >= required: pass through.
  4. Else: emit a ``PaywallEvent`` row + log a ``paywall_fired`` analytics
     event + redirect (HTML) or return 402 JSON (AJAX) to /pricing.

Screen catalog (cross-reference council brief, ``THE_PATH_20260520`` doc):

  Free tier:      S0 (estimator), S1 (signup), S2 (signup variant),
                  S3 (profile), S4 (earnings), S5 (deductions education)

  Self-File:      S6 (service providers), S7 (deduction commitments),
                  S8 (consents), S9 (compute), S10 (review),
                  S11 (tax bill), S12 (submit), S14 (post-submit dashboard)

  Auto-File:      v1.1 deferred

The catalogues are exposed as module constants so callers (tests, admin
funnel, the post-purchase return-to validator) can iterate them.
"""
from __future__ import annotations

import logging
from functools import wraps
from typing import Optional

from flask import (
    request, redirect, url_for, jsonify, abort, current_app,
)
from flask_login import current_user

from events import emit as emit_analytics_event

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Screen catalogues — the source of truth for free vs paid surface.
# --------------------------------------------------------------------------- #
FREE_TIER_SCREENS = frozenset({"S0", "S1", "S2", "S3", "S4", "S5"})
SELF_FILE_SCREENS = frozenset(
    {"S6", "S7", "S8", "S9", "S10", "S11", "S12", "S14"}
)
AUTO_FILE_SCREENS: frozenset = frozenset()  # v1.1


# --------------------------------------------------------------------------- #
# Effective-tier resolution.
# --------------------------------------------------------------------------- #

def active_subscription(user, tier: Optional[str] = None):
    """Return the most-recent active Subscription for ``user``, or None.

    If ``tier`` is given, restricts to that tier. Otherwise returns the
    highest-rank tier currently active.

    Never raises. Returns ``None`` if user is anonymous or has no row.
    """
    if user is None or not getattr(user, "id", None):
        return None

    try:
        from .models import Subscription, TIER_ORDER
        from datetime import datetime
        if Subscription is None:
            return None

        q = (
            Subscription.query
            .filter(Subscription.user_id == user.id)
            .filter(Subscription.status == "active")
            .filter(Subscription.expires_at > datetime.utcnow())
        )
        if tier:
            q = q.filter(Subscription.tier == tier)

        rows = q.all()
        if not rows:
            return None

        # Pick the highest-tier row; tie-break by latest expires_at.
        def _rank(row):
            return (TIER_ORDER.get(row.tier, 0), row.expires_at)
        rows.sort(key=_rank, reverse=True)
        return rows[0]
    except Exception as exc:
        log.debug("active_subscription failed for user=%s: %s",
                  getattr(user, "id", None), exc)
        return None


def effective_tier(user) -> str:
    """Resolve the user's effective tier name.

    Returns the highest-rank active tier, or ``'free_trial'`` for anon users
    and users with no active row. Never raises.

    Tier D6 / D8 (2026-05-25): cached via fiesta.perf_cache (60s TTL, per-user).
    The /agreements/* warm-path latency was eating 2-3 cross-region Neon
    roundtrips per request from this lookup. Invalidated explicitly by
    `invalidate_subscription_cache(user_id)` on Subscription writes (Stripe
    webhook, manual grants).
    """
    from .models import TIER_FREE_TRIAL

    user_id = getattr(user, "id", None) if user else None
    if not user_id:
        return TIER_FREE_TRIAL

    # Per-user TTL cache. Cold path runs the original lookup; warm path
    # skips the DB entirely.
    try:
        from fiesta.perf_cache import get as _cache_get, set as _cache_set
        cache_key = f"paywall:effective_tier:{int(user_id)}"
        hit, value = _cache_get(cache_key)
        if hit:
            return value
        sub = active_subscription(user)
        tier = TIER_FREE_TRIAL if sub is None else sub.tier
        _cache_set(cache_key, tier, seconds=60)
        return tier
    except Exception as exc:  # noqa: BLE001
        log.debug("effective_tier cache path failed: %s — direct lookup", exc)
        sub = active_subscription(user)
        return TIER_FREE_TRIAL if sub is None else sub.tier


def is_tier_active(user, required_tier: str) -> bool:
    """True iff ``user`` has an active subscription whose rank >= required_tier.

    ``free_trial`` has rank 0 and is always satisfied.
    """
    from .models import TIER_ORDER
    required_rank = TIER_ORDER.get(required_tier, 0)
    if required_rank == 0:
        return True
    user_rank = TIER_ORDER.get(effective_tier(user), 0)
    return user_rank >= required_rank


def invalidate_subscription_cache(user_id) -> None:
    """Drop the per-user paywall tier cache. Call after any Subscription
    INSERT/UPDATE/DELETE for this user (Stripe webhook, manual grant, expiry
    sweep). No-op if user_id is falsy or the cache module is unavailable.

    Tier D6 / D8 (2026-05-25): keeps the cache from masking a freshly-purchased
    tier upgrade. The natural 60s TTL is the worst-case stale window when this
    is not called; calling it makes the upgrade visible on the very next render.
    """
    if not user_id:
        return
    try:
        from fiesta.perf_cache import invalidate as _invalidate
        _invalidate(f"paywall:effective_tier:{int(user_id)}")
    except Exception as exc:  # noqa: BLE001
        log.debug("invalidate_subscription_cache failed for user=%s: %s",
                  user_id, exc)


# --------------------------------------------------------------------------- #
# AJAX detection — a request is AJAX if it asks for JSON or sets the standard
# XHR header. We are conservative: form-posted GETs always count as browser.
# --------------------------------------------------------------------------- #

def _is_ajax_request() -> bool:
    """True for XHR / JSON / fetch() requests. Browser nav -> False."""
    try:
        if request.headers.get("X-Requested-With", "").lower() == "xmlhttprequest":
            return True
        accept = request.headers.get("Accept", "")
        if accept and "application/json" in accept and "text/html" not in accept:
            return True
        if request.path.startswith("/api/"):
            return True
    except Exception:
        return False
    return False


# --------------------------------------------------------------------------- #
# PaywallEvent recording.
# --------------------------------------------------------------------------- #

def _record_paywall_event(user, screen_id: str, action_attempted: str,
                          required_tier: str, was_ajax: bool) -> Optional[int]:
    """Insert one PaywallEvent row + emit an analytics Event row.

    Returns the PaywallEvent id on success, None on any DB failure (we never
    block the user-visible redirect because of an analytics write failure).
    """
    paywall_event_id = None
    try:
        from app import db
        from .models import PaywallEvent
        if PaywallEvent is None:
            return None
        try:
            request_path = request.path[:512]
            user_agent = (request.headers.get("User-Agent") or "")[:512]
        except Exception:
            request_path = None
            user_agent = None

        row = PaywallEvent(
            user_id=getattr(user, "id", None) if user else None,
            screen_id=screen_id,
            action_attempted=action_attempted[:255] if action_attempted else None,
            required_tier=required_tier,
            request_path=request_path,
            user_agent=user_agent,
            was_ajax=was_ajax,
        )
        db.session.add(row)
        db.session.commit()
        paywall_event_id = row.id
    except Exception as exc:
        log.warning("paywall_event insert failed: %s", exc)
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass

    # Always best-effort emit to the analytics spine — even if the dedicated
    # PaywallEvent row failed, the analytics row keeps the funnel honest.
    emit_analytics_event(
        "paywall_fired",
        user_id=getattr(user, "id", None) if user else None,
        payload={
            "screen_id": screen_id,
            "required_tier": required_tier,
            "action_attempted": action_attempted,
            "was_ajax": was_ajax,
            "paywall_event_id": paywall_event_id,
        },
        source=f"paywall.gate:{screen_id}",
    )
    return paywall_event_id


# --------------------------------------------------------------------------- #
# Decorator.
# --------------------------------------------------------------------------- #

def paywall_required(min_tier: str = "self_file",
                     screen_id: str = "?",
                     action: Optional[str] = None):
    """Flask view decorator.

    Args:
        min_tier: minimum tier rank required. ``'self_file'`` or ``'auto_file'``.
        screen_id: which screen this view belongs to (e.g. ``'S6'``).
                   Surfaces in PaywallEvent for the funnel dashboard.
        action: optional action label (e.g. ``'generate_service_agreement'``).

    Behavior:
      * Anonymous user -> redirect to login with ?next=<current_path>.
        (We never count anonymous hits as paywall fires — that would inflate
        the funnel with bot traffic.)
      * Authenticated + tier OK -> call the wrapped view.
      * Authenticated + tier insufficient -> record PaywallEvent, then:
          - AJAX/JSON: 402 with {"paywall_url": "...", "screen_id": "...",
                                  "required_tier": "...",
                                  "message": "..."}
          - Browser: 302 redirect to /pricing?return_to=<path>&screen_id=<sid>
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            # Anonymous user -> punt to login. Don't count as paywall fire.
            if not getattr(current_user, "is_authenticated", False):
                login_endpoint = current_app.config.get(
                    "PAYWALL_LOGIN_ENDPOINT", None
                )
                if login_endpoint:
                    try:
                        return redirect(url_for(
                            login_endpoint, next=request.path
                        ))
                    except Exception:
                        pass
                # Fall through to the standard 401 if we have no login route.
                return abort(401)

            if is_tier_active(current_user, min_tier):
                return view_func(*args, **kwargs)

            # Paywall fires — record + route.
            was_ajax = _is_ajax_request()
            _record_paywall_event(
                user=current_user,
                screen_id=screen_id,
                action_attempted=action or view_func.__name__,
                required_tier=min_tier,
                was_ajax=was_ajax,
            )

            try:
                pricing_url = url_for(
                    "paywall.pricing_screen",
                    return_to=request.path,
                    screen_id=screen_id,
                )
            except Exception:
                # Fallback if blueprint isn't registered (shouldn't happen
                # in normal operation but keeps tests robust).
                pricing_url = (
                    f"/pricing/x1?return_to={request.path}&screen_id={screen_id}"
                )

            if was_ajax:
                return jsonify({
                    "error": "payment_required",
                    "paywall_url": pricing_url,
                    "screen_id": screen_id,
                    "required_tier": min_tier,
                    "message": (
                        "Unlock Service Agreement generation — "
                        "Rs 2,500 (refundable 14 days)."
                    ),
                }), 402

            return redirect(pricing_url, code=302)

        return wrapped
    return decorator


__all__ = [
    "paywall_required",
    "active_subscription",
    "effective_tier",
    "is_tier_active",
    "invalidate_subscription_cache",
    "FREE_TIER_SCREENS",
    "SELF_FILE_SCREENS",
    "AUTO_FILE_SCREENS",
]
