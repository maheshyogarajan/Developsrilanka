"""fiesta.submit -- S14 Submit screen: final gate + IRD-ready export package.

Wave 3 Week 5 (2026-05-20). This is the END of the FIESTA customer journey:
S2 signup -> S3 profile -> S4 earnings -> S5 deductions -> S6 SPs ->
S7 property -> S8 Service Agreement -> S9 Rental Agreement ->
S10 prep -> S12 tax bill -> S14 SUBMIT.

What S14 does
-------------
1. Final X6 compliance gate (calls `fiesta.compliance.gate.gate_check("S14", ...)`).
   - unresolved-warnings  -> yellow with override
   - section-195-missing  -> red block
   - deduction-ratio-final -> red block
   - missing-attestation  -> red block (S14-specific rule, vendored here)
2. Customer attestation under Electronic Transactions Act 19 of 2006
   + IRA section 195 (responsible-filer declaration).
3. IRD-ready export pack -- ZIP containing (a) pre-filled IRD return PDF
   + (b) FIESTA audit pack (S12 output) + (c) Service Agreements as schedules.
4. IRD walkthrough -- embeds G.1.5 screenshots inline with annotations.
5. Customer self-reports "filed on IRD" + uploads acknowledgment receipt.

Self-File-only in v1. Auto-File deferred to v1.1.

Public surface
--------------
- `fiesta.submit.models.Submission` -- one row per submit attempt
- `fiesta.submit.models.IrdConfirmationReceipt` -- customer-uploaded receipt
- `fiesta.submit.routes.register_routes(app)` -- wire blueprint into Flask app
- `fiesta.submit.final_gate.run_final_gate(customer_data)` -- thin wrapper
  around the X6 gate that adds S14-specific rules (missing-attestation).
- `fiesta.submit.attestation.build_attestation_text(...)` -- snapshot text
- `fiesta.submit.attestation.sign_attestation(...)` -- captures IP+ts+name
- `fiesta.submit.export.build_export_zip(submission, output_dir)` -- ZIP pack
"""
from __future__ import annotations

__all__ = [
    "TEMPLATE_VERSION",
]

TEMPLATE_VERSION = "v0.1-draft"
