"""Tests for fiesta.delivery_ops.doc_lens.

Coverage:
  - T10 happy path (synthetic) — extraction + Pydantic + fully_valid True
  - T10 messy format — regex tolerance
  - BANK_INTEREST_WHT stub schema acceptance
  - Failure routing: file-not-found, empty, malformed, no-text
  - Tesseract gracefully skipped if binary missing (counts as pass)
  - Gemini gracefully skipped if no API key (regex fallback works)
  - Auto-detect doc_type from text

Run via:
  cd C:/Users/mahes/fiesta_replit_source/DevelopSriLanka
  python -m pytest fiesta/delivery_ops/tests/test_doc_lens.py -v
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

# Ensure the repo root is on sys.path so `fiesta` resolves cleanly under pytest.
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fiesta.delivery_ops import DocType, validate_doc  # noqa: E402
from fiesta.delivery_ops.sample_docs._generate import generate_all  # noqa: E402


@pytest.fixture(scope="session")
def samples(tmp_path_factory):
    """Generate synthetic doc samples once per session."""
    target = tmp_path_factory.mktemp("doc_lens_samples")
    return generate_all(target)


# ---- T10 happy-path tests --------------------------------------------------


def test_t10_simple_happy_path(samples):
    """Standard T10 — all required fields extracted, fully_valid=True."""
    out = validate_doc(
        client_id="CLIENT-001",
        doc_path=str(samples["t10_simple"]),
        expected_doc_type=DocType.T10,
    )
    assert out["ok"] is True, f"errors: {out['errors']}"
    assert out["doc_type"] == "T10"
    assert out["fully_valid"] is True, f"failure_reason: {out['failure_reason']}"
    fields = out["extracted_fields"]
    assert fields["year_of_assessment"] == "2024/2025"
    assert fields["total_gross_remuneration"] == 4_800_000.00
    assert fields["total_tax_deducted"] == 540_000.00
    assert fields["employer_name"] and "ACME" in fields["employer_name"]
    # confidence ≥ 0.7 on clean synthetic
    assert out["confidence"] >= 0.7
    # SF writes proposed = T10_received__c
    proposed = out["sf_writes_proposed"]
    assert len(proposed) == 1
    assert proposed[0]["field"] == "T10_received__c"
    assert proposed[0]["value"] is True
    assert proposed[0]["object"] == "Tax_File__c"
    assert proposed[0]["client_id"] == "CLIENT-001"


def test_t10_messy_format_regex_tolerance(samples):
    """Messy T10 (Y/A spacing, Rs. prefix, APIT label) — regex still finds core fields."""
    out = validate_doc(
        client_id="CLIENT-002",
        doc_path=str(samples["t10_messy"]),
        expected_doc_type=DocType.T10,
    )
    assert out["ok"] is True
    assert out["doc_type"] == "T10"
    fields = out["extracted_fields"]
    assert fields["year_of_assessment"] == "2024/2025"
    assert fields["total_gross_remuneration"] == 2_160_000.0
    assert fields["total_tax_deducted"] == 162_000.0
    # Confidence may be marginally lower than the clean sample (no remitted line,
    # no benefits_excluded line) but still >= 0.6.
    assert out["confidence"] >= 0.6


def test_t10_auto_detect_doc_type(samples):
    """Pass expected_doc_type=None; should auto-detect from keywords."""
    out = validate_doc(
        client_id="CLIENT-003",
        doc_path=str(samples["t10_simple"]),
        expected_doc_type=None,
    )
    assert out["ok"] is True
    assert out["doc_type"] == "T10"


# ---- BANK_INTEREST_WHT tests -----------------------------------------------


def test_bank_interest_wht_schema_accepts_partial(samples):
    """BANK_INTEREST_WHT regex layer not implemented in v1.0; Gemini-only.

    Without GEMINI_API_KEY (skip-gracefully), this falls through to the
    'no extractor available' failure path — assert that path is graceful.
    """
    import os

    if os.environ.get("GEMINI_API_KEY"):
        pytest.skip(
            "GEMINI_API_KEY present — bank extraction would actually call Gemini "
            "(not a unit-test scope). Skip to keep tests hermetic."
        )

    out = validate_doc(
        client_id="CLIENT-004",
        doc_path=str(samples["bank_interest_wht"]),
        expected_doc_type=DocType.BANK_INTEREST_WHT,
    )
    # No Gemini, no regex layer for bank → ok=False with graceful reason.
    assert out["ok"] is False
    assert out["doc_type"] == "BANK_INTEREST_WHT"
    assert "no extractor" in (out["failure_reason"] or "").lower()
    assert out["fully_valid"] is False


def test_bank_interest_wht_auto_detect(samples):
    """Auto-detection should classify the bank sample as BANK_INTEREST_WHT."""
    import os
    if os.environ.get("GEMINI_API_KEY"):
        pytest.skip("Hermetic test skipped when Gemini key set")
    out = validate_doc(
        client_id="CLIENT-005",
        doc_path=str(samples["bank_interest_wht"]),
        expected_doc_type=None,
    )
    # Either ok=False (no extractor) but doc_type DOES get auto-classified.
    assert out["doc_type"] == "BANK_INTEREST_WHT"


# ---- Failure routing -------------------------------------------------------


def test_file_not_found_returns_graceful_failure(tmp_path):
    out = validate_doc(
        client_id="CLIENT-006",
        doc_path=str(tmp_path / "does_not_exist.pdf"),
        expected_doc_type=DocType.T10,
    )
    assert out["ok"] is False
    assert "file not found" in out["failure_reason"].lower()
    assert out["fully_valid"] is False


def test_empty_file_returns_graceful_failure(samples):
    out = validate_doc(
        client_id="CLIENT-007",
        doc_path=str(samples["empty"]),
        expected_doc_type=DocType.T10,
    )
    # Empty PDF has 1 blank page; pdfplumber returns empty text. Goes through
    # the "no text extractable" path.
    assert out["ok"] is False
    assert out["fully_valid"] is False
    # Either "no text extractable" or "no records" — depends on whether OCR
    # is available. Both are acceptable graceful failures.
    fr = (out["failure_reason"] or "").lower()
    assert any(s in fr for s in ("no text", "could not", "required fields"))


def test_malformed_pdf_returns_graceful_failure(samples):
    out = validate_doc(
        client_id="CLIENT-008",
        doc_path=str(samples["malformed"]),
        expected_doc_type=DocType.T10,
    )
    assert out["ok"] is False
    assert out["fully_valid"] is False
    # Should have at least one error logged.
    assert len(out["errors"]) >= 1


def test_unknown_doc_type_string_handled_gracefully(samples):
    out = validate_doc(
        client_id="CLIENT-009",
        doc_path=str(samples["t10_simple"]),
        expected_doc_type="NOT_A_REAL_DOCTYPE",
    )
    assert out["ok"] is False
    assert "unknown expected_doc_type" in out["failure_reason"]


# ---- Tesseract graceful-skip test ------------------------------------------


def test_tesseract_skipped_gracefully_when_binary_missing(samples, monkeypatch):
    """Even without Tesseract installed, T10 extraction on a text PDF must succeed
    (pdfplumber handles it). Confirm the OCR layer is purely a fallback.
    """
    # Force the test to behave as if Tesseract is absent by stubbing the import-level flag.
    import fiesta.delivery_ops.doc_lens as dl

    monkeypatch.setattr(dl, "_PYTESSERACT_AVAILABLE", False)
    out = validate_doc(
        client_id="CLIENT-010",
        doc_path=str(samples["t10_simple"]),
        expected_doc_type=DocType.T10,
    )
    # Should still succeed via pdfplumber.
    assert out["ok"] is True
    assert out["text_extraction_layer"] == "pdfplumber"
    assert out["fully_valid"] is True


# ---- Gemini graceful-skip test ---------------------------------------------


def test_gemini_skipped_when_key_missing(samples, monkeypatch):
    """Without GEMINI_API_KEY, T10 must still extract via regex fallback."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    out = validate_doc(
        client_id="CLIENT-011",
        doc_path=str(samples["t10_simple"]),
        expected_doc_type=DocType.T10,
    )
    assert out["ok"] is True
    # extraction_method should be 'regex' when Gemini is unavailable.
    assert out["extraction_method"] == "regex"
    assert out["fully_valid"] is True


