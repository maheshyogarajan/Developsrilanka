"""fiesta.earnings.extractor — doc_lens bridge for the S4 'drop statements' screen.

Single entry point: `extract_statement(statement, db_session)`.

Behaviour contract:
  * Calls fiesta.delivery_ops.doc_lens.validate_doc() with the right
    expected_doc_type derived from Statement.doc_type.
  * Increments Statement.extraction_attempts each call. Caps at
    MAX_EXTRACTION_ATTEMPTS (5).
  * On success (ok=True): parses extracted_fields into IncomeEntry rows, marks
    Statement status=extracted, returns the new IncomeEntry list.
  * On failure: marks Statement status=rejected once attempts reach cap, sets
    failure_reason. Customer is then routed to manual-entry by the UI.
  * Confidence < 0.6 surfaces a banner via the route layer (NOT decided here);
    we just record the confidence on the Statement and pass it forward.

Honest-uncertainty discipline (CLAUDE.md Step 2b):
  * doc_lens.confidence is a heuristic, NOT calibrated probability.
  * IncomeEntry rows from extraction are NOT confirmed_by_customer until the
    customer reviews each one.
  * Low-confidence rows surface for explicit confirmation in the UI.

Doc-type mapping (S4 doc_types → doc_lens DocType):
  bank_statement         → BANK_INTEREST_WHT (extracts interest_income[],
                            with_holding_tax[], balance_lkr). For pure-deposit
                            statements with no interest entries, doc_lens
                            re-classifies as BALANCE_CONFIRMATION; we still
                            consume it (zero income rows extracted) and let
                            the customer enter income manually.
  employer_letter        → T10 if present-as-T10, else EMPLOYER_LETTER (stub
                            schema — currently no auto-extraction; falls
                            through to manual entry).
  foreign_income_receipt → no native doc_lens type; we fall through to manual.
                            (Foreign remittances are best evidenced by SL bank
                            statements showing the inward credit — per
                            reference_foreign_income_remittance_basis.md.)
  other                  → auto-detect via doc_lens; manual fallback on miss.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fiesta.delivery_ops.doc_lens import DocType, validate_doc
from fiesta.earnings.models import (
    IncomeCategory,
    IncomeEntry,
    MAX_EXTRACTION_ATTEMPTS,
    Statement,
    StatementDocType,
    StatementStatus,
    sl_tax_year_for,
)

log = logging.getLogger(__name__)


# S4 doc_type → doc_lens DocType. Some have no clean mapping; those force
# manual-entry by returning None.
_DOC_TYPE_MAP = {
    StatementDocType.BANK_STATEMENT.value: DocType.BANK_INTEREST_WHT,
    StatementDocType.EMPLOYER_LETTER.value: DocType.T10,
    # FOREIGN_INCOME_RECEIPT + OTHER → None → auto-detect or manual fallback.
}


# ---- Date parsing helpers -------------------------------------------------- #


def _parse_iso_date(raw: Any) -> date | None:
    """Tolerant date parser — accepts ISO, slash, and dot separators.

    doc_lens returns dates as strings (YYYY-MM-DD preferred; sometimes
    DD/MM/YYYY or DD.MM.YYYY from messy bank PDFs).
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _safe_decimal(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw).replace(",", "").strip())
    except (ValueError, ArithmeticError):
        return None


# ---- Parsers per doc-type -------------------------------------------------- #


def _build_entries_from_bank(
    user_id: int,
    statement_id: int,
    tax_year_default: str,
    fields: dict,
) -> list[IncomeEntry]:
    """Parse BANK_INTEREST_WHT extraction into IncomeEntry rows.

    One row per `interest_income[i]` entry. Currency defaults to LKR (SL
    bank interest is always LKR at source). Source = bank_name. Category =
    INTEREST.
    """
    rows: list[IncomeEntry] = []
    interest_items = fields.get("interest_income") or []
    bank_name = fields.get("bank_name") or "Unknown bank"
    account = fields.get("account_number") or ""

    for item in interest_items:
        amount = _safe_decimal(item.get("amount"))
        if amount is None or amount <= 0:
            continue
        d_end = _parse_iso_date(item.get("period_end_date"))
        d_start = _parse_iso_date(item.get("period_start_date"))
        d_row = d_end or d_start or date.today()
        ty = sl_tax_year_for(d_row) if d_end or d_start else tax_year_default

        source_str = f"{bank_name}".strip()
        if account:
            source_str += f" ({account[-4:].rjust(4, '*')})" if len(account) >= 4 else f" ({account})"

        rows.append(IncomeEntry(
            user_id=user_id,
            statement_id=statement_id,
            entry_date=d_row,
            currency="LKR",
            amount=amount,
            amount_lkr=amount,
            fx_rate_lkr=Decimal("1"),
            fx_rate_source="lkr_native",
            source=source_str,
            category=IncomeCategory.INTEREST.value,
            confirmed_by_customer=False,
            tax_year=ty,
        ))
    return rows


