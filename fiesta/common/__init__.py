"""fiesta.common — shared cross-cutting helpers.

Currently exposes:
    - tax_year.TaxYear : canonical Year-of-Assessment model.
"""
from fiesta.common.tax_year import TaxYear, active_tax_year, supported_tax_years

__all__ = ["TaxYear", "active_tax_year", "supported_tax_years"]
