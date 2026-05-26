"""tests/tax_bill/test_attestation_guard.py — F6.3 placeholder-leak guard.

Phase B Wave 1 — Fix 2 (2026-05-26).

The attestation text under fiesta.submit.attestation is a §195 declaration
that the customer signs electronically under the Electronic Transactions
Act No. 19 of 2006. Previous behaviour: when ``full_name`` or ``nic`` was
empty, ``build_attestation_text`` substituted "(name not provided)" / "(NIC
not provided)" placeholder strings into the declaration, producing a signed
instrument that read e.g. "I, (name not provided) (NIC (NIC not provided)),
declare under section 195 ...". That is the F6.3 placeholder-leak defect —
a legal-liability hazard: the customer cannot meaningfully attest with
placeholder text where their identity should be, and IRD would have grounds
to dismiss the declaration's evidentiary value.

The fix is two-layered, defence-in-depth:

  Layer 1 (function-level, this suite):
    ``build_attestation_text`` now raises ``AttestationFieldMissingError``
    when ``full_name``, ``nic`` or ``tax_year`` is empty. The placeholder-
    string fallbacks have been removed. This protects ALL callers (current
    and future) at the source: the §195 text cannot be built without real
    identity.

  Layer 2 (route-level, exists in fiesta.submit.routes.show_submit +
   post_attest):
    Before building the attestation preview / signing, the routes check
    ``current_user.name`` + ``FiestaProfile.nic`` and route the user to
    /fiesta/profile if either is missing. Already wired (X9 F6.3 comments
    at submit/routes.py:546-572 + 689-706). Covered by the existing
    submit-flow tests; this suite focuses on the new function-level guard.

Cases:
  01. ``build_attestation_text`` happy path still renders normally.
  02. Empty ``full_name`` -> ``AttestationFieldMissingError``.
  03. Whitespace-only ``full_name`` -> ``AttestationFieldMissingError``.
  04. Empty ``nic`` -> ``AttestationFieldMissingError``.
  05. Whitespace-only ``nic`` -> ``AttestationFieldMissingError``.
  06. Empty ``tax_year`` -> ``AttestationFieldMissingError``.
  07. Both name + NIC empty -> error names BOTH missing fields.
  08. Error message contains "§195" mention so callers + log readers see
      WHY the call refused.
  09. Placeholder strings "(name not provided)" / "(NIC not provided)"
      no longer appear anywhere in the rendered text under any condition.
  10. ``AttestationFieldMissingError`` is exported from the package
      __all__ so callers can catch it explicitly.

Run:
    python -m pytest tests/tax_bill/test_attestation_guard.py -v
"""
from __future__ import annotations

import pathlib
import sys

import pytest

# Make the worktree root importable when invoked from repo root.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fiesta.submit.attestation import (  # noqa: E402
    AttestationFieldMissingError,
    build_attestation_text,
)


def test_01_happy_path_renders():
    """Full data still renders the canonical §195 declaration."""
    text = build_attestation_text(
        full_name="Anuk Wijesinghe",
        nic="901234567V",
        tax_year="2025/2026",
        final_tax_payable_lkr=475_000,
    )
    assert "Anuk Wijesinghe" in text
    assert "901234567V" in text
    assert "2025/2026" in text
    assert "section 195" in text.lower()


def test_02_empty_full_name_raises():
    with pytest.raises(AttestationFieldMissingError) as exc:
        build_attestation_text(
            full_name="",
            nic="901234567V",
            tax_year="2025/2026",
            final_tax_payable_lkr=100_000,
        )
    assert "Full name" in exc.value.missing_fields


def test_03_whitespace_full_name_raises():
    with pytest.raises(AttestationFieldMissingError) as exc:
        build_attestation_text(
            full_name="   \t  ",
            nic="901234567V",
            tax_year="2025/2026",
            final_tax_payable_lkr=100_000,
        )
    assert "Full name" in exc.value.missing_fields


def test_04_empty_nic_raises():
    with pytest.raises(AttestationFieldMissingError) as exc:
        build_attestation_text(
            full_name="Anuk Wijesinghe",
            nic="",
            tax_year="2025/2026",
            final_tax_payable_lkr=100_000,
        )
    assert "NIC" in exc.value.missing_fields


def test_05_whitespace_nic_raises():
    with pytest.raises(AttestationFieldMissingError) as exc:
        build_attestation_text(
            full_name="Anuk Wijesinghe",
            nic="\n",
            tax_year="2025/2026",
            final_tax_payable_lkr=100_000,
        )
    assert "NIC" in exc.value.missing_fields


def test_06_empty_tax_year_raises():
    with pytest.raises(AttestationFieldMissingError) as exc:
        build_attestation_text(
            full_name="Anuk Wijesinghe",
            nic="901234567V",
            tax_year="",
            final_tax_payable_lkr=100_000,
        )
    assert "Tax year" in exc.value.missing_fields


def test_07_both_name_and_nic_missing_lists_both():
    with pytest.raises(AttestationFieldMissingError) as exc:
        build_attestation_text(
            full_name="",
            nic="",
            tax_year="2025/2026",
            final_tax_payable_lkr=100_000,
        )
    assert "Full name" in exc.value.missing_fields
    assert "NIC" in exc.value.missing_fields
    # The error message should also surface the missing fields verbatim
    msg = str(exc.value)
    assert "Full name" in msg
    assert "NIC" in msg


def test_08_error_message_cites_section_195():
    """The error message must explain WHY the call refused — callers + log
    readers should immediately understand this is a §195/ETA defensibility
    issue, not a generic input-validation refusal."""
    with pytest.raises(AttestationFieldMissingError) as exc:
        build_attestation_text(
            full_name="",
            nic="",
            tax_year="2025/2026",
            final_tax_payable_lkr=0,
        )
    msg = str(exc.value)
    assert "§195" in msg or "195" in msg


def test_09_placeholder_strings_never_appear_in_output():
    """Defence-in-depth: under no condition should the rendered text contain
    "(name not provided)" / "(NIC not provided)" / "(tax year not provided)"
    placeholder strings. The previous fallback path produced these; the
    new contract is: either render with real identity, or raise."""
    # Happy path -- placeholders absent.
    text = build_attestation_text(
        full_name="Anuk Wijesinghe",
        nic="901234567V",
        tax_year="2025/2026",
        final_tax_payable_lkr=1_000,
    )
    assert "(name not provided)" not in text
    assert "(NIC not provided)" not in text
    assert "(tax year not provided)" not in text
    assert "(your NIC)" not in text
    assert "(your name)" not in text

    # Missing-field path -- function raises BEFORE rendering, so no text
    # leak is possible.
    for bad in [
        {"full_name": "", "nic": "X", "tax_year": "2025/2026"},
        {"full_name": "X", "nic": "", "tax_year": "2025/2026"},
        {"full_name": "X", "nic": "Y", "tax_year": ""},
    ]:
        with pytest.raises(AttestationFieldMissingError):
            build_attestation_text(**bad, final_tax_payable_lkr=0)


def test_10_exception_class_exported():
    """Callers should be able to ``from fiesta.submit.attestation import
    AttestationFieldMissingError`` and catch it specifically — i.e. it must
    be in the package __all__."""
    from fiesta.submit import attestation
    assert "AttestationFieldMissingError" in attestation.__all__
    # And it really must be a ValueError subclass so existing
    # ``except ValueError`` clauses (defensive logging in route handlers)
    # still catch it.
    assert issubclass(AttestationFieldMissingError, ValueError)
