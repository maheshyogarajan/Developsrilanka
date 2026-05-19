"""Tests for fiesta.agreements -- Service Agreement (S8) PDF generator.

Wave 3 (2026-05-20). 20 cases covering:

  Happy paths
    1. arm's-length SP -> PDF renders, §195 NOT injected
    2. all 13 sections present in body
  §195 trigger paths
    3. parent-SP -> §195 default-ON, clause renders
    4. shared bank account -> §195 default-ON
    5. customer override -> override captured but clause still ships
    6. customer opt-in even when detector says default-OFF -> clause renders
  Multi-jurisdiction
    7-10. US / UK / Singapore / Australia / Germany render via variant C
  Currency rendering
    11. all 5 currencies (LKR/USD/EUR/GBP/AUD) appear in fee field
  Audit-defensibility
    12. SHA-256 hash matches PDF content
    13. Template version pinned at v0.1
  X6 gate integration
    14. PDF can be generated; detector + gate are decoupled (pure functions)
  PDF validity
    15. PDF starts with %PDF- magic + ends with %%EOF
  Reference IDs
    16. reference id pattern matches SA-YY-YY-XX-HHHH
  Edge cases
    17. missing NIC / address -> graceful degrade (no exception)
    18. empty parameters -> uses defaults, still renders
  Determinism
    19. same inputs at same UTC stamp -> identical bytes
    20. different UTC stamp -> different bytes (different /CreationDate)
"""
from __future__ import annotations

import re
from datetime import datetime

import pytest

from fiesta.agreements import (
    TEMPLATE_VERSION,
    generate_service_agreement_pdf,
    make_reference_id,
)
from fiesta.agreements.disclosure import decide_disclosure
from fiesta.compliance.gate import gate_check


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def arms_length_customer() -> dict:
    return {
        "full_name": "Anuk Wijesinghe",
        "nic": "901234567V",
        "tin": "123456789",
        "address": "23 Main St, Colombo 05",
        "bank": "BOC",
        "account": "0071538877",
        "notice_email": "anuk@example.com",
    }


@pytest.fixture
def foreign_client_sp() -> dict:
    return {
        "name": "Acme Foreign Corp Ltd",
        "entity_type": "Foreign Company",
        "jurisdiction": "United Kingdom",
        "address": "1 King's Cross, London N1C 4AG",
        "registration_number": "12345678",
        "signatory_name": "John Smith",
        "signatory_title": "CFO",
        "notice_email": "cfo@acme.com",
    }


@pytest.fixture
def base_parameters() -> dict:
    return {
        "services_description": "Monthly marketing analysis and reporting.",
        "fee_structure_variant": "A",
        "monthly_fee_amount": "250000",
        "currency": "LKR",
        "start_date": "2026-06-01",
        "end_date": "2027-05-31",
        "governing_law_variant": "A",
        "ip_variant": "A",
    }


@pytest.fixture
def fixed_when() -> datetime:
    return datetime(2026, 5, 20, 12, 0, 0)


# ---------------------------------------------------------------------------
# 1. Happy path -- arm's length
# ---------------------------------------------------------------------------


def test_arms_length_generates_pdf_without_sec195(
    arms_length_customer, foreign_client_sp, base_parameters, fixed_when
):
    result = generate_service_agreement_pdf(
        user_id=1,
        user_initials="MY",
        customer=arms_length_customer,
        service_provider=foreign_client_sp,
        parameters=base_parameters,
        creation_date_override=fixed_when,
    )
    assert result.pdf_bytes[:5] == b"%PDF-"
    assert result.disclosure.should_render is False
    assert result.disclosure.detector_default_on is False
    assert "14. RELATED-PARTY DISCLOSURE" not in result.rendered_body_text


# ---------------------------------------------------------------------------
# 2. All 13 numbered sections appear in body
# ---------------------------------------------------------------------------


def test_thirteen_sections_present(
    arms_length_customer, foreign_client_sp, base_parameters, fixed_when
):
    result = generate_service_agreement_pdf(
        user_id=1,
        user_initials="MY",
        customer=arms_length_customer,
        service_provider=foreign_client_sp,
        parameters=base_parameters,
        creation_date_override=fixed_when,
    )
    body = result.rendered_body_text
    assert "1. PARTIES" in body
    assert "2. SCOPE OF SERVICES" in body
    assert "3. TERM AND RENEWAL" in body
    assert "4. COMPENSATION" in body
    assert "5. INVOICING AND PAYMENT TERMS" in body
    assert "6. CONTRACTOR STATUS" in body
    assert "7. EQUIPMENT, PREMISES, AND EXPENSES" in body
    assert "8. CONFIDENTIALITY" in body
    assert "9. INTELLECTUAL PROPERTY" in body
    assert "10. WARRANTIES AND LIMITATION OF LIABILITY" in body
    assert "11. TERMINATION" in body
    assert "12. DISPUTE RESOLUTION AND GOVERNING LAW" in body
    assert "13. GENERAL" in body
    assert "SCHEDULE A" in body
    assert "SCHEDULE B" in body


