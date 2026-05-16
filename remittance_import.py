"""
Bank-statement importer for the Remittance Ledger.

Flow: user uploads PDF/CSV → we extract text → Gemini classifies inward
foreign-currency credits → review screen shows each candidate with a
checkbox + editable fields → user confirms → bulk insert.

The 'agent fills the form' pattern. Built 2026-05-17 (Opus birthday build).
"""
import csv
import io
import json
import logging
import re
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# PDF / CSV text extraction
# --------------------------------------------------------------------------- #

def extract_pdf_text(file_bytes: bytes) -> str:
    """Pull text from every page. Empty string for image-only PDFs."""
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        from pypdf import PdfReader  # type: ignore
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        chunks = []
        for i, page in enumerate(reader.pages[:50]):
            try:
                t = page.extract_text() or ""
            except Exception as e:
                logger.warning("PDF page %d extract failed: %s", i, e)
                t = ""
            chunks.append(f"--- page {i + 1} ---\n{t}")
        return "\n".join(chunks).strip()
    except Exception as e:
        logger.error("PDF text extraction failed: %s", e)
        return ""


def extract_csv_rows(file_bytes: bytes) -> str:
    """Decode CSV bytes → first 200 rows joined as plain text for Gemini."""
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            text = file_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return ""

    out = io.StringIO()
    try:
        reader = csv.reader(io.StringIO(text))
        for i, row in enumerate(reader):
            if i >= 200:
                break
            out.write(" | ".join(c.strip() for c in row) + "\n")
    except Exception as e:
        logger.warning("CSV parse fell back to raw text: %s", e)
        return text[:50000]
    return out.getvalue()


# --------------------------------------------------------------------------- #
# Gemini classification
# --------------------------------------------------------------------------- #

GEMINI_PROMPT = """You are FIESTA's bank-statement parser for Sri Lankan foreign-income earners filing under PN/IT/2025-01 (15% flat rate from 2025-04-01).

I'll give you the raw text of a bank statement. Find every CREDIT (money IN, not OUT) and tell me which ones look like inward foreign-currency remittances.

For each credit, return a JSON object in a list. Use this exact shape — strings unless noted:

{
  "row_index": <integer, 0-based, in display order>,
  "txn_date": "YYYY-MM-DD" or null if unparseable,
  "description": "<raw bank description, trimmed>",
  "lkr_amount": <number — LKR credited to the account>,
  "foreign_currency": "USD"|"GBP"|"EUR"|"AUD"|... or null,
  "foreign_amount": <number — original foreign-ccy amount, or null>,
  "implied_rate": <number — foreign_amount converted at this rate gives lkr_amount, or null>,
  "likely_payer": "<best guess at the payer's name from description>" or null,
  "source_country_iso2": "AU"|"US"|"GB"|... or null,
  "is_foreign_remittance": true|false,
  "confidence": "high"|"medium"|"low",
  "reason": "<short justification: what in the description triggered the classification>"
}

CLASSIFICATION CUES (set is_foreign_remittance=true when ANY appears):
- Words: "INWARD", "REMIT", "REMITTANCE", "FT IN", "TT IN", "TELEGRAPHIC", "SWIFT", "MT103", "FOREIGN", "INTL", "INTERNATIONAL", "WIRE IN"
- Foreign currency code mentioned (USD/GBP/EUR/AUD/CAD/SGD/AED/JPY/CHF/NZD/SEK/HKD)
- "@" or "RATE" or "FX" or "CONVERSION" combined with two numbers (foreign amount and LKR)
- Sender name appears to be a foreign company / individual

NEGATIVE CUES (set is_foreign_remittance=false):
- "SALARY", "PAYROLL", "PENSION" from a SL employer
- "ATM", "POS", "PURCHASE", "EFT" outbound
- Internal bank transfers ("FUND TRANSFER FROM SELF", "OWN A/C")
- Refunds / reversals
- DEBIT entries (these are OUT, not IN — exclude entirely; only return credits)

NEVER invent numbers. If the description gives only one amount, that's lkr_amount. Foreign amount and rate stay null. If the date format is ambiguous, prefer DD/MM/YYYY (SL/UK norm) then convert to YYYY-MM-DD.

Return a JSON object: {"credits": [...]}. No prose, no markdown fences, just the JSON.

BANK STATEMENT TEXT:
---
"""


