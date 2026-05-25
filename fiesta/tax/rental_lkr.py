"""fiesta.tax.rental_lkr — G3.4 LKR Rental Income engine (MS4 W3c / Section G).

LKR-rental-income module for FIESTA. Brings local-currency rental earners
(landlords with SL real estate let to SL tenants) into the unified tax bill
without forcing them through the foreign-income-centric FIESTA shell.

Tax treatment (Sri Lanka):
  - IRA §7(2)(a) — investment income includes "rents" derived during the
    year of assessment. Verified 2026-05-25 via mcp__ira__get_section.
    Resident-on-resident rental income is included in the taxpayer's IIT
    base (no separate flat rate); it joins the progressive bracket
    schedule via the engine's ``rental_lkr`` bucket.
  - Allowable rental expenses (repairs, rates, depreciation, agent fees,
    mortgage interest, other) reduce the gross rent to a taxable net.
    The specific deduction sections (§11 / Chapter II Division III) were
    not retrievable at build time (IRA KG rate-limited 2026-05-25 — see
    TODO at the end of this docstring); the categories themselves are
    standard SL landlord practice and recorded for audit-pack provenance.
  - Foreign-source rental (rental_foreign in the canonical Income vocab)
    is OUT OF SCOPE for this module — the WHT/DTAA seam for foreign
    rental belongs to a Wave-X (G3.5 foreign half) dispatch. The Money
    block here is LKR-native and ``source_type='rental_lkr'`` is forced.

Persistence (Design Lock 2 §3-§4):
  - One ``RentalIncomeEntry`` row per property per tax year. Carries:
    property_address, tenant_name, period_start, period_end,
    source_country (always 'LK' for this LOCAL module), evidence_refs,
    and a back-FK to the paired Income row that holds gross rent Money.
  - The paired ``Income`` row holds the Money-flat columns (amount,
    currency, fx_rate=1.0, fx_source='lkr_native', fx_date, amount_lkr)
    per Design Lock 2 §4. No ORM bypass of the canonical Income table.
  - Many ``RentalDeductionEntry`` rows per ``RentalIncomeEntry`` (1:N).
    Each deduction carries a Money flat-column block + category +
    description + date_incurred.

Idempotency contract:
  - ``record_rental_income(user, property_address, gross_rent_money, ...)``
    twice produces ONE RentalIncomeEntry + ONE Income row. The natural
    key is (user_id, tax_year, property_address_lc, period_start) so a
    landlord with TWO tenants in the same property in the same year (a
    mid-tenancy switch) gets two distinct entries by the period_start
    discriminator. Same (user, year, address, period_start) → update.
  - ``record_rental_deduction`` is NOT idempotent on category alone (a
    landlord typically has multiple repairs / utility bills across a year
    in the same category). Use ``edit_rental_deduction`` /
    ``delete_rental_deduction`` for in-place changes.

Provenance: Inventory §G3.4 + Design Lock 2 §3/§4 + IRA §7(2)(a) (verified
2026-05-25 via mcp__ira__get_section).

TODO (IRA KG rate-limited 2026-05-25): cite the specific deduction
subsection(s) for rental expenses (§11 returned empty at build time; the
section number may differ in the consolidated 2025 Act). To be backfilled
when the IRA KG quota resets — search query: "rental income allowable
deductions repairs rates depreciation mortgage interest".
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Optional

from app import db

from fiesta.tax.credits import apply_foreign_tax_credit
from fiesta.tax.models import Income
from fiesta.tax.money import Money

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Locked vocabulary
# ---------------------------------------------------------------------------
DEDUCTION_CATEGORIES: tuple[str, ...] = (
    "repairs",
    "rates",
    "depreciation",
    "agent_fees",
    "mortgage_interest",
    "other",
)


# ---------------------------------------------------------------------------
# ORM models — RentalIncomeEntry (1:1 with Income) + RentalDeductionEntry (1:N)
# ---------------------------------------------------------------------------
class RentalIncomeEntry(db.Model):
    """One rental episode per (user, tax_year, property, period_start).

    The gross rent amount lives in the PAIRED Income row (Design Lock 2 §4 —
    Income is the canonical per-event ledger). This row carries rental-
    specific metadata that doesn't fit cleanly on Income: property_address,
    tenant_name, period_start, period_end, evidence_refs.

    period_start is part of the natural key so a mid-year tenant change in
    the same property gets two distinct entries.
    """

    __tablename__ = "rental_income_entries"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tax_year = db.Column(db.String(7), nullable=False, index=True)  # "2025/26"

    property_address = db.Column(db.String(256), nullable=False)
    tenant_name = db.Column(db.String(128), nullable=True)
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=True)
    # Always 'LK' for this LOCAL module; kept for forward-compat with the
    # Wave-X foreign-rental dispatch (which will set non-LK).
    source_country = db.Column(db.String(2), nullable=True, default="LK")

    # Back-link to the Income row this entry created. Nullable because the
    # Income row may be created in the same transaction or replaced on
    # update (the FK lives on Income.rental_income_id added in MG-003).
    income_id = db.Column(
        db.Integer,
        db.ForeignKey("incomes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    evidence_refs = db.Column(db.JSON, nullable=False, default=list)

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: __import__("datetime").datetime.utcnow(),
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: __import__("datetime").datetime.utcnow(),
        onupdate=lambda: __import__("datetime").datetime.utcnow(),
    )

    __table_args__ = (
        db.Index("ix_rental_income_entries_user_tax_year", "user_id", "tax_year"),
        # Idempotency anchor — (user_id, tax_year, property_address, period_start)
        # is the natural key. Enforced at the application layer via
        # _find_existing_entry; index speeds the lookup.
        db.Index(
            "ix_rental_income_entries_user_year_addr_start",
            "user_id", "tax_year", "property_address", "period_start",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RentalIncomeEntry id={self.id} user_id={self.user_id} "
            f"property={self.property_address!r} "
            f"tax_year={self.tax_year!r}>"
        )


class RentalDeductionEntry(db.Model):
    """One deductible expense incurred against a rental entry in a tax-year.

    Money flat columns mirror Income (Design Lock 2 §1) so LKR-native
    deductions are stored uniformly. The G3.4 module is LKR-only, so we
    expect currency='LKR' and fx_rate=1.0 always; the columns are kept
    Money-shaped for forward-compat with the Wave-X foreign-rental
    dispatch (foreign repair invoices in source-currency).
    """

    __tablename__ = "rental_deduction_entries"

    id = db.Column(db.Integer, primary_key=True)
    rental_income_id = db.Column(
        db.Integer,
        db.ForeignKey("rental_income_entries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Money flat columns
    amount = db.Column(db.Numeric(20, 4), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="LKR")
    fx_rate = db.Column(db.Numeric(20, 8), nullable=False, default=Decimal("1.0"))
    fx_source = db.Column(db.String(32), nullable=False, default="lkr_native")
    fx_date = db.Column(db.Date, nullable=False)
    amount_lkr = db.Column(db.Numeric(20, 2), nullable=False)

    category = db.Column(db.String(32), nullable=False, default="other")
    description = db.Column(db.String(512), nullable=True)
    date_incurred = db.Column(db.Date, nullable=False)

    evidence_refs = db.Column(db.JSON, nullable=False, default=list)

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: __import__("datetime").datetime.utcnow(),
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: __import__("datetime").datetime.utcnow(),
        onupdate=lambda: __import__("datetime").datetime.utcnow(),
    )

    __table_args__ = (
        db.Index(
            "ix_rental_deduction_entries_rental_date",
            "rental_income_id", "date_incurred",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RentalDeductionEntry id={self.id} "
            f"rental_income_id={self.rental_income_id} "
            f"category={self.category!r} amount_lkr={self.amount_lkr}>"
        )


# ---------------------------------------------------------------------------
# Tax-year derivation (SL Y/A runs 1 April → 31 March)
# ---------------------------------------------------------------------------
def _tax_year_for(d: date) -> str:
    """Return canonical 'YYYY/YY' tax-year string for date ``d``."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}/{str(start + 1)[2:]}"


