"""
Event emission helper — best-effort, never raises.

Call sites use:

    from events import emit
    emit('signup', user_id=new_user.id, payload={'persona': 'sl_foreign_income'},
         source='route:email_login')

Design constraints (Council #2, 2026-05-17):

  1. NEVER raise. Analytics failures must not break user-facing flows. We
     swallow any exception, log a warning, roll back the session, and return
     None. Callers do not need try/except.

  2. Best-effort request-context capture. If called within a Flask request
     scope we lift session_id / ip_address / user_agent automatically. If
     called outside (Celery task, CLI script, test), we omit them.

  3. Stateless. emit() opens no long-lived resources; it relies on the
     Flask-SQLAlchemy session already bound to the app context.

  4. Standard event types are enumerated in STANDARD_EVENTS. Ad-hoc strings
     are permitted (the DB column is free-form VARCHAR) but the canonical
     list should be promoted as patterns stabilise. The list is also the
     contract every Wave 2 consumer (dashboard, AI CRM, scheduler) reads.

Tier D1 / B-0040 perf (2026-05-24):

  5. `defer=True` runs the DB write on a small process-wide
     ThreadPoolExecutor. The HTTP request returns before the row hits
     Postgres. Anonymous-path call sites (notably `/` landing) use this so
     the user doesn't eat 3-4 cross-region DB round-trips on a page render.
     Tests can force synchronous emission by setting EVENTS_SYNC_FOR_TEST=1
     so assertions don't race the background thread.
"""
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Standard event types — the contract for Wave 2 consumers.
# --------------------------------------------------------------------------- #
#
# Each entry is referenced by at least one downstream consumer:
#
#   signup                        -> growth dashboard, AI CRM cold-start
#   email_verified                -> activation funnel, persona prompt timing
#   persona_set                   -> AI CRM segmentation
#   bank_statement_uploaded       -> Gemini-cost monitor, import quality KPI
#   remittance_added              -> tax-year remittance velocity dashboard
#   remittance_ird_ready          -> Lanka.tax staff queue ("review me")
#   checkout_started              -> Stripe funnel (top of pay funnel)
#   checkout_completed            -> Stripe funnel (paid conversion)
#   payment_failed                -> dunning queue trigger
#   support_message_received      -> CRM ticket creation
#   nudge_sent                    -> nudge fatigue / cooldown tracker
#   idea_submitted                -> product-backlog auto-import
#
STANDARD_EVENTS = [
    "signup",
    "email_verified",
    "persona_set",
    "bank_statement_uploaded",
    "remittance_added",
    "remittance_ird_ready",
    "checkout_started",
    "checkout_completed",
    "payment_failed",
    "support_message_received",
    "nudge_sent",
    "idea_submitted",
    # Markov-L2 2026-05-27 — funnel-progression signals that map onto
    # S-states. The Markov state writer (fiesta.markov.state_writer)
    # consumes these to populate user_state_history. They are NOT
    # high-volume (one per user per lifecycle step), so the existing
    # ThreadPoolExecutor + emit() defer path handles them without
    # contention.
    #
    #   profile_complete     -> S02 (FiestaProfile NIC+city+bank populated)
    #   al_completed         -> S09 (first AssetEntry or LiabilityEntry saved)
    #   tax_bill_computed    -> S10 (engine returned non-zero bill first time)
    #   tax_bill_finalized   -> S12 (user clicked "Lock this bill")
    "profile_complete",
    "al_completed",
    "tax_bill_computed",
    "tax_bill_finalized",
]


# --------------------------------------------------------------------------- #
# Deferred-emit infrastructure (Tier D1 / B-0040, 2026-05-24)
# --------------------------------------------------------------------------- #
#
# A single shared ThreadPoolExecutor handles `defer=True` emissions. 2 workers
# is enough headroom for the current event volume (single-digit emits/second
# at peak) while keeping process memory bounded. Workers are daemon threads
# (Python's default for ThreadPoolExecutor) so they don't block interpreter
# exit on shutdown.
#
# We do NOT use Celery here: the existing Celery worker is reserved for
# heavy/retry-able tasks (OCR, Gemini, S3 uploads). Analytics emit failures
# are tolerable losses — Council #2 design point 1.
#
# The executor is lazily constructed on first deferred call so that test
# environments that never trigger defer don't spin up a thread pool.
#
_EXECUTOR: Optional[ThreadPoolExecutor] = None
_EXECUTOR_MAX_WORKERS = 2