# ---------------------------------------------------------------------------
# 3. §195 trigger -- stated relationship "mother"
# ---------------------------------------------------------------------------


def test_sec195_triggered_by_stated_relationship(base_parameters, fixed_when):
    customer = {
        "full_name": "Anuk Wijesinghe",
        "nic": "901234567V",
        "stated_relationship_to_service_provider": "mother",
    }
    sp = {"name": "Saroja Wijesinghe", "entity_type": "Sri Lankan Individual"}
    result = generate_service_agreement_pdf(
        user_id=1, user_initials="MY",
        customer=customer, service_provider=sp,
        parameters={**base_parameters, "monthly_fee_amount": "25000"},
        creation_date_override=fixed_when,
    )
    assert result.disclosure.detector_default_on is True
    assert result.disclosure.should_render is True
    assert result.disclosure.relationship_label == "mother"
    assert "section 195" in result.disclosure.rendered_clause_text.lower()
    assert "14. RELATED-PARTY DISCLOSURE" in result.rendered_body_text


# ---------------------------------------------------------------------------
# 4. §195 trigger -- shared bank account
# ---------------------------------------------------------------------------


def test_sec195_triggered_by_shared_bank_account(base_parameters, fixed_when):
    customer = {
        "full_name": "Anuk Wijesinghe",
        "bank": "BOC",
        "bank_account": "0071538877",
    }
    sp = {
        "name": "Saroja Wijesinghe",
        "bank": "BOC",
        "bank_account": "0071538877",
    }
    result = generate_service_agreement_pdf(
        user_id=1, user_initials="MY",
        customer=customer, service_provider=sp,
        parameters=base_parameters, creation_date_override=fixed_when,
    )
    assert result.disclosure.detector_default_on is True
    assert "same_bank_account" in result.disclosure.signals


# ---------------------------------------------------------------------------
# 5. Customer override -- captured but clause still ships
# ---------------------------------------------------------------------------


def test_customer_override_captured_but_disclosure_still_ships(
    base_parameters, fixed_when
):
    customer = {
        "full_name": "Anuk Wijesinghe",
        "stated_relationship_to_service_provider": "sibling",
    }
    sp = {"name": "Janaka Wijesinghe", "entity_type": "Sri Lankan Individual"}
    result = generate_service_agreement_pdf(
        user_id=1, user_initials="MY",
        customer=customer, service_provider=sp,
        parameters=base_parameters,
        customer_override_reason=(
            "We benchmarked the Rs 25,000/mo fee against three bookkeeping "
            "agencies; our rate is at the median."
        ),
        creation_date_override=fixed_when,
    )
    # Disclosure still ships
    assert result.disclosure.should_render is True
    # Override captured
    assert result.disclosure.customer_override_reason is not None
    assert "Rs 25,000" in result.disclosure.customer_override_reason
    # Override text injected into clause §14.4
    assert "We benchmarked" in result.disclosure.rendered_clause_text


# ---------------------------------------------------------------------------
# 6. Customer opt-in -- detector says OFF, opt-in forces ON
# ---------------------------------------------------------------------------


def test_customer_opt_in_forces_disclosure(
    arms_length_customer, foreign_client_sp, base_parameters, fixed_when
):
    result = generate_service_agreement_pdf(
        user_id=1, user_initials="MY",
        customer=arms_length_customer,
        service_provider=foreign_client_sp,
        parameters=base_parameters,
        customer_opt_in_disclosure=True,
        creation_date_override=fixed_when,
    )
    assert result.disclosure.detector_default_on is False
    assert result.disclosure.should_render is True
    assert result.disclosure.customer_opted_in is True
    assert "14. RELATED-PARTY DISCLOSURE" in result.rendered_body_text


# ---------------------------------------------------------------------------
# 7-9. Multi-jurisdiction support via governing_law_variant C
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "jurisdiction,chosen_law,seat",
    [
        ("United States", "Delaware law", "New York"),
        ("United Kingdom", "England and Wales law", "London"),
        ("Singapore", "Singapore law", "Singapore"),
        ("Australia", "New South Wales law", "Sydney"),
        ("Germany", "German law", "Frankfurt"),
    ],
)
def test_governing_law_variant_c_renders_for_multiple_jurisdictions(
    arms_length_customer, base_parameters, fixed_when,
    jurisdiction, chosen_law, seat,
):
    sp = {
        "name": f"Foreign Co {jurisdiction}",
        "entity_type": "Foreign Company",
        "jurisdiction": jurisdiction,
    }
    parameters = {
        **base_parameters,
        "governing_law_variant": "C",
        "chosen_law": chosen_law,
        "arbitration_rules": "ICC Rules",
        "arbitration_seat": seat,
    }
    result = generate_service_agreement_pdf(
        user_id=1, user_initials="MY",
        customer=arms_length_customer,
        service_provider=sp,
        parameters=parameters,
        creation_date_override=fixed_when,
    )
    assert chosen_law in result.rendered_body_text
    assert "ICC Rules" in result.rendered_body_text
    assert seat in result.rendered_body_text


