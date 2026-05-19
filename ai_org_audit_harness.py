"""
AI-Org Audit Harness — Subagent B (2026-05-18).

The 50-sample human-review tool for AttributionLedger rows. Implements council
mitigation #1 (challengeable scores) + supports productize-trigger criterion 7
(attribution confidence >85%, verified via spot-audit of 50 attributions).

Workflow:
  1. `sample_attributions_for_audit(n=50, days_back=7)` returns a random sample
     of unverified AttributionLedger rows + their source-event summary.
  2. A human reviewer (Strategy Council role) inspects each and calls
     `audit_decision(attribution_id, decision, ...)`:
       * 'confirm'  → verify_attribution() flips verified_at; emits
                      `attribution_verified` reputation event (axis=ai_reliability).
       * 'reject'   → marks audit_status='rejected' on attribution_ledger;
                      emits `rollback` reputation event (axis=ai_reliability,
                      magnitude=-1.0) against the wrongly-claiming org.
       * 'reassign' → reject the original + create a fresh attribution for the
                      correct org with the same evidence_payload.
  3. `audit_metrics()` returns the aggregate confidence — read by Subagent C
     for confidence-multiplier computation on score recompute.

Additive schema:
  Adds `audit_status` VARCHAR(16) + `audit_notes` TEXT to attribution_ledger
  via idempotent ALTER TABLE … ADD COLUMN IF NOT EXISTS. Same belt-and-braces
  pattern as ai_org_models._ensure_ai_org_tables and app._ensure_additive_schema.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Additive schema: audit_status + audit_notes on attribution_ledger.
# --------------------------------------------------------------------------- #

def _ensure_audit_columns() -> None:
    """Idempotent ALTER TABLE ADD COLUMN IF NOT EXISTS for the two new audit
    columns. Safe to call multiple times. Runs at module import.
    """
    try:
        from sqlalchemy import text as sql_text
        from app import app, db
        with app.app_context():
            db.session.execute(sql_text(
                "ALTER TABLE attribution_ledger "
                "ADD COLUMN IF NOT EXISTS audit_status VARCHAR(16)"
            ))
            db.session.execute(sql_text(
                "ALTER TABLE attribution_ledger "
                "ADD COLUMN IF NOT EXISTS audit_notes TEXT"
            ))
            db.session.commit()
    except Exception as e:
        log.warning(f"_ensure_audit_columns failed: {e}")
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


_ensure_audit_columns()


# --------------------------------------------------------------------------- #
# Audit decisions vocabulary.
# --------------------------------------------------------------------------- #

DECISION_CONFIRM = "confirm"
DECISION_REJECT = "reject"
DECISION_REASSIGN = "reassign"
VALID_DECISIONS = {DECISION_CONFIRM, DECISION_REJECT, DECISION_REASSIGN}

AUDIT_STATUS_CONFIRMED = "confirmed"
AUDIT_STATUS_REJECTED = "rejected"
AUDIT_STATUS_REASSIGNED = "reassigned"


# --------------------------------------------------------------------------- #
# Sample for audit.
# --------------------------------------------------------------------------- #

def sample_attributions_for_audit(
    n: int = 50,
    days_back: int = 7,
) -> List[Dict[str, Any]]:
    """Return a random sample of UNVERIFIED, UNAUDITED attribution_ledger rows
    from the last `days_back` days. Each entry includes a thin source-event
    summary so the reviewer doesn't have to cross-query.
    """
    out: List[Dict[str, Any]] = []
    try:
        from sqlalchemy import func, text as sql_text
        from app import db
        from ai_org_models import AttributionLedger, AIOrg

        cutoff = datetime.utcnow() - timedelta(days=days_back)

        # Random sample via ORDER BY random() — fine for n<=200; we expect <=50.
        # WHERE verified_at IS NULL AND (audit_status IS NULL OR audit_status NOT IN ('confirmed','rejected','reassigned')).
        rows = (
            db.session.query(AttributionLedger)
            .filter(AttributionLedger.created_at >= cutoff)
            .filter(AttributionLedger.verified_at.is_(None))
            .filter(sql_text(
                "(audit_status IS NULL OR audit_status NOT IN "
                "('confirmed','rejected','reassigned'))"
            ))
            .order_by(func.random())
            .limit(n)
            .all()
        )

        # Pre-load org slugs in one query.
        org_ids = list({r.claimed_by_org_id for r in rows})
        org_map: Dict[int, str] = {}
        if org_ids:
            for o in AIOrg.query.filter(AIOrg.id.in_(org_ids)).all():
                org_map[o.id] = o.slug

        # Pre-load source events in one query.
        from event_models import Event
        ev_ids = list({r.external_event_ref_id for r in rows})
        ev_map: Dict[int, Dict[str, Any]] = {}
        if ev_ids:
            for e in Event.query.filter(Event.id.in_(ev_ids)).all():
                ev_map[e.id] = {
                    "id": e.id,
                    "event_type": e.event_type,
                    "user_id": e.user_id,
                    "source": e.source,
                    "payload": e.payload,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }

        for r in rows:
            out.append({
                "attribution_id": r.id,
                "external_event_type": r.external_event_type,
                "external_event_ref_id": r.external_event_ref_id,
                "claimed_by_org_id": r.claimed_by_org_id,
                "claimed_by_org_slug": org_map.get(r.claimed_by_org_id),
                "attribution_kind": r.attribution_kind,
                "confidence": float(r.confidence) if r.confidence is not None else None,
                "evidence_payload": r.evidence_payload,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "source_event_summary": ev_map.get(r.external_event_ref_id),
            })
    except Exception as e:
        log.warning(f"sample_attributions_for_audit failed: {e}")

    return out


# --------------------------------------------------------------------------- #
# Audit decision.
# --------------------------------------------------------------------------- #

def audit_decision(
    attribution_id: int,
    decision: str,
    verifier_role_id: int,
    new_org_id_if_reassign: Optional[int] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply a reviewer decision to an AttributionLedger row.

    Returns: {ok: bool, decision, attribution_id, new_attribution_id?, message}
    """
    if decision not in VALID_DECISIONS:
        return {
            "ok": False,
            "decision": decision,
            "attribution_id": attribution_id,
            "message": f"invalid decision {decision!r}; must be one of {VALID_DECISIONS}",
        }
    if decision == DECISION_REASSIGN and not new_org_id_if_reassign:
        return {
            "ok": False,
            "decision": decision,
            "attribution_id": attribution_id,
            "message": "reassign requires new_org_id_if_reassign",
        }

    try:
        from sqlalchemy import text as sql_text
        from app import db
        from ai_org_models import AttributionLedger
        from ai_org_substrate import (
            verify_attribution,
            emit_reputation_event,
            claim_attribution,
        )

        row = AttributionLedger.query.get(attribution_id)
        if row is None:
            return {
                "ok": False,
                "decision": decision,
                "attribution_id": attribution_id,
                "message": "attribution_id not found",
            }

        result: Dict[str, Any] = {
            "ok": True,
            "decision": decision,
            "attribution_id": attribution_id,
        }

        if decision == DECISION_CONFIRM:
            verified = verify_attribution(attribution_id, verifier_role_id)
            if verified is None:
                return {
                    "ok": False,
                    "decision": decision,
                    "attribution_id": attribution_id,
                    "message": "verify_attribution failed",
                }
            # Mark audit_status='confirmed' + notes via raw SQL (skirts the
            # ORM since audit_status isn't on the model — it's an additive col).
            db.session.execute(sql_text(
                "UPDATE attribution_ledger "
                "SET audit_status = :status, audit_notes = :notes "
                "WHERE id = :id"
            ), {"status": AUDIT_STATUS_CONFIRMED, "notes": notes, "id": attribution_id})
            db.session.commit()
            result["message"] = "confirmed + reputation event emitted"
            return result

        if decision == DECISION_REJECT:
            db.session.execute(sql_text(
                "UPDATE attribution_ledger "
                "SET audit_status = :status, audit_notes = :notes "
                "WHERE id = :id"
            ), {"status": AUDIT_STATUS_REJECTED, "notes": notes, "id": attribution_id})
            db.session.commit()
            # Emit rollback reputation event (negative AI reliability signal).
            emit_reputation_event(
                ai_org_id=row.claimed_by_org_id,
                event_type="rollback",
                magnitude=-1.0,
                axis="ai_reliability",
                attribution_confidence=1.0,
                payload={
                    "rejected_attribution_id": attribution_id,
                    "external_event_type": row.external_event_type,
                    "external_event_ref_id": row.external_event_ref_id,
                    "verifier_role_id": verifier_role_id,
                    "reason": notes or "audit_rejected",
                },
            )
            result["message"] = "rejected + rollback reputation event emitted"
            return result

        # DECISION_REASSIGN
        # 1. Mark the original rejected (with reassignment note).
        db.session.execute(sql_text(
            "UPDATE attribution_ledger "
            "SET audit_status = :status, audit_notes = :notes "
            "WHERE id = :id"
        ), {
            "status": AUDIT_STATUS_REASSIGNED,
            "notes": (notes or "") + f" | reassigned to org_id={new_org_id_if_reassign}",
            "id": attribution_id,
        })
        db.session.commit()
        # 2. Emit rollback for the original org.
        emit_reputation_event(
            ai_org_id=row.claimed_by_org_id,
            event_type="rollback",
            magnitude=-1.0,
            axis="ai_reliability",
            attribution_confidence=1.0,
            payload={
                "rejected_attribution_id": attribution_id,
                "reason": "audit_reassigned",
                "reassigned_to_org_id": new_org_id_if_reassign,
            },
        )
        # 3. Create a new attribution for the new org (idempotent — UNIQUE).
        new_attribution = claim_attribution(
            external_event_type=row.external_event_type,
            external_event_ref_id=row.external_event_ref_id,
            claimed_by_org_id=new_org_id_if_reassign,
            attribution_kind=row.attribution_kind,
            confidence=float(row.confidence) if row.confidence is not None else 1.0,
            evidence_payload={
                **(row.evidence_payload or {}),
                "reassigned_from_attribution_id": attribution_id,
                "verifier_role_id": verifier_role_id,
            },
        )
        # 4. Auto-verify the new attribution (the human just decided it).
        if new_attribution is not None:
            verify_attribution(new_attribution.id, verifier_role_id)
            result["new_attribution_id"] = new_attribution.id
        result["message"] = "reassigned: original rejected, new attribution verified"
        return result

    except Exception as e:
        log.warning(f"audit_decision failed: {e}")
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        return {
            "ok": False,
            "decision": decision,
            "attribution_id": attribution_id,
            "message": f"exception: {e}",
        }


