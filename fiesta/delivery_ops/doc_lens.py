"""doc_lens — FIESTA document extraction + validation.

Single-shot API: `validate_doc(client_id, doc_path, expected_doc_type=None)`
returns a dict mirroring the contract Commaut2.0/dev:tik_and_upload() implements
on Lanka.tax, but FIESTA-side rather than SF-writing.

PROVED-writer attribution (Step 2b honesty gate, mandatory for SF field refs):
  - On `fully_valid=True`, this module proposes which Tax_File__c boolean to
    flip via the canonical 18-key field_mapping in
    Commaut2.0/dev:src/dv_up.py:tik_and_upload() (SHA d0d5cc7, read 2026-05-19).
  - The actual SF write is NOT performed here — Subagent E (Delivery Ops
    Command consumer) writes the doc_received event on the FIESTA spine AND
    optionally invokes the Lanka.tax bridge (per PCSE Strategist D §1 row 14).
  - The `sf_writes_proposed` field in the return payload is what Subagent E
    consumes — it is a proposal, not a write.

Layered extraction pattern (PORTED from doclens-v1, NOT blindly copied):
  1. pdfplumber for text PDFs (most modern uploads)
  2. pytesseract OCR fallback if pdfplumber yields <200 chars (image PDFs,
     older scans). Graceful skip if Tesseract binary missing.
  3. Field extraction:
     - PRIMARY: Gemini-prompt-based (mirrors doclens-v1 Gemini-2.5-flash
       schema-validated extraction with structured_output JSON schema). Uses
       GEMINI_API_KEY env var; gracefully falls back if absent.
     - FALLBACK: regex extraction (v1 t10_extractor pattern) for the doc types
       where deterministic patterns work cleanly (T10).
  4. Pydantic schema validation per doc_type — `is_fully_valid()` per-schema
     gates whether the doc is consumable for downstream computation.

Honest-uncertainty contract (Step 2b):
  - SL-bank-specific prompts from doclens-v1 are NOT copied; fresh prompts
    written here using the doclens-v1 PATTERN (few-shot + Pydantic schema +
    confidence gating). Per council #2 §5.1 mitigation.
  - confidence is a heuristic — NOT a calibrated probability. Real-doc
    validation gate per council #2 §3 + D §4.4: classification ≥0.95,
    extraction ≥0.85 before FIESTA-internal prod activation.

References:
  - working files/_cockpit_fiesta/COUNCIL_SYNTHESIS_REPO_PORTING_20260519.md
    §1 (OCR repo table), §3 (effort row #4), §4 (Q on OCR repos), §5.1-5.2.
  - working files/_cockpit_fiesta/PCSE_STRATEGIST_D_FIESTA_PARITY_20260519.md
    §4.1-4.4 (extraction pattern + accuracy phase gate).
  - working files/lanka_tax_repos_source/doclens-v1/employment_logic.py
    (pattern source — DO NOT copy SL-bank prompts; copy the PATTERN).
  - working files/lanka_tax_repos_source/Commaut2.0/src/dv_up.py
    (PROVED 18-key field_mapping + idempotency contract).
  - working files/lanka_tax_repos_source/doclens-v1/case_creation.py
    (failure-routing pattern → Subagent E creates Case on `ok=false`).
  - working files/ocr/t10_extractor.py (v1 reference — layered fallback +
    confidence scoring approach this module carries forward).
"""

from __future__ import annotations

import json
import os
import re
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .schemas import (
    AssetsLiabilitiesExtraction,
    BalanceConfirmationExtraction,
    BankInterestWhtExtraction,
    EmployerLetterExtraction,
    T10Extraction,
)
from .schemas.bank_interest_wht import (
    is_fully_valid as _bank_is_fully_valid,
)
from .schemas.stubs import (
    is_a_and_l_fully_valid,
    is_balance_confirmation_fully_valid,
    is_employer_letter_fully_valid,
)
from .schemas.t10 import is_fully_valid as _t10_is_fully_valid

# ---- Soft deps -------------------------------------------------------------

try:
    import pdfplumber  # type: ignore
    _PDFPLUMBER_AVAILABLE = True
except Exception:  # pragma: no cover
    pdfplumber = None  # type: ignore
    _PDFPLUMBER_AVAILABLE = False

