"""tests/tax/test_b8_bank_parse.py — MS2 E.1 / B8 full-impl tests.

Covers:
  - upload route accepts pdf/jpg/png
  - upload route rejects oversized files
  - parse pipeline creates ParsedBankStatement row (mock Gemini)
  - parse pipeline extracts remittance rows (mock Gemini structured-output)
  - review UI renders parsed rows
  - confirm creates Income rows with bank_parse_id FK (canonical E.0 Income)
  - confirm creates RemittanceEntry rows linked to incomes (income_id FK)
  - SWIFT code extracted when present (+ absent case)
  - currency validation rejects invalid ISO codes

All tests MOCK GeminiBankParser.extract_rows — no real Gemini calls.
BANK_PARSE_ENABLED is forced True for tests via env in conftest.

Run::

    cd C:/Users/mahes/fiesta_phase_a/Developsrilanka_ms2_b8
    python -m pytest tests/tax/test_b8_bank_parse.py -v
"""
from __future__ import annotations

import io
import json
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

# Force BANK_PARSE_ENABLED on for these tests (must be set before module import).
os.environ["BANK_PARSE_ENABLED"] = "true"


# ---------------------------------------------------------------------------
# Blueprint registration — register the bank_parse blueprint AND a stub
# `getting_started.wizard` endpoint so the layout template's url_for() calls
# don't BuildError. The tax conftest deliberately doesn't load main.py
# (too heavy), so we wire the minimum needed for route tests here.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def _register_bank_parse_routes():
    from app import app as flask_app
    from flask import Blueprint

    import bank_parse_routes
    if "bank_parse" not in flask_app.blueprints:
        bank_parse_routes.register_routes(flask_app)

    # The review template links back to remittance.new / remittance.dashboard;
    # register the full remittance blueprint so url_for() resolves.
    if "remittance" not in flask_app.blueprints:
        import remittance_routes
        remittance_routes.register_routes(flask_app)

    # The layout.html template references ~20 endpoints via url_for() that
    # only exist when main.py loads every blueprint. Loading main.py in tests
    # would slow them dramatically AND make them depend on the prod DB. The
    # test-friendly path: force `layout_template` to a tiny stub layout that
    # only renders `{% block content %}{% endblock %}`. Tests assert the
    # rendered body contains the inputs/buttons/values we care about; the
    # full visual layout is covered by integration tests + Playwright.
    if "_test_layout_stub" not in flask_app.jinja_env.list_templates():
        flask_app.jinja_loader.mapping = getattr(  # type: ignore[attr-defined]
            flask_app.jinja_loader, "mapping", {}
        )
    # Inject a tiny layout template into Jinja's loader chain.
    from jinja2 import DictLoader, ChoiceLoader
    stub_loader = DictLoader({
        "_test_layout_stub.html": (
            "<!doctype html><html><head><title>test</title></head>"
            "<body>{% block content %}{% endblock %}</body></html>"
        ),
    })
    if not any(isinstance(l, DictLoader) for l in
               getattr(flask_app.jinja_loader, "loaders", [flask_app.jinja_loader])):
        flask_app.jinja_loader = ChoiceLoader([stub_loader, flask_app.jinja_loader])

    # Override the layout_template context processor to point at our stub.
    @flask_app.context_processor
    def _override_layout_for_tests():
        return {"layout_template": "_test_layout_stub.html"}
    yield


# ---------------------------------------------------------------------------
# Helpers — synthetic file bytes with valid magic bytes
# ---------------------------------------------------------------------------
def _pdf_bytes(payload: bytes = b"dummy") -> bytes:
    """Minimal-valid PDF magic header. Content body is irrelevant for the
    test path (we mock the parser; the magic-byte sniff is what we test)."""
    return b"%PDF-1.4\n" + payload + b"\n%%EOF\n"


def _jpeg_bytes(payload: bytes = b"dummy") -> bytes:
    return b"\xff\xd8\xff\xe0" + payload + b"\xff\xd9"


def _png_bytes(payload: bytes = b"dummy") -> bytes:
    return b"\x89PNG\r\n\x1a\n" + payload


def _today_iso() -> str:
    return date.today().isoformat()


def _yesterday_iso() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


# ---------------------------------------------------------------------------
# Mock Gemini responses (what GeminiBankParser.extract_rows would return)
# ---------------------------------------------------------------------------
MOCK_ROWS_TWO_REMITTANCES = [
    {
        "row_index": 0,
        "date": _yesterday_iso(),
        "amount": "1500.00",
        "currency": "USD",
        "sender": "Acme Corp",
        "narration": "INWARD REMITTANCE SWIFT BOFAUS3NXXX REF 12345",
        "swift_code": "BOFAUS3NXXX",
        "confidence": "high",
    },
    {
        "row_index": 1,
        "date": _today_iso(),
        "amount": "750.50",
        "currency": "GBP",
        "sender": "John Smith",
        "narration": "TT IN FROM UK CLIENT",
        "swift_code": None,
        "confidence": "medium",
    },
]


