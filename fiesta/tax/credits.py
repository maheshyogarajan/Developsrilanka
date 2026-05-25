"""fiesta.tax.credits — DTAA seam (Design Lock 2 §6).

BINDING. Every tax-engine code path that touches foreign-source income MUST
call ``apply_foreign_tax_credit(...)`` rather than reduce SL liability inline.
The function is a no-op until Wave-X (B9) lands the real DTAA treaty matrix,
but the call sites become the seam for the DTAA engine to drop in WITHOUT
schema or call-site rework downstream.

Consumers (B11 RSU, B13 crypto/CGT, Section G G3.5 foreign investment) all
import from here. Wave-X replaces ``dtaa_treaty_lookup`` body + populates a
``treaty_articles`` table; zero schema changes elsewhere.

Provenance: Design Lock 2 §6 (Council 2026-05-25 — Opus 4.7 "DTAA stub in
shared tax/credits.py" + Gemini 3.1 Pro "standardised FTC JSON").
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:  # avoid circular import at runtime
    from fiesta.tax.models import Income


@dataclass
class TreatyArticle:
    """One DTAA article that grants a credit or exemption.

    Wave-X (B9) populates real instances from the treaty_articles table.
    Until then, ``dtaa_treaty_lookup`` returns None for every (country,
    income_type) pair so this dataclass is effectively unused — but it is
    referenced by ``ForeignTaxCredit`` so downstream code can pre-write the
    type-safe seam.

    Fields (locked):
      country         — ISO-3166-1 alpha-2 (US, GB, AU, ...)
      article_number  — e.g. "15" (US treaty Art 15 — employment)
      treaty_year     — e.g. 2002 (US-SL DTAA signed)
      rule_text       — plain-English summary of the article
      full_text_ref   — URL or doc-id of canonical treaty text
      credit_kind     — "exemption" | "credit_max_treaty_rate"
                         | "credit_max_local"
    """

    country: str
    article_number: str
    treaty_year: int
    rule_text: str
    full_text_ref: str
    credit_kind: str


@dataclass
class ForeignTaxCredit:
    """Standardised FTC object emitted by the DTAA engine.

    Tax engine consumes this to reduce SL liability. Section G G3.x reuses
    the same shape so the audit pack speaks one language across every
    foreign-source code path.

    Fields (locked):
      treaty_article  — the TreatyArticle that grants the credit
      source_country  — ISO-3166-1 alpha-2 (matches Income.source_country)
      income_type     — matches Income.source_type
      gross_lkr       — gross income subject to relief
      credit_lkr      — credit applied against SL liability
      rationale       — audit-pack explanation
    """

    treaty_article: TreatyArticle
    source_country: str
    income_type: str
    gross_lkr: Decimal
    credit_lkr: Decimal
    rationale: str


def dtaa_treaty_lookup(country: str, income_type: str) -> Optional[TreatyArticle]:
    """Stub. Wave-X (B9) replaces this body with the real treaty matrix.

    Until then, returns ``None`` and all callers behave conservatively
    (no foreign tax credit applied; user pays full SL liability).

    Consumers (every call site is a Wave-X drop-in seam):
      - B11 RSU classifier:           ``compute_rsu_tax(...)``
      - B13 crypto/CGT:               ``compute_crypto_cgt(...)``
      - Section G G3.5 foreign inv:   ``compute_foreign_investment_tax(...)``

    Args:
        country:     ISO-3166-1 alpha-2 source country code.
        income_type: matches Income.source_type (one of the locked values).

    Returns:
        TreatyArticle if a matching treaty article exists, else None.
        Pre-Wave-X: always None.
    """
    return None


def apply_foreign_tax_credit(
    sl_liability_lkr: Decimal,
    income: "Income",
) -> Tuple[Decimal, Optional[ForeignTaxCredit]]:
    """Apply DTAA credit to an SL liability for one income row.

    Until Wave-X lands, ``dtaa_treaty_lookup`` returns None and this
    function is a no-op pass-through: returns ``(sl_liability_lkr, None)``.

    Args:
        sl_liability_lkr: SL income-tax liability attributable to ``income``.
        income:           the canonical Income row (must expose
                          ``source_country`` and ``source_type`` attributes).

    Returns:
        ``(net_liability_after_credit, ForeignTaxCredit_or_None)``.
    """
    if getattr(income, "source_country", None) is None:
        return sl_liability_lkr, None
    article = dtaa_treaty_lookup(income.source_country, income.source_type)
    if article is None:
        return sl_liability_lkr, None
    # Wave-X owns the real credit computation. Stub leaves liability untouched.
    return sl_liability_lkr, None


__all__ = [
    "TreatyArticle",
    "ForeignTaxCredit",
    "dtaa_treaty_lookup",
    "apply_foreign_tax_credit",
]
