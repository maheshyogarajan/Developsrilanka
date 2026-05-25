"""fiesta.tax.business_income — B12 Business Income classifier (MS3 / Tier D6).

Full business-income module for FIESTA. Replaces the personal-foreign-income-only
limitation flagged in Inventory §B12 — expands TAM beyond freelancers to cover
sole-proprietors, consulting practices, and small businesses earning in LKR
OR a foreign currency.

Tax treatment (Sri Lanka):
  - IRA §6 — "A person's income from a business for a year of assessment shall
    be the person's gains and profits from conducting the business for the
    year." Includes service fees, trading-stock consideration, gains from
    realisation of business assets (Chapter IV), depreciation recapture,
    business gifts, and effectively-connected investment income.
  - IRA §10-§19 (TODO — pending IRA KG verification; rate-limited at build
    time 2026-05-25) — deductible business expenses: rent, utilities,
    depreciation (Fourth Schedule), employee salaries + APIT remitted,
    professional fees + §85 WHT, bank charges, repairs, advertising,
    insurance premiums, etc. Caller passes already-categorised expenses;
    this module does NOT enforce specific deduction rules (Phase 2 work).
  - Taxable profit = gross receipts − deductible expenses. Returned in LKR.
  - For sole-prop / partnership share: taxed at the personal IIT bracket
    rates (engine wires this through the existing compute_tax_25_26 path).
    For incorporated (separate legal entity): corporate rates apply — out
    of scope for FIESTA's personal-tax-return product.
  - Foreign-source business income: same SL liability computation, with
    DTAA seam invoked via apply_foreign_tax_credit(...) for future credit.

Persistence (Design Lock 2 §3-§4 + Section G G3.3 forward-compat):
  - One BusinessIncomeEntry row per business per tax-year (metadata +
    relationship to Income). Carries: business_name, business_type,
    source_country, evidence_refs.
  - One PAIRED Income row created with source_type='business_lkr' OR
    'business_foreign' depending on currency. Income row holds the
    Money-flat columns (amount, currency, fx_rate, fx_source, fx_date,
    amount_lkr) per Design Lock 2 §4. No separate BusinessIncome ORM
    bypassing the canonical Income table.
  - Many BusinessExpenseEntry rows per BusinessIncomeEntry (1:N). Each
    expense is a Money flat-column block + category + description.

LKR + foreign currency support from day 1 (Section G G3.3 reuse target):
  - Currency='LKR' with fx_rate=1.0 + fx_source='lkr_native' is the
    LKR-native code path (no FX conversion).
  - Any other currency with fx_rate to LKR triggers the foreign-source
    code path (DTAA seam, source_country tagging, dual-track foreign
    bucket for 25/26 engine).
  - The same code path handles both — no special-casing required for
    G3.3 to drop in foreign FD / dividend / equity income downstream.

Idempotency contract:
  - record_business_income(user, business_name='Acme', tax_year='2025/26', ...)
    twice produces ONE BusinessIncomeEntry + ONE Income row. Second call
    UPDATES gross_receipts (Money + Income) but keeps the row id stable.
  - add_business_expense / edit_business_expense are NOT idempotent on
    description alone (multiple expenses per category are common —
    "Electricity — January", "Electricity — February", ...).

Provenance: Inventory §B12 + Design Lock 2 §1/§3/§4/§6 + IRA §6
(verified 2026-05-25 via mcp__ira__get_section).
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
BUSINESS_TYPES: tuple[str, ...] = (
    "sole_prop",
    "incorporated",
    "partnership",
)

EXPENSE_CATEGORIES: tuple[str, ...] = (
    "rent",
    "utilities",
    "salaries",
    "professional_fees",
    "equipment",
    "depreciation",
    "bank_charges",
    "repairs",
    "advertising",
    "insurance",
    "other",
)


# ---------------------------------------------------------------------------
# ORM models — BusinessIncomeEntry (1:1 with Income) + BusinessExpenseEntry (1:N)
# ---------------------------------------------------------------------------
class BusinessIncomeEntry(db.Model):
    """One business per (user, tax_year). Metadata + relationship to Income.

    The income amount lives in the PAIRED Income row (Design Lock 2 §4 —
    Income is the canonical per-event ledger). This row carries
    business-specific metadata that doesn't fit cleanly on Income:
    business_name, business_type, source_country, evidence_refs.
    """

    __tablename__ = "business_income_entries"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tax_year = db.Column(db.String(7), nullable=False, index=True)  # "2025/26"

    business_name = db.Column(db.String(128), nullable=False)
    business_type = db.Column(db.String(16), nullable=False, default="sole_prop")
    source_country = db.Column(db.String(2), nullable=True)  # ISO-3166-1 alpha-2

    # Back-link to the Income row this entry created. Nullable because the
    # Income row may be created in the same transaction or replaced on update
    # (the FK lives on Income.business_income_id added in M3-001).
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
        db.Index("ix_business_income_entries_user_tax_year", "user_id", "tax_year"),
        # Idempotency anchor — (user_id, tax_year, business_name) is the
        # natural key. Enforced at the application layer via
        # _find_existing_entry; index speeds the lookup.
        db.Index(
            "ix_business_income_entries_user_tax_year_name",
            "user_id", "tax_year", "business_name",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<BusinessIncomeEntry id={self.id} user_id={self.user_id} "
            f"name={self.business_name!r} type={self.business_type!r} "
            f"tax_year={self.tax_year!r}>"
        )


class BusinessExpenseEntry(db.Model):
    """One deductible expense incurred against a business in a tax-year.

    Money flat columns mirror Income/AssetDisposal (Design Lock 2 §1) so
    LKR + foreign-currency expenses are supported uniformly. Expense
    incurred in a different currency to the business's gross receipts is
    allowed — both get FX-converted to LKR.
    """

    __tablename__ = "business_expense_entries"

    id = db.Column(db.Integer, primary_key=True)
    business_income_id = db.Column(
        db.Integer,
        db.ForeignKey("business_income_entries.id", ondelete="CASCADE"),
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
            "ix_business_expense_entries_business_id_date",
            "business_income_id", "date_incurred",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<BusinessExpenseEntry id={self.id} "
            f"business_income_id={self.business_income_id} "
            f"category={self.category!r} amount_lkr={self.amount_lkr}>"
        )


# ---------------------------------------------------------------------------
# Tax-year derivation (SL Y/A runs 1 April → 31 March)
# ---------------------------------------------------------------------------
def _tax_year_for(d: date) -> str:
    """Return canonical 'YYYY/YY' tax-year string for date ``d``.

    Matches the helper in rsu_engine + nrr_classifier so business entries
    live alongside the other canonical-ledger rows.
    """
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}/{str(start + 1)[2:]}"


def _normalise_tax_year(ty: str) -> str:
    """Normalise tax-year shape to the canonical 'YYYY/YY' form.

    Accepts: '2025/26', '2025-26', '2025/2026', 'YYYY/YY'. Internal DB rows
    always use 'YYYY/YY'.
    """
    s = (ty or "").strip().replace("-", "/")
    if "/" in s:
        head, tail = s.split("/", 1)
        if len(tail) == 4 and tail.startswith(head[:2]):
            return f"{head}/{tail[2:]}"
        return s
    return s


def _income_source_type_for_currency(currency: str) -> str:
    """Map currency → Income.source_type per Design Lock 2 §3 vocabulary.

    LKR → 'business_lkr'. Any other currency → 'business_foreign'. The
    distinction matters for the 25/26 dual-track engine (foreign business
    income hits the foreign 15% cap bucket; LKR business income joins the
    progressive local bracket schedule).
    """
    return "business_lkr" if (currency or "").upper() == "LKR" else "business_foreign"


# ---------------------------------------------------------------------------
# Idempotent finder (natural key = user_id + tax_year + business_name)
# ---------------------------------------------------------------------------
def _find_existing_entry(
    user_id: int,
    tax_year: str,
    business_name: str,
) -> Optional[BusinessIncomeEntry]:
    """Return the existing BusinessIncomeEntry for this (user, year, name),
    or None. Case-insensitive name compare prevents 'Acme' / 'acme'
    accidental duplication.
    """
    if not business_name:
        return None
    try:
        rows = (
            BusinessIncomeEntry.query
            .filter_by(user_id=user_id, tax_year=tax_year)
            .all()
        )
    except Exception:  # pragma: no cover
        return None
    name_lc = business_name.strip().lower()
    for r in rows:
        if (r.business_name or "").strip().lower() == name_lc:
            return r
    return None


# ---------------------------------------------------------------------------
# Income recording — creates/updates BusinessIncomeEntry + paired Income row
# ---------------------------------------------------------------------------
def record_business_income(
    user,
    gross_receipts_money: Money,
    business_name: str,
    business_type: str = "sole_prop",
    source_country: Optional[str] = None,
    tax_year: Optional[str] = None,
    evidence_refs: Optional[list[dict[str, Any]]] = None,
) -> BusinessIncomeEntry:
    """Record (or update) business income for a (user, tax_year, business_name).

    Side effects (in one transaction):
      1. CREATE or UPDATE BusinessIncomeEntry row.
      2. CREATE or UPDATE paired Income row with source_type='business_lkr'
         OR 'business_foreign' depending on currency.
      3. ADD 'business_lkr' or 'business_foreign' to
         ``user.income_sources`` if not already present (idempotent).

    Idempotency: same (user, tax_year, business_name) → same row id;
    gross_receipts is overwritten on second call. Use add_business_expense
    + edit_business_expense for additive expense tracking.

    Args:
        user:                  User ORM row (must have id + income_sources).
        gross_receipts_money:  Money — gross business receipts in source
                               currency. Carries currency, fx_rate, fx_source,
                               fx_date. amount_lkr is derived.
        business_name:         Free-text name of the business (1-128 chars).
                               Natural key — case-insensitive.
        business_type:         One of BUSINESS_TYPES — default 'sole_prop'.
                               'incorporated' is recorded but FIESTA's personal
                               IIT engine treats it the same; corporate-rate
                               handling is out of scope for v1.
        source_country:        ISO-3166-1 alpha-2 — None for LKR-source.
                               Tagging is automatic for foreign currency but
                               can be overridden (e.g., LKR-billed work for a
                               Singapore client → source_country='SG').
        tax_year:              Canonical 'YYYY/YY'. If None, derived from
                               gross_receipts_money.fx_date.
        evidence_refs:         Optional list of evidence-ref dicts.

    Returns:
        The persisted BusinessIncomeEntry row (id populated).
    """
    if user is None or getattr(user, "id", None) is None:
        raise ValueError("user with .id is required")
    if not business_name or not str(business_name).strip():
        raise ValueError("business_name is required")
    if gross_receipts_money is None:
        raise ValueError("gross_receipts_money is required")
    if gross_receipts_money.amount is None or gross_receipts_money.amount < 0:
        raise ValueError(
            f"gross_receipts_money.amount must be >= 0; got {gross_receipts_money.amount}"
        )

    bt = (business_type or "sole_prop").strip().lower()
    if bt not in BUSINESS_TYPES:
        raise ValueError(
            f"business_type must be one of {BUSINESS_TYPES}; got {business_type!r}"
        )

    ty = _normalise_tax_year(tax_year) if tax_year else _tax_year_for(
        gross_receipts_money.fx_date
    )
    name_clean = str(business_name).strip()[:128]
    currency = (gross_receipts_money.currency or "LKR").upper()
    source_type = _income_source_type_for_currency(currency)

    # Source country defaults: LKR → None unless caller overrides; foreign →
    # caller-provided OR None (caller should ALWAYS set it for foreign income,
    # but we don't reject if missing — the DTAA seam handles None gracefully).
    src_country = source_country
    if src_country is None and currency != "LKR":
        # Forward-compat: foreign currency without explicit country is allowed
        # but the DTAA stub will short-circuit. Logged at warning level so the
        # UI can surface a "specify source country" nudge.
        logger.warning(
            "Foreign-currency business income without source_country: "
            "user=%s business=%r currency=%s",
            user.id, name_clean, currency,
        )

    refs = list(evidence_refs or [])

    # ---- Idempotency check
    existing = _find_existing_entry(user.id, ty, name_clean)

    if existing is not None:
        # ---- Update path ----
        existing.business_type = bt
        existing.source_country = src_country
        if refs:
            existing.evidence_refs = refs

        # Update paired Income row.
        inc = None
        if existing.income_id:
            inc = Income.query.get(int(existing.income_id))
        if inc is None:
            # Defensive: paired Income missing (e.g., manual DB cleanup).
            # Recreate it.
            inc = _new_income_for(
                user_id=user.id,
                tax_year=ty,
                source_type=source_type,
                money=gross_receipts_money,
                source_country=src_country,
                refs=_with_business_ref(refs, existing.id),
            )
            inc.business_income_id = existing.id
            db.session.add(inc)
            db.session.flush()
            existing.income_id = inc.id
        else:
            inc.source_type = source_type
            inc.tax_year = ty
            inc.amount = gross_receipts_money.amount
            inc.currency = currency
            inc.fx_rate = gross_receipts_money.fx_rate
            inc.fx_source = gross_receipts_money.fx_source
            inc.fx_date = gross_receipts_money.fx_date
            inc.amount_lkr = gross_receipts_money.amount_lkr
            inc.source_country = src_country
            inc.evidence_refs = _with_business_ref(refs, existing.id)
            # JSON column re-assignment is required for SQLAlchemy to detect
            # the change on mutable types.

        _add_income_source(user, source_type)
        db.session.commit()
        logger.info(
            "Business income UPDATED: user=%s business=%r tax_year=%s "
            "source_type=%s gross_lkr=%s",
            user.id, name_clean, ty, source_type, gross_receipts_money.amount_lkr,
        )
        return existing

    # ---- Create path ----
    entry = BusinessIncomeEntry(
        user_id=user.id,
        tax_year=ty,
        business_name=name_clean,
        business_type=bt,
        source_country=src_country,
        evidence_refs=refs,
    )
    db.session.add(entry)
    db.session.flush()  # populate entry.id for the Income FK below

    inc = _new_income_for(
        user_id=user.id,
        tax_year=ty,
        source_type=source_type,
        money=gross_receipts_money,
        source_country=src_country,
        refs=_with_business_ref(refs, entry.id),
    )
    inc.business_income_id = entry.id
    db.session.add(inc)
    db.session.flush()
    entry.income_id = inc.id

    _add_income_source(user, source_type)
    db.session.commit()
    logger.info(
        "Business income CREATED: user=%s business=%r tax_year=%s "
        "source_type=%s gross_lkr=%s entry_id=%s income_id=%s",
        user.id, name_clean, ty, source_type, gross_receipts_money.amount_lkr,
        entry.id, inc.id,
    )
    return entry


def _new_income_for(
    user_id: int,
    tax_year: str,
    source_type: str,
    money: Money,
    source_country: Optional[str],
    refs: list[dict[str, Any]],
) -> Income:
    """Build (do not add) an Income row from a Money + metadata."""
    return Income(
        user_id=user_id,
        tax_year=tax_year,
        source_type=source_type,
        amount=money.amount,
        currency=(money.currency or "LKR").upper(),
        fx_rate=money.fx_rate,
        fx_source=money.fx_source,
        fx_date=money.fx_date,
        amount_lkr=money.amount_lkr,
        source_country=source_country,
        evidence_refs=refs,
    )


def _with_business_ref(
    refs: list[dict[str, Any]], entry_id: int,
) -> list[dict[str, Any]]:
    """Append a back-pointer to the BusinessIncomeEntry, deduped."""
    out = list(refs or [])
    if not any(
        isinstance(r, dict)
        and r.get("type") == "business_income_entry"
        and int(r.get("ref_id", -1)) == int(entry_id)
        for r in out
    ):
        out.append({"type": "business_income_entry", "ref_id": int(entry_id)})
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
# Expense recording — add / edit / remove
# ---------------------------------------------------------------------------
def add_business_expense(
    business_entry_id: int,
    expense_money: Money,
    category: str,
    description: Optional[str] = None,
    date_incurred: Optional[date] = None,
    evidence_refs: Optional[list[dict[str, Any]]] = None,
) -> BusinessExpenseEntry:
    """Add one deductible expense to a BusinessIncomeEntry."""
    entry = BusinessIncomeEntry.query.get(int(business_entry_id))
    if entry is None:
        raise ValueError(f"BusinessIncomeEntry {business_entry_id} not found")
    if expense_money is None:
        raise ValueError("expense_money is required")
    if expense_money.amount is None or expense_money.amount < 0:
        raise ValueError(
            f"expense_money.amount must be >= 0; got {expense_money.amount}"
        )
    cat = (category or "other").strip().lower()
    if cat not in EXPENSE_CATEGORIES:
        cat = "other"
    di = date_incurred or expense_money.fx_date

    row = BusinessExpenseEntry(
        business_income_id=entry.id,
        amount=expense_money.amount,
        currency=(expense_money.currency or "LKR").upper(),
        fx_rate=expense_money.fx_rate,
        fx_source=expense_money.fx_source,
        fx_date=expense_money.fx_date,
        amount_lkr=expense_money.amount_lkr,
        category=cat,
        description=(description or "")[:512] or None,
        date_incurred=di,
        evidence_refs=list(evidence_refs or []),
    )
    db.session.add(row)
    db.session.commit()
    logger.info(
        "Business expense added: business_entry=%s category=%s amount_lkr=%s",
        entry.id, cat, expense_money.amount_lkr,
    )
    return row


def edit_business_expense(
    expense_id: int,
    expense_money: Optional[Money] = None,
    category: Optional[str] = None,
    description: Optional[str] = None,
    date_incurred: Optional[date] = None,
) -> BusinessExpenseEntry:
    """Edit one expense in-place. Only provided fields are overwritten."""
    row = BusinessExpenseEntry.query.get(int(expense_id))
    if row is None:
        raise ValueError(f"BusinessExpenseEntry {expense_id} not found")
    if expense_money is not None:
        if expense_money.amount is None or expense_money.amount < 0:
            raise ValueError("expense_money.amount must be >= 0")
        row.amount = expense_money.amount
        row.currency = (expense_money.currency or "LKR").upper()
        row.fx_rate = expense_money.fx_rate
        row.fx_source = expense_money.fx_source
        row.fx_date = expense_money.fx_date
        row.amount_lkr = expense_money.amount_lkr
    if category is not None:
        cat = (category or "other").strip().lower()
        if cat not in EXPENSE_CATEGORIES:
            cat = "other"
        row.category = cat
    if description is not None:
        row.description = (description or "")[:512] or None
    if date_incurred is not None:
        row.date_incurred = date_incurred
    db.session.commit()
    return row


def delete_business_expense(expense_id: int) -> bool:
    """Hard-delete an expense row. Returns True if deleted, False if missing."""
    row = BusinessExpenseEntry.query.get(int(expense_id))
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    return True


# ---------------------------------------------------------------------------
# Pure-function profit computation (does NOT touch DB)
# ---------------------------------------------------------------------------
def compute_business_taxable_profit(
    gross_receipts_lkr: Decimal,
    expenses_lkr: Iterable[Decimal],
) -> Decimal:
    """Compute taxable business profit in LKR.

    Profit = MAX(gross − sum(expenses), 0). Caps at zero — a business loss
    does not reduce other-source income at this layer (loss-carry-forward
    is Phase 2). The cap means the engine never sees a negative business
    contribution from this module.

    Pure function — takes already-LKR-converted Decimals. Callers wrap with
    a DB-reading helper (compute_business_taxable_profit_for_entry below).
    """
    gross = Decimal(str(gross_receipts_lkr or 0))
    total_exp = sum((Decimal(str(e or 0)) for e in expenses_lkr), Decimal("0"))
    profit = gross - total_exp
    return profit.quantize(Decimal("0.01")) if profit > 0 else Decimal("0.00")


def compute_business_taxable_profit_for_entry(
    entry: BusinessIncomeEntry,
) -> Decimal:
    """Read gross + expenses from DB and return taxable profit in LKR."""
    if entry is None:
        return Decimal("0.00")
    inc = Income.query.get(int(entry.income_id)) if entry.income_id else None
    gross = Decimal(str(inc.amount_lkr)) if (inc and inc.amount_lkr is not None) else Decimal("0")
    exp_rows = (
        BusinessExpenseEntry.query
        .filter_by(business_income_id=entry.id)
        .all()
    )
    expense_lkrs = [Decimal(str(r.amount_lkr or 0)) for r in exp_rows]
    return compute_business_taxable_profit(gross, expense_lkrs)


# ---------------------------------------------------------------------------
# Aggregate computation for tax-engine + tax-bill integration
# ---------------------------------------------------------------------------
def compute_business_tax(user, tax_year: str) -> dict[str, Any]:
    """Compute the business-income tax-bill components for ``user`` in ``tax_year``.

    Returns dict:
        {
            "tax_year":               "2025/26",
            "businesses":             [dict, …],
            "gross_total_lkr":        Decimal,
            "expenses_total_lkr":     Decimal,
            "taxable_profit_total_lkr": Decimal,
            "lkr_taxable_profit_lkr": Decimal,  # for engine local_lkr bucket
            "foreign_taxable_profit_lkr": Decimal,  # for engine foreign bucket
            "dtaa_credits":           [ForeignTaxCredit, …],  # empty pre-Wave-X
            "dtaa_deferred":          bool,
        }

    For every foreign-source row, calls ``apply_foreign_tax_credit(...)`` —
    the call site IS the seam Wave-X drops into without rework.
    """
    ty = _normalise_tax_year(tax_year)

    entries = (
        BusinessIncomeEntry.query
        .filter_by(user_id=user.id, tax_year=ty)
        .all()
    )

    businesses: list[dict[str, Any]] = []
    gross_total = Decimal("0")
    expense_total = Decimal("0")
    profit_total = Decimal("0")
    lkr_profit = Decimal("0")
    foreign_profit = Decimal("0")
    dtaa_credits: list[Any] = []
    has_foreign = False

    for entry in entries:
        inc = Income.query.get(int(entry.income_id)) if entry.income_id else None
        gross_lkr = Decimal(str(inc.amount_lkr)) if (inc and inc.amount_lkr is not None) else Decimal("0")

        exp_rows = (
            BusinessExpenseEntry.query
            .filter_by(business_income_id=entry.id)
            .order_by(BusinessExpenseEntry.date_incurred.asc(),
                      BusinessExpenseEntry.id.asc())
            .all()
        )
        expense_dicts: list[dict[str, Any]] = []
        by_category: dict[str, Decimal] = {}
        entry_expense_total = Decimal("0")
        for r in exp_rows:
            amt = Decimal(str(r.amount_lkr or 0))
            entry_expense_total += amt
            by_category[r.category] = by_category.get(r.category, Decimal("0")) + amt
            expense_dicts.append({
                "id": int(r.id),
                "category": r.category,
                "description": r.description,
                "date_incurred": r.date_incurred.isoformat() if r.date_incurred else None,
                "amount_lkr": amt,
                "currency": r.currency,
                "fx_rate": Decimal(str(r.fx_rate)),
            })

        profit = compute_business_taxable_profit(gross_lkr, [d["amount_lkr"] for d in expense_dicts])

        is_foreign = bool(inc and inc.source_type == "business_foreign")
        if is_foreign:
            has_foreign = True
            foreign_profit += profit
            # Call the DTAA seam for every foreign row — Wave-X drop-in point.
            _net, ftc = apply_foreign_tax_credit(profit, inc)
            if ftc is not None:
                dtaa_credits.append(ftc)
        else:
            lkr_profit += profit

        gross_total += gross_lkr
        expense_total += entry_expense_total
        profit_total += profit

        businesses.append({
            "entry_id": int(entry.id),
            "income_id": int(entry.income_id) if entry.income_id else None,
            "business_name": entry.business_name,
            "business_type": entry.business_type,
            "source_country": entry.source_country,
            "is_foreign": is_foreign,
            "currency": inc.currency if inc else None,
            "fx_rate": Decimal(str(inc.fx_rate)) if inc else None,
            "gross_lkr": gross_lkr,
            "expenses_total_lkr": entry_expense_total,
            "taxable_profit_lkr": profit,
            "expenses": expense_dicts,
            "expenses_by_category_lkr": by_category,
        })

    return {
        "tax_year": ty,
        "businesses": businesses,
        "gross_total_lkr": gross_total.quantize(Decimal("0.01")),
        "expenses_total_lkr": expense_total.quantize(Decimal("0.01")),
        "taxable_profit_total_lkr": profit_total.quantize(Decimal("0.01")),
        "lkr_taxable_profit_lkr": lkr_profit.quantize(Decimal("0.01")),
        "foreign_taxable_profit_lkr": foreign_profit.quantize(Decimal("0.01")),
        "dtaa_credits": dtaa_credits,
        # Banner flag: True iff there's at least one foreign-source business.
        "dtaa_deferred": has_foreign,
    }


# ---------------------------------------------------------------------------
# Listing helpers used by routes
# ---------------------------------------------------------------------------
def list_businesses_for_user(
    user, tax_year: Optional[str] = None,
) -> list[BusinessIncomeEntry]:
    """Return BusinessIncomeEntry rows for a user. Optional tax_year filter."""
    q = BusinessIncomeEntry.query.filter_by(user_id=user.id)
    if tax_year:
        q = q.filter_by(tax_year=_normalise_tax_year(tax_year))
    return q.order_by(
        BusinessIncomeEntry.tax_year.desc(),
        BusinessIncomeEntry.created_at.desc(),
    ).all()


def get_business_for_user(user, business_entry_id: int) -> Optional[BusinessIncomeEntry]:
    """Authoritative single-row lookup with ownership check.

    Returns None if missing OR if user doesn't own it (callers should abort 404).
    """
    row = BusinessIncomeEntry.query.get(int(business_entry_id))
    if row is None or int(row.user_id) != int(user.id):
        return None
    return row


__all__ = [
    "BusinessIncomeEntry",
    "BusinessExpenseEntry",
    "BUSINESS_TYPES",
    "EXPENSE_CATEGORIES",
    "record_business_income",
    "add_business_expense",
    "edit_business_expense",
    "delete_business_expense",
    "compute_business_taxable_profit",
    "compute_business_taxable_profit_for_entry",
    "compute_business_tax",
    "list_businesses_for_user",
    "get_business_for_user",
]
