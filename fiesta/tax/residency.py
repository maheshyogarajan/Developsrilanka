"""fiesta.tax.residency — ResidencyStatus enum (Design Lock 2 §2).

BINDING enum. Members + string values are LOCKED. B10 NRR classifier writes
User.residency_status using these values. Section G G1/G4 reads the same
column — no separate classifier table (Sonnet's lock, Council 2026-05-25).
"""

from __future__ import annotations

import enum


class ResidencyStatus(str, enum.Enum):
    """SL tax-residency classification.

    Members (LOCKED — do not rename):
      RESIDENT     — SL-resident: 183+ days in SL OR centre of vital interests in SL
      NRR          — Non-Resident Returnee: returned to SL after 5+ years
                     abroad, taxed concessionally for 3 years
      NONRESIDENT  — not SL-resident; SL-source income only is taxed
      UNKNOWN      — default for users without classifier run; treat as
                     RESIDENT for computation but flag in UI
    """

    RESIDENT = "resident"
    NRR = "nrr"
    NONRESIDENT = "nonresident"
    UNKNOWN = "unknown"


__all__ = ["ResidencyStatus"]