def _normalise_tax_year(ty: str) -> str:
    """Normalise tax-year shape to the canonical 'YYYY/YY' form.

    Accepts: '2025/26', '2025-26', '2025/2026', 'YYYY/YY'.
    """
    s = (ty or "").strip().replace("-", "/")
    if "/" in s:
        head, tail = s.split("/", 1)
        if len(tail) == 4 and tail.startswith(head[:2]):
            return f"{head}/{tail[2:]}"
        return s
    return s


# ---------------------------------------------------------------------------
# Idempotent finder
# ---------------------------------------------------------------------------
def _find_existing_entry(
    user_id: int,
    tax_year: str,
    property_address: str,
    period_start: date,
) -> Optional[RentalIncomeEntry]:
    """Return the existing RentalIncomeEntry, or None.

    Natural key: (user_id, tax_year, property_address_lc, period_start).
    Case-insensitive on the address so '12 Main St' / '12 main st' don't
    duplicate.
    """
    if not property_address:
        return None
    try:
        rows = (
            RentalIncomeEntry.query
            .filter_by(
                user_id=user_id, tax_year=tax_year, period_start=period_start,
            )
            .all()
        )
    except Exception:  # pragma: no cover
        return None
    addr_lc = property_address.strip().lower()
    for r in rows:
        if (r.property_address or "").strip().lower() == addr_lc:
            return r
    return None


