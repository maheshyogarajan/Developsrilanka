"""fiesta.tax_bill -- S12 "Your tax bill" outcome screen.

S12 is the showcase of FIESTA's value: the customer sees their final tax
bill with full audit trail. Aggregates upstream modules (S3 profile,
S4 earnings, S5 deductions, S6 service providers, S7 property, S8 SP
agreements, S9 rental agreements) -> tax engine (Phase 1) -> presentation
with §195 disclosures, evidence status, IRA citations, and the
deduction-ratio gate (X6 S12 rule).

Public surface
--------------
    assemble_tax_inputs(user_id, tax_year)      -> TaxInputs
    compute_tax_bill(user_id, tax_year)         -> TaxBillReport
    run_gate(report, action)                    -> GateResult
    build_audit_pack(report, output_path)       -> pdf_bytes (or path)

Wiring
------
Register from main.py:

    from fiesta.tax_bill.routes import register_blueprint as register_tax_bill
    register_tax_bill(app)

Routes:
    GET  /tax-bill                               main view (current TY)
    GET  /tax-bill/<tax_year>                    view for one TY
    GET  /tax-bill/<tax_year>/breakdown          JSON of the full computation
    GET  /tax-bill/<tax_year>/export             audit pack PDF
    POST /tax-bill/<tax_year>/finalize           lock the bill before S14 submit

Sources
-------
- Council brief: FIESTA S12 "Your tax bill -- outcome + audit trail".
- Tax engine: fiesta.tax (Phase 1, shipped cd9aa97).
- Upstream models: fiesta.earnings.models, fiesta.deductions.models,
  fiesta.service_providers.models, fiesta.property.models,
  fiesta.agreements.models.
- X6 gate: fiesta.compliance.gate.gate_check("S12", ...).
"""
from __future__ import annotations

from .aggregator import TaxInputs, assemble_tax_inputs  # noqa: F401
from .compute import TaxBillReport, compute_tax_bill  # noqa: F401
from .gate_check import run_gate  # noqa: F401

# audit_pack is imported lazily inside routes (ReportLab is heavy and we
# don't want to take the import hit for every test that touches the
# aggregator).

__all__ = [
    "TaxInputs",
    "assemble_tax_inputs",
    "TaxBillReport",
    "compute_tax_bill",
    "run_gate",
]
