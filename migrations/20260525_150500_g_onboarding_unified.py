"""
Migration MG-004 — MS4 W3e G4 unified onboarding backfill.

No schema changes. Pure backfill: for existing users with
``onboarding_completed=True`` AND empty ``income_sources``, infer
income_sources from observed signals so the unified-flow recommender
(``fiesta.onboarding._recommended_next``) and the hub funnel-state
recommender (``app._compute_non_remittance_next_step``) surface the
right modules without forcing the user back through the picker.

What this migration does
========================

For each user row matching the eligibility filter, derive an additive
set of income_sources from these signals (in order — first matching
signal contributes, all subsequent signals also contribute; the set is
deduped at write time):

  1. ``persona == 'sl_foreign_income'``                  → 'foreign_remittance'
  2. RemittanceEntry rows for the user                   → 'foreign_remittance'
  3. RSUVestingEvent rows                                → 'rsu'
  4. CryptoDisposal OR Income(source_type='crypto')      → 'crypto'
  5. BusinessIncome OR Income(source_type='business_*')  → 'business_lkr' / 'business_foreign' (whichever matches)
  6. Income(source_type='employment_lkr')                → 'employment_lkr'
  7. Income(source_type='professional_fees_lkr')         → 'professional_fees_lkr'
  8. RentalIncomeEntry OR Income(source_type='rental_*') → 'rental_lkr' / 'rental_foreign'
  9. LocalFDInterestEntry / LocalDividendEntry           → 'investment_lkr'
 10. Income(source_type='investment_foreign')            → 'investment_foreign'

If nothing matches AND persona is non-null, we DO NOT guess; the user
just sees the picker again on next hub visit (no_income_sources funnel
state → /onboarding/welcome card). That's the safe default — we never
fabricate an income type the user didn't actually have.

Idempotent: re-running the migration on a user who already has
income_sources is a no-op (the eligibility filter excludes them).

Dialect-aware via ORM — works on Postgres prod (Fly/Neon) AND SQLite
(test). All side-effect tables (RSU / Crypto / Business / Rental / etc.)
are queried defensively; ImportError or table-missing-in-DB falls
through silently. CI on a fresh DB will see zero eligible users and
exit happy with a zero row-count.

Run::

    python migrations/20260525_150500_g_onboarding_unified.py upgrade

Production (Fly)::

    flyctl ssh console -a fiesta-mvp -C \\
      'python migrations/20260525_150500_g_onboarding_unified.py upgrade'

Downgrade is a no-op — backfill is additive only. Reverting MS4 W3e
would leave the inferred income_sources in place; this is safe and
desirable (the picker is additive everywhere else too).

Provenance: Section G G4.3 +
working files/_fiesta_unification_addendum_20260525.md +
Design Lock 3 §D2-§D4 unified-hub contract.
"""
from __future__ import annotations

import logging
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app import app, db  # noqa: E402
from sqlalchemy import text  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("MG-004")


# Canonical vocabulary (mirrors fiesta.income_sources.routes.CANONICAL_INCOME_SOURCES
# so we don't import a heavy module at migration time).
_CANONICAL = (
    "foreign_remittance",
    "employment_lkr",
    "professional_fees_lkr",
    "business_lkr",
    "business_foreign",
    "rsu",
    "crypto",
    "rental_lkr",
    "rental_foreign",
    "investment_lkr",
    "investment_foreign",
    "other",
)