def _get_executor() -> ThreadPoolExecutor:
    """Return the process-wide deferred-emit executor, constructing it on
    first use. Not thread-safe to call concurrently from multiple threads
    on the very first call, but Python's GIL + ThreadPoolExecutor are
    cheap enough that a race here at most creates one extra orphan
    executor — harmless."""
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = ThreadPoolExecutor(
            max_workers=_EXECUTOR_MAX_WORKERS,
            thread_name_prefix="events-emit",
        )
    return _EXECUTOR


def _events_sync_override() -> bool:
    """When EVENTS_SYNC_FOR_TEST=1, force defer=True calls to emit
    synchronously. Tests need this so assertions on Event.query don't
    race the background thread."""
    return os.environ.get("EVENTS_SYNC_FOR_TEST", "0") == "1"


def _safe_request_context() -> dict:
    """Lift session_id / ip_address / user_agent from the Flask request if
    we're inside a request scope. Returns an empty dict otherwise — emit()
    works fine without it (Celery tasks, CLI scripts, tests).

    Wrapped in try/except because flask.request raises RuntimeError
    ("Working outside of request context") when called outside a request.
    """
    ctx = {}
    try:
        # Local import so module load doesn't blow up if Flask is absent
        # (which it never is in this app, but defence-in-depth).
        from flask import request, session
        # request.remote_addr can be None in some test/proxy configs.
        ctx["ip_address"] = request.headers.get("X-Forwarded-For", request.remote_addr)
        if ctx["ip_address"] and "," in ctx["ip_address"]:
            # Standard X-Forwarded-For: client, proxy1, proxy2 — take the first.
            ctx["ip_address"] = ctx["ip_address"].split(",")[0].strip()
        ua = request.headers.get("User-Agent")
        if ua:
            # Cap at a sane length — UA strings can be pathologically long.
            ctx["user_agent"] = ua[:2000]
        # Flask's signed-cookie session doesn't expose a stable id; we use
        # the underlying session token if Flask-Session is installed,
        # otherwise we synthesise a stable id from the session dict's
        # internal new flag fallback. For now, lift whatever is there.
        sid = session.get("_id") or session.get("session_id")
        if sid:
            ctx["session_id"] = str(sid)[:64]
    except Exception:
        # Outside a request context — fine, just return what we have.
        pass
    return ctx


def _do_emit(
    event_type: str,
    user_id: Optional[int],
    payload: Optional[dict],
    source: Optional[str],
    organization_id: Optional[int],
    session_anon_id: Optional[str],
    ctx: dict,
    app_obj: Any = None,
) -> Optional[int]:
    """The actual DB write. Used by both sync and deferred paths. If
    app_obj is supplied (deferred path), we push an app context so
    Flask-SQLAlchemy can find the session."""
    try:
        from app import db
        from event_models import Event

        # Dual-write reconciliation: prefer the explicit kwarg, fall back to
        # whatever the caller embedded in payload. This keeps every existing
        # call site (which passes session_anon_id INSIDE payload) writing to
        # the indexed top-level column without any code change at the caller.
        effective_anon = session_anon_id
        if not effective_anon and isinstance(payload, dict):
            v = payload.get("session_anon_id")
            if isinstance(v, str) and v:
                effective_anon = v
        if effective_anon:
            effective_anon = effective_anon[:64]  # column cap

        def _write() -> Optional[int]:
            event = Event(
                event_type=event_type[:64],  # column cap
                user_id=user_id,
                organization_id=organization_id,
                payload=payload,
                source=(source[:32] if source else None),
                session_id=ctx.get("session_id"),
                ip_address=ctx.get("ip_address"),
                user_agent=ctx.get("user_agent"),
                session_anon_id=effective_anon,
            )
            db.session.add(event)
            db.session.commit()
            event_id = event.id

            # ---- Markov Layer 2 hook ------------------------------------
            # After the Event row is persisted, opportunistically write a
            # UserStateHistory row when this event represents a Markov
            # state transition. Same NEVER-raises contract as the rest of
            # emit() — any failure inside the writer logs + returns None
            # without affecting the Event insert above.
            #
            # CRITICAL: we run this INSIDE _write() (which is itself
            # already executed inside the deferred app context when
            # called via defer=True) — that way the writer's session
            # commit doesn't fight the deferred-emit thread's session
            # boundary. The writer has its own try/except so we don't
            # need to wrap it here.
            try:
                if event_type and user_id is not None:
                    from fiesta.markov.state_writer import (
                        event_to_state,
                        record_state_transition,
                    )
                    new_state = event_to_state(event_type, payload, user_id)
                    if new_state is not None:
                        record_state_transition(
                            user_id=user_id,
                            new_state=new_state,
                            trigger=event_type,
                            metadata=payload if isinstance(payload, dict) else None,
                        )
            except Exception as _markov_exc:
                # Defence-in-depth — writer already swallows, but a
                # module-load failure (rare) would surface here.
                logger.warning(
                    "events.emit(%r): markov state-writer hook failed: %s. "
                    "Event row persisted; state transition lost.",
                    event_type, _markov_exc,
                )

            return event_id

        if app_obj is not None:
            # Background thread: need an app context for the SQLAlchemy
            # session to bind. Also use a fresh scoped session to avoid
            # cross-thread session reuse.
            with app_obj.app_context():
                try:
                    return _write()
                finally:
                    try:
                        db.session.remove()
                    except Exception:
                        pass
        else:
            return _write()
    except Exception as exc:
        # Best-effort: log, roll back, return None. Never propagate —
        # analytics is observational, not transactional.
        logger.warning(
            "events.emit(%r) failed: %s. Caller continues.",
            event_type, exc,
        )
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        return None