try:
    import pytesseract  # type: ignore
    from PIL import Image  # type: ignore  # noqa: F401  (used indirectly)
    _PYTESSERACT_AVAILABLE = True
except Exception:  # pragma: no cover
    pytesseract = None  # type: ignore
    _PYTESSERACT_AVAILABLE = False

try:
    import google.generativeai as genai  # type: ignore
    _GENAI_AVAILABLE = True
except Exception:  # pragma: no cover
    genai = None  # type: ignore
    _GENAI_AVAILABLE = False


# ---- Doc types -------------------------------------------------------------


class DocType(str, Enum):
    """Doc types recognized by FIESTA doc_lens.

    Values mirror the doclens-v1 identification taxonomy + the 18-key
    Commaut2.0/dev field_mapping subset most commonly seen in client uploads.
    """

    T10 = "T10"                                # PROVED: field_mapping[1]
    BANK_INTEREST_WHT = "BANK_INTEREST_WHT"   # PROVED: field_mapping[2]
    BALANCE_CONFIRMATION = "BALANCE_CONFIRMATION"  # PROVED: field_mapping[18] (stub)
    A_AND_L = "A_AND_L"                        # PROVED: field_mapping[9] (stub)
    EMPLOYER_LETTER = "EMPLOYER_LETTER"       # UNPROVED writer (stub)
    UNKNOWN = "UNKNOWN"


# PROVED 18-key field_mapping from Commaut2.0/dev:src/dv_up.py:tik_and_upload
# (SHA d0d5cc7, read 2026-05-19). Subset retained here for the doc types
# v1.0 / v1.1 covers. Used to build `sf_writes_proposed` in the return payload.
DOC_TYPE_TO_SF_FIELD: dict[DocType, str] = {
    DocType.T10: "T10_received__c",
    DocType.BANK_INTEREST_WHT: "Bank_documents_received__c",
    DocType.BALANCE_CONFIRMATION: "Bank_documents_received__c",
    DocType.A_AND_L: "Assets_and_Liabilities_form_received__c",
    # EMPLOYER_LETTER intentionally absent — UNPROVED writer attribution.
}


_TEXT_FALLBACK_THRESHOLD = 200  # chars; below this, fall back to OCR


# ---- Layered text extraction (carried over from v1) -----------------------


def _extract_text_pdfplumber(pdf_path: str, errors: list[str]) -> str:
    if not _PDFPLUMBER_AVAILABLE:
        errors.append("pdfplumber not available")
        return ""
    try:
        parts: list[str] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
        return "\n".join(parts)
    except Exception as exc:
        errors.append(f"pdfplumber error: {type(exc).__name__}: {exc}")
        return ""


def _extract_text_tesseract(pdf_path: str, errors: list[str]) -> str:
    """OCR fallback using Tesseract.

    Skips gracefully if Tesseract binary not installed (counts as pass per
    test instructions). Uses pdfplumber's `to_image` for rasterization to
    avoid poppler dependency on Windows.
    """
    if not _PYTESSERACT_AVAILABLE:
        errors.append("pytesseract not available")
        return ""
    if not _PDFPLUMBER_AVAILABLE:
        errors.append("pdfplumber required for OCR rasterization")
        return ""
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:
        errors.append(f"tesseract binary missing: {exc}")
        return ""
    try:
        parts: list[str] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                img = page.to_image(resolution=200).original
                parts.append(pytesseract.image_to_string(img) or "")
        return "\n".join(parts)
    except Exception as exc:
        errors.append(f"tesseract error: {type(exc).__name__}: {exc}")
        return ""


def _extract_text_layered(pdf_path: str, errors: list[str]) -> tuple[str, str]:
    """Return (text, extraction_method) — pdfplumber → tesseract → hybrid → none."""
    text = _extract_text_pdfplumber(pdf_path, errors)
    if len(text.strip()) >= _TEXT_FALLBACK_THRESHOLD:
        return text, "pdfplumber"

    ocr_text = _extract_text_tesseract(pdf_path, errors)
    if not ocr_text and not text:
        return "", "none"
    if not ocr_text:
        return text, "pdfplumber"
    if not text:
        return ocr_text, "tesseract"
    return f"{text}\n{ocr_text}", "hybrid"


