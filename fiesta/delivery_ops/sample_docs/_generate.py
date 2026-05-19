"""Synthetic doc PDF generator for reproducible doc_lens tests.

NOT real client documents. All values are fabricated.

Reuses the v1 t10_extractor sample pattern (working files/ocr/_generate_samples.py)
but extended with a bank-interest sample for BANK_INTEREST_WHT coverage.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

SAMPLES_DIR = Path(__file__).resolve().parent


def _lines(c: "canvas.Canvas", lines: list[str], x: int = 60, y: int = 800) -> None:
    c.setFont("Helvetica", 11)
    current_y = y
    for line in lines:
        c.drawString(x, current_y, line)
        current_y -= 18


def generate_simple_t10(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(60, 820, "STATEMENT OF EMPLOYEE'S REMUNERATION (T10)")
    _lines(
        c,
        [
            "",
            "Year of Assessment: 2024/2025",
            "",
            "Name of the Employer:    ACME CONSULTING (PRIVATE) LIMITED",
            "Employer TIN:            114567890",
            "",
            "Name of the Employee:    NIMAL PERERA",
            "Employee NIC:            199012345678",
            "",
            "Total Gross Remuneration:                LKR 4,800,000.00",
            "Value of Benefits Excluded for Tax:      LKR 0.00",
            "Total Amount of Tax Deducted:            LKR 540,000.00",
            "Total Amount Remitted to IRD:            LKR 540,000.00",
            "",
            "Date Issued: 2025-04-15",
        ],
    )
    c.showPage()
    c.save()


def generate_messy_t10(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(60, 820, "T 10 - Employer Statement")
    _lines(
        c,
        [
            "",
            "Y/A 2024 / 2025",
            "",
            "Name of the Employer: ZENITH HOLDINGS PLC",
            "Employer TIN:      223344556",
            "",
            "Employee Name:  KAMALA SILVA",
            "NIC:            789012345V",
            "",
            "Gross Remuneration                 Rs. 2,160,000",
            "APIT Deducted                      Rs. 162,000",
            "",
        ],
    )
    c.showPage()
    c.save()


def generate_bank_interest_wht(path: Path) -> None:
    """Synthetic bank interest + WHT statement, single account, annual granularity."""
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(60, 820, "INTEREST INCOME & WITHHOLDING TAX STATEMENT")
    _lines(
        c,
        [
            "",
            "Bank: SAMPATH BANK PLC",
            "Branch: COLOMBO MAIN",
            "Account Holder: NIMAL PERERA",
            "Account Number: 010012345678",
            "NIC: 199012345678",
            "",
            "Year of Assessment: 2024/2025",
            "Granularity: Annually",
            "",
            "Interest Income Period: 2024-04-01 to 2025-03-31",
            "  Interest paid: LKR 124,500.00",
            "",
            "Withholding Tax Period: 2024-04-01 to 2025-03-31",
            "  WHT amount: LKR 6,225.00",
            "  WHT certificate number: WHT-2024-A-009912",
            "",
            "Balance as at 2025-03-31: LKR 2,150,000.00",
            "",
        ],
    )
    c.showPage()
    c.save()


def generate_empty(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.showPage()
    c.save()


def generate_malformed(path: Path) -> None:
    """A non-PDF file with .pdf suffix to test the malformed-input path."""
    path.write_text("This is not a PDF file. Just garbage text.\n", encoding="utf-8")


def generate_all(target_dir: Path = SAMPLES_DIR) -> dict[str, Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    out = {
        "t10_simple": target_dir / "t10_simple.pdf",
        "t10_messy": target_dir / "t10_messy.pdf",
        "bank_interest_wht": target_dir / "bank_interest_wht.pdf",
        "empty": target_dir / "empty.pdf",
        "malformed": target_dir / "malformed.pdf",
    }
    generate_simple_t10(out["t10_simple"])
    generate_messy_t10(out["t10_messy"])
    generate_bank_interest_wht(out["bank_interest_wht"])
    generate_empty(out["empty"])
    generate_malformed(out["malformed"])
    return out


if __name__ == "__main__":
    files = generate_all()
    for name, p in files.items():
        print(f"  {name}: {p}")
