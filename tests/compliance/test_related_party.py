"""Tests for fiesta.compliance.related_party.

Wave 4 v1.0 (2026-05-20). Pure-function module -- no mocking required.

Run:
    cd C:/Users/mahes/fiesta_replit_source/DevelopSriLanka
    python -m pytest tests/compliance/test_related_party.py -v

Polarity reminder: overdetection is FINE, underdetection is the audit-
defensibility / Lanka.tax-operating-license risk. Tests therefore lean
toward "this MUST default ON" rather than "this MUST default OFF" -- the
DEFAULT-OFF cases are the small corner-set where we want to confirm we're
not generating noise on demonstrably arm's-length pairs.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

from fiesta.compliance import detect_related_party
from fiesta.compliance.related_party import (
    RelatedPartySignal,
    irregular_cadence,
    market_rate_band,
    same_address,
    same_bank_account,
    same_nic_prefix,
    same_surname,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def market_rates() -> dict[str, dict[str, float]]:
    """Load the v0.1 market-rate table from yaml -- keyed by casefold name."""
    path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "fiesta" / "compliance" / "market_rates_table.yaml"
    )
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    table = {}
    for k, v in raw["rates"].items():
        table[k.strip().casefold()] = v
    return table


# --------------------------------------------------------------------------- #
# Test cases -- the 12 from the dispatch + cross-format extras
# --------------------------------------------------------------------------- #


def test_01_father_son_same_old_nic_prefix_defaults_on() -> None:
    """Father-son SL old NIC prefix match => DEFAULT-ON.

    Father: 1965, day 045, registration district serial '123' -> 651234567V
        digits9 = 650451234; prefix = "65" + "123" = "65123"
    Son:   1992, day 100, same district serial '123'           -> 921001234V
        digits9 = 921001234; prefix = "92" + "123" = "92123"

    NIC prefixes DO differ on year (65 vs 92), so the helper SHOULD return
    False -- but a same-surname + same-address combo still defaults ON.
    This test instead constructs a same-PREFIX case (siblings, same year):
    """
    # Two siblings: same year-of-birth + same district -> shared prefix.
    customer = {
        "name": "Kumar Perera",
        "nic": "921001234V",        # 92|100|123|4
        "address": {"street": "12 Lake Road", "locality": "Colombo 05"},
    }
    sp = {
        "name": "Nimal Perera",
        "nic": "921501235V",        # 92|150|123|5 -- same year + same district 123
        "address": {"street": "12 Lake Road", "locality": "Colombo 05"},
    }
    result = detect_related_party(customer, sp)

    assert result.should_default_on_disclosure is True
    assert RelatedPartySignal.SAME_NIC_PREFIX in result.signals
    assert RelatedPartySignal.SAME_ADDRESS in result.signals
    assert RelatedPartySignal.SAME_SURNAME in result.signals
    assert result.audit_substance_risk in ("medium", "high")


def test_02_spouse_stated_relationship_defaults_on() -> None:
    """Customer-stated 'spouse' => always DEFAULT-ON regardless of other data."""
    customer = {
        "name": "Anjali Fernando",
        "stated_relationship_to_service_provider": "spouse",
        "address": {"street": "55 Galle Road", "locality": "Mount Lavinia"},
        "bank_account": "1234567890",
    }
    sp = {
        "name": "Ravi Silva",
        "address": {"street": "98 Marine Drive", "locality": "Colombo 06"},
        "bank_account": "0987654321",
    }
    result = detect_related_party(customer, sp)
    assert result.should_default_on_disclosure is True
    assert RelatedPartySignal.STATED_RELATIONSHIP in result.signals
    assert result.audit_substance_risk == "high"
    assert result.confidence >= 0.9


def test_03_roommates_same_address_only_defaults_off() -> None:
    """Same address ONLY, no other signals => moderate risk but should NOT
    be confidently labelled related-party. Single-signal SAME_ADDRESS scores
    0.40 which is above the 0.25 threshold => DEFAULT-ON.

    Polarity reminder: this is the FP we accept. Overdetection > under.
    """
    customer = {
        "name": "Asanka Bandara",
        "nic": "881234567V",
        "address": {"street": "7 Park Avenue", "locality": "Nugegoda"},
        "bank_account": "AAA111",
        "stated_relationship_to_service_provider": "none",
    }
    sp = {
        "name": "Roshani Jayasuriya",
        "nic": "920987651V",     # different year + different district serial
        "address": {"street": "7 Park Avenue", "locality": "Nugegoda"},
        "bank_account": "BBB222",
        "stated_relationship_to_service_provider": "",
    }
    result = detect_related_party(customer, sp)
    # We DELIBERATELY default ON for same-address (weight 0.40 > 0.25). The
    # client can switch off in UI if they're truly roommates. This is the
    # Risk B polarity decision -- coded as a test.
    assert result.should_default_on_disclosure is True
    assert RelatedPartySignal.SAME_ADDRESS in result.signals
    assert RelatedPartySignal.STATED_RELATIONSHIP not in result.signals
    assert result.audit_substance_risk == "medium"


def test_04_same_bank_account_defaults_on() -> None:
    """Same bank account => near-definitive DEFAULT-ON."""
    customer = {
        "name": "Dilshan Mendis",
        "bank_account": "BOC 0071-538-877",
    }
    sp = {
        "name": "Lanka Holdings (Pvt) Ltd",
        "bank_account": "BOC0071538877",   # same after normalisation
    }
    result = detect_related_party(customer, sp)
    assert result.should_default_on_disclosure is True
    assert RelatedPartySignal.SAME_BANK_ACCOUNT in result.signals
    assert result.audit_substance_risk == "high"
    assert result.confidence >= 0.9


def test_05_all_signals_false_defaults_off() -> None:
    """No signals fire => DEFAULT-OFF + low risk + empty signal list."""
    customer = {
        "name": "Suresh Kumar",
        "nic": "750123456V",
        "address": {"street": "100 Duplication Road", "locality": "Colombo 04"},
        "bank_account": "12340001",
        "stated_relationship_to_service_provider": "independent contractor",
    }
    sp = {
        "name": "Acme Software (Pvt) Ltd",
        "nic": "",
        "address": {"street": "200 Union Place", "locality": "Colombo 02"},
        "bank_account": "98760001",
        "stated_relationship_to_service_provider": "vendor",
    }
    result = detect_related_party(customer, sp)
    assert result.should_default_on_disclosure is False
    assert result.signals == []
    assert result.confidence == 0.0
    assert result.audit_substance_risk == "low"
    assert any("arm" in r for r in result.reasoning)


def test_06_above_market_rate_same_address_defaults_on_high(
    market_rates: dict[str, dict[str, float]],
) -> None:
    """Above-market rate + same address => DEFAULT-ON, HIGH risk banner."""
    customer = {
        "name": "Hiran Wickrema",
        "address": {"street": "5 Temple Lane", "locality": "Kandy"},
    }
    sp = {
        "name": "Hiran Wickrema Consulting",
        "address": {"street": "5 Temple Lane", "locality": "Kandy"},
        "service_type": "consulting",
        "monthly_fee_lkr": 800_000,   # > 2x median 180_000 = 360_000
    }
    result = detect_related_party(customer, sp, market_rate_table=market_rates)
    assert result.should_default_on_disclosure is True
    assert RelatedPartySignal.ABOVE_MARKET_RATE in result.signals
    assert RelatedPartySignal.SAME_ADDRESS in result.signals
    assert result.audit_substance_risk == "high"


def test_07_irregular_cadence_market_rate_ok_defaults_on(
    market_rates: dict[str, dict[str, float]],
) -> None:
    """Irregular payment cadence (claimed monthly but bursty) => DEFAULT-ON
    even when market-rate band is 'within'."""
    payments = [
        {"date": "2025-01-15", "amount_lkr": 80_000},
        {"date": "2025-02-12", "amount_lkr": 80_000},  # ~28 days
        {"date": "2025-07-10", "amount_lkr": 80_000},  # ~148-day gap
        {"date": "2025-08-08", "amount_lkr": 80_000},  # ~29 days
        {"date": "2025-12-30", "amount_lkr": 80_000},  # ~144-day gap
    ]
    customer = {"name": "Tharindu De Silva"}
    sp = {
        "name": "Independent Marketing Co",
        "service_type": "marketing",
        "monthly_fee_lkr": 120_000,  # within band
    }
    result = detect_related_party(
        customer, sp, payments=payments, market_rate_table=market_rates
    )
    assert result.should_default_on_disclosure is True
    assert RelatedPartySignal.IRREGULAR_CADENCE in result.signals
    assert RelatedPartySignal.ABOVE_MARKET_RATE not in result.signals
    assert RelatedPartySignal.BELOW_MARKET_RATE not in result.signals


def test_08_legitimate_independent_contractor_defaults_off(
    market_rates: dict[str, dict[str, float]],
) -> None:
    """Legitimate arm's-length contractor, all signals absent => DEFAULT-OFF."""
    payments = [
        {"date": "2025-01-31", "amount_lkr": 250_000},
        {"date": "2025-02-28", "amount_lkr": 250_000},
        {"date": "2025-03-31", "amount_lkr": 250_000},
        {"date": "2025-04-30", "amount_lkr": 250_000},
        {"date": "2025-05-31", "amount_lkr": 250_000},
    ]
    customer = {
        "name": "Mahesh Yogarajan",
        "nic": "750123456V",
        "address": {"street": "1A Independence Avenue", "locality": "Colombo 07"},
        "bank_account": "BOC-00001-001",
        "stated_relationship_to_service_provider": "independent contractor",
    }
    sp = {
        "name": "Globant (Pvt) Ltd",
        "nic": "",
        "address": {"street": "100 Galle Face", "locality": "Colombo 01"},
        "bank_account": "COM-99999-002",
        "stated_relationship_to_service_provider": "vendor",
        "service_type": "software dev",
        "monthly_fee_lkr": 250_000,
    }
    result = detect_related_party(
        customer, sp, payments=payments, market_rate_table=market_rates
    )
    assert result.should_default_on_disclosure is False
    assert result.signals == []
    assert result.audit_substance_risk == "low"


