"""tests/tax_bill/test_query_consolidation.py — x9 tier-b query budget guard.

The /tax-bill route used to fan out into ~20 SQL queries on cache miss:
    6 baseline + 2*N_sp + 4*N_rental
(via per-table SQLAlchemy ORM lookups in fiesta.tax_bill.aggregator).

x9 tier-b (2026-05-24) collapses that into:
    1. SELECT FROM vw_tax_bill_context (profile + dedns + SPs + rentals)
    2. IncomeEntry SELECT inside income_summary_for_tax_year
    3. (conditional) RemittanceEntry SELECT — short-circuited per bca49b2
       when no remits exist
Total: ≤ 3 queries on cache miss; 0 on cache hit (within 60s TTL).

These tests assert that contract WITHOUT requiring a live database. They
stub the SQLAlchemy session, count how many times each per-table legacy
loader is called, and verify the view path is taken.

Run:
    python -m pytest tests/tax_bill/test_query_consolidation.py -v
"""
from __future__ import annotations

import pathlib
import sys
from decimal import Decimal

import pytest

# Make the worktree root importable.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Fake DB / session plumbing.
# ---------------------------------------------------------------------------


class _FakeRowMapping(dict):
    """Mimic SQLAlchemy RowMapping (has .get + dict-style access)."""
    pass


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _FakeSession:
    """Captures every execute() call and returns a canned row."""
    def __init__(self, canned_row=None, raise_on_execute=False):
        self.canned_row = canned_row
        self.raise_on_execute = raise_on_execute
        self.executes: list = []

    def execute(self, stmt, params=None):
        self.executes.append((str(stmt), params))
        if self.raise_on_execute:
            raise RuntimeError("simulated: view does not exist")
        if self.canned_row is None:
            return _FakeResult(None)
        return _FakeResult(_FakeRowMapping(self.canned_row))


# ---------------------------------------------------------------------------
# Test 1: view returns row -> legacy per-table loaders are NOT called
# ---------------------------------------------------------------------------


def test_view_path_skips_legacy_loaders(monkeypatch):
    """When vw_tax_bill_context returns a row, _load_profile /
    _load_deductions / _load_service_providers / _load_property_and_rentals
    must NOT be called. They are the source of the N+1 query fan-out."""
    from fiesta.tax_bill import aggregator

    canned_row = {
        "nic": "123456789V",
        "tin": "TIN999",
        "city": "Colombo",
        "employment_type": "contractor",
        "user_name": "Pytest User",
        "deductions": [
            {
                "category_id": "internet_telecom",
                "estimated_lkr_cents": 18000000,
                "actual_lkr_cents": 18000000,
                "evidence_status": "collected",
                "notes": None,
            },
        ],
        "service_providers": [],
        "rentals": [],
    }

    fake_session = _FakeSession(canned_row=canned_row)

    # Stub app.db.session to our fake. _load_via_view does `from app import db`
    # inside the function body, so swapping sys.modules['app'] is sufficient.
    class _FakeDb:
        session = fake_session
    import types
    fake_app = types.ModuleType("app")
    fake_app.db = _FakeDb()
    monkeypatch.setitem(sys.modules, "app", fake_app)

    # Track legacy-loader calls.
    legacy_calls = {"profile": 0, "deductions": 0, "sps": 0, "rentals": 0}

    def _spy_profile(inputs):
        legacy_calls["profile"] += 1
    def _spy_deductions(inputs):
        legacy_calls["deductions"] += 1
    def _spy_sps(inputs):
        legacy_calls["sps"] += 1
    def _spy_rentals(inputs):
        legacy_calls["rentals"] += 1
    def _spy_earnings(inputs):
        pass  # earnings stays separate by design; out of scope here.

    monkeypatch.setattr(aggregator, "_load_profile", _spy_profile)
    monkeypatch.setattr(aggregator, "_load_deductions", _spy_deductions)
    monkeypatch.setattr(aggregator, "_load_service_providers", _spy_sps)
    monkeypatch.setattr(aggregator, "_load_property_and_rentals", _spy_rentals)
    monkeypatch.setattr(aggregator, "_load_earnings", _spy_earnings)

    inputs = aggregator.assemble_tax_inputs(user_id=42, tax_year="2025-26")

    # Legacy loaders MUST NOT have been called.
    assert legacy_calls == {"profile": 0, "deductions": 0, "sps": 0, "rentals": 0}, (
        f"legacy loaders called when view path succeeded: {legacy_calls}"
    )

    # And only ONE execute against the view.
    assert len(fake_session.executes) == 1, (
        f"expected 1 view query, got {len(fake_session.executes)}"
    )

    # Sanity: data flowed through.
    assert inputs.nic == "123456789V"
    assert inputs.profile_complete is True
    assert inputs.deductions_total_lkr == Decimal("180000.00")
    assert "vw_tax_bill_context" in inputs.sources_loaded


