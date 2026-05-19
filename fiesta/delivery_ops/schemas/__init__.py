"""Per-document-type Pydantic schemas.

PROVED-writer attribution (per CLAUDE.md Step 2b honesty gate):
  - T10 / BANK_INTEREST_WHT / BALANCE_CONFIRMATION / A_AND_L target the same
    18-key field_mapping that Commaut2.0/dev:src/dv_up.py:tik_and_upload()
    writes to Tax_File__c. See `reference_sf_doc_collection_writers.md`.
  - The EMPLOYER_LETTER stub targets the Employment_Letter_received__c family
    which is NOT in the 18-key map; marked UNPROVED accordingly.
"""

from .t10 import T10Extraction
from .bank_interest_wht import BankInterestWhtExtraction
from .stubs import (
    BalanceConfirmationExtraction,
    AssetsLiabilitiesExtraction,
    EmployerLetterExtraction,
)

__all__ = [
    "T10Extraction",
    "BankInterestWhtExtraction",
    "BalanceConfirmationExtraction",
    "AssetsLiabilitiesExtraction",
    "EmployerLetterExtraction",
]
