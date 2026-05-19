"""fiesta.tax — Sri Lanka Personal Income Tax engine (Phase 1).

Public surface:
  - compute_tax_25_26(income, deductions=None, senior_citizen=False)
        -> TaxComputation
  - compute_tax(income, deductions, year, senior_citizen=False)
        -> TaxComputation
  - Income, Deductions, TaxComputation, TaxYear  (types)

Scope (Phase 1, this ship):
  - Core PIT for 25/26 + 24/25 (24/25 used for regression against SF flow)
  - 5 income components, solar relief (capped 600K), personal relief,
    senior-citizen extra relief
  - Per-band audit trail, marginal + effective rates
  - JS parity bundle for browser-side preview (see _js_parity)

Out of scope (deferred to Phase 2-4):
  - Foreign-income FX conversion (Phase 2 — fiesta/tax/fx.py)
  - WHT credits (Phase 2 — fiesta/tax/wht.py)
  - Donations + qualifying payments (Phase 3 — fiesta/tax/qualifying_payments.py)
  - Per-category deduction aggregation (Phase 3 — fiesta/tax/deductions.py)
  - Amendments + penalties (Phase 4 — fiesta/tax/penalties.py)

Provenance:
  - G.1.4 proposal:
      working files/strategic/council/persistent/fiesta/blocker_G.1.4_proposal.md
  - Council THE_PATH 2026-05-20:
      working files/strategic/council/persistent/fiesta/THE_PATH_20260520.md
  - 24/25 slabs verbatim from SF Scr_Tax_Computation_for_PRM lines 912-922.
  - 25/26 slabs unanimous in council brief tax_math_anchors.
"""

from __future__ import annotations

from .engine import compute_tax, compute_tax_25_26
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
    "Income",
    "Deductions",
    "Reliefs",
    "TaxComputation",
    "TaxYear",
]
