"""fiesta.tax_bill.audit_pack -- IRD-defensibility-ready PDF for S12 'export'.

Sections (top-level):
    Cover         -- NIC + TIN + TY + final tax payable + reference ID
    1. Income     -- per-source itemisation, currency, FX rates
    2. Deductions -- per-category itemised; IRA cite + evidence status
    3. Agreements -- SP + rental agreement summary, §195 disclosure status
    4. Computation-- bracket-by-bracket walk (matches the on-screen breakdown)
    5. Attestation-- the lines the customer signs at S14
Schedules:
    Per Service Agreement: reference_id (the actual PDF lives on disk; this is
        the schedule entry that lists them).
    Per Rental Agreement:  reference_id (ditto).

Convention: ReportLab Platypus, A4 portrait, FIESTA primary colours from
pdf_engine PDF_BRANDING.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from typing import Any

logger = logging.getLogger(__name__)


# Branding constants (lazy-fetched from pdf_engine if it exists, else inline).
try:
    from fiesta.agreements.pdf_engine import PDF_BRANDING  # type: ignore
except Exception:
    PDF_BRANDING = {
        "product_name": "FIESTA",
        "product_long_name": "Foreign Income Earners' Savings & Tax Advisor",
        "company_domain": "developsrilanka.com",
        "primary_hex": "#0B5394",
        "accent_hex": "#9FC5E8",
        "draft_banner_hex": "#D32F2F",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_lkr(v) -> str:
    if v is None:
        return "Rs 0.00"
    try:
        d = Decimal(str(v))
    except Exception:
        return "Rs 0.00"
    return f"Rs {d:,.2f}"


def _mint_reference(report) -> str:
    """Deterministic reference for the audit-pack PDF."""
    raw = (
        f"AP|{report.user_id}|{report.tax_year_s5_format}|"
        f"{report.net_tax_payable_lkr}|{report.audit_defensibility_score}"
    )
    h = hashlib.sha256(raw.encode()).hexdigest()[:12].upper()
    return f"FIESTA-AP-{h}"


# ---------------------------------------------------------------------------
# Build entry point
# ---------------------------------------------------------------------------


def build_audit_pack(report) -> bytes:
    """Build the audit-pack PDF and return it as bytes.

    Args:
        report: TaxBillReport. inputs + computation already populated.

    Returns:
        PDF as bytes. Caller is responsible for streaming.

    Raises ImportError if ReportLab is not installed.
    """
    try:
        from reportlab.lib.colors import HexColor, black
        from reportlab.lib.enums import TA_LEFT, TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm, mm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
        )
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "ReportLab is required for the audit-pack PDF. Install reportlab>=4.0."
        ) from exc

    inputs = report.inputs
    primary = HexColor(PDF_BRANDING["primary_hex"])
    accent = HexColor(PDF_BRANDING["accent_hex"])

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        title="FIESTA Tax Audit Pack",
        author="FIESTA",
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle(
        name="FH1", parent=styles["Heading1"],
        fontName="Helvetica-Bold", fontSize=18, textColor=primary,
        spaceAfter=12, alignment=TA_LEFT,
    )
    h2 = ParagraphStyle(
        name="FH2", parent=styles["Heading2"],
        fontName="Helvetica-Bold", fontSize=13, textColor=primary,
        spaceAfter=8, spaceBefore=12, alignment=TA_LEFT,
    )
    h3 = ParagraphStyle(
        name="FH3", parent=styles["Heading3"],
        fontName="Helvetica-Bold", fontSize=11, textColor=black,
        spaceAfter=4, spaceBefore=8, alignment=TA_LEFT,
    )
    body = ParagraphStyle(
        name="FBody", parent=styles["BodyText"],
        fontName="Helvetica", fontSize=10, textColor=black,
        leading=14, alignment=TA_LEFT,
    )
    small = ParagraphStyle(
        name="FSmall", parent=styles["BodyText"],
        fontName="Helvetica", fontSize=8, textColor=black,
        leading=10, alignment=TA_LEFT,
    )

    story: list[Any] = []

    # ----------------------------------------------------------------- COVER
    ref_id = _mint_reference(report)
    story.append(Paragraph("FIESTA Tax Audit Pack", h1))
    story.append(Paragraph(
        f"Tax year {report.tax_year_s5_format} -- "
        f"{PDF_BRANDING['product_long_name']}", body
    ))
    story.append(Spacer(1, 6 * mm))

    cover_rows = [
        ["Customer name", inputs.full_name or "-"],
        ["NIC", inputs.nic or "-"],
        ["TIN", inputs.tin or "-"],
        ["Tax year", report.tax_year_s5_format],
        ["Final tax payable", _fmt_lkr(report.net_tax_payable_lkr)],
        [
            "Tax without deductions",
            _fmt_lkr(report.tax_without_deductions_lkr),
        ],
        [
            "FIESTA-assisted savings",
            _fmt_lkr(report.savings_vs_no_deductions_lkr),
        ],
        ["Audit defensibility", (
            f"{report.audit_defensibility_score}/100 "
            f"({report.audit_defensibility_label})"
        )],
        ["Document reference", ref_id],
        ["Generated", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")],
    ]
    t = Table(cover_rows, colWidths=[6 * cm, 10 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), accent),
        ("TEXTCOLOR", (0, 0), (-1, -1), black),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, black),
    ]))
    story.append(t)
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        "This audit pack accompanies your tax filing. It is generated "
        "automatically from your verified inputs and is intended to "
        "support an IRD examination or review.", small
    ))
    story.append(PageBreak())

    # --------------------------------------------------------- 1. INCOME
    story.append(Paragraph("Section 1 -- Income summary", h2))
    if not inputs.income_by_category_lkr or inputs.income_total_lkr <= 0:
        story.append(Paragraph(
            "No income entries recorded for this tax year.", body
        ))
    else:
        income_rows = [["Category", "Amount (LKR)"]]
        for k, v in inputs.income_by_category_lkr.items():
            if Decimal(str(v)) > 0:
                income_rows.append([k.replace("_", " ").title(), _fmt_lkr(v)])
        income_rows.append([
            "TOTAL", _fmt_lkr(inputs.income_total_lkr)
        ])
        t = Table(income_rows, colWidths=[10 * cm, 6 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), primary),
            ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#FFFFFF")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.25, black),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t)

        if inputs.income_by_currency:
            story.append(Spacer(1, 4 * mm))
            story.append(Paragraph("Currency breakdown", h3))
            cur_rows = [["Currency", "Amount (original)"]]
            for cur, amt in inputs.income_by_currency.items():
                cur_rows.append([cur, _fmt_lkr(amt) if cur == "LKR" else f"{amt}"])
            t = Table(cur_rows, colWidths=[5 * cm, 11 * cm])
            t.setStyle(TableStyle([
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.25, black),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]))
            story.append(t)
        if inputs.income_fx_warnings:
            story.append(Spacer(1, 3 * mm))
            for w in inputs.income_fx_warnings:
                story.append(Paragraph(
                    f"<font color='{PDF_BRANDING['draft_banner_hex']}'>"
                    f"FX warning: {w}</font>", small
                ))

    # ----------------------------------------------------- 2. DEDUCTIONS
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        "Section 2 -- Deductions itemised (with IRA citation)", h2
    ))
    if not inputs.deductions_itemised:
        story.append(Paragraph(
            "No deductions claimed for this tax year.", body
        ))
    else:
        ded_rows = [[
            "Category", "IRA", "Amount", "Evidence", "Cap note"
        ]]
        for d in inputs.deductions_itemised:
            ded_rows.append([
                Paragraph(d["name"], body),
                d.get("ira_section") or "§6",
                _fmt_lkr(d["used_lkr"]),
                d.get("evidence_status") or "pending",
                Paragraph(d.get("cap_note") or "-", small),
            ])
        ded_rows.append([
            "TOTAL", "", _fmt_lkr(inputs.deductions_total_lkr), "", ""
        ])
        t = Table(ded_rows, colWidths=[5 * cm, 1.5 * cm, 3 * cm, 2.5 * cm, 4 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), primary),
            ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#FFFFFF")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, black),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(
            f"{inputs.deductions_with_evidence_count} of "
            f"{len(inputs.deductions_itemised)} deduction lines have "
            f"evidence collected or submitted. Items marked 'pending' "
            f"must have supporting documents available before "
            f"submission. Retain all evidence for 6 years per IRA "
            f"section 120 (record-keeping).",
            small,
        ))

    # ----------------------------------------------------- 3. AGREEMENTS
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        "Section 3 -- Service providers + §195 disclosure status", h2
    ))
    if not inputs.service_providers:
        story.append(Paragraph(
            "No service providers recorded.", body
        ))
    else:
        sp_rows = [[
            "Name", "Type", "Monthly", "Disclosure?", "Agreement",
        ]]
        for sp in inputs.service_providers:
            disclosure_label = (
                "Required + Applied"
                if sp["requires_disclosure"] and sp["disclosure_applied_in_agreement"]
                else "Required + MISSING"
                if sp["requires_disclosure"]
                else "Not required"
            )
            agreement_label = sp.get("agreement_status") or "none"
            if sp.get("agreement_reference_id"):
                agreement_label = (
                    f"{sp['agreement_status']} ({sp['agreement_reference_id']})"
                )
            sp_rows.append([
                Paragraph(sp.get("name", "-"), small),
                Paragraph((sp.get("service_type") or "").replace("_", " "), small),
                _fmt_lkr(sp.get("monthly_rate_lkr") or 0),
                disclosure_label,
                Paragraph(agreement_label, small),
            ])
        t = Table(sp_rows, colWidths=[4 * cm, 3.5 * cm, 3 * cm, 3 * cm, 3 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), primary),
            ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#FFFFFF")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, black),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)

    # ----------------------------------------------------- 4. RENTALS
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        "Section 4 -- Property + rental summary + stamp duty status", h2
    ))
    if not inputs.rentals:
        story.append(Paragraph(
            "No rental arrangements recorded.", body
        ))
    else:
        rn_rows = [["Property", "Landlord", "Monthly rent",
                    "Home-office share", "§195 / Stamp"]]
        for r in inputs.rentals:
            stamp_label = ""
            if r.get("stamp_duty_chargeable"):
                stamp_label = (
                    f"Stamp duty {_fmt_lkr(r.get('stamp_duty_lkr'))}"
                )
            disclosure_label = ""
            if r.get("requires_disclosure"):
                disclosure_label = (
                    "§195 applied" if r.get("disclosure_applied_in_agreement")
                    else "§195 MISSING"
                )
            note = " · ".join(x for x in [disclosure_label, stamp_label] if x)
            rn_rows.append([
                Paragraph(r.get("property_address", "-"), small),
                Paragraph(r.get("landlord_name", "-"), small),
                _fmt_lkr(r.get("monthly_rent_lkr") or 0),
                _fmt_lkr(r.get("home_office_portion_monthly_lkr") or 0),
                Paragraph(note or "Arm's-length", small),
            ])
        t = Table(rn_rows, colWidths=[4 * cm, 3.5 * cm, 2.7 * cm, 3 * cm, 3.3 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), primary),
            ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#FFFFFF")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, black),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)

    # ------------------------------------------------- 5. COMPUTATION
    story.append(PageBreak())
    story.append(Paragraph(
        "Section 5 -- Tax computation (bracket-by-bracket)", h2
    ))
    comp = report.computation_with_deductions
    if comp is None:
        story.append(Paragraph(
            "<font color='{}'>Tax engine did not produce a computation: {}</font>"
            .format(
                PDF_BRANDING["draft_banner_hex"],
                report.engine_error or "unknown",
            ),
            body,
        ))
    else:
        bracket_rows = [["Band (LKR)", "Income in band", "Rate", "Tax in band"]]
        for b in comp.by_band:
            lo = b.band_lower
            hi = b.band_upper if b.band_upper is not None else None
            band_label = (
                f"{int(lo):,} - {int(hi):,}" if hi is not None
                else f"above {int(lo):,}"
            )
            bracket_rows.append([
                band_label,
                _fmt_lkr(b.income_in_band),
                f"{Decimal(str(b.rate)) * 100:.0f}%",
                _fmt_lkr(b.tax_in_band),
            ])
        bracket_rows.append([
            "Gross tax", "", "", _fmt_lkr(comp.gross_tax_lkr),
        ])
        bracket_rows.append([
            "Net tax due", "", "", _fmt_lkr(comp.net_tax_due_lkr),
        ])
        t = Table(bracket_rows, colWidths=[5 * cm, 4 * cm, 2 * cm, 5 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), primary),
            ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#FFFFFF")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, -2), (-1, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.25, black),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(
            f"Effective rate: {Decimal(str(comp.effective_rate)) * 100:.2f}% "
            f"&middot; Marginal rate: "
            f"{Decimal(str(comp.marginal_rate)) * 100:.0f}%",
            small,
        ))

    # ------------------------------------------------- 6. ATTESTATION
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Section 6 -- Customer attestation", h2))
    story.append(Paragraph(
        "I attest that the information in this audit pack is true and "
        "complete to the best of my knowledge. The income shown is the "
        "income I earned in the stated tax year. The deductions claimed "
        "are wholly, exclusively and necessarily incurred in the "
        "production of my income. I have retained the supporting "
        "evidence for each line item and will produce it on request by "
        "the Inland Revenue Department.", body,
    ))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("Customer signature:  ____________________________", body))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Date:                ____________________________", body))

    # ------------------------------------------- SCHEDULES (agreement refs)
    schedules: list[dict[str, Any]] = []
    for sp in inputs.service_providers:
        if sp.get("agreement_reference_id"):
            schedules.append({
                "kind": "service_agreement",
                "ref": sp["agreement_reference_id"],
                "name": sp.get("name", ""),
                "amount": sp.get("agreement_monthly_fee_lkr") or 0,
            })
    for r in inputs.rentals:
        if r.get("agreement_reference_id"):
            schedules.append({
                "kind": "rental_agreement",
                "ref": r["agreement_reference_id"],
                "name": r.get("landlord_name", ""),
                "amount": r.get("monthly_rent_lkr") or 0,
            })

    if schedules:
        story.append(PageBreak())
        story.append(Paragraph(
            "Schedule A -- Generated agreements (references)", h2
        ))
        story.append(Paragraph(
            "The following agreements were generated and stored by the "
            "FIESTA platform. The full PDFs are kept by the customer + "
            "the counterparty; their references are listed here for "
            "audit trail.", body
        ))
        sched_rows = [["Kind", "Counterparty", "Amount", "Reference ID"]]
        for s in schedules:
            sched_rows.append([
                s["kind"].replace("_", " ").title(),
                s["name"],
                _fmt_lkr(s["amount"]),
                s["ref"],
            ])
        t = Table(sched_rows, colWidths=[4 * cm, 5 * cm, 3 * cm, 4 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), primary),
            ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#FFFFFF")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, black),
        ]))
        story.append(t)

    doc.build(story)
    buffer.seek(0)
    return buffer.read()


__all__ = ["build_audit_pack"]
