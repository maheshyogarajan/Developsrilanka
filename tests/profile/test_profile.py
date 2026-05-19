"""S3 Profile test suite — 15 cases covering validators, progressive disclosure,
auto-save behaviour, persona lock, and per-screen requirements.

Run: pytest tests/profile/test_profile.py -v
"""

from __future__ import annotations

import json

import pytest

from tests.profile.conftest import login_as


# ---------------------------------------------------------------------------
# 1. Validators — pure functions, no Flask needed
# ---------------------------------------------------------------------------


def test_01_nic_old_format_accepted():
    """Old NIC: 9 digits + V/X. Case-insensitive in; uppercase out."""
    from fiesta.profile.validators import validate_nic

    assert validate_nic("853310123V") == "853310123V"
    assert validate_nic("853310123v") == "853310123V"
    assert validate_nic("123456789X") == "123456789X"


def test_02_nic_new_format_accepted():
    """New NIC: exactly 12 digits."""
    from fiesta.profile.validators import validate_nic

    assert validate_nic("198533101230") == "198533101230"
    assert validate_nic(" 198533101230 ") == "198533101230"  # strip whitespace


def test_03_nic_rejects_malformed():
    """Garbage NIC strings must raise NICValidationError."""
    from fiesta.profile.validators import validate_nic, NICValidationError

    bad_inputs = ["", "abc", "12345", "853310123Y", "1234567890", "1234567890123"]
    for bad in bad_inputs:
        with pytest.raises(NICValidationError):
            validate_nic(bad)


def test_04_tin_optional_initially():
    """TIN is optional by default (required=False) for progressive disclosure."""
    from fiesta.profile.validators import validate_tin, TINValidationError

    assert validate_tin(None) is None
    assert validate_tin("") is None
    assert validate_tin("   ") is None
    assert validate_tin("123456789012") == "123456789012"

    # Required mode (S14 Submit) blocks empty TIN
    with pytest.raises(TINValidationError):
        validate_tin(None, required=True)
    with pytest.raises(TINValidationError):
        validate_tin("", required=True)


def test_05_tin_rejects_wrong_length():
    """TIN must be exactly 12 digits."""
    from fiesta.profile.validators import validate_tin, TINValidationError

    for bad in ["1234567890", "12345678901234", "12345678abcd"]:
        with pytest.raises(TINValidationError):
            validate_tin(bad)


def test_06_address_minimum_requires_line1_and_city():
    """An SL filing needs at least line1 + city. Missing either = error."""
    from fiesta.profile.validators import validate_address, AddressValidationError

    ok = validate_address("123 Galle Rd", "Colombo")
    assert ok["line1"] == "123 Galle Rd"
    assert ok["city"] == "Colombo"
    assert ok["country"] == "LK"

    with pytest.raises(AddressValidationError):
        validate_address("", "Colombo")
    with pytest.raises(AddressValidationError):
        validate_address("123 Galle Rd", "")
    with pytest.raises(AddressValidationError):
        validate_address(None, None)


# ---------------------------------------------------------------------------
# 2. Progressive disclosure — pure functions
# ---------------------------------------------------------------------------


def test_07_progressive_s4_requires_minimum():
    """S4 earnings needs NIC + address_line1 + city. TIN NOT required yet."""
    from fiesta.profile.progressive import required_for_screen

    empty_profile = {}
    missing = required_for_screen("S4_earnings", empty_profile)
    assert set(missing) == {"nic", "address_line1", "city"}

    half_profile = {"nic": "198533101230", "address_line1": "123 Galle Rd"}
    missing = required_for_screen("S4_earnings", half_profile)
    assert missing == ["city"]

    complete = {"nic": "198533101230", "address_line1": "123 Galle Rd", "city": "Colombo"}
    assert required_for_screen("S4_earnings", complete) == []


def test_08_progressive_s8_requires_more_than_s4():
    """S8 service-agreement needs S4 fields PLUS TIN + employment_type."""
    from fiesta.profile.progressive import required_for_screen

    s4_complete = {
        "nic": "198533101230",
        "address_line1": "123 Galle Rd",
        "city": "Colombo",
    }
    # S4 passes
    assert required_for_screen("S4_earnings", s4_complete) == []
    # S8 still needs TIN + employer
    s8_missing = required_for_screen("S8_service_agreement", s4_complete)
    assert "tin" in s8_missing
    assert "employment_type" in s8_missing


def test_09_progressive_unknown_screen_fail_open():
    """Unknown screen IDs return [] — never block the user on screens we forgot."""
    from fiesta.profile.progressive import required_for_screen

    assert required_for_screen("S999_nonexistent", {}) == []
    assert required_for_screen("", {}) == []


