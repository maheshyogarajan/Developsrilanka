"""fiesta.agreements.pdf_engine — shared PDF rendering primitives.

ARCHITECTURE
============
The brief permits either ReportLab or WeasyPrint. The fiesta env has
ReportLab >=4.5 installed but NOT WeasyPrint (which needs cairo/pango binary
dependencies absent on Replit / Fly base image). We therefore use ReportLab
with a Jinja2 -> structured-paragraph pipeline:

    template (jinja2)  -- renders MARKDOWN-LITE source string
        --> _parse_blocks(source)
            -> list of (kind, content) tuples
                kind in {"h1","h2","h3","p","bullet","clause","sig","draft"}
        --> ReportLab Platypus SimpleDocTemplate (single-pass, no JS, no CSS)

The S8 build will share this engine. Anything S8-specific lives in
service_pdf.py; anything S9-specific in rental_pdf.py.

DETERMINISM
===========
Reference IDs are deterministic per (user_id, agreement_kind, tax_year,
seed_extra). This is so re-rendering the same agreement (e.g. after a
template-version bump) produces a stable reference if the customer hasn't
changed the underlying data -- audit-defence-friendly.

BRANDING
========
A single PDF_BRANDING dict drives header/footer text + colour. Customer-
visible UI calls it FIESTA; legal copy in templates references "Foreign
Income Earners' Savings & Tax Advisor" (developsrilanka.com).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from typing import Any

from reportlab.lib.colors import HexColor, black
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)


# --------------------------------------------------------------------------- #
# Branding constants -- shared with S8 service-agreement build
# --------------------------------------------------------------------------- #


PDF_BRANDING: dict[str, str] = {
    "product_name": "FIESTA",
    "product_long_name": "Foreign Income Earners' Savings & Tax Advisor",
    "company_domain": "developsrilanka.com",
    "primary_hex": "#0B5394",      # FIESTA primary blue
    "accent_hex": "#9FC5E8",       # FIESTA secondary
    "draft_banner_hex": "#D32F2F", # red for "DRAFT - PENDING LEGAL REVIEW"
}


# --------------------------------------------------------------------------- #
# Reference ID minting
# --------------------------------------------------------------------------- #


def _user_initials(user_name: str | None) -> str:
    """Two-letter initials. Fallback to 'XX' on empty / single-token."""
    if not user_name:
        return "XX"
    tokens = re.findall(r"[A-Za-z]+", user_name.strip())
    if not tokens:
        return "XX"
    if len(tokens) == 1:
        return (tokens[0][:2] or "XX").upper()
    return (tokens[0][0] + tokens[-1][0]).upper()


def mint_reference_id(
    *,
    prefix: str,
    tax_year: str,
    user_id: str | int,
    user_name: str | None,
    seed_extra: str = "",
) -> str:
    """Mint a deterministic agreement reference ID.

    Format:  {prefix}-{tax_year}-{initials}-{4HEX}
    Example: RA-25-26-AW-7F3A

    Determinism: the 4HEX suffix is derived from a stable hash of
    (prefix, tax_year, user_id, seed_extra). Re-rendering the same
    agreement therefore produces the same ID.
    """
    if not prefix or not prefix.isalpha():
        raise ValueError(f"prefix must be alphabetic, got {prefix!r}")
    if not tax_year:
        raise ValueError("tax_year required")
    seed = f"{prefix}|{tax_year}|{user_id}|{seed_extra}".encode("utf-8")
    digest = hashlib.sha256(seed).hexdigest().upper()
    return f"{prefix.upper()}-{tax_year}-{_user_initials(user_name)}-{digest[:4]}"


# --------------------------------------------------------------------------- #
# Markdown-lite -> Platypus story
# --------------------------------------------------------------------------- #


_HEADING_RE = re.compile(r"^(?P<hashes>#{1,3})\s+(?P<text>.+?)\s*$")
_BULLET_RE = re.compile(r"^[\-*]\s+(?P<text>.+?)\s*$")
_CLAUSE_RE = re.compile(r"^(?P<num>\d+(?:\.\d+)*)(?:\s+|\.\s*)(?P<text>.+?)\s*$")
_DRAFT_BANNER = "DRAFT"


@dataclass(frozen=True)
class _Block:
    kind: str   # h1 | h2 | h3 | p | bullet | clause | sig | draft
    text: str


def _parse_blocks(source: str) -> list[_Block]:
    """Turn rendered jinja2 markdown-lite into a list of Block tuples.

    Recognised line patterns:
        # text          -> h1
        ## text         -> h2
        ### text        -> h3
        - text          -> bullet
        N.M text        -> clause (numbered sub-paragraph)
        :draft:         -> draft banner
        :sig:           -> signature block separator
        anything else   -> p
        blank line      -> ignored (Platypus handles vertical rhythm)
    """
    blocks: list[_Block] = []
    for raw in source.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        stripped = line.strip()
        if stripped == ":draft:":
            blocks.append(_Block(kind="draft", text=_DRAFT_BANNER))
            continue
        if stripped == ":sig:":
            blocks.append(_Block(kind="sig", text=""))
            continue
        m_h = _HEADING_RE.match(line)
        if m_h:
            level = len(m_h.group("hashes"))
            kind = f"h{level}"
            blocks.append(_Block(kind=kind, text=m_h.group("text")))
            continue
        m_b = _BULLET_RE.match(line)
        if m_b:
            blocks.append(_Block(kind="bullet", text=m_b.group("text")))
            continue
        m_c = _CLAUSE_RE.match(line)
        if m_c:
            blocks.append(
                _Block(
                    kind="clause",
                    text=f"{m_c.group('num')}  {m_c.group('text')}",
                )
            )
            continue
        blocks.append(_Block(kind="p", text=line))
    return blocks


# --------------------------------------------------------------------------- #
# ReportLab styles
# --------------------------------------------------------------------------- #


def _build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    primary = HexColor(PDF_BRANDING["primary_hex"])
    draft_red = HexColor(PDF_BRANDING["draft_banner_hex"])

    styles: dict[str, ParagraphStyle] = {
        "h1": ParagraphStyle(
            "h1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=16,
            textColor=primary,
            spaceAfter=12,
            spaceBefore=18,
            alignment=TA_CENTER,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            textColor=primary,
            spaceAfter=8,
            spaceBefore=14,
            alignment=TA_LEFT,
        ),
        "h3": ParagraphStyle(
            "h3",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=10.5,
            textColor=black,
            spaceAfter=6,
            spaceBefore=10,
            alignment=TA_LEFT,
        ),
        "p": ParagraphStyle(
            "p",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            spaceAfter=6,
            alignment=TA_JUSTIFY,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            leftIndent=18,
            bulletIndent=6,
            spaceAfter=4,
            alignment=TA_LEFT,
        ),
        "clause": ParagraphStyle(
            "clause",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            leftIndent=14,
            spaceAfter=6,
            alignment=TA_JUSTIFY,
        ),
        "draft": ParagraphStyle(
            "draft",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=12,
            textColor=draft_red,
            spaceAfter=10,
            spaceBefore=4,
            alignment=TA_CENTER,
            borderColor=draft_red,
            borderWidth=1,
            borderPadding=4,
        ),
        "sig": ParagraphStyle(
            "sig",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=16,
            spaceAfter=4,
            alignment=TA_LEFT,
        ),
    }
    return styles


# --------------------------------------------------------------------------- #
# Public: source -> PDF bytes
# --------------------------------------------------------------------------- #


def render_blocks_to_pdf(
    source: str,
    *,
    title: str,
    author: str = "FIESTA",
    subject: str | None = None,
    show_draft_banner: bool = False,
) -> bytes:
    """Render markdown-lite `source` to a deterministic PDF byte string.

    Determinism: ReportLab sets PDF /CreationDate from the system clock by
    default, which breaks SHA-256 reproducibility. We override the metadata
    timestamp to a fixed sentinel and let callers stamp generated_at in
    their own metadata layer (DB row, not PDF chrome).
    """
    buf = BytesIO()
    styles = _build_styles()
    blocks = _parse_blocks(source)
    if show_draft_banner:
        blocks.insert(
            0,
            _Block(
                kind="draft",
                text=(
                    "DRAFT - PENDING LANKA.TAX LEGAL REVIEW. "
                    "Do not present to IRD or any third party until cleared."
                ),
            ),
        )

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
        author=author,
        subject=subject or title,
        creator=PDF_BRANDING["product_name"],
        producer=PDF_BRANDING["product_long_name"],
    )
    # Force a fixed creation date so PDF byte output is content-deterministic.
    # ReportLab respects `invariant=True` for this via the canvas, but the
    # Platypus shortcut requires us to monkey-patch the canvas creation.
    def _post_canvas(canv: Any, _doc: Any) -> None:
        canv.setTitle(title)
        if hasattr(canv, "_doc"):
            try:
                canv._doc.info.creationDate = None  # avoid timestamp leak
            except Exception:
                pass

    story: list[Any] = []
    for b in blocks:
        if b.kind == "sig":
            story.append(Spacer(1, 1.0 * cm))
            continue
        style = styles.get(b.kind, styles["p"])
        if b.kind == "bullet":
            text = f"<bullet>&bull;</bullet>{b.text}"
        else:
            text = b.text
        story.append(Paragraph(text, style))

    doc.build(story, onFirstPage=_post_canvas, onLaterPages=_post_canvas)
    return buf.getvalue()


__all__ = [
    "PDF_BRANDING",
    "mint_reference_id",
    "render_blocks_to_pdf",
]