# ---------------------------------------------------------------------------
# Income recording — creates/updates RentalIncomeEntry + paired Income row
# ---------------------------------------------------------------------------
def record_rental_income(
    user,
    property_address: str,
    gross_rent_money: Money,
    tenant_name: Optional[str] = None,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    tax_year: Optional[str] = None,
    evidence_refs: Optional[list[dict[str, Any]]] = None,
) -> RentalIncomeEntry:
    """Record (or update) LKR rental income for (user, tax_year, property,
    period_start).

    Side effects (in one transaction):
      1. CREATE or UPDATE RentalIncomeEntry row.
      2. CREATE or UPDATE paired Income row with source_type='rental_lkr'.
      3. ADD 'rental_lkr' to ``user.income_sources`` if not already
         present (idempotent).

    Idempotency: same (user, tax_year, property_address, period_start) →
    same row id; gross_rent is overwritten on second call. Use
    ``record_rental_deduction`` for additive expense tracking.

    Args:
        user:                User ORM row (must have id + income_sources).
        property_address:    Free-text property address (1-256 chars).
                             Part of natural key — case-insensitive compare.
        gross_rent_money:    Money — gross rent for the period in LKR.
                             LOCAL module only — non-LKR raises ValueError.
        tenant_name:         Optional tenant name for audit trail.
        period_start:        Start of the rent period. If None, derived
                             from gross_rent_money.fx_date.
        period_end:          Optional end of the rent period.
        tax_year:            Canonical 'YYYY/YY'. If None, derived from
                             period_start (or gross_rent_money.fx_date).
        evidence_refs:       Optional list of evidence-ref dicts.

    Returns:
        The persisted RentalIncomeEntry row (id populated).
    """
    if user is None or getattr(user, "id", None) is None:
        raise ValueError("user with .id is required")
    if not property_address or not str(property_address).strip():
        raise ValueError("property_address is required")
    if gross_rent_money is None:
        raise ValueError("gross_rent_money is required")
    if gross_rent_money.amount is None or gross_rent_money.amount < 0:
        raise ValueError(
            f"gross_rent_money.amount must be >= 0; got {gross_rent_money.amount}"
        )

    currency = (gross_rent_money.currency or "LKR").upper()
    if currency != "LKR":
        # LOCAL module — foreign-currency rentals belong to the Wave-X
        # dispatch (rental_foreign) which adds the DTAA seam wiring.
        raise ValueError(
            f"rental_lkr engine only accepts LKR; got currency={currency!r}. "
            "Foreign-currency rental (rental_foreign) is Wave-X scope."
        )

    addr_clean = str(property_address).strip()[:256]
    tenant_clean = (tenant_name or "").strip()[:128] or None

    p_start = period_start or gross_rent_money.fx_date
    if period_end is not None and period_end < p_start:
        raise ValueError(
            f"period_end {period_end} cannot precede period_start {p_start}"
        )

    ty = _normalise_tax_year(tax_year) if tax_year else _tax_year_for(p_start)
    refs = list(evidence_refs or [])

    # ---- Idempotency check
    existing = _find_existing_entry(user.id, ty, addr_clean, p_start)

    if existing is not None:
        # ---- Update path ----
        existing.tenant_name = tenant_clean
        existing.period_end = period_end
        existing.source_country = "LK"
        if refs:
            existing.evidence_refs = refs

        inc = None
        if existing.income_id:
            inc = Income.query.get(int(existing.income_id))
        if inc is None:
            # Defensive: paired Income missing (manual DB cleanup).
            inc = _new_income_for(
                user_id=user.id,
                tax_year=ty,
                money=gross_rent_money,
                refs=_with_rental_ref(refs, existing.id),
            )
            inc.rental_income_id = existing.id
            db.session.add(inc)
            db.session.flush()
            existing.income_id = inc.id
        else:
            inc.source_type = "rental_lkr"
            inc.tax_year = ty
            inc.amount = gross_rent_money.amount
            inc.currency = "LKR"
            inc.fx_rate = gross_rent_money.fx_rate
            inc.fx_source = gross_rent_money.fx_source
            inc.fx_date = gross_rent_money.fx_date
            inc.amount_lkr = gross_rent_money.amount_lkr
            inc.source_country = "LK"
            inc.evidence_refs = _with_rental_ref(refs, existing.id)

        _add_income_source(user, "rental_lkr")
        db.session.commit()
        logger.info(
            "Rental income UPDATED: user=%s property=%r tax_year=%s "
            "period_start=%s gross_lkr=%s",
            user.id, addr_clean, ty, p_start, gross_rent_money.amount_lkr,
        )
        return existing

    # ---- Create path ----
    entry = RentalIncomeEntry(
        user_id=user.id,
        tax_year=ty,
        property_address=addr_clean,
        tenant_name=tenant_clean,
        period_start=p_start,
        period_end=period_end,
        source_country="LK",
        evidence_refs=refs,
    )
    db.session.add(entry)
    db.session.flush()  # populate entry.id for the Income FK below

    inc = _new_income_for(
        user_id=user.id,
        tax_year=ty,
        money=gross_rent_money,
        refs=_with_rental_ref(refs, entry.id),
    )
    inc.rental_income_id = entry.id
    db.session.add(inc)
    db.session.flush()
    entry.income_id = inc.id

    _add_income_source(user, "rental_lkr")
    db.session.commit()
    logger.info(
        "Rental income CREATED: user=%s property=%r tax_year=%s "
        "period_start=%s gross_lkr=%s entry_id=%s income_id=%s",
        user.id, addr_clean, ty, p_start, gross_rent_money.amount_lkr,
        entry.id, inc.id,
    )
    return entry


