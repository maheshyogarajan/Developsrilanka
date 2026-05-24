"""fiesta.tax_bill.audit_pack_v2 -- B14 Audit-Defence PDF v2.

What changed from v1 (`audit_pack.py`)
--------------------------------------

v1 produced a flat 6-section pack: Cover, Income summary, Deductions table
(with one-cell IRA citation), Service Providers, Rentals, Computation,
Attestation, Schedule. The customer hands it to an IRD auditor and the
auditor has to leaf through the customer's evidence folder to reconcile.

v2 adds three new appendices that make the pack self-contained for an
audit walk-through:

  Section B -- Per-claim evidence chain
    One row per non-zero tax claim (income source, deduction). Each row
    shows the underlying source record(s) (IncomeEntry / RemittanceEntry /
    DeductionClaim / Statement), the IRA section number that authorises
    the claim, and a short calculation trace.

  Section C -- IRA sections cited (verbatim text)
    Only the sections actually cited from Section B are rendered. Text is
    pulled from `static/data/ira_cites.json` (loaded via
    `claim_provenance.load_ira_cites`). Sections are alphabetised by
    number. TODO entries (sections we have not yet pulled from the KG)
    appear last with a clear "Text pending" marker.

  Section D -- Calculation methodology (formula trace summary)
    The end-to-end trace from rolled-up income through deductions to net
    tax payable. This is the page an auditor reads first.

Preserved from v1:
  Section A -- Filing summary (the cover sheet)
  Section E -- Customer signature block
  Page numbers, ToC, FIESTA branding

Page cap
--------
v2 truncates Section B at `MAX_EVIDENCE_ROWS_PER_SECTION`. The truncation
notice tells the auditor that full evidence is available on request and
references the Section A document-reference ID.

Feature flag
------------
v2 is OPT-IN via env var `AUDIT_PDF_V2_ENABLED=true`. The route in
`tax_bill/routes.py` falls back to v1 when the flag is off OR when the
URL does not carry `?v=2`. See `docs/AUDIT_DEFENCE_V2.md` for operator
instructions.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from typing import Any

logger = logging.getLogger(__name__)


# Maximum number of evidence rows rendered in Section B before truncation.
# Keeps a typical filing under the 30-page cap stated in the B14 spec.
MAX_EVIDENCE_ROWS_PER_SECTION: int = 80


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


def _fmt_lkr(v: Any) -> str:
    if v is None:
        return "Rs 0.00"
    try:
        return f"Rs {Decimal(str(v)):,.2f}"
    except Exception:
        return "Rs 0.00"


def _mint_reference_v2(report) -> str:
    raw = (
        f"AP2|{report.user_id}|{report.tax_year_s5_format}|"
        f"{report.net_tax_payable_lkr}|{report.audit_defensibility_score}"
    )
    h = hashlib.sha256(raw.encode()).hexdigest()[:12].upper()
    return f"FIESTA-AP2-{h}"


def _ira_label(refs: list[str]) -> str:
    if not refs:
        return "-"
    return ", ".join(f"§{r}" for r in refs)


# ---------------------------------------------------------------------------
# Page footer + numbering
# ---------------------------------------------------------------------------


def _draw_footer(canvas, doc):
    """Render footer with page number, branding line, document reference."""
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    page_num = canvas.getPageNumber()
    footer_text = (
        f"FIESTA Tax Audit Pack v2 "
        f"· Page {page_num} "
        f"· {PDF_BRANDING['product_name']} "
        f"· {PDF_BRANDING['company_domain']}"
    )
    canvas.drawCentredString(doc.pagesize[0] / 2, 1.0 * doc.bottomMargin / 2, footer_text)
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Build entry point
# ---------------------------------------------------------------------------


def build_audit_pack_v2(report) -> bytes:
    """Build the v2 audit-pack PDF and return it as bytes.

    Args:
        report: TaxBillReport. Must have `inputs` populated.

    Returns:
        PDF as bytes. Caller streams via send_file().

    Raises:
        ImportError if ReportLab is not installed.
    """
    try:
        from reportlab.lib.colors import HexColor, black
        from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm, mm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
            KeepTogether,
        )
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "ReportLab is required for the audit-pack PDF. Install reportlab>=4.0."
        ) from exc

    # Lazy import: keeps v1 build path unchanged even if v2 fails to import.
    from .claim_provenance import (
        all_claim_rows, cited_section_numbers, cites_by_section,
    )

    inputs = report.inputs
    primary = HexColor(PDF_BRANDING["primary_hex"])
    accent = HexColor(PDF_BRANDING["accent_hex"])
    draft_red = HexColor(PDF_BRANDING["draft_banner_hex"])

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        title="FIESTA Tax Audit Pack v2",
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
        spaceAfter=8, spaceBefore=14, alignment=TA_LEFT,
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
    body_justify = ParagraphStyle(
        name="FBodyJ", parent=body, alignment=TA_JUSTIFY,
    )
    small = ParagraphStyle(
        name="FSmall", parent=styles["BodyText"],
        fontName="Helvetica", fontSize=8, textColor=black,
        leading=10, alignment=TA_LEFT,
    )
    small_grey = ParagraphStyle(
        name="FSmallGrey", parent=small, textColor=HexColor("#666666"),
    )
    mono = ParagraphStyle(
        name="FMono", parent=small,
        fontName="Helvetica", fontSize=8, textColor=black,
    )
    cite_text = ParagraphStyle(
        name="FCiteText", parent=body_justify,
        fontSize=9, leading=12,
        leftIndent=12, rightIndent=4,
    )

    story: list[Any] = []

    # ----------------------------------------------------------- COVER (A)
    ref_id = _mint_reference_v2(report)
    story.append(Paragraph("FIESTA Tax Audit Pack &mdash; v2", h1))
    story.append(Paragraph(
        f"Tax year {report.tax_year_s5_format} &middot; "
        f"{PDF_BRANDING['product_long_name']}",
        body,
    ))
    story.append(Spacer(1, 6 * mm))

    cover_rows = [
        ["Customer name", inputs.full_name or "-"],
        ["NIC", inputs.nic or "-"],
        ["TIN", inputs.tin or "-"],
        ["Tax year", report.tax_year_s5_format],
        ["Final tax payable", _fmt_lkr(report.net_tax_payable_lkr)],
        ["Tax without deductions", _fmt_lkr(report.tax_without_deductions_lkr)],
        ["FIESTA-assisted savings", _fmt_lkr(report.savings_vs_no_deductions_lkr)],
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

    # Table of contents (static for v2)
    story.append(Paragraph("Contents", h3))
    toc_rows = [
        ["Section A", "Filing summary (this page)"],
        ["Section B", "Per-claim evidence chain"],
        ["Section C", "IRA sections cited (verbatim text)"],
        ["Section D", "Calculation methodology (formula trace)"],
        ["Section E", "Customer attestation + signature"],
    ]
    t = Table(toc_rows, colWidths=[3 * cm, 13 * cm])
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
    ]))
    story.append(t)
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        "This audit pack accompanies the customer's tax filing. It is "
        "generated automatically from the customer's verified inputs and is "
        "intended to support an IRD examination or review. Every per-claim "
        "row in Section B references its source record(s) by id and date; "
        "Section C reproduces the IRA section text that authorises each "
        "claim; Section D shows the calculation trace from raw inputs to "
        "the net tax payable.",
        small,
    ))
    story.append(PageBreak())

    # ----------------------------------------------------- SECTION B -- EVIDENCE
    rows = all_claim_rows(inputs)
    story.append(Paragraph("Section B &mdash; Per-claim evidence chain", h2))
    if not rows:
        story.append(Paragraph(
            "No tax claims recorded for this year. Add income (S4) and "
            "deductions (S5) to populate this section.", body,
        ))
    else:
        truncated = False
        rendered = rows
        if len(rows) > MAX_EVIDENCE_ROWS_PER_SECTION:
            rendered = rows[:MAX_EVIDENCE_ROWS_PER_SECTION]
            truncated = True

        for row in rendered:
            # Per-claim header line
            ira_label = _ira_label(row.get("ira_section_refs", []))
            header_text = (
                f"<b>{row['label']}</b> &middot; "
                f"<b>{_fmt_lkr(row['amount_lkr'])}</b> &middot; "
                f"<font color='#666666'>{ira_label}</font>"
            )
            story.append(Paragraph(header_text, body))

            # Sources table
            srcs = row.get("sources", [])
            if srcs:
                src_rows = [["#", "Type", "Id", "Date", "Currency", "Amount", "LKR-equiv", "FX rate", "FX src", "Payer / Source", "File"]]
                for i, s in enumerate(srcs, 1):
                    src_rows.append([
                        str(i),
                        Paragraph(str(s.get("record_type", "-")), mono),
                        str(s.get("record_id") or s.get("category_id") or "-"),
                        str(s.get("date") or "-"),
                        str(s.get("currency") or "-"),
                        str(s.get("amount") or s.get("estimated_lkr") or "-"),
                        _fmt_lkr(s.get("amount_lkr") or s.get("actual_lkr") or 0),
                        str(s.get("fx_rate_lkr") or "-"),
                        str(s.get("fx_rate_source") or "-"),
                        Paragraph(str(s.get("payer_or_source") or "-"), mono),
                        Paragraph(str(s.get("filename") or "-"), mono),
                    ])
                t = Table(
                    src_rows,
                    colWidths=[
                        0.6 * cm, 2.2 * cm, 1.2 * cm, 1.7 * cm,
                        1.2 * cm, 1.6 * cm, 1.8 * cm, 1.2 * cm,
                        1.4 * cm, 2.2 * cm, 2.5 * cm,
                    ],
                    repeatRows=1,
                )
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), accent),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("GRID", (0, 0), (-1, -1), 0.2, black),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                ]))
                story.append(t)

            # Calculation trace
            trace = row.get("calculation_trace") or []
            if trace:
                story.append(Spacer(1, 1 * mm))
                story.append(Paragraph("<b>Calculation:</b>", small))
                for step in trace:
                    story.append(Paragraph(f"&bull; {step}", small))
            story.append(Spacer(1, 4 * mm))

        if truncated:
            story.append(Paragraph(
                f"<font color='{PDF_BRANDING['draft_banner_hex']}'><b>Truncated:</b></font> "
                f"{len(rows) - MAX_EVIDENCE_ROWS_PER_SECTION} additional claim row(s) "
                f"have been omitted to keep this pack under the standard page cap. "
                f"Full evidence is available on request &mdash; cite document "
                f"reference <b>{ref_id}</b>.",
                small,
            ))

    story.append(PageBreak())

    # ---------------------------------------- SECTION C -- IRA SECTIONS CITED
    story.append(Paragraph("Section C &mdash; IRA sections cited (verbatim text)", h2))
    cites = cites_by_section()
    cited = cited_section_numbers(rows)
    if not cited:
        story.append(Paragraph(
            "No IRA sections cited from Section B (no claims rendered). "
            "See `static/data/ira_cites.json` for the full catalog of "
            "sections FIESTA tracks.", body,
        ))
    else:
        story.append(Paragraph(
            f"The following {len(cited)} IRA section(s) authorise the "
            f"claims in Section B. Each entry quotes the section text "
            f"verbatim from the Inland Revenue Act No. 24 of 2017 "
            f"(consolidated to 31 March 2025). Sections appear in section "
            f"number order. Where the section text is marked &lsquo;Text "
            f"pending&rsquo;, the auditor should consult the published Act "
            f"directly &mdash; the citation itself is verified.",
            body,
        ))
        story.append(Spacer(1, 3 * mm))
        for ref in cited:
            cite = cites.get(ref) or {}
            if not cite:
                story.append(Paragraph(
                    f"<b>&sect;{ref}</b> &mdash; section not present in cite "
                    f"catalog (TODO: pull from IRA KG before GA).", small,
                ))
                story.append(Spacer(1, 2 * mm))
                continue
            heading = (
                f"<b>&sect;{ref} &mdash; {cite.get('title', '')}</b>"
                + (f" &middot; pages {cite['pages']}" if cite.get("pages") else "")
            )
            story.append(Paragraph(heading, h3))
            text = cite.get("text") or "(no text available)"
            if cite.get("todo"):
                story.append(Paragraph(
                    f"<font color='{PDF_BRANDING['draft_banner_hex']}'>"
                    f"<b>Text pending</b></font> &mdash; the full verbatim text "
                    f"for this section is not yet captured in the cite catalog. "
                    f"Auditor: consult the published IRA directly.",
                    small,
                ))
                story.append(Spacer(1, 1 * mm))
                story.append(Paragraph(text, cite_text))
            else:
                story.append(Paragraph(text, cite_text))
            relevance = cite.get("relevance_to_foreign_income_earner")
            if relevance:
                story.append(Spacer(1, 1 * mm))
                story.append(Paragraph(
                    f"<i>Relevance to this filing:</i> {relevance}", small_grey,
                ))
            story.append(Spacer(1, 3 * mm))

    story.append(PageBreak())

    # ---------------------------------------- SECTION D -- METHODOLOGY
    story.append(Paragraph("Section D &mdash; Calculation methodology", h2))
    story.append(Paragraph(
        "The end-to-end trace below shows how the customer's verified inputs "
        "combine into the final net tax payable. Every line is reproducible "
        "from the data in Section B and the IRA authority in Section C.",
        body,
    ))
    story.append(Spacer(1, 3 * mm))

    # Step 1: Income roll-up
    story.append(Paragraph("Step 1 &mdash; Income roll-up", h3))
    if inputs.income_by_category_lkr:
        income_rows = [["Category", "LKR amount"]]
        for k, v in inputs.income_by_category_lkr.items():
            if Decimal(str(v or 0)) > 0:
                income_rows.append([k.replace("_", " ").title(), _fmt_lkr(v)])
        income_rows.append(["Gross income (total)", _fmt_lkr(inputs.income_total_lkr)])
        t = Table(income_rows, colWidths=[10 * cm, 6 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), primary),
            ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#FFFFFF")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.25, black),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
        if inputs.income_fx_warnings:
            story.append(Spacer(1, 2 * mm))
            for w in inputs.income_fx_warnings:
                story.append(Paragraph(
                    f"<font color='{PDF_BRANDING['draft_banner_hex']}'>"
                    f"FX warning: {w}</font>", small,
                ))
    else:
        story.append(Paragraph("No income roll-up &mdash; the customer logged no income.", body))

    # Step 2: Deductions
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Step 2 &mdash; Deductions", h3))
    if inputs.deductions_itemised:
        ded_rows = [["Category", "IRA", "Evidence", "LKR amount"]]
        for d in inputs.deductions_itemised:
            ded_rows.append([
                Paragraph(d.get("name") or "-", small),
                d.get("ira_section") or "-",
                d.get("evidence_status") or "-",
                _fmt_lkr(d.get("used_lkr")),
            ])
        ded_rows.append([
            "Total deductions", "", "", _fmt_lkr(inputs.deductions_total_lkr),
        ])
        t = Table(ded_rows, colWidths=[7 * cm, 2 * cm, 3 * cm, 4 * cm])
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
    else:
        story.append(Paragraph("No deductions claimed.", body))

    # Step 3: Bracket walk
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        "Step 3 &mdash; Tax computation (bracket-by-bracket)", h3,
    ))
    comp = report.computation_with_deductions
    if comp is None:
        story.append(Paragraph(
            f"<font color='{PDF_BRANDING['draft_banner_hex']}'>"
            f"Tax engine did not produce a computation: "
            f"{report.engine_error or 'unknown'}</font>",
            body,
        ))
    else:
        b_rows = [["Band (LKR)", "Income in band", "Rate", "Tax in band"]]
        for b in comp.by_band:
            lo = b.band_lower
            hi = b.band_upper if b.band_upper is not None else None
            band_label = (
                f"{int(lo):,} - {int(hi):,}" if hi is not None
                else f"above {int(lo):,}"
            )
            b_rows.append([
                band_label,
                _fmt_lkr(b.income_in_band),
                f"{Decimal(str(b.rate)) * 100:.0f}%",
                _fmt_lkr(b.tax_in_band),
            ])
        b_rows.append(["Gross tax payable", "", "", _fmt_lkr(comp.gross_tax_lkr)])
        b_rows.append(["Net tax payable", "", "", _fmt_lkr(comp.net_tax_due_lkr)])
        t = Table(b_rows, colWidths=[5 * cm, 4 * cm, 2 * cm, 5 * cm])
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
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(
            f"Effective rate: {Decimal(str(comp.effective_rate)) * 100:.2f}% "
            f"&middot; Marginal rate: "
            f"{Decimal(str(comp.marginal_rate)) * 100:.0f}%",
            small,
        ))

    # Step 4: Methodology footer
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        "<b>Formula:</b> Net tax payable = (Gross income &minus; Total deductions) "
        "passed through the First-Schedule bracket walk, less foreign tax credit "
        "(&sect;80) and other tax credits (&sect;2(3)(c)).",
        small,
    ))

    # ----------------------------------------- SECTION E -- ATTESTATION
    story.append(PageBreak())
    story.append(Paragraph("Section E &mdash; Customer attestation", h2))
    story.append(Paragraph(
        "I attest that the information in this audit pack is true and complete "
        "to the best of my knowledge. The income shown is the income I earned "
        "in the stated tax year. The deductions claimed are wholly, exclusively "
        "and necessarily incurred in the production of my income. I have "
        "retained the supporting evidence for each line item and will produce "
        "it on request by the Inland Revenue Department. I understand that "
        "&sect;120(6)(a) of the Inland Revenue Act No. 24 of 2017 requires me "
        "to retain transaction records for a minimum of five years from the "
        "date of the transaction.",
        body,
    ))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("Customer signature: ____________________________", body))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Date: ____________________________", body))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        f"Document reference: <b>{ref_id}</b>", small,
    ))

    # Build with footer.
    doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    buffer.seek(0)
    return buffer.read()


__all__ = ["build_audit_pack_v2", "MAX_EVIDENCE_ROWS_PER_SECTION"]
