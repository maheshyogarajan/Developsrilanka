"""Tests for fiesta.delivery_ops.autoreply.

Wave 2c port of the Lanka.tax inbound-routing + F-code template pattern. No
live SF / Supabase / Telegram calls -- everything mocked via DI seams.

Run:
    cd C:/Users/mahes/fiesta_replit_source/DevelopSriLanka
    python -m pytest fiesta/delivery_ops/tests/test_autoreply.py -v
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
from copy import deepcopy

import pytest

from fiesta.delivery_ops import autoreply as ar


# --------------------------------------------------------------------------- #
# Catalog fixture -- minimal stand-in for the 55-action live catalog.
# --------------------------------------------------------------------------- #

_FIXTURE_CATALOG = {
    "version": "test-fixture",
    "actions": [
        {
            "f_code": "F3.0",
            "f_code_name": "missing_documents_composite",
            "stage": 3,
            "stage_name": "Doc Collection",
            "priority": 1,
            "default_action": "send_doc_request_composite",
            "action_type": "composite",
            "approval_tier": 1,
            "preconditions": [
                "profile_complete", "has_email", "payment_status_paid",
                "at_least_one_doc_missing",
            ],
            "cooldown_days": 7,
            "description": "Client missing income documents; send composite request.",
        },
        {
            "f_code": "F1.1",
            "f_code_name": "no_tin_no_application",
            "stage": 1,
            "stage_name": "Registration",
            "priority": 1,
            "action_type": "template_send",
            "sf_template_developer_name": "tin_required",
            "approval_tier": 1,
            "preconditions": ["has_email", "no_tin"],
            "cooldown_days": 14,
            "description": "Client has no TIN; ask them to apply.",
        },
        {
            "f_code": "F4",
            "f_code_name": "payment_due_pre_filing",
            "stage": 4,
            "stage_name": "Payment",
            "priority": 1,
            "action_type": "template_send",
            "sf_template_developer_name": "F4_payment_due",
            "approval_tier": 1,
            "preconditions": ["payment_status_not_paid", "profile_complete"],
            "cooldown_days": 5,
            "description": "All docs in, awaiting payment to file.",
        },
        {
            "f_code": "F5",
            "f_code_name": "confirm_computation",
            "stage": 5,
            "stage_name": "Confirmation",
            "priority": 1,
            "action_type": "template_send",
            "sf_template_developer_name": "F5_confirm_computation",
            "approval_tier": 1,
            "preconditions": ["payment_status_paid", "tax_computation_draft_ready"],
            "cooldown_days": 7,
            "description": "Computation draft ready, awaiting client confirmation.",
        },
        {
            "f_code": "F-AL-REQUEST",
            "f_code_name": "asset_liability_only_outstanding",
            "stage": 6,
            "stage_name": "Filing",
            "priority": 1,
            "action_type": "email_send",
            "approval_tier": 1,
            "preconditions": [
                "payment_status_paid", "profile_complete",
                "income_ready_for_computation",
                "customer_not_declared_al_complete",
            ],
            "cooldown_days": 7,
            "description": "All income docs received; A&L still outstanding.",
        },
    ],
    "conflict_resolution": {
        "rule": "earlier_stage_precedence",
    },
}


@pytest.fixture
def catalog_file(tmp_path):
    """Write the fixture catalog to a temp file; return its path."""
    p = tmp_path / "test_catalog.json"
    p.write_text(json.dumps(_FIXTURE_CATALOG), encoding="utf-8")
    # Clear the module cache so each test gets the fresh fixture.
    ar._CATALOG_CACHE.clear()
    yield str(p)
    ar._CATALOG_CACHE.clear()


# --------------------------------------------------------------------------- #
# Fake SF client -- records queries + scripts responses
# --------------------------------------------------------------------------- #

class FakeSFClient:
    """SF client mock that returns scripted records based on the SOQL pattern."""

    def __init__(self, *, case_record=None, taxfile_record=None,
                 prior_outbound_count=0, cooldown_records=None,
                 resolver_change_resp=None,
                 query_raises=None):
        self._case_record = case_record
        self._taxfile_record = taxfile_record
        self._prior_count = prior_outbound_count
        self._cooldown_records = cooldown_records or []
        self._rc_resp = resolver_change_resp or {"id": "rc_fake_id_001"}
        self._query_raises = query_raises
        self.queries = []
        self.posts = []

    def query(self, soql):
        self.queries.append(soql)
        if self._query_raises:
            raise self._query_raises
        # Route based on which object is in the SOQL.
        if "FROM Case " in soql:
            return {
                "records": [self._case_record] if self._case_record else [],
                "totalSize": 1 if self._case_record else 0,
            }
        if "FROM Tax_File__c" in soql:
            return {
                "records": [self._taxfile_record] if self._taxfile_record else [],
                "totalSize": 1 if self._taxfile_record else 0,
            }
        if "FROM EmailMessage" in soql:
            return {"records": [], "totalSize": self._prior_count}
        if "FROM Document_Follow_up_Reminder__c" in soql:
            return {
                "records": self._cooldown_records,
                "totalSize": len(self._cooldown_records),
            }
        return {"records": [], "totalSize": 0}

    def post(self, sobject, body):
        self.posts.append((sobject, body))
        if sobject == "Resolver_Change__c":
            return dict(self._rc_resp)
        return {"id": f"{sobject.lower()}_fake_id"}


# --------------------------------------------------------------------------- #
# Default fixtures
# --------------------------------------------------------------------------- #

def _make_case_record(*, inbound_subject="Question about my documents",
                      inbound_body="Hi, what documents do I need to send?",
                      contact_id="003fake0000contactA",
                      customer_id="a0Ffake0000customerA",
                      client_name="John Wickramasinghe",
                      client_email="john@example.com",
                      inbound_summary=""):
    return {
        "Id": "500fake0000caseAAAA",
        "Subject": inbound_subject,
        "ContactId": contact_id,
        "Incoming_email__c": inbound_body,
        "Incoming_Email_Summary__c": inbound_summary,
        "Contact": {"Name": client_name, "Email": client_email},
        "Customer__c": customer_id,
        "Customer__r": {
            "Name": client_name,
            "Full_Name_of_Applicant_English__c": client_name,
        },
    }


def _make_taxfile_record(*, tax_file_id="a0Ufake0000taxFile1A",
                         profile_status=5, payment_status="Paid",
                         tax_year="2025/2026"):
    return {
        "Id": tax_file_id,
        "Tax_Year__c": tax_year,
        "Customers_profile_filling_status__c": profile_status,
        "Purchased_package_ID__r": {"Payment_Status__c": payment_status},
    }


def _preflight_no_engagement(contact_id, tax_file_id):
    return {
        "defer_to_staff": False,
        "reason": "no active email cases",
        "active_case_ids": [],
        "active_case_count": 0,
    }


def _preflight_defer(contact_id, tax_file_id):
    return {
        "defer_to_staff": True,
        "reason": "2 active email case(s) in progress",
        "active_case_ids": ["500case1AAAAA", "500case2BBBBB"],
        "active_case_count": 2,
    }


# --------------------------------------------------------------------------- #
# TESTS -- classify_and_draft
# --------------------------------------------------------------------------- #

class TestClassifyAndDraft:

    def test_happy_path_F3_0_classification_renders_draft(self, catalog_file):
        """Client asks 'what documents do I need' -> F3.0 -> draft rendered with gates passed."""
        sf = FakeSFClient(
            case_record=_make_case_record(
                inbound_subject="What documents do I need",
                inbound_body="Hi team, please tell me what documents to upload.",
            ),
            taxfile_record=_make_taxfile_record(),  # paid + profile complete
        )
        result = ar.classify_and_draft(
            case_id="500fake0000caseAAAA",
            sf_client=sf,
            preflight_fn=_preflight_no_engagement,
            catalog_path=catalog_file,
            vip_email_set=set(),
        )
        assert result["ok"], f"expected ok, got errors={result['errors']} gates_failed={result['gates_failed']} reasoning={result['reasoning']}"
        assert result["classified_f_code"] == "F3.0"
        assert result["draft_subject"]
        assert result["draft_body"]
        assert "John Wickramasinghe" in result["draft_subject"]
        assert "John Wickramasinghe" in result["draft_body"]
        assert "https://www.lanka.tax/login" in result["draft_body"]
        assert "+94 71 314 0000" in result["draft_body"]
        assert "tax@lanka.tax" in result["draft_body"]
        assert "lanka_tax_login_link" in result["gates_passed"]
        assert "ceo_signature_block" in result["gates_passed"]
        assert "client_full_name_in_greeting" in result["gates_passed"]
        assert "client_full_name_in_subject" in result["gates_passed"]
        assert result["gates_failed"] == []
        assert result["approval_required"] is True
        assert result["sent"] is None  # NEVER auto-sends

    def test_defer_to_staff_when_active_case_in_last_14d(self, catalog_file):
        """Step 3e active engagement guard -> no draft, defer_to_staff=True."""
        sf = FakeSFClient(
            case_record=_make_case_record(),
            taxfile_record=_make_taxfile_record(),
        )
        result = ar.classify_and_draft(
            case_id="500fake0000caseAAAA",
            sf_client=sf,
            preflight_fn=_preflight_defer,
            catalog_path=catalog_file,
            vip_email_set=set(),
        )
        assert result["defer_to_staff"] is True
        assert result["draft_subject"] is None
        assert result["draft_body"] is None
        assert result["classified_f_code"] is None
        assert result["ok"] is False
        assert "active email case" in result["reasoning"]

    def test_cooldown_active_blocks_draft(self, catalog_file):
        """Prior send within cooldown window -> cooldown_active=True, no rendered draft."""
        sf = FakeSFClient(
            case_record=_make_case_record(
                inbound_subject="documents",
                inbound_body="What docs do I need to upload",
            ),
            taxfile_record=_make_taxfile_record(),
            cooldown_records=[{
                "Id": "a0Wcooldown001A",
                "CreatedDate": "2026-05-15T10:00:00.000+0000",
                "Reminder_Template__c": "f3_0_doc_request",
            }],
        )
        result = ar.classify_and_draft(
            case_id="500fake0000caseAAAA",
            sf_client=sf,
            preflight_fn=_preflight_no_engagement,
            catalog_path=catalog_file,
            vip_email_set=set(),
        )
        assert result["cooldown_active"] is True
        assert result["classified_f_code"] == "F3.0"
        assert result["ok"] is False
        assert "cooldown_active" in result["reasoning"]

    def test_no_match_returns_no_f_code(self, catalog_file):
        """Inbound with no matching keywords -> classified_f_code=None, needs_human."""
        sf = FakeSFClient(
            case_record=_make_case_record(
                inbound_subject="Random topic",
                inbound_body="My cat is sick and I need vacation advice please.",
            ),
            taxfile_record=_make_taxfile_record(),
        )
        result = ar.classify_and_draft(
            case_id="500fake0000caseAAAA",
            sf_client=sf,
            preflight_fn=_preflight_no_engagement,
            catalog_path=catalog_file,
            vip_email_set=set(),
        )
        assert result["classified_f_code"] is None
        assert result["draft_subject"] is None
        assert result["draft_body"] is None
        assert result["ok"] is False
        assert "no_keyword_matches" in result["reasoning"]

    def test_dry_run_does_not_write(self, catalog_file):
        """dry_run=True never writes anywhere; classify_and_draft has no writes anyway."""
        sf = FakeSFClient(
            case_record=_make_case_record(
                inbound_subject="documents",
                inbound_body="what docs to send",
            ),
            taxfile_record=_make_taxfile_record(),
        )
        result = ar.classify_and_draft(
            case_id="500fake0000caseAAAA",
            sf_client=sf,
            preflight_fn=_preflight_no_engagement,
            catalog_path=catalog_file,
            vip_email_set=set(),
            dry_run=True,
        )
        # classify_and_draft itself never POSTs -- only submit_for_tier1_approval does.
        assert sf.posts == []
        assert result["dry_run"] is True

    def test_vip_override_forces_human_review(self, catalog_file):
        """VIP contact -> vip_override=True, no auto-draft."""
        sf = FakeSFClient(
            case_record=_make_case_record(
                client_email="vip@example.com",
                inbound_subject="documents",
                inbound_body="what docs",
            ),
            taxfile_record=_make_taxfile_record(),
        )
        result = ar.classify_and_draft(
            case_id="500fake0000caseAAAA",
            sf_client=sf,
            preflight_fn=_preflight_no_engagement,
            catalog_path=catalog_file,
            vip_email_set={"vip@example.com"},
        )
        assert result["vip_override"] is True
        assert result["draft_subject"] is None
        assert result["classified_f_code"] is None
        assert result["ok"] is False
        assert "vip_contact_force_human_review" in result["reasoning"]

    def test_returning_client_personalization(self, catalog_file):
        """Returning client (>=3 prior outbound emails) gets welcome-back personalization."""
        sf = FakeSFClient(
            case_record=_make_case_record(
                inbound_subject="documents",
                inbound_body="what docs do I need this year",
            ),
            taxfile_record=_make_taxfile_record(),
            prior_outbound_count=5,
        )
        result = ar.classify_and_draft(
            case_id="500fake0000caseAAAA",
            sf_client=sf,
            preflight_fn=_preflight_no_engagement,
            catalog_path=catalog_file,
            vip_email_set=set(),
        )
        assert result["ok"]
        # Returning-client personalization line.
        assert "Welcome back" in result["draft_body"]

    def test_first_name_only_fails_full_name_gate(self, catalog_file):
        """Client with only a first name -> full-name gates fail, no draft."""
        sf = FakeSFClient(
            case_record=_make_case_record(
                client_name="Mahesh",  # single token, should fail
                inbound_subject="documents",
                inbound_body="what docs",
            ),
            taxfile_record=_make_taxfile_record(),
        )
        result = ar.classify_and_draft(
            case_id="500fake0000caseAAAA",
            sf_client=sf,
            preflight_fn=_preflight_no_engagement,
            catalog_path=catalog_file,
            vip_email_set=set(),
        )
        assert result["ok"] is False
        assert "client_full_name_in_greeting" in result["gates_failed"]
        assert "client_full_name_in_subject" in result["gates_failed"]
        # Reasoning explains the render refusal.
        assert "render_refused" in result["reasoning"]

    def test_payment_question_routes_to_F4(self, catalog_file):
        """'How much do I owe' on unpaid client -> F4 wins."""
        sf = FakeSFClient(
            case_record=_make_case_record(
                inbound_subject="payment",
                inbound_body="How much do I owe and how do I pay?",
            ),
            taxfile_record=_make_taxfile_record(payment_status="Unpaid"),
        )
        result = ar.classify_and_draft(
            case_id="500fake0000caseAAAA",
            sf_client=sf,
            preflight_fn=_preflight_no_engagement,
            catalog_path=catalog_file,
            vip_email_set=set(),
        )
        assert result["classified_f_code"] == "F4"
        assert result["ok"]

    def test_missing_inbound_body_returns_error(self, catalog_file):
        """Case with no inbound_body and no summary -> error, no draft."""
        case = _make_case_record(inbound_body="", inbound_summary="")
        sf = FakeSFClient(
            case_record=case,
            taxfile_record=_make_taxfile_record(),
        )
        result = ar.classify_and_draft(
            case_id="500fake0000caseAAAA",
            sf_client=sf,
            preflight_fn=_preflight_no_engagement,
            catalog_path=catalog_file,
            vip_email_set=set(),
        )
        assert result["ok"] is False
        assert any("no inbound body" in e for e in result["errors"])


# --------------------------------------------------------------------------- #
# TESTS -- submit_for_tier1_approval
# --------------------------------------------------------------------------- #

class TestSubmitForTier1Approval:

    def _make_good_draft(self, case_id="500fake0000caseAAAA"):
        return {
            "ok": True,
            "case_id": case_id,
            "customer_id": "a0Fcust",
            "contact_id": "003contact",
            "tax_file_id": "a0Utf",
            "client_name": "Test Client Name",
            "classified_f_code": "F3.0",
            "classified_action_name": "missing_documents_composite",
            "draft_subject": "Missing Documents Composite - Test Client Name - 2025/2026",
            "draft_body": (
                "Dear Test Client Name,\n\nPlease upload your docs at "
                "https://www.lanka.tax/login\n\nLanka.tax Team\nHotline: +94 71 314 0000\n"
                "Mobile: +94 71 460 0000\nEmail: tax@lanka.tax\n"
            ),
            "gates_passed": ["client_full_name_in_greeting", "client_full_name_in_subject",
                             "ceo_signature_block", "lanka_tax_login_link"],
            "gates_failed": [],
            "approval_required": True,
            "sent": None,
        }

    def test_dry_run_does_not_write_anything(self):
        sf = FakeSFClient()
        supabase_calls = []
        telegram_calls = []

        def fake_supabase(table, row):
            supabase_calls.append((table, row))
            return {"ok": True, "id": "aq_fake_id", "error": None}

        def fake_telegram(chat_id, text):
            telegram_calls.append((chat_id, text))
            return {"ok": True, "msg_id": 42, "error": None}

        result = ar.submit_for_tier1_approval(
            case_id="500fake0000caseAAAA",
            draft=self._make_good_draft(),
            sf_client=sf,
            supabase_writer=fake_supabase,
            telegram_sender=fake_telegram,
            dry_run=True,
        )
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert result["would_write"]["approval_queue_row"]["action_type"] == "email_send"
        assert result["would_write"]["resolver_change_row"]["Status__c"] == "pending"
        assert sf.posts == []
        assert supabase_calls == []
        assert telegram_calls == []
        assert result["resolver_change_id"] is None
        assert result["approval_queue_row_id"] is None
        assert result["telegram_msg_id"] is None

    def test_live_writes_resolver_change_first(self):
        sf = FakeSFClient(resolver_change_resp={"id": "rc_live_001"})
        supabase_calls = []
        telegram_calls = []

        def fake_supabase(table, row):
            supabase_calls.append((table, row))
            return {"ok": True, "id": "aq_live_001", "error": None}

        def fake_telegram(chat_id, text):
            telegram_calls.append((chat_id, text))
            return {"ok": True, "msg_id": 99, "error": None}

        result = ar.submit_for_tier1_approval(
            case_id="500fake0000caseAAAA",
            draft=self._make_good_draft(),
            sf_client=sf,
            supabase_writer=fake_supabase,
            telegram_sender=fake_telegram,
            dry_run=False,
        )
        assert result["ok"] is True
        assert result["resolver_change_id"] == "rc_live_001"
        assert result["approval_queue_row_id"] == "aq_live_001"
        assert result["telegram_msg_id"] == 99
        # SF post happened with Resolver_Change__c BEFORE anything else (Rule P1).
        assert sf.posts[0][0] == "Resolver_Change__c"
        assert sf.posts[0][1]["Status__c"] == "pending"
        assert sf.posts[0][1]["Reversible__c"] is False
        # Supabase write happened.
        assert supabase_calls[0][0] == "approval_queue"
        # Telegram went to the CEO chat.
        assert telegram_calls[0][0] == ar.CEO_TELEGRAM_CHAT_ID
        assert "AUTOREPLY DRAFT" in telegram_calls[0][1]

    def test_refuses_failed_draft(self):
        bad = self._make_good_draft()
        bad["ok"] = False
        result = ar.submit_for_tier1_approval(
            case_id="500fake0000caseAAAA",
            draft=bad,
            sf_client=FakeSFClient(),
            dry_run=True,
        )
        assert result["ok"] is False
        assert any("draft.ok=False" in e for e in result["errors"])

    def test_refuses_case_id_mismatch(self):
        draft = self._make_good_draft(case_id="500WRONG0000")
        result = ar.submit_for_tier1_approval(
            case_id="500RIGHT0000",
            draft=draft,
            sf_client=FakeSFClient(),
            dry_run=True,
        )
        assert result["ok"] is False
        assert any("case_id mismatch" in e for e in result["errors"])

    def test_resolver_change_failure_aborts(self):
        sf = FakeSFClient(resolver_change_resp={"error": True, "message": "Schema mismatch"})
        result = ar.submit_for_tier1_approval(
            case_id="500fake0000caseAAAA",
            draft=self._make_good_draft(),
            sf_client=sf,
            supabase_writer=lambda t, r: {"ok": True, "id": "x", "error": None},
            telegram_sender=lambda c, t: {"ok": True, "msg_id": 1, "error": None},
            dry_run=False,
        )
        assert result["ok"] is False
        assert any("resolver_change_failed" in e for e in result["errors"])


# --------------------------------------------------------------------------- #
# TESTS -- pure helpers
# --------------------------------------------------------------------------- #

class TestHelpers:
    def test_validate_full_name_accepts_first_and_last(self):
        assert ar._validate_full_name("John Smith") is True
        assert ar._validate_full_name("Mahesh Yogarajan") is True
        assert ar._validate_full_name("A B") is False  # too short total
        assert ar._validate_full_name("Mahesh") is False
        assert ar._validate_full_name("") is False
        assert ar._validate_full_name(None) is False

    def test_deadline_status_sentence_renders_for_known_year(self):
        s = ar._deadline_status_sentence("2025/2026")
        assert "2025/2026" in s
        assert "2026-11-30" in s

    def test_deadline_status_sentence_handles_unknown(self):
        s = ar._deadline_status_sentence("garbage")
        assert "deadline" in s.lower()

    def test_norm_collapses_whitespace_and_lowercases(self):
        assert ar._norm("  Hello   WORLD  ") == "hello world"
        assert ar._norm("") == ""
        assert ar._norm(None) == ""
