"""
fiesta.consultant — X4 Consultant booking flow (Wave 6, 2026-05-21).

Public surface:

  >>> from fiesta.consultant import register_routes, register_models
  >>> register_models()        # idempotent — defines Booking table
  >>> register_routes(app)     # mounts /consultant/book and friends

X4 spec
-------
* Available to ALL signed-up tiers (Trial / Self-File / Auto-File).
* Rs 5,000 / 30 min one-off Stripe payment.
* On Stripe success → redirect to ``calendar.app.google/upp97vgtE7oYVdzn9``
  (Google issues the Meet link automatically).
* Best-effort SendGrid prep-brief email to the consultant within minutes —
  the handler kicks it off synchronously; failures are logged + the row's
  ``prep_brief_sent_at`` stays NULL so a background sweeper can retry.

Storage: one ``consultant_booking`` table (defined in :mod:`models`).
Distinct from ``paywall_subscription`` — bookings are stand-alone one-off
purchases that do not unlock any other product.
"""
from .models import register_models, Booking  # noqa: F401
from .routes import register_routes  # noqa: F401

__all__ = ["register_routes", "register_models", "Booking"]