# ---- Doc-type auto-detection (regex + keyword heuristics) ------------------
#
# Lightweight; covers the common cases. Detailed classification (Gemini-based
# few-shot) is invoked only when this falls through to UNKNOWN AND Gemini is
# available. Pattern: doclens-v1/identify.py's general approach (LLM
# classification with structured output), but with regex as the fast path.


_T10_KEYWORDS = (
    "year of assessment",
    "y/a 20",
    "t10",
    "t 10",
    "apit",
    "statement of employee",
    "total gross remuneration",
    "total amount of tax deducted",
)

_BANK_INTEREST_KEYWORDS = (
    "interest income",
    "withholding tax",
    "wht certificate",
    "interest paid",
    "balance as at",
    "balance confirmation",
    "savings account",
    "fixed deposit",
)

_A_AND_L_KEYWORDS = (
    "assets and liabilities",
    "a&l declaration",
    "declaration of assets",
    "schedule of assets",
)

_EMPLOYER_LETTER_KEYWORDS = (
    "to whom it may concern",
    "this is to confirm that",
    "employed with",
    "salary confirmation",
    "employment confirmation",
)


def _auto_detect_doc_type(text: str) -> DocType:
    """Cheap regex/keyword classifier."""
    lower = text.lower()

    def hits(keywords: tuple[str, ...]) -> int:
        return sum(1 for kw in keywords if kw in lower)

    scores = {
        DocType.T10: hits(_T10_KEYWORDS),
        DocType.BANK_INTEREST_WHT: hits(_BANK_INTEREST_KEYWORDS),
        DocType.A_AND_L: hits(_A_AND_L_KEYWORDS),
        DocType.EMPLOYER_LETTER: hits(_EMPLOYER_LETTER_KEYWORDS),
    }
    best = max(scores.items(), key=lambda kv: kv[1])
    if best[1] == 0:
        return DocType.UNKNOWN

    # Disambiguate BALANCE_CONFIRMATION vs BANK_INTEREST_WHT: a doc that
    # mentions interest entries is INTEREST_WHT; a doc that mentions only
    # 'balance as at' is BALANCE_CONFIRMATION.
    if best[0] == DocType.BANK_INTEREST_WHT:
        if "interest" not in lower and "balance" in lower:
            return DocType.BALANCE_CONFIRMATION
    return best[0]


# ---- Field extraction: regex (T10 only — proved pattern from v1) ----------


_NUM = r"(?:LKR\s*|Rs\.?\s*)?([\d,]+(?:\.\d{1,2})?)"

_T10_PATTERNS = {
    "year_of_assessment": [
        re.compile(r"Year\s*of\s*Assessment[^\d]{0,40}(\d{4})\s*[/-]\s*(\d{4})", re.IGNORECASE),
        re.compile(r"Y[./]A[.:]?\s*(\d{4})\s*[/-]\s*(\d{4})", re.IGNORECASE),
        re.compile(r"(\d{4})\s*[/-]\s*(\d{4})"),
    ],
    "total_gross_remuneration": [
        re.compile(r"Total\s*Gross\s*Remuneration[^\d-]{0,50}" + _NUM, re.IGNORECASE),
        re.compile(r"Gross\s*Remuneration[^\d-]{0,50}" + _NUM, re.IGNORECASE),
    ],
    "total_tax_deducted": [
        re.compile(r"Total\s*Amount\s*of\s*Tax\s*Deducted[^\d-]{0,50}" + _NUM, re.IGNORECASE),
        re.compile(r"Total\s*Tax\s*Deducted[^\d-]{0,50}" + _NUM, re.IGNORECASE),
        re.compile(r"APIT\s*Deducted[^\d-]{0,50}" + _NUM, re.IGNORECASE),
        re.compile(r"Tax\s*Deducted[^\d-]{0,50}" + _NUM, re.IGNORECASE),
    ],
    "benefits_excluded_for_tax": [
        re.compile(r"(?:Value\s*of\s*)?Benefits\s*Excluded[^\d-]{0,50}" + _NUM, re.IGNORECASE),
    ],
    "total_amount_remitted": [
        re.compile(r"Total\s*Amount\s*Remitted[^\d-]{0,50}" + _NUM, re.IGNORECASE),
        re.compile(r"Amount\s*Remitted[^\d-]{0,50}" + _NUM, re.IGNORECASE),
    ],
    "employer_name": [
        re.compile(r"Name\s*of\s*(?:the\s*)?Employer[^\n:]*[:\-]?\s*([^\n]{3,120})", re.IGNORECASE),
        re.compile(r"Employer[^\n:]*[:\-]\s*([^\n]{3,120})", re.IGNORECASE),
    ],
    "employee_name": [
        re.compile(r"Name\s*of\s*(?:the\s*)?Employee[^\n:]*[:\-]?\s*([^\n]{3,120})", re.IGNORECASE),
        re.compile(r"Employee\s*Name[^\n:]*[:\-]\s*([^\n]{3,120})", re.IGNORECASE),
    ],
    "employer_tin": [
        re.compile(r"Employer\s*TIN[^\d]{0,20}(\d{6,12})", re.IGNORECASE),
        re.compile(r"\bTIN\b[^\d]{0,20}(\d{6,12})", re.IGNORECASE),
    ],
    "client_nic": [
        re.compile(r"(?:Employee|Client)\s*NIC[^\d]{0,20}([\dA-Za-z]{9,12})", re.IGNORECASE),
        re.compile(r"\bNIC\b[^\d]{0,20}([\dA-Za-z]{9,12})", re.IGNORECASE),
    ],
}


