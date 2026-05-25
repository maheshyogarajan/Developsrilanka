"""fiesta.tax_bill.aggregator -- pull from all upstream sources into TaxInputs.

Reads from (defensive imports -- module missing = empty section):
    fiesta.earnings.models.IncomeEntry              (S4)
    fiesta.earnings.to_tax.income_summary_for_tax_year
    fiesta.deductions.models.DeductionClaim         (S5)
    fiesta.deductions.estimate                       (cap rules)
    fiesta.service_providers.models.ServiceProvider (S6)
    fiesta.service_providers.models.ServiceProviderRelationship
    fiesta.property.models.Property / Landlord / RentalAgreement (S7)
    fiesta.property.models.LandlordRelationshipDetection
    fiesta.agreements.models.ServiceAgreement       (S8)
    fiesta.agreements.models.RentalAgreementGenerated (S9)
    fiesta.profile.models.FiestaProfile             (S3)

Tax-year string formats (intentionally tolerated -- upstream modules differ):
    S4:  "2025-26"
    S5:  "2025/2026"
    S7:  "2025/2026"
    S9:  "25_26" or similar
We normalise via `_canonical_tax_year()` which accepts any of those and
returns the canonical fiesta.tax.types.TaxYear value (Y25_26 / Y24_25).

This module is the ONLY place that knows how to bridge upstream tax-year
string conventions. The tax engine and the compute layer use the canonical
TaxYear enum exclusively.

Design constraints
------------------
- Pure function-ish: reads DB, but does not mutate. (Idempotent FX backfill
  is delegated to fiesta.earnings.to_tax which writes amount_lkr back to
  rows -- intentional and well-documented there.)
- Headless-import safe: any upstream not loaded => empty section, never raise.
- Decimal everywhere money is involved.

x9 tier-b (2026-05-24): the legacy per-table loaders below (_load_profile,
_load_deductions, _load_service_providers, _load_property_and_rentals) are
kept in place but are NOT invoked on the hot path when `vw_tax_bill_context`
(see migrations/add_vw_tax_bill_context.py) is available. The new
`_load_via_view` collapses the 6-baseline + 2*N_sp + 4*N_rental query fan-out
(~20 queries for a typical user) into a single SELECT against the view. If
the view does not exist (dev/test env without the migration applied), we
fall back to the legacy loaders so behaviour stays identical -- never raise.
The S4 earnings path stays separate from the view because
`income_summary_for_tax_year` does an idempotent FX backfill (writes
amount_lkr back to IncomeEntry rows on first read) that the view cannot
preserve.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tax-year canonicalisation
# ---------------------------------------------------------------------------

# Acceptable input forms -> S4-format (canonical to_tax.income_summary_for_tax_year)
_TAX_YEAR_ALIASES_S4: dict[str, str] = {
    "2025-26": "2025-26",
    "2025/26": "2025-26",      # X9 F6.1: current_sl_tax_year() returns this form
    "2025/2026": "2025-26",
    "25/26": "2025-26",
    "25_26": "2025-26",
    "Y25_26": "2025-26",
    "2024-25": "2024-25",
    "2024/25": "2024-25",      # X9 F6.1
    "2024/2025": "2024-25",
    "24/25": "2024-25",
    "24_25": "2024-25",
    "Y24_25": "2024-25",
}

# Same input -> S5 format
_TAX_YEAR_ALIASES_S5: dict[str, str] = {
    "2025-26": "2025/2026",
    "2025/26": "2025/2026",    # X9 F6.1
    "2025/2026": "2025/2026",
    "25/26": "2025/2026",
    "25_26": "2025/2026",
    "Y25_26": "2025/2026",
    "2024-25": "2024/2025",
    "2024/25": "2024/2025",    # X9 F6.1
    "2024/2025": "2024/2025",
    "24/25": "2024/2025",
    "24_25": "2024/2025",
    "Y24_25": "2024/2025",
}


import re as _re

# X9 F6.1: regex fallback so the normaliser handles any future YYYY/YY input
# (e.g. "2026/27", "2027/28") without us having to extend the alias map year
# by year. `current_sl_tax_year()` emits this form, so this fallback is what
# keeps /tax-bill from breaking after each 1 April fiscal flip.
_YYYY_YY_PATTERN = _re.compile(r"^(\d{4})/(\d{2})$")
_YYYY_YYYY_PATTERN = _re.compile(r"^(\d{4})/(\d{4})$")


def normalise_tax_year_to_s4_format(ty: str) -> str:
    """Return the S4/earnings canonical form (e.g. '2025-26')."""
    s = str(ty)
    if s in _TAX_YEAR_ALIASES_S4:
        return _TAX_YEAR_ALIASES_S4[s]
    m = _YYYY_YY_PATTERN.match(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = _YYYY_YYYY_PATTERN.match(s)
    if m and m.group(2).startswith(m.group(1)[:2]):
        # "2025/2026" -> "2025-26"
        return f"{m.group(1)}-{m.group(2)[2:]}"
    return s


def normalise_tax_year_to_s5_format(ty: str) -> str:
    """Return the S5/deductions canonical form (e.g. '2025/2026')."""
    s = str(ty)
    if s in _TAX_YEAR_ALIASES_S5:
        return _TAX_YEAR_ALIASES_S5[s]
    m = _YYYY_YY_PATTERN.match(s)
    if m:
        yy = m.group(1)
        return f"{yy}/{yy[:2]}{m.group(2)}"
    return s


def canonical_tax_year_enum(ty: str):
    """Return fiesta.tax.types.TaxYear enum value, or None if engine missing."""
    try:
        from fiesta.tax.types import TaxYear
    except Exception as exc:  # pragma: no cover
        logger.warning("tax engine unavailable: %s", exc)
        return None
    norm = normalise_tax_year_to_s4_format(ty)
    return {
        "2025-26": TaxYear.Y25_26,
        "2024-25": TaxYear.Y24_25,
    }.get(norm)


# ---------------------------------------------------------------------------
# TaxInputs -- the single shape S12 hands to the engine + UI.
# ---------------------------------------------------------------------------


@dataclass
class TaxInputs:
    """Aggregated upstream snapshot for one (user, tax_year).

    `engine_input_income` and `engine_input_deductions` are the
    pydantic Income / Deductions objects the engine consumes. The other
    fields carry the audit-trail context (per-entry breakdown, evidence
    status, agreement linkage, §195 disclosures) used by the UI + audit
    pack PDF.
    """

    user_id: int
    tax_year_s4_format: str         # "2025-26"
    tax_year_s5_format: str         # "2025/2026"

    # Profile snapshot (S3) -------------------------------------------------
    nic: Optional[str] = None
    tin: Optional[str] = None
    full_name: Optional[str] = None
    senior_citizen: bool = False
    profile_complete: bool = False

    # Income summary (S4) ---------------------------------------------------
    income_by_category_lkr: dict[str, Decimal] = field(default_factory=dict)
    income_by_currency: dict[str, Decimal] = field(default_factory=dict)
    income_total_lkr: Decimal = Decimal("0")
    income_entry_count: int = 0
    income_unconverted_currencies: list[str] = field(default_factory=list)
    income_fx_warnings: list[str] = field(default_factory=list)

    # Deductions (S5) -------------------------------------------------------
    # Each entry: dict with category_id, name, ira_section, estimated_lkr,
    #             actual_lkr, used_lkr, evidence_status, cap_note (or None).
    deductions_itemised: list[dict[str, Any]] = field(default_factory=list)
    deductions_total_lkr: Decimal = Decimal("0")
    deductions_with_evidence_count: int = 0
    deductions_pending_evidence_count: int = 0

    # Service providers (S6) ------------------------------------------------
    # Each entry: id, name, service_type, monthly_rate_lkr, hourly_rate_lkr,
    #             requires_disclosure, has_agreement, agreement_status,
    #             agreement_amount_lkr, disclosure_applied_in_agreement.
    service_providers: list[dict[str, Any]] = field(default_factory=list)
    sp_total_fees_lkr: Decimal = Decimal("0")
    sp_disclosure_required_count: int = 0
    sp_disclosure_applied_count: int = 0

    # Property + rental (S7 + S9) ------------------------------------------
    # rentals: list of dicts: property summary, landlord summary, monthly
    #          rent, home-office portion, agreement status, §195 status.
    rentals: list[dict[str, Any]] = field(default_factory=list)
    rental_total_lkr: Decimal = Decimal("0")
    home_office_portion_total_lkr: Decimal = Decimal("0")
    rental_disclosure_required_count: int = 0
    rental_disclosure_applied_count: int = 0
    rental_stamp_duty_outstanding_count: int = 0

    # RSU (B11, MS2 E.1) ---------------------------------------------------
    # Read from canonical Income(source_type='rsu') + AssetDisposal(asset_type='rsu').
    # rsu_vesting_lines: list of dicts {ticker, vesting_date, amount_lkr,
    #                                    source_country, currency, fx_rate}.
    # rsu_cgt_lines:     list of dicts {ticker, disposal_date, gain_lkr,
    #                                    acq_amount_lkr, disp_amount_lkr,
    #                                    source_country, asset_identifier}.
    rsu_vesting_lines: list[dict[str, Any]] = field(default_factory=list)
    rsu_vesting_total_lkr: Decimal = Decimal("0")
    rsu_cgt_lines: list[dict[str, Any]] = field(default_factory=list)
    rsu_cgt_total_lkr: Decimal = Decimal("0")
    rsu_dtaa_deferred: bool = False

    # Engine-shaped inputs (pydantic) --------------------------------------
    # These are computed at the end of assemble_tax_inputs; the compute layer
    # passes them directly to compute_tax_25_26.
    engine_income_kwargs: dict[str, Decimal] = field(default_factory=dict)
    engine_deductions_kwargs: dict[str, Decimal] = field(default_factory=dict)

    # Diagnostic counters --------------------------------------------------
    # Mismatch flag: customer-claimed SP fees > agreement-stated fees.
    # Each entry: {sp_id, sp_name, claimed_lkr, agreement_lkr, diff_lkr}.
    sp_agreement_mismatches: list[dict[str, Any]] = field(default_factory=list)

    # Missing-§195-disclosure flags (red blockers candidates) --------------
    # Each entry: {kind: 'service_provider'|'rental', id, name, reason}
    missing_disclosures: list[dict[str, Any]] = field(default_factory=list)

    # Bookkeeping
    sources_loaded: list[str] = field(default_factory=list)
    sources_missing: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_decimal(v) -> Decimal:
    """Robust conversion to Decimal -- accepts None, str, int, float, Decimal."""
    if v is None or v == "":
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:  # pragma: no cover
        return Decimal("0")


# ---------------------------------------------------------------------------
# Section loaders -- each is defensive: missing module / DB / row => skip.
# ---------------------------------------------------------------------------


def _load_profile(inputs: TaxInputs) -> None:
    """Section: S3 profile (NIC, TIN, full name, senior-citizen flag)."""
    try:
        from fiesta.profile.models import FiestaProfile  # type: ignore
    except Exception:
        inputs.sources_missing.append("fiesta.profile")
        return
    try:
        row = FiestaProfile.query.filter_by(user_id=inputs.user_id).first()
    except Exception as exc:
        logger.warning("profile lookup failed: %s", exc)
        inputs.sources_missing.append("fiesta.profile")
        return
    if row is None:
        inputs.sources_missing.append("fiesta.profile")
        return
    inputs.sources_loaded.append("fiesta.profile")
    inputs.nic = row.nic
    inputs.tin = row.tin
    # S3 v1 doesn't carry DOB; senior_citizen stays False.
    inputs.senior_citizen = False
    # S3 doesn't include "complete" flag; if NIC + city + employment_type
    # exist, treat as complete (matches the progressive-disclosure logic
    # used downstream).
    inputs.profile_complete = bool(
        row.nic and row.city and getattr(row, "employment_type", None)
    )
    # Pull display name from the linked User row.
    try:
        from models import User  # type: ignore
        u = User.query.get(inputs.user_id)
        if u and u.name:
            inputs.full_name = u.name
    except Exception:
        pass


def _load_earnings(inputs: TaxInputs) -> None:
    """Section: S4 earnings via fiesta.earnings.to_tax.income_summary_for_tax_year."""
    try:
        from fiesta.earnings.to_tax import income_summary_for_tax_year
    except Exception:
        inputs.sources_missing.append("fiesta.earnings")
        return
    try:
        summary = income_summary_for_tax_year(
            user_id=inputs.user_id,
            tax_year=inputs.tax_year_s4_format,
        )
    except Exception as exc:
        logger.warning("earnings.to_tax failed: %s", exc)
        inputs.sources_missing.append("fiesta.earnings")
        return
    inputs.sources_loaded.append("fiesta.earnings")
    inputs.income_by_category_lkr = {
        k: _to_decimal(v) for k, v in (summary.get("by_category_lkr") or {}).items()
    }
    inputs.income_by_currency = {
        k: _to_decimal(v) for k, v in (summary.get("by_currency") or {}).items()
    }
    inputs.income_total_lkr = _to_decimal(summary.get("total_lkr"))
    inputs.income_entry_count = int(summary.get("entry_count") or 0)
    inputs.income_unconverted_currencies = list(summary.get("unconverted_currencies") or [])
    inputs.income_fx_warnings = list(summary.get("fx_warnings") or [])


def _load_deductions(inputs: TaxInputs) -> None:
    """Section: S5 deduction claims + catalog cross-link."""
    try:
        from fiesta.deductions.models import DeductionClaim
        from fiesta.deductions.catalog_loader import get_category, get_caps
    except Exception:
        inputs.sources_missing.append("fiesta.deductions")
        return
    try:
        rows = (
            DeductionClaim.query
            .filter_by(
                user_id=inputs.user_id,
                tax_year=inputs.tax_year_s5_format,
                claimed=True,
            )
            .all()
        )
    except Exception as exc:
        logger.warning("deductions query failed: %s", exc)
        inputs.sources_missing.append("fiesta.deductions")
        return
    inputs.sources_loaded.append("fiesta.deductions")
    caps = {}
    try:
        caps = get_caps() or {}
    except Exception:
        caps = {}

    total = Decimal("0")
    with_evi = 0
    pending = 0
    items: list[dict[str, Any]] = []
    for r in rows:
        cat = get_category(r.category_id) or {}
        # Prefer actual_lkr (evidence-backed) over estimated_lkr.
        used = r.actual_lkr if r.actual_lkr is not None else r.estimated_lkr
        used_dec = _to_decimal(used)

        # Apply per-category cap from catalog (e.g. solar 600K).
        cap_note: Optional[str] = None
        cap_def = caps.get(r.category_id) if isinstance(caps, dict) else None
        if cap_def and used_dec > 0:
            cap_type = (cap_def or {}).get("type")
            if cap_type == "absolute":
                cap_amount = _to_decimal(cap_def.get("amount_lkr"))
                if cap_amount > 0 and used_dec > cap_amount:
                    cap_note = (
                        f"Capped at Rs {cap_amount:,} per "
                        f"{cap_def.get('rule', 'gazette rule')}."
                    )
                    used_dec = cap_amount

        items.append({
            "category_id": r.category_id,
            "name": cat.get("name") or r.category_id,
            "ira_section": cat.get("ira_section") or "§6",
            "ira_section_long": cat.get("ira_section_long") or "",
            "estimated_lkr": _to_decimal(r.estimated_lkr),
            "actual_lkr": _to_decimal(r.actual_lkr),
            "used_lkr": used_dec,
            "evidence_status": r.evidence_status,
            "notes": r.notes,
            "cap_note": cap_note,
            # The S5 catalog tags each cat to a tax-engine "bucket" we map below.
            "engine_bucket": _deduction_engine_bucket(r.category_id),
        })
        if r.evidence_status in ("collected", "submitted"):
            with_evi += 1
        else:
            pending += 1
        total += used_dec

    inputs.deductions_itemised = items
    inputs.deductions_total_lkr = total
    inputs.deductions_with_evidence_count = with_evi
    inputs.deductions_pending_evidence_count = pending


def _deduction_engine_bucket(category_id: str) -> str:
    """Map S5 catalog category -> tax engine Deductions field.

    The Phase-1 engine carries only 3 deduction lines:
        solar_investment_lkr   -- capped 600K
        rent_relief_lkr        -- rent (home_office_rental in catalog)
        expenditure_relief_lkr -- everything else

    This map keeps S12 in sync with that contract. Phase 3 will expand the
    engine into per-category buckets; until then, "expenditure_relief"
    aggregates the long tail.
    """
    if category_id == "solar":
        return "solar_investment_lkr"
    if category_id == "home_office_rental":
        return "rent_relief_lkr"
    return "expenditure_relief_lkr"


def _load_service_providers(inputs: TaxInputs) -> None:
    """Section: S6 service providers + §195 cached detection + S8 agreement linkage."""
    try:
        from fiesta.service_providers.models import (
            ServiceProvider, ServiceProviderRelationship,
        )
    except Exception:
        inputs.sources_missing.append("fiesta.service_providers")
        return
    try:
        sps = (
            ServiceProvider.query
            .filter_by(user_id=inputs.user_id, archived=False)
            .all()
        )
    except Exception as exc:
        logger.warning("SP query failed: %s", exc)
        inputs.sources_missing.append("fiesta.service_providers")
        return
    inputs.sources_loaded.append("fiesta.service_providers")

    total = Decimal("0")
    disc_required = 0
    disc_applied = 0
    items: list[dict[str, Any]] = []
    for sp in sps:
        # Monthly fee or notional 160h-month from hourly.
        monthly = sp.monthly_rate if sp.monthly_rate is not None else (
            (sp.hourly_rate * Decimal("160")) if sp.hourly_rate is not None
            else Decimal("0")
        )
        # 12 months total claim hypothesis (real apportionment is the
        # customer's S5 actual_lkr; this is just an SP-section roll-up).
        annual = monthly * Decimal("12") if monthly else Decimal("0")
        total += annual

        # §195: pull cached relationship if present.
        rel = None
        try:
            rel = ServiceProviderRelationship.query.filter_by(sp_id=sp.id).first()
        except Exception:
            pass
        requires_disclosure = bool(
            sp.requires_disclosure or (rel and rel.should_default_on_disclosure)
        )

        # Cross-link to S8: any ServiceAgreement for this sp?
        agreement_info = _latest_service_agreement_for(inputs.user_id, sp.id)

        if requires_disclosure:
            disc_required += 1
            if agreement_info.get("disclosure_applied"):
                disc_applied += 1
            else:
                # Missing-disclosure flag.
                if agreement_info.get("has_agreement"):
                    inputs.missing_disclosures.append({
                        "kind": "service_provider",
                        "id": sp.id,
                        "name": sp.name,
                        "reason": (
                            "Service Agreement generated without §195 "
                            "disclosure clause -- detector flagged related-party."
                        ),
                    })

        # Mismatch check: if customer claimed SP fees in S5 (rough proxy: any
        # subcontractor / professional fees deduction is non-zero AND we
        # have an agreement with monthly_fee), flag if agreement amount <
        # the customer's claim.
        if agreement_info.get("monthly_fee_lkr") and monthly > 0:
            agreement_monthly = _to_decimal(agreement_info["monthly_fee_lkr"])
            if monthly > agreement_monthly * Decimal("1.10"):  # 10% tolerance
                inputs.sp_agreement_mismatches.append({
                    "sp_id": sp.id,
                    "sp_name": sp.name,
                    "claimed_monthly_lkr": monthly,
                    "agreement_monthly_lkr": agreement_monthly,
                    "diff_lkr": monthly - agreement_monthly,
                })

        items.append({
            "id": sp.id,
            "name": sp.name,
            "service_type": sp.service_type,
            "monthly_rate_lkr": monthly,
            "annual_lkr": annual,
            "stated_relationship": sp.stated_relationship_to_customer,
            "requires_disclosure": requires_disclosure,
            "has_agreement": agreement_info.get("has_agreement", False),
            "agreement_status": agreement_info.get("status", "none"),
            "agreement_reference_id": agreement_info.get("reference_id"),
            "agreement_monthly_fee_lkr": _to_decimal(
                agreement_info.get("monthly_fee_lkr")
            ),
            "disclosure_applied_in_agreement": agreement_info.get(
                "disclosure_applied", False
            ),
            "rel_confidence": rel.confidence if rel else 0.0,
        })

    inputs.service_providers = items
    inputs.sp_total_fees_lkr = total
    inputs.sp_disclosure_required_count = disc_required
    inputs.sp_disclosure_applied_count = disc_applied


def _latest_service_agreement_for(user_id: int, sp_id: int) -> dict[str, Any]:
    """Latest non-draft ServiceAgreement for (user, sp). Returns audit fields."""
    try:
        from fiesta.agreements.models import ServiceAgreement
    except Exception:
        return {"has_agreement": False}
    try:
        # SP id is stored as string on the S8 model -- coerce.
        row = (
            ServiceAgreement.query
            .filter_by(user_id=user_id, service_provider_id=str(sp_id))
            .filter(ServiceAgreement.is_draft_preview.is_(False))
            .order_by(ServiceAgreement.generated_at.desc())
            .first()
        )
    except Exception:
        return {"has_agreement": False}
    if row is None:
        return {"has_agreement": False}

    # Status interpretation: both signed -> 'signed'; one side -> 'partial';
    # neither -> 'generated_unsigned'.
    cs = (row.customer_signature_status or "unsigned").lower()
    ss = (row.sp_signature_status or "unsigned").lower()
    if cs == "signed" and ss == "signed":
        status = "signed"
    elif cs == "signed" or ss == "signed":
        status = "partial"
    else:
        status = "generated_unsigned"

    return {
        "has_agreement": True,
        "status": status,
        "reference_id": row.reference_id,
        "monthly_fee_lkr": row.monthly_fee_lkr,
        "disclosure_applied": bool(row.sec195_disclosure_applied),
        "default_was_on": bool(row.sec195_default_was_on),
    }


def _load_property_and_rentals(inputs: TaxInputs) -> None:
    """Section: S7 property + S9 rental agreements."""
    try:
        from fiesta.property.models import (
            Property, Landlord, RentalAgreement, LandlordRelationshipDetection,
        )
    except Exception:
        inputs.sources_missing.append("fiesta.property")
        return
    try:
        rentals = (
            RentalAgreement.query
            .filter_by(
                user_id=inputs.user_id,
                tax_year=inputs.tax_year_s5_format,  # S7 uses "YYYY/YYYY"
            )
            .all()
        )
    except Exception as exc:
        logger.warning("rental query failed: %s", exc)
        inputs.sources_missing.append("fiesta.property")
        return
    inputs.sources_loaded.append("fiesta.property")

    total = Decimal("0")
    home_office = Decimal("0")
    disc_required = 0
    disc_applied = 0
    stamp_outstanding = 0
    items: list[dict[str, Any]] = []
    for ra in rentals:
        prop = None
        landlord = None
        try:
            prop = Property.query.get(ra.property_id)
            landlord = Landlord.query.get(ra.landlord_id)
        except Exception:
            pass

        monthly = ra.monthly_rent_lkr or Decimal("0")
        ho_portion = ra.home_office_portion_lkr or Decimal("0")
        # Annualise. Customer's S5 claim is the authoritative number; this is
        # the audit-trail figure that the rental-agreement sets the ceiling on.
        annual_rent = monthly * Decimal("12")
        annual_ho = ho_portion * Decimal("12")
        total += annual_rent
        home_office += annual_ho

        # §195: landlord relationship detection.
        rel = None
        if landlord:
            try:
                rel = (
                    LandlordRelationshipDetection.query
                    .filter_by(landlord_id=landlord.id)
                    .order_by(LandlordRelationshipDetection.detected_at.desc())
                    .first()
                )
            except Exception:
                pass

        landlord_self = bool(
            landlord and landlord.relationship_to_customer == "self-owns"
        )
        requires_disclosure = bool(
            landlord_self
            or (rel and rel.should_default_on_disclosure)
        )

        # Look up most recent S9-generated agreement for this rental.
        s9_info = _latest_rental_agreement_for(
            inputs.user_id, ra.property_id, ra.landlord_id,
        )

        if requires_disclosure:
            disc_required += 1
            if s9_info.get("disclosure_applied"):
                disc_applied += 1
            else:
                if s9_info.get("has_agreement"):
                    inputs.missing_disclosures.append({
                        "kind": "rental",
                        "id": ra.id,
                        "name": (landlord.full_name if landlord else "Landlord"),
                        "reason": (
                            "Rental Agreement generated without §195 "
                            "disclosure clause -- landlord relationship flagged."
                        ),
                    })

        # Stamp duty outstanding?
        if s9_info.get("stamp_duty_chargeable") and not s9_info.get(
            "stamp_duty_paid", False
        ):
            stamp_outstanding += 1

        items.append({
            "rental_id": ra.id,
            "property_address": (
                f"{prop.address_line1}, {prop.city}" if prop else "—"
            ),
            "property_type": prop.property_type if prop else "—",
            "customer_status": prop.customer_status if prop else "—",
            "landlord_name": landlord.full_name if landlord else "—",
            "landlord_relationship": (
                landlord.relationship_to_customer if landlord else "—"
            ),
            "monthly_rent_lkr": monthly,
            "annual_rent_lkr": annual_rent,
            "home_office_portion_monthly_lkr": ho_portion,
            "home_office_portion_annual_lkr": annual_ho,
            "home_office_percentage": prop.home_office_percentage if prop else None,
            "term_start": ra.start_date.isoformat() if ra.start_date else None,
            "term_end": ra.end_date.isoformat() if ra.end_date else None,
            "document_status": ra.document_status,
            "requires_disclosure": requires_disclosure,
            "disclosure_applied_in_agreement": s9_info.get(
                "disclosure_applied", False
            ),
            "agreement_reference_id": s9_info.get("reference_id"),
            "stamp_duty_chargeable": s9_info.get("stamp_duty_chargeable", False),
            "stamp_duty_lkr": _to_decimal(s9_info.get("stamp_duty_lkr")),
            "rel_confidence": rel.confidence if rel else 0.0,
        })

    inputs.rentals = items
    inputs.rental_total_lkr = total
    inputs.home_office_portion_total_lkr = home_office
    inputs.rental_disclosure_required_count = disc_required
    inputs.rental_disclosure_applied_count = disc_applied
    inputs.rental_stamp_duty_outstanding_count = stamp_outstanding


def _latest_rental_agreement_for(
    user_id: int, property_id: int, landlord_id: int,
) -> dict[str, Any]:
    """Latest RentalAgreementGenerated audit row."""
    try:
        from fiesta.agreements.models import RentalAgreementGenerated  # type: ignore
    except Exception:
        return {"has_agreement": False}
    try:
        row = (
            RentalAgreementGenerated.query
            .filter_by(
                user_id=user_id,
                property_id=property_id,
                landlord_id=landlord_id,
            )
            .order_by(RentalAgreementGenerated.generated_at.desc())
            .first()
        )
    except Exception:
        return {"has_agreement": False}
    if row is None:
        return {"has_agreement": False}
    return {
        "has_agreement": True,
        "reference_id": row.reference_id,
        "disclosure_applied": bool(row.s195_disclosure_applied),
        "default_on_recommended": bool(row.s195_default_on_recommended),
        "stamp_duty_chargeable": bool(row.stamp_duty_chargeable),
        "stamp_duty_lkr": row.stamp_duty_lkr,
        "stamp_duty_paid": False,  # tracker out-of-scope here; default false.
    }


# ---------------------------------------------------------------------------
# RSU loader (B11, MS2 E.1)
# ---------------------------------------------------------------------------


def _load_rsu(inputs: TaxInputs) -> None:
    """Section B11: RSU vesting (Income.source_type='rsu') + RSU CGT
    (AssetDisposal.asset_type='rsu') for the tax year.

    Reads from the canonical schema (Design Lock 2 §4 + §5). Tax-year
    matching uses the canonical YYYY/YY shape stored on Income / AssetDisposal
    rows; the aggregator's tax_year_s4_format is YYYY-YY so we convert.
    """
    try:
        from fiesta.tax.models import AssetDisposal, Income
    except Exception:
        inputs.sources_missing.append("fiesta.tax.rsu")
        return

    ty_canonical = inputs.tax_year_s4_format.replace("-", "/")

    try:
        vesting_rows = (
            Income.query
            .filter_by(
                user_id=inputs.user_id,
                source_type="rsu",
                tax_year=ty_canonical,
            )
            .all()
        )
    except Exception as exc:
        logger.warning("RSU vesting query failed: %s", exc)
        inputs.sources_missing.append("fiesta.tax.rsu")
        return

    try:
        cgt_rows = (
            AssetDisposal.query
            .filter_by(
                user_id=inputs.user_id,
                asset_type="rsu",
                tax_year=ty_canonical,
            )
            .all()
        )
    except Exception as exc:
        logger.warning("RSU CGT query failed: %s", exc)
        cgt_rows = []

    inputs.sources_loaded.append("fiesta.tax.rsu")

    v_total = Decimal("0")
    vesting_lines: list[dict[str, Any]] = []
    for row in vesting_rows:
        amt = _to_decimal(row.amount_lkr)
        v_total += amt
        # Pull ticker from the linked RSUVestingEvent (denormalised on row).
        ticker = None
        try:
            from fiesta.tax.models import RSUVestingEvent as _RSU
            if row.rsu_vesting_id:
                ev = _RSU.query.get(int(row.rsu_vesting_id))
                if ev:
                    ticker = ev.ticker
        except Exception:
            pass
        vesting_lines.append({
            "ticker": ticker,
            "vesting_date": row.fx_date.isoformat() if row.fx_date else None,
            "amount_lkr": amt,
            "currency": row.currency,
            "fx_rate": _to_decimal(row.fx_rate),
            "source_country": row.source_country,
        })

    c_total = Decimal("0")
    cgt_lines: list[dict[str, Any]] = []
    for row in cgt_rows:
        gain = _to_decimal(row.gain_lkr)
        c_total += gain
        cgt_lines.append({
            "asset_identifier": row.asset_identifier,
            "disposal_date": row.disposal_date.isoformat() if row.disposal_date else None,
            "acquisition_date": row.acquisition_date.isoformat() if row.acquisition_date else None,
            "acq_amount_lkr": _to_decimal(row.acq_amount_lkr),
            "disp_amount_lkr": _to_decimal(row.disp_amount_lkr),
            "gain_lkr": gain,
            "source_country": row.source_country,
        })

    inputs.rsu_vesting_lines = vesting_lines
    inputs.rsu_vesting_total_lkr = v_total.quantize(Decimal("0.01"))
    inputs.rsu_cgt_lines = cgt_lines
    inputs.rsu_cgt_total_lkr = c_total.quantize(Decimal("0.01"))
    inputs.rsu_dtaa_deferred = any(
        (r.get("source_country") for r in vesting_lines + cgt_lines)
    )


# ---------------------------------------------------------------------------
# Engine-shape composition
# ---------------------------------------------------------------------------


def _compose_engine_inputs(inputs: TaxInputs) -> None:
    """Build the kwargs that the tax engine's Income + Deductions take.

    Income mapping (S4 IncomeCategory -> engine Income fields):
        salary              -> employment_lkr
        contractor_fee      -> business_lkr
        foreign_remittance  -> foreign_lkr
        interest            -> fd_interest_lkr  (no separate bond/T-bill yet)
        dividend            -> investment_lkr
        rental              -> rental_lkr
        (anything else)     -> other_lkr

    Deductions mapping (engine's only 3 buckets):
        solar -> solar_investment_lkr
        home_office_rental -> rent_relief_lkr
        everything else -> expenditure_relief_lkr
    """
    cat = inputs.income_by_category_lkr

    income_kwargs = {
        "employment_lkr": cat.get("salary", Decimal("0")),
        "business_lkr": cat.get("contractor_fee", Decimal("0")),
        "foreign_lkr": cat.get("foreign_remittance", Decimal("0")),
        "rental_lkr": cat.get("rental", Decimal("0")),
        "fd_interest_lkr": cat.get("interest", Decimal("0")),
        "investment_lkr": cat.get("dividend", Decimal("0")),
        "other_lkr": Decimal("0"),
    }

    # B11 (MS2 E.1): RSU vesting income (IRA §5(2)(j)) is employment income.
    # Add it to employment_lkr so the bracket schedule sees the right total.
    # CGT (Section 7(2)(b) / Chapter IV) is a SEPARATE bucket that the v1
    # engine doesn't have a slot for — surface it via the rsu_cgt_total_lkr
    # field on inputs, rendered as a standalone tax-bill line.
    if inputs.rsu_vesting_total_lkr and inputs.rsu_vesting_total_lkr > 0:
        income_kwargs["employment_lkr"] = (
            income_kwargs["employment_lkr"] + inputs.rsu_vesting_total_lkr
        )
        # Also surface as a synthetic category for the bill UI breakdown.
        inputs.income_by_category_lkr = dict(inputs.income_by_category_lkr or {})
        inputs.income_by_category_lkr["rsu_vesting"] = (
            inputs.income_by_category_lkr.get("rsu_vesting", Decimal("0"))
            + inputs.rsu_vesting_total_lkr
        )

    # Roll the "everything else" categories into other_lkr.
    known_keys = {
        "salary", "contractor_fee", "foreign_remittance",
        "rental", "interest", "dividend",
    }
    for k, v in cat.items():
        if k not in known_keys:
            income_kwargs["other_lkr"] += _to_decimal(v)

    # Deductions: sum used_lkr per bucket.
    deduction_buckets: dict[str, Decimal] = {
        "solar_investment_lkr": Decimal("0"),
        "rent_relief_lkr": Decimal("0"),
        "expenditure_relief_lkr": Decimal("0"),
    }
    for item in inputs.deductions_itemised:
        bucket = item.get("engine_bucket") or "expenditure_relief_lkr"
        deduction_buckets[bucket] += item["used_lkr"]

    inputs.engine_income_kwargs = income_kwargs
    inputs.engine_deductions_kwargs = deduction_buckets


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# x9 tier-b: consolidated view-backed loader.
# ---------------------------------------------------------------------------

# Cap rules cache -- get_caps() is a YAML read, cheap but worth memoising
# since this hot path is in the request loop.
_DEDUCTION_CAPS_CACHE: Optional[dict] = None


def _get_deduction_caps() -> dict:
    """Memoised wrapper around fiesta.deductions.catalog_loader.get_caps."""
    global _DEDUCTION_CAPS_CACHE
    if _DEDUCTION_CAPS_CACHE is not None:
        return _DEDUCTION_CAPS_CACHE
    try:
        from fiesta.deductions.catalog_loader import get_caps
        _DEDUCTION_CAPS_CACHE = get_caps() or {}
    except Exception:
        _DEDUCTION_CAPS_CACHE = {}
    return _DEDUCTION_CAPS_CACHE


def _cents_to_decimal(c: Any) -> Decimal:
    """Cents-stored-as-int -> Decimal LKR (2dp)."""
    if c is None:
        return Decimal("0")
    try:
        return (Decimal(str(c)) / Decimal("100")).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0")


def _load_via_view(inputs: TaxInputs) -> bool:
    """Single-query path: read vw_tax_bill_context, populate profile + dedns
    + SPs + rentals from the JSONB-aggregated row.

    Returns True on success, False if the view is not available (caller falls
    back to the legacy per-table loaders).
    """
    try:
        from app import db
        from sqlalchemy import text
    except Exception:
        return False

    try:
        row = db.session.execute(
            text("""
                SELECT
                    nic, tin, city, employment_type, user_name,
                    deductions, service_providers, rentals
                FROM vw_tax_bill_context
                WHERE user_id = :uid
                  AND tax_year_s4 = :ty
                LIMIT 1
            """),
            {"uid": int(inputs.user_id), "ty": inputs.tax_year_s4_format},
        ).mappings().first()
    except Exception as exc:
        # View may not exist (migration not applied) or DB unreachable.
        # Caller decides whether to fall back. Log at debug because the
        # fallback path is intentional and well-tested.
        logger.debug("vw_tax_bill_context unavailable, falling back: %s", exc)
        return False

    # No row means "user has nothing yet"; we still want to mark sources_loaded
    # so the legacy loaders are NOT invoked as a fallback that would re-query.
    if row is None:
        inputs.sources_loaded.append("vw_tax_bill_context")
        # The earnings path still runs below in assemble_tax_inputs.
        return True

    inputs.sources_loaded.append("vw_tax_bill_context")

    # ---- Profile + User scalars ----
    inputs.nic = row.get("nic")
    inputs.tin = row.get("tin")
    inputs.full_name = row.get("user_name")
    inputs.senior_citizen = False  # S3 v1 doesn't carry DOB.
    # "Complete" = NIC + city + employment_type all present (matches
    # _load_profile semantics exactly).
    inputs.profile_complete = bool(
        row.get("nic") and row.get("city") and row.get("employment_type")
    )

    # ---- Deductions ----
    dedn_rows = row.get("deductions") or []
    caps = _get_deduction_caps()
    total = Decimal("0")
    with_evi = 0
    pending = 0
    items: list[dict[str, Any]] = []
    for d in dedn_rows:
        category_id = d.get("category_id")
        try:
            from fiesta.deductions.catalog_loader import get_category
            cat = get_category(category_id) or {}
        except Exception:
            cat = {}

        estimated = _cents_to_decimal(d.get("estimated_lkr_cents"))
        actual = _cents_to_decimal(d.get("actual_lkr_cents"))
        # Prefer actual_lkr (evidence-backed) over estimated_lkr -- match
        # _load_deductions semantics (None means "fall back").
        used = actual if d.get("actual_lkr_cents") is not None else estimated

        # Apply per-category cap (e.g. solar 600K).
        cap_note: Optional[str] = None
        cap_def = caps.get(category_id) if isinstance(caps, dict) else None
        if cap_def and used > 0:
            cap_type = (cap_def or {}).get("type")
            if cap_type == "absolute":
                cap_amount = _to_decimal(cap_def.get("amount_lkr"))
                if cap_amount > 0 and used > cap_amount:
                    cap_note = (
                        f"Capped at Rs {cap_amount:,} per "
                        f"{cap_def.get('rule', 'gazette rule')}."
                    )
                    used = cap_amount

        items.append({
            "category_id": category_id,
            "name": cat.get("name") or category_id,
            "ira_section": cat.get("ira_section") or "§6",
            "ira_section_long": cat.get("ira_section_long") or "",
            "estimated_lkr": estimated,
            "actual_lkr": actual,
            "used_lkr": used,
            "evidence_status": d.get("evidence_status"),
            "notes": d.get("notes"),
            "cap_note": cap_note,
            "engine_bucket": _deduction_engine_bucket(category_id),
        })
        if d.get("evidence_status") in ("collected", "submitted"):
            with_evi += 1
        else:
            pending += 1
        total += used

    inputs.deductions_itemised = items
    inputs.deductions_total_lkr = total
    inputs.deductions_with_evidence_count = with_evi
    inputs.deductions_pending_evidence_count = pending

    # ---- Service providers + agreements ----
    sp_rows = row.get("service_providers") or []
    sp_total = Decimal("0")
    sp_disc_required = 0
    sp_disc_applied = 0
    sp_items: list[dict[str, Any]] = []
    for sp in sp_rows:
        monthly_rate = _cents_to_decimal(sp.get("monthly_rate_cents"))
        hourly_rate = _cents_to_decimal(sp.get("hourly_rate_cents"))
        # Monthly fee or notional 160h-month from hourly -- matches legacy.
        if sp.get("monthly_rate_cents") is not None:
            monthly = monthly_rate
        elif sp.get("hourly_rate_cents") is not None:
            monthly = hourly_rate * Decimal("160")
        else:
            monthly = Decimal("0")
        annual = monthly * Decimal("12") if monthly else Decimal("0")
        sp_total += annual

        requires_disclosure = bool(
            sp.get("requires_disclosure")
            or sp.get("rel_should_default_on_disclosure")
        )

        has_agreement = bool(sp.get("agreement_has"))
        # Status derivation -- matches _latest_service_agreement_for exactly.
        if has_agreement:
            cs = (sp.get("agreement_customer_sig") or "unsigned").lower()
            ss = (sp.get("agreement_sp_sig") or "unsigned").lower()
            if cs == "signed" and ss == "signed":
                status = "signed"
            elif cs == "signed" or ss == "signed":
                status = "partial"
            else:
                status = "generated_unsigned"
        else:
            status = "none"

        agreement_monthly_fee = _to_decimal(sp.get("agreement_monthly_fee_lkr"))
        disclosure_applied_in_agreement = bool(sp.get("agreement_sec195_applied"))

        if requires_disclosure:
            sp_disc_required += 1
            if disclosure_applied_in_agreement:
                sp_disc_applied += 1
            else:
                if has_agreement:
                    inputs.missing_disclosures.append({
                        "kind": "service_provider",
                        "id": sp.get("id"),
                        "name": sp.get("name"),
                        "reason": (
                            "Service Agreement generated without §195 "
                            "disclosure clause -- detector flagged "
                            "related-party."
                        ),
                    })

        # Mismatch check: claimed monthly > agreement monthly * 1.10.
        if agreement_monthly_fee > 0 and monthly > 0:
            if monthly > agreement_monthly_fee * Decimal("1.10"):
                inputs.sp_agreement_mismatches.append({
                    "sp_id": sp.get("id"),
                    "sp_name": sp.get("name"),
                    "claimed_monthly_lkr": monthly,
                    "agreement_monthly_lkr": agreement_monthly_fee,
                    "diff_lkr": monthly - agreement_monthly_fee,
                })

        sp_items.append({
            "id": sp.get("id"),
            "name": sp.get("name"),
            "service_type": sp.get("service_type"),
            "monthly_rate_lkr": monthly,
            "annual_lkr": annual,
            "stated_relationship": sp.get("stated_relationship"),
            "requires_disclosure": requires_disclosure,
            "has_agreement": has_agreement,
            "agreement_status": status,
            "agreement_reference_id": sp.get("agreement_reference_id"),
            "agreement_monthly_fee_lkr": agreement_monthly_fee,
            "disclosure_applied_in_agreement": disclosure_applied_in_agreement,
            "rel_confidence": float(sp.get("rel_confidence") or 0.0),
        })

    inputs.service_providers = sp_items
    inputs.sp_total_fees_lkr = sp_total
    inputs.sp_disclosure_required_count = sp_disc_required
    inputs.sp_disclosure_applied_count = sp_disc_applied

    # ---- Rentals + property + landlord + S9 agreement ----
    rental_rows = row.get("rentals") or []
    rent_total = Decimal("0")
    ho_total = Decimal("0")
    rent_disc_required = 0
    rent_disc_applied = 0
    stamp_outstanding = 0
    rental_items: list[dict[str, Any]] = []
    for ra in rental_rows:
        monthly = _cents_to_decimal(ra.get("monthly_rent_lkr_cents"))
        ho_portion = _cents_to_decimal(ra.get("home_office_portion_lkr_cents"))
        annual_rent = monthly * Decimal("12")
        annual_ho = ho_portion * Decimal("12")
        rent_total += annual_rent
        ho_total += annual_ho

        landlord_rel = ra.get("landlord_relationship")
        landlord_self = bool(landlord_rel == "self-owns")
        requires_disclosure = bool(
            landlord_self or ra.get("lrd_should_default_on_disclosure")
        )

        has_rag = bool(ra.get("rag_has"))
        disclosure_applied = bool(ra.get("rag_s195_applied"))

        if requires_disclosure:
            rent_disc_required += 1
            if disclosure_applied:
                rent_disc_applied += 1
            else:
                if has_rag:
                    inputs.missing_disclosures.append({
                        "kind": "rental",
                        "id": ra.get("id"),
                        "name": ra.get("landlord_full_name") or "Landlord",
                        "reason": (
                            "Rental Agreement generated without §195 "
                            "disclosure clause -- landlord relationship "
                            "flagged."
                        ),
                    })

        stamp_chargeable = bool(ra.get("rag_stamp_duty_chargeable"))
        # _latest_rental_agreement_for hard-codes stamp_duty_paid=False;
        # mirror that.
        if stamp_chargeable:
            stamp_outstanding += 1

        property_address = ra.get("property_address_line1") or ""
        property_city = ra.get("property_city") or ""
        if property_address or property_city:
            address_str = f"{property_address}, {property_city}".strip(", ")
        else:
            address_str = "—"

        # start_date / end_date come back as date objects from PG; isoformat
        # them to match legacy behaviour (ra.start_date.isoformat()).
        def _maybe_iso(d):
            if d is None:
                return None
            iso = getattr(d, "isoformat", None)
            return iso() if callable(iso) else str(d)

        rental_items.append({
            "rental_id": ra.get("id"),
            "property_address": address_str if address_str.strip(", ") else "—",
            "property_type": ra.get("property_type") or "—",
            "customer_status": ra.get("property_customer_status") or "—",
            "landlord_name": ra.get("landlord_full_name") or "—",
            "landlord_relationship": landlord_rel or "—",
            "monthly_rent_lkr": monthly,
            "annual_rent_lkr": annual_rent,
            "home_office_portion_monthly_lkr": ho_portion,
            "home_office_portion_annual_lkr": annual_ho,
            "home_office_percentage": ra.get("property_home_office_percentage"),
            "term_start": _maybe_iso(ra.get("start_date")),
            "term_end": _maybe_iso(ra.get("end_date")),
            "document_status": ra.get("document_status"),
            "requires_disclosure": requires_disclosure,
            "disclosure_applied_in_agreement": disclosure_applied,
            "agreement_reference_id": ra.get("rag_reference_id"),
            "stamp_duty_chargeable": stamp_chargeable,
            "stamp_duty_lkr": _to_decimal(ra.get("rag_stamp_duty_lkr")),
            "rel_confidence": float(ra.get("lrd_confidence") or 0.0),
        })

    inputs.rentals = rental_items
    inputs.rental_total_lkr = rent_total
    inputs.home_office_portion_total_lkr = ho_total
    inputs.rental_disclosure_required_count = rent_disc_required
    inputs.rental_disclosure_applied_count = rent_disc_applied
    inputs.rental_stamp_duty_outstanding_count = stamp_outstanding

    return True


def assemble_tax_inputs(user_id: int, tax_year: str) -> TaxInputs:
    """Pull from all upstream sources into a TaxInputs snapshot.

    Args:
        user_id:  FIESTA user id (the User.id PK).
        tax_year: accepted forms: "2025-26", "2025/2026", "25/26", "25_26",
                  "Y25_26", and the same set for 24/25.

    Returns:
        TaxInputs with everything the compute + UI layers need. Sections
        sourced from missing modules / empty DB are returned as empty
        lists / zero Decimals; the `sources_missing` field tracks them.

    Query budget (x9 tier-b, 2026-05-24):
        Cache-miss hot path issues 1 query to vw_tax_bill_context, then
        1-2 queries inside income_summary_for_tax_year (IncomeEntry, and
        RemittanceEntry only if at least one row exists per the
        bca49b2 short-circuit). Total: 2-3 queries vs the legacy ~20.

        If the view does not exist (dev/test env without the migration
        applied), falls back to the legacy per-table loaders so behaviour
        stays identical -- never raises.
    """
    inputs = TaxInputs(
        user_id=int(user_id),
        tax_year_s4_format=normalise_tax_year_to_s4_format(tax_year),
        tax_year_s5_format=normalise_tax_year_to_s5_format(tax_year),
    )

    # Try the consolidated view first. If the view is missing or unreadable,
    # _load_via_view returns False and we fall through to the legacy path.
    view_ok = _load_via_view(inputs)

    if not view_ok:
        _load_profile(inputs)
        _load_deductions(inputs)
        _load_service_providers(inputs)
        _load_property_and_rentals(inputs)

    # Earnings is ALWAYS loaded via income_summary_for_tax_year because that
    # helper does an idempotent FX backfill (writes amount_lkr back to
    # IncomeEntry rows on first read) that a pure-read view cannot preserve.
    _load_earnings(inputs)

    # B11 (MS2 E.1): RSU vesting + CGT — canonical schema (Design Lock 2).
    _load_rsu(inputs)

    _compose_engine_inputs(inputs)

    return inputs


__all__ = [
    "TaxInputs",
    "assemble_tax_inputs",
    "normalise_tax_year_to_s4_format",
    "normalise_tax_year_to_s5_format",
    "canonical_tax_year_enum",
]
