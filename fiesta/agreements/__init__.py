"""fiesta.agreements -- Service Agreement (S8) + Rental Agreement (S9) PDF generators.

Wave 3 (2026-05-20). Source-of-truth template: G.1.3 v0.1 draft at
`working files/strategic/council/persistent/fiesta/blocker_G.1.3_proposal.md`.
Pending Lanka.tax legal counsel pass (target close 2026-05-27); when that
returns, bump TEMPLATE_VERSION here + refresh the .j2 templates.

Modules
-------
service_pdf : Top-level Service Agreement generator (ReportLab).
disclosure  : §195 related-party disclosure-default-ON orchestration.
service_routes : Flask blueprint exposing preview / generate / download / history.
models      : SQLAlchemy `service_agreements` table.
templates/  : Jinja2 templates -- one per agreement kind.
"""
from __future__ import annotations

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

__all__ = [
    "DisclosureDecision",
    "DisclosureDecisionInput",
    "decide_disclosure",
    "TEMPLATE_VERSION",
    "ServicePdfRenderResult",
    "generate_service_agreement_pdf",
    "make_reference_id",
]
