"""
Pydantic schemas for Gemini structured outputs API.
These schemas ensure type-safe, validated extraction from receipts.
"""

from typing import List, Literal
from pydantic import BaseModel, Field


class ReceiptItem(BaseModel):
    """Individual line item on a receipt with tax deductibility information."""
    
    name: str = Field(description="Item name or description")
    quantity: float = Field(default=1, description="Quantity purchased")
    price: float = Field(description="Unit price or total price for this item")
    tax_deductible: bool = Field(
        default=False,
        description="Whether this item is tax deductible under Sri Lankan tax law"
    )
    deductibility_percentage: float = Field(
        default=0,
        ge=0,
        le=100,
        description="Percentage of deductibility (0, 50, or 100) per IRA 2017"
    )
    tax_law_reference: str = Field(
        default="",
        description="Legal reference (e.g., 'Section 25 - IRA 2017')"
    )
    deduction_notes: str = Field(
        default="",
        description="Brief explanation of tax classification"
    )


class Receipt(BaseModel):
    """Complete receipt data extracted from an image or bank transfer screenshot."""
    
    vendor_name: str = Field(
        default="",
        description="Vendor/merchant name or beneficiary name for bank transfers"
    )
    vendor_address: str = Field(
        default="",
        description="Vendor address (converted to English if in another language)"
    )
    vendor_contact: str = Field(
        default="",
        description="Phone number, email, or other contact information"
    )
    date: str = Field(
        default="",
        description="Transaction date in YYYY-MM-DD format (ISO 8601)"
    )
    items: List[ReceiptItem] = Field(
        default_factory=list,
        description="List of items purchased or single item for bank transfers"
    )
    total_amount: float = Field(
        default=0,
        description="Total transaction amount as a number"
    )
    service_charge: float = Field(
        default=0,
        description="Service charge amount if applicable"
    )
    vat_tax: float = Field(
        default=0,
        description="VAT tax amount"
    )
    sscl_tax: float = Field(
        default=0,
        description="SSCL (Social Security Contribution Levy) tax amount"
    )
    vat_registration_number: str = Field(
        default="",
        description="VAT registration number of the vendor"
    )
    expense_major_category: Literal[
        "Operating Expenses",
        "Administrative Expenses",
        "Cost of Goods Sold",
        "Employee Benefits",
        "Finance Costs"
    ] = Field(
        default="Operating Expenses",
        description="Major expense category per accounting standards"
    )
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
        "Administrative and General"
    ] = Field(
        default="Administrative and General",
        description="Minor expense subcategory for detailed classification"
    )
