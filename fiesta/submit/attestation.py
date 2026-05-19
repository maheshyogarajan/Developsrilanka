"""fiesta.submit.attestation -- customer attestation under §195 + ETA 2006.

What this module does
---------------------
1. Builds the attestation text dynamically from (customer name, NIC, TY,
   final tax payable). Returns a SNAPSHOT string -- the routes persist it
   verbatim onto Submission.attestation_text so the customer sees the same
   text they signed even if the template changes later.

2. Captures the signature: typed-name + client IP + ISO8601 timestamp +
   session_id + user_agent. Returns a JSON-serializable dict; the routes
   persist it onto Submission.attestation_signature.

3. Validates the typed signature against the customer's profile name.
   - Case-insensitive comparison
   - Whitespace-normalised (collapsed)
   - Diacritic-insensitive (NFD-strip)
   - Empty strings rejected
   - Substring-of-profile-name rejected (must be the full canonical name)

Legal basis
-----------
- Electronic Transactions Act No. 19 of 2006, sections 6 + 7: an electronic
  signature is a "data message [...] used as an identifier" that the signer
  has adopted as theirs. The combination of (typed name + IP + timestamp +
  declaration text) constitutes a signature for non-deed instruments.
- Inland Revenue Act No. 24 of 2017, section 195: the person filing the
  return is the responsible filer; if the customer attests and FIESTA
  submits, the customer is still the responsible filer.
- The "I have reviewed all FIESTA-flagged warnings and §195 disclosures"
  clause defends FIESTA's posture per THE_PATH_20260520 Risk B (IRD must
  not characterise FIESTA as a systemic evasion facilitator).
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any


ATTESTATION_TEMPLATE = (
    "I, {full_name} (NIC {nic}), declare under section 195 of the Inland "
    "Revenue Act No. 24 of 2017 that the above tax return for tax year "
    "{tax_year} is true and correct to the best of my knowledge. I have "
    "reviewed all FIESTA-flagged warnings and section-195 disclosures. I "
    "understand I am the responsible filer.\n\n"
    "Final tax payable: LKR {final_tax_payable_lkr_formatted}.\n\n"
    "This declaration constitutes an electronic signature under the "
    "Electronic Transactions Act No. 19 of 2006."
)


def _format_lkr(amount: Any) -> str:
    """Format an LKR amount with thousands separators + 2 decimal places."""
    try:
        n = float(amount or 0)
    except (TypeError, ValueError):
        n = 0.0
    return f"{n:,.2f}"


def build_attestation_text(
    *,
    full_name: str,
    nic: str,
    tax_year: str,
    final_tax_payable_lkr: Any,
) -> str:
    """Render the attestation text for one customer + one tax year + one bill.

    Args:
        full_name: Customer's full name as it appears on their FIESTA profile.
        nic: Customer's NIC (Sri Lankan National Identity Card).
        tax_year: Canonical "YYYY/YYYY" tax-year string.
        final_tax_payable_lkr: Numeric (or string) LKR amount.

    Returns:
        The fully-rendered attestation text. Caller stores this VERBATIM.
    """
    return ATTESTATION_TEMPLATE.format(
        full_name=(full_name or "").strip() or "(name not provided)",
        nic=(nic or "").strip() or "(NIC not provided)",
        tax_year=(tax_year or "").strip() or "(tax year not provided)",
        final_tax_payable_lkr_formatted=_format_lkr(final_tax_payable_lkr),
    )


def _normalise_name(name: str) -> str:
    """Normalise a name for signature comparison.

    1. NFD-decompose to peel off diacritics.
    2. Drop combining marks.
    3. Collapse internal whitespace + strip ends.
    4. Lower-case.
    """
    s = unicodedata.normalize("NFD", name or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def validate_signature_name(
    typed_name: str, profile_name: str
) -> tuple[bool, str]:
    """Validate a typed signature against the customer's profile name.

    Returns:
        (is_valid, error_message). When is_valid=True, error_message="".

    Validation rules
    ----------------
    - Empty typed_name -> rejected ("Please type your full name to sign").
    - Empty profile_name -> rejected with a different message (data error
      that the customer can't fix).
    - Case-insensitive, whitespace-normalised, diacritic-insensitive match
      after both names are normalised.
    - Substring matches (e.g. "Anuk" vs profile "Anuk Wijesinghe") are
      REJECTED -- we require the full canonical name. This protects against
      "first name only" sloppy signing that wouldn't hold up at audit.
    """
    typed = (typed_name or "").strip()
    if not typed:
        return False, "Please type your full name to sign."

    profile = (profile_name or "").strip()
    if not profile:
        return False, (
            "Your profile name is missing -- complete S3 (profile) before "
            "signing the attestation."
        )

    typed_norm = _normalise_name(typed)
    profile_norm = _normalise_name(profile)

    if typed_norm == profile_norm:
        return True, ""

    # Reject substring matches.
    if typed_norm in profile_norm:
        return False, (
            "Type your FULL name as it appears in your profile "
            f"('{profile}'), not just a part of it."
        )

    return False, (
        f"Typed name doesn't match your profile name "
        f"('{profile}'). If your profile name is wrong, fix S3 first."
    )


def sign_attestation(
    *,
    typed_name: str,
    profile_name: str,
    client_ip: str | None,
    user_agent: str | None = None,
    session_id: str | None = None,
    now: datetime | None = None,
) -> tuple[bool, dict[str, Any] | str]:
    """Capture an attestation signature.

    Returns:
        (success, signature_dict_or_error_message).

        When success=True, the second item is a JSON-serialisable dict to
        store on Submission.attestation_signature. When False, it is a
        human-readable error string for the caller to surface.

    Side effects: NONE. Pure function. The route writes to the DB.
    """
    ok, err = validate_signature_name(typed_name, profile_name)
    if not ok:
        return False, err

    when = now or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    signature = {
        "signature_name": (typed_name or "").strip(),
        "ip": (client_ip or "").strip() or None,
        "timestamp_iso": when.isoformat(),
        "session_id": session_id,
        "user_agent": user_agent,
    }
    return True, signature


def serialize_signature(signature: dict[str, Any]) -> str:
    """JSON-encode a signature dict for storage on
    Submission.attestation_signature.
    """
    return json.dumps(signature, sort_keys=True, separators=(",", ":"))


def deserialize_signature(blob: str | None) -> dict[str, Any]:
    """Decode a stored signature blob. Returns {} on any decode failure."""
    if not blob:
        return {}
    try:
        return json.loads(blob)
    except (TypeError, ValueError):
        return {}


__all__ = [
    "ATTESTATION_TEMPLATE",
    "build_attestation_text",
    "validate_signature_name",
    "sign_attestation",
    "serialize_signature",
    "deserialize_signature",
]
