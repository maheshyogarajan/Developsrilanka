"""S4 earnings tests — Wave 3 / S4 ('drop in statements'), 2026-05-20.

Coverage (18 cases):

  Happy path
    1. test_index_renders                                         — GET /earnings renders
    2. test_upload_bank_statement_extracts_and_customer_confirms  — full happy round-trip
    3. test_t10_employer_letter_extracts_to_salary_entry          — T10 → SALARY row
    4. test_manual_entry_creates_confirmed_row                    — manual fallback
    5. test_summary_aggregates_confirmed_entries_only             — to_tax respects confirm gate

  Extraction-failure handling
    6. test_doc_lens_fail_5_attempts_routes_to_rejected           — 5 attempts → rejected
    7. test_extraction_with_low_confidence_surfaces_to_user       — UI banner triggers
    8. test_empty_file_rejected_immediately                       — 0-byte
    9. test_oversize_file_rejected                                — >10MB

  Edit / audit
   10. test_edit_extracted_entry_preserves_original_value         — original_value JSON populated
   11. test_edit_marks_entry_confirmed                            — edit ⇒ confirmed=True
   12. test_confirm_entry_endpoint                                — explicit confirm path

  Multi-currency
   13. test_multi_currency_aggregates_correctly                   — USD + EUR + LKR
   14. test_unconverted_currency_surfaces_in_fx_warnings          — missing FX rate path

  Authz / safety
   15. test_user_cannot_access_other_users_statement              — 403 cross-user
   16. test_unknown_doc_type_rejected                             — input validation
   17. test_delete_statement_cascades_entries                     — DELETE clears rows

  Tax-engine wiring
   18. test_income_summary_shape_matches_tax_engine_contract      — by_category_lkr keys + total
"""
from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tests.earnings.conftest import login_as  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_pdf(text: str = "Synthetic PDF body") -> bytes:
    """Tiny single-page PDF; pdfplumber should yield very little — that exercises
    the fallback paths cleanly without needing real Tesseract on Windows.
    """
    return (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R>>endobj\n"
        + ("4 0 obj<</Length %d>>stream\nBT /F1 12 Tf 50 700 Td (%s) Tj ET\nendstream\nendobj\n" % (
            40 + len(text), text
        )).encode("utf-8")
        + b"xref\n0 5\n0000000000 65535 f \n0000000010 00000 n \n0000000060 00000 n \n"
        + b"0000000110 00000 n \n0000000180 00000 n \ntrailer<</Size 5/Root 1 0 R>>\n"
        + b"startxref\n300\n%%EOF\n"
    )


def _persist_entry(db_session, user, **kw):
    """Helper to insert a confirmed IncomeEntry for summary tests."""
    from fiesta.earnings.models import IncomeCategory, IncomeEntry, sl_tax_year_for

    defaults = dict(
        user_id=user.id,
        statement_id=None,
        entry_date=date(2025, 12, 15),
        currency="LKR",
        amount=Decimal("100000.00"),
        amount_lkr=Decimal("100000.00"),
        fx_rate_lkr=Decimal("1"),
        fx_rate_source="lkr_native",
        source="manual",
        category=IncomeCategory.SALARY.value,
        confirmed_by_customer=True,
        confirmed_at=datetime.utcnow(),
        tax_year=sl_tax_year_for(date(2025, 12, 15)),
    )
    defaults.update(kw)
    e = IncomeEntry(**defaults)
    db_session.add(e)
    db_session.commit()
    return e


# --------------------------------------------------------------------------- #
# 1. GET /earnings renders
# --------------------------------------------------------------------------- #


def test_index_renders(client, user):
    login_as(client, user)
    resp = client.get("/earnings")
    assert resp.status_code == 200, resp.data[:200]
    assert b"Drop your statements" in resp.data
    assert b"Pick a PDF" in resp.data


# --------------------------------------------------------------------------- #
# 2. Upload bank statement → extract → confirm → summary updated
# --------------------------------------------------------------------------- #