def _parse_num(raw: str) -> float | None:
    if not raw:
        return None
    try:
        return float(raw.replace(",", "").strip())
    except ValueError:
        return None


def _find_first_str(text: str, patterns: list[re.Pattern[str]]) -> str | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            return m.group(1).strip(" :|,-") if m.lastindex else None
    return None


def _find_year(text: str, patterns: list[re.Pattern[str]]) -> str | None:
    for pat in patterns:
        m = pat.search(text)
        if m and m.lastindex == 2:
            s, e = int(m.group(1)), int(m.group(2))
            if e - s == 1 and 1990 <= s <= 2099:
                return f"{s}/{e}"
    return None


def _find_num_field(text: str, patterns: list[re.Pattern[str]]) -> float | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            return _parse_num(m.group(1))
    return None


def _regex_extract_t10(text: str) -> dict[str, Any]:
    """Extract T10 fields via regex. Returns a dict suitable for T10Extraction()."""
    return {
        "year_of_assessment": _find_year(text, _T10_PATTERNS["year_of_assessment"]),
        "employer_tin": _find_first_str(text, _T10_PATTERNS["employer_tin"]),
        "client_nic": _find_first_str(text, _T10_PATTERNS["client_nic"]),
        "employer_name": _find_first_str(text, _T10_PATTERNS["employer_name"]),
        "employee_name": _find_first_str(text, _T10_PATTERNS["employee_name"]),
        "total_gross_remuneration": _find_num_field(
            text, _T10_PATTERNS["total_gross_remuneration"]
        ),
        "total_tax_deducted": _find_num_field(text, _T10_PATTERNS["total_tax_deducted"]),
        "benefits_excluded_for_tax": _find_num_field(
            text, _T10_PATTERNS["benefits_excluded_for_tax"]
        )
        or 0.0,
        "total_amount_remitted": _find_num_field(text, _T10_PATTERNS["total_amount_remitted"]),
    }


# ---- Field extraction: Gemini (optional, graceful degrade) ----------------
#
# Mirrors the doclens-v1 PATTERN — Gemini call with structured-output JSON
# schema. Does NOT reuse SL-bank-specific prompts (per council #2 §5.1
# mitigation). Fresh prompts written below per doc_type.


_T10_GEMINI_PROMPT = """\
You are extracting fields from a Sri Lankan T10 employer income statement.
T10 is the APIT (Advance Personal Income Tax) statement an employer issues
to an employee. Read the document carefully and return JSON matching the
schema. Use 0.0 for numeric fields that are absent / blank / 'N/A'; use
empty string for absent text fields.

CRITICAL FIELD ALIGNMENT WARNING (per doclens-v1 prompt L518-525):
  - `total_tax_deducted` (Total Amount of Tax Deducted) and
    `benefits_excluded_for_tax` (Value of Benefits Excluded for Tax) are
    TWO SEPARATE fields with DIFFERENT values. Document alignment can make
    `total_tax_deducted` appear visually on the `benefits_excluded_for_tax`
    line — ALWAYS verify the field label, NOT the visual position. If the
    benefits line shows 0 / blank / "-", return 0.0; do NOT copy the
    `total_tax_deducted` value there.

Year of assessment format: 'YYYY/YYYY' (e.g. '2024/2025'). If the document
shows '2024 / 2025' or '2024-2025', normalize to '2024/2025'.

Return ONLY the JSON object matching the schema. No prose.
"""

