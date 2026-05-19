"""Bank interest + Withholding Tax (WHT) certificate extraction schema.

Pydantic schema mirroring DataSciLT/doclens-v1/scan_bank.py (read 2026-05-19,
SHA 352df1d). Covers SL bank interest-bearing accounts (savings, FD, T-Bill).

PROVED writer attribution (Step 2b honesty gate):
  - Bank_documents_received__c is the 18-key field_mapping entry #2 in
    Commaut2.0/dev:src/dv_up.py:tik_and_upload() (SHA d0d5cc7), covering BOTH
    bank confirmations (entry #2) AND balance confirmations (entry #18).
  - PROVED writer per memory/lanka_tax/reference_sf_doc_collection_writers.md
    — Commaut LLM doc validator flips this field on `fully_valid=True`.

Scope:
  - v1.0 covers per-account interest income + WHT certificate fields. The
    doclens-v1 schema has account-level granularity (Monthly / Quarterly /
    Annually); we mirror that.
  - Multi-account documents (one PDF, multiple accounts) ship as v1.1; v1.0
    handles single-account uploads cleanly and reports `failure_reason` for
    multi-account inputs.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class InterestEntry(BaseModel):
    """One interest-income line item.

    doclens-v1 granularity: Monthly (12 entries), Quarterly (4 entries),
    Annually (1 entry). PROVED — copy field names verbatim.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    period_start_date: Optional[str] = Field(
        default=None, description="Period start (YYYY-MM-DD). PROVED."
    )
    period_end_date: Optional[str] = Field(
        default=None, description="Period end (YYYY-MM-DD). PROVED."
    )
    amount: Optional[float] = Field(
        default=None, description="Interest amount for the period (LKR). PROVED."
    )


class WhtEntry(BaseModel):
    """One withholding-tax line item, matched to an interest period."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    period_start_date: Optional[str] = Field(default=None, description="PROVED.")
    period_end_date: Optional[str] = Field(default=None, description="PROVED.")
    amount: Optional[float] = Field(
        default=None, description="WHT amount deducted at source (LKR). PROVED."
    )


class WhtCertEntry(BaseModel):
    """One WHT certificate identifier.

    doclens-v1 warning: look for 'certificate number' / 'serial no' / 'reference
    no' — NOT 'CHQ No' / 'Ref No. PMT DATE' / 'Transaction Ref' / 'Cheque No'
    (those are payment IDs). PROVED.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    cert_number: Optional[str] = Field(
        default=None, description="Withholding tax certificate ID. PROVED."
    )


class BankInterestWhtExtraction(BaseModel):
    """One bank-account-year extraction.

    Field set per doclens-v1 scan_bank.py schema (L182-289). Year-of-assessment
    join semantics: the combo (account_number, year_of_assessment) uniquely
    identifies one row in Scan_result_balance__c / Scan_result_interest__c.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    # Account identifiers (REQUIRED).
    bank_name: Optional[str] = Field(
        default=None, description="Bank name (normalized via doclens-v1 valid_bank rules). PROVED."
    )
    branch_name: Optional[str] = Field(default=None, description="Branch. PROVED.")
    account_number: Optional[str] = Field(
        default=None,
        description="Account number. PROVED — joins to existing Bank_Information__c.",
    )
    account_holder_name: Optional[str] = Field(default=None, description="PROVED.")
    number_of_account_holders: Optional[int] = Field(
        default=1,
        description="1=sole, 2=joint, 3+=multi. PROVED — affects tax apportionment.",
    )
    client_nic: Optional[str] = Field(
        default=None,
        description="Client NIC for join. PROVED — same role as in T10 schema.",
    )

    # Year-of-assessment + granularity.
    year_of_assessment: Optional[str] = Field(
        default=None,
        description=(
            "Year of Assessment as 'YYYY/YYYY' for THIS account entry. PROVED. "
            "Per doclens-v1 prompt: derive from March-31 balance date OR from "
            "interest_income period_end_date. If period ends 31.03.YYYY, "
            "year_of_assessment = (YYYY-1)/YYYY."
        ),
    )
    granularity: Optional[str] = Field(
        default=None,
        description="One of: Monthly / Quarterly / Annually. PROVED.",
    )

    # Balance.
    balance_lkr: Optional[float] = Field(
        default=None,
        description=(
            "Closing balance on the March-31 confirmation date. PROVED. "
            "Maps to Scan_result_balance__c.Balance_Amount__c. The doclens-v1 "
            "schema uses a dynamic key `balance_as_of_<date>` — we normalize "
            "to a single field here and carry the date separately."
        ),
    )
    balance_as_of_date: Optional[str] = Field(
        default=None,
        description="Date the balance was confirmed (YYYY-MM-DD). PROVED.",
    )

    # Interest + WHT line items.
    interest_income: List[InterestEntry] = Field(
        default_factory=list, description="List of interest-income entries. PROVED."
    )
    with_holding_tax: List[WhtEntry] = Field(
        default_factory=list, description="List of WHT entries. PROVED."
    )
    wht_cert: List[WhtCertEntry] = Field(
        default_factory=list,
        description="List of WHT certificate identifiers. PROVED.",
    )


REQUIRED_FOR_FULLY_VALID: tuple[str, ...] = (
    "bank_name",
    "account_number",
    "year_of_assessment",
)


def is_fully_valid(extraction: BankInterestWhtExtraction) -> tuple[bool, str | None]:
    """Per-doc-type validity gate for BANK_INTEREST_WHT.

    Bank/account/year are the minimum identity tuple. Interest income OR
    balance must be present (one of the two; not both required).
    """
    missing = [
        name for name in REQUIRED_FOR_FULLY_VALID if getattr(extraction, name) in (None, "")
    ]
    if missing:
        return False, f"required fields missing: {', '.join(missing)}"

    has_interest = bool(extraction.interest_income)
    has_balance = extraction.balance_lkr is not None

    if not (has_interest or has_balance):
        return False, "neither interest_income entries nor balance_lkr present"

    # Sanity check: granularity should match interest entry count.
    if has_interest and extraction.granularity:
        expected = {"Monthly": 12, "Quarterly": 4, "Annually": 1}.get(
            extraction.granularity
        )
        if expected is not None and len(extraction.interest_income) != expected:
            return False, (
                f"granularity={extraction.granularity} expects {expected} "
                f"interest entries; got {len(extraction.interest_income)}"
            )

    return True, None


__all__ = [
    "BankInterestWhtExtraction",
    "InterestEntry",
    "WhtEntry",
    "WhtCertEntry",
    "REQUIRED_FOR_FULLY_VALID",
    "is_fully_valid",
]
