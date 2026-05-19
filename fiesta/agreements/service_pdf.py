"""fiesta.agreements.service_pdf -- ReportLab Service Agreement PDF renderer (S8 Wave 3).

Pipeline
--------
    inputs -> §195 disclosure decision -> Jinja2 text body
           -> ReportLab page-by-page render -> bytes + sha256 + metadata

Why ReportLab
-------------
The existing FIESTA codebase already uses ReportLab (fiesta/delivery_ops/sample_docs/_generate.py
+ working files/ocr/_generate_samples.py). ReportLab is pure-Python, no native
deps, ships in pyproject.toml -- consistent with FIESTA's
"add-no-new-runtime-deps" rule. WeasyPrint would have been cleaner for
typography but needs Cairo + Pango binaries; rejected for Replit deploy
simplicity.

PDF format
----------
ReportLab default PDF/A-compatible output (1.4). IRD-accepted -- the doc_lens
test docs (T10, BANK_INTEREST_WHT) are accepted by IRD on the same generator.
For strict PDF/A-1b conformance a later post-process step (pikepdf) can be
bolted on; v0.1 does not require it because the agreement is a customer-side
artefact, not an IRD-electronic-filing payload.

Determinism
-----------
The PDF metadata includes (template_version, generated_at). The body
content is fully determined by inputs; the same inputs at the same UTC
second yield byte-identical output (page-creation timestamp encoded in the
PDF /CreationDate metadata is fixed via a `creation_date_override` knob,
so tests can pin it).
"""
from __future__ import annotations

import hashlib
import io
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

from fiesta.agreements.disclosure import (
    DisclosureDecision,
    DisclosureDecisionInput,
    decide_disclosure,
)


# Template version. Bumps to v1.0 when the Lanka.tax legal pass closes
# G.1.3 (~2026-05-27).
TEMPLATE_VERSION = "v0.1-draft"

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


# --------------------------------------------------------------------------- #
# Reference-id helper
# --------------------------------------------------------------------------- #


def make_reference_id(
    user_initials: str,
    tax_year: str = "25-26",
    *,
    salt: str | bytes | None = None,
) -> str:
    """Build a reference id of the form `SA-{tax_year}-{user_initials}-{4HEX}`.

    The 4HEX block is derived from a SHA-256 of (initials || tax_year || salt
    || os.urandom(8)) so collisions are vanishingly unlikely and the id is
    not predictable from the public fields alone.

    The format `SA-25-26-AW-7F3A` is per G.1.3 v0.1 §4 (line 51).
    """
    initials = re.sub(r"[^A-Z]", "", (user_initials or "").upper())[:4] or "XX"
    tax_year_norm = re.sub(r"[^0-9-]", "", tax_year or "")[:8] or "00-00"

    seed = os.urandom(8)
    if salt is not None:
        seed += salt.encode() if isinstance(salt, str) else salt
    seed += initials.encode() + tax_year_norm.encode()
    h = hashlib.sha256(seed).hexdigest().upper()
    return f"SA-{tax_year_norm}-{initials}-{h[:4]}"


# --------------------------------------------------------------------------- #
# Result dataclass
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ServicePdfRenderResult:
    """What the renderer returns to the route / model layer."""

    pdf_bytes: bytes
    sha256: str
    byte_size: int
    reference_id: str
    template_version: str
    generated_at: datetime
    disclosure: DisclosureDecision
    rendered_body_text: str
    metadata: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Jinja2 env
# --------------------------------------------------------------------------- #


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(disabled_extensions=("j2",), default=False),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


# --------------------------------------------------------------------------- #
# Top-level generator
# --------------------------------------------------------------------------- #