def test_09_cross_format_nic_same_family_defaults_on() -> None:
    """Old NIC (9+V) + New NIC (12) same family prefix => DEFAULT-ON.

    Old NIC '92|150|123|5' -> prefix '92' + '123' = '92123'
    New NIC '1992|150|12345' -> prefix '92' + '123' = '92123'   (last2 of YYYY + first3 of SSSSS)
    """
    customer = {"name": "Niranjan", "nic": "921501235V"}
    sp = {"name": "Sahan", "nic": "199215012345"}
    assert same_nic_prefix(customer["nic"], sp["nic"]) is True
    result = detect_related_party(customer, sp)
    assert result.should_default_on_disclosure is True
    assert RelatedPartySignal.SAME_NIC_PREFIX in result.signals


def test_10_mixed_script_names_graceful_degrade() -> None:
    """Sinhala + Latin transliteration of same surname => helper does NOT
    crash; same_surname returns False (we don't normalise Sinhala script);
    rest of detection proceeds without exception."""
    customer = {
        "name": "ප්රේම් පේරේරා",  # 'Prem Perera' in Sinhala script
        "address": {"street": "10 Flower Road", "locality": "Colombo 07"},
    }
    sp = {
        "name": "Lalith Perera",
        "address": {"street": "12 Flower Road", "locality": "Colombo 07"},
    }
    # Should not raise even though names use different scripts.
    result = detect_related_party(customer, sp)
    # Same address Lev-distance 2 ('10' vs '12' on otherwise identical line)
    # is within the < 3 threshold -> signal fires.
    assert RelatedPartySignal.SAME_ADDRESS in result.signals
    # Surname comparison gracefully returns False on script mismatch (we
    # don't transliterate); helper proceeded without crashing.
    assert RelatedPartySignal.SAME_SURNAME not in result.signals