MOCK_ROWS_WITH_INVALID_CURRENCY = [
    {
        "row_index": 0,
        "date": _today_iso(),
        "amount": "1000.00",
        "currency": "XYZ",  # not in ALLOWED_CURRENCIES — should be dropped
        "sender": "Bogus",
        "narration": "junk row",
        "confidence": "low",
    },
    {
        "row_index": 1,
        "date": _today_iso(),
        "amount": "500.00",
        "currency": "EUR",  # valid
        "sender": "Real Sender",
        "narration": "INWARD",
        "confidence": "high",
    },
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_upload_route_accepts_pdf(app_ctx, user):
    """POST /remittance/import/parse with a PDF returns 302 to /review."""
    from fiesta.tax.bank_parse import GeminiBankParser

    client = app_ctx.test_client()
    with app_ctx.test_request_context():
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    pdf = _pdf_bytes(b"the report")

    with patch.object(GeminiBankParser, "extract_rows", return_value=MOCK_ROWS_TWO_REMITTANCES):
        resp = client.post(
            "/remittance/import/parse",
            data={"statement": (io.BytesIO(pdf), "statement.pdf")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
    assert resp.status_code in (302, 303), f"expected redirect, got {resp.status_code}"
    assert "/remittance/import/parse/" in resp.location
    assert "/review" in resp.location


def test_upload_route_rejects_oversized_file(app_ctx, user):
    """POST with a 12 MB file (limit is 10 MB) → flashed warning + redirect to upload."""
    from fiesta.tax.bank_parse import GeminiBankParser, MAX_UPLOAD_BYTES

    client = app_ctx.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True

    # 12 MB > 10 MB limit; magic bytes valid
    big = _pdf_bytes(b"x" * (MAX_UPLOAD_BYTES + (2 * 1024 * 1024)))

    with patch.object(GeminiBankParser, "extract_rows", return_value=[]):
        resp = client.post(
            "/remittance/import/parse",
            data={"statement": (io.BytesIO(big), "huge.pdf")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
    assert resp.status_code in (302, 303)
    # Should redirect back to the upload page (not /review)
    assert "/review" not in (resp.location or "")


def test_parse_pipeline_creates_parsed_bank_statement_row(app_ctx, user, session):
    """parse_file() creates a ParsedBankStatement row with status='parsed'."""
    from fiesta.tax.bank_parse import GeminiBankParser, parse_file
    from fiesta.tax.models import ParsedBankStatement

    pdf = _pdf_bytes(b"bank lines")
    with patch.object(GeminiBankParser, "extract_rows", return_value=MOCK_ROWS_TWO_REMITTANCES):
        result = parse_file(
            user_id=user.id,
            file_bytes=pdf,
            filename="march.pdf",
        )

    assert result.deduplicated is False
    assert result.rows_extracted == 2

    pbs = ParsedBankStatement.query.get(result.parsed_bank_statement.id)
    assert pbs is not None
    assert pbs.user_id == user.id
    assert pbs.status == "parsed"
    assert pbs.parsed_at is not None
    assert "sha256:" in pbs.file_ref
    payload = pbs.raw_text
    assert payload["kind"] == "pdf"
    assert payload["row_count_validated"] == 2
    assert len(payload["rows"]) == 2


def test_parse_pipeline_extracts_remittance_rows(app_ctx, user):
    """Gemini structured output → validated ParsedRow list with all fields."""
    from fiesta.tax.bank_parse import GeminiBankParser, parse_file

    jpg = _jpeg_bytes(b"image of statement")
    with patch.object(GeminiBankParser, "extract_rows", return_value=MOCK_ROWS_TWO_REMITTANCES):
        result = parse_file(
            user_id=user.id,
            file_bytes=jpg,
            filename="statement.jpg",
        )

    rows = result.parsed_bank_statement.raw_text["rows"]
    assert len(rows) == 2
    assert rows[0]["currency"] == "USD"
    assert rows[0]["amount"] == "1500.00"
    assert rows[0]["sender"] == "Acme Corp"
    assert rows[0]["confidence"] == "high"
    assert rows[1]["currency"] == "GBP"
    assert rows[1]["amount"] == "750.50"
    # row_index is re-numbered after validation
    assert rows[0]["row_index"] == 0
    assert rows[1]["row_index"] == 1


def test_review_ui_renders_parsed_rows(app_ctx, user):
    """GET /remittance/import/parse/<id>/review renders rows table."""
    from fiesta.tax.bank_parse import GeminiBankParser, parse_file

    client = app_ctx.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True

    pdf = _pdf_bytes(b"x")
    with patch.object(GeminiBankParser, "extract_rows", return_value=MOCK_ROWS_TWO_REMITTANCES):
        result = parse_file(user.id, pdf, "test.pdf")

    resp = client.get(f"/remittance/import/parse/{result.parsed_bank_statement.id}/review")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Currency strings should be present in form inputs
    assert "USD" in body
    assert "GBP" in body
    # Form action points at confirm endpoint
    assert f"/remittance/import/parse/{result.parsed_bank_statement.id}/confirm" in body


def test_confirm_creates_income_rows_with_bank_parse_fk(app_ctx, user, session):
    """confirm_parse() creates canonical Income rows with bank_parse_id FK populated."""
    from fiesta.tax.bank_parse import (
        GeminiBankParser, parse_file, confirm_parse, ConfirmedRowInput,
    )
    from fiesta.tax.models import Income

    pdf = _pdf_bytes(b"x")
    with patch.object(GeminiBankParser, "extract_rows", return_value=MOCK_ROWS_TWO_REMITTANCES):
        result = parse_file(user.id, pdf, "feb.pdf")
    pbs_id = result.parsed_bank_statement.id

    # Provide a deterministic FX lookup (305 LKR/USD; 400 LKR/GBP).
    def _fx(ccy, d):
        return {"USD": Decimal("305.50"), "GBP": Decimal("400.00")}.get(ccy)

    inputs = [
        ConfirmedRowInput(
            row_index=0, include=True, date=_yesterday_iso(),
            amount="1500.00", currency="USD", sender="Acme",
            source_country="US",
        ),
        ConfirmedRowInput(
            row_index=1, include=True, date=_today_iso(),
            amount="750.50", currency="GBP", sender="John",
            source_country="GB",
        ),
    ]

    cresult = confirm_parse(
        parsed_bank_statement_id=pbs_id,
        user_id=user.id,
        rows=inputs,
        fx_lookup=_fx,
    )
    assert cresult.income_created == 2
    assert cresult.remittance_created == 2

    incs = Income.query.filter_by(user_id=user.id, bank_parse_id=pbs_id).all()
    assert len(incs) == 2
    for inc in incs:
        assert inc.source_type == "foreign_remittance"
        assert inc.bank_parse_id == pbs_id
        assert inc.amount_lkr > 0
        # Evidence ref records the parse + row index
        assert any(
            isinstance(ref, dict)
            and ref.get("type") == "bank_statement_parse"
            and ref.get("ref_id") == pbs_id
            for ref in (inc.evidence_refs or [])
        )

    # PBS status flips to 'reviewed'
    from fiesta.tax.models import ParsedBankStatement
    session.refresh(ParsedBankStatement.query.get(pbs_id))
    assert ParsedBankStatement.query.get(pbs_id).status == "reviewed"


def test_confirm_creates_remittance_entries_linked_to_incomes(app_ctx, user, session):
    """Confirm also creates RemittanceEntry rows with income_id FK populated."""
    from fiesta.tax.bank_parse import (
        GeminiBankParser, parse_file, confirm_parse, ConfirmedRowInput,
    )
    from fiesta.tax.models import Income
    from remittance_models import RemittanceEntry

    pdf = _pdf_bytes(b"y")
    with patch.object(GeminiBankParser, "extract_rows", return_value=MOCK_ROWS_TWO_REMITTANCES):
        result = parse_file(user.id, pdf, "mar.pdf")

    def _fx(ccy, d):
        return Decimal("300.00")

    inputs = [
        ConfirmedRowInput(
            row_index=0, include=True, date=_today_iso(),
            amount="1500.00", currency="USD", sender="Acme",
        ),
        ConfirmedRowInput(
            row_index=1, include=True, date=_today_iso(),
            amount="750.50", currency="GBP", sender="John",
        ),
    ]
    cresult = confirm_parse(
        parsed_bank_statement_id=result.parsed_bank_statement.id,
        user_id=user.id,
        rows=inputs,
        fx_lookup=_fx,
    )
    assert cresult.remittance_created == 2

    for income_id, remit_id in zip(cresult.income_ids, cresult.remittance_ids):
        inc = Income.query.get(income_id)
        remit = RemittanceEntry.query.get(remit_id)
        assert remit.income_id == inc.id  # FK back-pointer
        # Same money totals on both sides
        assert remit.lkr_amount_cbsl == inc.amount_lkr
        assert remit.foreign_currency == inc.currency
        assert Decimal(str(remit.foreign_amount)) == Decimal(str(inc.amount))


def test_swift_code_extracted_when_present(app_ctx, user):
    """SWIFT/BIC code is captured when narration contains a valid BIC; None otherwise."""
    from fiesta.tax.bank_parse import GeminiBankParser, parse_file

    mock_with_swift = [
        {
            "row_index": 0,
            "date": _today_iso(),
            "amount": "1000.00",
            "currency": "USD",
            "sender": "X",
            "narration": "INWARD WIRE BIC BOFAUS3N REF 99",
            "swift_code": "BOFAUS3N",
            "confidence": "high",
        },
    ]
    mock_without_swift = [
        {
            "row_index": 0,
            "date": _today_iso(),
            "amount": "1000.00",
            "currency": "USD",
            "sender": "X",
            "narration": "REMITTANCE FROM CLIENT (no bic)",
            "swift_code": None,
            "confidence": "medium",
        },
    ]

    pdf1 = _pdf_bytes(b"with-swift")
    with patch.object(GeminiBankParser, "extract_rows", return_value=mock_with_swift):
        r1 = parse_file(user.id, pdf1, "a.pdf")
    assert r1.parsed_bank_statement.raw_text["rows"][0]["swift_code"] == "BOFAUS3N"

    pdf2 = _pdf_bytes(b"without-swift")
    with patch.object(GeminiBankParser, "extract_rows", return_value=mock_without_swift):
        r2 = parse_file(user.id, pdf2, "b.pdf")
    assert r2.parsed_bank_statement.raw_text["rows"][0]["swift_code"] is None


def test_currency_validation_rejects_invalid_iso(app_ctx, user):
    """Rows with currencies outside ALLOWED_CURRENCIES are dropped."""
    from fiesta.tax.bank_parse import GeminiBankParser, parse_file

    pdf = _pdf_bytes(b"mixed")
    with patch.object(GeminiBankParser, "extract_rows",
                       return_value=MOCK_ROWS_WITH_INVALID_CURRENCY):
        result = parse_file(user.id, pdf, "bad.pdf")
    rows = result.parsed_bank_statement.raw_text["rows"]
    assert len(rows) == 1
    assert rows[0]["currency"] == "EUR"
    # Row count is for VALID only; raw row count tracked separately
    assert result.parsed_bank_statement.raw_text["row_count_raw"] == 2
    assert result.parsed_bank_statement.raw_text["row_count_validated"] == 1


def test_idempotent_upload_same_file_twice(app_ctx, user):
    """Uploading the same bytes twice does NOT duplicate income rows."""
    from fiesta.tax.bank_parse import (
        GeminiBankParser, parse_file, confirm_parse, ConfirmedRowInput,
    )
    from fiesta.tax.models import Income

    pdf = _pdf_bytes(b"same-content-please")

    with patch.object(GeminiBankParser, "extract_rows", return_value=MOCK_ROWS_TWO_REMITTANCES):
        r1 = parse_file(user.id, pdf, "x.pdf")
        r2 = parse_file(user.id, pdf, "renamed.pdf")  # same bytes, different name

    assert r1.parsed_bank_statement.id == r2.parsed_bank_statement.id
    assert r2.deduplicated is True

    # Confirm once
    def _fx(ccy, d):
        return Decimal("300.00")
    inputs = [
        ConfirmedRowInput(row_index=0, include=True, date=_today_iso(),
                          amount="1500.00", currency="USD"),
        ConfirmedRowInput(row_index=1, include=True, date=_today_iso(),
                          amount="750.50", currency="GBP"),
    ]
    c1 = confirm_parse(r1.parsed_bank_statement.id, user.id, inputs, fx_lookup=_fx)
    assert c1.income_created == 2

    # Confirm again (e.g. user double-clicked submit) — should NOT create
    # duplicate Income rows for the same row_index.
    c2 = confirm_parse(r1.parsed_bank_statement.id, user.id, inputs, fx_lookup=_fx)
    assert c2.income_created == 0  # all skipped via already_done set

    total = Income.query.filter_by(
        user_id=user.id,
        bank_parse_id=r1.parsed_bank_statement.id,
    ).count()
    assert total == 2  # not 4


def test_validate_parsed_rows_drops_invalid_amounts():
    """validate_parsed_rows drops rows with amount <= 0 or non-numeric."""
    from fiesta.tax.bank_parse import validate_parsed_rows
    rows = [
        {"row_index": 0, "date": _today_iso(), "amount": "0", "currency": "USD"},
        {"row_index": 1, "date": _today_iso(), "amount": "-50", "currency": "USD"},
        {"row_index": 2, "date": _today_iso(), "amount": "abc", "currency": "USD"},
        {"row_index": 3, "date": _today_iso(), "amount": "100", "currency": "USD"},
    ]
    out = validate_parsed_rows(rows)
    assert len(out) == 1
    assert out[0].amount == "100"
