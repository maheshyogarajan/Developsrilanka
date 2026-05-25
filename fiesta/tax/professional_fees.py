"""fiesta.tax.professional_fees — G3.2 Professional Fees (LKR + §85 WHT credit).

Section G G3.2 (MS4 W3b): LKR professional-fees earners (lawyers,
accountants, doctors, engineers, consultants, software developers — the
§85(1C) "independent service provider" list) log their per-client invoice
income. §85 WHT (Withholding Tax) withheld by the paying client is
captured as a CREDIT against final IIT liability.

Tax treatment (Sri Lanka):
  - IRA §6: business income (which includes professional-fee receipts when
    the recipient operates as a sole practitioner / consultant). The gross
    professional-fees line is treated alongside business income for IIT
    bracket purposes — both contribute to the personal IIT computation.
  - IRA §85(1C) (effective 2023-01-01, S17/45-of-2023): "a person shall
    withhold tax at the rate of 5% of the payment, where such person pays
    a service fee with a source in Sri Lanka to a resident individual who
    is not an employee of the payer …(c) for services provided by such
    individual in the capacity of independent service provider such as
    doctor, engineer, accountant, lawyer, software developer, researcher,
    academic or any individual service provider as may be prescribed by
    regulation". Threshold: does NOT apply to a service payment of less
    than Rs 100,000 per month.
  - IRA §85(1B) (effective 2023-01-01): 14% WHT on service-fee or
    insurance-premium payments to non-resident persons. Out of scope for
    G3.2 v1 (resident professionals); FK preserves room for an
    'professional_fees_foreign' source-type in future.
  - The withheld tax is a CREDIT against the recipient's final IIT
    liability — when sum-of-WHT > liability, a refund is due. (verified
    2026-05-25 via mcp__ira__get_section)

Persistence (Design Lock 2 §4 + Section G G3.2):
  - One ProfessionalFeeMetadata row per (user, client_name, invoice_date).
    Metadata + §85 WHT credit. Paired with ONE Income row (source_type=
    'professional_fees_lkr') that carries the Money flat columns.
  - WHT is NOT on the Income row — it lives on the metadata row because
    it is a CREDIT, not income.
  - Multiple invoices per client per tax year → multiple metadata rows;
    compute_professional_fee_tax sums them.

Idempotency contract:
  - record_professional_fee(user, client='Foo Ltd', invoice_date=2025-04-01, ...)
    twice produces ONE ProfessionalFeeMetadata + ONE Income row. Second
    call UPDATES gross + WHT. Natural key: (user, client_name, invoice_date).
    Case-insensitive client match.

Provenance: Section G G3.2 in
working files/_fiesta_unification_addendum_20260525.md +
Design Lock 2 §1/§3/§4 in _fiesta_ms1_to_ms4/_canonical_models.md +
IRA §6 + §85(1C) verified 2026-05-25.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from app import db

from fiesta.tax.models import Income
from fiesta.tax.money import Money

logger = logging.getLogger(__name__)


# §85(1C) statutory WHT rate (resident independent professionals,
# effective 2023-01-01). §85(1B) non-resident rate is 14% — out of scope
# for G3.2 v1 but kept as a module-level constant for forward use.
SECTION_85_RESIDENT_RATE_DEFAULT = Decimal("0.05")  # 5%
SECTION_85_NONRESIDENT_RATE_DEFAULT = Decimal("0.14")  # 14%
# §85(3)(b) threshold: payment ≥ Rs 100,000 / month required for WHT to apply.
SECTION_85_MONTHLY_THRESHOLD_LKR = Decimal("100000.00")


# ---------------------------------------------------------------------------
# ORM model — ProfessionalFeeMetadata (1:1 with Income, holds §85 WHT credit)
# ---------------------------------------------------------------------------
class ProfessionalFeeMetadata(db.Model):
    """One invoice-level record per (user, client_name, invoice_date).

    Metadata + §85 WHT credit. The gross-invoice amount lives in the
    paired Income row (source_type='professional_fees_lkr'). WHT cannot
    go on Income because Income.amount semantics are "gross received
    income" — the WHT credit is a separate concept (tax withheld by the
    client at payment, refunded if over-withheld).

    Fields:
      - client_name: free-text label of the paying entity (1-128 chars).
        Case-insensitive natural-key component.
      - invoice_number: optional invoice identifier issued by the
        recipient.
      - wht_certificate_ref: optional client-issued §85 WHT certificate
        reference; used for IRD audit pack.
      - wht_credit_lkr: §85 WHT withheld by the client at payment, in LKR.
        Subtracted from final IIT liability (NOT from gross).
      - invoice_date: the date the invoice was raised. Used both for
        natural-key uniqueness and for tax-year derivation.
      - service_description: free-text label of the service rendered
        (e.g. "Legal opinion — capital gains structuring"). Up to 512
        chars; informational only (no tax-engine consequence).
      - income_id: FK to the paired Income row.
      - evidence_refs: provenance for audit pack.
    """

    __tablename__ = "professional_fee_metadata"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tax_year = db.Column(db.String(7), nullable=False, index=True)  # "2025/26"

    client_name = db.Column(db.String(128), nullable=False)
    invoice_number = db.Column(db.String(64), nullable=True)
    wht_certificate_ref = db.Column(db.String(128), nullable=True)
    wht_credit_lkr = db.Column(
        db.Numeric(20, 2), nullable=False, default=Decimal("0")
    )

    invoice_date = db.Column(db.Date, nullable=False)
    service_description = db.Column(db.String(512), nullable=True)

    income_id = db.Column(
        db.Integer,
        db.ForeignKey("incomes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    evidence_refs = db.Column(db.JSON, nullable=False, default=list)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        db.Index(
            "ix_professional_fee_metadata_user_tax_year",
            "user_id", "tax_year",
        ),
        # Idempotency anchor — (user, client_name, invoice_date). Two
        # invoices to the same client on the same day are uncommon enough
        # that they probably indicate user error; if a real one comes up
        # the user can use a distinct invoice_number to differentiate (the
        # composite uniqueness is per-application, not a DB UNIQUE
        # constraint, to keep migration flexibility).
        db.Index(
            "ix_professional_fee_metadata_user_client_date",
            "user_id", "client_name", "invoice_date",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ProfessionalFeeMetadata id={self.id} user_id={self.user_id} "
            f"client={self.client_name!r} invoice_date={self.invoice_date} "
            f"wht_credit={self.wht_credit_lkr}>"
        )


# ---------------------------------------------------------------------------
# Tax-year derivation (SL Y/A runs 1 April → 31 March)
# ---------------------------------------------------------------------------
def _tax_year_for(d: date) -> str:
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}/{str(start + 1)[2:]}"


def _normalise_tax_year(ty: str) -> str:
    s = (ty or "").strip().replace("-", "/")
    if "/" in s:
        head, tail = s.split("/", 1)
        if len(tail) == 4 and tail.startswith(head[:2]):
            return f"{head}/{tail[2:]}"
        return s
    return s


# ---------------------------------------------------------------------------
# Idempotent finder (natural key = user_id + client_name + invoice_date)
# ---------------------------------------------------------------------------
def _find_existing_metadata(
    user_id: int,
    client_name: str,
    invoice_date: date,
) -> Optional[ProfessionalFeeMetadata]:
    if not client_name:
        return None
    try:
        rows = (
            ProfessionalFeeMetadata.query
            .filter_by(user_id=user_id, invoice_date=invoice_date)
            .all()
        )
    except Exception:  # pragma: no cover
        return None
    name_lc = client_name.strip().lower()
    for r in rows:
        if (r.client_name or "").strip().lower() == name_lc:
            return r
    return None


# ---------------------------------------------------------------------------
# Income recording — creates/updates ProfessionalFeeMetadata + paired Income
# ---------------------------------------------------------------------------
def record_professional_fee(
    user,
    client_name: str,
    gross_money: Money,
    wht_withheld_money: Optional[Money],
    invoice_date: date,
    service_description: Optional[str] = None,
    invoice_number: Optional[str] = None,
    wht_certificate_ref: Optional[str] = None,
    tax_year: Optional[str] = None,
    evidence_refs: Optional[list[dict[str, Any]]] = None,
) -> ProfessionalFeeMetadata:
    """Record (or update) a professional-fee invoice for (user, client, date).

    Side effects in one transaction:
      1. CREATE or UPDATE ProfessionalFeeMetadata row.
      2. CREATE or UPDATE paired Income row (source_type='professional_fees_lkr').
      3. ADD 'professional_fees_lkr' to user.income_sources (idempotent).

    Idempotency: same (user, client_name, invoice_date) → same row id;
    gross + WHT overwritten.

    Args:
        user:                   User ORM row (must have id + income_sources).
        client_name:            Free-text label of the paying client.
        gross_money:            Money — gross invoice amount.
        wht_withheld_money:     Money — §85 WHT withheld by the client. None
                                or Money(0) means no WHT (e.g. invoice
                                below the Rs 100K/month threshold per §85(3)).
        invoice_date:           Date the invoice was raised.
        service_description:    Optional free-text label.
        invoice_number:         Optional invoice identifier.
        wht_certificate_ref:    Optional certificate identifier for audit.
        tax_year:               Canonical 'YYYY/YY'. If None, derived from
                                invoice_date.
        evidence_refs:          Optional provenance refs.

    Returns:
        The persisted ProfessionalFeeMetadata row (id populated).
    """
    if user is None or getattr(user, "id", None) is None:
        raise ValueError("user with .id is required")
    if not client_name or not str(client_name).strip():
        raise ValueError("client_name is required")
    if gross_money is None:
        raise ValueError("gross_money is required")
    if gross_money.amount is None or gross_money.amount < 0:
        raise ValueError(
            f"gross_money.amount must be >= 0; got {gross_money.amount}"
        )
    if invoice_date is None:
        raise ValueError("invoice_date is required")

    ty = _normalise_tax_year(tax_year) if tax_year else _tax_year_for(invoice_date)
    name_clean = str(client_name).strip()[:128]

    wht_lkr = Decimal("0.00")
    if wht_withheld_money is not None:
        if wht_withheld_money.amount is None or wht_withheld_money.amount < 0:
            raise ValueError("wht_withheld_money.amount must be >= 0")
        wht_lkr = Decimal(str(wht_withheld_money.amount_lkr)).quantize(
            Decimal("0.01")
        )

    refs = list(evidence_refs or [])

    # ---- Idempotency check ----
    existing = _find_existing_metadata(user.id, name_clean, invoice_date)

    if existing is not None:
        existing.tax_year = ty
        existing.wht_credit_lkr = wht_lkr
        if invoice_number is not None:
            existing.invoice_number = (
                str(invoice_number).strip()[:64] or None
            )
        if wht_certificate_ref is not None:
            existing.wht_certificate_ref = (
                str(wht_certificate_ref).strip()[:128] or None
            )
        if service_description is not None:
            existing.service_description = (
                str(service_description).strip()[:512] or None
            )
        if refs:
            existing.evidence_refs = refs

        inc = None
        if existing.income_id:
            inc = Income.query.get(int(existing.income_id))
        if inc is None:
            inc = _new_income_for(
                user_id=user.id,
                tax_year=ty,
                money=gross_money,
                refs=_with_professional_fee_ref(refs, existing.id),
            )
            db.session.add(inc)
            db.session.flush()
            existing.income_id = inc.id
        else:
            inc.tax_year = ty
            inc.amount = gross_money.amount
            inc.currency = (gross_money.currency or "LKR").upper()
            inc.fx_rate = gross_money.fx_rate
            inc.fx_source = gross_money.fx_source
            inc.fx_date = gross_money.fx_date
            inc.amount_lkr = gross_money.amount_lkr
            inc.source_type = "professional_fees_lkr"
            inc.source_country = None
            inc.evidence_refs = _with_professional_fee_ref(refs, existing.id)

        _add_income_source(user, "professional_fees_lkr")
        db.session.commit()
        logger.info(
            "Professional fee UPDATED: user=%s client=%r invoice_date=%s "
            "gross_lkr=%s wht_lkr=%s",
            user.id, name_clean, invoice_date, gross_money.amount_lkr, wht_lkr,
        )
        return existing

    meta = ProfessionalFeeMetadata(
        user_id=user.id,
        tax_year=ty,
        client_name=name_clean,
        invoice_number=(
            (str(invoice_number).strip()[:64] or None)
            if invoice_number is not None else None
        ),
        wht_certificate_ref=(
            (str(wht_certificate_ref).strip()[:128] or None)
            if wht_certificate_ref is not None else None
        ),
        wht_credit_lkr=wht_lkr,
        invoice_date=invoice_date,
        service_description=(
            (str(service_description).strip()[:512] or None)
            if service_description is not None else None
        ),
        evidence_refs=refs,
    )
    db.session.add(meta)
    db.session.flush()

    inc = _new_income_for(
        user_id=user.id,
        tax_year=ty,
        money=gross_money,
        refs=_with_professional_fee_ref(refs, meta.id),
    )
    db.session.add(inc)
    db.session.flush()
    meta.income_id = inc.id

    _add_income_source(user, "professional_fees_lkr")
    db.session.commit()
    logger.info(
        "Professional fee CREATED: user=%s client=%r invoice_date=%s "
        "gross_lkr=%s wht_lkr=%s meta_id=%s income_id=%s",
        user.id, name_clean, invoice_date, gross_money.amount_lkr, wht_lkr,
        meta.id, inc.id,
    )
    return meta


def _new_income_for(
    user_id: int,
    tax_year: str,
    money: Money,
    refs: list[dict[str, Any]],
) -> Income:
    return Income(
        user_id=user_id,
        tax_year=tax_year,
        source_type="professional_fees_lkr",
        amount=money.amount,
        currency=(money.currency or "LKR").upper(),
        fx_rate=money.fx_rate,
        fx_source=money.fx_source,
        fx_date=money.fx_date,
        amount_lkr=money.amount_lkr,
        source_country=None,
        evidence_refs=refs,
    )


def _with_professional_fee_ref(
    refs: list[dict[str, Any]], meta_id: int,
) -> list[dict[str, Any]]:
    out = list(refs or [])
    if not any(
        isinstance(r, dict)
        and r.get("type") == "professional_fee_metadata"
        and int(r.get("ref_id", -1)) == int(meta_id)
        for r in out
    ):
        out.append({"type": "professional_fee_metadata", "ref_id": int(meta_id)})
    return out


def _add_income_source(user, source_type: str) -> None:
    sources = list(getattr(user, "income_sources", None) or [])
    if source_type not in sources:
        sources.append(source_type)
        user.income_sources = sources
        try:
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(user, "income_sources")
        except Exception:  # pragma: no cover
            pass


# ---------------------------------------------------------------------------
# Aggregate computation — gross, WHT credit, lines per tax year
# ---------------------------------------------------------------------------
def compute_professional_fee_tax(user, tax_year: str) -> dict[str, Any]:
    """Compute professional-fees tax-bill components for ``user`` in ``tax_year``.

    Returns dict:
        {
            "tax_year":              "2025/26",
            "clients":               [dict, …],
            "gross_total_lkr":       Decimal,
            "wht_credit_total_lkr":  Decimal,
            "net_tax_lkr":           Decimal,  # informational placeholder
        }

    The engine computes IIT on gross_total_lkr (routed via the
    professional_fees_lkr → engine.employment_lkr bucket convention — see
    G3.2 spec). The WHT credit is reported separately so the UI shows
    "you've prepaid X via §85 WHT, your bracket tax is Y, net owed = Y - X".
    """
    ty = _normalise_tax_year(tax_year)

    rows = (
        ProfessionalFeeMetadata.query
        .filter_by(user_id=user.id, tax_year=ty)
        .order_by(
            ProfessionalFeeMetadata.invoice_date.asc(),
            ProfessionalFeeMetadata.id.asc(),
        )
        .all()
    )

    clients: list[dict[str, Any]] = []
    gross_total = Decimal("0")
    wht_total = Decimal("0")

    for meta in rows:
        inc = Income.query.get(int(meta.income_id)) if meta.income_id else None
        gross_lkr = (
            Decimal(str(inc.amount_lkr))
            if (inc and inc.amount_lkr is not None) else Decimal("0")
        )
        wht_lkr = Decimal(str(meta.wht_credit_lkr or 0))
        gross_total += gross_lkr
        wht_total += wht_lkr
        clients.append({
            "meta_id": int(meta.id),
            "income_id": int(meta.income_id) if meta.income_id else None,
            "client_name": meta.client_name,
            "invoice_number": meta.invoice_number,
            "wht_certificate_ref": meta.wht_certificate_ref,
            "invoice_date": (
                meta.invoice_date.isoformat() if meta.invoice_date else None
            ),
            "service_description": meta.service_description,
            "gross_lkr": gross_lkr,
            "wht_credit_lkr": wht_lkr,
            "currency": inc.currency if inc else "LKR",
            "fx_rate": (
                Decimal(str(inc.fx_rate)) if inc else Decimal("1.0")
            ),
        })

    return {
        "tax_year": ty,
        "clients": clients,
        "gross_total_lkr": gross_total.quantize(Decimal("0.01")),
        "wht_credit_total_lkr": wht_total.quantize(Decimal("0.01")),
        "net_tax_lkr": Decimal("0.00"),
    }


# ---------------------------------------------------------------------------
# Listing helpers used by routes
# ---------------------------------------------------------------------------
def list_professional_fees_for_user(
    user, tax_year: Optional[str] = None,
) -> list[ProfessionalFeeMetadata]:
    q = ProfessionalFeeMetadata.query.filter_by(user_id=user.id)
    if tax_year:
        q = q.filter_by(tax_year=_normalise_tax_year(tax_year))
    return q.order_by(
        ProfessionalFeeMetadata.tax_year.desc(),
        ProfessionalFeeMetadata.invoice_date.desc(),
    ).all()


def get_professional_fee_for_user(
    user, meta_id: int,
) -> Optional[ProfessionalFeeMetadata]:
    row = ProfessionalFeeMetadata.query.get(int(meta_id))
    if row is None or int(row.user_id) != int(user.id):
        return None
    return row


def delete_professional_fee_for_user(user, meta_id: int) -> bool:
    row = get_professional_fee_for_user(user, meta_id)
    if row is None:
        return False
    if row.income_id:
        inc = Income.query.get(int(row.income_id))
        if inc is not None:
            db.session.delete(inc)
    db.session.delete(row)
    db.session.commit()
    return True


__all__ = [
    "ProfessionalFeeMetadata",
    "SECTION_85_RESIDENT_RATE_DEFAULT",
    "SECTION_85_NONRESIDENT_RATE_DEFAULT",
    "SECTION_85_MONTHLY_THRESHOLD_LKR",
    "record_professional_fee",
    "compute_professional_fee_tax",
    "list_professional_fees_for_user",
    "get_professional_fee_for_user",
    "delete_professional_fee_for_user",
]
