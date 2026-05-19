"""fiesta.lifecycle — Wave 4 year-round companion modules.

Two pillars:

  X3 — year_end.py + rollover_scheduler.py
        Sri Lanka tax year is 1 April -> 31 March. Customers don't see
        their filing tool once a year; they live in it across the cycle:
        - 1 Apr: new tax year begins, prior-year carry-overs prompted
        - 30 Apr: filing window for prior year opens
        - 30 Nov: filing deadline (8 months after year-end per IRA s.93)
        - 31 Mar: tax year closing reminder

  S11 — invoice_cadence.py + reminders.py + audit_log.py
        Service providers + business expenses generate recurring invoices.
        FIESTA tracks cadence per (customer, service_provider) to:
        - detect missing months (coverage gaps)
        - detect irregular cadence (audit risk -> X6 compliance gate)
        - send pre-period reminders so the next invoice doesn't slip

Provenance:
  - Council brief: working files/strategic/council/_briefs/fiesta_council_brief.json
    (X3 + S11 entries in lifecycle_actions list)
  - THE_PATH 2026-05-20 PM doc 27: Week 4-5 "Wave 4 cadence + year-end.
    UI hardening for Risk A & B."
  - Integration spec: cadence-irregularity feeds X6 compliance gates on
    the S6 service-providers screen (fiesta.compliance.market_rates_table).

Public surface intentionally narrow — the rest of the FIESTA app talks to
this package through these names, never reaches into private modules:
"""
from __future__ import annotations

from .year_end import (
    TaxYear,
    TransitionResult,
    current_tax_year,
    filing_window_status,
    parse_year_label,
    transition_customer_to_new_year,
)

__all__ = [
    "TaxYear",
    "TransitionResult",
    "current_tax_year",
    "filing_window_status",
    "parse_year_label",
    "transition_customer_to_new_year",
]
