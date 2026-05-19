"""fiesta.service_providers.models — Service Provider persistence layer.

S6 "Your support team — Service Providers". The customer lists every person
they pay for services that produce income (subcontractor, accountant,
designer, VA, etc.). Two tables:

    fiesta_service_provider
        One row per SP. Soft-archive only (`archived=True`) — never
        hard-delete; downstream S8/S9 audit trail + IRD examiner defence
        relies on continuity.

    fiesta_service_provider_relationship
        Persisted §195 detection result. One-to-one with ServiceProvider
        (unique on sp_id). Refreshed on every create / edit / re-detect.

Money discipline: monetary fields (hourly_rate, monthly_rate) are stored
as integer LKR-cents on the model so the SQLAlchemy schema does not
introduce float drift. The CRUD layer accepts Decimal / int / float and
multiplies by 100 on the way in.

Total paid YTD is INTENTIONALLY NOT stored — recomputed on read from a
join against PaymentLedger (when present) or returned as zero (MVP).
See routes._compute_total_paid_ytd().

Service-type picklist:
    Cross-linked to S5 deductions catalog (subcontractor_fees +
    professional_services). The S6 picklist is a SUPERSET — we want
    finer granularity at SP-list time so the customer thinks in concrete
    roles ("accountant", "designer") rather than IRA categories. The
    mapping from SP service_type to S5 deduction category is handled
    in fiesta.service_providers.taxonomy.

DB compatibility: shared `from app import db` so the table joins
the existing migration framework. Created by db.create_all() in
app._ensure_additive_schema() at app startup.
"""
from __future__ import annotations

import logging
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DB session — match the same defensive pattern used by S2 / S5 so pure
# unit tests can import the module without a Flask app context.
# ---------------------------------------------------------------------------
try:
    from app import db
    from sqlalchemy import Index, UniqueConstraint
    _HAS_APP = True
except Exception as exc:  # pragma: no cover -- unit-test fallback
    logger.warning(
        "service_providers/models.py: app.db not available — using "
        "standalone Base: %s", exc
    )
    from sqlalchemy.orm import declarative_base
    from sqlalchemy import (
        Column, Integer, String, Boolean, DateTime, Date, Text, JSON,
        Index, UniqueConstraint, Float, ForeignKey,
    )

    class _StandaloneDb:
        Model = declarative_base()
        Column = Column
        Integer = Integer
        String = String
        Boolean = Boolean
        DateTime = DateTime
        Date = Date
        Text = Text
        JSON = JSON
        Float = Float
        ForeignKey = ForeignKey

    db = _StandaloneDb()  # type: ignore[assignment]
    _HAS_APP = False

# ---------------------------------------------------------------------------
# Service-type catalog (12 entries).
#
# Maps each SP role to:
#     - human label
#     - icon (Bootstrap Icons)
#     - s5_category (cross-link to S5 deduction catalog id; one of
#       "subcontractor_fees" or "professional_services")
#     - is_subcontractor: True for delivery subcontractors (IRA §6 produces
#       income), False for back-office services (IRA §6 still, but the
#       deductibility narrative is different — "necessary to produce
#       income" vs "directly producing income").
# ---------------------------------------------------------------------------
SERVICE_TYPE_CATALOG: list[dict[str, Any]] = [
    {
        "id": "subcontractor_developer",
        "name": "Developer / engineer (subcontracted)",
        "icon": "bi-code-slash",
        "s5_category": "subcontractor_fees",
        "is_subcontractor": True,
    },
    {
        "id": "subcontractor_designer",
        "name": "Designer (subcontracted)",
        "icon": "bi-palette",
        "s5_category": "subcontractor_fees",
        "is_subcontractor": True,
    },
    {
        "id": "subcontractor_writer",
        "name": "Writer / editor (subcontracted)",
        "icon": "bi-pencil-square",
        "s5_category": "subcontractor_fees",
        "is_subcontractor": True,
    },
    {
        "id": "subcontractor_marketer",
        "name": "Marketer / growth (subcontracted)",
        "icon": "bi-megaphone",
        "s5_category": "subcontractor_fees",
        "is_subcontractor": True,
    },
    {
        "id": "subcontractor_virtual_assistant",
        "name": "Virtual assistant",
        "icon": "bi-headset",
        "s5_category": "subcontractor_fees",
        "is_subcontractor": True,
    },
    {
        "id": "subcontractor_other",
        "name": "Other subcontractor",
        "icon": "bi-people",
        "s5_category": "subcontractor_fees",
        "is_subcontractor": True,
    },
    {
        "id": "professional_accountant",
        "name": "Accountant / bookkeeper",
        "icon": "bi-calculator",
        "s5_category": "professional_services",
        "is_subcontractor": False,
    },
    {
        "id": "professional_lawyer",
        "name": "Lawyer / legal advisor",
        "icon": "bi-bank2",
        "s5_category": "professional_services",
        "is_subcontractor": False,
    },
    {
        "id": "professional_tax_advisor",
        "name": "Tax advisor",
        "icon": "bi-clipboard-data",
        "s5_category": "professional_services",
        "is_subcontractor": False,
    },
    {
        "id": "professional_consultant",
        "name": "Business consultant",
        "icon": "bi-briefcase",
        "s5_category": "professional_services",
        "is_subcontractor": False,
    },
    {
        "id": "professional_coach",
        "name": "Coach / mentor",
        "icon": "bi-person-arms-up",
        "s5_category": "professional_services",
        "is_subcontractor": False,
    },
    {
        "id": "professional_other",
        "name": "Other professional service",
        "icon": "bi-three-dots",
        "s5_category": "professional_services",
        "is_subcontractor": False,
    },
]