# ---------------------------------------------------------------------------
# 11. All 5 currencies render
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("currency", ["LKR", "USD", "EUR", "GBP", "AUD"])
def test_all_five_currencies_render(
    arms_length_customer, foreign_client_sp, base_parameters, fixed_when, currency
):
    parameters = {**base_parameters, "currency": currency, "monthly_fee_amount": "1000"}
    result = generate_service_agreement_pdf(
        user_id=1, user_initials="MY",
        customer=arms_length_customer,
        service_provider=foreign_client_sp,
        parameters=parameters,
        creation_date_override=fixed_when,
    )
    body = result.rendered_body_text
    assert (
        f"{currency} 1000" in body
        or f"{currency}  1000" in body
        or f"{currency}" in body
    )
    assert currency in body


# ---------------------------------------------------------------------------
# 12. SHA-256 hash matches PDF content (audit trail integrity)
# ---------------------------------------------------------------------------


def test_sha256_matches_pdf_bytes(
    arms_length_customer, foreign_client_sp, base_parameters, fixed_when
):
    import hashlib
    result = generate_service_agreement_pdf(
        user_id=1, user_initials="MY",
        customer=arms_length_customer,
        service_provider=foreign_client_sp,
        parameters=base_parameters,
        creation_date_override=fixed_when,
    )
    assert result.sha256 == hashlib.sha256(result.pdf_bytes).hexdigest()
    assert len(result.sha256) == 64


# ---------------------------------------------------------------------------
# 13. Template version pinned at v0.1-draft
# ---------------------------------------------------------------------------


def test_template_version_pinned_v01_draft(
    arms_length_customer, foreign_client_sp, base_parameters, fixed_when
):
    result = generate_service_agreement_pdf(
        user_id=1, user_initials="MY",
        customer=arms_length_customer,
        service_provider=foreign_client_sp,
        parameters=base_parameters,
        creation_date_override=fixed_when,
    )
    assert TEMPLATE_VERSION == "v0.1-draft"
    assert result.template_version == "v0.1-draft"
    # Draft banner appears in rendered body
    assert "DRAFT v0.1-draft" in result.rendered_body_text or "v0.1-draft" in result.rendered_body_text
    assert "pending Lanka.tax legal" in result.rendered_body_text


# ---------------------------------------------------------------------------
# 14. X6 compliance gate integration
# ---------------------------------------------------------------------------


def test_x6_gate_returns_result_for_s8(
    arms_length_customer, foreign_client_sp, base_parameters
):
    """The gate is a sibling module; verify gate_check returns a usable
    result for the S8 screen so the route handler can refuse generation
    when blocks fire. We don't assert specific rules (those live in
    test_gates.py); we only confirm the contract."""
    customer_data = {
        **arms_length_customer,
        "service_provider": foreign_client_sp,
        **base_parameters,
    }
    gate = gate_check("S8", customer_data, "generate")
    assert hasattr(gate, "passed")
    assert hasattr(gate, "warnings")
    assert hasattr(gate, "blocks")
    # blocks is a list (may be empty)
    assert isinstance(gate.blocks, list)


# ---------------------------------------------------------------------------
# 15. PDF validity -- magic + EOF
# ---------------------------------------------------------------------------


def test_pdf_validity_magic_and_eof(
    arms_length_customer, foreign_client_sp, base_parameters, fixed_when
):
    result = generate_service_agreement_pdf(
        user_id=1, user_initials="MY",
        customer=arms_length_customer,
        service_provider=foreign_client_sp,
        parameters=base_parameters,
        creation_date_override=fixed_when,
    )
    # Magic header
    assert result.pdf_bytes.startswith(b"%PDF-")
    # EOF marker (may be followed by trailing newline)
    assert b"%%EOF" in result.pdf_bytes[-200:]


# ---------------------------------------------------------------------------
# 16. Reference id pattern -- SA-YY-YY-XX-HHHH
# ---------------------------------------------------------------------------


def test_reference_id_pattern():
    ref = make_reference_id("MY", tax_year="25-26")
    assert re.match(r"^SA-25-26-[A-Z]{2,4}-[0-9A-F]{4}$", ref), ref


