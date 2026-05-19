"""Profile field validators.

Pure functions + pydantic v2 models. No DB / Flask dependencies — safe to unit-test
in isolation. All public errors subclass ValueError so they can be caught uniformly.

Pattern sources:
- SL NIC old format: 9 digits + V or X suffix (e.g. 853310123V). Issued pre-2016.
- SL NIC new format: 12 digits (e.g. 198533101230). Issued from 2016.
  First 4 = year of birth; next 3 = day-of-year (501-866 = female); last 5 = serial.
- IRD TIN: 12-digit numeric, issued by SL Inland Revenue.
- Postcode: 5-digit numeric SL postcode (optional; some areas don't use postcodes).
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Exception types
# ---------------------------------------------------------------------------


class NICValidationError(ValueError):
    """Raised when NIC fails format check."""


class TINValidationError(ValueError):
    """Raised when TIN fails format check."""


class AddressValidationError(ValueError):
    """Raised when address fails minimum requirements."""


# ---------------------------------------------------------------------------
# Regex constants (compiled once)
# ---------------------------------------------------------------------------

# Old NIC: 9 digits + V (citizen) or X (other). Case-insensitive accepted; normalized to uppercase.
_NIC_OLD_RE = re.compile(r"^\d{9}[VX]$", re.IGNORECASE)

# New NIC: exactly 12 digits.
_NIC_NEW_RE = re.compile(r"^\d{12}$")

# TIN: exactly 12 digits.
_TIN_RE = re.compile(r"^\d{12}$")

# SL postcode: 5 digits. Optional in v1 (some rural areas lack postcodes).
_POSTCODE_RE = re.compile(r"^\d{5}$")


# ---------------------------------------------------------------------------
# Pure validators
# ---------------------------------------------------------------------------


def validate_nic(value: str) -> str:
    """Validate SL NIC (old or new format). Returns normalized uppercase value.

    Raises NICValidationError if the input does not match either format.
    """
    if value is None:
        raise NICValidationError("NIC is required")
    v = str(value).strip().upper()
    if not v:
        raise NICValidationError("NIC is required")
    if _NIC_OLD_RE.match(v):
        return v
    if _NIC_NEW_RE.match(v):
        return v
    raise NICValidationError(
        "NIC must be 9 digits + V/X (old format) or 12 digits (new format). "
        f"Got: {value!r}"
    )


def validate_tin(value: Optional[str], *, required: bool = False) -> Optional[str]:
    """Validate IRD TIN (12-digit numeric). Optional by default for v1 — required at S14 Submit.

    Returns the trimmed value, or None if blank and not required.
    Raises TINValidationError if the input is present but malformed.
    """
    if value is None or str(value).strip() == "":
        if required:
            raise TINValidationError("TIN is required to file your return")
        return None
    v = str(value).strip()
    if not _TIN_RE.match(v):
        raise TINValidationError(
            f"TIN must be exactly 12 digits. Got: {value!r}"
        )
    return v


def validate_address(
    line1: Optional[str],
    city: Optional[str],
    country: Optional[str] = "LK",
    postcode: Optional[str] = None,
) -> dict:
    """Validate address minimum for SL filings: line1 + city required.

    Returns normalized dict {line1, city, country, postcode}.
    Raises AddressValidationError on missing required components.
    """
    errors = []
    line1_clean = (line1 or "").strip()
    city_clean = (city or "").strip()
    country_clean = (country or "LK").strip().upper() or "LK"

    if not line1_clean:
        errors.append("Address line 1 is required")
    if not city_clean:
        errors.append("City is required")
    # Country defaults to LK; only validate if explicitly supplied.
    if country_clean and len(country_clean) != 2:
        errors.append("Country must be a 2-letter ISO code (e.g. LK)")

    postcode_clean: Optional[str] = None
    if postcode is not None and str(postcode).strip() != "":
        pc = str(postcode).strip()
        if not _POSTCODE_RE.match(pc):
            errors.append("Postcode must be 5 digits (SL format) or blank")
        else:
            postcode_clean = pc

    if errors:
        raise AddressValidationError("; ".join(errors))

    return {
        "line1": line1_clean,
        "city": city_clean,
        "country": country_clean,
        "postcode": postcode_clean,
    }


# ---------------------------------------------------------------------------
# Pydantic v2 model for full-form POST validation
# ---------------------------------------------------------------------------


class ProfileFormPayload(BaseModel):
    """All accepted profile fields. All optional individually — progressive disclosure.

    Required-for-screen logic lives in progressive.required_for_screen — this model
    only enforces field-level format correctness for fields that ARE supplied.
    """

    # Identity
    nic: Optional[str] = Field(default=None, max_length=20)
    tin: Optional[str] = Field(default=None, max_length=20)

    # Address
    address_line1: Optional[str] = Field(default=None, max_length=255)
    address_line2: Optional[str] = Field(default=None, max_length=255)
    city: Optional[str] = Field(default=None, max_length=100)
    postcode: Optional[str] = Field(default=None, max_length=20)
    country: Optional[str] = Field(default="LK", max_length=2)

    # Tax residency
    tax_resident_year: Optional[int] = Field(default=None, ge=1, le=99)
    days_in_sl_current_year: Optional[int] = Field(default=None, ge=0, le=366)

    # Employer status
    employment_type: Optional[str] = Field(default=None)
    has_foreign_clients: Optional[bool] = None

    # Bank
    bank_account_holder_name: Optional[str] = Field(default=None, max_length=255)
    bank_name: Optional[str] = Field(default=None, max_length=100)
    bank_branch: Optional[str] = Field(default=None, max_length=255)
    bank_account_number: Optional[str] = Field(default=None, max_length=50)

    # Persona — v1: locked to 'self' (read-only in UI; enforced server-side)
    persona: Optional[str] = Field(default="self", max_length=20)

    @field_validator("nic")
    @classmethod
    def _validate_nic(cls, v: Optional[str]) -> Optional[str]:
        if v is None or str(v).strip() == "":
            return None
        return validate_nic(v)

    @field_validator("tin")
    @classmethod
    def _validate_tin(cls, v: Optional[str]) -> Optional[str]:
        if v is None or str(v).strip() == "":
            return None
        return validate_tin(v, required=False)

    @field_validator("employment_type")
    @classmethod
    def _validate_employment_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None or str(v).strip() == "":
            return None
        allowed = {"employee", "contractor", "business_owner", "mix"}
        v_clean = str(v).strip().lower()
        if v_clean not in allowed:
            raise ValueError(
                f"employment_type must be one of {sorted(allowed)}; got {v!r}"
            )
        return v_clean

    @field_validator("persona")
    @classmethod
    def _lock_persona(cls, v: Optional[str]) -> str:
        # v1: persona is always 'self'. Reject any attempt to override.
        if v is not None and str(v).strip().lower() not in {"self", ""}:
            raise ValueError(
                "persona is locked to 'self' in v1 (single-persona FIESTA platform)"
            )
        return "self"

    @field_validator("bank_account_number")
    @classmethod
    def _validate_account_number(cls, v: Optional[str]) -> Optional[str]:
        if v is None or str(v).strip() == "":
            return None
        v_clean = str(v).strip().replace(" ", "").replace("-", "")
        # SL bank account numbers vary 8-20 digits. Allow alphanumeric (some banks use suffixes).
        if not re.match(r"^[0-9A-Z]{6,30}$", v_clean.upper()):
            raise ValueError(
                "Bank account number must be 6-30 alphanumeric characters"
            )
        return v_clean

    @field_validator("country")
    @classmethod
    def _validate_country(cls, v: Optional[str]) -> str:
        if v is None or str(v).strip() == "":
            return "LK"
        v_clean = str(v).strip().upper()
        if len(v_clean) != 2:
            raise ValueError("Country must be a 2-letter ISO code")
        return v_clean


__all__ = [
    "validate_nic",
    "validate_tin",
    "validate_address",
    "ProfileFormPayload",
    "NICValidationError",
    "TINValidationError",
    "AddressValidationError",
]
