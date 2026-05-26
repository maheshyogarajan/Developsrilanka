"""
fiesta.markov.state_writer — event -> S-state mapping + transition writer.

Two public entry points
-----------------------

``event_to_state(event_name, payload, user_id) -> str | None``
    Pure, side-effect-free mapping from an emitted event name to the
    S-state the user moves INTO. Returns None for events that DO NOT
    represent a state transition (we don't want every event to spam the
    history). Safe to call without a DB.

``record_state_transition(user_id, new_state, trigger, metadata=None) -> int | None``
    Look up the user's most recent ``UserStateHistory`` row, compute
    ``previous_state_code``, INSERT one new row, return its id.
    NO-OP (returns None) if ``new_state == previous_state`` so consecutive
    duplicates don't accumulate.

    Same best-effort contract as ``events.emit``: NEVER raises, NEVER
    blocks the user-facing flow. On any DB failure we roll back, log a
    warning, return None.

Design intent
-------------
* Append-only. No UPDATE/DELETE.
* Dedupe on consecutive same-state (write S03 -> S03 is a no-op).
* No timestamp-window dedupe — we want every legitimate transition.
* No retroactive backfill of previous_state_code on out-of-order writes
  (callers go through events.emit + ThreadPoolExecutor; ordering is
  approximate, not strict). Layer 1 remains the source of truth for
  "current state right now"; Layer 2 is the time-series.

Submission-driven states (S11, S12, S13, S14) are out-of-band: the
Submission status changes inside /submit/* routes don't go through
events.emit (they're status mutations on an existing row). For those
sites, call ``record_state_transition`` DIRECTLY with a sentinel
trigger such as ``submission.attested`` so the time-series still
captures the transition.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# State catalogue — mirrors fiesta.admin.fiesta_states_routes.STATE_LABELS.
# Duplicated here (NOT imported) so the writer has zero coupling to the
# admin route blueprint and works even when the admin blueprint isn't
# registered (test isolation).
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Event-name -> S-state mapping
# --------------------------------------------------------------------------- #
#
# Only events that REPRESENT a Markov state transition appear here.
# Events that DON'T transition (e.g. nudge_sent, support_message_received,
# idea_submitted, payment_failed) return None and are NOT logged to
# user_state_history.
#
# Income-evidence events (remittance_added, bank_statement_uploaded, and
# the new profile_complete / al_completed / tax_bill_computed /
# tax_bill_finalized) map to the appropriate destination state. The
# writer is INTENTIONALLY coarse here — we don't try to distinguish S04
# vs S05 vs S06 vs S07 vs S08 at write time, because that would require
# the writer to re-query the entire user fact bag (which is what Layer 1
# already does). Instead, an income event maps to the LOWEST state it
# guarantees (S04) and the Layer-1 re-derive (run on read) is the
# authoritative classification. The time-series captures "user reached
# at least S04 at time T".
#
# CALLER PATTERN for higher-precision states (S05/S06/S07/S08): pass a
# ``destination_state`` override into ``record_state_transition`` from
# the call site that already knows the answer.
#
_EVENT_STATE_MAP: Dict[str, str] = {
    # S00 — fresh signup, no payment yet
    "signup": "S00",

    # S01 — paid, profile pending. Driven by Stripe webhook.
    "checkout_completed": "S01",

    # S02 — profile complete. Emitted by /fie/profile POST when the
    # minimum required fields (NIC + city + bank) all populated.
    "profile_complete": "S02",

    # S04 — at least one income evidence row. Layer 1 promotes to S05/S06/
    # S07 when conditions match; the time-series records the floor.
    "remittance_added": "S04",
    "bank_statement_uploaded": "S04",

    # S09 — A&L received. Emitted by /fie/al POST.
    "al_completed": "S09",

    # S10 — computation drafted. Emitted the first time the tax-engine
    # returns a non-zero bill for a user.
    "tax_bill_computed": "S10",

    # S12 — bill finalized / "Locked". Emitted by /tax-bill/<yr>/finalize.
    # (S11 "awaiting attestation" is captured separately by the in-route
    # submission-status writer below; S12 ALSO covers the Submission
    # "attested" status.)
    "tax_bill_finalized": "S12",
}


# Optional: in-route Submission status -> state sentinels. These names
# aren't in STANDARD_EVENTS (they're not user-driven funnel events) but
# the writer accepts them so /submit/attest, /submit/export, /submit/
# mark-filed can call record_state_transition with a deterministic
# trigger string. None of these are required for the test matrix.
_SUBMISSION_STATUS_STATE_MAP: Dict[str, str] = {
    "submission.awaiting-attestation": "S11",
    "submission.attested": "S12",
    "submission.export-generated": "S13",
    "submission.customer-filed-on-ird": "S14",
}


def event_to_state(
    event_name: str,
    payload: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
) -> Optional[str]:
    """Map an emitted event name to the destination S-state.

    Returns ``None`` for events that do not represent a state transition
    (the caller should NOT write a history row in that case).

    ``payload`` and ``user_id`` are accepted for forward compatibility
    (some future event might encode the destination state inside the
    payload — e.g. ``checkout_completed`` with ``tier='premium_high'``
    might one day skip S01 and land directly in S02). For now both are
    unused.
    """
    if not event_name:
        return None
    # First try the canonical map; then fall through to the submission-
    # sentinel map so callers can pass either flavour.
    if event_name in _EVENT_STATE_MAP:
        return _EVENT_STATE_MAP[event_name]
    if event_name in _SUBMISSION_STATUS_STATE_MAP:
        return _SUBMISSION_STATUS_STATE_MAP[event_name]
    return None


# --------------------------------------------------------------------------- #
# Writer
# --------------------------------------------------------------------------- #
def _get_previous_state(user_id: int):
    """Return the most recent UserStateHistory row for `user_id` or None.

    Wrapped in try/except so a DB hiccup in the lookup doesn't poison the
    write path — we'd rather write a row with NULL previous_state_code
    (signalling 'no prior history') than not write at all.
    """
    try:
        from fiesta.markov.models import UserStateHistory

        return (
            UserStateHistory.query
            .filter(UserStateHistory.user_id == user_id)
            .order_by(UserStateHistory.created_at.desc(), UserStateHistory.id.desc())
            .first()
        )
    except Exception as exc:
        log.warning(
            "markov: previous-state lookup for user_id=%s failed: %s",
            user_id, exc,
        )
        return None


def record_state_transition(
    user_id: int,
    new_state: str,
    trigger: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Insert one UserStateHistory row for `user_id` moving INTO `new_state`.

    Best-effort:
      - NEVER raises (analytics is observational, not transactional).
      - No-op (returns None) if `new_state == previous_state` (no
        consecutive dupes pollute the time-series).
      - No-op (returns None) if `user_id` or `new_state` is falsy.
      - On DB failure, rolls back the session and returns None.

    Returns the new row id on success, None otherwise.
    """
    if not user_id or not new_state:
        return None

    try:
        from app import db
        from fiesta.markov.models import UserStateHistory

        prior = _get_previous_state(user_id)
        prev_code = prior.state_code if prior is not None else None

        # Dedupe consecutive same-state writes — the time-series captures
        # transitions, not heartbeats.
        if prev_code == new_state:
            return None

        row = UserStateHistory(
            user_id=user_id,
            state_code=new_state[:8],
            state_label=STATE_LABELS.get(new_state, new_state)[:64],
            previous_state_code=(prev_code[:8] if prev_code else None),
            trigger_event=(trigger or "unknown")[:64],
            metadata_json=metadata if isinstance(metadata, dict) else None,
        )
        db.session.add(row)
        db.session.commit()
        return row.id
    except Exception as exc:
        log.warning(
            "markov: record_state_transition(user_id=%s, new_state=%r, "
            "trigger=%r) failed: %s. Caller continues.",
            user_id, new_state, trigger, exc,
        )
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        return None


__all__ = [
    "STATE_LABELS",
    "event_to_state",
    "record_state_transition",
]
