"""MS4 W3e G4 — Unified Onboarding blueprint.

Replaces two legacy onboarding flows with ONE flow:

  - Legacy `/onboarding` (business-org wizard) → now an escape hatch at
    `/onboarding/legacy`; primary URL 302s to `/onboarding/welcome`.
  - Legacy `/fie/triage` (3-question FIESTA triage) → still accessible but
    sl_foreign_income personas with no income_sources are routed to
    `/onboarding/welcome` instead.

The new flow has three steps, all under the unified `/onboarding/*` prefix:

  1. `/onboarding/welcome`       — value-prop + "Get started" CTA
  2. `/onboarding/income-sources` — full-page render of the G3.6 picker partial
  3. `/onboarding/confirm`        — confirms enabled modules + recommended next step;
                                     marks `User.onboarding_completed=True` on POST

Plus a small JSON endpoint for client-side flow control:

  - `/api/fiesta/onboarding-state` — returns current step + selected
    income_sources + recommended_next_step

See `working files/_fiesta_unification_addendum_20260525.md` §G4 for the
binding spec.
"""
from __future__ import annotations

from .routes import register_blueprint

__all__ = ["register_blueprint"]