_T10_GEMINI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "year_of_assessment": {"type": "STRING"},
        "employer_tin": {"type": "STRING"},
        "client_nic": {"type": "STRING"},
        "employer_name": {"type": "STRING"},
        "employee_name": {"type": "STRING"},
        "total_gross_remuneration": {"type": "NUMBER"},
        "total_tax_deducted": {"type": "NUMBER"},
        "benefits_excluded_for_tax": {"type": "NUMBER"},
        "total_amount_remitted": {"type": "NUMBER"},
        "date": {"type": "STRING"},
        "email": {"type": "STRING"},
    },
    "required": [
        "year_of_assessment",
        "employer_tin",
        "employer_name",
        "total_gross_remuneration",
        "total_tax_deducted",
    ],
}


_BANK_GEMINI_PROMPT = """\
You are extracting fields from a Sri Lankan bank interest + withholding-tax
statement (savings account, fixed deposit, or T-bill confirmation). Read the
document carefully and return JSON matching the schema.

Granularity: how often interest is posted. 'Monthly' = 12 entries,
'Quarterly' = 4 entries, 'Annually' = 1 entry.

Year of assessment: Sri Lankan tax year runs April 1 to March 31. If the
interest period ends 31.03.YYYY, year_of_assessment = '(YYYY-1)/YYYY'. If
the period spans 01.04.YYYY to 31.03.(YYYY+1), year_of_assessment =
'YYYY/(YYYY+1)'. PROVED rule per doclens-v1 scan_bank.py.

WHT certificate IDs: look for 'certificate number' / 'serial no.' / 'reference
no.' associated with the withholding tax. AVOID 'CHQ No.', 'Ref No. PMT DATE',
'Transaction Ref', 'Cheque No.' — those are payment IDs, NOT tax certificate
IDs.

Return ONLY the JSON object. No prose.
"""

_BANK_GEMINI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "bank_name": {"type": "STRING"},
        "branch_name": {"type": "STRING"},
        "account_number": {"type": "STRING"},
        "account_holder_name": {"type": "STRING"},
        "number_of_account_holders": {"type": "INTEGER"},
        "client_nic": {"type": "STRING"},
        "year_of_assessment": {"type": "STRING"},
        "granularity": {"type": "STRING"},
        "balance_lkr": {"type": "NUMBER"},
        "balance_as_of_date": {"type": "STRING"},
        "interest_income": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "period_start_date": {"type": "STRING"},
                    "period_end_date": {"type": "STRING"},
                    "amount": {"type": "NUMBER"},
                },
            },
        },
        "with_holding_tax": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "period_start_date": {"type": "STRING"},
                    "period_end_date": {"type": "STRING"},
                    "amount": {"type": "NUMBER"},
                },
            },
        },
        "wht_cert": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"cert_number": {"type": "STRING"}},
            },
        },
    },
    "required": ["bank_name", "account_number", "year_of_assessment"],
}


def _gemini_extract(
    doc_type: DocType, text: str, errors: list[str]
) -> dict[str, Any] | None:
    """Send extracted text to Gemini for schema-validated field extraction.

    Returns the parsed dict or None on any failure. Graceful: missing key,
    missing lib, API error all return None.
    """
    if not _GENAI_AVAILABLE:
        errors.append("google-generativeai not installed; using regex fallback")
        return None
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        errors.append("GEMINI_API_KEY not set; using regex fallback")
        return None

    if doc_type == DocType.T10:
        prompt, schema = _T10_GEMINI_PROMPT, _T10_GEMINI_SCHEMA
    elif doc_type == DocType.BANK_INTEREST_WHT:
        prompt, schema = _BANK_GEMINI_PROMPT, _BANK_GEMINI_SCHEMA
    else:
        errors.append(f"Gemini extraction not implemented for {doc_type.value}")
        return None

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")
        gen_config = genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=schema,
        )
        response = model.generate_content(
            [prompt, "DOCUMENT TEXT:\n" + text],
            generation_config=gen_config,
        )
        return json.loads(response.text)
    except Exception as exc:
        errors.append(f"gemini error: {type(exc).__name__}: {exc}")
        return None