SERVICE_TYPE_IDS: frozenset[str] = frozenset(s["id"] for s in SERVICE_TYPE_CATALOG)

# Fee structure picklist.
FEE_STRUCTURE_CHOICES: tuple[tuple[str, str], ...] = (
    ("hourly", "Hourly"),
    ("monthly", "Monthly retainer"),
    ("per_deliverable", "Per deliverable / project"),
    ("milestone", "Milestone-based"),
    ("other", "Other"),
)
FEE_STRUCTURE_IDS: frozenset[str] = frozenset(c[0] for c in FEE_STRUCTURE_CHOICES)

# Stated-relationship picklist.
#
# Empowerment voice: question framed as "How do you know this person?".
# Default = "professional_arms_length" — the overwhelming-majority answer
# for foreign-client-revenue earners. The detector treats this default as
# "no signal fires", per fiesta.compliance.related_party.NON_RELATED_RELATIONSHIPS.
#
# We deliberately do NOT expose the IRA s.195 jargon to the customer here;
# the customer says how they know the person, the system maps that to
# the s.195 framework downstream. The KEY part is the second tuple element
# (the value posted to the detector); the third element is human-displayed.
STATED_RELATIONSHIP_CHOICES: tuple[tuple[str, str], ...] = (
    ("professional_arms_length", "Independent professional / arm's-length"),
    ("spouse", "Spouse / civil partner"),
    ("parent", "Parent"),
    ("child", "Child"),
    ("sibling", "Sibling"),
    ("in_law", "In-law"),
    ("cousin", "Cousin"),
    ("self", "Myself (self-deal)"),
    ("business_partner", "Business partner / co-owner"),
    ("friend", "Friend (non-family)"),
    ("other", "Other / prefer not to say"),
)
STATED_RELATIONSHIP_IDS: frozenset[str] = frozenset(
    c[0] for c in STATED_RELATIONSHIP_CHOICES
)

# Map UI stated-relationship to detector's normalized vocabulary.
# (detector accepts a free-text "stated_relationship_to_service_provider"
# string and casefolds it against its RELATED_RELATIONSHIPS / NON_RELATED
# sets — we want to send the canonical value, not the human label.)
STATED_RELATIONSHIP_TO_DETECTOR: dict[str, str] = {
    "professional_arms_length": "independent contractor",
    "spouse": "spouse",
    "parent": "parent",
    "child": "child",
    "sibling": "sibling",
    "in_law": "in-law",
    "cousin": "cousin",
    "self": "self",
    "business_partner": "",  # treat as no-stated; rely on other signals
    "friend": "",  # treat as no-stated; rely on other signals
    "other": "",  # treat as no-stated; let other signals decide
}