# ---------------------------------------------------------------------------
# Test 2: view missing -> legacy fallback fires (no regression)
# ---------------------------------------------------------------------------


def test_view_missing_falls_back_to_legacy_loaders(monkeypatch):
    """When the view query raises (e.g. view not yet migrated), the legacy
    per-table loaders MUST run so behaviour is identical to pre-tier-b."""
    from fiesta.tax_bill import aggregator
    import types

    fake_session = _FakeSession(raise_on_execute=True)
    class _FakeDb:
        session = fake_session
    fake_app = types.ModuleType("app")
    fake_app.db = _FakeDb()
    monkeypatch.setitem(sys.modules, "app", fake_app)

    legacy_calls = {"profile": 0, "deductions": 0, "sps": 0, "rentals": 0}
    monkeypatch.setattr(aggregator, "_load_profile",
                        lambda inp: legacy_calls.__setitem__("profile", legacy_calls["profile"] + 1))
    monkeypatch.setattr(aggregator, "_load_deductions",
                        lambda inp: legacy_calls.__setitem__("deductions", legacy_calls["deductions"] + 1))
    monkeypatch.setattr(aggregator, "_load_service_providers",
                        lambda inp: legacy_calls.__setitem__("sps", legacy_calls["sps"] + 1))
    monkeypatch.setattr(aggregator, "_load_property_and_rentals",
                        lambda inp: legacy_calls.__setitem__("rentals", legacy_calls["rentals"] + 1))
    monkeypatch.setattr(aggregator, "_load_earnings", lambda inp: None)

    aggregator.assemble_tax_inputs(user_id=42, tax_year="2025-26")

    # Each legacy loader should have run exactly once.
    assert legacy_calls == {"profile": 1, "deductions": 1, "sps": 1, "rentals": 1}, (
        f"legacy loaders not invoked when view path failed: {legacy_calls}"
    )


# ---------------------------------------------------------------------------
# Test 3: query budget cap (view path) — count execute() invocations
# ---------------------------------------------------------------------------


def test_view_path_total_query_count_is_one(monkeypatch):
    """On cache-miss in the view path, assemble_tax_inputs must trigger
    EXACTLY ONE db.session.execute() — the view SELECT.

    The earnings path uses ORM `IncomeEntry.query.filter().all()` separately
    (counted via the existing instrumentation in income_summary_for_tax_year,
    capped at 1-2). This test pins the view-path contract specifically."""
    from fiesta.tax_bill import aggregator
    import types

    canned_row = {
        "nic": "999000111V",
        "tin": "T1",
        "city": "Galle",
        "employment_type": "employee",
        "user_name": "Budget Test",
        "deductions": [],
        "service_providers": [
            {
                "id": 1,
                "name": "Sample SP",
                "service_type": "accountant",
                "stated_relationship": "professional_arms_length",
                "requires_disclosure": False,
                "monthly_rate_cents": 5000000,
                "hourly_rate_cents": None,
                "rel_confidence": 0.1,
                "rel_should_default_on_disclosure": False,
                "agreement_has": False,
                "agreement_reference_id": None,
                "agreement_monthly_fee_lkr": None,
                "agreement_customer_sig": None,
                "agreement_sp_sig": None,
                "agreement_sec195_applied": False,
                "agreement_sec195_default_was_on": False,
            },
        ],
        "rentals": [
            {
                "id": 7,
                "property_id": 3,
                "landlord_id": 4,
                "monthly_rent_lkr_cents": 12000000,
                "home_office_portion_lkr_cents": 6000000,
                "start_date": None,
                "end_date": None,
                "document_status": "signed",
                "property_address_line1": "Sample Rd",
                "property_city": "Colombo",
                "property_type": "apartment",
                "property_customer_status": "tenant",
                "property_home_office_percentage": 50.0,
                "landlord_full_name": "Sample Landlord",
                "landlord_relationship": "arm's-length",
                "lrd_confidence": 0.0,
                "lrd_should_default_on_disclosure": False,
                "rag_has": False,
                "rag_reference_id": None,
                "rag_s195_applied": False,
                "rag_s195_default_on_recommended": False,
                "rag_stamp_duty_chargeable": False,
                "rag_stamp_duty_lkr": None,
            },
        ],
    }

    fake_session = _FakeSession(canned_row=canned_row)
    class _FakeDb:
        session = fake_session
    fake_app = types.ModuleType("app")
    fake_app.db = _FakeDb()
    monkeypatch.setitem(sys.modules, "app", fake_app)

    # Make the legacy loaders raise loudly if they fire — we want a true count.
    def _boom(_):
        raise AssertionError("legacy loader called on view-path test")
    monkeypatch.setattr(aggregator, "_load_profile", _boom)
    monkeypatch.setattr(aggregator, "_load_deductions", _boom)
    monkeypatch.setattr(aggregator, "_load_service_providers", _boom)
    monkeypatch.setattr(aggregator, "_load_property_and_rentals", _boom)

    # Stub earnings so it doesn't try a real DB call (it's counted separately).
    monkeypatch.setattr(aggregator, "_load_earnings", lambda inp: None)

    inputs = aggregator.assemble_tax_inputs(user_id=42, tax_year="2025-26")

    assert len(fake_session.executes) == 1, (
        f"view path issued {len(fake_session.executes)} queries, expected 1"
    )

    # Sanity: SP + rental shapes survived the JSON-aggregated transit.
    assert len(inputs.service_providers) == 1
    assert inputs.service_providers[0]["monthly_rate_lkr"] == Decimal("50000.00")
    assert inputs.service_providers[0]["annual_lkr"] == Decimal("600000.00")
    assert len(inputs.rentals) == 1
    assert inputs.rentals[0]["monthly_rent_lkr"] == Decimal("120000.00")


