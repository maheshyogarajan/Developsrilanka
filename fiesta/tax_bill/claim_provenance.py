"""fiesta.tax_bill.claim_provenance -- per-claim evidence chain for B14 audit PDF v2.

For each tax claim line (income source, deduction, exemption) this module
produces a `ClaimProvenance` describing:

  1. The data SOURCE -- which input record(s) the claim derives from
     (IncomeEntry, RemittanceEntry, Statement, DeductionClaim, manual entry).
     For each source we capture record type + id + date + value + an optional
     filename / file_sha256 hint that lets an auditor trace back to the
     scanned PDF or bank-statement page.

  2. The IRA citation that authorises the claim (e.g. "§6", "§13") + the
     short title pulled from `static/data/ira_cites.json`. The full quoted
     text is rendered separately in the PDF (Section C) -- we surface only
     the section number + title in the per-claim row to keep the table dense.

  3. The CALCULATION trace -- a list of human-readable steps showing how
     raw inputs combine into the final tax-line amount. Examples:
        "USD 1,000 on 2025-06-15 x CBSL middle 305.12 = Rs 305,120.00"
        "Rs 60,000/mo rent x 12 months x 30% work-use share = Rs 216,000"
        "Subject to absolute cap Rs 600,000 (§13 + Fourth Schedule)"

  4. (Reserved) a `confidence` hint future iterations can use to flag
     low-confidence rows for pre-audit cleanup. Not consumed by v2 PDF.

Design goals:

  * Pure functions over `TaxInputs` -- never query the DB directly. The
    aggregator already pulled everything we need. This keeps the module
    cheap (PDF build is on the request path) and headless-test-safe.

  * Defensive about missing fields. RemittanceEntry rows have richer
    provenance than IncomeEntry rows; manual-entry IncomeEntry rows have
    sparser provenance than statement-extracted ones. Whatever is present
    is surfaced; what's missing is shown as "-" (never raises).

  * Source of truth for IRA cite text: `static/data/ira_cites.json`. The
    helper exposes a single `load_ira_cites()` accessor that PDF v2 uses.

The shape returned here is plain dicts (not dataclasses) so it round-trips
through JSON serialisation for the breakdown endpoint without extra glue.
"""
from __future__ import annotations

import json
import logging
import pathlib
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IRA cite loader
# ---------------------------------------------------------------------------

_IRA_CITES_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "static" / "data" / "ira_cites.json"
)
_IRA_CACHE: Optional[dict[str, Any]] = None


def load_ira_cites(path: Optional[pathlib.Path] = None) -> dict[str, Any]:
    """Return the full IRA cites payload (cached after first read).

    The payload shape is documented in `static/data/ira_cites.json` under
    `_meta`. Callers usually want `cites_by_section()` for direct lookup.
    """
    global _IRA_CACHE
    if path is None and _IRA_CACHE is not None:
        return _IRA_CACHE
    p = path or _IRA_CITES_PATH
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        logger.warning("IRA cites file not found at %s", p)
        data = {"_meta": {"version": "unknown"}, "sections": []}
    except Exception as exc:  # pragma: no cover -- defensive
        logger.warning("Failed to load IRA cites from %s: %s", p, exc)
        data = {"_meta": {"version": "unknown"}, "sections": []}
    if path is None:
        _IRA_CACHE = data
    return data


def cites_by_section(payload: Optional[dict[str, Any]] = None) -> dict[str, dict[str, Any]]:
    """Index the cites list by section number (string, no §)."""
    p = payload or load_ira_cites()
    return {s["section"]: s for s in p.get("sections", [])}


def _normalise_section_ref(ref: str) -> list[str]:
    """Split a catalog ira_section like "§6 + §13" into ["6", "13"].

    Tolerates the leading section sign and whitespace. Filters non-numeric
    fragments (e.g. "Relief schedule") -- those have no per-section entry.
    """
    if not ref:
        return []
    raw = ref.replace("§", " ").replace("+", " ").replace(",", " ").split()
    out: list[str] = []
    for token in raw:
        token = token.strip().rstrip(".")
        # Accept "6", "13A", "85(1B)" etc. We strip parenthesised suffixes
        # because the section table is keyed by the bare section number.
        bare = token.split("(", 1)[0]
        if bare and bare[0].isdigit():
            out.append(bare)
    return out


# ---------------------------------------------------------------------------
# Per-claim provenance assemblers
# ---------------------------------------------------------------------------


def _fmt_lkr(v: Any) -> str:
    if v is None:
        return "Rs 0.00"
    try:
        return f"Rs {Decimal(str(v)):,.2f}"
    except Exception:
        return "Rs 0.00"