# ---------------------------------------------------------------------------
# ServiceProvider model.
# ---------------------------------------------------------------------------
class ServiceProvider(db.Model):  # type: ignore[name-defined,misc]
    """One row per SP the customer pays for services.

    Money columns:
        hourly_rate_cents, monthly_rate_cents — nullable. Cents to avoid
        float drift. .hourly_rate / .monthly_rate properties return Decimal
        LKR for templates.
    """

    __tablename__ = "fiesta_service_provider"

    id = db.Column(db.Integer, primary_key=True)

    # Ownership ----------------------------------------------------------
    user_id = db.Column(db.Integer, nullable=False, index=True)

    # Identity -----------------------------------------------------------
    name = db.Column(db.String(255), nullable=False)
    nic = db.Column(db.String(32), nullable=True)
    tin = db.Column(db.String(32), nullable=True)

    # Address (flat columns, mirrors the detector's address dict shape) --
    address_line1 = db.Column(db.String(255), nullable=True)
    address_line2 = db.Column(db.String(255), nullable=True)
    city = db.Column(db.String(128), nullable=True)
    country = db.Column(db.String(64), nullable=True, default="LK")
    postcode = db.Column(db.String(16), nullable=True)

    # Banking ------------------------------------------------------------
    bank_name = db.Column(db.String(128), nullable=True)
    bank_account_number = db.Column(db.String(64), nullable=True)

    # Service classification --------------------------------------------
    # Must be in SERVICE_TYPE_IDS — enforced by routes layer.
    service_type = db.Column(db.String(64), nullable=False)

    # Fee structure ------------------------------------------------------
    # Must be in FEE_STRUCTURE_IDS — enforced by routes layer.
    fee_structure = db.Column(db.String(32), nullable=False, default="monthly")
    hourly_rate_cents = db.Column(db.Integer, nullable=True)
    monthly_rate_cents = db.Column(db.Integer, nullable=True)

    # Relationship disclosure -------------------------------------------
    # Must be in STATED_RELATIONSHIP_IDS — enforced by routes layer.
    stated_relationship_to_customer = db.Column(
        db.String(48), nullable=False, default="professional_arms_length"
    )

    # Tracking -----------------------------------------------------------
    # Whether downstream agreement generator should default the
    # §195 disclosure clause ON. Synced from ServiceProviderRelationship.
    requires_disclosure = db.Column(db.Boolean, nullable=False, default=False)

    # Soft-archive (audit-trail retention). Never hard-delete.
    archived = db.Column(db.Boolean, nullable=False, default=False, index=True)

    # Free-form note from the customer (e.g. "she works on Mondays only").
    notes = db.Column(db.String(2048), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        Index("ix_fiesta_sp_user_archived", "user_id", "archived"),
        Index("ix_fiesta_sp_user_service", "user_id", "service_type"),
    )

    # ------------------------------------------------------------------
    # Decimal LKR helpers.
    # ------------------------------------------------------------------
    @property
    def hourly_rate(self) -> Optional[Decimal]:
        if self.hourly_rate_cents is None:
            return None
        return (Decimal(self.hourly_rate_cents) / Decimal(100)).quantize(Decimal("0.01"))

    @hourly_rate.setter
    def hourly_rate(self, value: Any) -> None:
        if value is None or value == "":
            self.hourly_rate_cents = None
            return
        if isinstance(value, (int, float)):
            value = Decimal(str(value))
        self.hourly_rate_cents = int((Decimal(value) * 100).to_integral_value())

    @property
    def monthly_rate(self) -> Optional[Decimal]:
        if self.monthly_rate_cents is None:
            return None
        return (Decimal(self.monthly_rate_cents) / Decimal(100)).quantize(Decimal("0.01"))

    @monthly_rate.setter
    def monthly_rate(self, value: Any) -> None:
        if value is None or value == "":
            self.monthly_rate_cents = None
            return
        if isinstance(value, (int, float)):
            value = Decimal(str(value))
        self.monthly_rate_cents = int((Decimal(value) * 100).to_integral_value())

    # ------------------------------------------------------------------
    # Adapters for the §195 detector.
    # ------------------------------------------------------------------
    def to_detector_dict(self) -> dict[str, Any]:
        """Shape this SP for fiesta.compliance.related_party.detect_related_party.

        The detector expects:
            name, nic, address (dict), bank_account, service_type,
            monthly_fee_lkr.
        """
        addr: dict[str, Any] = {}
        if self.address_line1:
            addr["street"] = self.address_line1
        if self.address_line2:
            # Some upstream brain rolls the line2 into 'locality'. We send
            # city as locality and leave line2 as a noise field the
            # detector ignores; this matches the detector's existing
            # behaviour (it only reads street + locality + postcode).
            pass
        if self.city:
            addr["locality"] = self.city
        if self.postcode:
            addr["postcode"] = self.postcode

        # Monthly fee for market-rate band check. If the SP is hourly, we
        # estimate a notional monthly figure as (hourly_rate * 160 hours).
        monthly_fee_lkr: Optional[float] = None
        if self.monthly_rate_cents is not None:
            monthly_fee_lkr = self.monthly_rate_cents / 100.0
        elif self.hourly_rate_cents is not None:
            monthly_fee_lkr = (self.hourly_rate_cents / 100.0) * 160.0

        return {
            "name": self.name or "",
            "nic": self.nic or "",
            "address": addr or None,
            "bank_account": self.bank_account_number or "",
            "service_type": self.service_type or "",
            "monthly_fee_lkr": monthly_fee_lkr,
        }

    def to_dict(self, include_relationship: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "nic": self.nic,
            "tin": self.tin,
            "address_line1": self.address_line1,
            "address_line2": self.address_line2,
            "city": self.city,
            "country": self.country,
            "postcode": self.postcode,
            "bank_name": self.bank_name,
            "bank_account_number": self.bank_account_number,
            "service_type": self.service_type,
            "fee_structure": self.fee_structure,
            "hourly_rate_lkr": (
                str(self.hourly_rate) if self.hourly_rate is not None else None
            ),
            "monthly_rate_lkr": (
                str(self.monthly_rate) if self.monthly_rate is not None else None
            ),
            "stated_relationship_to_customer": self.stated_relationship_to_customer,
            "requires_disclosure": self.requires_disclosure,
            "archived": self.archived,
            "notes": self.notes,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "updated_at": (
                self.updated_at.isoformat() if self.updated_at else None
            ),
        }
        if include_relationship:
            d["relationship"] = None  # populated by routes layer
        return d

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ServiceProvider id={self.id} user={self.user_id} "
            f"name={self.name!r} svc={self.service_type} "
            f"archived={self.archived}>"
        )


