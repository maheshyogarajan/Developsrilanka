"""
Sentry verification routes — Tier D1 / E1.

Single endpoint: ``GET /sentry-test`` (admin-only). Deliberately raises an
exception so Sentry can prove ingestion end-to-end. Use this once after
setting the ``SENTRY_DSN`` Fly secret to confirm the inbox receives events.

Wiring: import + register from ``app.py``:

    from sentry_routes import sentry_bp
    app.register_blueprint(sentry_bp)
"""
from __future__ import annotations

from flask import Blueprint

from fiesta.auth.decorators import admin_required

sentry_bp = Blueprint("sentry_test", __name__)


class SentryVerificationError(RuntimeError):
    """Deliberate exception class raised by /sentry-test.

    Distinct subclass so a Sentry alert filter can mute these events if the
    CEO wants to suppress the noise from periodic verification pings.
    """


@sentry_bp.route("/sentry-test", methods=["GET"])
@admin_required
def sentry_test():
    """Raise a deliberate exception so Sentry ingestion can be verified.

    Returns 500 to the caller (the unhandled exception propagates through
    Flask's error handler, which renders the standard 500 page). The matching
    event lands in Sentry within ~5 seconds.
    """
    raise SentryVerificationError(
        "Deliberate /sentry-test exception — Sentry ingestion check (E1)."
    )


__all__ = ["sentry_bp", "SentryVerificationError"]
