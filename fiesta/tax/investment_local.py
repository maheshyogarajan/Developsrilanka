"""fiesta.tax.investment_local — G3.5 LOCAL Investment Income engine (MS4 W3c).

LKR investment-income module for FIESTA. Brings local-currency investment
earners (FD-holders, equity dividend recipients, local real-estate / equity /
unit-trust disposers) into the unified tax bill.

Scope (LOCAL only):
  - Local FD interest (Sri Lankan bank Fixed Deposits, WHT withheld at
    source)
  - Local dividends (equity dividends from Sri Lankan companies, WHT
    withheld at source)
  - Local capital gains (real estate, equity, unit trusts) — uses the
    canonical ``AssetDisposal`` model with ``asset_type`` in
    {'real_estate','equity','unit_trust'} and source_country='LK'

Foreign-source counterparts (foreign FD, foreign dividend, foreign equity
CGT) are OUT OF SCOPE — they belong to a Wave-X dispatch that wires the
DTAA seam. The Wave-X stub ``apply_foreign_tax_credit(...)`` IS called
for parity at every aggregation site, but for LOCAL rows the
source_country='LK' so the stub no-ops (no treaty lookup attempted).

Tax treatment (Sri Lanka):
  - IRA §7(2)(a) — investment income includes "dividends, interest,
    discounts, charges, annuities, natural resource payments, rents,
    premiums and royalties". Verified 2026-05-25 via mcp__ira__get_section.
    Local FD interest + local dividends are included in the IIT base
    via the engine's ``investment_lkr`` bucket.
  - IRA §36 + §37 — capital gains on realisation of investment assets
    (cost basis = expenditure incurred + incidental costs; gain = sum
    of consideration received − cost). Verified 2026-05-25 via
    mcp__ira__get_section. Local real-estate / equity / unit-trust
    disposals create AssetDisposal rows the engine treats as CGT base.
  - Loss carry-forward: §36(2) defines loss; carry-forward rules
    (Chapter IV §38-§40) couldn't be retrieved at build time (IRA KG
    rate-limited 2026-05-25). This module mirrors B13's pattern —
    indefinite carry-forward applied; the engine's Phase-3 will impose
    the actual cap (TODO at end of docstring).
  - WHT credits: FD interest in the SL banking system has WHT
    withheld at source (current resident-individual rate not retrievable
    at build time — IRA KG rate-limited; the helper takes the
    withheld amount AS-RECORDED rather than computing it, so the engine
    doesn't have to know the rate). Same pattern for dividend WHT.
  - WHT-credit application: at aggregate time, WHT withheld on local
    FD + dividend rows is summed and emitted as ``investment_lkr_wht_credit_lkr``
    in the result dict so the engine's compute layer can subtract it
    from final liability (placeholder hook for Phase-3 engine wiring).

Persistence (Design Lock 2 §3-§5):
  - ``LocalFDInterest`` rows hold FD-specific metadata + back-FK to a
    paired Income row (source_type='investment_lkr', currency=LKR,
    source_country='LK'). The Income row holds the gross interest amount.
  - ``LocalDividendIncome`` rows hold dividend-specific metadata + back-FK
    to a paired Income row (same source_type).
  - Local capital gains use the CANONICAL ``AssetDisposal`` model
    (Design Lock 2 §5/§8 — no parallel CryptoDisposal-style table).
    asset_type in {'real_estate','equity','unit_trust'} discriminates;
    source_country='LK' for LOCAL.

Loss carry-forward generalisation:
  - B13 crypto's ``_compute_loss_carry_forward`` filters on
    ``asset_type='crypto'`` only. This module ships a generalised helper
    ``_compute_loss_carry_forward_for_asset_types(...)`` that accepts a
    tuple of asset_types and sums net-loss carry-forward across them.
    The G3.5 LOCAL aggregation calls it with
    ('real_estate','equity','unit_trust','fd').
  - The original B13 helper stays untouched (per W3c constraint).

Idempotency contract:
  - ``record_fd_interest(user, bank_name, principal_money, interest_money,
    wht_money, year, fd_account_ref)`` twice with the same fd_account_ref
    + tax_year produces ONE LocalFDInterest row + ONE Income row. Second
    call UPDATES the principal/interest/wht amounts. fd_account_ref is
    the natural key (a FD has a unique deposit certificate reference).
  - ``record_dividend(user, company_name, dividend_money, wht_money,
    ex_dividend_date)`` natural key is (user, ex_dividend_date,
    company_name_lc, tax_year) — a single company paying multiple
    interim dividends in a year gets multiple rows by ex_dividend_date.
  - Local CGT (AssetDisposal) — each disposal event is unique; no
    idempotency dedup. Caller is responsible for not creating duplicate
    disposal rows.

Provenance: Inventory §G3.5 (LOCAL half) + Design Lock 2 §3/§4/§5/§6/§8 +
IRA §7(2)(a) + §36 + §37 (verified 2026-05-25 via mcp__ira__get_section).

TODOs (IRA KG rate-limited 2026-05-25):
  - Confirm current resident-individual FD interest WHT rate (commonly
    cited as 5% for individuals but the consolidated 2025 Act subsection
    couldn't be retrieved). The engine doesn't need the rate — callers
    pass the withheld amount.
  - Confirm dividend WHT rate (commonly 15%) and any exemptions.
  - Confirm CGT rate(s) for local real-estate / equity / unit-trust
    disposals — these go via the engine's ``investment_lkr`` bucket
    in Phase 1 and will be split into a dedicated CGT bucket in
    Phase 3.
  - Confirm carry-forward cap for non-crypto investment losses (B13
    crypto v1.0 also has this TODO).
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Optional

from app import db

from fiesta.tax.credits import apply_foreign_tax_credit
from fiesta.tax.models import AssetDisposal, Income
from fiesta.tax.money import Money

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Locked vocabulary
# ---------------------------------------------------------------------------
LOCAL_CGT_ASSET_TYPES: tuple[str, ...] = (
    "real_estate",
    "equity",
    "unit_trust",
)

# Asset-types that pool together for the G3.5 LOCAL carry-forward sum.
# 'fd' is included for completeness (FDs typically don't realise losses
# but the AssetDisposal seam permits it for early-redemption penalty
# scenarios in a future iteration).
_LOCAL_CARRY_ASSET_TYPES: tuple[str, ...] = (
    "real_estate",
    "equity",
    "unit_trust",
    "fd",
)


# ---------------------------------------------------------------------------
# ORM models — LocalFDInterest + LocalDividendIncome (Income paired)
# ---------------------------------------------------------------------------
class LocalFDInterest(db.Model):
    """One Fixed-Deposit interest payment per (user, fd_account_ref, tax_year).

    Paired with an Income row that holds the gross interest amount.
    WHT withheld at source is stored on THIS row (not Income) because
    Income.amount_lkr is the GROSS for bracket aggregation; the WHT
    credit is applied separately at the engine-credit stage.
    """

    __tablename__ = "local_fd_interest_entries"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tax_year = db.Column(db.String(7), nullable=False, index=True)  # "2025/26"

    bank_name = db.Column(db.String(128), nullable=False)
    fd_account_ref = db.Column(db.String(64), nullable=True)
    # Principal stored for audit trail (NOT taxed — only the interest is).
    principal_lkr = db.Column(db.Numeric(20, 2), nullable=False, default=Decimal("0"))
    # WHT withheld at source on this interest payment (LKR).
    wht_lkr = db.Column(db.Numeric(20, 2), nullable=False, default=Decimal("0"))

    interest_date = db.Column(db.Date, nullable=False)

    # Back-link to the Income row this entry created (interest = income).
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
        db.Index(
            "ix_local_fd_interest_user_tax_year",
            "user_id", "tax_year",
        ),
        # Natural-key index (user + fd_account_ref + tax_year).
        db.Index(
            "ix_local_fd_interest_user_ref_year",
            "user_id", "fd_account_ref", "tax_year",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<LocalFDInterest id={self.id} user_id={self.user_id} "
            f"bank={self.bank_name!r} fd_ref={self.fd_account_ref!r}>"
        )


class LocalDividendIncome(db.Model):
    """One dividend payment per (user, company_name, ex_dividend_date)."""

    __tablename__ = "local_dividend_entries"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tax_year = db.Column(db.String(7), nullable=False, index=True)

    company_name = db.Column(db.String(128), nullable=False)
    ex_dividend_date = db.Column(db.Date, nullable=False)
    # WHT withheld at source on this dividend (LKR).
    wht_lkr = db.Column(db.Numeric(20, 2), nullable=False, default=Decimal("0"))

    # Back-link to Income row holding the gross dividend.
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
        db.Index("ix_local_dividend_user_tax_year", "user_id", "tax_year"),
        db.Index(
            "ix_local_dividend_user_date_company",
            "user_id", "ex_dividend_date", "company_name",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<LocalDividendIncome id={self.id} user_id={self.user_id} "
            f"company={self.company_name!r} date={self.ex_dividend_date}>"
        )


# ---------------------------------------------------------------------------
# Tax-year derivation (SL Y/A runs 1 April → 31 March)
# ---------------------------------------------------------------------------
def _tax_year_for(d: date) -> str:
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}/{str(start + 1)[2:]}"


def _normalise_tax_year(ty: str) -> str:
    """Normalise tax-year shape to canonical 'YYYY/YY'."""
    s = (ty or "").strip().replace("-", "/")
    if "/" in s:
        head, tail = s.split("/", 1)
        if len(tail) == 4 and tail.startswith(head[:2]):
            return f"{head}/{tail[2:]}"
        return s
    return s


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


def _new_investment_income(
    user_id: int,
    tax_year: str,
    money: Money,
    refs: list[dict[str, Any]],
) -> Income:
    """Build (do not add) an Income row for an LKR investment payment.

    source_type='investment_lkr', currency=LKR, source_country='LK'.
    """
    return Income(
        user_id=user_id,
        tax_year=tax_year,
        source_type="investment_lkr",
        amount=money.amount,
        currency="LKR",
        fx_rate=money.fx_rate,
        fx_source=money.fx_source,
        fx_date=money.fx_date,
        amount_lkr=money.amount_lkr,
        source_country="LK",
        evidence_refs=refs,
    )


# ---------------------------------------------------------------------------
# FD interest recording
# ---------------------------------------------------------------------------
def record_fd_interest(
    user,
    bank_name: str,
    principal_money: Money,
    interest_money: Money,
    wht_money: Optional[Money] = None,
    interest_date: Optional[date] = None,
    fd_account_ref: Optional[str] = None,
    tax_year: Optional[str] = None,
    evidence_refs: Optional[list[dict[str, Any]]] = None,
) -> LocalFDInterest:
    """Record (or update) one LKR FD interest payment.

    Side effects (in one transaction):
      1. CREATE or UPDATE LocalFDInterest row.
      2. CREATE or UPDATE paired Income row with source_type='investment_lkr'.
      3. ADD 'investment_lkr' to ``user.income_sources`` if not present.

    Idempotency: (user_id, fd_account_ref, tax_year) is the natural key.
    Same key → update; missing fd_account_ref → always-create (no dedup
    possible without a stable reference).

    Args:
        user:                 User ORM row (must have id + income_sources).
        bank_name:            Free-text bank name (1-128 chars).
        principal_money:      Money — FD principal (for audit only, NOT
                              taxed). Must be LKR.
        interest_money:       Money — gross interest earned in this
                              payment. Must be LKR.
        wht_money:            Optional Money — WHT withheld at source.
                              Must be LKR if provided. Defaults to 0.
        interest_date:        Date of the interest payment.  Defaults to
                              interest_money.fx_date.
        fd_account_ref:       Optional FD reference (deposit certificate
                              number). Natural-key component.
        tax_year:             Canonical 'YYYY/YY'. If None, derived from
                              interest_date.
        evidence_refs:        Optional evidence pointers.

    Returns:
        The persisted LocalFDInterest row (id populated).
    """
    if user is None or getattr(user, "id", None) is None:
        raise ValueError("user with .id is required")
    if not bank_name or not str(bank_name).strip():
        raise ValueError("bank_name is required")
    if principal_money is None or interest_money is None:
        raise ValueError("principal_money + interest_money are required")
    if (principal_money.currency or "LKR").upper() != "LKR":
        raise ValueError("FD principal must be LKR")
    if (interest_money.currency or "LKR").upper() != "LKR":
        raise ValueError("FD interest must be LKR")
    if interest_money.amount is None or interest_money.amount < 0:
        raise ValueError("interest_money.amount must be >= 0")
    if wht_money is not None:
        if (wht_money.currency or "LKR").upper() != "LKR":
            raise ValueError("WHT must be LKR")
        if wht_money.amount is None or wht_money.amount < 0:
            raise ValueError("wht_money.amount must be >= 0")

    int_date = interest_date or interest_money.fx_date
    ty = _normalise_tax_year(tax_year) if tax_year else _tax_year_for(int_date)
    bank_clean = str(bank_name).strip()[:128]
    fd_ref_clean = (fd_account_ref or "").strip()[:64] or None
    refs = list(evidence_refs or [])

    # ---- Idempotency lookup (only if fd_account_ref provided) ----
    existing: Optional[LocalFDInterest] = None
    if fd_ref_clean:
        try:
            existing = (
                LocalFDInterest.query
                .filter_by(
                    user_id=user.id,
                    fd_account_ref=fd_ref_clean,
                    tax_year=ty,
                )
                .first()
            )
        except Exception:  # pragma: no cover
            existing = None

    if existing is not None:
        existing.bank_name = bank_clean
        existing.principal_lkr = Decimal(str(principal_money.amount_lkr))
        existing.wht_lkr = (
            Decimal(str(wht_money.amount_lkr)) if wht_money is not None
            else Decimal("0")
        )
        existing.interest_date = int_date
        if refs:
            existing.evidence_refs = refs

        inc = None
        if existing.income_id:
            inc = Income.query.get(int(existing.income_id))
        if inc is None:
            inc = _new_investment_income(
                user.id, ty, interest_money,
                _with_fd_ref(refs, existing.id),
            )
            db.session.add(inc)
            db.session.flush()
            existing.income_id = inc.id
        else:
            inc.source_type = "investment_lkr"
            inc.tax_year = ty
            inc.amount = interest_money.amount
            inc.currency = "LKR"
            inc.fx_rate = interest_money.fx_rate
            inc.fx_source = interest_money.fx_source
            inc.fx_date = interest_money.fx_date
            inc.amount_lkr = interest_money.amount_lkr
            inc.source_country = "LK"
            inc.evidence_refs = _with_fd_ref(refs, existing.id)

        _add_income_source(user, "investment_lkr")
        db.session.commit()
        logger.info(
            "FD interest UPDATED: user=%s bank=%r fd_ref=%s tax_year=%s "
            "interest_lkr=%s wht_lkr=%s",
            user.id, bank_clean, fd_ref_clean, ty,
            interest_money.amount_lkr,
            wht_money.amount_lkr if wht_money is not None else 0,
        )
        return existing

    # ---- Create path ----
    entry = LocalFDInterest(
        user_id=user.id,
        tax_year=ty,
        bank_name=bank_clean,
        fd_account_ref=fd_ref_clean,
        principal_lkr=Decimal(str(principal_money.amount_lkr)),
        wht_lkr=(
            Decimal(str(wht_money.amount_lkr)) if wht_money is not None
            else Decimal("0")
        ),
        interest_date=int_date,
        evidence_refs=refs,
    )
    db.session.add(entry)
    db.session.flush()  # populate entry.id

    inc = _new_investment_income(
        user.id, ty, interest_money, _with_fd_ref(refs, entry.id),
    )
    db.session.add(inc)
    db.session.flush()
    entry.income_id = inc.id

    _add_income_source(user, "investment_lkr")
    db.session.commit()
    logger.info(
        "FD interest CREATED: user=%s bank=%r fd_ref=%s tax_year=%s "
        "interest_lkr=%s wht_lkr=%s entry_id=%s income_id=%s",
        user.id, bank_clean, fd_ref_clean, ty,
        interest_money.amount_lkr,
        wht_money.amount_lkr if wht_money is not None else 0,
        entry.id, inc.id,
    )
    return entry


def _with_fd_ref(
    refs: list[dict[str, Any]], entry_id: int,
) -> list[dict[str, Any]]:
    """Append a back-pointer to the LocalFDInterest row, deduped."""
    out = list(refs or [])
    if not any(
        isinstance(r, dict)
        and r.get("type") == "local_fd_interest"
        and int(r.get("ref_id", -1)) == int(entry_id)
        for r in out
    ):
        out.append({"type": "local_fd_interest", "ref_id": int(entry_id)})
    return out


# ---------------------------------------------------------------------------
# Dividend recording
# ---------------------------------------------------------------------------
def record_dividend(
    user,
    company_name: str,
    dividend_money: Money,
    wht_money: Optional[Money] = None,
    ex_dividend_date: Optional[date] = None,
    tax_year: Optional[str] = None,
    evidence_refs: Optional[list[dict[str, Any]]] = None,
) -> LocalDividendIncome:
    """Record (or update) one LKR dividend payment.

    Side effects (in one transaction):
      1. CREATE or UPDATE LocalDividendIncome row.
      2. CREATE or UPDATE paired Income row.
      3. ADD 'investment_lkr' to user.income_sources.

    Idempotency: (user_id, ex_dividend_date, company_name_lc, tax_year)
    is the natural key — case-insensitive on company name.

    Args:
        user:                User ORM row.
        company_name:        1-128 chars. Case-insensitive natural key.
        dividend_money:      Money — gross dividend. Must be LKR.
        wht_money:           Optional Money — WHT at source. Must be LKR.
        ex_dividend_date:    Required for natural-key dedup. Defaults to
                             dividend_money.fx_date.
        tax_year:            Canonical 'YYYY/YY'. If None, derived.
        evidence_refs:       Optional evidence pointers.
    """
    if user is None or getattr(user, "id", None) is None:
        raise ValueError("user with .id is required")
    if not company_name or not str(company_name).strip():
        raise ValueError("company_name is required")
    if dividend_money is None:
        raise ValueError("dividend_money is required")
    if (dividend_money.currency or "LKR").upper() != "LKR":
        raise ValueError("Dividend must be LKR")
    if dividend_money.amount is None or dividend_money.amount < 0:
        raise ValueError("dividend_money.amount must be >= 0")
    if wht_money is not None:
        if (wht_money.currency or "LKR").upper() != "LKR":
            raise ValueError("WHT must be LKR")
        if wht_money.amount is None or wht_money.amount < 0:
            raise ValueError("wht_money.amount must be >= 0")

    ex_date = ex_dividend_date or dividend_money.fx_date
    ty = _normalise_tax_year(tax_year) if tax_year else _tax_year_for(ex_date)
    company_clean = str(company_name).strip()[:128]
    company_lc = company_clean.lower()
    refs = list(evidence_refs or [])

    # Idempotency lookup
    rows = (
        LocalDividendIncome.query
        .filter_by(
            user_id=user.id, ex_dividend_date=ex_date, tax_year=ty,
        )
        .all()
    )
    existing = next(
        (r for r in rows if (r.company_name or "").strip().lower() == company_lc),
        None,
    )

    if existing is not None:
        existing.company_name = company_clean
        existing.wht_lkr = (
            Decimal(str(wht_money.amount_lkr)) if wht_money is not None
            else Decimal("0")
        )
        if refs:
            existing.evidence_refs = refs

        inc = None
        if existing.income_id:
            inc = Income.query.get(int(existing.income_id))
        if inc is None:
            inc = _new_investment_income(
                user.id, ty, dividend_money,
                _with_dividend_ref(refs, existing.id),
            )
            db.session.add(inc)
            db.session.flush()
            existing.income_id = inc.id
        else:
            inc.source_type = "investment_lkr"
            inc.tax_year = ty
            inc.amount = dividend_money.amount
            inc.currency = "LKR"
            inc.fx_rate = dividend_money.fx_rate
            inc.fx_source = dividend_money.fx_source
            inc.fx_date = dividend_money.fx_date
            inc.amount_lkr = dividend_money.amount_lkr
            inc.source_country = "LK"
            inc.evidence_refs = _with_dividend_ref(refs, existing.id)

        _add_income_source(user, "investment_lkr")
        db.session.commit()
        logger.info(
            "Dividend UPDATED: user=%s company=%r ex_date=%s "
            "dividend_lkr=%s wht_lkr=%s",
            user.id, company_clean, ex_date,
            dividend_money.amount_lkr,
            wht_money.amount_lkr if wht_money is not None else 0,
        )
        return existing

    # Create path
    entry = LocalDividendIncome(
        user_id=user.id,
        tax_year=ty,
        company_name=company_clean,
        ex_dividend_date=ex_date,
        wht_lkr=(
            Decimal(str(wht_money.amount_lkr)) if wht_money is not None
            else Decimal("0")
        ),
        evidence_refs=refs,
    )
    db.session.add(entry)
    db.session.flush()

    inc = _new_investment_income(
        user.id, ty, dividend_money,
        _with_dividend_ref(refs, entry.id),
    )
    db.session.add(inc)
    db.session.flush()
    entry.income_id = inc.id

    _add_income_source(user, "investment_lkr")
    db.session.commit()
    logger.info(
        "Dividend CREATED: user=%s company=%r ex_date=%s "
        "dividend_lkr=%s wht_lkr=%s entry_id=%s income_id=%s",
        user.id, company_clean, ex_date,
        dividend_money.amount_lkr,
        wht_money.amount_lkr if wht_money is not None else 0,
        entry.id, inc.id,
    )
    return entry


def _with_dividend_ref(
    refs: list[dict[str, Any]], entry_id: int,
) -> list[dict[str, Any]]:
    """Append a back-pointer to the LocalDividendIncome row, deduped."""
    out = list(refs or [])
    if not any(
        isinstance(r, dict)
        and r.get("type") == "local_dividend_income"
        and int(r.get("ref_id", -1)) == int(entry_id)
        for r in out
    ):
        out.append({"type": "local_dividend_income", "ref_id": int(entry_id)})
    return out


# ---------------------------------------------------------------------------
# Local CGT recording — wraps the canonical AssetDisposal
# ---------------------------------------------------------------------------
def record_local_cgt_disposal(
    user,
    asset_type: str,
    acquisition_money: Money,
    disposal_money: Money,
    acquisition_date: date,
    disposal_date: date,
    asset_identifier: Optional[str] = None,
    evidence_refs: Optional[list[dict[str, Any]]] = None,
) -> AssetDisposal:
    """Record one local capital-gain disposal as an AssetDisposal row.

    asset_type must be in {'real_estate','equity','unit_trust'}.
    source_country forced to 'LK' (LOCAL module). gain_lkr is computed
    as disposal − acquisition in LKR.

    Side effects:
      1. CREATE AssetDisposal row.
      2. ADD 'investment_lkr' to user.income_sources.

    No idempotency dedup — each disposal is a unique event. Caller
    must not double-create.

    Args:
        user:                User ORM row.
        asset_type:          One of LOCAL_CGT_ASSET_TYPES.
        acquisition_money:   Money — cost basis. Must be LKR.
        disposal_money:      Money — proceeds. Must be LKR.
        acquisition_date:    Buy date.
        disposal_date:       Sell date. Must be >= acquisition_date.
        asset_identifier:    Optional — street address, equity ticker,
                             unit-trust name.
        evidence_refs:       Optional evidence pointers.
    """
    if user is None or getattr(user, "id", None) is None:
        raise ValueError("user with .id is required")
    at = (asset_type or "").strip().lower()
    if at not in LOCAL_CGT_ASSET_TYPES:
        raise ValueError(
            f"asset_type must be one of {LOCAL_CGT_ASSET_TYPES}; "
            f"got {asset_type!r}"
        )
    if acquisition_money is None or disposal_money is None:
        raise ValueError("acquisition_money + disposal_money are required")
    if (acquisition_money.currency or "LKR").upper() != "LKR":
        raise ValueError("Local CGT — acquisition must be LKR")
    if (disposal_money.currency or "LKR").upper() != "LKR":
        raise ValueError("Local CGT — disposal must be LKR")
    if acquisition_money.amount is None or acquisition_money.amount < 0:
        raise ValueError("acquisition_money.amount must be >= 0")
    if disposal_money.amount is None or disposal_money.amount < 0:
        raise ValueError("disposal_money.amount must be >= 0")
    if disposal_date < acquisition_date:
        raise ValueError(
            f"disposal_date {disposal_date} cannot precede "
            f"acquisition_date {acquisition_date}"
        )

    tax_year = _tax_year_for(disposal_date)

    acq_lkr = Decimal(str(acquisition_money.amount_lkr))
    disp_lkr = Decimal(str(disposal_money.amount_lkr))
    gain_lkr = (disp_lkr - acq_lkr).quantize(Decimal("0.01"))

    disposal = AssetDisposal(
        user_id=user.id,
        tax_year=tax_year,
        asset_type=at,
        acq_amount=acquisition_money.amount,
        acq_currency="LKR",
        acq_fx_rate=acquisition_money.fx_rate,
        acq_fx_source=acquisition_money.fx_source,
        acq_fx_date=acquisition_money.fx_date,
        acq_amount_lkr=acq_lkr,
        disp_amount=disposal_money.amount,
        disp_currency="LKR",
        disp_fx_rate=disposal_money.fx_rate,
        disp_fx_source=disposal_money.fx_source,
        disp_fx_date=disposal_money.fx_date,
        disp_amount_lkr=disp_lkr,
        gain_lkr=gain_lkr,
        acquisition_date=acquisition_date,
        disposal_date=disposal_date,
        source_country="LK",
        asset_identifier=(asset_identifier or "")[:128] or None,
        evidence_refs=list(evidence_refs or []),
    )
    db.session.add(disposal)
    db.session.flush()

    _add_income_source(user, "investment_lkr")
    db.session.commit()
    logger.info(
        "Local CGT disposal recorded: user=%s asset_type=%s id=%s "
        "asset_identifier=%r gain_lkr=%s",
        user.id, at, asset_identifier, disposal.id, gain_lkr,
    )
    return disposal


# ---------------------------------------------------------------------------
# Generalised loss carry-forward — spans real_estate / equity / unit_trust / fd
#
# B13 crypto ships its own helper that filters on asset_type='crypto' (per
# W3c constraint we don't touch it). This is the parallel helper for the
# G3.5 LOCAL asset_types. Same indefinite-carry semantics (Phase-3 will
# impose the IRA Chapter IV §38-§40 cap).
# ---------------------------------------------------------------------------
def _compute_loss_carry_forward_for_asset_types(
    user_id: int,
    tax_year: str,
    asset_types: Iterable[str],
) -> Decimal:
    """Sum NET LOSSES from all prior tax years across the given asset_types.

    Each prior tax year's net is max(0, -sum(gain_lkr)) per asset-type
    bucket; only the loss portion carries forward. Losses are POOLED
    across the asset_types — a real-estate loss can offset an equity
    gain in a future year. (B13 crypto pools within crypto only;
    pooling within the LOCAL investment cohort matches the SL CGT
    treatment per Chapter IV §36/§37 — gain/loss is computed per
    realisation event and aggregated across the year regardless of
    asset class. TODO subsection cite — IRA KG rate-limited 2026-05-25.)

    Returns: Decimal >= 0; the LKR amount available to offset gains in
    ``tax_year`` from these asset_types.
    """
    ty_canonical = _normalise_tax_year(tax_year)
    types = tuple(asset_types)
    if not types:
        return Decimal("0.00")

    prior_rows = (
        AssetDisposal.query
        .filter(
            AssetDisposal.user_id == user_id,
            AssetDisposal.asset_type.in_(types),
            AssetDisposal.tax_year != ty_canonical,
        )
        .all()
    )
    by_year: dict[str, Decimal] = {}
    for r in prior_rows:
        if r.tax_year < ty_canonical:
            by_year[r.tax_year] = (
                by_year.get(r.tax_year, Decimal("0"))
                + Decimal(str(r.gain_lkr))
            )
    carry = Decimal("0")
    for year_net in by_year.values():
        if year_net < 0:
            carry += -year_net
    return carry.quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Aggregate compute (mirrors compute_crypto_cgt / compute_business_tax)
# ---------------------------------------------------------------------------
def compute_investment_local_tax_year(
    user,
    tax_year: str,
) -> dict[str, Any]:
    """Compute the LKR investment-income tax-bill components for ``user`` in
    ``tax_year``.

    Returns dict:
        {
            "tax_year":                          "2025/26",
            "fd_interest_lines":                 [dict, …],
            "dividend_lines":                    [dict, …],
            "cgt_lines":                         [dict, …],  # local CGT disposals
            "fd_interest_total_lkr":             Decimal,
            "dividend_total_lkr":                Decimal,
            "wht_credit_total_lkr":              Decimal,
            "cgt_gross_gain_lkr":                Decimal,
            "cgt_gross_loss_lkr":                Decimal,
            "cgt_net_gain_pre_carry_lkr":        Decimal,
            "cgt_loss_carry_forward_in_lkr":     Decimal,
            "cgt_net_gain_after_carry_lkr":      Decimal,
            "cgt_loss_carry_forward_out_lkr":    Decimal,
            "cgt_by_asset_type":                 {asset_type: dict},
            "investment_total_taxable_lkr":      Decimal,
            # Engine routing
            "lkr_investment_income_lkr":         Decimal,  # FD + div + cgt net
            "dtaa_credits":                      [],       # always [] (LOCAL)
            "dtaa_deferred":                     False,    # LOCAL — always False
        }

    For parity with the foreign-investment Wave-X handler this function
    calls ``apply_foreign_tax_credit(...)`` for every row that has a
    source_country set — for LOCAL all rows have source_country='LK' so
    the stub no-ops. The call site exists for forward-compat.
    """
    ty = _normalise_tax_year(tax_year)

    # ---- FD interest rows ----
    fd_rows = (
        LocalFDInterest.query
        .filter_by(user_id=user.id, tax_year=ty)
        .order_by(
            LocalFDInterest.interest_date.asc(),
            LocalFDInterest.id.asc(),
        )
        .all()
    )

    fd_lines: list[dict[str, Any]] = []
    fd_total = Decimal("0")
    fd_wht_total = Decimal("0")
    for entry in fd_rows:
        inc = (
            Income.query.get(int(entry.income_id))
            if entry.income_id else None
        )
        gross_lkr = (
            Decimal(str(inc.amount_lkr))
            if (inc and inc.amount_lkr is not None) else Decimal("0")
        )
        wht_lkr = Decimal(str(entry.wht_lkr or 0))
        fd_total += gross_lkr
        fd_wht_total += wht_lkr

        # DTAA seam (LOCAL: source_country='LK' → stub no-ops).
        if inc is not None and inc.source_country:
            apply_foreign_tax_credit(gross_lkr, inc)

        fd_lines.append({
            "entry_id": int(entry.id),
            "income_id": int(entry.income_id) if entry.income_id else None,
            "bank_name": entry.bank_name,
            "fd_account_ref": entry.fd_account_ref,
            "principal_lkr": Decimal(str(entry.principal_lkr or 0)),
            "interest_lkr": gross_lkr,
            "wht_lkr": wht_lkr,
            "interest_date": (
                entry.interest_date.isoformat() if entry.interest_date else None
            ),
        })

    # ---- Dividend rows ----
    div_rows = (
        LocalDividendIncome.query
        .filter_by(user_id=user.id, tax_year=ty)
        .order_by(
            LocalDividendIncome.ex_dividend_date.asc(),
            LocalDividendIncome.id.asc(),
        )
        .all()
    )

    div_lines: list[dict[str, Any]] = []
    div_total = Decimal("0")
    div_wht_total = Decimal("0")
    for entry in div_rows:
        inc = (
            Income.query.get(int(entry.income_id))
            if entry.income_id else None
        )
        gross_lkr = (
            Decimal(str(inc.amount_lkr))
            if (inc and inc.amount_lkr is not None) else Decimal("0")
        )
        wht_lkr = Decimal(str(entry.wht_lkr or 0))
        div_total += gross_lkr
        div_wht_total += wht_lkr

        if inc is not None and inc.source_country:
            apply_foreign_tax_credit(gross_lkr, inc)

        div_lines.append({
            "entry_id": int(entry.id),
            "income_id": int(entry.income_id) if entry.income_id else None,
            "company_name": entry.company_name,
            "ex_dividend_date": (
                entry.ex_dividend_date.isoformat()
                if entry.ex_dividend_date else None
            ),
            "dividend_lkr": gross_lkr,
            "wht_lkr": wht_lkr,
        })

    # ---- Local CGT disposal rows ----
    cgt_rows = (
        AssetDisposal.query
        .filter(
            AssetDisposal.user_id == user.id,
            AssetDisposal.tax_year == ty,
            AssetDisposal.asset_type.in_(LOCAL_CGT_ASSET_TYPES),
            # LOCAL-only filter: source_country must be 'LK' or NULL (None).
            # Excludes rows the foreign-CGT Wave-X handler will own.
            db.or_(
                AssetDisposal.source_country == "LK",
                AssetDisposal.source_country.is_(None),
            ),
        )
        .order_by(
            AssetDisposal.disposal_date.asc(),
            AssetDisposal.id.asc(),
        )
        .all()
    )

    cgt_lines: list[dict[str, Any]] = []
    cgt_gross_gain = Decimal("0")
    cgt_gross_loss = Decimal("0")
    cgt_by_asset_type: dict[str, dict[str, Any]] = {}

    for row in cgt_rows:
        gain = Decimal(str(row.gain_lkr))
        if gain >= 0:
            cgt_gross_gain += gain
        else:
            cgt_gross_loss += -gain

        bucket = cgt_by_asset_type.setdefault(row.asset_type, {
            "asset_type": row.asset_type,
            "gain_lkr": Decimal("0"),
            "loss_lkr": Decimal("0"),
            "net_lkr": Decimal("0"),
            "rows": 0,
        })
        bucket["net_lkr"] += gain
        if gain >= 0:
            bucket["gain_lkr"] += gain
        else:
            bucket["loss_lkr"] += -gain
        bucket["rows"] += 1

        # DTAA seam (LOCAL — no credit; stub no-ops on source_country='LK').
        if row.source_country:
            class _DisposalAsIncome:
                source_country = row.source_country
                source_type = "investment_lkr"
            apply_foreign_tax_credit(gain.copy_abs(), _DisposalAsIncome())

        cgt_lines.append({
            "disposal_id": int(row.id),
            "asset_type": row.asset_type,
            "asset_identifier": row.asset_identifier,
            "acquisition_date": (
                row.acquisition_date.isoformat()
                if row.acquisition_date else None
            ),
            "disposal_date": (
                row.disposal_date.isoformat()
                if row.disposal_date else None
            ),
            "acq_amount_lkr": Decimal(str(row.acq_amount_lkr)),
            "disp_amount_lkr": Decimal(str(row.disp_amount_lkr)),
            "gain_lkr": gain,
            "source_country": row.source_country or "LK",
        })

    cgt_net_pre_carry = (cgt_gross_gain - cgt_gross_loss).quantize(Decimal("0.01"))

    # Carry-forward IN from prior years (pooled across LOCAL asset types).
    cgt_cf_in = _compute_loss_carry_forward_for_asset_types(
        int(user.id), ty, _LOCAL_CARRY_ASSET_TYPES,
    )

    if cgt_net_pre_carry > 0:
        offset = min(cgt_net_pre_carry, cgt_cf_in)
        cgt_net_after = (cgt_net_pre_carry - offset).quantize(Decimal("0.01"))
        cgt_cf_out = Decimal("0")
    else:
        cgt_net_after = Decimal("0")
        cgt_cf_out = (cgt_cf_in + (-cgt_net_pre_carry)).quantize(Decimal("0.01"))

    # Engine-bucket: FD interest + dividends + net CGT (after carry) all
    # join the investment_lkr bucket in the Phase-1 engine. Phase-3 will
    # split CGT into a dedicated bucket.
    lkr_investment_total = (
        fd_total + div_total + cgt_net_after
    ).quantize(Decimal("0.01"))

    wht_credit_total = (fd_wht_total + div_wht_total).quantize(Decimal("0.01"))

    return {
        "tax_year": ty,
        "fd_interest_lines": fd_lines,
        "dividend_lines": div_lines,
        "cgt_lines": cgt_lines,
        "fd_interest_total_lkr": fd_total.quantize(Decimal("0.01")),
        "dividend_total_lkr": div_total.quantize(Decimal("0.01")),
        "wht_credit_total_lkr": wht_credit_total,
        "cgt_gross_gain_lkr": cgt_gross_gain.quantize(Decimal("0.01")),
        "cgt_gross_loss_lkr": cgt_gross_loss.quantize(Decimal("0.01")),
        "cgt_net_gain_pre_carry_lkr": cgt_net_pre_carry,
        "cgt_loss_carry_forward_in_lkr": cgt_cf_in,
        "cgt_net_gain_after_carry_lkr": cgt_net_after,
        "cgt_loss_carry_forward_out_lkr": cgt_cf_out,
        "cgt_by_asset_type": cgt_by_asset_type,
        "investment_total_taxable_lkr": lkr_investment_total,
        "lkr_investment_income_lkr": lkr_investment_total,
        "dtaa_credits": [],
        "dtaa_deferred": False,
    }


# ---------------------------------------------------------------------------
# Listing helpers used by routes
# ---------------------------------------------------------------------------
def list_fd_interest_for_user(
    user, tax_year: Optional[str] = None,
) -> list[LocalFDInterest]:
    q = LocalFDInterest.query.filter_by(user_id=user.id)
    if tax_year:
        q = q.filter_by(tax_year=_normalise_tax_year(tax_year))
    return q.order_by(
        LocalFDInterest.tax_year.desc(),
        LocalFDInterest.interest_date.desc(),
    ).all()


def list_dividends_for_user(
    user, tax_year: Optional[str] = None,
) -> list[LocalDividendIncome]:
    q = LocalDividendIncome.query.filter_by(user_id=user.id)
    if tax_year:
        q = q.filter_by(tax_year=_normalise_tax_year(tax_year))
    return q.order_by(
        LocalDividendIncome.tax_year.desc(),
        LocalDividendIncome.ex_dividend_date.desc(),
    ).all()


def list_local_cgt_disposals_for_user(
    user, tax_year: Optional[str] = None,
) -> list[AssetDisposal]:
    q = AssetDisposal.query.filter(
        AssetDisposal.user_id == user.id,
        AssetDisposal.asset_type.in_(LOCAL_CGT_ASSET_TYPES),
        db.or_(
            AssetDisposal.source_country == "LK",
            AssetDisposal.source_country.is_(None),
        ),
    )
    if tax_year:
        q = q.filter(AssetDisposal.tax_year == _normalise_tax_year(tax_year))
    return q.order_by(AssetDisposal.disposal_date.desc()).all()


def apply_foreign_tax_credit_for_local(*_a, **_kw):
    """Stub-mirror entry point.

    Wave-X dispatch will replace this with the foreign-investment handler
    that wires the real DTAA seam. For LOCAL rows the function is a no-op
    and returns None (no credit). Documented for completeness.
    """
    return None


__all__ = [
    "LocalFDInterest",
    "LocalDividendIncome",
    "LOCAL_CGT_ASSET_TYPES",
    "record_fd_interest",
    "record_dividend",
    "record_local_cgt_disposal",
    "_compute_loss_carry_forward_for_asset_types",
    "compute_investment_local_tax_year",
    "list_fd_interest_for_user",
    "list_dividends_for_user",
    "list_local_cgt_disposals_for_user",
]