def test_11_empty_payments_list_no_cadence_inference() -> None:
    """Empty / None payments => IRREGULAR_CADENCE never fires."""
    customer = {"name": "X"}
    sp = {"name": "Y"}
    r1 = detect_related_party(customer, sp, payments=None)
    r2 = detect_related_party(customer, sp, payments=[])
    r3 = detect_related_party(
        customer, sp, payments=[{"date": "2025-01-01", "amount_lkr": 1}]
    )
    for r in (r1, r2, r3):
        assert RelatedPartySignal.IRREGULAR_CADENCE not in r.signals


def test_12_missing_fields_skip_gracefully() -> None:
    """Inputs may have any subset of fields missing -- helper must not raise
    and must only fire signals it can confidently compute."""
    customer = {"name": "Solo Person"}
    sp: dict = {}
    result = detect_related_party(customer, sp)
    assert result.should_default_on_disclosure is False
    assert result.signals == []
    # Edge case: non-dict input
    bad = detect_related_party("not-a-dict", sp)  # type: ignore[arg-type]
    assert bad.should_default_on_disclosure is False
    assert "not dicts" in " ".join(bad.reasoning)


# --------------------------------------------------------------------------- #
# Unit tests for the public sub-helpers (used by the orchestrator above but
# worth pinning independently for refactor safety).
# --------------------------------------------------------------------------- #


