"""fiesta.agreements — S8/S9 customer-artefact PDF generators.

Generates Service Agreements (S8) and Rental Agreements (S9) for FIESTA
customers to present to IRD as evidence of:

  - S8 Service Agreement: deductible service-provider expenses under IRA s.6
    OR justification of foreign-client revenue as trade income.
  - S9 Rental Agreement (THIS MODULE): deductible workspace rent under
    IRA s.6 ("wholly + exclusively + necessarily for production of income").

Architecture (per G.1.3 proposal + brief 2026-05-20):

    fiesta/agreements/
      __init__.py                       <- this file, public surface
      templates/
        rental_agreement.j2             <- Jinja2 source for the PDF body
      models.py                         <- SQLAlchemy + pydantic DTOs
      pdf_engine.py                     <- shared PDF gen helpers (S8/S9 reuse)
      rental_pdf.py                     <- S9 entry point (render -> bytes)
      stamp_duty.py                     <- SL Stamp Duty Act exposure calc
      rental_routes.py                  <- Flask blueprint /agreements/rental
      RENTAL_AGREEMENT_DESIGN.md        <- v1 design + audit-defence rationale

The S8 sibling (service_pdf.py / service_routes.py) is built by the parallel
worktree on wave3/s8-service-agreement and will share pdf_engine.py via a
follow-up merge. The shared surface is intentionally MINIMAL (just
fonts/margins/branding constants + reference-ID minting) so the two builds
can land independently then converge.

§195 INTEGRATION
================
Every Rental Agreement render calls fiesta.compliance.related_party
before rendering. When should_default_on_disclosure=True the template's
§195 disclosure block is included; the customer can override per-PDF (must
supply a reason which is persisted to RentalAgreementGenerated for audit).

STAMP DUTY
==========
SL Stamp Duty Act No. 12 of 2006 charges a stamp on leases > 365 days. The
default term is 364 days; stamp_duty.py warns the customer if they pick a
longer term and computes the expected duty so the customer can decide
whether to pay it or shorten the term.
"""
from __future__ import annotations

# Re-export the public surface so callers can `from fiesta.agreements import ...`
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

__all__ = [
    "PDF_BRANDING",
    "RentalAgreementGeneratedSchema",
    "RentalAgreementInput",
    "RentalPDFOutput",
    "mint_reference_id",
    "render_rental_agreement",
    "stamp_duty_for_term",
]