def test_10_progress_pct_calculation():
    """progress_pct returns 0 for empty and 100 for fully-filled."""
    from fiesta.profile.progressive import progress_pct, ALL_PROFILE_FIELDS

    assert progress_pct({}) == 0

    full = {}
    for tab_fields in ALL_PROFILE_FIELDS.values():
        for f in tab_fields:
            full[f] = "x"
    assert progress_pct(full) == 100

    # Partial — fill exactly half
    half = {}
    all_fields = [f for tf in ALL_PROFILE_FIELDS.values() for f in tf]
    for f in all_fields[: len(all_fields) // 2]:
        half[f] = "x"
    pct = progress_pct(half)
    assert 40 <= pct <= 60  # roughly half


# ---------------------------------------------------------------------------
# 3. ProfileFormPayload pydantic model
# ---------------------------------------------------------------------------


def test_11_persona_locked_to_self_in_v1():
    """Attempting to set persona != 'self' must be rejected. Default = 'self'."""
    from fiesta.profile.validators import ProfileFormPayload
    from pydantic import ValidationError

    # Default → self
    p = ProfileFormPayload()
    assert p.persona == "self"

    # Explicit 'self' → self
    p = ProfileFormPayload(persona="self")
    assert p.persona == "self"

    # Anything else → ValidationError
    for bad in ["caregiver", "employer", "admin", "company"]:
        with pytest.raises(ValidationError):
            ProfileFormPayload(persona=bad)


def test_12_payload_validates_employment_type():
    """employment_type must be one of the 4 allowed values."""
    from fiesta.profile.validators import ProfileFormPayload
    from pydantic import ValidationError

    for ok in ["employee", "contractor", "business_owner", "mix"]:
        p = ProfileFormPayload(employment_type=ok)
        assert p.employment_type == ok

    with pytest.raises(ValidationError):
        ProfileFormPayload(employment_type="ceo")


# ---------------------------------------------------------------------------
# 4. Flask route integration — auto-save + progress endpoint
# ---------------------------------------------------------------------------


def test_13_auto_save_partial_field(client, user_a, app):
    """POST {'nic': '...'} as JSON should save and return progress_pct."""
    login_as(client, user_a)

    resp = client.post(
        "/fiesta/profile",
        data=json.dumps({"nic": "198533101230"}),
        content_type="application/json",
        headers={"X-Auto-Save": "1"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["progress_pct"] > 0
    assert data["profile"]["nic"] == "198533101230"

    # A second partial save should NOT wipe the previously-saved NIC
    resp2 = client.post(
        "/fiesta/profile",
        data=json.dumps({"city": "Colombo"}),
        content_type="application/json",
        headers={"X-Auto-Save": "1"},
    )
    assert resp2.status_code == 200
    data2 = resp2.get_json()
    assert data2["ok"] is True
    assert data2["profile"]["nic"] == "198533101230"
    assert data2["profile"]["city"] == "Colombo"


def test_14_auto_save_rejects_bad_nic(client, user_a, app):
    """Malformed NIC via auto-save returns 422 with structured errors."""
    login_as(client, user_a)

    resp = client.post(
        "/fiesta/profile",
        data=json.dumps({"nic": "garbage"}),
        content_type="application/json",
        headers={"X-Auto-Save": "1"},
    )
    assert resp.status_code == 422
    data = resp.get_json()
    assert data["ok"] is False
    assert any("nic" in e["field"] for e in data["errors"])


def test_15_required_for_screen_endpoint(client, user_a, app):
    """GET /fiesta/profile/required-for/S4_earnings returns missing fields list."""
    login_as(client, user_a)

    # Empty profile — S4 should report 3 missing
    resp = client.get("/fiesta/profile/required-for/S4_earnings")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert set(data["missing"]) == {"nic", "address_line1", "city"}
    assert data["can_proceed"] is False

    # Save the 3 fields then re-check — should now be able to proceed
    client.post(
        "/fiesta/profile",
        data=json.dumps(
            {"nic": "198533101230", "address_line1": "123 Galle Rd", "city": "Colombo"}
        ),
        content_type="application/json",
        headers={"X-Auto-Save": "1"},
    )
    resp2 = client.get("/fiesta/profile/required-for/S4_earnings")
    data2 = resp2.get_json()
    assert data2["missing"] == []
    assert data2["can_proceed"] is True

    # Unknown screen 404
    resp3 = client.get("/fiesta/profile/required-for/S999_bogus")
    assert resp3.status_code == 404
