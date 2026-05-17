"""
T4 — Pure-function tests on remittance_import.py: file-kind detection, PII redaction,
candidate normalisation. No DB, no Flask, no Gemini calls.
"""
import pytest

from remittance_import import (
    detect_file_kind, redact_pii, normalise_candidates, sha256_hex,
)


# --- File-kind detection (Wave H H4) -------------------------------------- #

def test_detect_pdf_by_magic_bytes():
    assert detect_file_kind("anything.txt", b"%PDF-1.7\nfake pdf body") == "pdf"


def test_detect_csv_by_content():
    csv_bytes = b"Date,Description,Credit,Balance\n2026-03-15,TT INWARD,305500,0\n"
    assert detect_file_kind("foo.csv", csv_bytes) == "csv"


def test_detect_rejects_image():
    # JPEG magic bytes
    assert detect_file_kind("evil.pdf", b"\xff\xd8\xff\xe0\x00\x10JFIF") is None


def test_detect_rejects_empty():
    assert detect_file_kind("blank.csv", b"") is None


def test_detect_rejects_binary_random():
    assert detect_file_kind("random.pdf", b"\x00\x01\x02\x03random binary") is None


# --- PII redaction (Wave H R1) -------------------------------------------- #

def test_redact_account_number():
    text = "Account 1234567890 credit 305500"
    out = redact_pii(text)
    assert "1234567890" not in out
    assert "[REDACTED-ACCT]" in out


def test_redact_short_numbers_kept():
    # 4-digit dates / 3-digit amounts should NOT be redacted (would break parsing)
    text = "Date 2026 Amount 305"
    out = redact_pii(text)
    assert "2026" in out
    assert "305" in out


def test_redact_card_number():
    text = "Card 4111 1111 1111 1111 charged"
    out = redact_pii(text)
    assert "[REDACTED-CARD]" in out
    assert "4111 1111" not in out


def test_redact_preserves_classifiable_signals():
    """Critical: redaction must not blow away the cues Gemini needs."""
    text = "15/03/2026 TT INWARD REMIT ACME PTY LTD USD 2500.00 @ 305.50 Acct 9876543210"
    out = redact_pii(text)
    assert "TT INWARD" in out
    assert "USD" in out
    assert "2500.00" in out
    assert "ACME" in out
    assert "9876543210" not in out


# --- Normalisation (Wave A) ----------------------------------------------- #

def test_normalise_filters_invalid_currency():
    raw = [{"row_index": 0, "lkr_amount": "1000", "foreign_currency": "BTC",
            "foreign_amount": "10", "txn_date": "2026-03-15",
            "description": "x", "is_foreign_remittance": True, "confidence": "low"}]
    out = normalise_candidates(raw)
    assert len(out) == 1
    # BTC is not in CURRENCY_OK → cleared
    assert out[0]["foreign_currency"] == ""


def test_normalise_handles_non_dict():
    out = normalise_candidates([None, "garbage", {"row_index": 0, "lkr_amount": "100",
                                                   "foreign_currency": "USD",
                                                   "is_foreign_remittance": False}])
    assert len(out) == 1


# --- SHA256 hash (Wave H H8) ---------------------------------------------- #

def test_sha256_stable():
    assert sha256_hex(b"hello") == sha256_hex(b"hello")
    assert sha256_hex(b"hello") != sha256_hex(b"world")