def test_same_nic_prefix_handles_invalid_input() -> None:
    assert same_nic_prefix("", "") is False
    assert same_nic_prefix("abc", "def") is False
    assert same_nic_prefix("750123456V", "750123456X") is True
    # Cross-format same prefix verified in test_09; here ensure non-match
    assert same_nic_prefix("750123456V", "199915012345") is False


def test_same_bank_account_normalises() -> None:
    assert same_bank_account("BOC-0071-538-877", "BOC0071538877") is True
    assert same_bank_account("", "BOC0071538877") is False
    assert same_bank_account(None, "x") is False  # type: ignore[arg-type]


def test_same_surname_filters_short_tokens() -> None:
    # 'de' is too short -> should NOT match on it.
    assert same_surname("Suresh de Silva", "Anil de Costa") is False
    assert same_surname("Suresh Perera", "Anil Perera") is True
    # Surname-first community: 'Wickramasinghe Sunil Perera' vs 'Wickramasinghe Lalith Costa'
    assert same_surname(
        "Wickramasinghe Sunil Perera",
        "Wickramasinghe Lalith Costa",
    ) is True


def test_same_address_postcode_mismatch_blocks() -> None:
    a = {"street": "10 Lotus Road", "locality": "Colombo 07", "postcode": "00700"}
    b = {"street": "10 Lotus Road", "locality": "Colombo 07", "postcode": "00800"}
    assert same_address(a, b) is False


def test_market_rate_band_thresholds(
    market_rates: dict[str, dict[str, float]],
) -> None:
    # median = 180_000 LKR for 'consulting'
    assert market_rate_band("consulting", 90_000, market_rates) == "within"
    assert market_rate_band("consulting", 89_999, market_rates) == "below"
    assert market_rate_band("consulting", 360_001, market_rates) == "above"
    assert market_rate_band("consulting", 360_000, market_rates) == "within"
    assert market_rate_band("unknown service", 100_000, market_rates) == "unknown"
    assert market_rate_band("consulting", 0, market_rates) == "unknown"


def test_irregular_cadence_needs_three_points() -> None:
    assert irregular_cadence([
        {"date": "2025-01-01"},
        {"date": "2025-02-01"},
    ]) is False
    regular = [
        {"date": "2025-01-01"},
        {"date": "2025-02-01"},
        {"date": "2025-03-01"},
        {"date": "2025-04-01"},
    ]
    assert irregular_cadence(regular) is False
    bursty = [
        {"date": "2025-01-01"},
        {"date": "2025-01-15"},  # 14 days
        {"date": "2025-08-01"},  # 198 days
        {"date": "2025-08-05"},  # 4 days
    ]
    assert irregular_cadence(bursty) is True
