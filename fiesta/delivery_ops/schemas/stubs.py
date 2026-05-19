"""v1.1 stub schemas for remaining doc types.

These doc types are recognized by `validate_doc` but extraction fields are
incomplete — the schemas document the deferred fields explicitly rather than
half-extracting. v1.1 will flesh them out using doclens-v1 + Commaut2.0/dev as
canonical sources.

PROVED writer attribution per Commaut2.0/dev:src/dv_up.py field_mapping:
  - BALANCE_CONFIRMATION → field_mapping[18] = Bank_documents_received__c
  - A_AND_L              → field_mapping[9]  = Assets_and_Liabilities_form_received__c
  - EMPLOYER_LETTER      → NOT in the 18-key map (UNPROVED writer attribution;
    Lanka.tax has no boolean-flip for employer letters today — they're handled
    inline within the T10 / Scan_result_employement__c pipeline)
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class BalanceConfirmationExtraction(BaseModel):
    """v1.1 stub — bank balance confirmation (31-March snapshot).

    PROVED writer: Bank_documents_received__c (field_mapping[18] in Commaut2.0/dev
    src/dv_up.py — same field as bank confirmation per doclens-v1 dual-purpose
    pattern). Distinguished from BANK_INTEREST_WHT by the absence of interest
    entries and the presence of a 31-March balance.

    v1.1 fields to flesh out: bank_name, account_number, balance_lkr,
    balance_as_of_date (must be 31-March of some year), client_nic.
    Most of the schema overlaps BankInterestWhtExtraction — v1.1 will likely
    refactor to share a BankAccountBase.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    bank_name: Optional[str] = Field(default=None, description="UNPROVED v1.1 stub.")
    account_number: Optional[str] = Field(default=None, description="UNPROVED v1.1 stub.")
    balance_lkr: Optional[float] = Field(default=None, description="UNPROVED v1.1 stub.")
    balance_as_of_date: Optional[str] = Field(
        default=None, description="UNPROVED v1.1 stub. Expected 31-March of year."
    )
    client_nic: Optional[str] = Field(default=None, description="UNPROVED v1.1 stub.")


def is_balance_confirmation_fully_valid(
    extraction: BalanceConfirmationExtraction,
) -> tuple[bool, str | None]:
    return False, "BALANCE_CONFIRMATION extraction is v1.1 stub — not yet implemented"


class AssetsLiabilitiesExtraction(BaseModel):
    """v1.1 stub — A&L declaration form extraction.

    PROVED writer: Assets_and_Liabilities_form_received__c (field_mapping[9]).

    PCSE NOTE: per CLAUDE.md V5 addendum 2026-04-23, A&L is a SEPARATE pre-FILING
    step (NOT pre-computation per the filename). Computation runs in parallel.
    Extraction maps to the line-item child object `Asset_or_Liability__c`
    (34 fields, per Strategist D §1 row 17).

    v1.1 fields: assets[] (list of asset line items), liabilities[] (list of
    liability line items), declared_for_year_of_assessment, declared_date,
    client_nic.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    declared_for_year_of_assessment: Optional[str] = Field(
        default=None, description="UNPROVED v1.1 stub."
    )
    declared_date: Optional[str] = Field(default=None, description="UNPROVED v1.1 stub.")
    client_nic: Optional[str] = Field(default=None, description="UNPROVED v1.1 stub.")
    total_assets_lkr: Optional[float] = Field(default=None, description="UNPROVED v1.1 stub.")
    total_liabilities_lkr: Optional[float] = Field(default=None, description="UNPROVED v1.1 stub.")


def is_a_and_l_fully_valid(
    extraction: AssetsLiabilitiesExtraction,
) -> tuple[bool, str | None]:
    return False, "A_AND_L extraction is v1.1 stub — not yet implemented"


class EmployerLetterExtraction(BaseModel):
    """v1.1 stub — employer confirmation letter (NOT a T10).

    UNPROVED writer attribution: there is NO entry in the 18-key Commaut2.0/dev
    field_mapping for employer letters. Today Lanka.tax handles these inline as
    T10 supporting documents (single Scan_result_employement__c row covers
    both). FIESTA may eventually grow a dedicated `Employer_Letter__c` object;
    until then, this stub exists for classification purposes only.

    v1.1 fields: employer_name, employee_name, employee_nic, statement_date,
    employment_start_date, role / designation, salary_disclosed (yes/no/amount).
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    employer_name: Optional[str] = Field(
        default=None,
        description="UNPROVED v1.1 stub. Lanka.tax has no Employer_Letter__c object today.",
    )
    employee_name: Optional[str] = Field(default=None, description="UNPROVED v1.1 stub.")
    employee_nic: Optional[str] = Field(default=None, description="UNPROVED v1.1 stub.")
    statement_date: Optional[str] = Field(default=None, description="UNPROVED v1.1 stub.")


def is_employer_letter_fully_valid(
    extraction: EmployerLetterExtraction,
) -> tuple[bool, str | None]:
    return False, "EMPLOYER_LETTER extraction is v1.1 stub — not yet implemented"


__all__ = [
    "BalanceConfirmationExtraction",
    "is_balance_confirmation_fully_valid",
    "AssetsLiabilitiesExtraction",
    "is_a_and_l_fully_valid",
    "EmployerLetterExtraction",
    "is_employer_letter_fully_valid",
]
