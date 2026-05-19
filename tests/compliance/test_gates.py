"""Tests for fiesta.compliance.gate -- X6 cross-cutting compliance gates.

Layout
------
Unit tests (20): organised by screen. Each covers at least one pass + one
fire scenario. Some screens have more than 2 cases where multiple distinct
rules need coverage.

Integration tests (3): exercise the full S2->S14 customer journey across
multiple screens. They simulate realistic customer-state snapshots and assert
on cumulative GateResult shape per screen.

These tests do NOT touch the Flask app, the postgres DB, or any external
service. fiesta.compliance.gate is pure -- pytest can run them anywhere.

Env isolation: events.py / override.py write to a SQLite file. We point
FIESTA_COMPLIANCE_DB_PATH at a per-test tempfile so writes don't pollute
the dev DB.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest


# Ensure repo root is on sys.path so `import fiesta.compliance` resolves.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


from fiesta.compliance.gate import (  # noqa: E402
    DEDUCTION_RATIO_BLOCK_THRESHOLD,
    DEDUCTION_RATIO_WARN_THRESHOLD,
    SP_QUALIFICATION_TIERS,
    GateResult,
    gate_check,
)


@pytest.fixture(autouse=True)
def _isolated_compliance_db(monkeypatch):
    """Each test gets a fresh sqlite db so events/override modules don't share state."""
    with tempfile.NamedTemporaryFile(
        suffix=".sqlite3", delete=False
    ) as fh:
        path = fh.name
    monkeypatch.setenv("FIESTA_COMPLIANCE_DB_PATH", path)
    # Reset module-level caches between tests.
    import fiesta.compliance.events as ev_mod
    import fiesta.compliance.override as ov_mod
    ev_mod._TABLE_CREATED = False
    ov_mod._TABLE_CREATED = False
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


# ===========================================================================
# UNIT TESTS -- 20 cases across 10 screens
# ===========================================================================

# --- S2 signup (2 cases) ----------------------------------------------------

def test_s2_signup_pass_clean_email_strong_password():
    result = gate_check(
        "S2",
        {"email": "user@example.com", "password": "VeryStrongPw!23"},
        action="submit_signup",
    )
    assert result.passed is True
    assert result.warnings == []
    assert result.blocks == []


def test_s2_signup_blocks_malformed_email_and_warns_weak_password():
    result = gate_check(
        "S2",
        {"email": "not-an-email", "password": "abc"},
        action="submit_signup",
    )
    assert result.passed is False
    assert any(b["rule_id"] == "S2-EMAIL-FORMAT" for b in result.blocks)
    assert any(w["rule_id"] == "S2-PASSWORD-WEAK" for w in result.warnings)


# --- S3 profile (3 cases) ---------------------------------------------------

def test_s3_profile_pass_complete_data():
    result = gate_check(
        "S3",
        {
            "nic": "199012345678",  # 12-digit new format
            "address": {"line1": "12 Galle Rd", "city": "Colombo"},
            "earns_foreign_income": True,
            "foreign_income_source": "United States -- Upwork",
        },
        action="save_profile",
    )
    assert result.passed is True


def test_s3_profile_warns_malformed_nic():
    result = gate_check(
        "S3",
        {
            "nic": "ABC123",
            "address": {"line1": "12 Galle Rd", "city": "Colombo"},
        },
        action="save_profile",
    )
    assert result.passed is False
    assert any(w["rule_id"] == "S3-NIC-FORMAT" for w in result.warnings)


def test_s3_profile_warns_foreign_flag_without_source():
    result = gate_check(
        "S3",
        {
            "earns_foreign_income": True,
            "foreign_income_source": "",
            "address": {"line1": "x", "city": "y"},
        },
        action="save_profile",
    )
    assert any(
        w["rule_id"] == "S3-FOREIGN-INCOME-SOURCE-MISSING"
        for w in result.warnings
    )


# --- S4 connect-earnings (2 cases) ------------------------------------------

