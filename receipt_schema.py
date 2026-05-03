"""
Pydantic schemas for Gemini structured outputs API.
These schemas ensure type-safe, validated extraction from receipts.
Note: Simplified to be compatible with Gemini's schema conversion (no defaults).
"""

from typing import List, Literal, Optional
from pydantic import BaseModel


class ReceiptItem(BaseModel):
    """Individual line item on a receipt with tax deductibility information."""

    name: str
    quantity: float
    price: float
    tax_deductible: bool
    deductibility_percentage: float
    tax_law_reference: str
    deduction_notes: str


class Receipt(BaseModel):
    """Complete receipt data extracted from an image or bank transfer screenshot."""

    vendor_name: str
    vendor_address: str
    vendor_contact: str
    date: str
    items: List[ReceiptItem]
    total_amount: float
    service_charge: float
    vat_tax: float
    sscl_tax: float
    vat_registration_number: str
    expense_major_category: Literal[
        "Operating Expenses",
        "Administrative Expenses",
        "Cost of Goods Sold",
        "Employee Benefits",
        "Finance Costs",
    ]
    expense_minor_category: Literal[
        "Meals and Entertainment",
        "Travel and Transportation",
        "Professional Services",
        "Office Supplies",
        "Marketing and Advertising",
        "Utilities",
        "Rent and Facilities",
        "Software and SaaS",
        "Bank and Merchant Fees",
        "Repairs and Maintenance",
        "Training, Education and Development",
        "Legal and Accounting",
        "Telecommunications",
        "Administrative and General",
    ]


class StageARawItem(BaseModel):
    """Single line item produced by Stage A vision OCR (no tax classification)."""

    name: str
    quantity: float = 1
    price: float = 0

    class Config:
        extra = "ignore"


class StageARawReceipt(BaseModel):
    """
    Stage A raw-OCR payload: factual fields only, no tax classification.
    Used to validate the JSON returned by GLM-OCR (or any other Stage A
    provider) before handing it to the Stage B reasoner.
    """

    vendor_name: str
    vendor_address: Optional[str] = ""
    vendor_contact: Optional[str] = ""
    date: Optional[str] = ""
    items: List[StageARawItem] = []
    total_amount: float = 0
    service_charge: Optional[float] = 0
    vat_tax: Optional[float] = 0
    sscl_tax: Optional[float] = 0
    vat_registration_number: Optional[str] = ""

    class Config:
        extra = "ignore"
