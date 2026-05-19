"""fiesta.compliance -- X6 cross-screen compliance gates.

The full X6 implementation lives on `wave3/x6-compliance-gates` (commit 7397e8c).
This `__init__` is a stub on `wave3/s14-submit` -- the real `gate.py` arrives
when X6 merges. Until then the S14 routes call `gate_check` defensively: if
the real gate is unavailable, the local S14-only stub still enforces the
launch-critical rules (missing-attestation, section-195-missing, deduction
ratio block).

After X6 merges this directory will be a no-op replacement (same public
contract). Routes never need to change.
"""
from __future__ import annotations

__all__: list[str] = []
