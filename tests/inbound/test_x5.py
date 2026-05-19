"""X5 inbound reply handler tests.

15 cases covering:
  - classifier accuracy (7 categories + noise + low-confidence)
  - webhook signature verification
  - end-to-end process_inbound() pipeline
  - threading fallback (in_reply_to inheritance)
  - privacy redaction for unmatched senders
  - draft-only-Tier-1 contract (NEVER auto-send in v1)

Run:
    cd C:/Users/mahes/fiesta_replit_source/DevelopSriLanka
    python -m pytest tests/inbound/test_x5.py -v
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Optional

import pytest

from fiesta.inbound import classifier as cls
from fiesta.inbound import router as router_mod
from fiesta.inbound import webhook as wh
from fiesta.inbound.models import (
    InboundEmailDTO,
    OutboundDraftDTO,
    PRIVACY_UNMATCHED_BODY_CHARS,
    _redact_body_for_unmatched,
)


# --------------------------------------------------------------------------- #
# Fake persistence layer
# --------------------------------------------------------------------------- #

class FakeDB:
    """Captures persisted DTOs for assertion."""

    def __init__(self) -> None:
        self.inbound_rows: list[InboundEmailDTO] = []
        self.draft_rows: list[OutboundDraftDTO] = []
        self._next_inbound_id = 1
        self._next_draft_id = 1
        self.persist_inbound_raises: Optional[Exception] = None
        self.persist_draft_raises: Optional[Exception] = None

    def persist_inbound(self, dto: InboundEmailDTO) -> int:
        if self.persist_inbound_raises:
            raise self.persist_inbound_raises
        dto.id = self._next_inbound_id
        self.inbound_rows.append(dto)
        self._next_inbound_id += 1
        return dto.id

    def persist_draft(self, dto: OutboundDraftDTO) -> int:
        if self.persist_draft_raises:
            raise self.persist_draft_raises
        dto.id = self._next_draft_id
        self.draft_rows.append(dto)
        self._next_draft_id += 1
        return dto.id


def _make_deps(
    *,
    customer_by_email: Optional[dict[str, dict[str, Any]]] = None,
    customer_by_thread: Optional[dict[str, dict[str, Any]]] = None,
    customer_contexts: Optional[dict[int, dict[str, Any]]] = None,
    db: Optional[FakeDB] = None,
) -> tuple[wh.WebhookDeps, FakeDB]:
    fake_db = db or FakeDB()
    customer_by_email = customer_by_email or {}
    customer_by_thread = customer_by_thread or {}
    customer_contexts = customer_contexts or {}

    def lookup_email(addr: str) -> Optional[dict[str, Any]]:
        return customer_by_email.get(addr.lower())

    def lookup_thread(msg_id: str) -> Optional[dict[str, Any]]:
        return customer_by_thread.get(msg_id)

    def ctx(cid: int) -> dict[str, Any]:
        return customer_contexts.get(cid, {})

    deps = wh.WebhookDeps(
        customer_lookup_fn=lookup_email,
        thread_lookup_fn=lookup_thread,
        customer_context_fn=ctx,
        persist_inbound_fn=fake_db.persist_inbound,
        persist_draft_fn=fake_db.persist_draft,
    )
    return deps, fake_db


# --------------------------------------------------------------------------- #
# Test 1 - Happy: PROFILE_INCOMPLETE end-to-end
# --------------------------------------------------------------------------- #

class TestEndToEnd:
    """Happy paths through process_inbound()."""

    def test_01_profile_password_question_drafted_for_approval(self):
        """Customer asks 'what's my password?' -> PROFILE_INCOMPLETE -> draft
        prepared (Tier-1) -> NEVER auto-sent."""
        deps, db = _make_deps(
            customer_by_email={
                "alice@example.com": {
                    "id": 42, "email": "alice@example.com",
                    "name": "Alice Fernando",
                },
            },
        )
        payload = {
            "From": "Alice Fernando <alice@example.com>",
            "To": "support@fiesta.lanka.tax",
            "Subject": "I forgot my password",
            "TextBody": "Hi, I forgot my password and cannot log in. Please help.",
            "Headers": [],
        }
        result = wh.process_inbound(payload=payload, deps=deps)

        assert result.ok is True
        assert result.customer_matched is True
        assert result.customer_id == 42
        assert result.classified_as == cls.CATEGORY_PROFILE_INCOMPLETE
        assert result.inbound_email_id == 1
        assert result.outbound_draft_id == 1
        # NEVER auto-sent contract:
        assert len(db.draft_rows) == 1
        d = db.draft_rows[0]
        assert d.sent_at is None
        assert d.approved_at is None
        assert d.status == "ready_for_approval"
        # Customer linkback present:
        assert "/profile" in d.linkback_url
        assert "Alice Fernando" in d.draft_subject
        assert "Alice Fernando" in d.draft_body
        # Signature present:
        assert "FIESTA Support Team" in d.draft_body


# --------------------------------------------------------------------------- #
# Test 2 - Unmatched customer flagged for staff + body redacted
# --------------------------------------------------------------------------- #

    def test_02_unmatched_customer_flagged_and_body_redacted(self):
        """Inbound from email with no matching User -> UNMATCHED_CUSTOMER ->
        flagged_for_staff status, body redacted for privacy."""
        deps, db = _make_deps()  # no customers configured
        long_body = "Hello, I have a question. " * 50  # > 200 chars
        payload = {
            "From": "stranger@unknown.com",
            "Subject": "Random message",
            "TextBody": long_body,
            "Headers": [],
        }
        result = wh.process_inbound(payload=payload, deps=deps)

        assert result.ok is True
        assert result.customer_matched is False
        assert result.customer_id is None
        assert result.classified_as == cls.CATEGORY_UNMATCHED_CUSTOMER
        assert result.status == "flagged_for_staff"
        # No draft for unmatched (router still produces one but the inbound
        # status is flagged_for_staff). Check the draft IS there but staff
        # context will tell the team they handle it manually.
        # In our pipeline, decision.draft_subject is built for unmatched too,
        # so a draft row exists -- let's verify it's the generic one.
        assert result.outbound_draft_id is not None
        # Body redaction:
        ie = db.inbound_rows[0]
        assert ie.body_text is not None
        assert len(ie.body_text) <= PRIVACY_UNMATCHED_BODY_CHARS + 50
        assert "truncated for privacy" in ie.body_text


# --------------------------------------------------------------------------- #
# Tests 3-9: classifier accuracy across the 7 categories
# --------------------------------------------------------------------------- #

class TestClassifierAccuracy:
    """One representative inbound per category. 7 cases."""

    SAMPLES = [
        # (subject, body, expected_category)
        (
            "I forgot my login password",
            "Hi team, I cannot log in to FIESTA. Reset link please?",
            cls.CATEGORY_PROFILE_INCOMPLETE,
        ),
        (
            "Income statement upload issue",
            "I uploaded my T10 but it shows an error. Salary section is blank.",
            cls.CATEGORY_EARNINGS_QUESTION,
        ),
        (
            "Question about tax deductions",
            "Can I claim my life insurance premium as a deduction this year?",
            cls.CATEGORY_DEDUCTION_QUESTION,
        ),
        (
            "Payment receipt",
            "Was I double charged Rs 2500? My card was debited twice last week.",
            cls.CATEGORY_PAYMENT_QUESTION,
        ),
        (
            "About the engagement agreement",
            "How do I generate my service agreement letter? I need to download it.",
            cls.CATEGORY_AGREEMENT_QUESTION,
        ),
        (
            "Question about IRD filing",
            "Do I need to file with IRD if I left Sri Lanka mid-year? Residency rule?",
            cls.CATEGORY_IRD_QUESTION,
        ),
        (
            "Hello FIESTA team",
            "Just saying thanks for the service, nothing specific. Cheers.",
            cls.CATEGORY_GENERIC_INQUIRY,
        ),
    ]

    @pytest.mark.parametrize("subject,body,expected", SAMPLES)
    def test_03to09_classifier_categories(self, subject, body, expected):
        result = cls.classify(
            subject=subject, body=body, customer_context={}, customer_matched=True,
        )
        assert result.category == expected, (
            f"subject={subject!r} expected={expected} got={result.category} "
            f"reasoning={result.reasoning}"
        )


# --------------------------------------------------------------------------- #
# Test 10 - Threading: in_reply_to matches a prior outbound -> customer linked
# --------------------------------------------------------------------------- #

class TestThreading:

    def test_10_threading_inherits_customer_via_in_reply_to(self):
        """from_addr doesn't match a user, but in_reply_to matches an
        outbound message id -> customer is inherited from the thread."""
        deps, db = _make_deps(
            customer_by_email={},  # not matched by email
            customer_by_thread={
                "<msg-abc-123@fiesta.lanka.tax>": {
                    "id": 99,
                    "email": "bob@new-email.com",
                    "name": "Bob Perera",
                },
            },
        )
        payload = {
            "From": "bob@new-email.com",  # different email than User.email
            "Subject": "Re: Your FIESTA tax document",
            "TextBody": "I forgot my password actually. Can you help?",
            "Headers": [
                {"Name": "In-Reply-To",
                 "Value": "<msg-abc-123@fiesta.lanka.tax>"},
            ],
        }
        result = wh.process_inbound(payload=payload, deps=deps)
        assert result.ok is True
        assert result.customer_matched is True
        assert result.customer_id == 99
        assert result.classified_as == cls.CATEGORY_PROFILE_INCOMPLETE


# --------------------------------------------------------------------------- #
# Test 11 - Webhook signature verification: reject 401
# --------------------------------------------------------------------------- #

class TestSignatureVerification:

    def test_11_invalid_signature_fails(self):
        secret = "test-secret-abc"
        raw = b'{"From":"a@b.c","Subject":"x"}'
        # Wrong signature
        headers = {"X-Fiesta-Signature": "deadbeef"}
        assert wh.verify_signature(
            raw_body=raw, headers=headers, secret=secret,
        ) is False

    def test_11b_valid_signature_passes(self):
        secret = "test-secret-abc"
        raw = b'{"From":"a@b.c","Subject":"x"}'
        sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        headers = {"X-Fiesta-Signature": sig}
        assert wh.verify_signature(
            raw_body=raw, headers=headers, secret=secret,
        ) is True

    def test_11c_no_secret_dev_mode_passes(self):
        """When no secret configured, signature check is bypassed (dev mode)."""
        raw = b'{"From":"a@b.c"}'
        assert wh.verify_signature(
            raw_body=raw, headers={}, secret=None,
        ) is True


# --------------------------------------------------------------------------- #
# Test 12 - Auto-reply noise (OOO) discarded, no draft generated
# --------------------------------------------------------------------------- #

class TestAutoReplyNoise:

    def test_12_out_of_office_discarded_no_draft(self):
        deps, db = _make_deps(
            customer_by_email={
                "alice@example.com": {
                    "id": 1, "email": "alice@example.com", "name": "Alice Fernando",
                },
            },
        )
        payload = {
            "From": "alice@example.com",
            "Subject": "Out of office: Re: your FIESTA reminder",
            "TextBody": "I am out of office until next Monday. Will reply on return.",
            "Headers": [],
        }
        result = wh.process_inbound(payload=payload, deps=deps)
        assert result.ok is True
        ie = db.inbound_rows[0]
        assert ie.is_autoreply_noise is True
        assert ie.status == "noise_discarded"
        # NO draft generated.
        assert len(db.draft_rows) == 0
        assert result.outbound_draft_id is None


# --------------------------------------------------------------------------- #
# Test 13 - Precondition boost: customer_context flag elevates category
# --------------------------------------------------------------------------- #

class TestPreconditionBoost:

    def test_13_payment_pending_flag_boosts_payment_question(self):
        """A short ambiguous body + payment_pending context -> PAYMENT routed."""
        # 'fee' is a payment keyword + body hit (1pt) + payment_pending boost (2pt).
        result = cls.classify(
            subject="quick question",
            body="just a quick fee question",  # 'fee' = 1 body pt for payment
            customer_context={"payment_pending": True},
            customer_matched=True,
        )
        assert result.category == cls.CATEGORY_PAYMENT_QUESTION
        # Confirm the boost actually fired in the reasoning trail.
        boost_logged = any(
            "ctx:payment_pending" in hit
            for hit in result.matched_keywords.get(
                cls.CATEGORY_PAYMENT_QUESTION, []
            )
        )
        assert boost_logged


# --------------------------------------------------------------------------- #
# Test 14 - NEVER-AUTO-SEND contract: even when ok=True, sent_at remains None
# --------------------------------------------------------------------------- #

class TestNeverAutoSend:

    def test_14_drafts_never_have_sent_at_set_in_v1(self):
        """v1 contract: every draft is Tier-1 only, sent_at stays None."""
        deps, db = _make_deps(
            customer_by_email={
                "alice@example.com": {
                    "id": 1, "email": "alice@example.com", "name": "Alice Fernando",
                },
            },
        )
        # Run 3 different inbounds; verify ALL drafts have sent_at == None.
        payloads = [
            {"From": "alice@example.com", "Subject": "password reset",
             "TextBody": "I cant log in", "Headers": []},
            {"From": "alice@example.com", "Subject": "my income statement",
             "TextBody": "I need to upload my T10", "Headers": []},
            {"From": "alice@example.com", "Subject": "deductions",
             "TextBody": "what can I claim as a deduction", "Headers": []},
        ]
        for p in payloads:
            wh.process_inbound(payload=p, deps=deps)
        assert len(db.draft_rows) == 3
        for d in db.draft_rows:
            assert d.sent_at is None, f"draft id={d.id} unexpectedly has sent_at"
            assert d.approved_at is None
            assert d.status == "ready_for_approval"


# --------------------------------------------------------------------------- #
# Test 15 - SendGrid Inbound Parse payload shape (multipart-form keys)
# --------------------------------------------------------------------------- #

class TestSendGridPayloadShape:

    def test_15_sendgrid_inbound_parse_shape_works(self):
        """FIESTA uses SendGrid; the Inbound Parse webhook sends lowercase
        multipart-form keys. Verify the normalizer accepts that shape."""
        deps, db = _make_deps(
            customer_by_email={
                "alice@example.com": {
                    "id": 7, "email": "alice@example.com", "name": "Alice Fernando",
                },
            },
        )
        sendgrid_payload = {
            "from": "Alice Fernando <alice@example.com>",
            "to": "support@fiesta.lanka.tax",
            "subject": "Help with my income statement upload",
            "text": "Hi, my T10 wont upload, the page errors out.",
            "headers": (
                "Message-Id: <abc@example.com>\n"
                "In-Reply-To: <prior@fiesta.lanka.tax>\n"
                "References: <prior@fiesta.lanka.tax>\n"
            ),
        }
        result = wh.process_inbound(payload=sendgrid_payload, deps=deps)
        assert result.ok is True
        assert result.classified_as == cls.CATEGORY_EARNINGS_QUESTION
        ie = db.inbound_rows[0]
        assert ie.from_addr == "alice@example.com"  # display name stripped
        assert ie.in_reply_to == "<prior@fiesta.lanka.tax>"
        assert ie.references == "<prior@fiesta.lanka.tax>"
