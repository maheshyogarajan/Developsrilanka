"""fiesta.tax — Sri Lanka PIT preview module.

This is the lightweight, self-contained preview layer used by the S0 Tax
Math Breakdown landing component. It computes a bracket-by-bracket
estimate from a few inputs to defuse the "Rs 2,500 saving Rs 540,000 =
scam" risk before the customer hits the paywall.

It is NOT the authoritative tax engine. The full engine (with WHT,
qualifying payments, FX conversion, A&L lodging, etc.) lives elsewhere
and is the source of truth for filed returns. This module shares the
same slabs.yaml so the numbers match.

Public surface:
  - quick_preview(...)  -> dict
  - PreviewError        -> ValueError subclass for known input failures
"""
from .preview import PreviewError, quick_preview

__all__ = ["quick_preview", "PreviewError"]
