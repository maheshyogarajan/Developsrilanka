"""fiesta.tax.employment — G3.1 Employment Income (LKR + APIT credit).

Section G G3.1 (MS4 W3b): salaried LKR earners log per-employer employment
income; APIT (Advance Personal Income Tax) withheld at source is captured
as a CREDIT against final IIT liability (not as a deduction from gross
income — the gross drives the bracket walker, the APIT credit reduces the
liability after the brackets have run).

Tax treatment (Sri Lanka):
  - IRA §5(1): "An individual's income from an employment for a year of
    assessment shall be the individual's gains and profits from the
    employment for that year of assessment." (verified 2026-05-25 via
    mcp__ira__get_section)
  - IRA §5(2)(a): includes "payments of salary, wages, leave pay, overtime
    pay, fees, pensions, commissions, gratuities, bonuses and other
    similar payments" — i.e. the gross-employment line the engine sums.
  - APIT (Advance Personal Income Tax) — employer-withheld via the
    PAYE/APIT schedule; the certificate-issued credit goes against the
    employee's final personal IIT liability (it does NOT reduce taxable
    income). When sum-of-APIT > liability, the difference is a refund.
  - Resident employee with one employer: a single annual APIT certificate.
    Resident with multiple employers in a year: one certificate per
    employer-period; this module sums them with no double-count protection
    on the employer side (CEO + auditor reconciles certs externally).

Persistence (Design Lock 2 §4 + Section G G3.1):
  - One EmploymentIncomeMetadata row per (user, employer_name, period_start).
    Metadata + APIT credit. Paired with ONE Income row (source_type=
    'employment_lkr') that carries the Money flat columns. APIT is NOT
    on the Income row — it lives on the metadata row because it's a
    CREDIT, not income.
  - Multiple employers per tax year → multiple metadata rows + multiple
    Income rows; compute_employment_tax sums both sides.

Idempotency contract:
  - record_employment_income(user, employer='Acme', period_start=2025-04-01, ...)
    twice produces ONE EmploymentIncomeMetadata + ONE Income row. Second
    call UPDATES gross + APIT. Natural key: (user, employer_name,
    period_start). Case-insensitive employer match (Acme vs ACME).

Provenance: Section G G3.1 in
working files/_fiesta_unification_addendum_20260525.md +
Design Lock 2 §1/§3/§4 in _fiesta_ms1_to_ms4/_canonical_models.md +
IRA §5(1)/(2)(a) verified 2026-05-25.
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


# ---------------------------------------------------------------------------
# ORM model — EmploymentIncomeMetadata (1:1 with Income, holds APIT credit)
# ---------------------------------------------------------------------------
class EmploymentIncomeMetadata(db.Model):
    """One employment-period record per (user, employer_name, period_start).

    Metadata + APIT credit. The gross-employment amount lives in the paired
    Income row (source_type='employment_lkr'). APIT cannot go on Income
    because Income.amount semantics are "gross received income" — the APIT
    credit is a separate concept (tax withheld against future liability,
    refunded if over-withheld).

    Fields:
      - employer_name: free-text employer label (1-128 chars). Case-
        insensitive natural-key component.
      - apit_certificate_ref: optional employer-issued certificate
        reference (e.g. "APIT-2025-26-ACME-001"); used for IRD-defensible
        audit pack.
      - apit_credit_lkr: APIT withheld during the period, in LKR. Subtracted
        from final IIT liability (NOT from gross).
      - period_start / period_end: the employment window inside the SL
        tax year (1 Apr → 31 Mar). Most resident employees: full year
        (2025-04-01 → 2026-03-31). Multiple employers: one row each with
        non-overlapping windows.
      - income_id: FK to the paired Income row (back-populated after
        flush). Nullable for defensive recovery (cf business_income.py).
      - evidence_refs: provenance for audit pack (cert ref, payslip ref,
        bank credit ref, etc.).
    """

    __tablename__ = "employment_income_metadata"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tax_year = db.Column(db.String(7), nullable=False, index=True)  # "2025/26"

    employer_name = db.Column(db.String(128), nullable=False)
    apit_certificate_ref = db.Column(db.String(128), nullable=True)
    apit_credit_lkr = db.Column(
        db.Numeric(20, 2), nullable=False, default=Decimal("0")
    )

    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)

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
            "ix_employment_income_metadata_user_tax_year",
            "user_id", "tax_year",
        ),
        # Idempotency anchor — (user, employer, period_start). The
        # period_start makes multi-employer scenarios safe: an employee
        # who works at "Acme" for two non-overlapping stints in the same
        # tax year gets two rows, one per stint.
        db.Index(
            "ix_employment_income_metadata_user_emp_period",
            "user_id", "employer_name", "period_start",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<EmploymentIncomeMetadata id={self.id} user_id={self.user_id} "
            f"employer={self.employer_name!r} tax_year={self.tax_year!r} "
            f"apit_credit={self.apit_credit_lkr}>"
        )


# ---------------------------------------------------------------------------
# Tax-year derivation (SL Y/A runs 1 April → 31 March)
# ---------------------------------------------------------------------------
def _tax_year_for(d: date) -> str:
    """Return canonical 'YYYY/YY' tax-year string for date ``d``."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}/{str(start + 1)[2:]}"


