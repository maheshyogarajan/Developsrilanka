"""fiesta.earnings — S4 'Drop in statements' Connect-earnings screen.

THE_PATH_20260520 Self-File-only v1, Wave 3 / S4 (2026-05-20).

What this package owns:
  * Customer-facing 'drop your statements' upload UI (templates/earnings/).
  * Models for uploaded `Statement` rows and extracted `IncomeEntry` rows
    (per-line items the tax engine consumes).
  * doc_lens integration — extraction with 5-attempt cap, manual-entry fallback.
  * to_tax() bridge — aggregate confirmed IncomeEntry rows for the tax engine.
  * Audit log + edit history (original_value preserved when customer edits).

What this package does NOT own:
  * The doc_lens module itself (lives in fiesta.delivery_ops.doc_lens; we call
    validate_doc() from extractor.py).
  * FX rate sourcing (delegates to fx_rate_service.get_rate when available).
  * Tax computation (delegates to fiesta.tax.engine.compute_tax_25_26 via the
    to_tax.income_summary_for_tax_year() shape contract).

Register via earnings_routes.register_routes(app) from main.py.
"""
from __future__ import annotations

__all__ = ["models", "routes", "extractor", "to_tax"]
