"""fiesta.assets_liabilities.pdf — IRD-compliant A&L declaration PDF generator.

Feature 9 D8 (PLAN_X9_COMPLETION §5).

Mirrors the ReportLab Platypus pipeline used by fiesta/agreements/pdf_engine.py:
  - SimpleDocTemplate on A4
  - ParagraphStyle dict built from getSampleStyleSheet()
  - FIESTA cream/forest-green/terracotta design tokens adapted for a
    tabular financial document (Table + TableStyle rather than Paragraph only)
  - Deterministic reference ID (mint_reference_id from pdf_engine)
  - BytesIO output → returned as bytes for Flask send_file

PDF structure (IRD-compliant declaration layout):
  1. Header band    — FIESTA logo text + product long name
  2. Declaration header — "Statement of Assets and Liabilities"
                          Tax Year | NIC | Full Name
  3. Assets table   — Category | Description | Value (LKR)
                      Sub-total per category, Total row
  4. Liabilities table — Category | Description | Lender | Balance (LKR)
                         Sub-total, Total row
  5. Net Worth summary — Assets Total − Liabilities Total
  6. Declaration clause — IRD statutory form language + signature line + date
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import Any, Sequence

log = logging.getLogger(__name__)

try:
    from reportlab.lib import colors
    from reportlab.lib.colors import HexColor, black, white
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm, mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
    _HAS_REPORTLAB = True
except ImportError:
    _HAS_REPORTLAB = False
    log.warning("pdf.py: ReportLab not installed — PDF generation will fail at runtime")

try:
    from fiesta.agreements.pdf_engine import mint_reference_id
    _HAS_ENGINE = True
except Exception:
    _HAS_ENGINE = False

    def mint_reference_id(*, prefix, tax_year, user_id, user_name, seed_extra="") -> str:  # type: ignore[misc]
        """Fallback reference ID when pdf_engine is unavailable."""
        import hashlib
        seed = f"{prefix}|{tax_year}|{user_id}|{seed_extra}".encode()
        return f"{prefix}-{tax_year}-{hashlib.sha256(seed).hexdigest().upper()[:6]}"


# ---------------------------------------------------------------------------
# FIESTA design tokens (cream paper palette)
# ---------------------------------------------------------------------------
_FOREST_GREEN = "#2D5016"   # primary CTA colour in FIESTA hub
_TERRACOTTA   = "#C1442D"   # accent / alert
_CREAM        = "#F7F5F0"   # paper background
_INK_STRONG   = "#0F1115"
_INK_SOFT     = "#4B525C"
_BORDER       = "#C8C2B6"


# ---------------------------------------------------------------------------
# Style builder
# ---------------------------------------------------------------------------
def _build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    forest = HexColor(_FOREST_GREEN)
    terracotta = HexColor(_TERRACOTTA)

    return {
        "brand_header": ParagraphStyle(
            "brand_header",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=14,
            textColor=forest,
            alignment=TA_CENTER,
            spaceAfter=2,
        ),
        "brand_sub": ParagraphStyle(
            "brand_sub",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=HexColor(_INK_SOFT),
            alignment=TA_CENTER,
            spaceAfter=10,
        ),
        "doc_title": ParagraphStyle(
            "doc_title",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=13,
            textColor=black,
            alignment=TA_CENTER,
            spaceBefore=6,
            spaceAfter=4,
        ),
        "meta_label": ParagraphStyle(
            "meta_label",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=HexColor(_INK_SOFT),
            alignment=TA_LEFT,
        ),
        "meta_value": ParagraphStyle(
            "meta_value",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=HexColor(_INK_STRONG),
            alignment=TA_LEFT,
        ),
        "section_heading": ParagraphStyle(
            "section_heading",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=10,
            textColor=forest,
            spaceBefore=14,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            spaceAfter=6,
        ),
        "declaration_clause": ParagraphStyle(
            "declaration_clause",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=13,
            textColor=HexColor(_INK_SOFT),
            spaceAfter=10,
        ),
        "sig_label": ParagraphStyle(
            "sig_label",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=HexColor(_INK_SOFT),
            spaceAfter=2,
        ),
        "net_worth": ParagraphStyle(
            "net_worth",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11,
            textColor=forest,
            spaceBefore=10,
            spaceAfter=4,
        ),
    }


# ---------------------------------------------------------------------------
# Table style helpers
# ---------------------------------------------------------------------------
def _asset_table_style(num_data_rows: int) -> TableStyle:
    """Header row forest-green; alternating cream rows; right-aligned values."""
    cmds = [
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), HexColor(_FOREST_GREEN)),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        # Data rows
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 8.5),
        ("ALIGN", (2, 1), (2, -1), "RIGHT"),   # value column right-aligned
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUND", (0, 1), (-1, -1), [HexColor(_CREAM), white]),
        ("GRID", (0, 0), (-1, -1), 0.4, HexColor(_BORDER)),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    if num_data_rows > 0:
        # Total row — bold + slightly heavier border
        total_row = num_data_rows + 1  # header is row 0
        cmds += [
            ("FONTNAME", (0, total_row), (-1, total_row), "Helvetica-Bold"),
            ("BACKGROUND", (0, total_row), (-1, total_row), HexColor("#EAE6DE")),
            ("LINEABOVE", (0, total_row), (-1, total_row), 0.8, HexColor(_FOREST_GREEN)),
        ]
    return TableStyle(cmds)


def _liability_table_style(num_data_rows: int) -> TableStyle:
    """Same palette but terracotta header for liability table."""
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), HexColor(_TERRACOTTA)),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 8.5),
        ("ALIGN", (3, 1), (3, -1), "RIGHT"),   # balance column right-aligned
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUND", (0, 1), (-1, -1), [HexColor(_CREAM), white]),
        ("GRID", (0, 0), (-1, -1), 0.4, HexColor(_BORDER)),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    if num_data_rows > 0:
        total_row = num_data_rows + 1
        cmds += [
            ("FONTNAME", (0, total_row), (-1, total_row), "Helvetica-Bold"),
            ("BACKGROUND", (0, total_row), (-1, total_row), HexColor("#F5E8E6")),
            ("LINEABOVE", (0, total_row), (-1, total_row), 0.8, HexColor(_TERRACOTTA)),
        ]
    return TableStyle(cmds)


# ---------------------------------------------------------------------------
# LKR formatting
# ---------------------------------------------------------------------------
def _fmt_lkr(cents: int) -> str:
    """Format cents as 'LKR 1,234,567.89'."""
    lkr = (Decimal(cents or 0) / Decimal(100)).quantize(Decimal("0.01"))
    integer_part, frac_part = divmod(lkr * 100, 100)
    integer_part = int(integer_part)
    # comma-separate
    formatted = f"{integer_part:,}"
    return f"LKR {formatted}.{int(frac_part):02d}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def generate_al_pdf(
    *,
    user_id: int,
    user_name: str,
    user_nic: str,
    tax_year: str,
    assets: Sequence[Any],       # list of AssetEntry model instances
    liabilities: Sequence[Any],  # list of LiabilityEntry model instances
    generated_date: date | None = None,
) -> bytes:
    """Render an IRD-compliant A&L declaration to PDF bytes.

    Parameters
    ----------
    user_id       : int — used for deterministic reference ID minting
    user_name     : str — taxpayer full name (printed in header)
    user_nic      : str — NIC printed in header (pass empty string if unavailable)
    tax_year      : str — e.g. "2025/2026"
    assets        : list of AssetEntry instances (or dicts with same attributes)
    liabilities   : list of LiabilityEntry instances (or dicts with same attributes)
    generated_date: date to print on declaration; defaults to today

    Returns
    -------
    bytes — PDF content ready for send_file(..., mimetype='application/pdf')
    """
    if not _HAS_REPORTLAB:
        raise RuntimeError(
            "ReportLab is not installed. Cannot generate PDF. "
            "Add reportlab>=4.0 to requirements.txt."
        )

    generated_date = generated_date or date.today()
    ref_id = mint_reference_id(
        prefix="AL",
        tax_year=tax_year.replace("/", "-"),
        user_id=user_id,
        user_name=user_name,
    )

    buf = BytesIO()
    styles = _build_styles()
    page_w, page_h = A4

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Assets & Liabilities Declaration — {tax_year}",
        author="FIESTA",
        subject="IRD A&L Declaration",
        creator="FIESTA — Foreign Income Earners' Savings & Tax Advisor",
        producer="developsrilanka.com",
    )

    usable_width = page_w - 40 * mm  # left + right margin

    story: list[Any] = []

    # ------------------------------------------------------------------ #
    # 1. Header band
    # ------------------------------------------------------------------ #
    story.append(Paragraph("FIESTA", styles["brand_header"]))
    story.append(Paragraph(
        "Foreign Income Earners' Savings &amp; Tax Advisor · developsrilanka.com",
        styles["brand_sub"],
    ))
    # Thin rule
    rule_table = Table([[""]], colWidths=[usable_width], rowHeights=[1.5])
    rule_table.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, -1), 1, HexColor(_FOREST_GREEN)),
        ("LINEBELOW", (0, 0), (-1, -1), 0, white),
    ]))
    story.append(rule_table)
    story.append(Spacer(1, 6))

    # ------------------------------------------------------------------ #
    # 2. Declaration header
    # ------------------------------------------------------------------ #
    story.append(Paragraph("Statement of Assets and Liabilities", styles["doc_title"]))
    story.append(Spacer(1, 4))

    meta_data = [
        ["Tax Year:", tax_year, "Reference:", ref_id],
        ["Full Name:", user_name, "NIC:", user_nic or "—"],
        ["Declaration Date:", generated_date.strftime("%d %B %Y"), "", ""],
    ]
    meta_col_widths = [
        usable_width * 0.15,
        usable_width * 0.37,
        usable_width * 0.15,
        usable_width * 0.33,
    ]
    meta_table = Table(meta_data, colWidths=meta_col_widths)
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), HexColor(_INK_SOFT)),
        ("TEXTCOLOR", (2, 0), (2, -1), HexColor(_INK_SOFT)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 10))

    # ------------------------------------------------------------------ #
    # 3. Assets table
    # ------------------------------------------------------------------ #
    story.append(Paragraph("Part A — Assets", styles["section_heading"]))

    asset_col_widths = [
        usable_width * 0.22,  # category
        usable_width * 0.52,  # description
        usable_width * 0.26,  # value
    ]
    asset_rows = [["Category", "Description", "Value (LKR)"]]
    total_assets_cents = 0
    for a in assets:
        cat = str(getattr(a, "category", "—")).replace("_", " ").title()
        desc = str(getattr(a, "description", "—"))
        cents = int(getattr(a, "value_lkr_cents", 0) or 0)
        total_assets_cents += cents
        asset_rows.append([cat, desc, _fmt_lkr(cents)])
    asset_rows.append(["", "TOTAL ASSETS", _fmt_lkr(total_assets_cents)])

    num_data = len(asset_rows) - 2  # header + total don't count as data rows
    asset_table = Table(asset_rows, colWidths=asset_col_widths, repeatRows=1)
    asset_table.setStyle(_asset_table_style(num_data))
    story.append(asset_table)

    # ------------------------------------------------------------------ #
    # 4. Liabilities table
    # ------------------------------------------------------------------ #
    story.append(Paragraph("Part B — Liabilities", styles["section_heading"]))

    liab_col_widths = [
        usable_width * 0.20,  # category
        usable_width * 0.35,  # description
        usable_width * 0.20,  # lender
        usable_width * 0.25,  # balance
    ]
    liab_rows = [["Category", "Description", "Lender / Institution", "Balance (LKR)"]]
    total_liab_cents = 0
    for lb in liabilities:
        cat = str(getattr(lb, "category", "—")).replace("_", " ").title()
        desc = str(getattr(lb, "description", "—"))
        lender = str(getattr(lb, "lender", "") or "—")
        cents = int(getattr(lb, "balance_lkr_cents", 0) or 0)
        total_liab_cents += cents
        liab_rows.append([cat, desc, lender, _fmt_lkr(cents)])
    liab_rows.append(["", "", "TOTAL LIABILITIES", _fmt_lkr(total_liab_cents)])

    num_data_l = len(liab_rows) - 2
    liab_table = Table(liab_rows, colWidths=liab_col_widths, repeatRows=1)
    liab_table.setStyle(_liability_table_style(num_data_l))
    story.append(liab_table)

    # ------------------------------------------------------------------ #
    # 5. Net Worth summary
    # ------------------------------------------------------------------ #
    net_worth_cents = total_assets_cents - total_liab_cents
    net_sign = "+" if net_worth_cents >= 0 else ""
    story.append(Spacer(1, 8))
    nw_label = "Net Worth (Assets − Liabilities)"
    nw_value = f"{net_sign}{_fmt_lkr(abs(net_worth_cents))}" if net_worth_cents >= 0 else f"−{_fmt_lkr(abs(net_worth_cents))}"
    nw_data = [[nw_label, nw_value]]
    nw_widths = [usable_width * 0.65, usable_width * 0.35]
    nw_table = Table(nw_data, colWidths=nw_widths)
    nw_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (-1, -1), HexColor(_FOREST_GREEN)),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, -1), HexColor("#E8EDE0")),
        ("BOX", (0, 0), (-1, -1), 1, HexColor(_FOREST_GREEN)),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(nw_table)

    # ------------------------------------------------------------------ #
    # 6. Declaration clause + signature
    # ------------------------------------------------------------------ #
    story.append(Spacer(1, 14))
    story.append(Paragraph(
        "Declaration:",
        styles["section_heading"],
    ))
    story.append(Paragraph(
        "I, the undersigned, hereby declare that the information provided above "
        "regarding my assets and liabilities is true and correct to the best of "
        "my knowledge and belief, and that no material information has been "
        "withheld. I understand that furnishing false information to the Inland "
        "Revenue Department of Sri Lanka is a punishable offence under the "
        "Inland Revenue Act No. 24 of 2017.",
        styles["declaration_clause"],
    ))

    # Signature block
    sig_data = [
        ["Signature:", "___________________________", "Date:", generated_date.strftime("%d / %m / %Y")],
        ["Name (Block):", user_name, "NIC:", user_nic or "___________"],
    ]
    sig_widths = [usable_width * 0.16, usable_width * 0.37, usable_width * 0.1, usable_width * 0.37]
    sig_table = Table(sig_data, colWidths=sig_widths)
    sig_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), HexColor(_INK_SOFT)),
        ("TEXTCOLOR", (2, 0), (2, -1), HexColor(_INK_SOFT)),
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(sig_table)

    # Footer note
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        f"Generated by FIESTA · developsrilanka.com · Ref: {ref_id}",
        ParagraphStyle(
            "footer",
            parent=getSampleStyleSheet()["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            textColor=HexColor(_INK_SOFT),
            alignment=TA_CENTER,
        ),
    ))

    # ------------------------------------------------------------------ #
    # Build
    # ------------------------------------------------------------------ #
    def _post_canvas(canv: Any, _doc: Any) -> None:
        canv.setTitle(f"A&L Declaration — {tax_year}")
        try:
            canv._doc.info.creationDate = None  # strip timestamp for determinism
        except Exception:
            pass

    doc.build(story, onFirstPage=_post_canvas, onLaterPages=_post_canvas)
    return buf.getvalue()


__all__ = ["generate_al_pdf"]
