"""fiesta.submit.export -- IRD-ready export pack builder.

Output: a single ZIP file named `FIESTA_TaxReturn_<NIC>_<TY>.zip`
containing:

    ird_return_form_25_26.pdf       (pre-filled IRD Personal Income Tax form)
    fiesta_audit_pack.pdf           (S12 audit pack -- referenced from Submission)
    service_agreements/             (S8 Service Agreement PDFs, one per SP)
        SA-<ref>.pdf
    rental_agreements/              (S9 Rental Agreement PDFs, one per property)
        RA-<ref>.pdf
    README.txt                      (operator instructions for the customer)

The ZIP is deterministic given identical inputs at the same UTC second --
ReportLab embeds /CreationDate metadata; we set that explicitly so the
sha256 of the ZIP is reproducible.

PDF generator
-------------
Reuses ReportLab (already in pyproject; see fiesta/agreements/service_pdf.py
for the precedent). No new runtime deps.

Determinism caveat
------------------
ReportLab's /CreationDate is the only source of run-to-run drift. We set it
to the Submission.ird_export_generated_at value (passed in as `when`). If
the caller passes the same `when` twice the byte-output is identical.

Test surface
------------
- build_ird_return_form_pdf(customer, tax_data, when) -> bytes
- build_export_zip(submission_payload, output_dir, when) -> (zip_path, sha256, byte_size)
- ird_return_form_byte_check(pdf_bytes) -> bool (starts %PDF + ends %%EOF)
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


# Slug helper -- same pattern as fiesta.agreements (kept local to avoid
# cross-package import on the submit branch).
_SLUG_CHARS_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _slugify(text: str) -> str:
    s = unicodedata.normalize("NFKD", text or "")
    s = s.encode("ascii", "ignore").decode("ascii")
    s = _SLUG_CHARS_RE.sub("-", s)
    s = s.strip("-_.")
    return s or "unknown"


def _format_lkr(amount: Any) -> str:
    try:
        n = float(amount or 0)
    except (TypeError, ValueError):
        n = 0.0
    return f"{n:,.2f}"


# ---------------------------------------------------------------------------
# IRD return form PDF builder (pre-filled Form 1 Personal Income Tax)
# ---------------------------------------------------------------------------
def build_ird_return_form_pdf(
    *,
    customer: dict[str, Any],
    tax_year: str,
    tax_data: dict[str, Any],
    when: datetime,
) -> bytes:
    """Generate the pre-filled IRD return form PDF.

    The PDF is INTENT-TO-FILE in v1 -- it's a customer-facing pre-fill that
    the customer prints, signs, and either (a) attaches when they self-file
    on eservices.ird.gov.lk or (b) uses as a reference while keying the
    same numbers in.

    Args:
        customer: {"full_name", "nic", "tin", "address", "email", "phone"}
        tax_year: e.g. "2025/2026"
        tax_data: {"gross_income_lkr", "total_deductions_lkr",
                   "taxable_income_lkr", "tax_payable_lkr",
                   "credits_lkr", "final_tax_payable_lkr",
                   "income_breakdown": [{"source", "amount_lkr"}],
                   "deductions_breakdown": [{"category", "amount_lkr"}]}
        when: UTC timestamp; embedded as /CreationDate for determinism.

    Returns:
        Raw PDF bytes.
    """
    # Lazy import so the package is importable in test envs without
    # ReportLab on path (we skip those tests cleanly).
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        PageBreak,
    )

    buf = io.BytesIO()

    # Deterministic /CreationDate
    creation_date_str = when.strftime("D:%Y%m%d%H%M%SZ00'00'")

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=f"IRD Personal Income Tax Return -- {tax_year}",
        author="FIESTA (pre-fill -- customer is the responsible filer)",
        subject=f"Pre-filled Form 1 PIT -- {tax_year}",
        creator="FIESTA",
        producer="FIESTA / ReportLab",
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle(
        "h1",
        parent=styles["Heading1"],
        fontSize=14,
        spaceAfter=8,
        textColor=colors.HexColor("#1a1a1a"),
    )
    h2 = ParagraphStyle(
        "h2",
        parent=styles["Heading2"],
        fontSize=11,
        spaceAfter=6,
        textColor=colors.HexColor("#333333"),
    )
    body = ParagraphStyle(
        "body",
        parent=styles["BodyText"],
        fontSize=9,
        leading=12,
    )
    label = ParagraphStyle(
        "label",
        parent=styles["BodyText"],
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#555555"),
    )

    story: list[Any] = []

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------
    story.append(
        Paragraph(
            f"<b>Sri Lanka Inland Revenue Department</b><br/>"
            f"Personal Income Tax Return -- Form 1<br/>"
            f"Tax Year: <b>{tax_year}</b>",
            h1,
        )
    )
    story.append(Spacer(1, 4 * mm))
    story.append(
        Paragraph(
            "<i>PRE-FILLED BY FIESTA. The customer is the responsible "
            "filer under section 195 of the Inland Revenue Act No. 24 of "
            "2017. Verify each line before signing and submitting to IRD.</i>",
            label,
        )
    )
    story.append(Spacer(1, 5 * mm))

    # ------------------------------------------------------------------
    # Personal details
    # ------------------------------------------------------------------
    story.append(Paragraph("Personal Details", h2))
    pd_rows = [
        ["Full Name", customer.get("full_name", "")],
        ["NIC", customer.get("nic", "")],
        ["TIN", customer.get("tin", "(not registered)")],
        ["Address", customer.get("address", "")],
        ["Email", customer.get("email", "")],
        ["Phone", customer.get("phone", "")],
    ]
    pd_table = Table(pd_rows, colWidths=[45 * mm, 125 * mm])
    pd_table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f0f0")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(pd_table)
    story.append(Spacer(1, 6 * mm))

    # ------------------------------------------------------------------
    # Income breakdown
    # ------------------------------------------------------------------
    story.append(Paragraph("Income Sources", h2))
    inc_rows = [["Source", "Amount (LKR)"]]
    for item in tax_data.get("income_breakdown") or []:
        inc_rows.append(
            [
                item.get("source", "(unspecified)"),
                _format_lkr(item.get("amount_lkr")),
            ]
        )
    if len(inc_rows) == 1:
        inc_rows.append(["(no income sources declared)", "0.00"])
    inc_rows.append(
        [
            "Gross income total",
            _format_lkr(tax_data.get("gross_income_lkr")),
        ]
    )
    inc_table = Table(inc_rows, colWidths=[120 * mm, 50 * mm])
    inc_table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f8f8f8")),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(inc_table)
    story.append(Spacer(1, 6 * mm))

    # ------------------------------------------------------------------
    # Deductions breakdown
    # ------------------------------------------------------------------
    story.append(Paragraph("Deductions / Qualifying Payments", h2))
    ded_rows = [["Category", "Amount (LKR)"]]
    for item in tax_data.get("deductions_breakdown") or []:
        ded_rows.append(
            [
                item.get("category", "(unspecified)"),
                _format_lkr(item.get("amount_lkr")),
            ]
        )
    if len(ded_rows) == 1:
        ded_rows.append(["(no deductions claimed)", "0.00"])
    ded_rows.append(
        [
            "Total deductions",
            _format_lkr(tax_data.get("total_deductions_lkr")),
        ]
    )
    ded_table = Table(ded_rows, colWidths=[120 * mm, 50 * mm])
    ded_table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f8f8f8")),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(ded_table)
    story.append(Spacer(1, 8 * mm))

    # ------------------------------------------------------------------
    # Tax computation summary
    # ------------------------------------------------------------------
    story.append(Paragraph("Tax Computation", h2))
    comp_rows = [
        ["Gross income", _format_lkr(tax_data.get("gross_income_lkr"))],
        ["Total deductions", _format_lkr(tax_data.get("total_deductions_lkr"))],
        ["Taxable income", _format_lkr(tax_data.get("taxable_income_lkr"))],
        ["Tax payable (before credits)", _format_lkr(tax_data.get("tax_payable_lkr"))],
        ["Tax credits / withholding", _format_lkr(tax_data.get("credits_lkr"))],
        [
            "FINAL TAX PAYABLE",
            _format_lkr(tax_data.get("final_tax_payable_lkr")),
        ],
    ]
    comp_table = Table(comp_rows, colWidths=[120 * mm, 50 * mm])
    comp_table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
                ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 10),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#fff7c2")),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(comp_table)
    story.append(Spacer(1, 8 * mm))

    # ------------------------------------------------------------------
    # Signature block (customer signs by hand if printed; or types on portal)
    # ------------------------------------------------------------------
    story.append(Paragraph("Declaration + Signature", h2))
    story.append(
        Paragraph(
            "I declare that the information given in this return is true and "
            "correct to the best of my knowledge and belief, and that no income "
            "has been omitted or under-stated. "
            "<i>(Section 195, Inland Revenue Act No. 24 of 2017.)</i>",
            body,
        )
    )
    story.append(Spacer(1, 10 * mm))
    sig_rows = [
        ["Signature: ___________________________", "Date: ______________"],
        [
            f"Name (printed): {customer.get('full_name', '')}",
            f"NIC: {customer.get('nic', '')}",
        ],
    ]
    sig_table = Table(sig_rows, colWidths=[100 * mm, 70 * mm])
    sig_table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(sig_table)

    # Build
    doc.build(story)

    pdf_bytes = buf.getvalue()
    buf.close()

    # Patch /CreationDate + /ModDate + /ID for determinism. ReportLab's
    # SimpleDocTemplate writes a random PDF /ID (hex pair) on every build
    # plus /CreationDate using time.time(). For audit-trail purposes we
    # need byte-deterministic output given identical inputs; the cleanest
    # cross-version override is a post-process regex pass.
    pdf_bytes = re.sub(
        rb"/CreationDate \(D:\d+Z?\d*'\d*'?\)",
        f"/CreationDate ({creation_date_str})".encode("ascii"),
        pdf_bytes,
        count=1,
    )
    pdf_bytes = re.sub(
        rb"/ModDate \(D:\d+Z?\d*'\d*'?\)",
        f"/ModDate ({creation_date_str})".encode("ascii"),
        pdf_bytes,
        count=1,
    )
    # Pin /ID to a deterministic hash of (creation_date + customer + tax_year)
    # so same inputs yield identical /ID. ReportLab's actual /ID format is:
    #   /ID \n[<hex32><hex32>]\n
    # (newline-separated, NO space between the two hex pairs, lowercase hex).
    id_seed = (
        creation_date_str
        + (customer.get("nic") or "")
        + (customer.get("full_name") or "")
        + str(tax_year)
    ).encode("utf-8")
    id_hex = hashlib.sha256(id_seed).hexdigest()[:32]
    pdf_bytes = re.sub(
        rb"/ID\s*\[<[0-9A-Fa-f]+>\s*<[0-9A-Fa-f]+>\]",
        f"/ID [<{id_hex}><{id_hex}>]".encode("ascii"),
        pdf_bytes,
        count=1,
    )

    return pdf_bytes


def ird_return_form_byte_check(pdf_bytes: bytes) -> bool:
    """Cheap PDF integrity check: starts with %PDF- and ends with %%EOF."""
    if not pdf_bytes or len(pdf_bytes) < 20:
        return False
    return pdf_bytes[:5] == b"%PDF-" and b"%%EOF" in pdf_bytes[-32:]


# ---------------------------------------------------------------------------
# Export ZIP builder
# ---------------------------------------------------------------------------
def _make_readme(submission_payload: dict[str, Any]) -> bytes:
    customer = submission_payload.get("customer") or {}
    tax_year = submission_payload.get("tax_year", "")
    final_tax = submission_payload.get("tax_data", {}).get(
        "final_tax_payable_lkr"
    )
    txt = (
        f"FIESTA Tax Return Pack -- {tax_year}\n"
        f"=" * 50
        + "\n\n"
        + f"Customer:        {customer.get('full_name', '')}\n"
        + f"NIC:             {customer.get('nic', '')}\n"
        + f"Tax year:        {tax_year}\n"
        + f"Final tax payable: LKR {_format_lkr(final_tax)}\n\n"
        + "Contents\n"
        + "--------\n"
        + "  ird_return_form.pdf      Pre-filled IRD Form 1 (Personal Income Tax).\n"
        + "                           Print, sign, and either attach when self-filing\n"
        + "                           on eservices.ird.gov.lk OR use as a reference\n"
        + "                           while typing the same numbers into the portal.\n"
        + "  fiesta_audit_pack.pdf    Detailed FIESTA audit pack (S12 output).\n"
        + "                           Hold this for 5 years -- IRA section 120.\n"
        + "  service_agreements/      Service Agreements (S8) supporting your\n"
        + "                           consulting / freelance income.\n"
        + "  rental_agreements/       Rental Agreements (S9) supporting your\n"
        + "                           property income.\n\n"
        + "How to use\n"
        + "----------\n"
        + "1. Open https://eservices.ird.gov.lk in your browser (NOT a clone).\n"
        + "2. Log in with your TIN + PIN.\n"
        + "3. Navigate to Return / Schedule Management -> My Returns.\n"
        + "4. Open the return for tax year above.\n"
        + "5. Copy each field from `ird_return_form.pdf` into the portal.\n"
        + "6. Hit Submit. Save the IRD acknowledgment PDF.\n"
        + "7. Come back to FIESTA -> S14 -> 'I have filed' -> upload the PDF.\n\n"
        + "You are the responsible filer under section 195 of the Inland\n"
        + "Revenue Act No. 24 of 2017. FIESTA is a pre-fill / audit tool, not\n"
        + "a filing agent in v1.\n"
    )
    return txt.encode("utf-8")


def build_export_zip(
    *,
    submission_payload: dict[str, Any],
    output_dir: Path,
    when: datetime,
) -> tuple[Path, str, int]:
    """Build the IRD-ready export ZIP.

    Args:
        submission_payload: {
            "customer": {...},
            "tax_year": "2025/2026",
            "tax_data": {...},
            "audit_pack_pdf_path": "abs path to S12 audit pack (may not exist)",
            "service_agreement_pdfs": [{"reference_id": "SA-...", "path": "..."}, ...],
            "rental_agreement_pdfs": [{"reference_id": "RA-...", "path": "..."}, ...],
        }
        output_dir: Directory to write the ZIP into. Created if missing.
        when: UTC timestamp; used for IRD return form /CreationDate AND
              ZIP file mtimes (for determinism).

    Returns:
        (zip_path, sha256_hex, byte_size)
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    customer = submission_payload.get("customer") or {}
    nic = customer.get("nic", "unknown")
    tax_year_slug = (submission_payload.get("tax_year") or "0000-0000").replace(
        "/", "-"
    )
    fname = f"FIESTA_TaxReturn_{_slugify(nic)}_{tax_year_slug}.zip"
    zip_path = output_dir / fname

    # Build the IRD return form PDF.
    ird_pdf = build_ird_return_form_pdf(
        customer=customer,
        tax_year=submission_payload.get("tax_year", ""),
        tax_data=submission_payload.get("tax_data") or {},
        when=when,
    )
    if not ird_return_form_byte_check(ird_pdf):
        raise RuntimeError(
            "Generated IRD return form PDF failed byte-check (%PDF/EOF)"
        )

    # ZIP it deterministically.
    # zipfile.ZipInfo with date_time fixed to `when` makes the ZIP
    # bit-identical when inputs are identical.
    when_tuple = (
        when.year,
        when.month,
        when.day,
        when.hour,
        when.minute,
        when.second,
    )

    # We write to a temp byte stream first so we can hash before flushing
    # to disk.
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:

        def _add(name: str, data: bytes) -> None:
            info = zipfile.ZipInfo(filename=name, date_time=when_tuple)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)

        _add("README.txt", _make_readme(submission_payload))
        _add("ird_return_form.pdf", ird_pdf)

        # Audit pack -- read from disk if it exists.
        audit_path = submission_payload.get("audit_pack_pdf_path")
        if audit_path and Path(audit_path).is_file():
            _add("fiesta_audit_pack.pdf", Path(audit_path).read_bytes())
        else:
            _add(
                "fiesta_audit_pack.pdf",
                b"(audit pack not available -- S12 was not run)",
            )

        for ag in submission_payload.get("service_agreement_pdfs") or []:
            ag_path = ag.get("path")
            ag_ref = ag.get("reference_id") or "SA-unknown"
            if ag_path and Path(ag_path).is_file():
                _add(
                    f"service_agreements/{_slugify(ag_ref)}.pdf",
                    Path(ag_path).read_bytes(),
                )
            else:
                _add(
                    f"service_agreements/{_slugify(ag_ref)}.pdf",
                    b"(file missing at export time)",
                )

        for rp in submission_payload.get("rental_agreement_pdfs") or []:
            rp_path = rp.get("path")
            rp_ref = rp.get("reference_id") or "RA-unknown"
            if rp_path and Path(rp_path).is_file():
                _add(
                    f"rental_agreements/{_slugify(rp_ref)}.pdf",
                    Path(rp_path).read_bytes(),
                )
            else:
                _add(
                    f"rental_agreements/{_slugify(rp_ref)}.pdf",
                    b"(file missing at export time)",
                )

    zip_bytes = zip_buf.getvalue()
    zip_buf.close()

    sha256 = hashlib.sha256(zip_bytes).hexdigest()
    zip_path.write_bytes(zip_bytes)

    # Try to pin the mtime to `when` (best-effort -- some FSes will refuse).
    try:
        ts = when.replace(tzinfo=timezone.utc).timestamp()
        os.utime(zip_path, (ts, ts))
    except (OSError, OverflowError) as exc:  # noqa: PERF203
        logger.debug("Could not pin export ZIP mtime: %s", exc)

    return zip_path, sha256, len(zip_bytes)


__all__ = [
    "build_ird_return_form_pdf",
    "ird_return_form_byte_check",
    "build_export_zip",
]