# ---- Confidence scoring ----------------------------------------------------


def _plausible_lkr(value: float | None, low: float = 1_000.0, high: float = 5_000_000_000.0) -> bool:
    if value is None:
        return False
    return low <= value <= high


def _score_t10_confidence(extraction_dict: dict[str, Any]) -> float:
    """Confidence blend: required-field hit rate + numeric plausibility + sanity."""
    required = ("year_of_assessment", "employer_tin", "employer_name",
                "total_gross_remuneration", "total_tax_deducted")
    hit_rate = sum(1 for f in required if extraction_dict.get(f) not in (None, "", 0.0)) / len(required)

    gross = extraction_dict.get("total_gross_remuneration")
    tax = extraction_dict.get("total_tax_deducted")
    gross_ok = _plausible_lkr(gross, low=50_000.0)
    tax_ok = _plausible_lkr(tax, low=0.0) or tax == 0.0
    plausibility = (int(gross_ok) + int(tax_ok)) / 2.0

    tax_lt_gross = (
        gross is not None and tax is not None and 0 <= tax <= gross
    )
    sanity = 1.0 if tax_lt_gross else 0.0

    return round(0.5 * hit_rate + 0.3 * plausibility + 0.2 * sanity, 3)


def _score_bank_confidence(extraction_dict: dict[str, Any]) -> float:
    """Confidence blend for BANK_INTEREST_WHT."""
    required = ("bank_name", "account_number", "year_of_assessment")
    hit_rate = sum(1 for f in required if extraction_dict.get(f) not in (None, "")) / len(required)

    has_interest = bool(extraction_dict.get("interest_income"))
    has_balance = extraction_dict.get("balance_lkr") is not None
    presence = 1.0 if (has_interest or has_balance) else 0.0

    granularity = extraction_dict.get("granularity")
    interest_count = len(extraction_dict.get("interest_income") or [])
    granularity_match = 1.0
    if has_interest and granularity:
        expected = {"Monthly": 12, "Quarterly": 4, "Annually": 1}.get(granularity)
        if expected is not None and interest_count != expected:
            granularity_match = 0.5

    return round(0.5 * hit_rate + 0.3 * presence + 0.2 * granularity_match, 3)


# ---- Public API ------------------------------------------------------------


def _build_result(
    *,
    ok: bool,
    doc_type: DocType,
    confidence: float,
    extracted_fields: dict[str, Any],
    fully_valid: bool,
    failure_reason: str | None,
    sf_writes_proposed: list[dict[str, Any]],
    extraction_method: str,
    text_layer: str,
    errors: list[str],
    client_id: str,
    raw_text_sample: str,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "client_id": client_id,
        "doc_type": doc_type.value,
        "confidence": confidence,
        "extracted_fields": extracted_fields,
        "fully_valid": fully_valid,
        "failure_reason": failure_reason,
        "sf_writes_proposed": sf_writes_proposed,
        "extraction_method": extraction_method,
        "text_extraction_layer": text_layer,
        "errors": errors,
        "raw_text_sample": raw_text_sample[:500] if raw_text_sample else "",
    }