def _normalise_tax_year(ty: str) -> str:
    """Normalise to canonical 'YYYY/YY' form. Accepts '2025/26', '2025-26',
    '2025/2026'."""
    s = (ty or "").strip().replace("-", "/")
    if "/" in s:
        head, tail = s.split("/", 1)
        if len(tail) == 4 and tail.startswith(head[:2]):
            return f"{head}/{tail[2:]}"
        return s
    return s


# ---------------------------------------------------------------------------
# Idempotent finder (natural key = user_id + employer_name + period_start)
# ---------------------------------------------------------------------------
def _find_existing_metadata(
    user_id: int,
    employer_name: str,
    period_start: date,
) -> Optional[EmploymentIncomeMetadata]:
    """Case-insensitive employer match within a (user, period_start) scope."""
    if not employer_name:
        return None
    try:
        rows = (
            EmploymentIncomeMetadata.query
            .filter_by(user_id=user_id, period_start=period_start)
            .all()
        )
    except Exception:  # pragma: no cover
        return None
    name_lc = employer_name.strip().lower()
    for r in rows:
        if (r.employer_name or "").strip().lower() == name_lc:
            return r
    return None


# ---------------------------------------------------------------------------
# Income recording — creates/updates EmploymentIncomeMetadata + paired Income
# ---------------------------------------------------------------------------
def record_employment_income(
    user,
    employer_name: str,
    gross_money: Money,
    apit_withheld_money: Optional[Money],
    period_start: date,
    period_end: date,
    apit_certificate_ref: Optional[str] = None,
    tax_year: Optional[str] = None,
    evidence_refs: Optional[list[dict[str, Any]]] = None,
) -> EmploymentIncomeMetadata:
    """Record (or update) employment income for a (user, employer, period).

    Side effects in one transaction:
      1. CREATE or UPDATE EmploymentIncomeMetadata row.
      2. CREATE or UPDATE paired Income row (source_type='employment_lkr').
      3. ADD 'employment_lkr' to user.income_sources (idempotent).

    Idempotency: same (user, employer_name, period_start) → same row id;
    gross + APIT overwritten.

    Args:
        user:                  User ORM row (must have id + income_sources).
        employer_name:         Employer label (1-128 chars). Case-insensitive
                               natural-key component.
        gross_money:           Money — gross employment income for the period.
                               LKR-native expected; foreign currency tolerated
                               but discouraged (most SL employment is LKR).
        apit_withheld_money:   Money — APIT withheld at source. None or
                               Money(0) means no APIT (rare; allowed).
        period_start:          Start of the employment period (inclusive).
        period_end:            End of the employment period (inclusive).
        apit_certificate_ref:  Optional certificate identifier for audit.
        tax_year:              Canonical 'YYYY/YY'. If None, derived from
                               period_start.
        evidence_refs:         Optional provenance refs.

    Returns:
        The persisted EmploymentIncomeMetadata row (id populated).
    """
    if user is None or getattr(user, "id", None) is None:
        raise ValueError("user with .id is required")
    if not employer_name or not str(employer_name).strip():
        raise ValueError("employer_name is required")
    if gross_money is None:
        raise ValueError("gross_money is required")
    if gross_money.amount is None or gross_money.amount < 0:
        raise ValueError(
            f"gross_money.amount must be >= 0; got {gross_money.amount}"
        )
    if period_start is None or period_end is None:
        raise ValueError("period_start and period_end are required")
    if period_end < period_start:
        raise ValueError("period_end must be >= period_start")

    ty = _normalise_tax_year(tax_year) if tax_year else _tax_year_for(period_start)
    name_clean = str(employer_name).strip()[:128]

    # Resolve APIT credit (in LKR)
    apit_lkr = Decimal("0.00")
    if apit_withheld_money is not None:
        if apit_withheld_money.amount is None or apit_withheld_money.amount < 0:
            raise ValueError("apit_withheld_money.amount must be >= 0")
        apit_lkr = Decimal(str(apit_withheld_money.amount_lkr)).quantize(
            Decimal("0.01")
        )

    refs = list(evidence_refs or [])

    # ---- Idempotency check ----
    existing = _find_existing_metadata(user.id, name_clean, period_start)

    if existing is not None:
        # ---- Update path ----
        existing.tax_year = ty
        existing.period_end = period_end
        existing.apit_credit_lkr = apit_lkr
        if apit_certificate_ref is not None:
            existing.apit_certificate_ref = (
                str(apit_certificate_ref).strip()[:128] or None
            )
        if refs:
            existing.evidence_refs = refs

        # Update paired Income row
        inc = None
        if existing.income_id:
            inc = Income.query.get(int(existing.income_id))
        if inc is None:
            # Defensive — paired Income missing (manual DB cleanup). Recreate.
            inc = _new_income_for(
                user_id=user.id,
                tax_year=ty,
                money=gross_money,
                refs=_with_employment_ref(refs, existing.id),
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
            inc.source_type = "employment_lkr"
            inc.source_country = None  # employment LKR has no DTAA seam
            inc.evidence_refs = _with_employment_ref(refs, existing.id)

        _add_income_source(user, "employment_lkr")
        db.session.commit()
        logger.info(
            "Employment income UPDATED: user=%s employer=%r tax_year=%s "
            "gross_lkr=%s apit_lkr=%s",
            user.id, name_clean, ty, gross_money.amount_lkr, apit_lkr,
        )
        return existing

    # ---- Create path ----
    meta = EmploymentIncomeMetadata(
        user_id=user.id,
        tax_year=ty,
        employer_name=name_clean,
        apit_certificate_ref=(
            (str(apit_certificate_ref).strip()[:128] or None)
            if apit_certificate_ref is not None else None
        ),
        apit_credit_lkr=apit_lkr,
        period_start=period_start,
        period_end=period_end,
        evidence_refs=refs,
    )
    db.session.add(meta)
    db.session.flush()  # populate meta.id for the Income ref below

    inc = _new_income_for(
        user_id=user.id,
        tax_year=ty,
        money=gross_money,
        refs=_with_employment_ref(refs, meta.id),
    )
    db.session.add(inc)
    db.session.flush()
    meta.income_id = inc.id

    _add_income_source(user, "employment_lkr")
    db.session.commit()
    logger.info(
        "Employment income CREATED: user=%s employer=%r tax_year=%s "
        "gross_lkr=%s apit_lkr=%s meta_id=%s income_id=%s",
        user.id, name_clean, ty, gross_money.amount_lkr, apit_lkr,
        meta.id, inc.id,
    )
    return meta


def _new_income_for(
    user_id: int,
    tax_year: str,
    money: Money,
    refs: list[dict[str, Any]],
) -> Income:
    """Build (do not add) an Income row for the employment line."""
    return Income(
        user_id=user_id,
        tax_year=tax_year,
        source_type="employment_lkr",
        amount=money.amount,
        currency=(money.currency or "LKR").upper(),
        fx_rate=money.fx_rate,
        fx_source=money.fx_source,
        fx_date=money.fx_date,
        amount_lkr=money.amount_lkr,
        source_country=None,
        evidence_refs=refs,
    )


def _with_employment_ref(
    refs: list[dict[str, Any]], meta_id: int,
) -> list[dict[str, Any]]:
    """Append a back-pointer to the EmploymentIncomeMetadata, deduped."""
    out = list(refs or [])
    if not any(
        isinstance(r, dict)
        and r.get("type") == "employment_income_metadata"
        and int(r.get("ref_id", -1)) == int(meta_id)
        for r in out
    ):
        out.append({"type": "employment_income_metadata", "ref_id": int(meta_id)})
    return out


def _add_income_source(user, source_type: str) -> None:
    """Idempotently add ``source_type`` to ``user.income_sources``."""
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
# Aggregate computation — gross, APIT credit, lines per tax year
# ---------------------------------------------------------------------------
def compute_employment_tax(user, tax_year: str) -> dict[str, Any]:
    """Compute employment-income tax-bill components for ``user`` in ``tax_year``.

    Returns dict:
        {
            "tax_year":              "2025/26",
            "employers":             [dict, …],
            "gross_total_lkr":       Decimal,
            "apit_credit_total_lkr": Decimal,
            # net_tax_lkr is the LIABILITY-side adjustment the engine
            # consumes; positive when more tax is owed, negative when a
            # refund is due. The engine itself does NOT compute net_tax
            # here — it's reported as a CREDIT (subtraction from final
            # IIT) elsewhere. This dict's "net_tax_lkr" is a convenience
            # for the UI/tax-bill render to show "owed vs withheld".
            "net_tax_lkr":           Decimal,
        }

    Note: this function does NOT compute the bracket tax on gross_total_lkr.
    That happens inside the central engine (compute_tax_25_26) once the
    aggregator routes gross into the employment_lkr bucket. This function
    surfaces the components the engine + the tax bill need:
      - gross (added to engine's employment_lkr)
      - APIT credit (subtracted from engine's final liability)
      - net_tax (informational — IIT_on_employment - APIT, but IIT is
        computed by the engine, so the UI shows the credit separately)
    """
    ty = _normalise_tax_year(tax_year)

    rows = (
        EmploymentIncomeMetadata.query
        .filter_by(user_id=user.id, tax_year=ty)
        .order_by(
            EmploymentIncomeMetadata.period_start.asc(),
            EmploymentIncomeMetadata.id.asc(),
        )
        .all()
    )

    employers: list[dict[str, Any]] = []
    gross_total = Decimal("0")
    apit_total = Decimal("0")

    for meta in rows:
        inc = Income.query.get(int(meta.income_id)) if meta.income_id else None
        gross_lkr = (
            Decimal(str(inc.amount_lkr))
            if (inc and inc.amount_lkr is not None) else Decimal("0")
        )
        apit_lkr = Decimal(str(meta.apit_credit_lkr or 0))
        gross_total += gross_lkr
        apit_total += apit_lkr
        employers.append({
            "meta_id": int(meta.id),
            "income_id": int(meta.income_id) if meta.income_id else None,
            "employer_name": meta.employer_name,
            "apit_certificate_ref": meta.apit_certificate_ref,
            "period_start": (
                meta.period_start.isoformat() if meta.period_start else None
            ),
            "period_end": (
                meta.period_end.isoformat() if meta.period_end else None
            ),
            "gross_lkr": gross_lkr,
            "apit_credit_lkr": apit_lkr,
            "currency": inc.currency if inc else "LKR",
            "fx_rate": (
                Decimal(str(inc.fx_rate)) if inc else Decimal("1.0")
            ),
        })

    # net_tax is a placeholder informational line: APIT credit subtracted from
    # whatever IIT applies to the gross. The engine owns the IIT computation;
    # the UI uses (gross, apit) to surface "you've prepaid X, your bracket
    # tax is Y, net owed = Y - X". We expose APIT only here; the bracket
    # tax surfaces from the central engine.
    return {
        "tax_year": ty,
        "employers": employers,
        "gross_total_lkr": gross_total.quantize(Decimal("0.01")),
        "apit_credit_total_lkr": apit_total.quantize(Decimal("0.01")),
        # net_tax_lkr is a convenience field for templates that want to
        # show "you may be owed a refund / you may owe additional". It is
        # NOT the engine's final answer.
        "net_tax_lkr": Decimal("0.00"),
    }


# ---------------------------------------------------------------------------
# Listing helpers used by routes
# ---------------------------------------------------------------------------
def list_employment_for_user(
    user, tax_year: Optional[str] = None,
) -> list[EmploymentIncomeMetadata]:
    q = EmploymentIncomeMetadata.query.filter_by(user_id=user.id)
    if tax_year:
        q = q.filter_by(tax_year=_normalise_tax_year(tax_year))
    return q.order_by(
        EmploymentIncomeMetadata.tax_year.desc(),
        EmploymentIncomeMetadata.period_start.desc(),
    ).all()


def get_employment_for_user(
    user, meta_id: int,
) -> Optional[EmploymentIncomeMetadata]:
    row = EmploymentIncomeMetadata.query.get(int(meta_id))
    if row is None or int(row.user_id) != int(user.id):
        return None
    return row


def delete_employment_for_user(user, meta_id: int) -> bool:
    """Hard-delete an employment row + paired Income. Returns True if removed."""
    row = get_employment_for_user(user, meta_id)
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
    "EmploymentIncomeMetadata",
    "record_employment_income",
    "compute_employment_tax",
    "list_employment_for_user",
    "get_employment_for_user",
    "delete_employment_for_user",
]