def _new_income_for(
    user_id: int,
    tax_year: str,
    money: Money,
    refs: list[dict[str, Any]],
) -> Income:
    """Build (do not add) an Income row from a Money for an LKR rental.

    source_type is hard-coded 'rental_lkr' (LOCAL module).
    source_country='LK' for the DTAA seam (no credit applied — the engine
    only routes foreign source through apply_foreign_tax_credit).
    """
    return Income(
        user_id=user_id,
        tax_year=tax_year,
        source_type="rental_lkr",
        amount=money.amount,
        currency="LKR",
        fx_rate=money.fx_rate,
        fx_source=money.fx_source,
        fx_date=money.fx_date,
        amount_lkr=money.amount_lkr,
        source_country="LK",
        evidence_refs=refs,
    )


def _with_rental_ref(
    refs: list[dict[str, Any]], entry_id: int,
) -> list[dict[str, Any]]:
    """Append a back-pointer to the RentalIncomeEntry, deduped."""
    out = list(refs or [])
    if not any(
        isinstance(r, dict)
        and r.get("type") == "rental_income_entry"
        and int(r.get("ref_id", -1)) == int(entry_id)
        for r in out
    ):
        out.append({"type": "rental_income_entry", "ref_id": int(entry_id)})
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
# Deduction recording — add / edit / remove
# ---------------------------------------------------------------------------
def record_rental_deduction(
    user,
    rental_income_id: int,
    category: str,
    amount_money: Money,
    description: Optional[str] = None,
    date_incurred: Optional[date] = None,
    evidence_refs: Optional[list[dict[str, Any]]] = None,
) -> RentalDeductionEntry:
    """Add one deductible expense to a RentalIncomeEntry.

    Categories: repairs / rates / depreciation / agent_fees /
    mortgage_interest / other. Unknown category → silently coerced to
    'other' (no exception — landlord UX prefers leniency).

    Args:
        user:                Used only for ownership check on
                             rental_income_id.
        rental_income_id:    The RentalIncomeEntry to attach to.
        category:            One of DEDUCTION_CATEGORIES; unknown → 'other'.
        amount_money:        Money — must be LKR (LOCAL module).
        description:         Optional free-text.
        date_incurred:       Optional; defaults to amount_money.fx_date.
        evidence_refs:       Optional evidence pointers.
    """
    entry = RentalIncomeEntry.query.get(int(rental_income_id))
    if entry is None:
        raise ValueError(f"RentalIncomeEntry {rental_income_id} not found")
    if user is not None and getattr(user, "id", None) is not None:
        if int(entry.user_id) != int(user.id):
            raise ValueError(
                f"RentalIncomeEntry {rental_income_id} not owned by user {user.id}"
            )
    if amount_money is None:
        raise ValueError("amount_money is required")
    if amount_money.amount is None or amount_money.amount < 0:
        raise ValueError(
            f"amount_money.amount must be >= 0; got {amount_money.amount}"
        )
    if (amount_money.currency or "LKR").upper() != "LKR":
        raise ValueError(
            f"rental_lkr deductions must be LKR; got currency={amount_money.currency!r}"
        )
    cat = (category or "other").strip().lower()
    if cat not in DEDUCTION_CATEGORIES:
        cat = "other"
    di = date_incurred or amount_money.fx_date

    row = RentalDeductionEntry(
        rental_income_id=entry.id,
        amount=amount_money.amount,
        currency="LKR",
        fx_rate=amount_money.fx_rate,
        fx_source=amount_money.fx_source,
        fx_date=amount_money.fx_date,
        amount_lkr=amount_money.amount_lkr,
        category=cat,
        description=(description or "")[:512] or None,
        date_incurred=di,
        evidence_refs=list(evidence_refs or []),
    )
    db.session.add(row)
    db.session.commit()
    logger.info(
        "Rental deduction added: rental_entry=%s category=%s amount_lkr=%s",
        entry.id, cat, amount_money.amount_lkr,
    )
    return row