def generate_service_agreement_pdf(
    *,
    user_id: int | str | None,
    user_initials: str,
    customer: dict[str, Any],
    service_provider: dict[str, Any],
    parameters: dict[str, Any] | None = None,
    payments: list[dict[str, Any]] | None = None,
    market_rate_table: dict[str, dict[str, float]] | None = None,
    customer_override_reason: str | None = None,
    customer_opt_in_disclosure: bool = False,
    tax_year: str = "25-26",
    reference_id: str | None = None,
    creation_date_override: datetime | None = None,
    is_draft_preview: bool = False,
) -> ServicePdfRenderResult:
    """Generate a Service Agreement PDF.

    Parameters
    ----------
    user_id : int | str | None
        For audit log; not embedded in PDF content.
    user_initials : str
        2-4 char initials for the reference id (e.g. "MY" for Mahesh
        Yogarajan). Non-alpha chars are stripped.
    customer : dict
        Profile of the FIESTA user (the Contractor). See template.
    service_provider : dict
        Profile of the engagement counterparty (the Client). See template.
    parameters : dict
        Agreement-level params (variants, term, fees, currency, etc.).
    payments : list[dict] | None
        Historical payment cadence data (used by the related-party detector).
    market_rate_table : dict | None
        Optional override for the §195 market-rate-band computation.
    customer_override_reason : str | None
        Free text the customer typed to justify "this is arm's-length".
        Logged + injected into §14.4. Does NOT suppress the clause.
    customer_opt_in_disclosure : bool
        If True, force-render the §195 disclosure even when the detector
        says default-OFF.
    tax_year : str
        e.g. "25-26". Used in the reference id.
    reference_id : str | None
        Optional pre-generated reference id; otherwise computed.
    creation_date_override : datetime | None
        Test/replay hook -- pin PDF /CreationDate.
    is_draft_preview : bool
        If True, render a watermarked "PENDING LEGAL REVIEW" banner stripe
        across every page (free-trial preview path).
    """
    parameters = dict(parameters or {})
    customer = dict(customer or {})
    service_provider = dict(service_provider or {})

    generated_at = creation_date_override or datetime.utcnow()
    ref = reference_id or make_reference_id(user_initials, tax_year=tax_year)

    # --- §195 disclosure decision ---
    disclosure = decide_disclosure(
        DisclosureDecisionInput(
            customer=customer,
            service_provider=service_provider,
            payments=payments,
            market_rate_table=market_rate_table,
            customer_override_reason=customer_override_reason,
            customer_opt_in_disclosure=customer_opt_in_disclosure,
            market_rate_benchmark_text=parameters.get("market_rate_benchmark_text"),
            relationship_label=parameters.get("relationship_label"),
        )
    )

    # --- Jinja2 render of the text body ---
    env = _jinja_env()
    tmpl = env.get_template("service_agreement.j2")
    body_text = tmpl.render(
        reference_id=ref,
        agreement_date=parameters.get("agreement_date") or _format_date(generated_at),
        template_version=TEMPLATE_VERSION,
        client=_normalise_client_dict(service_provider, parameters),
        contractor=_normalise_contractor_dict(customer),
        services_description=parameters.get("services_description"),
        start_date=parameters.get("start_date"),
        end_date=parameters.get("end_date"),
        renewal_variant=parameters.get("renewal_variant", "A"),
        renewal_period=parameters.get("renewal_period"),
        renewal_notice_days=parameters.get("renewal_notice_days"),
        fee_structure_variant=parameters.get("fee_structure_variant", "A"),
        currency=parameters.get("currency", "LKR"),
        monthly_fee_amount=parameters.get("monthly_fee_amount"),
        hourly_rate=parameters.get("hourly_rate"),
        expenses_borne_by=parameters.get("expenses_borne_by", "Contractor"),
        invoice_cadence=parameters.get("invoice_cadence", "monthly"),
        net_days=parameters.get("net_days", 30),
        late_days=parameters.get("late_days", 14),
        late_interest_rate=parameters.get("late_interest_rate", 1.5),
        liability_cap_months=parameters.get("liability_cap_months", 6),
        confidentiality_survival_years=parameters.get(
            "confidentiality_survival_years", 2
        ),
        ip_variant=parameters.get("ip_variant", "A"),
        governing_law_variant=parameters.get("governing_law_variant", "A"),
        chosen_law=parameters.get("chosen_law"),
        arbitration_rules=parameters.get("arbitration_rules"),
        arbitration_seat=parameters.get("arbitration_seat"),
        termination_notice_days=parameters.get("termination_notice_days", 30),
        cure_period_days=parameters.get("cure_period_days", 14),
        final_payment_days=parameters.get("final_payment_days", 14),
        section195_disclosure_clause=disclosure.rendered_clause_text or "",
        deliverables=parameters.get("deliverables") or [],
        reimbursable_expenses=parameters.get("reimbursable_expenses") or [],
    )

    # --- ReportLab paint of the rendered text body ---
    pdf_bytes = _paint_pdf(
        body_text=body_text,
        creation_date=generated_at,
        is_draft_preview=is_draft_preview,
        reference_id=ref,
    )
    pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()

    return ServicePdfRenderResult(
        pdf_bytes=pdf_bytes,
        sha256=pdf_hash,
        byte_size=len(pdf_bytes),
        reference_id=ref,
        template_version=TEMPLATE_VERSION,
        generated_at=generated_at,
        disclosure=disclosure,
        rendered_body_text=body_text,
        metadata={
            "user_id": user_id,
            "tax_year": tax_year,
            "fee_structure_variant": parameters.get("fee_structure_variant", "A"),
            "ip_variant": parameters.get("ip_variant", "A"),
            "governing_law_variant": parameters.get("governing_law_variant", "A"),
            "renewal_variant": parameters.get("renewal_variant", "A"),
            "currency": parameters.get("currency", "LKR"),
            "is_draft_preview": is_draft_preview,
            "sec195_disclosure_applied": disclosure.should_render,
            "sec195_default_was_on": disclosure.detector_default_on,
            "sec195_confidence": disclosure.confidence,
            "sec195_audit_substance_risk": disclosure.audit_substance_risk,
            "sec195_signals": list(disclosure.signals),
        },
    )


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _format_date(d: date | datetime | str | None) -> str:
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    if isinstance(d, str):
        return d
    return ""


