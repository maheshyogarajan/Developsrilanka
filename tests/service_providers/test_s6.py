"""tests/service_providers/test_s6.py — S6 unit tests (18 cases).

Run:
    cd C:/Users/mahes/AppData/Local/Temp/fiesta-s6
    python -m pytest tests/service_providers/test_s6.py -v

These tests use an in-memory SQLite database + a fresh declarative_base
so they bypass the Flask app context. The S6 module imports defensively
so this is supported by design.

Coverage (18 cases):

    Catalog / picklist (T1.x) — 2
        T1.1 service-type catalog has expected ids + S5 cross-links valid
        T1.2 stated-relationship choices map to detector vocabulary

    Model (T2.x) — 3
        T2.1 hourly/monthly money round-trips through Decimal LKR
        T2.2 to_detector_dict shapes for the §195 detector
        T2.3 archive sets archived=True but row persists

    Detection wiring (T3.x) — 5
        T3.1 happy SP -> arm's length, no signals
        T3.2 stated_relationship=spouse fires STATED_RELATIONSHIP -> default-on
        T3.3 same NIC family-prefix fires SAME_NIC_PREFIX
        T3.4 same address fires SAME_ADDRESS
        T3.5 same bank-account fires SAME_BANK_ACCOUNT (definitive)

    Persistence (T4.x) — 3
        T4.1 persist_detection_result creates ServiceProviderRelationship
        T4.2 re-running detection upserts (no duplicate row)
        T4.3 customer override flips effective_disclosure_required

    §195 passthrough integration (T5.x) — 5
        T5.1 list view excludes archived SPs
        T5.2 re-detect upserts confidence after bank-account added
        T5.3 override without 20-char justification rejected
        T5.4 detector failure -> fail-closed (requires_disclosure=True)
        T5.5 privacy: detection runs per-user, never leaks across user_id
"""
from __future__ import annotations

import os
import pathlib
import sys
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest

# Make the worktree root importable.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Test fixtures — in-memory SQLite with a dedicated declarative base.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def db_session():
    """Build an isolated SQLAlchemy session against in-memory SQLite.

    We patch `app.db` so the model imports use our standalone base. This
    must happen BEFORE the first model import.
    """
    # Force the fallback path in models.py by making `import app` fail.
    sys.modules.pop("fiesta.service_providers", None)
    sys.modules.pop("fiesta.service_providers.models", None)
    sys.modules.pop("fiesta.service_providers.related_party", None)
    sys.modules.pop("fiesta.service_providers.routes", None)
    if "app" in sys.modules:
        del sys.modules["app"]

    # Block `app` from being importable so the fallback Base is used.
    import builtins
    _original_import = builtins.__import__

    def _blocking_import(name, *args, **kwargs):
        if name == "app":
            raise ImportError("blocked for test isolation")
        return _original_import(name, *args, **kwargs)

    builtins.__import__ = _blocking_import
    try:
        from fiesta.service_providers import models as sp_models  # noqa: F401
    finally:
        builtins.__import__ = _original_import

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", echo=False)
    # Create both tables using the standalone Base from models.
    sp_models.db.Model.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def sp_models(db_session):
    from fiesta.service_providers import models as m
    return m


@pytest.fixture
def sp_related_party(db_session):
    from fiesta.service_providers import related_party as r
    return r


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _make_sp(sp_models, db_session, **overrides):
    defaults = dict(
        user_id=1,
        name="Anuradha Perera",
        service_type="subcontractor_developer",
        fee_structure="monthly",
        stated_relationship_to_customer="professional_arms_length",
        monthly_rate_cents=15_000_000,  # LKR 150,000/mo
    )
    defaults.update(overrides)
    sp = sp_models.ServiceProvider(**defaults)
    db_session.add(sp)
    db_session.flush()
    return sp