def edit_rental_deduction(
    deduction_id: int,
    amount_money: Optional[Money] = None,
    category: Optional[str] = None,
    description: Optional[str] = None,
    date_incurred: Optional[date] = None,
) -> RentalDeductionEntry:
    """Edit one deduction in-place. Only provided fields are overwritten."""
    row = RentalDeductionEntry.query.get(int(deduction_id))
    if row is None:
        raise ValueError(f"RentalDeductionEntry {deduction_id} not found")
    if amount_money is not None:
        if amount_money.amount is None or amount_money.amount < 0:
            raise ValueError("amount_money.amount must be >= 0")
        if (amount_money.currency or "LKR").upper() != "LKR":
            raise ValueError("rental_lkr deductions must be LKR")
        row.amount = amount_money.amount
        row.currency = "LKR"
        row.fx_rate = amount_money.fx_rate
        row.fx_source = amount_money.fx_source
        row.fx_date = amount_money.fx_date
        row.amount_lkr = amount_money.amount_lkr
    if category is not None:
        cat = (category or "other").strip().lower()
        if cat not in DEDUCTION_CATEGORIES:
            cat = "other"
        row.category = cat
    if description is not None:
        row.description = (description or "")[:512] or None
    if date_incurred is not None:
        row.date_incurred = date_incurred
    db.session.commit()
    return row


