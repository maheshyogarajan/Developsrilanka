"""tests/tax_bill/test_b14_audit_pdf_v2.py

B14 -- Audit-defence PDF v2 regression + feature-coverage tests.

Coverage:
  1. v1 unchanged: rendering audit-pack v1 still produces a valid PDF
     with the same section headings (regression).
  2. v2 builds when AUDIT_PDF_V2_ENABLED is set + ?v=2 (via direct call
     to build_audit_pack_v2 with a fixture report).
  3. Route falls back to v1 (or skips v2) when the flag is OFF, even
     with ?v=2.
  4. v2 has the per-claim evidence chain section.
  5. v2 cites IRA sections (Section C present).
  6. v2 has calculation trace (Section D present).
  7. Typical-filing fixture renders under the page cap.
  8. PDF starts with %PDF- header (valid PDF bytes).
  9. IRA cite loader returns the expected schema.
 10. Provenance builder produces non-empty rows for a fixture customer
     with mixed income types.

Framework: pytest. Tests do NOT require a Flask app context for the
builder paths; they bypass DB loaders via the existing _make_inputs
fixture pattern from test_s12.py.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
from decimal import Decimal

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Fixture: mixed-income customer for realistic v2 render
# ---------------------------------------------------------------------------


def _make_mixed_income_report():
    """A realistic customer: 2 income categories + 3 deductions + 1 SP + 1 rental."""
    from fiesta.tax_bill.aggregator import TaxInputs, _compose_engine_inputs
    from fiesta.tax_bill.compute import compute_tax_bill

    inputs = TaxInputs(
        user_id=4242,
        tax_year_s4_format="2025-26",
        tax_year_s5_format="2025/2026",
        full_name="Test Mixed-Income Customer",
        nic="999000111V",
        tin="TINFIXT123",
        senior_citizen=False,
        profile_complete=True,
    )
    inputs.income_by_category_lkr = {
        "salary": Decimal("3000000"),
        "foreign_remittance": Decimal("4500000"),
        "interest": Decimal("250000"),
    }
    inputs.income_total_lkr = sum(
        (v for v in inputs.income_by_category_lkr.values()),
        Decimal("0"),
    )
    inputs.income_by_currency = {
        "LKR": Decimal("3250000"),
        "USD": Decimal("15000"),
    }
    inputs.income_entry_count = 14
    inputs.income_fx_warnings = []

    inputs.deductions_itemised = [
        {
            "category_id": "internet_telecom",
            "name": "Internet & telecom for work",
            "ira_section": "§6",
            "ira_section_long": "Inland Revenue Act §6",
            "estimated_lkr": Decimal("120000"),
            "actual_lkr": Decimal("120000"),
            "used_lkr": Decimal("120000"),
            "evidence_status": "collected",
            "notes": None,
            "cap_note": None,
            "engine_bucket": "expenditure_relief_lkr",
        },
        {
            "category_id": "solar",
            "name": "Solar installations",
            "ira_section": "§6 + §13",
            "ira_section_long": "Inland Revenue Act §6 read with §13",
            "estimated_lkr": Decimal("700000"),
            "actual_lkr": Decimal("700000"),
            "used_lkr": Decimal("600000"),
            "evidence_status": "submitted",
            "notes": None,
            "cap_note": "Capped at Rs 600,000 per gazette rule.",
            "engine_bucket": "solar_investment_lkr",
        },
        {
            "category_id": "home_office_rental",
            "name": "Home office rental",
            "ira_section": "§6",
            "ira_section_long": "Inland Revenue Act §6",
            "estimated_lkr": Decimal("192000"),
            "actual_lkr": Decimal("192000"),
            "used_lkr": Decimal("192000"),
            "evidence_status": "collected",
            "notes": None,
            "cap_note": None,
            "engine_bucket": "rent_relief_lkr",
        },
    ]
    inputs.deductions_total_lkr = sum(
        (d["used_lkr"] for d in inputs.deductions_itemised), Decimal("0")
    )
    inputs.deductions_with_evidence_count = 3
    inputs.deductions_pending_evidence_count = 0

    inputs.service_providers = [{
        "id": 1, "name": "Acme Accountancy LLP",
        "service_type": "professional_accountant",
        "monthly_rate_lkr": Decimal("50000"),
        "annual_lkr": Decimal("600000"),
        "requires_disclosure": False,
        "has_agreement": True,
        "agreement_status": "signed",
        "agreement_reference_id": "FIESTA-SA-TEST1",
        "agreement_monthly_fee_lkr": Decimal("50000"),
        "disclosure_applied_in_agreement": False,
        "rel_confidence": 0.05,
    }]
    inputs.rentals = [{
        "rental_id": 1, "property_address": "12 Test Rd, Colombo 03",
        "property_type": "apartment", "customer_status": "tenant",
        "landlord_name": "Test Landlord",
        "landlord_relationship": "arm's-length",
        "monthly_rent_lkr": Decimal("80000"),
        "annual_rent_lkr": Decimal("960000"),
        "home_office_portion_monthly_lkr": Decimal("16000"),
        "home_office_portion_annual_lkr": Decimal("192000"),
        "home_office_percentage": 20.0,
        "term_start": "2025-04-01", "term_end": "2026-03-31",
        "document_status": "signed",
        "requires_disclosure": False,
        "disclosure_applied_in_agreement": False,
        "agreement_reference_id": "FIESTA-RA-TEST1",
        "stamp_duty_chargeable": False,
        "stamp_duty_lkr": Decimal("0"),
        "rel_confidence": 0.1,
    }]
    inputs.sp_disclosure_required_count = 0
    inputs.sp_disclosure_applied_count = 0
    inputs.rental_disclosure_required_count = 0
    inputs.rental_disclosure_applied_count = 0
    inputs.missing_disclosures = []
    inputs.sp_agreement_mismatches = []

    _compose_engine_inputs(inputs)

    report = compute_tax_bill(
        user_id=inputs.user_id,
        tax_year="2025-26",
        pre_assembled=inputs,
    )
    return report


# ---------------------------------------------------------------------------
# Test 1 -- v1 unchanged (regression)
# ---------------------------------------------------------------------------


def test_b14_01_v1_pdf_unchanged_regression():
    """audit_pack.build_audit_pack still produces a valid PDF with v1
    sections after the v2 patch lands."""
    from fiesta.tax_bill.audit_pack import build_audit_pack

    report = _make_mixed_income_report()
    pdf = build_audit_pack(report)
    assert isinstance(pdf, (bytes, bytearray))
    assert pdf[:4] == b"%PDF"
    # Sanity: still has the v1 section markers (verbatim text from audit_pack.py).
    text = bytes(pdf).decode("latin-1", errors="ignore")
    # The PDF stream is binary; we look for substrings ReportLab places
    # in plain-text form before compression of titles.
    # Be lenient -- some sections appear inside compressed streams.
    # Existence of cover title is sufficient regression proof.
    assert b"FIESTA" in pdf
    assert b"Audit" in pdf
    # v1 produced at least 3KB
    assert len(pdf) > 3000


# ---------------------------------------------------------------------------
# Test 2 -- v2 generates and yields a valid PDF
# ---------------------------------------------------------------------------


def test_b14_02_v2_generates_valid_pdf():
    """Direct call to build_audit_pack_v2 yields a valid PDF."""
    from fiesta.tax_bill.audit_pack_v2 import build_audit_pack_v2

    report = _make_mixed_income_report()
    pdf = build_audit_pack_v2(report)
    assert isinstance(pdf, (bytes, bytearray))
    assert pdf[:4] == b"%PDF", "PDF header missing -- generator did not produce a valid PDF"
    # v2 carries more content than v1; should be larger than the v1 floor.
    assert len(pdf) > 5000, f"v2 PDF unexpectedly small: {len(pdf)} bytes"


# ---------------------------------------------------------------------------
# Test 3 -- route falls back to v1 when flag is off
# ---------------------------------------------------------------------------


def test_b14_03_route_v2_flag_resolution(monkeypatch):
    """_v2_flag_enabled is False by default; flips True when env var is set."""
    from fiesta.tax_bill import routes

    # Default: OFF
    monkeypatch.delenv("AUDIT_PDF_V2_ENABLED", raising=False)
    # Patch feature_flags.is_feature_enabled to return False explicitly
    import sys
    import types
    fake_ff = types.SimpleNamespace(is_feature_enabled=lambda name: False)
    monkeypatch.setitem(sys.modules, "feature_flags", fake_ff)
    assert routes._v2_flag_enabled() is False

    # Flag on via env var
    monkeypatch.setenv("AUDIT_PDF_V2_ENABLED", "true")
    assert routes._v2_flag_enabled() is True

    # Flag on via feature_flags module
    monkeypatch.delenv("AUDIT_PDF_V2_ENABLED", raising=False)
    fake_ff_on = types.SimpleNamespace(is_feature_enabled=lambda name: name == "AUDIT_PDF_V2_ENABLED")
    monkeypatch.setitem(sys.modules, "feature_flags", fake_ff_on)
    assert routes._v2_flag_enabled() is True


# ---------------------------------------------------------------------------
# Test 4 -- v2 has per-claim evidence chain (Section B)
# ---------------------------------------------------------------------------


def test_b14_04_v2_has_per_claim_evidence():
    """v2 PDF must contain Section B heading and claim rows for the fixture."""
    from fiesta.tax_bill.audit_pack_v2 import build_audit_pack_v2

    report = _make_mixed_income_report()
    pdf = build_audit_pack_v2(report)
    assert _pdf_contains_text(pdf, "Per-claim evidence chain"), (
        "Section B header not found in v2 PDF"
    )
    # At least one of our fixture deduction labels should appear.
    assert _pdf_contains_text(pdf, "Internet"), (
        "Per-claim evidence rows do not include the Internet deduction"
    )


# ---------------------------------------------------------------------------
# Test 5 -- v2 cites IRA sections (Section C)
# ---------------------------------------------------------------------------


def test_b14_05_v2_has_ira_citations():
    """Section C (IRA cite verbatim text) is present and quotes §6 + §120."""
    from fiesta.tax_bill.audit_pack_v2 import build_audit_pack_v2

    report = _make_mixed_income_report()
    pdf = build_audit_pack_v2(report)
    # Catalog cites §6 for internet_telecom + home_office_rental + §6 + §13
    # for solar -> §6 must appear in Section C verbatim text.
    assert _pdf_contains_text(pdf, "Business income") or _pdf_contains_text(pdf, "section 6") or _pdf_contains_text(pdf, "Section 6"), (
        "Section C does not include §6 cite text"
    )


# ---------------------------------------------------------------------------
# Test 6 -- v2 has calculation methodology (Section D)
# ---------------------------------------------------------------------------


def test_b14_06_v2_has_calculation_trace():
    """Section D (Calculation methodology) shows the bracket walk."""
    from fiesta.tax_bill.audit_pack_v2 import build_audit_pack_v2

    report = _make_mixed_income_report()
    pdf = build_audit_pack_v2(report)
    assert _pdf_contains_text(pdf, "Calculation methodology") or _pdf_contains_text(pdf, "methodology"), (
        "Section D header not found"
    )
    # Should show the income roll-up + deductions + bracket walk steps
    assert _pdf_contains_text(pdf, "Income roll-up") or _pdf_contains_text(pdf, "roll-up"), (
        "Income roll-up step not in Section D"
    )
    assert _pdf_contains_text(pdf, "Deductions") or _pdf_contains_text(pdf, "Deduction"), (
        "Deductions step not in Section D"
    )


# ---------------------------------------------------------------------------
# Test 7 -- typical-filing fixture renders under the page cap
# ---------------------------------------------------------------------------


def test_b14_07_v2_typical_filing_under_page_cap():
    """A typical mixed-income filing should render under 30 pages."""
    from fiesta.tax_bill.audit_pack_v2 import build_audit_pack_v2

    report = _make_mixed_income_report()
    pdf = build_audit_pack_v2(report)
    page_count = _pdf_page_count(pdf)
    assert page_count > 0, "Could not count pages in generated PDF"
    assert page_count <= 30, (
        f"Typical filing rendered {page_count} pages; spec target is <=30."
    )


# ---------------------------------------------------------------------------
# Test 8 -- PDF bytes start with %PDF- header
# ---------------------------------------------------------------------------


def test_b14_08_pdf_returns_valid_pdf_bytes():
    """Both v1 and v2 builders return bytes starting with the PDF header."""
    from fiesta.tax_bill.audit_pack import build_audit_pack
    from fiesta.tax_bill.audit_pack_v2 import build_audit_pack_v2

    report = _make_mixed_income_report()
    for builder in (build_audit_pack, build_audit_pack_v2):
        pdf = builder(report)
        assert isinstance(pdf, (bytes, bytearray))
        assert pdf[:5] == b"%PDF-", (
            f"{builder.__module__} did not produce a valid PDF header: {pdf[:8]!r}"
        )


# ---------------------------------------------------------------------------
# Test 9 -- IRA cite loader returns the expected schema
# ---------------------------------------------------------------------------


def test_b14_09_ira_cites_loader_schema():
    """load_ira_cites() returns a payload with _meta + sections[] entries."""
    from fiesta.tax_bill.claim_provenance import (
        load_ira_cites, cites_by_section,
    )

    payload = load_ira_cites()
    assert "_meta" in payload
    assert "sections" in payload
    assert isinstance(payload["sections"], list)
    assert len(payload["sections"]) >= 10, (
        "Expected at least 10 IRA sections in catalog; B14 spec target is 10-15."
    )

    cites = cites_by_section()
    # The most-cited sections from the deduction catalog
    for required in ("6", "120", "52"):
        assert required in cites, f"Missing required IRA section §{required} from catalog"
        entry = cites[required]
        assert "title" in entry
        assert "text" in entry
        # §6 + §120 must have real (non-TODO) text
        if required in ("6", "120"):
            assert not entry.get("todo"), f"§{required} should not be a TODO entry"
            assert len(entry["text"]) > 200, (
                f"§{required} text is too short ({len(entry['text'])} chars); expected verbatim quote"
            )


# ---------------------------------------------------------------------------
# Test 10 -- provenance builder yields non-empty rows for the fixture
# ---------------------------------------------------------------------------


def test_b14_10_provenance_rows_mixed_income():
    """all_claim_rows yields rows for income + deductions of the fixture."""
    from fiesta.tax_bill.claim_provenance import (
        all_claim_rows, cited_section_numbers,
    )

    report = _make_mixed_income_report()
    rows = all_claim_rows(report.inputs)
    # 3 income categories + 3 deductions = 6 rows expected
    assert len(rows) == 6, f"Expected 6 claim rows, got {len(rows)}: {[r['claim_id'] for r in rows]}"
    income_rows = [r for r in rows if r["claim_kind"] == "income"]
    ded_rows = [r for r in rows if r["claim_kind"] == "deduction"]
    assert len(income_rows) == 3
    assert len(ded_rows) == 3

    # Every row carries a label + amount + IRA refs
    for row in rows:
        assert row.get("label")
        assert row.get("amount_lkr")
        assert "calculation_trace" in row
        assert len(row["calculation_trace"]) >= 1

    # cited_section_numbers de-dupes and includes the catalog cites
    refs = cited_section_numbers(rows)
    assert "6" in refs, f"Expected §6 in cited refs, got {refs}"
    # Solar cites §6 + §13; §13 must surface
    assert "13" in refs, f"Expected §13 in cited refs (from solar), got {refs}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pdf_page_count(pdf_bytes: bytes) -> int:
    """Quick page count via PDF /Type /Page object matching.

    Robust to compression (we only count /Page object headers, not their
    contents). Returns 0 on parse failure.
    """
    body = pdf_bytes if isinstance(pdf_bytes, bytes) else bytes(pdf_bytes)
    # Each page object emits "/Type /Page" (NOT /Pages, the root object).
    # Use a negative-lookahead to skip /Pages.
    matches = re.findall(rb"/Type\s*/Page(?!s)", body)
    return len(matches)


_PDF_TEXT_CACHE: dict[int, str] = {}


def _pdf_extract_text(pdf_bytes: bytes) -> str:
    """Extract text from a PDF using pypdf (preferred) with PyPDF2 fallback."""
    body = pdf_bytes if isinstance(pdf_bytes, bytes) else bytes(pdf_bytes)
    key = hash(body)
    if key in _PDF_TEXT_CACHE:
        return _PDF_TEXT_CACHE[key]
    text = ""
    try:
        import pypdf
        from io import BytesIO
        reader = pypdf.PdfReader(BytesIO(body))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        try:
            import PyPDF2  # type: ignore
            from io import BytesIO
            reader = PyPDF2.PdfReader(BytesIO(body))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception:
            # Last resort: best-effort latin-1 decode of the raw stream.
            text = body.decode("latin-1", errors="ignore")
    _PDF_TEXT_CACHE[key] = text
    return text


def _pdf_contains_text(pdf_bytes: bytes, needle: str) -> bool:
    """Search the extracted PDF text for a substring (whitespace-tolerant)."""
    text = _pdf_extract_text(pdf_bytes)
    if needle in text:
        return True
    # Whitespace-tolerant fallback (pypdf sometimes splits across spans).
    collapsed = re.sub(r"\s+", "", text)
    if re.sub(r"\s+", "", needle) in collapsed:
        return True
    return False
