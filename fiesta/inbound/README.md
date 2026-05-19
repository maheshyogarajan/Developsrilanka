# fiesta.inbound — X5 Inbound Customer Reply Handler (Wave 3)

Cross-cutting Wave 3 build. When a FIESTA customer replies to an outbound
email (verification, S2/S3/S4 reminders, S5 deductions, payment receipts,
S8/S9 agreement, etc.) the provider's inbound webhook POSTs the parsed reply
to `POST /webhooks/inbound-email`. This module:

1. Verifies the webhook signature.
2. Parses and normalizes the payload (SendGrid Inbound Parse + Postmark
   shapes both supported).
3. Matches the sender to a FIESTA `User` by `from_address` (lowercased),
   falling back to threading via `In-Reply-To`.
4. Persists an `InboundEmail` row.
5. Classifies into one of 7 v1 categories:
   - `PROFILE_INCOMPLETE` → `/profile`
   - `EARNINGS_QUESTION` → `/earnings`
   - `DEDUCTION_QUESTION` → `/reduce-tax`
   - `PAYMENT_QUESTION` → `/billing` (+ billing-team route hint)
   - `AGREEMENT_QUESTION` → `/agreement`
   - `IRD_QUESTION` → Lanka.tax bookkeeping referral (v1 fallback;
     consultant-booking is v1.1)
   - `GENERIC_INQUIRY` → `/help` (+ staff-classify route hint)
6. Builds a draft auto-reply (template-driven, includes the linkback URL +
   FIESTA signature block).
7. Persists an `OutboundDraft` row in the Tier-1 approval queue.
8. **Never auto-sends.** `OutboundDraft.sent_at` stays `None` until staff
   approves through the queue UI.

## Files

| Path | Purpose |
|---|---|
| `fiesta/inbound/__init__.py` | Namespace + module docstring |
| `fiesta/inbound/classifier.py` | Keyword + precondition classifier (pydantic v2 result) |
| `fiesta/inbound/router.py` | Category → draft template + linkback + tag |
| `fiesta/inbound/models.py` | `InboundEmail` + `OutboundDraft` SQLAlchemy models (lazy-bound) + pydantic v2 DTOs |
| `fiesta/inbound/webhook.py` | Signature verification, payload normalizer, `process_inbound()` core, optional Flask route registration |
| `templates/inbound/staff_queue.html` | Staff-review UI (Bootstrap, matches existing FIESTA layout) |
| `tests/inbound/test_x5.py` | 15 tests (17 with parametrize expansion); all passing |

## Privacy guarantees

- Bodies for **unmatched senders** are truncated to 200 chars before
  persistence (the rest is dropped, never written to disk / DB).
- Matched-customer bodies are persisted in full so support staff can read
  the conversation context. Standard FIESTA RBAC applies to the staff queue.

## Wiring into FIESTA (app.py)

```python
from flask import Flask
from app import db
from fiesta.inbound.models import build_sqlalchemy_models
from fiesta.inbound.webhook import register_webhook_routes, WebhookDeps

# 1. Bind ORM models (once, at import time alongside other models.py classes)
_inbound_models = build_sqlalchemy_models(db)
InboundEmail = _inbound_models["InboundEmail"]
OutboundDraft = _inbound_models["OutboundDraft"]

# 2. Per-request dependency factory
def inbound_deps_factory() -> WebhookDeps:
    from models import User  # FIESTA user model

    def lookup_email(addr: str):
        u = User.query.filter_by(email=addr).first()
        return {"id": u.id, "email": u.email, "name": u.name} if u else None

    def lookup_thread(msg_id: str):
        # TODO Wave 3.1: query outbound_emails by message_id
        return None

    def ctx(cid: int):
        # TODO Wave 3.1: return {"profile_incomplete": ..., "payment_pending": ...}
        return {}

    def persist_inbound(dto):
        row = InboundEmail(**dto.model_dump(exclude={"id"}))
        db.session.add(row); db.session.commit()
        return row.id

    def persist_draft(dto):
        row = OutboundDraft(**dto.model_dump(exclude={"id"}))
        db.session.add(row); db.session.commit()
        return row.id

    return WebhookDeps(
        customer_lookup_fn=lookup_email,
        thread_lookup_fn=lookup_thread,
        customer_context_fn=ctx,
        persist_inbound_fn=persist_inbound,
        persist_draft_fn=persist_draft,
    )

register_webhook_routes(app, deps_factory=inbound_deps_factory)
```

## Tier-1 Staff Queue UI

The `/inbound-queue` route (to be added in app.py) renders
`templates/inbound/staff_queue.html`. Staff sees the inbound on the left
and the draft on the right with three buttons:

1. **Approve & send** — writes `approved_at` + `sent_at`, fires the
   outbound EmailMessage send through SendGrid.
2. **Save edits** — updates `draft_subject` / `draft_body`; remains in
   `ready_for_approval` state.
3. **Dismiss** — writes `dismissed_at` + `dismissed_reason`.

The send handler itself is **out of scope for X5** — it lives in the
existing FIESTA outbound email service.

## Hard constraints (enforced in code, not docs)

| Constraint | Enforcement |
|---|---|
| Never auto-send | `OutboundDraft.sent_at` is never written by `process_inbound`. Verified in `test_14`. |
| Privacy redaction for unmatched | `_redact_body_for_unmatched` is invoked when `customer_matched=False`. Verified in `test_02`. |
| Signature verification | `verify_signature()` returns False on mismatched signature. Verified in `test_11`. |
| Tier-1 only | `RoutingDecision.auto_send=False` and `needs_staff_review=True` are hardcoded. |

## v1.1 deferrals (documented, not built)

- AI fallback classifier for `GENERIC_INQUIRY` (call into existing Gemini /
  GPT path). Today: routed to staff with reasoning trail.
- Consultant booking flow for `IRD_QUESTION` (today: Lanka.tax bookkeeping
  referral URL).
- Inbound attachment handling (SendGrid Inbound Parse supports it; we drop
  attachments in v1 to keep the privacy / scope surface small).
- Per-customer auto-reply rate limit (today: relies on the natural
  Tier-1 staff bottleneck).
- Webhook idempotency by `message_id` (today: duplicate inbounds create
  duplicate rows; staff dedupes manually).

## Tests

```bash
cd C:/Users/mahes/fiesta_replit_source/DevelopSriLanka
python -m pytest tests/inbound/test_x5.py -v
```

15 logical tests, 17 with parametrize expansion. All passing in 0.23s.