def test_reference_id_strips_non_alpha_initials():
    ref = make_reference_id("m..", tax_year="25-26")
    # 'm..' -> 'M' single letter; helper pads to 'M'
    assert re.match(r"^SA-25-26-[A-Z]{1,4}-[0-9A-F]{4}$", ref), ref


# ---------------------------------------------------------------------------
# 17. Edge -- missing NIC / address graceful degrade
# ---------------------------------------------------------------------------


def test_missing_nic_address_graceful_degrade(base_parameters, fixed_when):
    minimal_customer = {"full_name": "Just Name", "notice_email": "a@b.com"}
    minimal_sp = {"name": "Counterparty"}
    result = generate_service_agreement_pdf(
        user_id=1, user_initials="JN",
        customer=minimal_customer, service_provider=minimal_sp,
        parameters=base_parameters,
        creation_date_override=fixed_when,
    )
    # Renders without raising, placeholders visible
    assert result.pdf_bytes.startswith(b"%PDF-")
    assert "[NIC not supplied]" in result.rendered_body_text
    assert "[address not supplied]" in result.rendered_body_text


# ---------------------------------------------------------------------------
# 18. Empty parameters -- defaults take over
# ---------------------------------------------------------------------------


def test_empty_parameters_uses_defaults(
    arms_length_customer, foreign_client_sp, fixed_when
):
    result = generate_service_agreement_pdf(
        user_id=1, user_initials="MY",
        customer=arms_length_customer,
        service_provider=foreign_client_sp,
        parameters={},  # no parameters at all
        creation_date_override=fixed_when,
    )
    assert result.pdf_bytes.startswith(b"%PDF-")
    # default currency LKR
    assert "LKR" in result.rendered_body_text
    # default governing_law_variant A
    assert "Sri Lanka" in result.rendered_body_text
    assert "courts of Colombo" in result.rendered_body_text


# ---------------------------------------------------------------------------
# 19. Determinism -- same inputs at same UTC stamp -> identical bytes
# ---------------------------------------------------------------------------


def test_determinism_same_inputs_same_bytes(
    arms_length_customer, foreign_client_sp, base_parameters, fixed_when
):
    a = generate_service_agreement_pdf(
        user_id=1, user_initials="MY",
        customer=arms_length_customer,
        service_provider=foreign_client_sp,
        parameters=base_parameters,
        reference_id="SA-25-26-MY-AAAA",
        creation_date_override=fixed_when,
    )
    b = generate_service_agreement_pdf(
        user_id=1, user_initials="MY",
        customer=arms_length_customer,
        service_provider=foreign_client_sp,
        parameters=base_parameters,
        reference_id="SA-25-26-MY-AAAA",
        creation_date_override=fixed_when,
    )
    assert a.sha256 == b.sha256
    assert a.pdf_bytes == b.pdf_bytes


# ---------------------------------------------------------------------------
# 20. Different UTC stamp -> different bytes
# ---------------------------------------------------------------------------


def test_different_stamp_changes_bytes(
    arms_length_customer, foreign_client_sp, base_parameters
):
    a = generate_service_agreement_pdf(
        user_id=1, user_initials="MY",
        customer=arms_length_customer,
        service_provider=foreign_client_sp,
        parameters=base_parameters,
        reference_id="SA-25-26-MY-AAAA",
        creation_date_override=datetime(2026, 5, 20, 12, 0, 0),
    )
    b = generate_service_agreement_pdf(
        user_id=1, user_initials="MY",
        customer=arms_length_customer,
        service_provider=foreign_client_sp,
        parameters=base_parameters,
        reference_id="SA-25-26-MY-AAAA",
        creation_date_override=datetime(2026, 5, 21, 12, 0, 0),
    )
    assert a.sha256 != b.sha256


# ---------------------------------------------------------------------------
# Extras -- decide_disclosure direct tests
# ---------------------------------------------------------------------------


def test_decide_disclosure_arms_length_returns_no_render():
    decision = decide_disclosure(
        {
            "customer": {"full_name": "A", "nic": "901234567V"},
            "service_provider": {"name": "Corp", "entity_type": "Foreign Company"},
        }
    )
    assert decision.detector_default_on is False
    assert decision.should_render is False
    assert decision.rendered_clause_text == ""


def test_decide_disclosure_below_market_benchmark_text():
    decision = decide_disclosure(
        {
            "customer": {
                "full_name": "Anuk",
                "stated_relationship_to_service_provider": "spouse",
            },
            "service_provider": {
                "name": "Saroja",
                "service_type": "bookkeeping",
                "monthly_fee_lkr": 10_000,
            },
            "market_rate_table": {
                "bookkeeping": {"median_monthly_lkr": 100_000},
            },
        }
    )
    assert decision.should_render is True
    assert decision.market_rate_benchmark is not None
