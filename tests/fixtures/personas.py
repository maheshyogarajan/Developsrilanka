"""Auth-fixture upserts for Playwright/mobile regression tests.

Why this exists
---------------
The mobile viewport regression test (tests/mobile/test_viewports.py) runs
against a real deployed BASE_URL (default https://fiesta-mvp.fly.dev) and
authenticates as ``playwright.smoke@smarter.tax``. Prior to this fixture,
that account had no ``persona`` value and no active paywall subscription,
so two logged-in surfaces — ``/tax-bill/<ty>`` and ``/agreements/service``
— redirected the test browser to ``/login``. The resulting snapshots were
of the login page, not the templates under test. Tier C #5 mobile audit
called this out (28 false tap-target violations measured against the login
page chrome).

What this fixture does
----------------------
Idempotently upserts the smoke account so the two logged-in surfaces
render their real templates:

  1) User row with persona='sl_foreign_income'. The persona column gates
     /tax-bill, /agreements, and the authenticated hub at app.py:677.
     Also sets is_email_verified + onboarding_completed + a known
     password_hash so the existing TEST_PASSWORD continues to work.

  2) An active Subscription row at tier='self_file'. Both routes are
     decorated with @paywall_required(min_tier='self_file', ...) — without
     this row the request would be rewritten to /pricing.

  3) At least one ServiceProvider row owned by the user. The bare
     /agreements/service route (service_routes.py:318) redirects to the
     SP listing when the user has zero SPs and redirects straight to the
     SP's preview when they have exactly one. We want the preview path,
     so we make sure there's a single deterministic seed SP.

Scope cap (per the Tier D2 B2 task brief)
-----------------------------------------
* Test-fixture work ONLY. No new persona values, no changes to production
  user/persona logic, no migrations.
* Writes go to the Neon DB via the live SQLAlchemy models — the same
  models the deployed app uses. The seed targets a dedicated test email
  so production users are not touched.
* Helper is callable from CI or interactively; safe to re-run (every
  operation is "find-or-update").

Usage
-----
::

    from tests.fixtures.personas import ensure_sl_foreign_income_user
    ensure_sl_foreign_income_user()  # uses env defaults

    # or with overrides:
    ensure_sl_foreign_income_user(
        email="playwright.smoke@smarter.tax",
        password="Playwright$moke2026!",
    )

Returns a small dict with the canonical ids for downstream assertions::

    {"user_id": 4711, "subscription_id": 832, "service_provider_id": 217}
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, TypedDict

log = logging.getLogger(__name__)


DEFAULT_EMAIL = "playwright.smoke@smarter.tax"
DEFAULT_PASSWORD = "Playwright$moke2026!"
DEFAULT_NAME = "Playwright Smoke"
DEFAULT_PERSONA = "sl_foreign_income"

# Seed SP attributes. Lightweight, deterministic, recognisably a fixture.
SEED_SP_NAME = "Playwright Fixture SP"
SEED_SP_SERVICE_TYPE = "subcontractor_developer"
SEED_SP_FEE_STRUCTURE = "monthly"
SEED_SP_MONTHLY_RATE_CENTS = 50_000_00  # LKR 50,000


class _SeedResult(TypedDict):
    user_id: int
    subscription_id: int
    service_provider_id: int


def _load_fiesta_env() -> None:
    """Load DATABASE_URL / SECRET_KEY from the cockpit env file if present.

    Mirrors tests/remittance/conftest.py so this helper is usable from a
    bare ``python -c`` invocation, not just under pytest's collection.
    """
    env_path = Path(
        "G:/My Drive/CEO OS/working files/_cockpit_fiesta/fiesta.env"
    )
    if not env_path.exists():
        log.debug("fiesta.env not found at %s; assuming env is already set",
                  env_path)
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip())


def ensure_sl_foreign_income_user(
    email: str = DEFAULT_EMAIL,
    password: str = DEFAULT_PASSWORD,
    name: str = DEFAULT_NAME,
    persona: str = DEFAULT_PERSONA,
) -> _SeedResult:
    """Idempotently upsert the auth fixture user + subscription + SP.

    Safe to call repeatedly. Returns the canonical row ids.
    """
    _load_fiesta_env()

    # IMPORTANT: import inside the function so this module stays importable
    # in environments without DATABASE_URL set (e.g. test collection of
    # unrelated suites). All imports here are deferred until call-time.
    import main  # noqa: F401  ensure blueprints + model registration
    from app import app as flask_app, db
    from werkzeug.security import generate_password_hash
    from models import User

    # Subscription is registered lazily via fiesta.paywall.register_models.
    from fiesta.paywall import register_models, get_models
    from fiesta.paywall.models import (
        TIER_SELF_FILE,
        current_sl_tax_year,
        expires_at_for_tax_year,
    )

    # ServiceProvider lives in its own module; importing registers the model.
    from fiesta.service_providers.models import ServiceProvider

    register_models()

    with flask_app.app_context():
        db.create_all()

        # ----- 1) User -----
        user = User.query.filter(User.email == email).one_or_none()
        if user is None:
            user = User(
                email=email,
                password_hash=generate_password_hash(password),
                name=name,
                role="user",
                subscription_status="free_trial",
                access_expiration_date=datetime.utcnow() + timedelta(days=365),
                is_email_verified=True,
                onboarding_completed=True,
                persona=persona,
            )
            db.session.add(user)
            db.session.commit()
            log.info("Created auth fixture user id=%s email=%s", user.id, email)
        else:
            # Repair drift. Re-set every gated attribute even if some are
            # already correct — the cost is negligible and an explicit set
            # is the simplest way to be re-run-safe.
            changed = False
            if user.persona != persona:
                user.persona = persona
                changed = True
            if not user.is_email_verified:
                user.is_email_verified = True
                changed = True
            if not user.onboarding_completed:
                user.onboarding_completed = True
                changed = True
            if not user.password_hash:
                # Only set the password if the row has none — never silently
                # overwrite a hash that may have been rotated by an operator.
                user.password_hash = generate_password_hash(password)
                changed = True
            if (
                user.access_expiration_date is None
                or user.access_expiration_date < datetime.utcnow() + timedelta(days=30)
            ):
                user.access_expiration_date = datetime.utcnow() + timedelta(days=365)
                changed = True
            if changed:
                db.session.commit()
                log.info("Repaired auth fixture user id=%s", user.id)

        # ----- 2) Active self_file subscription -----
        Subscription, _, _ = get_models()
        sub = (
            Subscription.query
            .filter(Subscription.user_id == user.id)
            .filter(Subscription.tier == TIER_SELF_FILE)
            .filter(Subscription.status == "active")
            .filter(Subscription.expires_at > datetime.utcnow())
            .order_by(Subscription.expires_at.desc())
            .first()
        )
        if sub is None:
            tax_year = current_sl_tax_year()
            sub = Subscription(
                user_id=user.id,
                tier=TIER_SELF_FILE,
                tax_year=tax_year,
                purchased_at=datetime.utcnow(),
                expires_at=expires_at_for_tax_year(tax_year),
                status="active",
                amount_paid_lkr=0,  # fixture row, never went through Stripe
            )
            db.session.add(sub)
            db.session.commit()
            log.info("Created fixture subscription id=%s user=%s", sub.id, user.id)

        # ----- 3) At least one ServiceProvider -----
        sp = (
            ServiceProvider.query
            .filter(ServiceProvider.user_id == user.id)
            .filter(ServiceProvider.archived.is_(False))
            .order_by(ServiceProvider.id.asc())
            .first()
        )
        if sp is None:
            sp = ServiceProvider(
                user_id=user.id,
                name=SEED_SP_NAME,
                service_type=SEED_SP_SERVICE_TYPE,
                fee_structure=SEED_SP_FEE_STRUCTURE,
                monthly_rate_cents=SEED_SP_MONTHLY_RATE_CENTS,
                stated_relationship_to_customer="professional_arms_length",
                requires_disclosure=False,
                archived=False,
            )
            db.session.add(sp)
            db.session.commit()
            log.info("Created fixture service provider id=%s user=%s",
                     sp.id, user.id)

        return _SeedResult(
            user_id=int(user.id),
            subscription_id=int(sub.id),
            service_provider_id=int(sp.id),
        )


if __name__ == "__main__":  # pragma: no cover - manual seed entry point
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = ensure_sl_foreign_income_user()
    print(
        "Seeded: user_id={user_id} subscription_id={subscription_id} "
        "service_provider_id={service_provider_id}".format(**result)
    )
