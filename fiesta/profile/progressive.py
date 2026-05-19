"""Progressive disclosure logic for the FIESTA S3 profile.

Each downstream screen declares the minimum profile fields it needs. The progressive
helpers compare the customer's profile against the screen's requirement set and
return missing fields — never block until the user actually tries to use that screen.

This file is the SINGLE SOURCE OF TRUTH for "what does screen X need?" — if you add
a new screen requirement, update SCREEN_REQUIREMENTS below and add a test in
tests/profile/test_profile.py.

Screen IDs follow the wave-3 PM doc (THE_PATH_20260520.md). Examples:
- S4  = Foreign earnings entry (needs identity + minimal address)
- S8  = Service agreement generation (needs identity + TIN + employer status)
- S14 = Submit return (needs everything; this is the final gate)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# Canonical field registry
# ---------------------------------------------------------------------------

# All profile fields by tab (drives UI grouping + progress calculation).
ALL_PROFILE_FIELDS: Dict[str, List[str]] = {
    "identity": ["nic", "tin"],
    "address": ["address_line1", "city", "country", "postcode"],
    "residency": ["tax_resident_year", "days_in_sl_current_year"],
    "employer": ["employment_type", "has_foreign_clients"],
    "bank": [
        "bank_account_holder_name",
        "bank_name",
        "bank_branch",
        "bank_account_number",
    ],
}

# Fields that count toward "base profile complete" — the minimum to do anything
# meaningful in FIESTA. (Distinct from per-screen requirements below.)
REQUIRED_BASE_FIELDS: Set[str] = {
    "nic",
    "address_line1",
    "city",
    "country",
}

# Per-screen requirement registry. Each value is the set of profile fields a screen
# REFUSES to render / complete without. Empty set = no profile requirement (e.g. S0
# preview, S1 landing). Order is significant: list base / cheap fields first.
SCREEN_REQUIREMENTS: Dict[str, Set[str]] = {
    # Public / pre-signup
    "S0_preview": set(),
    "S1_landing": set(),
    "S2_signup": set(),
    # Profile screen itself
    "S3_profile": set(),
    # Foreign earnings — needs minimal identity + address (for IRD trail)
    "S4_earnings": {"nic", "address_line1", "city"},
    # Reduce-tax screen — same as S4, no additional fields
    "S5_reduce_tax": {"nic", "address_line1", "city"},
    # Receipt capture
    "S6_receipts": {"nic"},
    # Roster / subcontractor management
    "S7_roster": {"nic"},
    # Service-agreement PDF generation — needs employer status to label parties correctly
    "S8_service_agreement": {
        "nic",
        "tin",
        "address_line1",
        "city",
        "employment_type",
    },
    # Invoice cadence
    "S9_invoice_cadence": {"nic", "tin", "bank_account_holder_name", "bank_name"},
    # Year-end pack
    "S10_year_end_pack": {
        "nic",
        "tin",
        "address_line1",
        "city",
        "tax_resident_year",
    },
    # Lifecycle / X3 rollover
    "S11_lifecycle": {"nic"},
    # IRD walkthrough
    "S12_ird_walkthrough": {"nic", "tin"},
    # Auto-File / automation_runner
    "S13_auto_file": {
        "nic",
        "tin",
        "address_line1",
        "city",
        "tax_resident_year",
        "bank_account_number",
    },
    # FINAL SUBMIT — full set required
    "S14_submit": {
        "nic",
        "tin",
        "address_line1",
        "city",
        "country",
        "tax_resident_year",
        "days_in_sl_current_year",
        "employment_type",
        "bank_account_holder_name",
        "bank_name",
        "bank_account_number",
    },
}


# ---------------------------------------------------------------------------
# SL bank picklist (v1 scope — top 12 SL banks)
# ---------------------------------------------------------------------------

SL_BANKS: List[str] = [
    "Bank of Ceylon (BOC)",
    "Cargills Bank",
    "Commercial Bank of Ceylon",
    "DFCC Bank",
    "Hatton National Bank (HNB)",
    "National Development Bank (NDB)",
    "National Savings Bank (NSB)",
    "Nations Trust Bank (NTB)",
    "Pan Asia Banking Corporation",
    "People's Bank",
    "Sampath Bank",
    "Seylan Bank",
    "Standard Chartered Bank",
    "Union Bank of Colombo",
    "Other",
]


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _get_field_value(profile: Any, field: str) -> Any:
    """Read a field from either a dict-like or attribute-like profile object."""
    if profile is None:
        return None
    if isinstance(profile, dict):
        return profile.get(field)
    return getattr(profile, field, None)


def _is_filled(value: Any) -> bool:
    """A field counts as 'filled' if it's not None / empty string / empty container."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict, set, tuple)):
        return len(value) > 0
    # 0, False, empty Decimal etc are valid filled values for numeric / bool fields
    return True


def required_for_screen(screen_id: str, profile: Any) -> List[str]:
    """Return list of REQUIRED fields still MISSING for the given screen.

    Empty list means the screen can render. Caller decides whether to redirect to
    /fiesta/profile or render a per-field inline prompt.

    Unknown screen_id returns [] (fail-open: don't block users on screens we forgot
    to register). Update SCREEN_REQUIREMENTS to add a new screen.
    """
    requirements = SCREEN_REQUIREMENTS.get(screen_id, set())
    if not requirements:
        return []
    missing = []
    for field in sorted(requirements):
        if not _is_filled(_get_field_value(profile, field)):
            missing.append(field)
    return missing


def progress_pct(profile: Any) -> int:
    """Return a 0-100 completion percentage across ALL profile fields.

    Used by the dashboard widget. Weighted equally; not a tax-readiness score —
    use required_for_screen('S14_submit', profile) for the strict gate.
    """
    all_fields: List[str] = []
    for tab_fields in ALL_PROFILE_FIELDS.values():
        all_fields.extend(tab_fields)
    if not all_fields:
        return 100
    filled = sum(1 for f in all_fields if _is_filled(_get_field_value(profile, f)))
    return int(round(100 * filled / len(all_fields)))


def section_progress(profile: Any) -> Dict[str, Dict[str, int]]:
    """Return per-tab progress: {tab: {filled, total, pct}}."""
    out: Dict[str, Dict[str, int]] = {}
    for tab, fields in ALL_PROFILE_FIELDS.items():
        total = len(fields)
        filled = sum(1 for f in fields if _is_filled(_get_field_value(profile, f)))
        pct = int(round(100 * filled / total)) if total else 100
        out[tab] = {"filled": filled, "total": total, "pct": pct}
    return out


def base_profile_complete(profile: Any) -> bool:
    """True if the BASE required fields are all filled. Cheaper check than S14."""
    for field in REQUIRED_BASE_FIELDS:
        if not _is_filled(_get_field_value(profile, field)):
            return False
    return True


__all__ = [
    "ALL_PROFILE_FIELDS",
    "REQUIRED_BASE_FIELDS",
    "SCREEN_REQUIREMENTS",
    "SL_BANKS",
    "required_for_screen",
    "progress_pct",
    "section_progress",
    "base_profile_complete",
]