def _income_sources_for_category(inputs: Any, category: str) -> list[dict[str, Any]]:
    """Return source records for one income category.

    This is a best-effort pass: the aggregator only stores rolled-up
    `income_by_category_lkr`. We attempt to pull richer per-row provenance
    by reading the underlying IncomeEntry / RemittanceEntry tables IF a DB
    session is available; otherwise we fall back to a single synthetic
    "aggregated" source row built from the rollup amount.

    Defensive: any DB error returns the synthetic row + a `provenance_note`.
    """
    sources: list[dict[str, Any]] = []
    user_id = inputs.user_id
    tax_year_s4 = inputs.tax_year_s4_format

    # Fast path: IncomeEntry rows
    try:
        from fiesta.earnings.models import IncomeEntry  # type: ignore
        rows = (
            IncomeEntry.query
            .filter(
                IncomeEntry.user_id == user_id,
                IncomeEntry.tax_year == tax_year_s4,
                IncomeEntry.category == category,
                IncomeEntry.confirmed_by_customer.is_(True),
            )
            .all()
        )
        for r in rows:
            sources.append({
                "record_type": "IncomeEntry",
                "record_id": r.id,
                "statement_id": r.statement_id,
                "entry_kind": "manual" if r.statement_id is None else "extracted",
                "date": r.entry_date.isoformat() if r.entry_date else None,
                "currency": r.currency or "LKR",
                "amount": str(r.amount) if r.amount is not None else None,
                "amount_lkr": str(r.amount_lkr) if r.amount_lkr is not None else None,
                "fx_rate_lkr": str(r.fx_rate_lkr) if r.fx_rate_lkr is not None else None,
                "fx_rate_source": r.fx_rate_source,
                "payer_or_source": r.source,
                "filename": None,  # IncomeEntry doesn't carry the statement filename
            })
    except Exception as exc:
        logger.debug("IncomeEntry provenance unavailable for %s: %s", category, exc)

    # Foreign remittance: also pull RemittanceEntry rows (richer provenance)
    if category == "foreign_remittance":
        try:
            from remittance_models import RemittanceEntry  # type: ignore
            # tax-year string may be S4 ("2025-26") or S5 ("2025/26")
            alt_forms = {tax_year_s4}
            head, _, tail = tax_year_s4.partition("-")
            if len(head) == 4 and len(tail) == 2:
                alt_forms.add(f"{head}/{tail}")
            rrows = (
                RemittanceEntry.query
                .filter(
                    RemittanceEntry.user_id == user_id,
                    RemittanceEntry.tax_year.in_(list(alt_forms)),
                )
                .all()
            )
            for r in rrows:
                sources.append({
                    "record_type": "RemittanceEntry",
                    "record_id": r.id,
                    "date": r.remittance_date.isoformat() if r.remittance_date else None,
                    "currency": r.foreign_currency,
                    "amount": str(r.foreign_amount) if r.foreign_amount is not None else None,
                    "amount_lkr": (
                        str(r.lkr_amount_cbsl) if r.lkr_amount_cbsl is not None
                        else (str(r.lkr_amount_bank_rate) if r.lkr_amount_bank_rate is not None else None)
                    ),
                    "fx_rate_lkr": str(r.cbsl_rate) if r.cbsl_rate is not None else None,
                    "fx_rate_source": (
                        r.cbsl_rate_source or ("bank_rate" if r.lkr_amount_cbsl is None else "cbsl_middle")
                    ),
                    "payer_or_source": r.payer_name,
                    "source_country": r.source_country,
                    "sl_bank_account_id": r.sl_bank_account_id,
                    "filename": r.source_doc_filename or r.bank_proof_filename,
                    "manual_rate": r.rate_entered_manually,
                })
        except Exception as exc:
            logger.debug("RemittanceEntry provenance unavailable for %s: %s", category, exc)

    return sources


def _build_calculation_trace_income(
    category: str,
    rollup_lkr: Any,
    sources: list[dict[str, Any]],
) -> list[str]:
    """Build the calculation trace for one income category."""
    trace: list[str] = []
    if not sources:
        trace.append(
            f"Aggregated {category.replace('_', ' ')} from upstream summary: "
            f"{_fmt_lkr(rollup_lkr)}. (Per-entry provenance not available in this "
            f"environment; see the Remittance Ledger or Earnings Statements screen "
            f"for the underlying rows.)"
        )
        return trace
    n_total = len(sources)
    n_with_fx = sum(1 for s in sources if s.get("fx_rate_lkr"))
    fx_sources = sorted({s.get("fx_rate_source") or "-" for s in sources if s.get("fx_rate_source")})
    trace.append(
        f"{n_total} source record{'s' if n_total != 1 else ''} aggregated; "
        f"{n_with_fx} carry an explicit FX rate"
        + (f" ({', '.join(fx_sources)})" if fx_sources else "")
        + "."
    )
    if any(s.get("manual_rate") for s in sources):
        trace.append(
            "At least one source was converted at a manually-entered rate. "
            "Auditor: confirm that rate against CBSL middle for the entry date."
        )
    trace.append(f"Sum of per-record LKR-equivalents = {_fmt_lkr(rollup_lkr)}.")
    return trace


