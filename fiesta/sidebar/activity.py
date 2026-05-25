"""fiesta.sidebar.activity — legacy-bookkeeping activity detection.

PURPOSE
-------
MS4 W2 Agent 2 (G1.4 — Design Lock 3 §D6, 2026-05-25): the unified FIESTA
sidebar surfaces a "Bookkeeping" group conditional on the authenticated
user having historical activity in the corresponding legacy tables. This
module is the single source of truth for those "is this module relevant
for this user?" predicates.

Per the design lock, every authenticated non-admin user gets the FIESTA
shell (`layout_fiesta.html`). For legacy bookkeeping users who don't yet
have `income_sources` populated (the new discriminator), the bookkeeping
modules they have data in MUST still appear in the sidebar — otherwise
G1.2 + G1.3 silently strips their navigation. This helper closes that
hole.

DESIGN
------
- One pure function — `compute_bookkeeping_modules_available(user)` —
  returns a dict of booleans keyed by module slug. The Jinja template
  reads `bookkeeping_modules_available.receipts`, etc.
- Computed once per request via context processor + memoised per-user
  for 60s via `fiesta.perf_cache.memoize_ttl` (same TTL the FIESTA hub
  context uses). The cache is invalidated whenever the user touches one
  of the underlying tables — but for sidebar accuracy a 60s lag is fine
  (the user sees the new module on the next page load, not the current
  POST response).
- Org-scoped tables (Account, BankStatement) resolve user→orgs via
  `OrganizationUser`. A user in zero orgs has every org-scoped flag
  False — that is the correct answer (no bookkeeping activity yet).
- Every predicate uses `.first()`-style existence checks; never loads
  rows. The query plan is a single `SELECT 1 ... LIMIT 1` per table.
- Defensive: if a model import or query raises (e.g. table not yet
  migrated in CI), the predicate returns False rather than blowing up
  the page render. This mirrors the defensive style of
  `inject_fiesta_hub_context` in app.py.

KEYS RETURNED
-------------
- receipts          → any Receipt row for the user
- pnl               → any expense or invoice (i.e. P&L has at least one line)
- cash_in           → any Client or Invoice row (incoming-money flow)
- cash_out          → any CompanyExpense or ClientExpense row (outgoing-money flow)
- accounts          → any Account or BankAccount row tied to user's orgs / user
- bank_statements   → any BankStatement row tied to user's orgs
- tax_documents     → no clean FK from a "tax document" table to the user
                      today (the legacy /tax-doc/scan route doesn't persist
                      to its own table — receipts surface there). Surfaced
                      with the same heuristic as receipts; tracked as TODO
                      in the dispatch summary for W3 to wire to the real
                      bookkeeping/tax-doc model when one exists.

Each key is also exposed under `*_label` and `*_url` for any template
that wants the rendered string + href without recomputing — but the
sidebar template renders those literals inline, so today only the
booleans matter.
"""
from __future__ import annotations

import logging
from typing import Any

from fiesta.perf_cache import memoize_ttl

logger = logging.getLogger(__name__)


# Public dict shape — callers (template + tests) rely on these keys
# being present. The values are always booleans.
MODULE_KEYS: tuple[str, ...] = (
    "receipts",
    "pnl",
    "cash_in",
    "cash_out",
    "accounts",
    "bank_statements",
    "tax_documents",
)


def _empty_flags() -> dict[str, bool]:
    """Return a dict with every module key set to False. Used as the
    safe-default for anonymous users + on any exception path."""
    return {k: False for k in MODULE_KEYS}


def _resolve_user_org_ids(user_id: int) -> list[int]:
    """Return every organization_id the user is a member of.

    Org-scoped bookkeeping tables (BankStatement, Account) live one
    step removed from the user — we have to traverse OrganizationUser.
    Returns [] (not None) when the user belongs to zero orgs.
    """
    try:
        from models import OrganizationUser
        rows = OrganizationUser.query.filter_by(user_id=user_id).all()
        return [r.organization_id for r in rows]
    except Exception as exc:  # noqa: BLE001 — defensive, see module docstring
        logger.debug("activity._resolve_user_org_ids failed: %s", exc)
        return []


def _has_receipts(user_id: int) -> bool:
    try:
        from models import Receipt
        return Receipt.query.filter_by(user_id=user_id).first() is not None
    except Exception as exc:  # noqa: BLE001
        logger.debug("activity._has_receipts failed: %s", exc)
        return False


