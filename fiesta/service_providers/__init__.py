"""fiesta.service_providers — S6 customer-journey screen.

"Your support team — Service Providers". The customer lists every person
they pay for services that produce their income (subcontractor, accountant,
lawyer, marketer, designer, VA, etc.). Each entry triggers an inline
§195 related-party check (fiesta.compliance.related_party). Disclosure
defaults ON when a relationship is detected; the customer can switch it
off only with a commercial-substance override.

Upstream of S8 (Service Agreement generator) — S6 is where the list lives,
S8 reads it to author the agreement copy.

Voice: empowerment ("Add a Service Provider"), not registration. §195
banners gentle ("Heads up"), never warning. Every detection trace is
exposed to the customer for audit-defensibility.
"""
from __future__ import annotations

# Public surface re-exports for sibling modules and downstream importers.
from fiesta.service_providers.models import (
    ServiceProvider,
    ServiceProviderRelationship,
    SERVICE_TYPE_CATALOG,
    STATED_RELATIONSHIP_CHOICES,
    FEE_STRUCTURE_CHOICES,
)
from fiesta.service_providers.related_party import (
    run_detection_for_sp,
    persist_detection_result,
)

__all__ = [
    "ServiceProvider",
    "ServiceProviderRelationship",
    "SERVICE_TYPE_CATALOG",
    "STATED_RELATIONSHIP_CHOICES",
    "FEE_STRUCTURE_CHOICES",
    "run_detection_for_sp",
    "persist_detection_result",
]