def delete_rental_deduction(deduction_id: int) -> bool:
    """Hard-delete a deduction row. Returns True if deleted, False if missing."""
    row = RentalDeductionEntry.query.get(int(deduction_id))
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    return True


# ---------------------------------------------------------------------------
# Pure-function profit computation (does NOT touch DB)
# ---------------------------------------------------------------------------
def compute_rental_taxable_income(
    gross_rent_lkr: Decimal,
    deductions_lkr: Iterable[Decimal],
) -> Decimal:
    """Compute taxable rental income in LKR.

    Net = MAX(gross_rent − sum(deductions), 0). Caps at zero — a rental
    loss does not reduce other-source income at this layer (rental-loss
    treatment is Phase 2 — depends on IRA §22 / Chapter II Div III which
    couldn't be retrieved at build time, IRA KG rate-limited).

    Pure function — takes already-LKR Decimals. Callers wrap with a
    DB-reading helper (compute_rental_taxable_income_for_entry below).
    """
    gross = Decimal(str(gross_rent_lkr or 0))
    total_ded = sum(
        (Decimal(str(d or 0)) for d in deductions_lkr),
        Decimal("0"),
    )
    net = gross - total_ded
    return net.quantize(Decimal("0.01")) if net > 0 else Decimal("0.00")


def compute_rental_taxable_income_for_entry(
    entry: RentalIncomeEntry,
) -> Decimal:
    """Read gross + deductions from DB and return taxable rental income LKR."""
    if entry is None:
        return Decimal("0.00")
    inc = Income.query.get(int(entry.income_id)) if entry.income_id else None
    gross = (
        Decimal(str(inc.amount_lkr))
        if (inc and inc.amount_lkr is not None) else Decimal("0")
    )
    ded_rows = (
        RentalDeductionEntry.query
        .filter_by(rental_income_id=entry.id)
        .all()
    )
    ded_lkrs = [Decimal(str(r.amount_lkr or 0)) for r in ded_rows]
    return compute_rental_taxable_income(gross, ded_lkrs)