def _has_pnl_activity(user_id: int, org_ids: list[int]) -> bool:
    """P&L is non-empty when the user has at least one expense OR
    one invoice OR one journal-line touching one of their orgs.

    We start with the cheap user-scoped checks (CompanyExpense /
    Invoice) and only escalate to the org-scoped GeneralLedgerEntry
    check if those miss — minimises query count for the common case
    (an active bookkeeping user typically has expenses + invoices).
    """
    try:
        from models import CompanyExpense, Invoice
        if CompanyExpense.query.filter_by(user_id=user_id).first() is not None:
            return True
        if Invoice.query.filter_by(user_id=user_id).first() is not None:
            return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("activity._has_pnl_activity user-scoped check failed: %s", exc)
    if not org_ids:
        return False
    try:
        from accounting_models import GeneralLedgerEntry
        return GeneralLedgerEntry.query.filter(
            GeneralLedgerEntry.organization_id.in_(org_ids)
        ).first() is not None
    except Exception as exc:  # noqa: BLE001
        logger.debug("activity._has_pnl_activity org-scoped check failed: %s", exc)
        return False


def _has_cash_in_activity(user_id: int) -> bool:
    """Cash-in = invoices to clients OR client records. Either signals
    the user has used the receivables side of the bookkeeping product."""
    try:
        from models import Invoice, Client
        if Invoice.query.filter_by(user_id=user_id).first() is not None:
            return True
        if Client.query.filter_by(user_id=user_id).first() is not None:
            return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("activity._has_cash_in_activity failed: %s", exc)
    return False


def _has_cash_out_activity(user_id: int) -> bool:
    """Cash-out = company OR client expenses for the user."""
    try:
        from models import CompanyExpense, ClientExpense
        if CompanyExpense.query.filter_by(user_id=user_id).first() is not None:
            return True
        if ClientExpense.query.filter_by(user_id=user_id).first() is not None:
            return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("activity._has_cash_out_activity failed: %s", exc)
    return False


def _has_accounts(user_id: int, org_ids: list[int]) -> bool:
    """Accounts module is relevant if the user has a personal BankAccount
    row OR if any of their orgs has Chart-of-Accounts entries."""
    try:
        from models import BankAccount
        if BankAccount.query.filter_by(user_id=user_id).first() is not None:
            return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("activity._has_accounts BankAccount check failed: %s", exc)
    if not org_ids:
        return False
    try:
        from accounting_models import Account
        return Account.query.filter(
            Account.organization_id.in_(org_ids)
        ).first() is not None
    except Exception as exc:  # noqa: BLE001
        logger.debug("activity._has_accounts Account check failed: %s", exc)
        return False


def _has_bank_statements(org_ids: list[int]) -> bool:
    """BankStatement is strictly org-scoped (`organization_id` FK, no
    direct user FK). Empty org list → no bank statements."""
    if not org_ids:
        return False
    try:
        from enhanced_financial_models import BankStatement
        return BankStatement.query.filter(
            BankStatement.organization_id.in_(org_ids)
        ).first() is not None
    except Exception as exc:  # noqa: BLE001
        logger.debug("activity._has_bank_statements failed: %s", exc)
        return False


def _has_tax_documents(user_id: int) -> bool:
    """Legacy `/tax-doc/scan` does not persist into a dedicated tax-doc
    table — scanned tax documents land in Receipt with a category
    discriminator. Until the bookkeeping side gains a real tax-doc
    model, we treat "has receipts" as the surfacing signal. Splitting
    the two is W3 (G2.4) work; flagged in the dispatch summary.
    """
    return _has_receipts(user_id)


def _compute_uncached(user_id: int) -> dict[str, bool]:
    """The actual compute — called only when the per-user cache misses."""
    org_ids = _resolve_user_org_ids(user_id)
    return {
        "receipts":        _has_receipts(user_id),
        "pnl":             _has_pnl_activity(user_id, org_ids),
        "cash_in":         _has_cash_in_activity(user_id),
        "cash_out":        _has_cash_out_activity(user_id),
        "accounts":        _has_accounts(user_id, org_ids),
        "bank_statements": _has_bank_statements(org_ids),
        "tax_documents":   _has_tax_documents(user_id),
    }


# Per-user 60s TTL cache. Same window as inject_fiesta_hub_context so a
# state change visible in the hub is also visible in the sidebar within
# the same window.
@memoize_ttl(seconds=60, key_func=lambda user_id: f"bookkeeping_modules:{user_id}")
def _compute_cached(user_id: int) -> dict[str, bool]:
    return _compute_uncached(user_id)


def compute_bookkeeping_modules_available(user: Any) -> dict[str, bool]:
    """Public entrypoint — return the dict of {module_slug: bool} for
    `user`. Anonymous / None / id-less users get the empty-flags dict.

    Safe to call from both context processors and direct test paths.
    """
    if user is None:
        return _empty_flags()
    if not getattr(user, "is_authenticated", False):
        return _empty_flags()
    user_id = getattr(user, "id", None)
    if user_id is None:
        return _empty_flags()
    try:
        return _compute_cached(int(user_id))
    except Exception as exc:  # noqa: BLE001 — never break a page render
        logger.warning(
            "compute_bookkeeping_modules_available failed for user_id=%s: %s",
            user_id,
            exc,
        )
        return _empty_flags()


__all__ = [
    "MODULE_KEYS",
    "compute_bookkeeping_modules_available",
]
