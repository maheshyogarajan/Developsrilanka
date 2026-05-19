"""fiesta.agreements -- S8 Service Agreement + S9 Rental Agreement PDF generators.

Wave 3 (2026-05-20). Source-of-truth templates: G.1.3 v0.1 draft at
`working files/strategic/council/persistent/fiesta/blocker_G.1.3_proposal.md`.
Pending Lanka.tax legal counsel pass (target close 2026-05-27); when that
returns, bump TEMPLATE_VERSION here + refresh the .j2 templates.

Generates Service Agreements (S8) and Rental Agreements (S9) for FIESTA
customers to present to IRD as evidence of:

  - S8 Service Agreement: deductible service-provider expenses under IRA s.6
    OR justification of foreign-client revenue as trade income.
  - S9 Rental Agreement: deductible workspace rent under IRA s.6
    ("wholly + exclusively + necessarily for production of income").

Modules
-------
service_pdf    : S8 Service Agreement generator (ReportLab).
disclosure     : §195 related-party disclosure-default-ON orchestration.
service_routes : Flask blueprint exposing preview / generate / download / history.
models         : SQLAlchemy `service_agreements` + `rental_agreements_generated` tables.
templates/     : Jinja2 templates -- one per agreement kind.
pdf_engine     : Shared PDF generation helpers (S8/S9 reuse).
rental_pdf     : S9 entry point (render -> bytes).
stamp_duty     : SL Stamp Duty Act exposure calc.
rental_routes  : Flask blueprint /agreements/rental.

§195 INTEGRATION
================
Every agreement render calls fiesta.compliance.related_party before
rendering. When should_default_on_disclosure=True the template's §195
disclosure block is included; the customer can override per-PDF (must
supply a reason which is persisted for audit).

STAMP DUTY (S9 only)
====================
SL Stamp Duty Act No. 12 of 2006 charges a stamp on leases > 365 days. The
default term is 364 days; stamp_duty.py warns the customer if they pick a
longer term and computes the expected duty so the customer can decide
whether to pay it or shorten the term.
"""
from __future__ import annotations

# S8 surface
from fiesta.agreements.disclosure import (
    DisclosureDecision,
    DisclosureDecisionInput,
    decide_disclosure,
)
from fiesta.agreements.service_pdf import (
    TEMPLATE_VERSION,
    ServicePdfRenderResult,
    generate_service_agreement_pdf,
    make_reference_id,
)

# S9 surface (pdf_engine + rental_pdf + stamp_duty)
try:
    from fiesta.agreements.models import (
        RentalAgreementGeneratedSchema,
        RentalAgreementInput,
    )
    from fiesta.agreements.pdf_engine import (
        PDF_BRANDING,
        mint_reference_id,
    )
    from fiesta.agreements.rental_pdf import (
        RentalPDFOutput,
        render_rental_agreement,
    )
    from fiesta.agreements.stamp_duty import stamp_duty_for_term
    _S9_AVAILABLE = True
except ImportError:
    _S9_AVAILABLE = False

__all__ = [
    # S8
    "DisclosureDecision",
    "DisclosureDecisionInput",
    "decide_disclosure",
    "TEMPLATE_VERSION",
    "ServicePdfRenderResult",
    "generate_service_agreement_pdf",
    "make_reference_id",
]

if _S9_AVAILABLE:
    __all__ += [
        "PDF_BRANDING",
        "RentalAgreementGeneratedSchema",
        "RentalAgreementInput",
        "RentalPDFOutput",
        "mint_reference_id",
        "render_rental_agreement",
        "stamp_duty_for_term",
    ]
