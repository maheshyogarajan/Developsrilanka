"""
fiesta.paywall — X1 Paywall Trigger (cross-cutting, Wave 2 build).

Council brief (2026-05-20): unify the paywall trigger across S0-S14 screens.
Free trial unlocks S0-S5 (estimator + signup + profile + earnings + deductions
education). Paywall fires at S6+ (service providers / agreement generators /
tax bill / submit) because committing to deductions requires generating Service
Agreements — paid territory.

Pricing model (X1, distinct from the legacy 3-tier subscription model in
``pricing_engine.py``):

* ``self_file`` — Rs 2,500 one-time, refundable 14 days, **tax-year-bounded**
  (expires 31 Mar TY+1). Unlocks S6-S12 + S14.
* ``auto_file`` — deferred to v1.1.

Wiring:

  >>> from fiesta.paywall import paywall_required, register_routes, register_models
  >>> register_models()  # idempotent, called from main.py
  >>> register_routes(app)
  >>> @paywall_required(min_tier='self_file', screen_id='S6')
  ... def service_provider_view(): ...

Hard rules
----------
1. **One-time purchases, not subscriptions.** Stripe Checkout in ``payment``
   mode (NOT ``subscription``). Tax-year boundary, not 12-month rolling.
2. **Idempotent webhook.** Re-deliveries of ``checkout.session.completed``
   MUST NOT double-create Subscription rows. We dedupe on
   ``stripe_payment_intent_id``.
3. **AJAX returns 402 + JSON.** Browser GETs redirect to /pricing with
   ``return_to``. AJAX/JSON requests get 402 Payment Required with
   ``{"paywall_url": "..."}``.
4. **Funnel instrumented.** Every paywall fire writes ``PaywallEvent``;
   conversion updates ``converted_at`` + ``conversion_revenue_lkr`` once
   the matching Subscription row materialises.
"""

from .models import (
    register_models,
    TIER_FREE_TRIAL, TIER_SELF_FILE, TIER_AUTO_FILE,
    SELF_FILE_PRICE_LKR, current_sl_tax_year, expires_at_for_tax_year,
)
from .gate import (
    paywall_required,
    is_tier_active,
    active_subscription,
    effective_tier,
    FREE_TIER_SCREENS,
    SELF_FILE_SCREENS,
    AUTO_FILE_SCREENS,
)
from .trial import is_in_trial, trial_days_remaining, trial_ends_at, TRIAL_DAYS
from .funnel import funnel_summary, funnel_daily
from .pricing_screen import register_routes, paywall_bp, SELF_FILE_PRODUCT


def get_models():
    """Lazy accessor for Subscription / PaywallEvent / StripeEvent classes.

    Returns the three classes from .models (which are None until
    register_models() has been called). Useful in tests + admin routes."""
    from . import models as _models
    return _models.Subscription, _models.PaywallEvent, _models.StripeEvent


__all__ = [
    "register_models",
    "register_routes",
    "paywall_bp",
    "paywall_required",
    "is_tier_active",
    "active_subscription",
    "effective_tier",
    "is_in_trial",
    "trial_days_remaining",
    "trial_ends_at",
    "TRIAL_DAYS",
    "funnel_summary",
    "funnel_daily",
    "FREE_TIER_SCREENS",
    "SELF_FILE_SCREENS",
    "AUTO_FILE_SCREENS",
    "TIER_FREE_TRIAL",
    "TIER_SELF_FILE",
    "TIER_AUTO_FILE",
    "SELF_FILE_PRICE_LKR",
    "current_sl_tax_year",
    "expires_at_for_tax_year",
    "SELF_FILE_PRODUCT",
    "get_models",
]