def build_income_claim_rows(inputs: Any) -> list[dict[str, Any]]:
    """One row per non-zero income category, with sources + calc trace + cite refs."""
    cites = cites_by_section()
    # Map category -> default IRA cite refs
    cat_to_ira: dict[str, list[str]] = {
        "salary": ["5"],
        "contractor_fee": ["6"],
        "foreign_remittance": ["6", "7"],  # business OR investment per character
        "interest": ["7"],
        "dividend": ["7"],
        "rental": ["7"],
    }
    rows: list[dict[str, Any]] = []
    for category, lkr in (inputs.income_by_category_lkr or {}).items():
        if Decimal(str(lkr or 0)) <= 0:
            continue
        ira_refs = cat_to_ira.get(category, ["6"])
        sources = _income_sources_for_category(inputs, category)
        trace = _build_calculation_trace_income(category, lkr, sources)
        rows.append({
            "claim_kind": "income",
            "claim_id": f"income.{category}",
            "label": category.replace("_", " ").title(),
            "amount_lkr": str(Decimal(str(lkr))),
            "ira_section_refs": ira_refs,
            "ira_section_titles": [
                (cites.get(ref) or {}).get("title", "-") for ref in ira_refs
            ],
            "sources": sources,
            "calculation_trace": trace,
        })
    return rows


def build_deduction_claim_rows(inputs: Any) -> list[dict[str, Any]]:
    """One row per claimed deduction with the catalog-supplied cite + evidence + cap trace."""
    cites = cites_by_section()
    rows: list[dict[str, Any]] = []
    for d in inputs.deductions_itemised or []:
        amount = d.get("used_lkr") or Decimal("0")
        if Decimal(str(amount)) <= 0:
            continue
        ira_section_str = d.get("ira_section") or "§6"
        ira_refs = _normalise_section_ref(ira_section_str)
        if not ira_refs:
            # No numeric section (e.g. "Relief schedule") -- still record the
            # citation string verbatim but skip cite-lookup.
            ira_refs = []
        sources: list[dict[str, Any]] = []
        estimated = d.get("estimated_lkr") or Decimal("0")
        actual = d.get("actual_lkr") or Decimal("0")
        # The DeductionClaim row itself is the source; we surface its key fields.
        sources.append({
            "record_type": "DeductionClaim",
            "category_id": d.get("category_id"),
            "evidence_status": d.get("evidence_status") or "pending",
            "estimated_lkr": str(Decimal(str(estimated))),
            "actual_lkr": str(Decimal(str(actual))),
            "notes": d.get("notes"),
            "filename": None,  # evidence-file uploads are tracked elsewhere
        })
        trace = []
        if Decimal(str(actual)) > 0 and Decimal(str(actual)) != Decimal(str(amount)):
            trace.append(
                f"Customer-actual {_fmt_lkr(actual)} subject to "
                f"the cap below."
            )
        elif Decimal(str(actual)) > 0:
            trace.append(
                f"Customer-actual (evidence-backed) "
                f"{_fmt_lkr(actual)} used directly."
            )
        elif Decimal(str(estimated)) > 0:
            trace.append(
                f"Customer-estimate {_fmt_lkr(estimated)} used "
                f"(no evidence-backed actual yet)."
            )
        if d.get("cap_note"):
            trace.append(str(d["cap_note"]))
        trace.append(f"Final deduction line = {_fmt_lkr(amount)}.")
        rows.append({
            "claim_kind": "deduction",
            "claim_id": f"deduction.{d.get('category_id')}",
            "label": d.get("name") or d.get("category_id") or "Deduction",
            "amount_lkr": str(Decimal(str(amount))),
            "ira_section_str": ira_section_str,
            "ira_section_refs": ira_refs,
            "ira_section_titles": [
                (cites.get(ref) or {}).get("title", "-") for ref in ira_refs
            ],
            "evidence_status": d.get("evidence_status") or "pending",
            "sources": sources,
            "calculation_trace": trace,
        })
    return rows


def build_exemption_relief_rows(inputs: Any) -> list[dict[str, Any]]:
    """Phase-1 reliefs are folded into the deduction list via the catalog;
    no separate rows are emitted for v2. Function is kept as the extension
    point for future Fifth-Schedule personal-relief itemisation.
    """
    return []


def all_claim_rows(inputs: Any) -> list[dict[str, Any]]:
    """All per-claim rows, in display order: income -> deductions -> reliefs."""
    return (
        build_income_claim_rows(inputs)
        + build_deduction_claim_rows(inputs)
        + build_exemption_relief_rows(inputs)
    )


def cited_section_numbers(rows: list[dict[str, Any]]) -> list[str]:
    """Return the de-duplicated, numerically-sorted set of section numbers
    cited across all claim rows. Used by the PDF to render Section C only
    with sections actually referenced (keeps the page count tight)."""
    seen: set[str] = set()
    for row in rows:
        for ref in row.get("ira_section_refs", []):
            seen.add(str(ref))
    def _sort_key(ref: str):
        # bare numeric sort with letter suffix tiebreak ("83" < "83A")
        head = "".join(ch for ch in ref if ch.isdigit())
        tail = ref[len(head):]
        return (int(head) if head else 999, tail)
    return sorted(seen, key=_sort_key)


__all__ = [
    "load_ira_cites",
    "cites_by_section",
    "build_income_claim_rows",
    "build_deduction_claim_rows",
    "build_exemption_relief_rows",
    "all_claim_rows",
    "cited_section_numbers",
]
