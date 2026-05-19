"""fiesta.property.related_party — §195 detector binding for S7.

Wraps fiesta.compliance.related_party.detect_related_party for the
property/landlord case. The wave-4 detector is service-provider-shaped
(customer + service_provider), so this module adapts a (customer +
landlord + property + rental_agreement) tuple into that shape and adds
two property-specific signals on top:

    DATA_ERROR_SELF_LANDLORD_BUT_TENANT
        Customer marks themselves as "owner-occupant" of the property AND
        provides a landlord. That's an impossible state — flag as data
        error, halt §195 default-on (because the user's earlier input
        contradicts itself; UI should ask them to reconcile).

    SELF_OWNS_FORCES_DEFAULT_ON
        Customer marks landlord.relationship_to_customer="self-owns".
        It's a legitimate arrangement (company pays customer rent for
        room used wholly+exclusively+necessarily as a home office), but
        it IS by definition a related-party transaction so disclosure
        MUST default on. We force should_default_on_disclosure=True
        regardless of the wave-4 confidence score.

The wave-4 detector remains the single source of truth for confidence +
reasoning. We layer property-specific decisions on top.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from fiesta.compliance.related_party import (
        RelatedPartyResult,
        RelatedPartySignal,
        detect_related_party as _wave4_detect,
    )
    _HAS_WAVE4 = True
except Exception as exc:  # pragma: no cover
    logger.error(
        "fiesta.property.related_party: wave-4 detector unavailable — "
        "§195 detection will be disabled. Error: %s",
        exc,
    )
    _HAS_WAVE4 = False
    RelatedPartyResult = None  # type: ignore[assignment]
    RelatedPartySignal = None  # type: ignore[assignment]
    _wave4_detect = None  # type: ignore[assignment]


# Property-specific "soft" signal codes (not in wave-4 enum; carried in
# reasoning text + flags).
DATA_ERROR_SELF_LANDLORD_BUT_TENANT = "data_error_self_landlord_but_tenant"
SELF_OWNS_FORCES_DEFAULT_ON = "self_owns_forces_default_on"


def _normalise_rel(rel: Optional[str]) -> str:
    if not isinstance(rel, str):
        return ""
    return rel.strip().casefold()


# Map of property/Landlord.relationship_to_customer → wave-4 stated_relationship.
# Only listed values get passed through. "arm's-length" stays empty so
# the wave-4 detector doesn't mark it as related.
_REL_TO_WAVE4 = {
    "arm's-length": "",
    "family": "family",
    "friend": "friend",  # wave-4 will not treat as related — correct
    "spouse": "spouse",
    "parent": "parent",
    "sibling": "sibling",
    "child": "child",
    "self-owns": "self",
    "business-associate": "",  # arm's-length unless other signals fire
}


def _build_customer_dict(customer_profile: dict[str, Any]) -> dict[str, Any]:
    """Map FIESTA customer profile → wave-4 customer dict."""
    return {
        "name": customer_profile.get("full_name") or customer_profile.get("name"),
        "nic": customer_profile.get("nic"),
        "address": customer_profile.get("address"),
        "bank_account": customer_profile.get("bank_account_number")
        or customer_profile.get("bank_account"),
    }


def _build_landlord_dict(
    landlord_record: dict[str, Any],
    rental_agreement: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Map FIESTA Landlord row → wave-4 service_provider dict."""
    rel = _normalise_rel(landlord_record.get("relationship_to_customer"))
    stated = _REL_TO_WAVE4.get(rel, rel)  # passthrough unknowns conservatively

    out: dict[str, Any] = {
        "name": landlord_record.get("full_name"),
        "nic": landlord_record.get("nic"),
        "address": landlord_record.get("address"),
        "bank_account": landlord_record.get("bank_account_number"),
        # rental is "service_type=rental"; keep stable for table calibration
        "service_type": "rental",
    }
    if stated:
        # wave-4 reads this from the CUSTOMER dict but accepts it here too;
        # we pass it via customer_profile.stated_relationship_to_service_provider.
        # (Stored separately for clarity — applied at the orchestrator level.)
        pass

    if rental_agreement is not None:
        monthly = rental_agreement.get("monthly_rent_lkr")
        if monthly is not None:
            try:
                out["monthly_fee_lkr"] = float(monthly)
            except (TypeError, ValueError):
                pass

    return out