def emit(
    event_type: str,
    user_id: Optional[int] = None,
    payload: Optional[dict] = None,
    source: Optional[str] = None,
    organization_id: Optional[int] = None,
    session_anon_id: Optional[str] = None,
    defer: bool = False,
) -> Optional[int]:
    """Best-effort emit one Event row. Returns the new event id on success,
    None on any failure or when deferred. NEVER raises.

    Args:
        event_type: short slug, ideally from STANDARD_EVENTS.
        user_id: FK user.id. Nullable — unauth events (ad clicks) are valid.
        payload: JSON-serialisable dict. Keep small (<2 KB practical ceiling).
        source: where the event was emitted from (route:..., webhook:...,
                cron:..., ai:...). See event_models.Event.source docstring.
        organization_id: FK organization.id. Nullable.
        session_anon_id: anonymous-session identifier (from session_anon_id
                cookie). Promoted from payload JSON to top-level indexed
                column on Tier C2 (2026-05-24). If caller also passes the
                value inside `payload`, we honour the explicit kwarg first.
                For backward compatibility, when the kwarg is None but
                payload['session_anon_id'] is set, we lift it into the
                top-level column (transitional dual-write — keeps any
                pre-Tier-C2 caller's row queryable on the new index).
        defer: when True, schedule the DB write on a background thread and
                return immediately. Returns None (no event id available).
                When EVENTS_SYNC_FOR_TEST=1 the defer flag is ignored and
                emit() behaves synchronously, so tests can assert on the
                row immediately. Tier D1 / B-0040 — landing-path latency.

    Returns:
        The new Event.id on success (sync path), None on failure or when
        deferred.
    """
    ctx = _safe_request_context()

    # Sync path — either the caller didn't request defer, or the test
    # override is active.
    if not defer or _events_sync_override():
        return _do_emit(
            event_type=event_type,
            user_id=user_id,
            payload=payload,
            source=source,
            organization_id=organization_id,
            session_anon_id=session_anon_id,
            ctx=ctx,
            app_obj=None,
        )

    # Deferred path — grab the current Flask app object now (we're still
    # in the request thread) so the background worker can push its own
    # app context. Catch the import lazily; if anything goes sideways
    # we silently fall back to a sync emit to preserve the row.
    try:
        from flask import current_app
        app_obj = current_app._get_current_object()
    except Exception:
        return _do_emit(
            event_type=event_type,
            user_id=user_id,
            payload=payload,
            source=source,
            organization_id=organization_id,
            session_anon_id=session_anon_id,
            ctx=ctx,
            app_obj=None,
        )

    try:
        _get_executor().submit(
            _do_emit,
            event_type,
            user_id,
            payload,
            source,
            organization_id,
            session_anon_id,
            ctx,
            app_obj,
        )
    except Exception as exc:
        # Pool saturated / shutdown / etc. — fall back to sync so we don't
        # silently drop the event.
        logger.warning(
            "events.emit(%r) defer submit failed: %s. Falling back to sync.",
            event_type, exc,
        )
        return _do_emit(
            event_type=event_type,
            user_id=user_id,
            payload=payload,
            source=source,
            organization_id=organization_id,
            session_anon_id=session_anon_id,
            ctx=ctx,
            app_obj=None,
        )
    return None


# Alias for sites where the intent ("this is a fire-and-forget side-effect")
# benefits from being explicit at the call site.
emit_safe = emit


__all__ = ["emit", "emit_safe", "STANDARD_EVENTS"]