def _strip_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _normalise_client_dict(sp: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    """Map service_provider fields to the template's client.* surface.

    The 'service provider' in FIESTA-vocab is the engagement counterparty -- a
    foreign company or SL business. For the template's purposes that
    counterparty IS the Client in the Service Agreement (the entity buying
    the SL Contractor's services). We do this mapping here so callers don't
    have to think about the asymmetry.
    """
    return _strip_none({
        "legal_name": sp.get("legal_name") or sp.get("name"),
        "entity_type": sp.get("entity_type") or parameters.get("client_entity_type"),
        "jurisdiction": sp.get("jurisdiction") or parameters.get("client_jurisdiction"),
        "address": sp.get("address"),
        "registration_number": sp.get("registration_number"),
        "signatory_name": sp.get("signatory_name"),
        "signatory_title": sp.get("signatory_title"),
        "signature_block": sp.get("signature_block"),
        "notice_email": sp.get("notice_email") or sp.get("email"),
    })


def _normalise_contractor_dict(customer: dict[str, Any]) -> dict[str, Any]:
    return _strip_none({
        "full_name": customer.get("full_name") or customer.get("name"),
        "nic": customer.get("nic"),
        "tin": customer.get("tin"),
        "address": customer.get("address"),
        "bank": customer.get("bank"),
        "account": customer.get("account") or customer.get("account_number"),
        "signature_block": customer.get("signature_block"),
        "notice_email": customer.get("notice_email") or customer.get("email"),
    })


def _paint_pdf(
    *,
    body_text: str,
    creation_date: datetime,
    is_draft_preview: bool,
    reference_id: str,
) -> bytes:
    """Render the text body to PDF bytes via ReportLab platypus.

    We use Paragraph + Spacer rather than canvas.drawString so wrapping is
    automatic for the long sentences in clauses 2.2, 5.3, etc. We keep
    styling intentionally minimal -- 11pt Helvetica, no fancy colours --
    because the artefact is a legal document and customers may print it.
    """
    buf = io.BytesIO()

    title = f"FIESTA Service Agreement -- {reference_id}"
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2.0 * cm,
        rightMargin=2.0 * cm,
        topMargin=2.0 * cm,
        bottomMargin=2.0 * cm,
        title=title,
        author="FIESTA -- developsrilanka.com",
        subject="Service Agreement",
        creator=f"FIESTA agreement-generator {TEMPLATE_VERSION}",
        producer="ReportLab via FIESTA",
    )

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        name="AgreementBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        spaceAfter=4,
    )
    h1_style = ParagraphStyle(
        name="AgreementH1",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        spaceBefore=10,
        spaceAfter=6,
    )
    draft_banner_style = ParagraphStyle(
        name="DraftBanner",
        parent=body_style,
        fontName="Helvetica-Bold",
        textColor="#aa0000",
        fontSize=11,
        alignment=1,
    )

    flowables: list = []
    if is_draft_preview:
        flowables.append(
            Paragraph(
                "DRAFT v0.1 -- PENDING LEGAL REVIEW (FIESTA Free Trial preview)",
                draft_banner_style,
            )
        )
        flowables.append(Spacer(1, 6))

    # Convert the rendered body text into Paragraph flowables, treating
    # blank lines as paragraph breaks and lines starting with a digit-and-dot
    # at column 0 ("1. PARTIES") as headings.
    paragraphs = _split_into_paragraphs(body_text)
    for kind, text in paragraphs:
        # Escape angle brackets for ReportLab safety while preserving
        # markdown-like bold formatting (**text** -> <b>text</b>).
        safe_text = _markdownish_to_reportlab(text)
        style = h1_style if kind == "heading" else body_style
        flowables.append(Paragraph(safe_text, style))
        if kind == "heading":
            flowables.append(Spacer(1, 3))

    # Footer-watermark hook for draft preview pages.
    def _on_page(canv, _doc) -> None:  # pragma: no cover -- visual only
        if not is_draft_preview:
            return
        canv.saveState()
        canv.setFont("Helvetica-Bold", 36)
        canv.setFillColorRGB(0.92, 0.85, 0.85)
        canv.translate(A4[0] / 2, A4[1] / 2)
        canv.rotate(45)
        canv.drawCentredString(0, 0, "DRAFT v0.1")
        canv.restoreState()

    # Pin the PDF /CreationDate via reportlab internal hook so identical
    # inputs at the same UTC second yield identical bytes.
    doc._noCreationDate = True  # type: ignore[attr-defined]
    doc.build(flowables, onFirstPage=_on_page, onLaterPages=_on_page)

    raw = buf.getvalue()

    # ReportLab embeds /CreationDate and /ModDate even when we set
    # _noCreationDate. Patch the bytes to a deterministic timestamp so the
    # sha256 hash for the same logical inputs is stable.
    raw = _force_pdf_creation_date(raw, creation_date)
    return raw