def detect_landlord_relationship(
    customer_profile: dict[str, Any],
    landlord_record: dict[str, Any],
    property_record: Optional[dict[str, Any]] = None,
    rental_agreement: Optional[dict[str, Any]] = None,
    market_rate_table: Optional[dict[str, dict[str, float]]] = None,
) -> dict[str, Any]:
    """Run §195 detection for a (customer, landlord, property) triple.

    Returns a serialisable dict shaped:
        {
            "signals": list[str],           # wave-4 + property soft codes
            "confidence": float,
            "should_default_on_disclosure": bool,
            "audit_substance_risk": "low|medium|high",
            "reasoning": list[str],
            "data_error": bool,             # halt the form if True
            "soft_signals": list[str],      # property layer (not in wave-4 enum)
        }
    """
    soft: list[str] = []
    reasoning_extra: list[str] = []
    data_error = False

    # Property-layer guard #1 — owner-occupant + landlord = data error
    cust_status = (
        property_record.get("customer_status", "")
        if isinstance(property_record, dict)
        else ""
    )
    rel = _normalise_rel(landlord_record.get("relationship_to_customer"))
    if cust_status == "owner-occupant" and rel != "self-owns" and landlord_record:
        data_error = True
        soft.append(DATA_ERROR_SELF_LANDLORD_BUT_TENANT)
        reasoning_extra.append(
            "DATA ERROR: customer_status='owner-occupant' but a landlord "
            "(other than self) is listed. Owner-occupants cannot also pay "
            "rent to a third party for the same property. Please reconcile "
            "before proceeding."
        )

    # Wave-4 detection (when available)
    if not _HAS_WAVE4:
        return {
            "signals": [],
            "confidence": 0.0,
            "should_default_on_disclosure": rel == "self-owns",
            "audit_substance_risk": "low",
            "reasoning": reasoning_extra or [
                "wave-4 detector unavailable; defaulted to off unless self-owns"
            ],
            "data_error": data_error,
            "soft_signals": soft,
        }

    customer_in = _build_customer_dict(customer_profile)
    landlord_in = _build_landlord_dict(landlord_record, rental_agreement)

    # The wave-4 detector reads stated_relationship_to_service_provider off
    # the customer dict (not the service-provider dict). We translate.
    stated_for_wave4 = _REL_TO_WAVE4.get(rel, rel) if rel else ""
    if stated_for_wave4:
        customer_in["stated_relationship_to_service_provider"] = stated_for_wave4

    payments: list[dict[str, Any]] = []  # S7 v1: no cadence yet

    wave4_result = _wave4_detect(
        customer=customer_in,
        service_provider=landlord_in,
        payments=payments,
        market_rate_table=market_rate_table,
    )

    signals_strs = [s.value for s in wave4_result.signals]
    reasoning = list(wave4_result.reasoning) + reasoning_extra
    confidence = wave4_result.confidence
    should_default_on = wave4_result.should_default_on_disclosure
    audit_risk = wave4_result.audit_substance_risk

    # Property-layer guard #2 — self-owns ALWAYS forces default-on
    if rel == "self-owns":
        soft.append(SELF_OWNS_FORCES_DEFAULT_ON)
        if not should_default_on:
            reasoning.append(
                "landlord.relationship_to_customer='self-owns' — the customer "
                "is renting from themselves. §195 disclosure forced ON "
                "regardless of wave-4 confidence score (legitimate but "
                "definitionally related-party)."
            )
            should_default_on = True
        if audit_risk == "low":
            audit_risk = "medium"

    return {
        "signals": signals_strs,
        "confidence": round(float(confidence), 4),
        "should_default_on_disclosure": bool(should_default_on),
        "audit_substance_risk": audit_risk,
        "reasoning": reasoning,
        "data_error": data_error,
        "soft_signals": soft,
    }


def snapshot_to_persisted_fields(detection: dict[str, Any]) -> dict[str, Any]:
    """Translate detect_landlord_relationship() output → DB column kwargs."""
    return {
        "signals_csv": ",".join(detection.get("signals", []) + detection.get("soft_signals", [])),
        "confidence": float(detection.get("confidence", 0.0)),
        "should_default_on_disclosure": bool(
            detection.get("should_default_on_disclosure", False)
        ),
        "audit_substance_risk": str(detection.get("audit_substance_risk", "low")),
        "reasoning_json": json.dumps(detection.get("reasoning", []), ensure_ascii=False),
    }
