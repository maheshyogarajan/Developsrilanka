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
"""
import logging
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
]


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


def emit(
    event_type: str,
    user_id: Optional[int] = None,
    payload: Optional[dict] = None,
    source: Optional[str] = None,
    organization_id: Optional[int] = None,
    session_anon_id: Optional[str] = None,
) -> Optional[int]:
    """Best-effort emit one Event row. Returns the new event id on success,
    None on any failure. NEVER raises.

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

    Returns:
        The new Event.id on success, None on failure.
    """
    try:
        # Local imports so this module can be imported by app.py without a
        # circular-import death spiral (app -> events -> event_models -> app).
        from app import db
        from event_models import Event

        ctx = _safe_request_context()

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
        return event.id
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


# Alias for sites where the intent ("this is a fire-and-forget side-effect")
# benefits from being explicit at the call site.
emit_safe = emit


__all__ = ["emit", "emit_safe", "STANDARD_EVENTS"]
