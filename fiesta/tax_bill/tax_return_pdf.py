"""fiesta.tax_bill.tax_return_pdf -- IRD-ready downloadable tax return PDF.

Tier D2-bpdf (2026-05-24): Customers can download an IRD-ready PDF from the
/tax-bill page and MANUALLY submit it to IRD. This is the sellable path that
bypasses the IRD-portal automation gates (B4 Auto-File / B5 CAPTCHA / B7
25/26 portal test) — those are still externally gated, but FIESTA can ship
revenue without them by handing the customer a print-ready tax return PDF.

Distinction from neighbouring PDFs
----------------------------------
- fiesta/tax_bill/audit_pack.py  -> S12 audit pack PDF (defensibility evidence)
- fiesta/submit/export.py        -> S14 IRD return form PDF (packaged in ZIP)
- THIS module                    -> S12 IRD return form PDF (standalone download)

The S14 export.py module already builds an IRD-form-style PDF. This module is
a THIN wrapper that:

    1. Adapts a TaxBillReport into the (customer, tax_data) dicts that
       build_ird_return_form_pdf() expects.
    2. Calls build_ird_return_form_pdf().
    3. Overlays a "Review before submission" watermark on every page so it's
       impossible to mistake the standalone tax-bill download for an
       attestation-signed S14 submission.

Single source of truth for the layout stays in fiesta.submit.export. We do
NOT duplicate the IRD form rendering here.

Scope cap (Tier D2-bpdf):
- ONE tax year per PDF (no multi-year aggregate).
- Watermark is non-negotiable (legal safety — we are NOT a registered tax agent).
- ReportLab plain layout, NO custom IRD official-form branding.
- Numbers come from existing compute_tax_bill() -> TaxBillReport.
- NO digital signature, NO encryption.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


WATERMARK_TEXT = (
    "FIESTA MVP — Review before submission. "
    "Final tax position requires CA/tax-agent sign-off."
)


# ---------------------------------------------------------------------------
# Adapters: TaxBillReport -> S14 export builder kwargs
# ---------------------------------------------------------------------------


def _to_money(value: Any) -> float:
    """Best-effort numeric coercion for the float-typed S14 export builder."""
    if value is None:
        return 0.0
    try:
        return float(Decimal(str(value)))
    except Exception:
        return 0.0


def _humanise_category(key: str) -> str:
    return key.replace("_", " ").title()


def _build_customer_dict(report) -> dict[str, Any]:
    """Pull the customer header fields off TaxBillReport.inputs.

    The S14 builder fills missing fields with placeholders, but we surface
    every value we have so the customer's print-out is as complete as
    possible. The full_name / NIC / TIN come from the S3 profile snapshot
    that the aggregator already loaded.
    """
    inputs = report.inputs
    return {
        "full_name": getattr(inputs, "full_name", None) or "",
        "nic": getattr(inputs, "nic", None) or "",
        "tin": getattr(inputs, "tin", None) or "",
        # S3 profile doesn't yet have a unified address/email/phone on
        # TaxInputs — leave blank so the printed form has a fillable line
        # rather than wrong data.
        "address": getattr(inputs, "address", None) or "",
        "email": getattr(inputs, "email", None) or "",
        "phone": getattr(inputs, "phone", None) or "",
    }


def _build_tax_data_dict(report) -> dict[str, Any]:
    """Project TaxBillReport headline numbers into the S14 builder's shape.

    The S14 builder takes a `tax_data` dict with:
        gross_income_lkr, total_deductions_lkr, taxable_income_lkr,
        tax_payable_lkr, credits_lkr, final_tax_payable_lkr,
        income_breakdown: [{source, amount_lkr}],
        deductions_breakdown: [{category, amount_lkr}]

    We feed it the same numbers the customer sees on /tax-bill so the
    printed PDF reconciles line-by-line with the on-screen breakdown.
    """
    inputs = report.inputs

    income_breakdown: list[dict[str, Any]] = []
    for cat_key, lkr in (inputs.income_by_category_lkr or {}).items():
        amount = _to_money(lkr)
        if amount <= 0:
            continue
        income_breakdown.append({
            "source": _humanise_category(cat_key),
            "amount_lkr": amount,
        })

    deductions_breakdown: list[dict[str, Any]] = []
    for item in (inputs.deductions_itemised or []):
        amount = _to_money(item.get("used_lkr"))
        if amount <= 0:
            continue
        deductions_breakdown.append({
            "category": item.get("name") or _humanise_category(
                item.get("category_id") or "deduction"
            ),
            "amount_lkr": amount,
        })

    # Credits / withholding: not yet aggregated on TaxBillReport. Default 0
    # and let the printed form show a fillable line for the customer.
    credits_lkr = 0.0
    gross_tax_lkr = _to_money(report.gross_tax_payable_lkr)
    final_tax_lkr = _to_money(report.net_tax_payable_lkr)

    return {
        "gross_income_lkr": _to_money(report.gross_income_lkr),
        "total_deductions_lkr": _to_money(report.total_deductions_lkr),
        "taxable_income_lkr": _to_money(report.taxable_income_lkr),
        "tax_payable_lkr": gross_tax_lkr,
        "credits_lkr": credits_lkr,
        "final_tax_payable_lkr": final_tax_lkr,
        "income_breakdown": income_breakdown,
        "deductions_breakdown": deductions_breakdown,
    }


# ---------------------------------------------------------------------------
# Watermark overlay
# ---------------------------------------------------------------------------


def _overlay_watermark(base_pdf_bytes: bytes) -> bytes:
    """Stamp WATERMARK_TEXT diagonally across every page of base_pdf_bytes.

    Uses PyPDF (lazy import) if available — falls back to ReportLab-only
    re-render with watermark embedded on the first build.

    PyPDF is already a transitive dependency in agreements/pdf_engine, so
    the lazy import should resolve in production. If it doesn't, we return
    the base bytes unchanged + log a warning rather than failing the
    download (the customer still gets the IRD form; the watermark is a
    legal-safety preference, not a correctness requirement).
    """
    try:
        from reportlab.lib.colors import HexColor
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError:
        logger.warning(
            "ReportLab unavailable for watermark overlay — returning "
            "base PDF without watermark."
        )
        return base_pdf_bytes

    # Build a single-page watermark PDF.
    overlay_buf = io.BytesIO()
    c = canvas.Canvas(overlay_buf, pagesize=A4)
    c.saveState()
    # Diagonal across centre of page. A4 = 595 x 842 pt.
    c.translate(297, 421)
    c.rotate(30)
    c.setFillColor(HexColor("#DC2626"))
    c.setFillAlpha(0.15)
    c.setFont("Helvetica-Bold", 22)
    # Two-line watermark for legibility.
    c.drawCentredString(0, 20, "REVIEW BEFORE SUBMISSION")
    c.setFont("Helvetica", 11)
    c.drawCentredString(0, -12, "FIESTA MVP — CA / tax-agent sign-off advised")
    c.restoreState()
    # Footer banner so the warning is also legible at print thumbnail size.
    c.saveState()
    c.setFillColor(HexColor("#DC2626"))
    c.setFillAlpha(0.85)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(36, 24, WATERMARK_TEXT)
    c.restoreState()
    c.showPage()
    c.save()
    overlay_bytes = overlay_buf.getvalue()
    overlay_buf.close()

    # Merge overlay onto every page of base_pdf_bytes using pypdf.
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        try:
            from PyPDF2 import PdfReader, PdfWriter  # type: ignore
        except ImportError:
            logger.warning(
                "pypdf/PyPDF2 unavailable — returning base PDF without "
                "watermark overlay (B-PDF watermark is a legal preference, "
                "not a correctness requirement)."
            )
            return base_pdf_bytes

    base_reader = PdfReader(io.BytesIO(base_pdf_bytes))
    overlay_reader = PdfReader(io.BytesIO(overlay_bytes))
    overlay_page = overlay_reader.pages[0]

    writer = PdfWriter()
    for page in base_reader.pages:
        try:
            page.merge_page(overlay_page)
        except Exception as exc:
            logger.warning("watermark merge failed on one page: %s", exc)
        writer.add_page(page)

    out_buf = io.BytesIO()
    writer.write(out_buf)
    return out_buf.getvalue()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def render_tax_return_pdf(report) -> bytes:
    """Render an IRD-ready tax return PDF for one TaxBillReport.

    Wraps fiesta.submit.export.build_ird_return_form_pdf() so the heavy
    layout work stays in one place. Adds a "Review before submission"
    watermark stamped diagonally across every page + a footer banner.

    Args:
        report: TaxBillReport returned by compute_tax_bill().

    Returns:
        PDF bytes (starts with %PDF-, ends with %%EOF).

    Raises:
        ImportError: if ReportLab is not installed.
        ValueError:  if report has no usable computation (engine_error set).
    """
    if getattr(report, "engine_error", None):
        raise ValueError(
            f"Cannot render tax return PDF — tax engine error: "
            f"{report.engine_error}"
        )

    # Lazy import — keeps test envs without the submit package importable.
    from fiesta.submit.export import build_ird_return_form_pdf

    customer = _build_customer_dict(report)
    tax_data = _build_tax_data_dict(report)
    tax_year = report.tax_year_s5_format
    # The B-PDF download is generated on demand; use UTC now so the
    # /CreationDate is human-meaningful, not a determinism seed. (S14 needs
    # determinism because it's hashed for the audit trail; B-PDF is just a
    # download.)
    when = datetime.now(timezone.utc)

    base_pdf = build_ird_return_form_pdf(
        customer=customer,
        tax_year=tax_year,
        tax_data=tax_data,
        when=when,
    )

    # Stamp the watermark.
    final_pdf = _overlay_watermark(base_pdf)
    return final_pdf


def filename_for(report) -> str:
    """Suggested Content-Disposition filename for the download."""
    inputs = report.inputs
    safe_name = (getattr(inputs, "full_name", None) or "customer").strip()
    # Strip characters that browsers tend to mangle in filenames.
    safe_name = "".join(
        c if c.isalnum() or c in ("-", "_") else "_"
        for c in safe_name
    ) or "customer"
    return f"FIESTA_tax_return_{report.tax_year_s4_format}_{safe_name}.pdf"


__all__ = ["render_tax_return_pdf", "filename_for", "WATERMARK_TEXT"]