def classify_with_gemini(statement_text: str) -> List[Dict[str, Any]]:
    """Send the statement text to Gemini and parse the credit list."""
    if not statement_text.strip():
        return []

    try:
        import google.generativeai as genai
        import os
        genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))
    except Exception as e:
        logger.error("Gemini SDK config failed: %s", e)
        return []

    # Truncate to keep token budget sane; bank statements compress well.
    text = statement_text[:60000]

    for model_name in ("gemini-2.5-flash", "gemini-3-flash-preview", "gemini-2.5-pro"):
        try:
            model = genai.GenerativeModel(model_name)
            resp = model.generate_content(
                GEMINI_PROMPT + text,
                generation_config={"response_mime_type": "application/json", "temperature": 0.1},
                request_options={"timeout": 90},
            )
            raw = resp.text if hasattr(resp, "text") else str(resp)
            return _parse_gemini_response(raw)
        except Exception as e:
            logger.warning("Gemini call (%s) failed: %s", model_name, e)
            continue
    logger.error("All Gemini models failed for bank-statement classification")
    return []


def _parse_gemini_response(raw: str) -> List[Dict[str, Any]]:
    """Tolerant JSON parsing — strips fences if Gemini adds them despite mime hint."""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    try:
        obj = json.loads(s)
    except Exception as e:
        logger.error("Could not parse Gemini JSON: %s | raw=%r", e, raw[:500])
        return []
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and "credits" in obj:
        return obj["credits"]
    if isinstance(obj, dict) and "items" in obj:
        return obj["items"]
    logger.error("Unexpected Gemini schema: keys=%s", list(obj.keys()) if isinstance(obj, dict) else type(obj))
    return []


# --------------------------------------------------------------------------- #
# Normalisation for the review page (defensive — Gemini's not always perfect)
# --------------------------------------------------------------------------- #

CURRENCY_OK = {"USD", "GBP", "EUR", "AUD", "CAD", "SGD", "AED", "JPY", "CHF", "NZD", "SEK", "HKD", "LKR"}


def _safe_decimal(v) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _safe_date(v) -> Optional[date]:
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def normalise_candidates(raw_credits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Coerce Gemini output into a clean shape the review template can rely on."""
    out = []
    for i, c in enumerate(raw_credits):
        if not isinstance(c, dict):
            continue
        ccy = (c.get("foreign_currency") or "").strip().upper()
        if ccy and ccy not in CURRENCY_OK:
            ccy = ""
        d = _safe_date(c.get("txn_date"))
        out.append({
            "row_index": int(c.get("row_index", i)),
            "txn_date": d.isoformat() if d else "",
            "description": (c.get("description") or "")[:300].strip(),
            "lkr_amount": _safe_decimal(c.get("lkr_amount")) or Decimal("0"),
            "foreign_currency": ccy,
            "foreign_amount": _safe_decimal(c.get("foreign_amount")),
            "implied_rate": _safe_decimal(c.get("implied_rate")),
            "likely_payer": (c.get("likely_payer") or "")[:255].strip() or None,
            "source_country_iso2": (c.get("source_country_iso2") or "")[:2].strip().upper() or None,
            "is_foreign_remittance": bool(c.get("is_foreign_remittance")),
            "confidence": (c.get("confidence") or "low").lower(),
            "reason": (c.get("reason") or "")[:280].strip(),
        })
    # Sort by date descending, then by amount descending
    out.sort(key=lambda r: (r["txn_date"] or "", r["lkr_amount"]), reverse=True)
    # Re-number row_index after sort so checkbox names align with display
    for i, r in enumerate(out):
        r["row_index"] = i
    return out


def parse_upload(filename: str, file_bytes: bytes) -> Tuple[str, List[Dict[str, Any]]]:
    """Top-level entry — returns (kind, normalised_candidates).

    kind ∈ {"pdf","csv","unknown"}. Empty list = nothing extractable.
    """
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        text = extract_pdf_text(file_bytes)
        if not text:
            return "pdf", []
        return "pdf", normalise_candidates(classify_with_gemini(text))
    if name.endswith(".csv") or name.endswith(".tsv"):
        text = extract_csv_rows(file_bytes)
        if not text:
            return "csv", []
        return "csv", normalise_candidates(classify_with_gemini(text))
    # Best-effort: try CSV decode first, then PDF
    text = extract_csv_rows(file_bytes)
    if text:
        return "unknown", normalise_candidates(classify_with_gemini(text))
    text = extract_pdf_text(file_bytes)
    if text:
        return "unknown", normalise_candidates(classify_with_gemini(text))
    return "unknown", []