def test_s4_earnings_pass_when_statements_match_declarations():
    result = gate_check(
        "S4",
        {
            "declared_income_sources": ["foreign", "local"],
            "statements": [
                {"kind": "foreign_bank", "label": "Wise USD"},
                {"kind": "local_bank", "label": "Commercial Bank LKR"},
            ],
        },
        action="upload_statements",
    )
    assert result.passed is True


def test_s4_earnings_warns_when_foreign_statement_no_foreign_declaration():
    result = gate_check(
        "S4",
        {
            "declared_income_sources": ["local"],
            "statements": [{"kind": "foreign_bank", "label": "Payoneer"}],
        },
        action="upload_statements",
    )
    assert any(
        w["rule_id"] == "S4-EARNINGS-MISMATCH-FOREIGN" for w in result.warnings
    )


# --- S5 reduce-tax (2 cases) ------------------------------------------------

def test_s5_pass_when_no_planned_service_providers():
    result = gate_check(
        "S5",
        {"planned_service_providers": []},
        action="open_screen",
    )
    assert result.passed is True


def test_s5_warns_related_party_precheck_on_same_address():
    result = gate_check(
        "S5",
        {
            "nic": "199012345678",
            "address": {"line1": "12 Galle Rd", "city": "Colombo"},
            "full_name": "Aakash Wijesinghe",
            "planned_service_providers": [
                {
                    "full_name": "Aruna Wijesinghe",  # shared surname
                    "address": {"line1": "12 Galle Rd", "city": "Colombo"},
                }
            ],
        },
        action="open_screen",
    )
    assert any(
        w["rule_id"] == "S5-RELATED-PARTY-PRECHECK" for w in result.warnings
    )


# --- S6 service-providers (2 cases) -----------------------------------------

def test_s6_sp_pass_when_fee_within_tier_ceiling():
    result = gate_check(
        "S6",
        {
            "service_provider": {
                "qualification_tier": "mid",
                "monthly_fee_lkr": SP_QUALIFICATION_TIERS["mid"] - 1,
            }
        },
        action="add_sp",
    )
    assert result.passed is True


def test_s6_sp_warns_when_fee_above_market():
    fee = SP_QUALIFICATION_TIERS["junior"] * 3  # way above 1.5x ceiling
    result = gate_check(
        "S6",
        {
            "service_provider": {
                "qualification_tier": "junior",
                "monthly_fee_lkr": fee,
            }
        },
        action="add_sp",
    )
    assert any(w["rule_id"] == "S6-SP-FEE-ABOVE-MARKET" for w in result.warnings)


# --- S7 property-owner (2 cases) --------------------------------------------

def test_s7_rental_pass_when_within_index():
    result = gate_check(
        "S7",
        {"rental": {"monthly_rent_lkr": 50_000, "square_feet": 800}},
        action="add_rental",
    )
    assert result.passed is True


def test_s7_rental_warns_when_above_index():
    result = gate_check(
        "S7",
        {"rental": {"monthly_rent_lkr": 500_000, "square_feet": 400}},
        action="add_rental",
    )
    assert any(w["rule_id"] == "S7-RENTAL-ABOVE-INDEX" for w in result.warnings)


# --- S8 service-agreement (2 cases) -----------------------------------------

def test_s8_pass_when_unrelated_party():
    result = gate_check(
        "S8",
        {
            "nic": "199012345678",
            "address": {"line1": "12 Galle Rd"},
            "service_provider": {
                "nic": "851234567V",
                "address": {"line1": "47 Park Ave"},
            },
            "agreement": {"section_195_disclosure_enabled": False},
        },
        action="generate_agreement",
    )
    assert result.passed is True


def test_s8_blocks_override_when_related_party_signals_present():
    result = gate_check(
        "S8",
        {
            "nic": "199012345678",
            "address": {"line1": "12 Galle Rd"},
            "service_provider": {
                "nic": "199012345678",  # same nic = related-party
                "address": {"line1": "12 Galle Rd"},
            },
            "agreement": {
                "section_195_disclosure_enabled": False,
                "section_195_override_requested": True,
            },
        },
        action="generate_agreement",
    )
    assert any(b["rule_id"] == "S8-SECTION-195-OVERRIDE-DENIED" for b in result.blocks)


