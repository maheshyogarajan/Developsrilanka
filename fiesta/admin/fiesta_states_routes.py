"""
fiesta.admin.fiesta_states_routes — /admin/fiesta-states.

The CEO-facing live Markov tracker view that counts FIESTA users at each of
the 15 v1-admissible states (S00-S14). DISTINCT from /admin/pcse which reads
Salesforce-side Customer__c pipeline data via Supabase: this view derives
state from FIESTA's OWN tables (User, FiestaProfile, RemittanceEntry,
Income, AssetEntry, Submission, etc.).

Mirror of the STATE_LABELS catalogue in pcse_inspector.py.

State catalogue (FIESTA-side derivation rules)
==============================================
S00 Unpaid                         — user exists; no active paywall row
S01 Paid / profile pending         — active paywall row; profile incomplete
                                      (no FiestaProfile row OR profile NIC empty)
S02 Profile complete               — FiestaProfile populated (NIC + city + bank)
S03 Docs collecting                — profile complete; no income evidence yet
                                      (no Income / RemittanceEntry / Statement
                                       row for the current SL tax year)
S04 Income docs received           — at least one income source row exists
                                      (Income or RemittanceEntry)
S05 T10 received                   — employment income (source_type='employment_lkr')
                                      present (proxy for T10 statement)
S06 Bank docs received             — a Statement row exists with
                                      doc_type='bank_statement' (uploaded statement)
S07 Foreign income docs received   — RemittanceEntry rows have source documents
                                      attached (source_doc_s3_key non-null)
S08 All income docs received       — composite: S04 AND every declared
                                      income_source in User.income_sources is
                                      represented by an Income row for the year
S09 A&L received                   — at least one AssetEntry OR LiabilityEntry
                                      row for the current tax year
S10 Computation drafted            — Submission exists in status='preparing'
                                      AND has final_tax_payable_lkr set, OR
                                      Submission in 'final-gate-pending'
S11 Confirmation pending           — Submission in 'awaiting-attestation'
S12 Confirmed                      — Submission in 'attested'
S13 Pre-filing                     — Submission in 'export-generated'
S14 Filed (v1 terminal)            — Submission in 'customer-filed-on-ird'

Notes on lossy derivation
-------------------------
* S05 vs S04 — FIESTA doesn't have a dedicated T10 evidence flag (the
  T10 is the Sri Lankan annual employer statement). We use the presence of
  any ``employment_lkr`` Income row as a proxy. A user with no salary income
  declared will never advance past S04, which matches reality (no T10 needed).
* S07 — we treat "foreign income docs received" as having BOTH a
  RemittanceEntry row AND ``source_doc_s3_key`` populated. Without the
  document upload it's at most S04.
* S08 composite — requires the user's declared ``income_sources`` array to
  map onto present Income rows. If ``income_sources`` is empty, S04 is the
  terminal docs state.
* Tie-breaking — a user can match multiple states. We always assign the
  HIGHEST-numbered state that matches (the natural progression direction).

Caching
-------
60-second per-process cache. Acceptable for a CEO-facing admin dashboard.
The cache is cleared by ``invalidate_cache()`` (no UI affordance yet — wait
for a real cache-stale complaint before adding one).

Cross-references
----------------
* pcse_inspector.STATE_LABELS — canonical state names
* fiesta/submit/models.py    — Submission status vocabulary
* fiesta/paywall/models.py   — Subscription / is_active semantics
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, Flask, jsonify, render_template, request
from sqlalchemy import func

from fiesta.auth.decorators import admin_required

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# State catalogue — sourced from pcse_inspector.STATE_LABELS so the two
# admin views stay in lockstep. We define our OWN copy here (instead of
# importing pcse_inspector) so this route works even if pcse_inspector
# can't connect to Supabase (e.g. PCSE_SUPABASE_DB_URL not set).
# --------------------------------------------------------------------------- #
V1_STATES: Tuple[str, ...] = tuple(f"S{n:02d}" for n in range(0, 15))   # S00-S14
V2_STATES: Tuple[str, ...] = tuple(f"S{n:02d}" for n in range(15, 38))  # S15-S37

STATE_LABELS: Dict[str, str] = {
    "S00": "Unpaid",
    "S01": "Paid / profile pending",
    "S02": "Profile complete",
    "S03": "Docs collecting",
    "S04": "Income docs received",
    "S05": "T10 received",
    "S06": "Bank docs received",
    "S07": "Foreign income docs received",
    "S08": "All income docs received",
    "S09": "A&L received",
    "S10": "Computation drafted",
    "S11": "Confirmation pending",
    "S12": "Confirmed",
    "S13": "Pre-filing",
    "S14": "Filed (v1 terminal)",
}
for _s in V2_STATES:
    STATE_LABELS[_s] = f"v2: {_s}"


# --------------------------------------------------------------------------- #
# Cache: 60-second TTL, module-level dict, thread-safe via a single Lock.
# --------------------------------------------------------------------------- #
_CACHE_TTL_SECONDS = 60
_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {
    "computed_at": 0.0,
    "payload": None,
}


def invalidate_cache() -> None:
    """Clear the cache. Idempotent."""
    with _cache_lock:
        _cache["computed_at"] = 0.0
        _cache["payload"] = None


# --------------------------------------------------------------------------- #
# Tax-year helper — same convention as fiesta.paywall.models.current_sl_tax_year
# but local so we don't pull the paywall import chain into this module's
# fast path.
# --------------------------------------------------------------------------- #
def _current_tax_year_short() -> str:
    """Return the current SL tax year in short form (e.g. '2025-26')."""
    today = datetime.utcnow().date()
    start = today.year if today.month >= 4 else today.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def _current_tax_year_long() -> str:
    """Return the current SL tax year in YYYY/YYYY form (Submission table)."""
    today = datetime.utcnow().date()
    start = today.year if today.month >= 4 else today.year - 1
    return f"{start}/{start + 1}"


def _current_tax_year_slash_short() -> str:
    """Return the current SL tax year in YYYY/YY form (paywall table)."""
    today = datetime.utcnow().date()
    start = today.year if today.month >= 4 else today.year - 1
    return f"{start}/{str(start + 1)[-2:]}"


# --------------------------------------------------------------------------- #
# State-derivation engine
# --------------------------------------------------------------------------- #
# The Submission status -> S-state map. Higher S beats lower S in
# tie-breaking; we walk the dict in order.
_SUBMISSION_STATUS_TO_STATE: Dict[str, str] = {
    "customer-filed-on-ird": "S14",
    "export-generated": "S13",
    "attested": "S12",
    "awaiting-attestation": "S11",
    "final-gate-pending": "S10",
    "preparing": "S10",  # only when final_tax_payable_lkr is non-null
}


def _profile_complete(profile) -> bool:
    """Return True iff the FiestaProfile row exists AND has the minimum
    fields populated for a 'profile complete' classification.

    "Complete" requires NIC + city + bank_account_number. The PCSE
    catalogue's S02 corresponds to the 'Customers_profile_filling_status__c = 5'
    flag in Salesforce; FIESTA's equivalent is "the user has filled enough of
    their FiestaProfile that downstream screens can use it".
    """
    if profile is None:
        return False
    return bool(
        (profile.nic or "").strip()
        and (profile.city or "").strip()
        and (profile.bank_account_number or "").strip()
    )


def _has_active_subscription(user_id: int, Subscription) -> bool:
    """Return True iff the user has any active paywall_subscription row.

    Active = status='active' AND now < expires_at. Mirrors
    Subscription.is_active (property) but at the query level for the bulk
    aggregate path.
    """
    if Subscription is None:
        return False
    now = datetime.utcnow()
    return (
        Subscription.query
        .filter(
            Subscription.user_id == user_id,
            Subscription.status == "active",
            Subscription.expires_at > now,
        )
        .count() > 0
    )


def derive_state_for_user(
    user,
    *,
    profile=None,
    submission=None,
    income_count: int = 0,
    employment_income_count: int = 0,
    remittance_count: int = 0,
    remittance_with_doc_count: int = 0,
    bank_statement_count: int = 0,
    asset_or_liability_count: int = 0,
    has_active_subscription: bool = False,
    tax_year_short: Optional[str] = None,
) -> str:
    """Pure function: given a user's bag of facts, return the S-state code.

    All inputs are pre-fetched COUNT/EXISTS results so this function can be
    unit-tested without a DB. The aggregate path computes the inputs once and
    invokes this per-user.

    Tie-breaking: walk states from highest (S14) to lowest (S00) and return
    the first match. This is the natural-progression direction: a user who
    qualifies for S12 also qualifies for S04 et al — we want to report the
    furthest progression.
    """
    # Submission-driven states (S10-S14)
    if submission is not None:
        status = (submission.status or "").lower()
        if status in _SUBMISSION_STATUS_TO_STATE:
            mapped = _SUBMISSION_STATUS_TO_STATE[status]
            # S10 special-case: 'preparing' only counts if a tax bill amount
            # has been snapshotted. Otherwise the user is in some earlier
            # state and just happens to have a Submission row from an
            # abandoned attempt.
            if status == "preparing":
                if getattr(submission, "final_tax_payable_lkr", None) is not None:
                    return "S10"
                # else fall through to upstream classification
            else:
                return mapped

    # S09 — A&L (assets or liabilities) for the current tax year
    if asset_or_liability_count > 0:
        return "S09"

    # S08 — composite "all income docs received"
    # Defined as: at least one Income row AND the user's declared
    # income_sources array is non-empty AND every declared source has at
    # least one Income row for the year. We can't fully check the last
    # condition cheaply from a single COUNT, so the bulk path passes
    # income_count as a coarse signal. Treat S08 as: declared income_sources
    # is non-empty AND income_count >= len(income_sources).
    declared_sources = list(getattr(user, "income_sources", None) or [])
    if (
        income_count > 0
        and declared_sources
        and income_count >= len(declared_sources)
    ):
        return "S08"

    # S07 — foreign income docs received (RemittanceEntry with attached doc)
    if remittance_with_doc_count > 0:
        return "S07"

    # S06 — bank statement uploaded (any Statement of doc_type=bank_statement)
    if bank_statement_count > 0:
        return "S06"

    # S05 — T10 received (proxy: any employment_lkr Income row exists)
    if employment_income_count > 0:
        return "S05"

    # S04 — at least one income source row (Income or RemittanceEntry)
    if income_count > 0 or remittance_count > 0:
        return "S04"

    # S03 / S02 / S01 / S00 — profile + payment progression
    pc = _profile_complete(profile)
    if pc:
        # Profile complete but no income evidence yet
        return "S03"
    if has_active_subscription:
        return "S01"
    return "S00"


def _compute_state_distribution() -> Dict[str, Any]:
    """Build the {state -> count} aggregate over all FIESTA users.

    Returns a payload dict ready for the template/JSON serialiser:

        {
            "computed_at_iso": str,
            "tax_year_short": "2025-26",
            "tax_year_long": "2025/2026",
            "tax_year_slash_short": "2025/26",
            "total_users": int,
            "state_distribution": {"S00": n, ..., "S14": n},
            "state_distribution_pct": {"S00": float, ..., "S14": float},
            "v1_states": [...],
            "state_labels": {...},
            "errors": [...],   # non-fatal errors per source table
        }
    """
    errors: List[Dict[str, str]] = []
    distribution: Dict[str, int] = {s: 0 for s in V1_STATES}

    # Lazy imports inside function so test runs that monkey-patch the DB
    # don't trigger heavy import side-effects at module load time.
    try:
        from app import db
        from models import User
    except Exception as exc:
        log.exception("fiesta_states: cannot import db/User")
        errors.append({"section": "import", "error": str(exc)})
        return {
            "computed_at_iso": datetime.utcnow().isoformat(timespec="seconds"),
            "tax_year_short": _current_tax_year_short(),
            "tax_year_long": _current_tax_year_long(),
            "tax_year_slash_short": _current_tax_year_slash_short(),
            "total_users": 0,
            "state_distribution": distribution,
            "state_distribution_pct": {s: 0.0 for s in V1_STATES},
            "v1_states": list(V1_STATES),
            "state_labels": STATE_LABELS,
            "errors": errors,
        }

    # Pull every other dependency lazily and tolerantly — any one of these
    # being unavailable should NOT zero out the whole page.
    try:
        from fiesta.profile.models import FiestaProfile
    except Exception as exc:
        FiestaProfile = None
        errors.append({"section": "FiestaProfile", "error": str(exc)})

    try:
        from fiesta.paywall.models import register_models as _register_paywall
        _Subscription, _, _ = _register_paywall()
        Subscription = _Subscription
    except Exception as exc:
        Subscription = None
        errors.append({"section": "Subscription", "error": str(exc)})

    try:
        from fiesta.submit.models import Submission
    except Exception as exc:
        Submission = None
        errors.append({"section": "Submission", "error": str(exc)})

    try:
        from fiesta.tax.models import Income
    except Exception as exc:
        Income = None
        errors.append({"section": "Income", "error": str(exc)})

    try:
        from fiesta.earnings.models import Statement, StatementDocType
    except Exception as exc:
        Statement = None
        StatementDocType = None
        errors.append({"section": "Statement", "error": str(exc)})

    try:
        from remittance_models import RemittanceEntry
    except Exception as exc:
        RemittanceEntry = None
        errors.append({"section": "RemittanceEntry", "error": str(exc)})

    try:
        from fiesta.assets_liabilities.models import AssetEntry, LiabilityEntry
    except Exception as exc:
        AssetEntry = None
        LiabilityEntry = None
        errors.append({"section": "AssetEntry/LiabilityEntry", "error": str(exc)})

    tax_year_short = _current_tax_year_short()
    tax_year_long = _current_tax_year_long()
    now = datetime.utcnow()

    # --------------------------------------------------------------------- #
    # Pre-fetch aggregates per-user so we don't N+1 over the user table.
    # Each dict maps user_id -> count (or bool).
    # --------------------------------------------------------------------- #
    profile_by_user: Dict[int, Any] = {}
    if FiestaProfile is not None:
        try:
            for p in FiestaProfile.query.all():
                profile_by_user[p.user_id] = p
        except Exception as exc:
            errors.append({"section": "FiestaProfile-fetch", "error": str(exc)})

    active_subscription_user_ids: set = set()
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
            errors.append({"section": "Subscription-fetch", "error": str(exc)})

    submission_by_user: Dict[int, Any] = {}
    if Submission is not None:
        try:
            # Pick the latest Submission per (user, tax_year_long) — we want
            # the *current* in-flight filing attempt, not historical ones.
            subs = (
                Submission.query
                .filter(Submission.tax_year == tax_year_long)
                .order_by(Submission.user_id, Submission.updated_at.desc())
                .all()
            )
            # First row per user wins (since ordered by updated_at DESC).
            for s in subs:
                if s.user_id not in submission_by_user:
                    submission_by_user[s.user_id] = s
        except Exception as exc:
            errors.append({"section": "Submission-fetch", "error": str(exc)})

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
            errors.append({"section": "Income-fetch", "error": str(exc)})

    remittance_count_by_user: Dict[int, int] = {}
    remittance_with_doc_count_by_user: Dict[int, int] = {}
    if RemittanceEntry is not None:
        try:
            rows = (
                db.session.query(RemittanceEntry.user_id, RemittanceEntry.source_doc_s3_key)
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
            errors.append({"section": "RemittanceEntry-fetch", "error": str(exc)})

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
            errors.append({"section": "Statement-fetch", "error": str(exc)})

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
            errors.append({"section": "AssetEntry-fetch", "error": str(exc)})
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
            errors.append({"section": "LiabilityEntry-fetch", "error": str(exc)})

    # --------------------------------------------------------------------- #
    # Walk every non-deleted user and assign a state.
    # --------------------------------------------------------------------- #
    total = 0
    try:
        # Filter out soft-deleted users (User.deleted_at IS NULL)
        users_query = User.query.filter(
            (User.deleted_at.is_(None))
        )
        for u in users_query.yield_per(500):
            total += 1
            uid = u.id
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
            if state in distribution:
                distribution[state] += 1
            else:
                # Shouldn't happen — derive_state_for_user always returns v1
                # but be defensive (a future v2 expansion might).
                distribution.setdefault(state, 0)
                distribution[state] += 1
    except Exception as exc:
        log.exception("fiesta_states: aggregate walk failed")
        errors.append({"section": "user-walk", "error": str(exc)})

    # Percentages
    pct: Dict[str, float] = {}
    for s in V1_STATES:
        if total > 0:
            pct[s] = round(distribution.get(s, 0) * 100.0 / total, 1)
        else:
            pct[s] = 0.0

    return {
        "computed_at_iso": datetime.utcnow().isoformat(timespec="seconds"),
        "tax_year_short": tax_year_short,
        "tax_year_long": tax_year_long,
        "tax_year_slash_short": _current_tax_year_slash_short(),
        "total_users": total,
        "state_distribution": distribution,
        "state_distribution_pct": pct,
        "v1_states": list(V1_STATES),
        "state_labels": STATE_LABELS,
        "errors": errors,
    }


def get_state_distribution(use_cache: bool = True) -> Dict[str, Any]:
    """Public reader. Returns a cached payload (60s TTL) by default.

    Passing ``use_cache=False`` forces a fresh compute (used by tests and the
    JSON ``?nocache=1`` endpoint).
    """
    if not use_cache:
        return _compute_state_distribution()

    now = time.time()
    with _cache_lock:
        if (
            _cache["payload"] is not None
            and (now - _cache["computed_at"]) < _CACHE_TTL_SECONDS
        ):
            return _cache["payload"]
    # Compute outside the lock so other readers don't block on the SQL.
    payload = _compute_state_distribution()
    with _cache_lock:
        _cache["payload"] = payload
        _cache["computed_at"] = time.time()
    return payload


# --------------------------------------------------------------------------- #
# Blueprint + route
# --------------------------------------------------------------------------- #
fiesta_states_bp = Blueprint(
    "fiesta_states",
    __name__,
    url_prefix="/admin/fiesta-states",
    template_folder="../../templates",
)


@fiesta_states_bp.route("", methods=["GET"])
@fiesta_states_bp.route("/", methods=["GET"])
@admin_required
def fiesta_states_view():
    """Render the Markov-tracker admin page.

    Query params:
      * ``?nocache=1`` — bypass the 60s cache for this request
    """
    use_cache = request.args.get("nocache", "").lower() not in ("1", "true", "yes")
    payload = get_state_distribution(use_cache=use_cache)
    return render_template(
        "admin/fiesta_states.html",
        payload=payload,
    )


@fiesta_states_bp.route("/data", methods=["GET"])
@admin_required
def fiesta_states_data():
    """JSON endpoint — same shape as the template payload, no rendering."""
    use_cache = request.args.get("nocache", "").lower() not in ("1", "true", "yes")
    try:
        payload = get_state_distribution(use_cache=use_cache)
        return jsonify({"ok": True, **payload})
    except Exception as exc:
        log.exception("fiesta_states_data error")
        return jsonify({"ok": False, "error": str(exc)}), 500


# --------------------------------------------------------------------------- #
# Public registration hook (called from main.py)
# --------------------------------------------------------------------------- #
def register_routes(app: Flask) -> None:
    """Standard FIESTA blueprint hook. Idempotent."""
    if "fiesta_states" in app.blueprints:
        log.debug("fiesta_states blueprint already registered — skipping.")
        return
    app.register_blueprint(fiesta_states_bp)
    log.info(
        "fiesta_states blueprint registered "
        "(/admin/fiesta-states, /admin/fiesta-states/data)"
    )


__all__ = [
    "fiesta_states_bp",
    "register_routes",
    "get_state_distribution",
    "derive_state_for_user",
    "invalidate_cache",
    "STATE_LABELS",
    "V1_STATES",
]