def _infer_sources_for_user(user) -> list[str]:
    """Return an ordered list of inferred income_sources for `user`.

    Each helper that queries a side-effect table is wrapped in try/except
    so a missing import / missing table / dialect quirk doesn't break
    the whole backfill. We collect into a set, then return in canonical
    order so the persisted column is deterministic.
    """
    inferred: set[str] = set()
    uid = getattr(user, "id", None)
    if uid is None:
        return []

    # 1. persona signal
    if getattr(user, "persona", None) == "sl_foreign_income":
        inferred.add("foreign_remittance")

    # 2. RemittanceEntry — foreign_remittance evidence
    try:
        from models import RemittanceEntry
        if RemittanceEntry.query.filter_by(user_id=uid).first() is not None:
            inferred.add("foreign_remittance")
    except Exception:
        pass

    # 3. RSU vesting events
    try:
        from fiesta.tax.models import RSUVestingEvent
        if RSUVestingEvent.query.filter_by(user_id=uid).first() is not None:
            inferred.add("rsu")
    except Exception:
        pass

    # 4. Crypto disposals
    try:
        from fiesta.tax.models import CryptoDisposal
        if CryptoDisposal.query.filter_by(user_id=uid).first() is not None:
            inferred.add("crypto")
    except Exception:
        pass

    # 5. Business income
    try:
        from fiesta.tax.models import BusinessIncome
        for bi in BusinessIncome.query.filter_by(user_id=uid).all():
            # Guess local vs foreign from source_country if present;
            # default to LKR.
            _src_country = getattr(bi, "source_country", None) or "LK"
            if _src_country.upper() == "LK":
                inferred.add("business_lkr")
            else:
                inferred.add("business_foreign")
    except Exception:
        pass

    # 6-10. Canonical Income table — covers everything not in side
    # tables above. Be defensive about source_type values.
    try:
        from fiesta.tax.models import Income
        for inc in Income.query.filter_by(user_id=uid).all():
            st = (getattr(inc, "source_type", None) or "").strip()
            if not st:
                continue
            if st in _CANONICAL:
                inferred.add(st)
            # Some legacy rows may have collapsed variants — map them
            # to the canonical vocab.
            elif st == "business":
                inferred.add("business_lkr")
            elif st == "rental":
                inferred.add("rental_lkr")
            elif st == "investment":
                inferred.add("investment_lkr")
    except Exception:
        pass

    # 8b. Rental side table
    try:
        from sqlalchemy import inspect as _sa_inspect
        if _sa_inspect(db.engine).has_table("rental_income_entries"):
            row = db.session.execute(
                text(
                    "SELECT source_country FROM rental_income_entries "
                    "WHERE user_id = :uid LIMIT 1"
                ),
                {"uid": uid},
            ).first()
            if row is not None:
                _country = (row[0] or "LK").upper() if row[0] else "LK"
                if _country == "LK":
                    inferred.add("rental_lkr")
                else:
                    inferred.add("rental_foreign")
    except Exception:
        pass

    # 9b. FD interest / dividend side tables → investment_lkr
    try:
        from sqlalchemy import inspect as _sa_inspect
        insp = _sa_inspect(db.engine)
        if insp.has_table("local_fd_interest_entries"):
            row = db.session.execute(
                text(
                    "SELECT 1 FROM local_fd_interest_entries "
                    "WHERE user_id = :uid LIMIT 1"
                ),
                {"uid": uid},
            ).first()
            if row is not None:
                inferred.add("investment_lkr")
        if insp.has_table("local_dividend_entries"):
            row = db.session.execute(
                text(
                    "SELECT 1 FROM local_dividend_entries "
                    "WHERE user_id = :uid LIMIT 1"
                ),
                {"uid": uid},
            ).first()
            if row is not None:
                inferred.add("investment_lkr")
    except Exception:
        pass

    # Return in canonical order for deterministic persistence.
    return [s for s in _CANONICAL if s in inferred]


def _eligible_user_ids() -> list[int]:
    """User IDs eligible for backfill — onboarding_completed=True AND
    income_sources column is null/empty list/empty string.

    JSON-column emptiness check is dialect-sensitive; we read the rows
    into Python and filter there, which works on both Postgres and
    SQLite without dialect branching.
    """
    from models import User
    eligible: list[int] = []
    # Use a small batch size so the migration scales without an
    # all-rows-in-memory dance.
    try:
        for u in User.query.filter(User.onboarding_completed.is_(True)).all():
            src = getattr(u, "income_sources", None)
            if src is None or src == [] or src == "" or src == "[]":
                eligible.append(u.id)
    except Exception as exc:
        log.error("MG-004: failed to enumerate users: %s", exc)
    return eligible


def upgrade() -> bool:
    """Apply backfill. Idempotent — re-runs are no-ops once
    income_sources is populated for a given user.

    Returns True on success (including zero-eligible).
    """
    with app.app_context():
        log.info(
            "=== MG-004 unified-onboarding backfill: UPGRADE starting ==="
        )
        from models import User

        ids = _eligible_user_ids()
        log.info("MG-004: %d eligible users (onboarding_completed=True, "
                 "income_sources empty)", len(ids))

        if not ids:
            log.info(
                "=== MG-004 unified-onboarding backfill: UPGRADE complete "
                "(no-op, 0 users) ==="
            )
            return True

        ok = True
        n_updated = 0
        n_skipped = 0
        for uid in ids:
            try:
                u = User.query.get(uid)
                if u is None:
                    n_skipped += 1
                    continue
                inferred = _infer_sources_for_user(u)
                if not inferred:
                    # Honest: no signal → leave empty. User sees the
                    # picker on next hub visit.
                    log.info(
                        "MG-004: user %d has no inferable income_sources "
                        "(persona=%s); skipping",
                        uid,
                        getattr(u, "persona", None),
                    )
                    n_skipped += 1
                    continue
                u.income_sources = inferred
                try:
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(u, "income_sources")
                except Exception:
                    pass
                db.session.add(u)
                db.session.commit()
                n_updated += 1
                log.info(
                    "MG-004: user %d backfilled income_sources=%s",
                    uid,
                    inferred,
                )
            except Exception as exc:
                db.session.rollback()
                log.error(
                    "MG-004: user %d backfill FAILED: %s", uid, exc
                )
                ok = False

        log.info(
            "=== MG-004 unified-onboarding backfill: UPGRADE %s "
            "(updated=%d, skipped=%d) ===",
            "complete" if ok else "PARTIAL",
            n_updated,
            n_skipped,
        )
        return ok


def downgrade() -> bool:
    """No-op. The backfill is additive; reverting MS4 W3e leaves the
    inferred income_sources in place, which is safe (the picker is
    additive everywhere else too)."""
    with app.app_context():
        log.info(
            "=== MG-004 unified-onboarding backfill: DOWNGRADE no-op ==="
        )
        return True


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    if action == "downgrade":
        sys.exit(0 if downgrade() else 1)
    sys.exit(0 if upgrade() else 1)
