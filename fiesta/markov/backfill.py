"""
fiesta.markov.backfill — seed user_state_history for the pre-launch cohort.

Pre-launch FIESTA already has ~3,877 users. None of them produced an
``events.emit`` row when they crossed the Markov state transitions
because Layer 2 didn't exist yet. Without a one-shot seed, every
historical user shows up as "no Markov history" — dwell-time and
conversion-rate queries can't include them, so cohort analytics are
broken until the next state change.

This module derives each existing user's CURRENT state (using the
Layer-1 derivation function — the source of truth for snapshot state)
and inserts ONE backfill row per user. From that point forward the
event-driven writer captures every new transition.

Public surface
--------------
``backfill_all_users(commit: bool = False) -> dict``
    Run the backfill. Returns a summary dict
    ``{seen, would_seed, seeded, skipped_existing, skipped_no_state, errors}``.
    ``commit=False`` (default) is a dry-run: counts but doesn't write.

The Flask CLI registration in ``fiesta.markov.cli`` exposes this as
``flask markov backfill`` / ``flask markov backfill --commit``.

Idempotency
-----------
A user with EVEN ONE existing UserStateHistory row is skipped — re-
running the backfill is safe. (We don't want to bury a real transition
under a synthetic backfill row.)

Trigger string
--------------
Backfill rows use ``trigger_event='backfill'`` so analytics queries can
filter them in or out. They also carry ``metadata_json={"backfilled_at":
ISO timestamp, "derivation_source": "layer1"}`` for traceability.

Performance
-----------
Walks every non-deleted user once. The per-user state derivation reads
several joined tables; we pre-fetch the aggregates the same way the
Layer-1 aggregate path does. For 3,877 users this is sub-30s on Neon.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional, Set

log = logging.getLogger(__name__)


def _has_any_history(user_id: int) -> bool:
    """Cheap idempotency probe — return True if the user already has at
    least one UserStateHistory row."""
    try:
        from fiesta.markov.models import UserStateHistory

        return (
            UserStateHistory.query
            .filter(UserStateHistory.user_id == user_id)
            .first()
            is not None
        )
    except Exception as exc:
        log.warning(
            "markov.backfill: history probe for user_id=%s failed: %s",
            user_id, exc,
        )
        # Fail-closed: pretend there's already history so we DON'T double-
        # write. Caller can re-run after fixing the underlying issue.
        return True


def _insert_backfill_row(
    user_id: int,
    state: str,
    metadata: Dict[str, Any],
) -> Optional[int]:
    """Insert a single backfill row directly via ORM. Uses
    ``record_state_transition`` semantics minus the previous-state
    lookup (backfill rows always have NULL previous_state_code).

    Returns row id on success, None on failure.
    """
    try:
        from app import db
        from fiesta.markov.models import UserStateHistory
        from fiesta.markov.state_writer import STATE_LABELS

        row = UserStateHistory(
            user_id=user_id,
            state_code=state[:8],
            state_label=STATE_LABELS.get(state, state)[:64],
            previous_state_code=None,
            trigger_event="backfill",
            metadata_json=metadata,
        )
        db.session.add(row)
        db.session.commit()
        return row.id
    except Exception as exc:
        log.warning(
            "markov.backfill: insert for user_id=%s failed: %s",
            user_id, exc,
        )
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        return None


def backfill_all_users(commit: bool = False) -> Dict[str, Any]:
    """Walk every non-deleted user, derive current state, insert ONE
    backfill row per user that lacks history.

    Args:
        commit: When False (default), counts what WOULD be done without
                writing. When True, actually inserts rows.

    Returns:
        Summary dict:
            seen                — total users walked
            already_have_history — skipped because UserStateHistory row exists
            would_seed          — eligible users that WOULD be seeded
            seeded              — rows actually inserted (only when commit=True)
            skipped_no_state    — derivation returned no recognised state
                                  (shouldn't happen — Layer 1 always returns
                                  one of S00..S14 — defensive only)
            errors              — list of (user_id, exception_repr)
            dry_run             — bool, mirrors `commit` for the caller
    """
    summary: Dict[str, Any] = {
        "seen": 0,
        "already_have_history": 0,
        "would_seed": 0,
        "seeded": 0,
        "skipped_no_state": 0,
        "errors": [],
        "dry_run": not commit,
    }

    # Lazy imports — keep module load cheap for tests that exercise
    # backfill_all_users without running it.
    try:
        from app import db
        from models import User
        from fiesta.admin.fiesta_states_routes import (
            derive_state_for_user,
            _current_tax_year_short,
            _current_tax_year_long,
        )
    except Exception as exc:
        log.exception("markov.backfill: bootstrap import failed")
        summary["errors"].append(("bootstrap", repr(exc)))
        return summary

    # Pull the same dependencies the Layer-1 aggregate path uses. Each
    # is independently optional — a missing table doesn't sink the whole
    # backfill.
    try:
        from fiesta.profile.models import FiestaProfile
    except Exception:
        FiestaProfile = None
    try:
        from fiesta.paywall.models import register_models as _register_paywall
        Subscription, _, _ = _register_paywall()
    except Exception:
        Subscription = None
    try:
        from fiesta.submit.models import Submission
    except Exception:
        Submission = None
    try:
        from fiesta.tax.models import Income
    except Exception:
        Income = None
    try:
        from fiesta.earnings.models import Statement, StatementDocType
    except Exception:
        Statement = None
        StatementDocType = None
    try:
        from remittance_models import RemittanceEntry
    except Exception:
        RemittanceEntry = None
    try:
        from fiesta.assets_liabilities.models import (
            AssetEntry,
            LiabilityEntry,
        )
    except Exception:
        AssetEntry = None
        LiabilityEntry = None

    from sqlalchemy import func

    tax_year_short = _current_tax_year_short()
    tax_year_long = _current_tax_year_long()
    now = datetime.utcnow()

    # ----- Pre-fetch aggregates (same recipe as Layer 1) -------------------
    profile_by_user: Dict[int, Any] = {}
    if FiestaProfile is not None:
        try:
            for p in FiestaProfile.query.all():
                profile_by_user[p.user_id] = p
        except Exception as exc:
            summary["errors"].append(("FiestaProfile-fetch", repr(exc)))

    active_subscription_user_ids: Set[int] = set()
    if Subscription is not None:
        try:
            rows = (
                db.session.query(Subscription.user_id)
                .filter(
                    Subscription.status == "active",
                    Subscription.expires_at > now,
                )
                .distinct()
                .all()
            )
            active_subscription_user_ids = {r[0] for r in rows}
        except Exception as exc:
            summary["errors"].append(("Subscription-fetch", repr(exc)))

    submission_by_user: Dict[int, Any] = {}
    if Submission is not None:
        try:
            subs = (
                Submission.query
                .filter(Submission.tax_year == tax_year_long)
                .order_by(Submission.user_id, Submission.updated_at.desc())
                .all()
            )
            for s in subs:
                if s.user_id not in submission_by_user:
                    submission_by_user[s.user_id] = s
        except Exception as exc:
            summary["errors"].append(("Submission-fetch", repr(exc)))

    income_count_by_user: Dict[int, int] = {}
    employment_count_by_user: Dict[int, int] = {}
    if Income is not None:
        try:
            rows = (
                db.session.query(
                    Income.user_id, Income.source_type, func.count(Income.id)
                )
                .filter(Income.tax_year == tax_year_short)
                .group_by(Income.user_id, Income.source_type)
                .all()
            )
            for uid, src, cnt in rows:
                income_count_by_user[uid] = income_count_by_user.get(uid, 0) + int(cnt)
                if src == "employment_lkr":
                    employment_count_by_user[uid] = (
                        employment_count_by_user.get(uid, 0) + int(cnt)
                    )
        except Exception as exc:
            summary["errors"].append(("Income-fetch", repr(exc)))

    remittance_count_by_user: Dict[int, int] = {}
    remittance_with_doc_count_by_user: Dict[int, int] = {}
    if RemittanceEntry is not None:
        try:
            rows = (
                db.session.query(
                    RemittanceEntry.user_id, RemittanceEntry.source_doc_s3_key
                )
                .filter(RemittanceEntry.tax_year == tax_year_short)
                .all()
            )
            for uid, doc_key in rows:
                remittance_count_by_user[uid] = remittance_count_by_user.get(uid, 0) + 1
                if doc_key:
                    remittance_with_doc_count_by_user[uid] = (
                        remittance_with_doc_count_by_user.get(uid, 0) + 1
                    )
        except Exception as exc:
            summary["errors"].append(("RemittanceEntry-fetch", repr(exc)))

    bank_statement_count_by_user: Dict[int, int] = {}
    if Statement is not None and StatementDocType is not None:
        try:
            rows = (
                db.session.query(Statement.user_id, func.count(Statement.id))
                .filter(Statement.doc_type == StatementDocType.BANK_STATEMENT.value)
                .group_by(Statement.user_id)
                .all()
            )
            for uid, cnt in rows:
                bank_statement_count_by_user[uid] = int(cnt)
        except Exception as exc:
            summary["errors"].append(("Statement-fetch", repr(exc)))

    al_count_by_user: Dict[int, int] = {}
    if AssetEntry is not None:
        try:
            rows = (
                db.session.query(AssetEntry.user_id, func.count(AssetEntry.id))
                .filter(AssetEntry.tax_year == tax_year_long)
                .group_by(AssetEntry.user_id)
                .all()
            )
            for uid, cnt in rows:
                al_count_by_user[uid] = al_count_by_user.get(uid, 0) + int(cnt)
        except Exception as exc:
            summary["errors"].append(("AssetEntry-fetch", repr(exc)))
    if LiabilityEntry is not None:
        try:
            rows = (
                db.session.query(LiabilityEntry.user_id, func.count(LiabilityEntry.id))
                .filter(LiabilityEntry.tax_year == tax_year_long)
                .group_by(LiabilityEntry.user_id)
                .all()
            )
            for uid, cnt in rows:
                al_count_by_user[uid] = al_count_by_user.get(uid, 0) + int(cnt)
        except Exception as exc:
            summary["errors"].append(("LiabilityEntry-fetch", repr(exc)))

    # ----- Per-user walk ---------------------------------------------------
    backfill_metadata_template = {
        "backfilled_at": now.isoformat(timespec="seconds"),
        "derivation_source": "layer1",
        "tax_year_short": tax_year_short,
        "tax_year_long": tax_year_long,
    }

    try:
        users_query = User.query.filter(User.deleted_at.is_(None))
        for u in users_query.yield_per(500):
            summary["seen"] += 1
            uid = u.id

            # Skip users who already have history — strict idempotency.
            if _has_any_history(uid):
                summary["already_have_history"] += 1
                continue

            try:
                state = derive_state_for_user(
                    u,
                    profile=profile_by_user.get(uid),
                    submission=submission_by_user.get(uid),
                    income_count=income_count_by_user.get(uid, 0),
                    employment_income_count=employment_count_by_user.get(uid, 0),
                    remittance_count=remittance_count_by_user.get(uid, 0),
                    remittance_with_doc_count=remittance_with_doc_count_by_user.get(uid, 0),
                    bank_statement_count=bank_statement_count_by_user.get(uid, 0),
                    asset_or_liability_count=al_count_by_user.get(uid, 0),
                    has_active_subscription=(uid in active_subscription_user_ids),
                    tax_year_short=tax_year_short,
                )
            except Exception as exc:
                summary["errors"].append((uid, repr(exc)))
                continue

            if not state:
                summary["skipped_no_state"] += 1
                continue

            summary["would_seed"] += 1
            if not commit:
                continue

            row_id = _insert_backfill_row(
                user_id=uid,
                state=state,
                metadata=dict(backfill_metadata_template, derived_state=state),
            )
            if row_id is not None:
                summary["seeded"] += 1
            else:
                summary["errors"].append((uid, "insert returned None"))
    except Exception as exc:
        log.exception("markov.backfill: user walk failed")
        summary["errors"].append(("user-walk", repr(exc)))

    log.info(
        "markov.backfill: %s — seen=%d already=%d would_seed=%d seeded=%d "
        "skipped_no_state=%d errors=%d",
        "DRY-RUN" if not commit else "COMMIT",
        summary["seen"],
        summary["already_have_history"],
        summary["would_seed"],
        summary["seeded"],
        summary["skipped_no_state"],
        len(summary["errors"]),
    )
    return summary


__all__ = ["backfill_all_users"]