# ---- SF write proposal structure -------------------------------------------


def test_sf_writes_proposed_match_commaut_field_mapping(samples):
    """Verify the proposed SF write matches the PROVED 18-key field_mapping.

    PROVED writer attribution: T10 → field_mapping[1] → T10_received__c
    (Commaut2.0/dev:src/dv_up.py:tik_and_upload, SHA d0d5cc7).
    """
    out = validate_doc(
        client_id="CLIENT-012",
        doc_path=str(samples["t10_simple"]),
        expected_doc_type=DocType.T10,
    )
    assert out["ok"] is True
    assert out["fully_valid"] is True
    proposed = out["sf_writes_proposed"]
    assert len(proposed) == 1
    p = proposed[0]
    assert p["object"] == "Tax_File__c"
    assert p["field"] == "T10_received__c"  # PROVED field_mapping[1]
    assert p["value"] is True
    assert p["idempotent"] is True  # mirrors Commaut: skip if already True
    assert p["source"] == "fiesta.delivery_ops.doc_lens"


# ---- Stub doc-type tests ---------------------------------------------------


def test_stub_doc_types_return_unimplemented_failure(samples):
    """v1.1 stubs (A&L, BALANCE, EMPLOYER_LETTER) should report graceful UNPROVED state."""
    import os
    if os.environ.get("GEMINI_API_KEY"):
        pytest.skip("Hermetic test skipped when Gemini key set")
    for dt in [DocType.A_AND_L, DocType.BALANCE_CONFIRMATION, DocType.EMPLOYER_LETTER]:
        out = validate_doc(
            client_id="CLIENT-stub",
            doc_path=str(samples["t10_simple"]),
            expected_doc_type=dt,
        )
        # Stubs have no Gemini schema and no regex layer → ok=False, graceful.
        assert out["ok"] is False, f"{dt.value} should fail without an extractor"
        # No SF writes proposed for failures.
        assert out["sf_writes_proposed"] == []


# ---- Return payload contract assertions ------------------------------------


def test_return_payload_shape(samples):
    """Every result dict has the documented keys."""
    out = validate_doc(
        client_id="CLIENT-shape",
        doc_path=str(samples["t10_simple"]),
        expected_doc_type=DocType.T10,
    )
    expected_keys = {
        "ok",
        "client_id",
        "doc_type",
        "confidence",
        "extracted_fields",
        "fully_valid",
        "failure_reason",
        "sf_writes_proposed",
        "extraction_method",
        "text_extraction_layer",
        "errors",
        "raw_text_sample",
    }
    assert set(out.keys()) == expected_keys
    # JSON-serializable
    json.dumps(out, default=str)