def validate_doc(
    *,
    client_id: str,
    doc_path: str,
    expected_doc_type: str | DocType | None = None,
) -> dict[str, Any]:
    """Validate + extract a document; return a Commaut-compatible payload.

    Args:
        client_id: FIESTA client identifier (any string the caller uses).
            Mirrors the `record_id` arg of Commaut2.0/dev:tik_and_upload().
        doc_path: Path to PDF or image. PDF preferred.
        expected_doc_type: One of DocType values OR None to auto-detect.

    Returns dict with keys:
        ok                 — bool, True if extraction succeeded structurally
        client_id          — echoed back
        doc_type           — DocType.value, possibly auto-detected
        confidence         — float 0..1, heuristic (NOT calibrated probability)
        extracted_fields   — dict of schema-conformant fields
        fully_valid        — bool, True if doc passes per-doc-type validity gate
        failure_reason     — str | None, set when fully_valid=False
        sf_writes_proposed — list of {object, id, field, value} dicts that
                             Subagent E may apply via the Lanka.tax bridge
                             OR mirror as `doc_received` events on the FIESTA spine
        extraction_method  — 'gemini' | 'regex' | 'none'
        text_extraction_layer — 'pdfplumber' | 'tesseract' | 'hybrid' | 'none'
        errors             — list of human-readable failure notes
        raw_text_sample    — first 500 chars (debug aid)

    Honest-uncertainty contract: see module docstring + CLAUDE.md Step 2b.
    """
    errors: list[str] = []
    path = Path(doc_path)

    # Input validation.
    if not path.exists():
        return _build_result(
            ok=False,
            doc_type=DocType.UNKNOWN,
            confidence=0.0,
            extracted_fields={},
            fully_valid=False,
            failure_reason=f"file not found: {doc_path}",
            sf_writes_proposed=[],
            extraction_method="none",
            text_layer="none",
            errors=[f"file not found: {doc_path}"],
            client_id=client_id,
            raw_text_sample="",
        )
    if path.stat().st_size == 0:
        return _build_result(
            ok=False,
            doc_type=DocType.UNKNOWN,
            confidence=0.0,
            extracted_fields={},
            fully_valid=False,
            failure_reason="file is empty (0 bytes)",
            sf_writes_proposed=[],
            extraction_method="none",
            text_layer="none",
            errors=["file is empty (0 bytes)"],
            client_id=client_id,
            raw_text_sample="",
        )

    # Resolve doc_type from input.
    if isinstance(expected_doc_type, str):
        try:
            doc_type_resolved: DocType | None = DocType(expected_doc_type)
        except ValueError:
            return _build_result(
                ok=False,
                doc_type=DocType.UNKNOWN,
                confidence=0.0,
                extracted_fields={},
                fully_valid=False,
                failure_reason=f"unknown expected_doc_type: {expected_doc_type}",
                sf_writes_proposed=[],
                extraction_method="none",
                text_layer="none",
                errors=[f"unknown expected_doc_type: {expected_doc_type}"],
                client_id=client_id,
                raw_text_sample="",
            )
    elif isinstance(expected_doc_type, DocType):
        doc_type_resolved = expected_doc_type
    else:
        doc_type_resolved = None

    # Layered text extraction.
    text, text_layer = _extract_text_layered(str(path), errors)
    if not text.strip():
        return _build_result(
            ok=False,
            doc_type=DocType.UNKNOWN,
            confidence=0.0,
            extracted_fields={},
            fully_valid=False,
            failure_reason="no text extractable (pdfplumber + Tesseract both failed)",
            sf_writes_proposed=[],
            extraction_method="none",
            text_layer=text_layer,
            errors=errors,
            client_id=client_id,
            raw_text_sample="",
        )

    # Auto-detect doc_type if not specified.
    if doc_type_resolved is None:
        doc_type_resolved = _auto_detect_doc_type(text)
        if doc_type_resolved == DocType.UNKNOWN:
            return _build_result(
                ok=False,
                doc_type=DocType.UNKNOWN,
                confidence=0.0,
                extracted_fields={},
                fully_valid=False,
                failure_reason="could not auto-detect doc_type from text",
                sf_writes_proposed=[],
                extraction_method="none",
                text_layer=text_layer,
                errors=errors,
                client_id=client_id,
                raw_text_sample=text,
            )

    # Extract fields — Gemini primary, regex fallback.
    extraction_method = "none"
    raw_extraction: dict[str, Any] | None = None

    if doc_type_resolved in (DocType.T10, DocType.BANK_INTEREST_WHT):
        raw_extraction = _gemini_extract(doc_type_resolved, text, errors)
        if raw_extraction is not None:
            extraction_method = "gemini"

    # Regex fallback (T10 only — proved patterns from v1 t10_extractor).
    if raw_extraction is None and doc_type_resolved == DocType.T10:
        raw_extraction = _regex_extract_t10(text)
        extraction_method = "regex"

    # No extraction path for stub types.
    if raw_extraction is None:
        return _build_result(
            ok=False,
            doc_type=doc_type_resolved,
            confidence=0.0,
            extracted_fields={},
            fully_valid=False,
            failure_reason=(
                f"no extractor available for {doc_type_resolved.value} "
                "(v1.1 stub — Gemini fallback only, no regex layer)"
            ),
            sf_writes_proposed=[],
            extraction_method="none",
            text_layer=text_layer,
            errors=errors,
            client_id=client_id,
            raw_text_sample=text,
        )

    # Schema validate + per-doc-type validity gate.
    try:
        if doc_type_resolved == DocType.T10:
            extraction = T10Extraction(**raw_extraction)
            extracted_fields = extraction.model_dump()
            confidence = _score_t10_confidence(extracted_fields)
            fully_valid, fv_reason = _t10_is_fully_valid(extraction)
        elif doc_type_resolved == DocType.BANK_INTEREST_WHT:
            extraction = BankInterestWhtExtraction(**raw_extraction)
            extracted_fields = extraction.model_dump()
            confidence = _score_bank_confidence(extracted_fields)
            fully_valid, fv_reason = _bank_is_fully_valid(extraction)
        elif doc_type_resolved == DocType.BALANCE_CONFIRMATION:
            extraction = BalanceConfirmationExtraction(**raw_extraction)
            extracted_fields = extraction.model_dump()
            confidence = 0.0
            fully_valid, fv_reason = is_balance_confirmation_fully_valid(extraction)
        elif doc_type_resolved == DocType.A_AND_L:
            extraction = AssetsLiabilitiesExtraction(**raw_extraction)
            extracted_fields = extraction.model_dump()
            confidence = 0.0
            fully_valid, fv_reason = is_a_and_l_fully_valid(extraction)
        elif doc_type_resolved == DocType.EMPLOYER_LETTER:
            extraction = EmployerLetterExtraction(**raw_extraction)
            extracted_fields = extraction.model_dump()
            confidence = 0.0
            fully_valid, fv_reason = is_employer_letter_fully_valid(extraction)
        else:
            return _build_result(
                ok=False,
                doc_type=doc_type_resolved,
                confidence=0.0,
                extracted_fields=raw_extraction,
                fully_valid=False,
                failure_reason=f"no schema for {doc_type_resolved.value}",
                sf_writes_proposed=[],
                extraction_method=extraction_method,
                text_layer=text_layer,
                errors=errors,
                client_id=client_id,
                raw_text_sample=text,
            )
    except ValidationError as exc:
        return _build_result(
            ok=False,
            doc_type=doc_type_resolved,
            confidence=0.0,
            extracted_fields=raw_extraction,
            fully_valid=False,
            failure_reason=f"pydantic validation failed: {exc.errors()[:3]}",
            sf_writes_proposed=[],
            extraction_method=extraction_method,
            text_layer=text_layer,
            errors=errors + [f"validation: {type(exc).__name__}"],
            client_id=client_id,
            raw_text_sample=text,
        )

    # Build SF write proposals (Commaut field_mapping). Only for fully_valid.
    sf_writes_proposed: list[dict[str, Any]] = []
    if fully_valid:
        sf_field = DOC_TYPE_TO_SF_FIELD.get(doc_type_resolved)
        if sf_field:
            sf_writes_proposed.append({
                "object": "Tax_File__c",
                "id_field": "Customer__c",  # caller resolves Customer→Tax_File join
                "client_id": client_id,
                "field": sf_field,
                "value": True,
                "idempotent": True,  # mirror Commaut: skip if already True
                "source": "fiesta.delivery_ops.doc_lens",
            })

    return _build_result(
        ok=True,
        doc_type=doc_type_resolved,
        confidence=confidence,
        extracted_fields=extracted_fields,
        fully_valid=fully_valid,
        failure_reason=fv_reason if not fully_valid else None,
        sf_writes_proposed=sf_writes_proposed,
        extraction_method=extraction_method,
        text_layer=text_layer,
        errors=errors,
        client_id=client_id,
        raw_text_sample=text,
    )


__all__ = ["validate_doc", "DocType", "DOC_TYPE_TO_SF_FIELD"]


# ---- CLI for ad-hoc inspection --------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import sys

    if len(sys.argv) < 3:
        print(
            "usage: python -m fiesta.delivery_ops.doc_lens <client_id> <doc_path> [doc_type]",
            file=sys.stderr,
        )
        sys.exit(2)
    cid, dpath = sys.argv[1], sys.argv[2]
    dtype = sys.argv[3] if len(sys.argv) > 3 else None
    out = validate_doc(client_id=cid, doc_path=dpath, expected_doc_type=dtype)
    print(json.dumps(out, indent=2, default=str))
    sys.exit(0 if out["ok"] else 1)