def test_upload_bank_statement_extracts_and_customer_confirms(client, user, db_session, monkeypatch):
    """Full happy round-trip with a stubbed doc_lens return."""
    from fiesta.earnings import extractor
    from fiesta.earnings.models import Statement, IncomeEntry, StatementStatus

    def fake_validate_doc(*, client_id, doc_path, expected_doc_type=None):
        return {
            "ok": True, "client_id": client_id,
            "doc_type": "BANK_INTEREST_WHT", "confidence": 0.92,
            "extracted_fields": {
                "bank_name": "Sampath Bank",
                "account_number": "1234567890",
                "year_of_assessment": "2025/2026",
                "interest_income": [
                    {"period_start_date": "2025-04-01", "period_end_date": "2025-09-30", "amount": 14500.00},
                    {"period_start_date": "2025-10-01", "period_end_date": "2026-03-31", "amount": 18200.00},
                ],
                "balance_as_of_date": "2026-03-31",
            },
            "fully_valid": True, "failure_reason": None, "sf_writes_proposed": [],
            "extraction_method": "gemini", "text_extraction_layer": "pdfplumber",
            "errors": [], "raw_text_sample": "",
        }

    monkeypatch.setattr(extractor, "validate_doc", fake_validate_doc)

    login_as(client, user)
    pdf = _make_pdf("Sampath Bank statement")
    resp = client.post(
        "/earnings/upload",
        data={
            "document": (io.BytesIO(pdf), "sampath_2025_26.pdf"),
            "doc_type": "bank_statement",
            "tax_year": "2025-26",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.data[:200]
    assert "/earnings/extraction/" in resp.headers["Location"]

    stmt = Statement.query.filter_by(user_id=user.id).order_by(Statement.id.desc()).first()
    assert stmt is not None
    assert stmt.status == StatementStatus.EXTRACTED.value
    assert stmt.bank_name == "Sampath Bank"
    assert stmt.extraction_confidence == 0.92

    entries = IncomeEntry.query.filter_by(statement_id=stmt.id).all()
    assert len(entries) == 2
    assert sum(float(e.amount) for e in entries) == pytest.approx(32700.00)
    assert all(e.category == "interest" for e in entries)
    assert all(not e.confirmed_by_customer for e in entries)

    # Confirm both entries.
    for e in entries:
        r2 = client.post(f"/earnings/confirm/{e.id}", follow_redirects=False)
        assert r2.status_code == 302

    db_session.expire_all()
    confirmed = IncomeEntry.query.filter_by(statement_id=stmt.id).all()
    assert all(e.confirmed_by_customer for e in confirmed)

    # Statement should be CONFIRMED now.
    db_session.expire(stmt)
    refreshed = Statement.query.get(stmt.id)
    assert refreshed.status == StatementStatus.CONFIRMED.value


# --------------------------------------------------------------------------- #
# 3. T10 employer letter → SALARY entry
# --------------------------------------------------------------------------- #


def test_t10_employer_letter_extracts_to_salary_entry(client, user, db_session, monkeypatch):
    from fiesta.earnings import extractor
    from fiesta.earnings.models import IncomeCategory, IncomeEntry, Statement

    def fake_validate_doc(*, client_id, doc_path, expected_doc_type=None):
        return {
            "ok": True, "client_id": client_id,
            "doc_type": "T10", "confidence": 0.88,
            "extracted_fields": {
                "year_of_assessment": "2025/2026",
                "employer_tin": "123456789",
                "employer_name": "Acme Lanka (Pvt) Ltd",
                "employee_name": "Test User",
                "total_gross_remuneration": 4800000.00,
                "total_tax_deducted": 540000.00,
                "benefits_excluded_for_tax": 0.0,
                "total_amount_remitted": 540000.00,
            },
            "fully_valid": True, "failure_reason": None, "sf_writes_proposed": [],
            "extraction_method": "gemini", "text_extraction_layer": "pdfplumber",
            "errors": [], "raw_text_sample": "",
        }

    monkeypatch.setattr(extractor, "validate_doc", fake_validate_doc)

    login_as(client, user)
    resp = client.post(
        "/earnings/upload",
        data={
            "document": (io.BytesIO(_make_pdf("T10")), "t10_2025_26.pdf"),
            "doc_type": "employer_letter",
            "tax_year": "2025-26",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302

    stmt = Statement.query.filter_by(user_id=user.id).order_by(Statement.id.desc()).first()
    entries = IncomeEntry.query.filter_by(statement_id=stmt.id).all()
    assert len(entries) == 1
    e = entries[0]
    assert e.category == IncomeCategory.SALARY.value
    assert float(e.amount) == 4800000.00
    assert e.source == "Acme Lanka (Pvt) Ltd"


# --------------------------------------------------------------------------- #
# 4. Manual entry creates a confirmed row
# --------------------------------------------------------------------------- #


def test_manual_entry_creates_confirmed_row(client, user, db_session):
    from fiesta.earnings.models import IncomeCategory, IncomeEntry

    login_as(client, user)
    resp = client.post(
        "/earnings/manual",
        data={
            "entry_date": "2025-12-01",
            "amount": "250000.00",
            "currency": "LKR",
            "source": "Self-Employment",
            "category": "contractor_fee",
        },
    )
    assert resp.status_code == 302
    entry = (
        IncomeEntry.query
        .filter_by(user_id=user.id)
        .order_by(IncomeEntry.id.desc())
        .first()
    )
    assert entry is not None
    assert entry.statement_id is None
    assert entry.confirmed_by_customer is True
    assert entry.category == IncomeCategory.CONTRACTOR_FEE.value
    assert float(entry.amount) == 250000.00


# --------------------------------------------------------------------------- #
# 5. Summary aggregates only confirmed rows
# --------------------------------------------------------------------------- #


def test_summary_aggregates_confirmed_entries_only(client, user, db_session):
    from fiesta.earnings.models import IncomeCategory

    # 2 confirmed + 1 unconfirmed.
    _persist_entry(db_session, user, amount=Decimal("100000"), amount_lkr=Decimal("100000"))
    _persist_entry(db_session, user, amount=Decimal("250000"), amount_lkr=Decimal("250000"),
                   category=IncomeCategory.INTEREST.value)
    _persist_entry(db_session, user, amount=Decimal("999999"), amount_lkr=Decimal("999999"),
                   confirmed_by_customer=False, confirmed_at=None)

    login_as(client, user)
    resp = client.get("/earnings/summary?tax_year=2025-26", headers={"Accept": "application/json"})
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["entry_count"] == 2
    assert Decimal(payload["total_lkr"]) == Decimal("350000.00")
    assert Decimal(payload["by_category_lkr"]["salary"]) == Decimal("100000.00")
    assert Decimal(payload["by_category_lkr"]["interest"]) == Decimal("250000.00")


# --------------------------------------------------------------------------- #
# 6. doc_lens fail 5 attempts → REJECTED
# --------------------------------------------------------------------------- #


def test_doc_lens_fail_5_attempts_routes_to_rejected(client, user, db_session, monkeypatch):
    from fiesta.earnings import extractor
    from fiesta.earnings.extractor import extract_statement
    from fiesta.earnings.models import MAX_EXTRACTION_ATTEMPTS, Statement, StatementStatus

    def fake_validate_doc(*, client_id, doc_path, expected_doc_type=None):
        return {
            "ok": False, "client_id": client_id, "doc_type": "UNKNOWN",
            "confidence": 0.0, "extracted_fields": {}, "fully_valid": False,
            "failure_reason": "could not auto-detect doc_type from text",
            "sf_writes_proposed": [], "extraction_method": "none",
            "text_extraction_layer": "none", "errors": [], "raw_text_sample": "",
        }

    monkeypatch.setattr(extractor, "validate_doc", fake_validate_doc)

    # Create one statement, then call extract_statement repeatedly (simulating retries).
    stmt = Statement(
        user_id=user.id,
        file_path="/nonexistent/x.pdf",
        file_name="x.pdf",
        file_size_bytes=100,
        doc_type="bank_statement",
        status=StatementStatus.UPLOADED.value,
        tax_year="2025-26",
    )
    db_session.add(stmt)
    db_session.commit()

    for i in range(MAX_EXTRACTION_ATTEMPTS):
        out = extract_statement(stmt, db_session)
        db_session.commit()
        assert out["ok"] is False

    assert stmt.extraction_attempts == MAX_EXTRACTION_ATTEMPTS
    assert stmt.status == StatementStatus.REJECTED.value

    # 6th call should refuse and route to manual.
    out = extract_statement(stmt, db_session)
    assert out["ok"] is False
    assert out["at_attempt_cap"] is True
    assert "exhausted" in (out["failure_reason"] or "").lower() or stmt.at_attempt_cap()


# --------------------------------------------------------------------------- #
# 7. Low confidence surfaces in UI
# --------------------------------------------------------------------------- #


def test_extraction_with_low_confidence_surfaces_to_user(client, user, db_session, monkeypatch):
    from fiesta.earnings import extractor

    def fake_validate_doc(*, client_id, doc_path, expected_doc_type=None):
        return {
            "ok": True, "client_id": client_id, "doc_type": "BANK_INTEREST_WHT",
            "confidence": 0.42,
            "extracted_fields": {
                "bank_name": "Unclear Bank", "account_number": "999",
                "year_of_assessment": "2025/2026",
                "interest_income": [{"period_end_date": "2025-12-31", "amount": 5000.00}],
            },
            "fully_valid": True, "failure_reason": None, "sf_writes_proposed": [],
            "extraction_method": "gemini", "text_extraction_layer": "pdfplumber",
            "errors": [], "raw_text_sample": "",
        }

    monkeypatch.setattr(extractor, "validate_doc", fake_validate_doc)
    login_as(client, user)
    resp = client.post(
        "/earnings/upload",
        data={"document": (io.BytesIO(_make_pdf()), "x.pdf"), "doc_type": "bank_statement"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    # Banner copy lives in extraction.html
    assert b"confidence" in resp.data.lower()


# --------------------------------------------------------------------------- #
# 8. Empty file rejected
# --------------------------------------------------------------------------- #


def test_empty_file_rejected_immediately(client, user):
    login_as(client, user)
    resp = client.post(
        "/earnings/upload",
        data={"document": (io.BytesIO(b""), "empty.pdf"), "doc_type": "bank_statement"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"empty" in resp.data.lower()


# --------------------------------------------------------------------------- #
# 9. Oversize file rejected
# --------------------------------------------------------------------------- #


def test_oversize_file_rejected(client, user):
    from fiesta.earnings.models import MAX_FILE_BYTES

    login_as(client, user)
    big = b"X" * (MAX_FILE_BYTES + 1024)
    resp = client.post(
        "/earnings/upload",
        data={"document": (io.BytesIO(big), "huge.pdf"), "doc_type": "bank_statement"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"larger" in resp.data.lower() or b"compress" in resp.data.lower()


# --------------------------------------------------------------------------- #
# 10. Edit preserves original_value JSON
# --------------------------------------------------------------------------- #


def test_edit_extracted_entry_preserves_original_value(client, user, db_session):
    entry = _persist_entry(
        db_session, user,
        amount=Decimal("100000"),
        amount_lkr=Decimal("100000"),
        confirmed_by_customer=False,
        confirmed_at=None,
    )

    login_as(client, user)
    resp = client.post(
        f"/earnings/edit/{entry.id}",
        data={
            "amount": "150000",
            "currency": "LKR",
            "entry_date": entry.entry_date.isoformat(),
            "source": entry.source,
            "category": entry.category,
        },
    )
    assert resp.status_code == 302

    db_session.expire(entry)
    from fiesta.earnings.models import IncomeEntry
    refreshed = IncomeEntry.query.get(entry.id)
    assert float(refreshed.amount) == 150000.00
    assert refreshed.original_value
    fields = [h["field"] for h in refreshed.original_value]
    assert "amount" in fields
    amount_hist = next(h for h in refreshed.original_value if h["field"] == "amount")
    assert amount_hist["old_value"] == "100000.0"
    assert amount_hist["new_value"] == "150000.0"


# --------------------------------------------------------------------------- #
# 11. Edit auto-confirms entry
# --------------------------------------------------------------------------- #


def test_edit_marks_entry_confirmed(client, user, db_session):
    from fiesta.earnings.models import IncomeEntry

    entry = _persist_entry(
        db_session, user,
        amount=Decimal("100000"),
        amount_lkr=Decimal("100000"),
        confirmed_by_customer=False,
        confirmed_at=None,
    )
    login_as(client, user)
    resp = client.post(
        f"/earnings/edit/{entry.id}",
        data={"amount": "100001", "currency": "LKR", "entry_date": entry.entry_date.isoformat(),
              "source": entry.source, "category": entry.category},
    )
    assert resp.status_code == 302
    db_session.expire(entry)
    refreshed = IncomeEntry.query.get(entry.id)
    assert refreshed.confirmed_by_customer is True
    assert refreshed.confirmed_at is not None


# --------------------------------------------------------------------------- #
# 12. Explicit confirm endpoint
# --------------------------------------------------------------------------- #


def test_confirm_entry_endpoint(client, user, db_session):
    from fiesta.earnings.models import IncomeEntry

    entry = _persist_entry(
        db_session, user, confirmed_by_customer=False, confirmed_at=None,
    )
    login_as(client, user)
    resp = client.post(f"/earnings/confirm/{entry.id}")
    assert resp.status_code == 302
    db_session.expire(entry)
    refreshed = IncomeEntry.query.get(entry.id)
    assert refreshed.confirmed_by_customer is True


# --------------------------------------------------------------------------- #
# 13. Multi-currency aggregation
# --------------------------------------------------------------------------- #


def test_multi_currency_aggregates_correctly(client, user, db_session, monkeypatch):
    """USD + EUR + LKR rows; we pre-fill amount_lkr to avoid live FX lookups."""
    from fiesta.earnings.models import IncomeCategory

    _persist_entry(db_session, user,
                   currency="USD", amount=Decimal("1000"),
                   amount_lkr=Decimal("310000.00"),
                   fx_rate_lkr=Decimal("310"), fx_rate_source="cbsl",
                   category=IncomeCategory.FOREIGN_REMITTANCE.value)
    _persist_entry(db_session, user,
                   currency="EUR", amount=Decimal("500"),
                   amount_lkr=Decimal("170000.00"),
                   fx_rate_lkr=Decimal("340"), fx_rate_source="cbsl",
                   category=IncomeCategory.FOREIGN_REMITTANCE.value)
    _persist_entry(db_session, user,
                   currency="LKR", amount=Decimal("200000"),
                   amount_lkr=Decimal("200000"),
                   category=IncomeCategory.SALARY.value)

    login_as(client, user)
    resp = client.get("/earnings/summary?tax_year=2025-26", headers={"Accept": "application/json"})
    p = resp.get_json()
    assert Decimal(p["total_lkr"]) == Decimal("680000.00")
    assert Decimal(p["by_category_lkr"]["foreign_remittance"]) == Decimal("480000.00")
    assert Decimal(p["by_category_lkr"]["salary"]) == Decimal("200000.00")
    assert set(p["by_currency"].keys()) == {"USD", "EUR", "LKR"}


# --------------------------------------------------------------------------- #
# 14. Unconverted currency surfaces in fx_warnings
# --------------------------------------------------------------------------- #


def test_unconverted_currency_surfaces_in_fx_warnings(client, user, db_session, monkeypatch):
    """Row in JPY with no FX rate; tax engine must learn about it via fx_warnings."""
    import fiesta.earnings.to_tax as to_tax_mod
    from fiesta.earnings.models import IncomeCategory

    _persist_entry(db_session, user,
                   currency="JPY",
                   amount=Decimal("100000"),
                   amount_lkr=None,        # force lookup
                   fx_rate_lkr=None,
                   fx_rate_source=None,
                   category=IncomeCategory.CONTRACTOR_FEE.value)

    # Force the FX lookup to return None.
    monkeypatch.setattr(to_tax_mod, "_try_fx_lookup", lambda c, d: (None, None))

    login_as(client, user)
    resp = client.get("/earnings/summary?tax_year=2025-26", headers={"Accept": "application/json"})
    p = resp.get_json()
    assert "JPY" in p["unconverted_currencies"]
    assert any("JPY" in w for w in p["fx_warnings"])
    # Total LKR excludes the unconverted row.
    assert Decimal(p["total_lkr"]) == Decimal("0.00")


# --------------------------------------------------------------------------- #
# 15. Cross-user 403
# --------------------------------------------------------------------------- #


def test_user_cannot_access_other_users_statement(client, user, db_session):
    """Stamp another user's statement and verify 403."""
    from tests.earnings.conftest import _make_user
    from fiesta.earnings.models import Statement, StatementStatus

    other = _make_user(db_session, "u2")
    try:
        stmt = Statement(
            user_id=other.id,
            file_path="/nonexistent/o.pdf",
            file_name="o.pdf",
            file_size_bytes=100,
            doc_type="bank_statement",
            status=StatementStatus.UPLOADED.value,
            tax_year="2025-26",
        )
        db_session.add(stmt)
        db_session.commit()

        login_as(client, user)
        resp = client.get(f"/earnings/extraction/{stmt.id}")
        assert resp.status_code == 403
    finally:
        from models import AuditLog, User
        from fiesta.earnings.models import IncomeEntry as _IE
        _IE.query.filter(_IE.user_id == other.id).delete()
        Statement.query.filter(Statement.user_id == other.id).delete()
        AuditLog.query.filter(AuditLog.user_id == other.id).delete()
        User.query.filter(User.id == other.id).delete()
        db_session.commit()


# --------------------------------------------------------------------------- #
# 16. Unknown doc_type rejected
# --------------------------------------------------------------------------- #


def test_unknown_doc_type_rejected(client, user):
    login_as(client, user)
    resp = client.post(
        "/earnings/upload",
        data={"document": (io.BytesIO(_make_pdf()), "x.pdf"), "doc_type": "totally_invalid"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"document type" in resp.data.lower() or b"pick from the list" in resp.data.lower()


# --------------------------------------------------------------------------- #
# 17. DELETE cascades entries
# --------------------------------------------------------------------------- #


def test_delete_statement_cascades_entries(client, user, db_session, monkeypatch):
    from fiesta.earnings import extractor
    from fiesta.earnings.models import IncomeEntry, Statement

    def fake_validate_doc(*, client_id, doc_path, expected_doc_type=None):
        return {
            "ok": True, "client_id": client_id, "doc_type": "BANK_INTEREST_WHT",
            "confidence": 0.9,
            "extracted_fields": {
                "bank_name": "X", "account_number": "1", "year_of_assessment": "2025/2026",
                "interest_income": [{"period_end_date": "2025-12-31", "amount": 5000}],
            },
            "fully_valid": True, "failure_reason": None, "sf_writes_proposed": [],
            "extraction_method": "gemini", "text_extraction_layer": "pdfplumber",
            "errors": [], "raw_text_sample": "",
        }
    monkeypatch.setattr(extractor, "validate_doc", fake_validate_doc)

    login_as(client, user)
    client.post(
        "/earnings/upload",
        data={"document": (io.BytesIO(_make_pdf()), "x.pdf"), "doc_type": "bank_statement"},
        content_type="multipart/form-data",
    )
    stmt = Statement.query.filter_by(user_id=user.id).order_by(Statement.id.desc()).first()
    assert stmt is not None
    assert IncomeEntry.query.filter_by(statement_id=stmt.id).count() == 1

    resp = client.post(
        f"/earnings/{stmt.id}",
        data={"_method": "DELETE"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert Statement.query.get(stmt.id) is None
    assert IncomeEntry.query.filter_by(statement_id=stmt.id).count() == 0


# --------------------------------------------------------------------------- #
# 18. income_summary_for_tax_year shape matches the tax-engine contract
# --------------------------------------------------------------------------- #


def test_income_summary_shape_matches_tax_engine_contract(app, db_session, user):
    """The returned dict must carry the keys the tax engine expects."""
    from fiesta.earnings.models import IncomeCategory
    from fiesta.earnings.to_tax import income_summary_for_tax_year

    _persist_entry(db_session, user, amount=Decimal("100000"), amount_lkr=Decimal("100000"))
    _persist_entry(db_session, user, amount=Decimal("50000"), amount_lkr=Decimal("50000"),
                   category=IncomeCategory.RENTAL.value)

    with app.app_context():
        out = income_summary_for_tax_year(user.id, "2025-26")
    db_session.commit()

    # Required top-level keys.
    for k in ("user_id", "tax_year", "by_category_lkr", "by_currency", "total_lkr",
              "entry_count", "unconverted_currencies", "fx_warnings"):
        assert k in out, f"missing key: {k}"
    # All IncomeCategory values present in by_category_lkr (even if zero).
    for c in IncomeCategory:
        assert c.value in out["by_category_lkr"]
    # Totals add up.
    assert out["total_lkr"] == Decimal("150000.00")
    assert out["entry_count"] == 2
    assert out["unconverted_currencies"] == []
