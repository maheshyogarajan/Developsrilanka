"""fiesta.agreements.rental_pdf — S9 Rental Agreement PDF orchestrator.

ENTRY POINT
===========
``render_rental_agreement(input, *, market_rates=None, payments=None)`` is
THE public surface. It:

  1. Calls fiesta.compliance.related_party.detect_related_party to decide
     whether the §195 disclosure should default ON.
  2. Applies any customer / staff override (s195_force_on / s195_force_off).
  3. Owner-rented-from-self heuristic always forces ON.
  4. Computes home_office_portion_lkr from monthly_rent * home_office_pct.
  5. Calls stamp_duty.stamp_duty_for_term to flag exposure if term > 364d.
  6. Renders the jinja2 template into markdown-lite source.
  7. Hands source to pdf_engine.render_blocks_to_pdf for ReportLab output.
  8. Returns (pdf_bytes, RentalPDFOutput) -- metadata sufficient for the
     RentalAgreementGenerated row.

NETWORK
=======
None. Pure function over inputs. Suitable for unit tests + offline batch.

CONCURRENCY
===========
Stateless. Jinja2 environment is module-level + immutable.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Literal

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from fiesta.agreements.models import RentalAgreementInput
from fiesta.agreements.pdf_engine import (
    PDF_BRANDING,
    mint_reference_id,
    render_blocks_to_pdf,
)
from fiesta.agreements.stamp_duty import StampDutyResult, stamp_duty_for_term
from fiesta.compliance import RelatedPartyResult, detect_related_party
from fiesta.compliance.related_party import RelatedPartySignal


TEMPLATE_VERSION = "v1.0-draft-pending-legal-review"

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(enabled_extensions=(), default=False),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


# --------------------------------------------------------------------------- #
# Output dataclass
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RentalPDFOutput:
    """Metadata produced alongside the PDF bytes."""

    reference_id: str
    template_version: str
    generated_at: datetime
    pdf_sha256: str
    pdf_size_bytes: int

    term_days: int
    monthly_rent_lkr: Decimal
    home_office_portion_lkr: Decimal

    s195_disclosure_applied: bool
    s195_default_on_recommended: bool
    s195_confidence: float
    s195_signals: list[str]
    s195_audit_substance_risk: Literal["low", "medium", "high"]
    s195_override_reason: str | None

    stamp_duty_chargeable: bool
    stamp_duty_lkr: Decimal
    stamp_duty_reason: str
    stamp_duty_band: str

    # passthroughs for the persistence layer
    user_id: int
    tax_year: str
    currency: str


# --------------------------------------------------------------------------- #
# §195 decision orchestration
# --------------------------------------------------------------------------- #


def _party_for_detector(p: Any, *, role: str) -> dict[str, Any]:
    """Translate models.Party into the detector dict format.

    role is informational (logging surface only); the detector treats
    customer + service_provider symmetrically except for stated_relationship.
    """
    addr_line = p.address_line or ""
    # Best-effort street/locality split: comma OR ' - ' separator.
    parts = [s.strip() for s in addr_line.replace("\n", ", ").split(",") if s.strip()]
    street = parts[0] if parts else ""
    locality = parts[1] if len(parts) > 1 else ""
    postcode = ""
    if parts and parts[-1].isdigit() and len(parts[-1]) >= 4:
        postcode = parts[-1]
    return {
        "name": p.full_name,
        "nic": p.nic or "",
        "address": {
            "street": street,
            "locality": locality,
            "postcode": postcode,
        },
        "bank_account": p.bank_account or "",
        "_role": role,
    }


def _resolve_related_party(
    input_: RentalAgreementInput,
    market_rates: dict[str, dict[str, float]] | None,
    payments: list[dict[str, Any]] | None,
) -> tuple[RelatedPartyResult, dict[str, Any]]:
    """Run detection + apply overrides. Returns (raw_result, context_dict).

    context_dict mirrors the template's `ctx.related_party` namespace.
    """
    tenant_dict = _party_for_detector(input_.tenant, role="tenant")
    landlord_dict = _party_for_detector(input_.landlord, role="landlord")

    # Owner-rented-from-self always implies §195.
    if input_.customer_status_owner_rented_from_self:
        result = RelatedPartyResult(
            signals=[RelatedPartySignal.STATED_RELATIONSHIP],
            confidence=1.0,
            should_default_on_disclosure=True,
            reasoning=[
                "customer_status_owner_rented_from_self=True: tenant rents "
                "from a property they themselves own (corporate or other "
                "self-managed vehicle). This is always a §195 associated-"
                "person arrangement and requires disclosure."
            ],
            audit_substance_risk="high",
        )
    else:
        # Use empty rate-table if not supplied -- detector tolerates None.
        result = detect_related_party(
            customer=tenant_dict,
            service_provider=landlord_dict,
            payments=payments,
            market_rate_table=market_rates,
        )

    # Apply explicit overrides ------------------------------------------- #
    disclosure_applied = result.should_default_on_disclosure
    if input_.s195_force_on:
        disclosure_applied = True
    if input_.s195_force_off:
        disclosure_applied = False

    # When forcing OFF, we additionally REQUIRE a reason (validator catches
    # the missing case earlier; this is defence in depth).
    if input_.s195_force_off and not input_.s195_override_reason:
        raise ValueError(
            "s195_force_off=True requires s195_override_reason for audit"
        )

    stated_basis = (
        input_.s195_stated_basis
        or (result.reasoning[0] if result.reasoning else "evidence inconclusive")
    )

    ctx = {
        "disclosure_applied": disclosure_applied,
        "default_on_recommended": result.should_default_on_disclosure,
        "stated_basis": stated_basis,
        "evidence_market_rate": True,
        "evidence_payment_cadence": True,
        "evidence_owner_occupation": True,
        "evidence_third_party_quotes": True,
    }
    return result, ctx


# --------------------------------------------------------------------------- #
# Foreign currency normalisation
# --------------------------------------------------------------------------- #

_FX_TO_LKR_FALLBACK: dict[str, Decimal] = {
    # CBSL indicative 2026-05-20 mid-rates; the template uses CBSL selling
    # rate at the date funds reach the bank account, so this is purely a
    # computational helper for storage of "rent in LKR" not a published rate.
    "LKR": Decimal("1.0"),
    "USD": Decimal("315.00"),
    "GBP": Decimal("395.00"),
    "EUR": Decimal("340.00"),
    "AUD": Decimal("210.00"),
    "SGD": Decimal("232.00"),
    "INR": Decimal("3.78"),
    "JPY": Decimal("2.05"),
}


def _to_lkr(amount: Decimal, currency: str, fx_table: dict[str, Decimal] | None) -> Decimal:
    table = fx_table or _FX_TO_LKR_FALLBACK
    rate = table.get(currency.upper())
    if rate is None:
        raise ValueError(
            f"unsupported currency {currency!r}; supply fx_table to override"
        )
    return (amount * rate).quantize(Decimal("0.01"))


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #


def render_rental_agreement(
    input_: RentalAgreementInput,
    *,
    market_rates: dict[str, dict[str, float]] | None = None,
    payments: list[dict[str, Any]] | None = None,
    fx_table: dict[str, Decimal] | None = None,
) -> tuple[bytes, RentalPDFOutput]:
    """Render the S9 Rental Agreement PDF.

    See module docstring. The returned bytes are deterministic for the same
    inputs (modulo the system-clock metadata which is sanitised inside
    pdf_engine).
    """
    # 1. §195 ------------------------------------------------------------ #
    rp_result, rp_ctx = _resolve_related_party(input_, market_rates, payments)

    # 2. Stamp duty ----------------------------------------------------- #
    # total_rent for duty calc = monthly_rent * months covered
    months = max(1, input_.term_days // 30)
    total_rent_lkr = _to_lkr(
        input_.monthly_rent_lkr * Decimal(str(months)),
        input_.currency,
        fx_table,
    )
    sd_result: StampDutyResult = stamp_duty_for_term(
        term_days=input_.term_days,
        total_rent_lkr=total_rent_lkr,
    )

    # 3. Reference ID ---------------------------------------------------- #
    reference_id = mint_reference_id(
        prefix="RA",
        tax_year=input_.tax_year,
        user_id=input_.user_id,
        user_name=input_.user_name,
        seed_extra=f"{input_.term_start.isoformat()}|{input_.monthly_rent_lkr}",
    )

    # 4. Template render ------------------------------------------------- #
    ctx = {
        "reference_id": reference_id,
        "template_version": TEMPLATE_VERSION,
        "agreement_date": date.today(),
        "tax_year": input_.tax_year,
        "tenant": input_.tenant,
        "landlord": input_.landlord,
        "property": input_.property,
        "term_start": input_.term_start,
        "term_end": input_.term_end,
        "term_days": input_.term_days,
        "currency": input_.currency,
        "monthly_rent_lkr": input_.monthly_rent_lkr,
        "home_office_percentage": input_.home_office_percentage,
        "home_office_portion_lkr": input_.home_office_portion_lkr,
        "deposit_months": input_.deposit_months,
        "deposit_lkr": input_.monthly_rent_lkr * Decimal(str(input_.deposit_months)),
        "deposit_return_days": input_.deposit_return_days,
        "rent_due_day": input_.rent_due_day,
        "termination_notice_months": input_.termination_notice_months,
        "rent_arrears_days": input_.rent_arrears_days,
        "notice_email": input_.notice_email,
        "court_district": input_.court_district,
        "related_party": rp_ctx,
        "stamp_duty_lkr": sd_result.payable_amount_lkr,
    }
    template = _env.get_template("rental_agreement.j2")
    source = template.render(
        ctx=ctx,
        branding=PDF_BRANDING,
        show_draft_banner=input_.show_draft_banner,
    )

    # 5. PDF render ------------------------------------------------------ #
    pdf_bytes = render_blocks_to_pdf(
        source,
        title=f"FIESTA Rental Agreement {reference_id}",
        subject="Workspace Rental for Production of Income",
        show_draft_banner=False,  # already in source via :draft: token
    )
    sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    out = RentalPDFOutput(
        reference_id=reference_id,
        template_version=TEMPLATE_VERSION,
        generated_at=datetime.now(timezone.utc),
        pdf_sha256=sha256,
        pdf_size_bytes=len(pdf_bytes),
        term_days=input_.term_days,
        monthly_rent_lkr=input_.monthly_rent_lkr,
        home_office_portion_lkr=input_.home_office_portion_lkr,
        s195_disclosure_applied=rp_ctx["disclosure_applied"],
        s195_default_on_recommended=rp_result.should_default_on_disclosure,
        s195_confidence=rp_result.confidence,
        s195_signals=[s.value for s in rp_result.signals],
        s195_audit_substance_risk=rp_result.audit_substance_risk,
        s195_override_reason=input_.s195_override_reason,
        stamp_duty_chargeable=sd_result.chargeable,
        stamp_duty_lkr=sd_result.payable_amount_lkr,
        stamp_duty_reason=sd_result.reason,
        stamp_duty_band=sd_result.band,
        user_id=input_.user_id,
        tax_year=input_.tax_year,
        currency=input_.currency,
    )
    return pdf_bytes, out


__all__ = [
    "TEMPLATE_VERSION",
    "RentalPDFOutput",
    "render_rental_agreement",
]