# --- S9 rental-agreement (2 cases) ------------------------------------------

def test_s9_rental_agreement_pass_when_rate_within_index():
    result = gate_check(
        "S9",
        {"agreement": {"monthly_rent_lkr": 70_000, "square_feet": 700}},
        action="generate_agreement",
    )
    assert result.passed is True


def test_s9_rental_agreement_warns_when_rate_above_index():
    result = gate_check(
        "S9",
        {"agreement": {"monthly_rent_lkr": 800_000, "square_feet": 400}},
        action="generate_agreement",
    )
    assert any(
        w["rule_id"] == "S9-RENTAL-AGREEMENT-ABOVE-INDEX"
        for w in result.warnings
    )


# --- S12 your-tax-bill (3 cases -- pass, warn, block) ----------------------

def test_s12_pass_when_deduction_ratio_low():
    result = gate_check(
        "S12",
        {"gross_income_lkr": 6_000_000, "total_deductions_lkr": 1_000_000},
        action="view_bill",
    )
    assert result.passed is True


def test_s12_warns_when_deduction_ratio_above_warn_threshold():
    gross = 6_000_000
    deductions = int(gross * (DEDUCTION_RATIO_WARN_THRESHOLD + 0.05))
    result = gate_check(
        "S12",
        {"gross_income_lkr": gross, "total_deductions_lkr": deductions},
        action="view_bill",
    )
    assert any(
        w["rule_id"] == "S12-DEDUCTION-RATIO-HIGH" for w in result.warnings
    )
    assert result.blocks == []


def test_s12_blocks_when_deduction_ratio_above_block_threshold():
    gross = 6_000_000
    deductions = int(gross * (DEDUCTION_RATIO_BLOCK_THRESHOLD + 0.05))
    result = gate_check(
        "S12",
        {"gross_income_lkr": gross, "total_deductions_lkr": deductions},
        action="view_bill",
    )
    assert any(
        b["rule_id"] == "S12-DEDUCTION-RATIO-EXCESSIVE" for b in result.blocks
    )


# --- S14 submit (2 cases) ---------------------------------------------------

def test_s14_pass_when_all_clear():
    result = gate_check(
        "S14",
        {
            "unresolved_prior_warnings": [],
            "service_agreements": [
                {"related_party_flag": False, "section_195_disclosure_enabled": False},
            ],
            "gross_income_lkr": 6_000_000,
            "total_deductions_lkr": 1_000_000,
        },
        action="submit",
    )
    assert result.passed is True


def test_s14_blocks_when_section_195_missing_on_related_party_agreement():
    result = gate_check(
        "S14",
        {
            "service_agreements": [
                {
                    "id": "SA-25-26-AW-A1B2",
                    "related_party_flag": True,
                    "section_195_disclosure_enabled": False,
                }
            ]
        },
        action="submit",
    )
    assert any(b["rule_id"] == "S14-SECTION-195-MISSING" for b in result.blocks)


# ===========================================================================
# INTEGRATION TESTS -- 3 end-to-end customer journeys
# ===========================================================================

def test_integration_happy_path_journey():
    """Customer with clean data passes every screen S2 -> S14."""
    customer = {
        "email": "aakash@example.com",
        "password": "SecurePass123!",
        "nic": "199012345678",
        "address": {"line1": "12 Galle Rd", "city": "Colombo"},
        "earns_foreign_income": True,
        "foreign_income_source": "United States -- Upwork",
        "declared_income_sources": ["foreign"],
        "statements": [{"kind": "foreign_bank", "label": "Wise USD"}],
        "full_name": "Aakash Wijesinghe",
        "planned_service_providers": [],
        "service_provider": {
            "qualification_tier": "mid",
            "monthly_fee_lkr": 150_000,
            "nic": "851234567V",
            "address": {"line1": "47 Park Ave"},
        },
        "rental": {"monthly_rent_lkr": 50_000, "square_feet": 600},
        "agreement": {
            "monthly_rent_lkr": 50_000,
            "square_feet": 600,
            "section_195_disclosure_enabled": False,
        },
        "gross_income_lkr": 6_000_000,
        "total_deductions_lkr": 1_500_000,  # 25% -- below warn threshold
        "unresolved_prior_warnings": [],
        "service_agreements": [
            {"related_party_flag": False, "section_195_disclosure_enabled": False}
        ],
    }
    for screen in ["S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S12", "S14"]:
        r = gate_check(screen, customer, action="navigate")
        assert r.passed is True, f"{screen} unexpectedly failed: warnings={r.warnings}, blocks={r.blocks}"


