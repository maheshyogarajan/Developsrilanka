"""fiesta.tax — Sri Lanka Personal Income Tax engine + preview.

Public surface:
  - compute_tax_25_26(income, deductions=None, senior_citizen=False) -> TaxComputation
  - compute_tax(income, deductions, year, senior_citizen=False) -> TaxComputation
  - quick_preview(...) -> dict  (S0 lightweight preview for landing)
  - Income, Deductions, TaxComputation, TaxYear, Reliefs (types)
  - PreviewError (ValueError subclass for known input failures)

Scope (Phase 1, this ship):
  - Core PIT for 25/26 + 24/25 (24/25 used for regression against SF flow)
  - 5 income components, solar relief (capped 600K), personal relief,
    senior-citizen extra relief
  - Per-band audit trail, marginal + effective rates
  - JS parity bundle for browser-side preview (see _js_parity)
  - S0 quick_preview: bracket-by-bracket estimate from a few inputs to
    defuse the "Rs 2,500 saving Rs 540,000 = scam" risk before paywall.

Out of scope (deferred to Phase 2-4):
  - Foreign-income FX conversion (Phase 2 — fiesta/tax/fx.py)
  - WHT credits (Phase 2 — fiesta/tax/wht.py)
  - Donations + qualifying payments (Phase 3 — fiesta/tax/qualifying_payments.py)
  - Per-category deduction aggregation (Phase 3 — fiesta/tax/deductions.py)
  - Amendments + penalties (Phase 4 — fiesta/tax/penalties.py)

Provenance:
  - 24/25 slabs verbatim from SF Scr_Tax_Computation_for_PRM lines 912-922.
  - 25/26 slabs unanimous in council brief tax_math_anchors.
"""

from __future__ import annotations

from .engine import compute_tax, compute_tax_25_26
from .preview import PreviewError, quick_preview
from .types import (
    Deductions,
    Income,
    Reliefs,
    TaxComputation,
    TaxYear,
)

__all__ = [
    "compute_tax",
    "compute_tax_25_26",
    "quick_preview",
    "PreviewError",
    "Income",
    "Deductions",
    "Reliefs",
    "TaxComputation",
    "TaxYear",
]
