"""
webhooks/ — third-party webhook handlers.

Currently hosts:

  * stripe_subscription.py — Tier D1 C1 subscription auto-renew + billing
    portal. Distinct from the legacy stripe_routes.py (one-time checkout
    webhook) and fiesta/paywall/pricing_screen.py (X1 one-time webhook).

Each handler module exposes a Flask blueprint + a ``register_routes(app)``
helper. main.py wires them in.
"""