def _build_entries_from_t10(
    user_id: int,
    statement_id: int,
    tax_year_default: str,
    fields: dict,
) -> list[IncomeEntry]:
    """Parse T10 extraction into a single SALARY IncomeEntry.

    T10 is an annual statement — total_gross_remuneration is the year's gross,
    so it lands as ONE row dated at the YA end (31 March of the closing year).
    """
    gross = _safe_decimal(fields.get("total_gross_remuneration"))
    if gross is None or gross <= 0:
        return []

    employer_name = fields.get("employer_name") or "Employer"
    ya = fields.get("year_of_assessment") or ""  # e.g. '2024/2025'
    if "/" in ya:
        try:
            _, year_end = ya.split("/", 1)
            entry_date = date(int(year_end), 3, 31)
            tax_year = f"{int(year_end) - 1}-{str(year_end)[2:]}"
        except (ValueError, TypeError):
            entry_date = date.today()
            tax_year = tax_year_default
    else:
        entry_date = date.today()
        tax_year = tax_year_default

    return [IncomeEntry(
        user_id=user_id,
        statement_id=statement_id,
        entry_date=entry_date,
        currency="LKR",
        amount=gross,
        amount_lkr=gross,
        fx_rate_lkr=Decimal("1"),
        fx_rate_source="lkr_native",
        source=employer_name,
        category=IncomeCategory.SALARY.value,
        confirmed_by_customer=False,
        tax_year=tax_year,
    )]


# ---- Public API ------------------------------------------------------------ #


def extract_statement(statement: Statement, db_session) -> dict[str, Any]:
    """Run doc_lens against `statement.file_path` and persist extracted rows.

    Caller must commit the session after this returns; we add rows but do NOT
    commit, so the caller controls transaction boundaries.

    Returns dict:
      ok               — bool (True if extraction returned ok=True)
      entries          — list[IncomeEntry] persisted (may be empty)
      confidence       — float
      method           — 'gemini' | 'regex' | 'none'
      doc_type_resolved — doc_lens DocType value or None
      failure_reason   — str | None
      at_attempt_cap   — bool (True if we are now at the 5-attempt limit)
      raw              — full doc_lens payload (kept on Statement.extracted_data)
    """
    # Hard guard: never retry beyond the cap. Caller should route to manual.
    if statement.extraction_attempts >= MAX_EXTRACTION_ATTEMPTS:
        return {
            "ok": False,
            "entries": [],
            "confidence": None,
            "method": "none",
            "doc_type_resolved": None,
            "failure_reason": f"extraction attempts exhausted ({MAX_EXTRACTION_ATTEMPTS}/{MAX_EXTRACTION_ATTEMPTS}); use manual entry",
            "at_attempt_cap": True,
            "raw": None,
        }

    statement.status = StatementStatus.PROCESSING.value
    statement.extraction_attempts = (statement.extraction_attempts or 0) + 1

    expected = _DOC_TYPE_MAP.get(statement.doc_type)  # None → auto-detect
    raw = validate_doc(
        client_id=f"fiesta_user_{statement.user_id}",
        doc_path=statement.file_path,
        expected_doc_type=expected,
    )
    statement.extracted_data = raw
    statement.extraction_confidence = raw.get("confidence")
    statement.extraction_method = raw.get("extraction_method") or "none"

    if not raw.get("ok"):
        statement.failure_reason = raw.get("failure_reason") or "extraction failed"
        if statement.extraction_attempts >= MAX_EXTRACTION_ATTEMPTS:
            statement.status = StatementStatus.REJECTED.value
        else:
            # Still attempts left — surface back to customer for retry/different doc.
            statement.status = StatementStatus.UPLOADED.value
        return {
            "ok": False,
            "entries": [],
            "confidence": statement.extraction_confidence,
            "method": statement.extraction_method,
            "doc_type_resolved": raw.get("doc_type"),
            "failure_reason": statement.failure_reason,
            "at_attempt_cap": statement.at_attempt_cap(),
            "raw": raw,
        }

    # ok=True. Parse extracted_fields into IncomeEntry rows.
    doc_type_resolved = raw.get("doc_type")
    fields = raw.get("extracted_fields") or {}
    tax_year_default = statement.tax_year or sl_tax_year_for(date.today())

    if doc_type_resolved == DocType.BANK_INTEREST_WHT.value:
        entries = _build_entries_from_bank(
            user_id=statement.user_id,
            statement_id=statement.id,
            tax_year_default=tax_year_default,
            fields=fields,
        )
        # Capture bank_name + period range on the Statement for display.
        statement.bank_name = fields.get("bank_name") or statement.bank_name
        if fields.get("balance_as_of_date"):
            d_balance = _parse_iso_date(fields.get("balance_as_of_date"))
            if d_balance:
                statement.period_end = d_balance
    elif doc_type_resolved == DocType.T10.value:
        entries = _build_entries_from_t10(
            user_id=statement.user_id,
            statement_id=statement.id,
            tax_year_default=tax_year_default,
            fields=fields,
        )
    else:
        # BALANCE_CONFIRMATION, A_AND_L, EMPLOYER_LETTER → no income rows yet.
        entries = []
        log.info(
            "earnings.extractor: doc_type=%s does not yield income rows directly; "
            "customer can add manual entries against statement_id=%s",
            doc_type_resolved, statement.id,
        )

    for e in entries:
        db_session.add(e)

    statement.status = StatementStatus.EXTRACTED.value
    statement.failure_reason = None

    return {
        "ok": True,
        "entries": entries,
        "confidence": statement.extraction_confidence,
        "method": statement.extraction_method,
        "doc_type_resolved": doc_type_resolved,
        "failure_reason": None,
        "at_attempt_cap": statement.at_attempt_cap(),
        "raw": raw,
    }


__all__ = ["extract_statement"]