# ---------------------------------------------------------------------------
# Test 4: cache hits short-circuit assembly entirely (0 queries on warm)
# ---------------------------------------------------------------------------


def test_cache_hit_skips_assembly(monkeypatch):
    """A second compute_tax_bill() within the TTL must not call
    assemble_tax_inputs at all — that's the 0-query warm path."""
    from fiesta.tax_bill import compute as compute_mod
    from fiesta.tax_bill.compute import compute_tax_bill, TaxBillReport
    from fiesta.tax_bill.aggregator import TaxInputs

    # Fresh cache so this test is hermetic.
    compute_mod._TAX_BILL_CACHE.clear()

    call_count = {"n": 0}

    def _fake_assemble(user_id, tax_year):
        call_count["n"] += 1
        return TaxInputs(
            user_id=int(user_id),
            tax_year_s4_format="2025-26",
            tax_year_s5_format="2025/2026",
            profile_complete=True,
        )

    monkeypatch.setattr(compute_mod, "assemble_tax_inputs", _fake_assemble)

    # Stub the engine path so we don't try to import fiesta.tax (it may, but
    # we make this test focused on cache behaviour, not engine correctness).
    monkeypatch.setattr(compute_mod, "canonical_tax_year_enum",
                        lambda ty: "FAKE_ENUM")
    class _FakeComputation:
        gross_income_lkr = Decimal("0")
        deductions_input_lkr = Decimal("0")
        taxable_income_lkr = Decimal("0")
        gross_tax_lkr = Decimal("0")
        net_tax_due_lkr = Decimal("0")
        def to_dict(self):
            return {}
    monkeypatch.setattr(compute_mod, "_run_engine",
                        lambda *a, **kw: _FakeComputation())

    # First call: cold cache, fake_assemble fires.
    r1 = compute_tax_bill(user_id=42, tax_year="2025-26")
    assert call_count["n"] == 1, "first call must invoke assemble_tax_inputs"
    assert isinstance(r1, TaxBillReport)

    # Second call: warm cache, fake_assemble must NOT fire.
    r2 = compute_tax_bill(user_id=42, tax_year="2025-26")
    assert call_count["n"] == 1, (
        f"second call invoked assemble_tax_inputs {call_count['n']} times — "
        "cache miss"
    )

    # Same report object returned (identity).
    assert r2 is r1

    # use_cache=False forces a fresh assemble.
    compute_tax_bill(user_id=42, tax_year="2025-26", use_cache=False)
    assert call_count["n"] == 2, "use_cache=False must bypass the cache"


def test_invalidate_tax_bill_cache_drops_entries():
    """_invalidate_tax_bill_cache(user_id, tax_year) must drop the matching
    cache entry; called by deductions claim/unclaim/evidence writes."""
    from fiesta.tax_bill import compute as compute_mod
    from fiesta.tax_bill.compute import _invalidate_tax_bill_cache

    compute_mod._TAX_BILL_CACHE.clear()
    compute_mod._TAX_BILL_CACHE[(42, "2025-26")] = (9_999_999_999, "stub")
    compute_mod._TAX_BILL_CACHE[(42, "2024-25")] = (9_999_999_999, "stub")
    compute_mod._TAX_BILL_CACHE[(7, "2025-26")] = (9_999_999_999, "stub")

    # Drop one (user, year).
    _invalidate_tax_bill_cache(42, "2025-26")
    assert (42, "2025-26") not in compute_mod._TAX_BILL_CACHE
    assert (42, "2024-25") in compute_mod._TAX_BILL_CACHE
    assert (7, "2025-26") in compute_mod._TAX_BILL_CACHE

    # Drop all years for one user.
    _invalidate_tax_bill_cache(42)
    assert (42, "2024-25") not in compute_mod._TAX_BILL_CACHE
    assert (7, "2025-26") in compute_mod._TAX_BILL_CACHE

    # Wipe everything.
    _invalidate_tax_bill_cache()
    assert len(compute_mod._TAX_BILL_CACHE) == 0