def test_integration_section_195_trigger_journey():
    """Customer adds an SP that shares NIC -- section-195 auto-enables, S8 warns + S14 passes IF disclosure on."""
    customer = {
        "email": "aakash@example.com",
        "password": "SecurePass123!",
        "nic": "199012345678",
        "address": {"line1": "12 Galle Rd", "city": "Colombo"},
        "full_name": "Aakash Wijesinghe",
        "service_provider": {
            "full_name": "Pradeep Wijesinghe",
            "nic": "199012345678",  # same NIC = related-party
            "qualification_tier": "mid",
            "monthly_fee_lkr": 150_000,
            "address": {"line1": "47 Park Ave"},
        },
        "agreement": {
            "section_195_disclosure_enabled": False,  # not yet enabled
            "section_195_override_requested": False,
        },
        "gross_income_lkr": 6_000_000,
        "total_deductions_lkr": 1_500_000,
    }
    s8 = gate_check("S8", customer, action="generate_agreement")
    # auto-enable warning fires
    assert any(w["rule_id"] == "S8-SECTION-195-AUTO-ENABLED" for w in s8.warnings)

    # Now simulate: customer accepted the auto-enablement (UI wrote it back)
    customer["service_agreements"] = [
        {
            "id": "SA-25-26-AW-A1B2",
            "related_party_flag": True,
            "section_195_disclosure_enabled": True,
        }
    ]
    s14 = gate_check("S14", customer, action="submit")
    # Should now pass on the section-195 axis
    assert not any(b["rule_id"] == "S14-SECTION-195-MISSING" for b in s14.blocks)


def test_integration_deduction_block_journey():
    """Customer with 65% deduction ratio is blocked at S12 + S14 unless override."""
    customer = {
        "email": "aakash@example.com",
        "password": "SecurePass123!",
        "gross_income_lkr": 6_000_000,
        "total_deductions_lkr": int(6_000_000 * 0.65),  # 65% -- above block threshold
        "unresolved_prior_warnings": [],
        "service_agreements": [],
    }
    s12 = gate_check("S12", customer, action="view_bill")
    assert any(b["rule_id"] == "S12-DEDUCTION-RATIO-EXCESSIVE" for b in s12.blocks)
    assert any("consultant" in r.lower() for r in s12.recommendations)

    s14 = gate_check("S14", customer, action="submit")
    assert any(b["rule_id"] == "S14-DEDUCTION-RATIO-FINAL" for b in s14.blocks)

    # With override flag, S14 passes the ratio gate (but not S12 by design --
    # S12 is informational; only the submit-time gate honours the override).
    customer["ceo_override_deduction_ratio"] = True
    s14_override = gate_check("S14", customer, action="submit")
    assert not any(
        b["rule_id"] == "S14-DEDUCTION-RATIO-FINAL" for b in s14_override.blocks
    )


# ===========================================================================
# Additional unit tests for module-level invariants
# ===========================================================================

def test_unknown_screen_returns_pass_with_trace():
    """A new screen_id with no rules yet returns a clean pass + a trace entry."""
    r = gate_check("S99", {"foo": "bar"}, action="any")
    assert r.passed is True
    assert any("NO-RULES" in t["rule_id"] for t in r.reasoning_trace)


def test_gate_result_is_pydantic_v2_model():
    """GateResult should be a pydantic v2 BaseModel (model_dump available)."""
    r = GateResult()
    dumped = r.model_dump()
    assert dumped["passed"] is True
    assert dumped["warnings"] == []
    assert dumped["blocks"] == []
