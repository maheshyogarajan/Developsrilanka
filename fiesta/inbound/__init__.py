"""fiesta.inbound — X5 inbound customer reply handler (Wave 3, FIESTA-side).

When a FIESTA customer replies to an outbound email (verification, S2/S3/S4
reminders, S5 deduction prompts, payment receipt, S8/S9 agreement etc.), the
provider (SendGrid / Postmark inbound webhook) POSTs the parsed reply here.

Pipeline:
    webhook.py   -> POST /webhooks/inbound-email
                    - verifies signature
                    - parses email
                    - matches customer (User/Client) by from_address + threading
                    - persists InboundEmail row
                    - calls classifier
    classifier.py -> maps reply -> one of 7 categories
                    (PROFILE_INCOMPLETE, EARNINGS_QUESTION, DEDUCTION_QUESTION,
                     PAYMENT_QUESTION, AGREEMENT_QUESTION, IRD_QUESTION,
                     GENERIC_INQUIRY)
    router.py    -> per category, builds:
                    - linkback URL (resume URL into S2/S3/S4/S5/S8 etc.)
                    - draft auto-reply (Tier-1 only, NEVER auto-sent)
                    - internal tag
                    - draft persists as OutboundDraft

NEVER AUTO-SENDS. Tier-1 approval queue mandatory.

Mirrors the autoreply.py classifier pattern (subject + body keyword scoring,
precondition-aware) but operates on FIESTA's own SQLAlchemy models, not SF.
"""
from __future__ import annotations

__all__ = [
    "classifier",
    "router",
    "models",
    "webhook",
]