# --------------------------------------------------------------------------- #
# Aggregate metrics — Subagent C reads this.
# --------------------------------------------------------------------------- #

def audit_metrics() -> Dict[str, Any]:
    """Aggregate accuracy of attribution. Subagent C's score engine uses this
    as a confidence multiplier (council criterion 7: >85% accuracy required).

    Returns:
      {
        total_attributions,
        verified_count,
        rejected_count,
        reassigned_count,
        unaudited_count,
        attribution_accuracy_pct  # verified / (verified + rejected + reassigned)
      }
    """
    out = {
        "total_attributions": 0,
        "verified_count": 0,
        "rejected_count": 0,
        "reassigned_count": 0,
        "unaudited_count": 0,
        "attribution_accuracy_pct": None,
    }
    try:
        from sqlalchemy import text as sql_text
        from app import db

        row = db.session.execute(sql_text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE audit_status = 'confirmed') AS confirmed,
                COUNT(*) FILTER (WHERE audit_status = 'rejected') AS rejected,
                COUNT(*) FILTER (WHERE audit_status = 'reassigned') AS reassigned,
                COUNT(*) FILTER (WHERE audit_status IS NULL) AS unaudited
            FROM attribution_ledger
        """)).fetchone()
        if row is not None:
            total, confirmed, rejected, reassigned, unaudited = row
            out["total_attributions"] = int(total or 0)
            out["verified_count"] = int(confirmed or 0)
            out["rejected_count"] = int(rejected or 0)
            out["reassigned_count"] = int(reassigned or 0)
            out["unaudited_count"] = int(unaudited or 0)
            denom = (confirmed or 0) + (rejected or 0) + (reassigned or 0)
            if denom > 0:
                out["attribution_accuracy_pct"] = round(
                    100.0 * (confirmed or 0) / denom, 2
                )
    except Exception as e:
        log.warning(f"audit_metrics failed: {e}")
    return out


__all__ = [
    "sample_attributions_for_audit",
    "audit_decision",
    "audit_metrics",
    "DECISION_CONFIRM",
    "DECISION_REJECT",
    "DECISION_REASSIGN",
    "VALID_DECISIONS",
]
