"""fiesta.cosign -- S10 Service Provider co-sign workflow.

Wave 3 (2026-05-20). Per the S10 dispatch brief.

What S10 owns
-------------
The EXPERIENCE between S8 (Service Agreement PDF generated) and
"agreement signed by both parties". The customer needs to:

  1. Send the PDF to their Service Provider (SP)
  2. Walk the SP through why/how to sign it
  3. Wait for SP to sign
  4. Countersign themselves

This module wraps that entire experience: workflow tracking, SP-side
signing UI (tracking-token-gated, no SP-side auth required), email
templates for outreach + reminders, a daily reminder scheduler, and an
abandon path for customers who handle co-signing offline.

Legal anchor
------------
SP-side typed-name signing is Electronic Transactions Act No. 19 of 2006
compliant (Sri Lanka). We capture: typed name, IP address, UA, timestamp,
agreement reference, tracking token (single-use). Retention 7 years for
IRD audit defence per S8 design recommendation.

Privacy
-------
SP signature artefacts (typed name + IP + UA + timestamp) are stored on
the CosignWorkflow row + are visible ONLY to the customer who initiated
that workflow. They are NEVER joined or surfaced across customers.

Module wiring
-------------
Registered by main.py via:

    from fiesta.cosign.routes import register_routes as register_cosign
    register_cosign(app)
"""
from __future__ import annotations

from fiesta.cosign.models import CosignWorkflow, CosignReminder

__all__ = ["CosignWorkflow", "CosignReminder"]
