"""T10 (Sri Lankan APIT employer income statement) extraction schema.

Pydantic schema mirroring the non-split-year T10 surface used by
DataSciLT/doclens-v1/employment_logic.py (read 2026-05-19, SHA 352df1d).

PROVED writer attribution (Step 2b honesty gate):
  - T10_received__c is THE 18-key field_mapping entry #1 in
    Commaut2.0/dev:src/dv_up.py:tik_and_upload() (SHA d0d5cc7).
  - On `fully_valid=True`, the Lanka.tax writer is Commaut LLM doc validator
    via Apex REST → Tax_File__c.T10_received__c=true. FIESTA-side equivalent
    writes a `doc_received` event on the spine (per Strategist D §1 row 14).

Scope:
  - v1.0 ships non-split-year only (years OTHER than 2019/2020 and 2022/2023).
  - Split-year format (2019/2020, 2022/2023) explicitly NOT supported; mark
    as UNPROVED and surface in failure_reason if encountered.

References:
  - working files/lanka_tax_repos_source/doclens-v1/employment_logic.py L154-280
    (get_output_schema): canonical non-split-year unified schema. We copy
    field names verbatim to preserve compatibility with downstream Lanka.tax
    SR_employment_done__c consumers.
  - memory/lanka_tax/reference_sf_doc_collection_writers.md (PROVED writers).
  - memory/lanka_tax/reference_third_eye_ai_scanner.md (failure routing).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class T10Extraction(BaseModel):
    """Non-split-year T10 employer income statement extraction.

    Field names mirror doclens-v1 EmploymentDocumentExtractor schema so
    extractions can be round-tripped to Lanka.tax `Scan_result_employement__c`
    (PROVED 2026-05-19, source: doclens-v1/employment_update_logic.py).
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    # Identifiers (REQUIRED — without these, fully_valid is impossible).
    year_of_assessment: Optional[str] = Field(
        default=None,
        description="Year of Assessment as 'YYYY/YYYY' (e.g. '2024/2025'). "
        "PROVED: doclens-v1 schema requires this for SF dispatch.",
    )
    employer_tin: Optional[str] = Field(
        default=None,
        description="Employer's TIN. PROVED.",
    )
    client_nic: Optional[str] = Field(
        default=None,
        description="Client NIC (National Identity Card). PROVED — doclens-v1 "
        "uses this to join the extraction back to Customer__c via NIC.",
    )
    employer_name: Optional[str] = Field(
        default=None,
        description="Name of the Employer. PROVED — required for SR record.",
    )

    # Numeric core (REQUIRED for `fully_valid=True`).
    total_gross_remuneration: Optional[float] = Field(
        default=None,
        description="Total Gross Remuneration as per APIT pay sheet. "
        "PROVED — feeds Scan_result_employement__c.Gross_Remuneration__c.",
    )
    total_tax_deducted: Optional[float] = Field(
        default=None,
        description="Total Amount of Tax Deducted (APIT). "
        "PROVED — SEPARATE FROM benefits_excluded_for_tax. doclens-v1 prompt "
        "warns about document-alignment ambiguity; preserve the warning.",
    )
    benefits_excluded_for_tax: Optional[float] = Field(
        default=0.0,
        description="Value of Benefits Excluded for Tax. PROVED — DO NOT copy "
        "from total_tax_deducted even when misaligned visually.",
    )
    total_amount_remitted: Optional[float] = Field(
        default=None,
        description="Total Amount Remitted to IRD. Often equals "
        "total_tax_deducted; can differ for split-quarter or partial remit. "
        "PROVED.",
    )

    # Optional supporting fields.
    employee_name: Optional[str] = Field(
        default=None,
        description="Name of the Employee. NOT required by doclens-v1 schema "
        "(it joins by NIC). UNPROVED as a Tax_File__c writer field — kept for "
        "human-readable display only.",
    )
    date: Optional[str] = Field(
        default=None,
        description="Date of issue (YYYY-MM-DD). Optional. UNPROVED.",
    )
    email: Optional[str] = Field(
        default=None,
        description="Email on document (if present). Optional. UNPROVED.",
    )


# Required-field set for the per-doc-type `fully_valid` gate.
#
# Decision: a T10 is `fully_valid` only when ALL of these are populated AND
# pass sanity check (tax < gross). This is the FIESTA equivalent of the
# doclens-v1 two-extractor consensus pattern — at the schema layer rather than
# the LLM layer.
REQUIRED_FOR_FULLY_VALID: tuple[str, ...] = (
    "year_of_assessment",
    "employer_tin",
    "employer_name",
    "total_gross_remuneration",
    "total_tax_deducted",
)


def is_fully_valid(extraction: T10Extraction) -> tuple[bool, str | None]:
    """Apply the per-doc-type validity gate.

    Returns (ok, reason). When `ok=False`, reason is a human-readable string
    suitable for surfacing in `validate_doc` `failure_reason` field.
    """
    missing = [
        name for name in REQUIRED_FOR_FULLY_VALID
        if getattr(extraction, name) in (None, "", 0.0)
        # Allow 0.0 for benefits_excluded_for_tax but NOT for gross/tax numbers.
    ]
    # Patch: 0.0 IS allowed for benefits_excluded but we don't include that in REQUIRED.
    # For numeric required fields, 0 is suspicious — treat as missing.
    if missing:
        return False, f"required fields missing or zero: {', '.join(missing)}"

    gross = extraction.total_gross_remuneration
    tax = extraction.total_tax_deducted
    if gross is not None and tax is not None and tax > gross:
        return False, (
            f"sanity check failed: tax_deducted ({tax}) exceeds "
            f"gross_remuneration ({gross}) — likely OCR misread"
        )

    # Split-year format guard: if year is one of the two split-year codes,
    # this v1 schema does NOT support it (split fields are absent).
    if extraction.year_of_assessment in {"2019/2020", "2022/2023"}:
        return False, (
            f"split-year format {extraction.year_of_assessment} not supported "
            "in v1 — UNPROVED (doclens-v1 has split-year schema that requires "
            "separate Apr-Dec / Jan-Mar fields; deferred to v1.1)"
        )

    return True, None


__all__ = ["T10Extraction", "REQUIRED_FOR_FULLY_VALID", "is_fully_valid"]