# ---------------------------------------------------------------------------
# Per-user aggregate signature mirroring compute_business_tax
# ---------------------------------------------------------------------------
def compute_rental_lkr_tax_year(
    user,
    tax_year: str,
) -> dict[str, Any]:
    """Compute the LKR rental-income tax-bill components for ``user`` in
    ``tax_year``.

    Returns dict:
        {
            "tax_year":                "2025/26",
            "rentals":                 [dict, …],
            "gross_total_lkr":         Decimal,
            "deductions_total_lkr":    Decimal,
            "taxable_income_total_lkr": Decimal,
            "lkr_taxable_income_lkr":  Decimal,  # for engine rental_lkr bucket
            "dtaa_credits":            [],       # always empty (LOCAL module)
            "dtaa_deferred":           False,    # LOCAL module — never True
        }

    The DTAA seam ``apply_foreign_tax_credit`` is called for every row
    that has a non-None source_country — for the LOCAL module that's
    always 'LK' so the stub short-circuits, but the call site exists
    for parity with the foreign-rental Wave-X handler.
    """
    ty = _normalise_tax_year(tax_year)

    entries = (
        RentalIncomeEntry.query
        .filter_by(user_id=user.id, tax_year=ty)
        .all()
    )

    rentals: list[dict[str, Any]] = []
    gross_total = Decimal("0")
    deduction_total = Decimal("0")
    net_total = Decimal("0")

    for entry in entries:
        inc = Income.query.get(int(entry.income_id)) if entry.income_id else None
        gross_lkr = (
            Decimal(str(inc.amount_lkr))
            if (inc and inc.amount_lkr is not None) else Decimal("0")
        )

        ded_rows = (
            RentalDeductionEntry.query
            .filter_by(rental_income_id=entry.id)
            .order_by(
                RentalDeductionEntry.date_incurred.asc(),
                RentalDeductionEntry.id.asc(),
            )
            .all()
        )
        deduction_dicts: list[dict[str, Any]] = []
        by_category: dict[str, Decimal] = {}
        entry_deduction_total = Decimal("0")
        for r in ded_rows:
            amt = Decimal(str(r.amount_lkr or 0))
            entry_deduction_total += amt
            by_category[r.category] = by_category.get(r.category, Decimal("0")) + amt
            deduction_dicts.append({
                "id": int(r.id),
                "category": r.category,
                "description": r.description,
                "date_incurred": (
                    r.date_incurred.isoformat() if r.date_incurred else None
                ),
                "amount_lkr": amt,
            })

        net = compute_rental_taxable_income(
            gross_lkr, [d["amount_lkr"] for d in deduction_dicts]
        )

        # DTAA seam — always called for parity with foreign-rental flow.
        # Stub no-ops for source_country='LK'.
        if inc is not None:
            apply_foreign_tax_credit(net, inc)

        gross_total += gross_lkr
        deduction_total += entry_deduction_total
        net_total += net

        rentals.append({
            "entry_id": int(entry.id),
            "income_id": int(entry.income_id) if entry.income_id else None,
            "property_address": entry.property_address,
            "tenant_name": entry.tenant_name,
            "period_start": (
                entry.period_start.isoformat() if entry.period_start else None
            ),
            "period_end": (
                entry.period_end.isoformat() if entry.period_end else None
            ),
            "source_country": entry.source_country or "LK",
            "gross_lkr": gross_lkr,
            "deductions_total_lkr": entry_deduction_total,
            "taxable_income_lkr": net,
            "deductions": deduction_dicts,
            "deductions_by_category_lkr": by_category,
        })

    return {
        "tax_year": ty,
        "rentals": rentals,
        "gross_total_lkr": gross_total.quantize(Decimal("0.01")),
        "deductions_total_lkr": deduction_total.quantize(Decimal("0.01")),
        "taxable_income_total_lkr": net_total.quantize(Decimal("0.01")),
        "lkr_taxable_income_lkr": net_total.quantize(Decimal("0.01")),
        "dtaa_credits": [],
        "dtaa_deferred": False,
    }


# ---------------------------------------------------------------------------
# Listing helpers used by routes
# ---------------------------------------------------------------------------
def list_rentals_for_user(
    user, tax_year: Optional[str] = None,
) -> list[RentalIncomeEntry]:
    """Return RentalIncomeEntry rows for a user. Optional tax_year filter."""
    q = RentalIncomeEntry.query.filter_by(user_id=user.id)
    if tax_year:
        q = q.filter_by(tax_year=_normalise_tax_year(tax_year))
    return q.order_by(
        RentalIncomeEntry.tax_year.desc(),
        RentalIncomeEntry.period_start.desc(),
        RentalIncomeEntry.created_at.desc(),
    ).all()


def get_rental_for_user(user, rental_entry_id: int) -> Optional[RentalIncomeEntry]:
    """Authoritative single-row lookup with ownership check."""
    row = RentalIncomeEntry.query.get(int(rental_entry_id))
    if row is None or int(row.user_id) != int(user.id):
        return None
    return row


__all__ = [
    "RentalIncomeEntry",
    "RentalDeductionEntry",
    "DEDUCTION_CATEGORIES",
    "record_rental_income",
    "record_rental_deduction",
    "edit_rental_deduction",
    "delete_rental_deduction",
    "compute_rental_taxable_income",
    "compute_rental_taxable_income_for_entry",
    "compute_rental_lkr_tax_year",
    "list_rentals_for_user",
    "get_rental_for_user",
]