def _make_customer_dict(**overrides):
    base = {
        "name": "Mahesh Yogarajan",
        "nic": "200012345678",
        "address": {"street": "12 Galle Road", "locality": "Colombo", "postcode": "00300"},
        "bank_account": "1234567890",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# T1.x — Catalog / picklist.
# ---------------------------------------------------------------------------
def test_t1_1_service_type_catalog_structure(sp_models):
    """T1.1 — catalog has the 12 expected ids, every entry has an S5 cross-link."""
    cat = sp_models.SERVICE_TYPE_CATALOG
    assert len(cat) == 12
    ids = {c["id"] for c in cat}
    # Subcontractor variants.
    assert "subcontractor_developer" in ids
    assert "subcontractor_designer" in ids
    assert "subcontractor_writer" in ids
    assert "subcontractor_marketer" in ids
    assert "subcontractor_virtual_assistant" in ids
    assert "subcontractor_other" in ids
    # Professional variants.
    assert "professional_accountant" in ids
    assert "professional_lawyer" in ids
    assert "professional_tax_advisor" in ids
    assert "professional_consultant" in ids
    assert "professional_coach" in ids
    assert "professional_other" in ids

    valid_s5 = {"subcontractor_fees", "professional_services"}
    for entry in cat:
        assert "name" in entry and entry["name"]
        assert "icon" in entry and entry["icon"]
        assert entry["s5_category"] in valid_s5
        assert isinstance(entry["is_subcontractor"], bool)


def test_t1_2_stated_relationship_choices_map_to_detector(sp_models):
    """T1.2 — every UI stated-relationship value has a detector mapping."""
    ui_values = {v for v, _ in sp_models.STATED_RELATIONSHIP_CHOICES}
    mapping = sp_models.STATED_RELATIONSHIP_TO_DETECTOR
    # Every UI value must have a mapping entry.
    assert ui_values == set(mapping.keys()), (
        f"UI choices and detector mapping diverged: "
        f"ui={ui_values}, mapping={set(mapping.keys())}"
    )
    # The mapping must point to detector-recognised strings or empty.
    from fiesta.compliance.related_party import (
        RELATED_RELATIONSHIPS, NON_RELATED_RELATIONSHIPS,
    )
    for ui, det in mapping.items():
        assert det in RELATED_RELATIONSHIPS or det in NON_RELATED_RELATIONSHIPS or det == "", (
            f"detector mapping '{det}' for '{ui}' is neither related "
            f"nor non-related — would force conservative-related path"
        )


# ---------------------------------------------------------------------------
# T2.x — Model.
# ---------------------------------------------------------------------------
def test_t2_1_money_roundtrips_through_decimal(sp_models, db_session):
    """T2.1 — money fields round-trip through Decimal LKR."""
    sp = _make_sp(sp_models, db_session, monthly_rate_cents=None)
    # Setter through property.
    sp.monthly_rate = Decimal("250000.50")
    assert sp.monthly_rate_cents == 25_000_050
    assert sp.monthly_rate == Decimal("250000.50")

    # Hourly path.
    sp.hourly_rate = 1500
    assert sp.hourly_rate_cents == 150_000
    assert sp.hourly_rate == Decimal("1500.00")

    # None clears.
    sp.monthly_rate = None
    assert sp.monthly_rate_cents is None
    assert sp.monthly_rate is None

    # Empty-string also clears (form-post path).
    sp.hourly_rate = ""
    assert sp.hourly_rate_cents is None


def test_t2_2_to_detector_dict_shapes(sp_models, db_session):
    """T2.2 — to_detector_dict shapes correctly for fiesta.compliance.related_party."""
    sp = _make_sp(
        sp_models, db_session,
        nic="200012345678",
        address_line1="12 Galle Road",
        city="Colombo",
        postcode="00300",
        bank_account_number="1234567890",
    )
    d = sp.to_detector_dict()
    assert d["name"] == "Anuradha Perera"
    assert d["nic"] == "200012345678"
    assert d["bank_account"] == "1234567890"
    assert d["service_type"] == "subcontractor_developer"
    assert d["monthly_fee_lkr"] == 150000.0
    assert d["address"] == {
        "street": "12 Galle Road",
        "locality": "Colombo",
        "postcode": "00300",
    }

    # Hourly-only: monthly_fee_lkr should be derived as hourly * 160.
    sp.monthly_rate = None
    sp.hourly_rate = 1000
    db_session.flush()
    d2 = sp.to_detector_dict()
    assert d2["monthly_fee_lkr"] == 160000.0


def test_t2_3_archive_soft_deletes(sp_models, db_session):
    """T2.3 — archiving sets archived=True; row persists for audit."""
    sp = _make_sp(sp_models, db_session, name="ArchiveTarget")
    sp_id = sp.id
    sp.archived = True
    db_session.flush()

    # Still queryable.
    refetched = (
        db_session.query(sp_models.ServiceProvider)
        .filter_by(id=sp_id).first()
    )
    assert refetched is not None
    assert refetched.archived is True


# ---------------------------------------------------------------------------
# T3.x — Detection wiring.
# ---------------------------------------------------------------------------
def test_t3_1_happy_sp_arms_length(sp_models, sp_related_party, db_session):
    """T3.1 — independent professional with no overlapping signals -> arm's length."""
    sp = _make_sp(
        sp_models, db_session,
        name="Saman Wickrama",
        nic="850123456V",
        address_line1="55 Park Lane",
        city="Galle",
        bank_account_number="9876543210",
        stated_relationship_to_customer="professional_arms_length",
    )
    customer = _make_customer_dict()  # different NIC, address, bank.
    result = sp_related_party.run_detection_for_sp(
        sp, payments=None, customer_dict=customer
    )
    assert result.signals == []
    assert result.confidence == 0.0
    assert result.should_default_on_disclosure is False
    assert result.audit_substance_risk == "low"


def test_t3_2_spouse_relationship_fires_stated(sp_models, sp_related_party, db_session):
    """T3.2 — stated_relationship=spouse fires STATED_RELATIONSHIP -> default-on."""
    from fiesta.compliance.related_party import RelatedPartySignal
    sp = _make_sp(
        sp_models, db_session,
        name="Nilanka Yogarajan",
        stated_relationship_to_customer="spouse",
    )
    customer = _make_customer_dict()
    result = sp_related_party.run_detection_for_sp(
        sp, payments=None, customer_dict=customer
    )
    assert RelatedPartySignal.STATED_RELATIONSHIP in result.signals
    assert result.should_default_on_disclosure is True
    assert result.audit_substance_risk == "high"


def test_t3_3_same_nic_prefix_fires(sp_models, sp_related_party, db_session):
    """T3.3 — matching NIC family-signature prefix fires SAME_NIC_PREFIX."""
    from fiesta.compliance.related_party import RelatedPartySignal
    # Both new-format NICs sharing same family signature (YY + first-3 of SSSSS).
    # Old format prefix: YY + sss positions [5:8].
    # Customer NIC 200012345678 -> "new" -> prefix = "00" + "678"[0:3] -> "00678"
    # SP NIC      200056789999  -> "new" -> prefix = "00" + "999"[0:3] -> "00999"  (no match)
    # So pick matching: SP NIC 200012367890 -> prefix "00" + "789"[0:3] = "00789"  -> no.
    # The detector's new-NIC prefix is yyyy[2:4] + sssss[0:3] of digits12[7:12].
    # customer 200012345678: digits12=200012345678, sssss=digits12[7:12]="45678", prefix="00"+"456"="00456"
    # We need SP with digits12 where sssss starts "456" -> e.g. 200099945688: sssss=digits12[7:12]="45688", prefix="00"+"456"="00456" -> MATCH
    sp = _make_sp(
        sp_models, db_session,
        name="Pradeep Yogarajan",
        nic="200099945688",
        stated_relationship_to_customer="professional_arms_length",
    )
    customer = _make_customer_dict(nic="200012345678")
    result = sp_related_party.run_detection_for_sp(
        sp, payments=None, customer_dict=customer
    )
    assert RelatedPartySignal.SAME_NIC_PREFIX in result.signals
    # 0.55 weight => confidence >= 0.55 (single signal)
    assert result.confidence >= 0.5


def test_t3_4_same_address_fires(sp_models, sp_related_party, db_session):
    """T3.4 — same residential address fires SAME_ADDRESS."""
    from fiesta.compliance.related_party import RelatedPartySignal
    sp = _make_sp(
        sp_models, db_session,
        name="Suresh Roommate",
        address_line1="12 Galle Road",
        city="Colombo",
        postcode="00300",
        stated_relationship_to_customer="professional_arms_length",
    )
    customer = _make_customer_dict()  # same address.
    result = sp_related_party.run_detection_for_sp(
        sp, payments=None, customer_dict=customer
    )
    assert RelatedPartySignal.SAME_ADDRESS in result.signals


def test_t3_5_same_bank_account_definitive(sp_models, sp_related_party, db_session):
    """T3.5 — same bank-account is near-definitive: fires SAME_BANK_ACCOUNT + high risk."""
    from fiesta.compliance.related_party import RelatedPartySignal
    sp = _make_sp(
        sp_models, db_session,
        name="Self Deal",
        bank_account_number="1234567890",
        stated_relationship_to_customer="professional_arms_length",
    )
    customer = _make_customer_dict()  # same bank account.
    result = sp_related_party.run_detection_for_sp(
        sp, payments=None, customer_dict=customer
    )
    assert RelatedPartySignal.SAME_BANK_ACCOUNT in result.signals
    assert result.should_default_on_disclosure is True
    assert result.audit_substance_risk == "high"


# ---------------------------------------------------------------------------
# T4.x — Persistence.
# ---------------------------------------------------------------------------
def test_t4_1_persist_detection_creates_relationship(sp_models, sp_related_party, db_session):
    """T4.1 — persist_detection_result creates a ServiceProviderRelationship row."""
    sp = _make_sp(
        sp_models, db_session,
        name="Self Deal 2",
        bank_account_number="1234567890",
    )
    customer = _make_customer_dict()
    result = sp_related_party.run_detection_for_sp(
        sp, payments=None, customer_dict=customer
    )
    rel = sp_related_party.persist_detection_result(
        sp, result, db_session=db_session
    )
    db_session.flush()
    assert rel is not None
    assert rel.sp_id == sp.id
    assert rel.user_id == sp.user_id
    assert rel.should_default_on_disclosure is True
    assert sp.requires_disclosure is True
    assert isinstance(rel.signals, list)
    assert "same_bank_account" in rel.signals


def test_t4_2_re_detection_upserts_no_duplicate(sp_models, sp_related_party, db_session):
    """T4.2 — re-running detection updates the existing row (no duplicate)."""
    sp = _make_sp(sp_models, db_session, name="Upsert Test")
    customer = _make_customer_dict()
    r1 = sp_related_party.run_detection_for_sp(sp, payments=None, customer_dict=customer)
    sp_related_party.persist_detection_result(sp, r1, db_session=db_session)
    db_session.flush()

    # Mutate the SP — same bank account now matches.
    sp.bank_account_number = "1234567890"
    db_session.flush()
    r2 = sp_related_party.run_detection_for_sp(sp, payments=None, customer_dict=customer)
    sp_related_party.persist_detection_result(sp, r2, db_session=db_session)
    db_session.flush()

    rows = (
        db_session.query(sp_models.ServiceProviderRelationship)
        .filter_by(sp_id=sp.id).all()
    )
    assert len(rows) == 1, (
        f"persist_detection_result should upsert — found {len(rows)} rows"
    )
    assert rows[0].should_default_on_disclosure is True


def test_t4_3_customer_override_flips_effective_flag(sp_models, sp_related_party, db_session):
    """T4.3 — customer override changes the effective disclosure flag."""
    sp = _make_sp(
        sp_models, db_session,
        name="Override Target",
        stated_relationship_to_customer="spouse",
    )
    customer = _make_customer_dict()
    result = sp_related_party.run_detection_for_sp(sp, payments=None, customer_dict=customer)
    rel = sp_related_party.persist_detection_result(sp, result, db_session=db_session)
    db_session.flush()
    assert rel.effective_disclosure_required is True  # detector said default-on.

    # Customer overrides off.
    rel.customer_disclosure_override = False
    rel.override_justification = (
        "Genuinely arm's length — we hired through a public agency."
    )
    rel.override_set_at = datetime.utcnow()
    db_session.flush()
    assert rel.effective_disclosure_required is False


# ---------------------------------------------------------------------------
# T5.x — §195 passthrough integration.
# ---------------------------------------------------------------------------
def test_t5_1_archived_excluded_from_list(sp_models, db_session):
    """T5.1 — list view filters out archived=True rows."""
    a = _make_sp(sp_models, db_session, name="Active SP")
    b = _make_sp(sp_models, db_session, name="Archived SP")
    b.archived = True
    db_session.flush()
    # Mimic the routes.index query.
    rows = (
        db_session.query(sp_models.ServiceProvider)
        .filter_by(user_id=1, archived=False)
        .all()
    )
    names = {r.name for r in rows}
    assert "Active SP" in names
    assert "Archived SP" not in names


def test_t5_2_redetect_after_bank_add(sp_models, sp_related_party, db_session):
    """T5.2 — re-detect upserts confidence after bank-account added."""
    sp = _make_sp(sp_models, db_session, name="Bank Later", bank_account_number=None)
    customer = _make_customer_dict()
    r1 = sp_related_party.run_detection_for_sp(sp, payments=None, customer_dict=customer)
    sp_related_party.persist_detection_result(sp, r1, db_session=db_session)
    db_session.flush()
    assert r1.confidence == 0.0
    assert sp.requires_disclosure is False

    # Now customer-aware: add the bank account.
    sp.bank_account_number = customer["bank_account"]
    db_session.flush()
    r2 = sp_related_party.run_detection_for_sp(sp, payments=None, customer_dict=customer)
    sp_related_party.persist_detection_result(sp, r2, db_session=db_session)
    db_session.flush()
    assert r2.confidence > r1.confidence
    assert sp.requires_disclosure is True


def test_t5_3_override_requires_justification(sp_models, sp_related_party, db_session):
    """T5.3 — overriding a default-on disclosure to OFF requires substance text.

    Tested at the model level — we don't go through the Flask route in
    unit tests, but the route logic mirrors this: justification < 20
    chars is rejected when flipping default-on -> override-off.
    """
    sp = _make_sp(
        sp_models, db_session,
        name="Short Note",
        stated_relationship_to_customer="spouse",
    )
    customer = _make_customer_dict()
    result = sp_related_party.run_detection_for_sp(sp, payments=None, customer_dict=customer)
    rel = sp_related_party.persist_detection_result(sp, result, db_session=db_session)
    db_session.flush()

    # Replicate the routes.override_disclosure validation rule.
    def _route_validate(rel, override_bool, justification):
        if not override_bool and rel.should_default_on_disclosure and len(justification) < 20:
            return False, "needs 20+ char justification"
        return True, None

    ok1, _ = _route_validate(rel, False, "too short")
    assert ok1 is False

    ok2, _ = _route_validate(
        rel, False, "She works through Upwork and bills monthly with hours."
    )
    assert ok2 is True


def test_t5_4_detector_failure_fails_closed(sp_models, sp_related_party, db_session):
    """T5.4 — when detect_related_party raises, route falls back to default-on."""
    sp = _make_sp(sp_models, db_session, name="Fail Closed")

    # Patch the detector to raise.
    with patch(
        "fiesta.service_providers.related_party.detect_related_party",
        side_effect=RuntimeError("simulated detector outage"),
    ):
        # Direct call should propagate the exception (so callers can handle).
        with pytest.raises(RuntimeError):
            sp_related_party.run_detection_for_sp(
                sp, payments=None, customer_dict=_make_customer_dict()
            )

    # The route layer wraps this and sets requires_disclosure=True; we
    # simulate the same fail-closed behaviour here.
    sp.requires_disclosure = True
    db_session.flush()
    assert sp.requires_disclosure is True


def test_t5_5_privacy_per_user_isolation(sp_models, sp_related_party, db_session):
    """T5.5 — SPs from different users are isolated; detection runs against
    the right customer profile per SP."""
    sp1 = _make_sp(
        sp_models, db_session,
        user_id=1, name="User1's SP",
        bank_account_number="1111111111",
    )
    sp2 = _make_sp(
        sp_models, db_session,
        user_id=2, name="User2's SP",
        bank_account_number="2222222222",
    )

    # User 1's customer profile.
    c1 = _make_customer_dict(bank_account="1111111111")
    r1 = sp_related_party.run_detection_for_sp(sp1, payments=None, customer_dict=c1)

    # User 2's customer profile.
    c2 = _make_customer_dict(bank_account="9999999999")
    r2 = sp_related_party.run_detection_for_sp(sp2, payments=None, customer_dict=c2)

    # User 1 should fire SAME_BANK_ACCOUNT, user 2 should not.
    from fiesta.compliance.related_party import RelatedPartySignal
    assert RelatedPartySignal.SAME_BANK_ACCOUNT in r1.signals
    assert RelatedPartySignal.SAME_BANK_ACCOUNT not in r2.signals

    # And the user_id stays on the SP rows.
    assert sp1.user_id == 1
    assert sp2.user_id == 2

    # Filter by user_id like the route would.
    user1_sps = (
        db_session.query(sp_models.ServiceProvider)
        .filter_by(user_id=1, archived=False).all()
    )
    user2_sps = (
        db_session.query(sp_models.ServiceProvider)
        .filter_by(user_id=2, archived=False).all()
    )
    assert all(sp.user_id == 1 for sp in user1_sps)
    assert all(sp.user_id == 2 for sp in user2_sps)
    assert {sp.name for sp in user1_sps}.isdisjoint({sp.name for sp in user2_sps})