def _force_pdf_creation_date(pdf_bytes: bytes, when: datetime) -> bytes:
    """Replace /CreationDate (D:...) and /ModDate (D:...) entries with a
    deterministic stamp derived from `when`, and replace the random PDF
    /ID with a deterministic hash derived from `when`.

    ReportLab emits D:YYYYMMDDHHMMSS+00'00' format. We replace any
    matching `D:14digits[+-]HH'mm'` strings with the pin. We also patch
    the trailer /ID [<hex32><hex32>] pair with a hash of `when` so the
    file is byte-stable for fixed inputs.
    """
    stamp = when.strftime("D:%Y%m%d%H%M%S+00'00'")
    date_pat = re.compile(rb"D:\d{14}[+\-]\d{2}'\d{2}'")
    out = date_pat.sub(stamp.encode("ascii"), pdf_bytes)

    # Deterministic /ID derived from `when`.
    id_seed = hashlib.sha256(when.isoformat().encode("ascii")).hexdigest()[:32].encode("ascii")
    id_pat = re.compile(rb"/ID\s*\[<[0-9a-fA-F]+>\s*<[0-9a-fA-F]+>\s*\]")
    out = id_pat.sub(b"/ID \n[<" + id_seed + b"> <" + id_seed + b"> ]", out)
    return out


_HEADING_RE = re.compile(r"^\s*\d+\.\s+[A-Z][A-Z ]+")


def _split_into_paragraphs(body_text: str) -> list[tuple[str, str]]:
    """Split the Jinja2-rendered body text into (kind, paragraph_text)
    pairs where kind is either "heading" or "para"."""
    paragraphs: list[tuple[str, str]] = []
    buf: list[str] = []
    for line in body_text.splitlines():
        stripped = line.strip()
        if not stripped:
            if buf:
                joined = " ".join(buf).strip()
                if joined:
                    kind = "heading" if _HEADING_RE.match(joined) else "para"
                    paragraphs.append((kind, joined))
                buf = []
            continue
        if _HEADING_RE.match(stripped):
            if buf:
                joined = " ".join(buf).strip()
                if joined:
                    paragraphs.append(("para", joined))
                buf = []
            paragraphs.append(("heading", stripped))
        else:
            buf.append(stripped)
    if buf:
        joined = " ".join(buf).strip()
        if joined:
            paragraphs.append(("para", joined))
    return paragraphs


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ANGLE_RE = re.compile(r"[<>]")


def _markdownish_to_reportlab(text: str) -> str:
    # Escape `<` / `>` first so the eventual <b></b> we emit isn't doubly
    # escaped.
    text = _ANGLE_RE.sub(lambda m: "&lt;" if m.group(0) == "<" else "&gt;", text)
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    return text


__all__ = [
    "TEMPLATE_VERSION",
    "ServicePdfRenderResult",
    "generate_service_agreement_pdf",
    "make_reference_id",
]