# ---------------------------------------------------------------------------
# ServiceProviderRelationship model.
# ---------------------------------------------------------------------------
class ServiceProviderRelationship(db.Model):  # type: ignore[name-defined,misc]
    """Cached §195 detection result for one SP.

    One-to-one with ServiceProvider (unique on sp_id). Refreshed on every
    SP create / edit / re-detect. The JSONB column stores the full
    RelatedPartyResult so the audit-defence panel can replay the reasoning
    trace verbatim (no truncation; the customer-facing detail is the audit
    trail).
    """

    __tablename__ = "fiesta_service_provider_relationship"

    id = db.Column(db.Integer, primary_key=True)

    sp_id = db.Column(db.Integer, nullable=False, unique=True, index=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)

    # Snapshot of detection result -------------------------------------
    signals = db.Column(db.JSON, nullable=False, default=list)
    confidence = db.Column(db.Float, nullable=False, default=0.0)
    should_default_on_disclosure = db.Column(
        db.Boolean, nullable=False, default=False
    )
    audit_substance_risk = db.Column(
        db.String(8), nullable=False, default="low"
    )
    reasoning = db.Column(db.JSON, nullable=False, default=list)

    # Customer override --------------------------------------------------
    # If the customer disagrees with the default-ON disclosure, they can
    # toggle it OFF — but only with a commercial-substance justification
    # (free text). We record the override + the justification; downstream
    # S8/S9 generator uses customer_disclosure_override if present, else
    # should_default_on_disclosure.
    customer_disclosure_override = db.Column(db.Boolean, nullable=True)
    override_justification = db.Column(db.String(2048), nullable=True)
    override_set_at = db.Column(db.DateTime, nullable=True)

    last_detected_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sp_id": self.sp_id,
            "user_id": self.user_id,
            "signals": self.signals or [],
            "confidence": self.confidence,
            "should_default_on_disclosure": self.should_default_on_disclosure,
            "audit_substance_risk": self.audit_substance_risk,
            "reasoning": self.reasoning or [],
            "customer_disclosure_override": self.customer_disclosure_override,
            "override_justification": self.override_justification,
            "override_set_at": (
                self.override_set_at.isoformat() if self.override_set_at else None
            ),
            "last_detected_at": (
                self.last_detected_at.isoformat() if self.last_detected_at else None
            ),
        }

    @property
    def effective_disclosure_required(self) -> bool:
        """Disclosure flag the downstream S8 generator should honour.

        - If customer set an explicit override, use that.
        - Otherwise use the detector's default-on flag.
        """
        if self.customer_disclosure_override is not None:
            return bool(self.customer_disclosure_override)
        return bool(self.should_default_on_disclosure)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SPRelationship sp_id={self.sp_id} signals={self.signals} "
            f"conf={self.confidence:.3f} default_on="
            f"{self.should_default_on_disclosure}>"
        )
