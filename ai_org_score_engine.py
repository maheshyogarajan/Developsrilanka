"""
AI-Org Score Engine — Subagent C (2026-05-18).

Nightly batch that computes the council-locked AI Social Status Score:

    Score = 0.5 * Economic + 0.3 * HumanImpact + 0.2 * AIReliability

Per-axis pipeline:
  1. Sum reputation_event.magnitude over the last 270 days (3 half-lives ≈
     87.5% mass; older noise discarded) decayed by 90-day half-life:
         decayed_magnitude = magnitude * 0.5^(age_days / 90)
  2. Optional confidence multiplier (Subagent B note #2): only multiplied
     when total_attributions >= 50 AND audited_count >= 50; multiplier =
     attribution_accuracy_pct / 100.
  3. Z-score per axis across all active orgs, then mapped to [0,100] via
         normalised = clip(50 + 12.5 * z, 0, 100)
     (centred at 50; ±4σ saturates). If stddev == 0 (single org or all equal),
     every org gets 50 on that axis — neutral.
  4. Composite = 0.5*economic + 0.3*human + 0.2*reliability.
  5. Band = S (≥80) / A (≥65) / B (≥50) / C (≥35) / D (<35).

Update cadence: nightly Celery beat (03:00 UTC — picked to avoid clashing with
ai_crm@02:00, ai_org_attribution every 5min, ops_sentinel every 5min,
engagement_engine hourly, lankatax_crosssell@07:00). Council line 44:
"Update cadence: nightly batch (not real-time). Real-time would invite
latency-game exploits." Acquisition Studio (Subagent D) MUST NOT assume any
in-session feedback on its own actions — scores update once a day.

Also exposes ScoreDispute model (council mitigation #1, dispute API surface)
and an idempotent _ensure_score_dispute_table() at module import — same
belt-and-braces pattern as ai_org_models._ensure_ai_org_tables and
ai_org_audit_harness._ensure_audit_columns.
"""
import logging
import math
from datetime import datetime, timedelta
from decimal import Decimal, getcontext
from typing import Dict, Optional, Tuple

from app import db
from celery_config import app as celery_app

log = logging.getLogger(__name__)

# Decimal precision wide enough for 18,4-magnitude sums of millions of events
# without rounding drift on the half-life exponent.
getcontext().prec = 28


# --------------------------------------------------------------------------- #
# Constants — council-locked
# --------------------------------------------------------------------------- #

HALF_LIFE_DAYS = Decimal("90")
LOOKBACK_DAYS = 270  # 3 half-lives ≈ 87.5% mass
LN2 = Decimal(str(math.log(2)))

AXIS_ECONOMIC = "economic"
AXIS_HUMAN_IMPACT = "human_impact"
AXIS_AI_RELIABILITY = "ai_reliability"
ALL_AXES = (AXIS_ECONOMIC, AXIS_HUMAN_IMPACT, AXIS_AI_RELIABILITY)

WEIGHTS = {
    AXIS_ECONOMIC: Decimal("0.5"),
    AXIS_HUMAN_IMPACT: Decimal("0.3"),
    AXIS_AI_RELIABILITY: Decimal("0.2"),
}

# Z-score → 0-100 mapping params.
Z_CENTER = Decimal("50")
Z_SCALE = Decimal("12.5")  # ±4σ saturates to 0 / 100
Z_MIN = Decimal("0")
Z_MAX = Decimal("100")

# Band thresholds (council line 42).
BAND_THRESHOLDS = (
    (Decimal("80"), "S"),
    (Decimal("65"), "A"),
    (Decimal("50"), "B"),
    (Decimal("35"), "C"),
)
BAND_FALLBACK = "D"

# Confidence multiplier gates (Subagent B note #2).
CONFIDENCE_GATE_MIN_AUDITS = 50


# --------------------------------------------------------------------------- #
# Decay maths
# --------------------------------------------------------------------------- #

def _apply_decay(magnitude: Decimal, age_days: int) -> Decimal:
    """Apply 90-day half-life decay: magnitude * 0.5^(age_days / 90).

    Implementation uses Decimal arithmetic for precision. Exponent computed via
    Decimal-friendly identity: 0.5^x = exp(-ln(2) * x).
    age_days < 0 (event in the "future" — clock skew) treated as age=0.
    """
    if not isinstance(magnitude, Decimal):
        magnitude = Decimal(str(magnitude))
    if age_days <= 0:
        return magnitude
    age_dec = Decimal(age_days)
    # decay_factor = exp(-LN2 * age / 90)
    exponent = -(LN2 * age_dec / HALF_LIFE_DAYS)
    # Decimal lacks exp(); use float for the transcendental, cast back.
    decay = Decimal(str(math.exp(float(exponent))))
    return magnitude * decay


def band_for_score(score: Decimal) -> str:
    """S/A/B/C/D bands per council line 42."""
    if not isinstance(score, Decimal):
        score = Decimal(str(score))
    for threshold, band in BAND_THRESHOLDS:
        if score >= threshold:
            return band
    return BAND_FALLBACK


# --------------------------------------------------------------------------- #
# Per-axis raw computation
# --------------------------------------------------------------------------- #

def compute_axis_raw(
    ai_org_id: int,
    axis: str,
    as_of: Optional[datetime] = None,
) -> Decimal:
    """Sum decayed magnitudes for (org, axis) over the last LOOKBACK_DAYS days.

    Reverse-scans the (ai_org_id, axis, occurred_at DESC) composite index — see
    ai_org_models.ReputationEvent docstring for the access pattern.

    Per-event attribution_confidence is applied as a per-event multiplier
    (council mitigation #5 — micro-task spam attenuation). The cross-org
    confidence multiplier from audit_metrics is applied later in
    apply_confidence_multiplier_if_eligible.
    """
    from ai_org_models import ReputationEvent
    if as_of is None:
        as_of = datetime.utcnow()
    cutoff = as_of - timedelta(days=LOOKBACK_DAYS)

    total = Decimal("0")
    rows = (
        db.session.query(
            ReputationEvent.magnitude,
            ReputationEvent.attribution_confidence,
            ReputationEvent.occurred_at,
        )
        .filter(ReputationEvent.ai_org_id == ai_org_id)
        .filter(ReputationEvent.axis == axis)
        .filter(ReputationEvent.occurred_at >= cutoff)
        .all()
    )
    for mag, conf, occurred in rows:
        if mag is None or occurred is None:
            continue
        age = (as_of - occurred).days
        per_conf = Decimal(str(conf)) if conf is not None else Decimal("1")
        contribution = _apply_decay(Decimal(str(mag)) * per_conf, age)
        total += contribution
    return total


def compute_org_raw_axes(
    ai_org_id: int,
    as_of: Optional[datetime] = None,
) -> Dict[str, Decimal]:
    """Returns {economic: X, human_impact: Y, ai_reliability: Z} for one org."""
    return {axis: compute_axis_raw(ai_org_id, axis, as_of) for axis in ALL_AXES}


# --------------------------------------------------------------------------- #
# Z-score normalisation across orgs
# --------------------------------------------------------------------------- #

def _mean(values):
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _stddev(values, mu):
    if len(values) < 2:
        return Decimal("0")
    var_sum = sum(((v - mu) * (v - mu) for v in values), Decimal("0"))
    variance = var_sum / Decimal(len(values))
    # Decimal sqrt
    return Decimal(str(math.sqrt(float(variance))))


def z_score_normalize_across_orgs(
    per_org_raw: Dict[int, Dict[str, Decimal]],
) -> Dict[int, Dict[str, Decimal]]:
    """For each axis, compute mean+stddev across orgs; transform each org's
    raw value into a normalised [0,100] score via clip(50 + 12.5*z, 0, 100).

    Edge cases:
      * Empty input → empty output
      * Single org OR stddev == 0 (all-equal) → 50 on that axis for every org
    """
    if not per_org_raw:
        return {}

    out: Dict[int, Dict[str, Decimal]] = {org_id: {} for org_id in per_org_raw}
    org_ids = list(per_org_raw.keys())

    for axis in ALL_AXES:
        vals = [Decimal(str(per_org_raw[oid].get(axis, Decimal("0")))) for oid in org_ids]
        mu = _mean(vals)
        sigma = _stddev(vals, mu)
        if sigma == 0:
            for oid in org_ids:
                out[oid][axis] = Z_CENTER
            continue
        for oid, v in zip(org_ids, vals):
            z = (v - mu) / sigma
            normalised = Z_CENTER + Z_SCALE * z
            if normalised < Z_MIN:
                normalised = Z_MIN
            elif normalised > Z_MAX:
                normalised = Z_MAX
            out[oid][axis] = normalised
    return out


def compute_composite_score(econ: Decimal, human: Decimal, reliab: Decimal) -> Decimal:
    """Composite per council line 35: 0.5*Econ + 0.3*Human + 0.2*Reliability.
    All three inputs expected to be normalised [0,100]."""
    if not isinstance(econ, Decimal):
        econ = Decimal(str(econ))
    if not isinstance(human, Decimal):
        human = Decimal(str(human))
    if not isinstance(reliab, Decimal):
        reliab = Decimal(str(reliab))
    return (
        WEIGHTS[AXIS_ECONOMIC] * econ
        + WEIGHTS[AXIS_HUMAN_IMPACT] * human
        + WEIGHTS[AXIS_AI_RELIABILITY] * reliab
    )


# --------------------------------------------------------------------------- #
# Confidence multiplier (Subagent B handoff)
# --------------------------------------------------------------------------- #

def apply_confidence_multiplier_if_eligible(
    per_org_raw: Dict[int, Dict[str, Decimal]],
) -> Tuple[Dict[int, Dict[str, Decimal]], Dict[str, object]]:
    """Multiply every raw axis value by (attribution_accuracy_pct / 100) if and
    only if total_attributions >= 50 AND (verified+rejected+reassigned) >= 50.

    Returns (possibly-scaled per_org_raw, decision_record_dict) so the
    orchestrator can log what was done.
    """
    decision = {
        "applied": False,
        "reason": None,
        "multiplier": None,
        "audit_metrics": None,
    }
    try:
        from ai_org_audit_harness import audit_metrics
        m = audit_metrics()
        decision["audit_metrics"] = m
        total = int(m.get("total_attributions") or 0)
        audited = (
            int(m.get("verified_count") or 0)
            + int(m.get("rejected_count") or 0)
            + int(m.get("reassigned_count") or 0)
        )
        acc = m.get("attribution_accuracy_pct")
        if total < CONFIDENCE_GATE_MIN_AUDITS:
            decision["reason"] = (
                f"total_attributions={total} < {CONFIDENCE_GATE_MIN_AUDITS} — gate closed"
            )
            return per_org_raw, decision
        if audited < CONFIDENCE_GATE_MIN_AUDITS:
            decision["reason"] = (
                f"audited_count={audited} < {CONFIDENCE_GATE_MIN_AUDITS} — gate closed"
            )
            return per_org_raw, decision
        if acc is None:
            decision["reason"] = "attribution_accuracy_pct is None — gate closed"
            return per_org_raw, decision
        multiplier = Decimal(str(acc)) / Decimal("100")
        decision["applied"] = True
        decision["multiplier"] = float(multiplier)
        decision["reason"] = f"both gates open; multiplier={multiplier}"
        scaled = {}
        for oid, axes in per_org_raw.items():
            scaled[oid] = {a: v * multiplier for a, v in axes.items()}
        return scaled, decision
    except Exception as e:
        log.warning(f"apply_confidence_multiplier_if_eligible failed: {e}")
        decision["reason"] = f"exception: {e}"
        return per_org_raw, decision


# --------------------------------------------------------------------------- #
# Orchestrator: recompute_all_orgs
# --------------------------------------------------------------------------- #

def recompute_all_orgs(as_of: Optional[datetime] = None) -> Dict[str, object]:
    """Load active AiOrg rows, compute raw axes for each, normalise across
    orgs, composite + band, then UPDATE each ai_org row in one commit.

    Returns:
      {
        orgs_scored: int,
        computed_at: ISO timestamp,
        ranges: {economic: (min,max), human_impact: (min,max), ai_reliability: (min,max), composite: (min,max)},
        confidence_decision: {...},
      }
    """
    from ai_org_models import AIOrg

    if as_of is None:
        as_of = datetime.utcnow()

    summary: Dict[str, object] = {
        "orgs_scored": 0,
        "computed_at": as_of.isoformat(),
        "ranges": {},
        "confidence_decision": None,
    }

    try:
        active_orgs = AIOrg.query.filter_by(status="active").all()
        if not active_orgs:
            summary["note"] = "no active orgs"
            return summary

        # 1. Per-org raw axes.
        per_org_raw: Dict[int, Dict[str, Decimal]] = {}
        for org in active_orgs:
            per_org_raw[org.id] = compute_org_raw_axes(org.id, as_of)

        # 2. Confidence multiplier (Subagent B handoff).
        per_org_raw, conf_decision = apply_confidence_multiplier_if_eligible(per_org_raw)
        summary["confidence_decision"] = conf_decision

        # 3. Z-score normalise per axis across orgs.
        normalised = z_score_normalize_across_orgs(per_org_raw)

        # 4. Composite + band, update DB rows.
        composites: Dict[int, Decimal] = {}
        for org in active_orgs:
            axes = normalised.get(org.id, {a: Decimal("0") for a in ALL_AXES})
            econ = axes.get(AXIS_ECONOMIC, Decimal("0"))
            human = axes.get(AXIS_HUMAN_IMPACT, Decimal("0"))
            reliab = axes.get(AXIS_AI_RELIABILITY, Decimal("0"))
            comp = compute_composite_score(econ, human, reliab)
            composites[org.id] = comp

            # Quantize to NUMERIC(5,2) before write to avoid Decimal precision overflow.
            q = Decimal("0.01")
            org.economic_axis = econ.quantize(q)
            org.human_impact_axis = human.quantize(q)
            org.ai_reliability_axis = reliab.quantize(q)
            org.status_score = comp.quantize(q)
            org.status_band = band_for_score(comp)
            org.last_score_computed_at = as_of

        db.session.commit()
        summary["orgs_scored"] = len(active_orgs)

        # Ranges for the orchestrator return value.
        def _range(d):
            if not d:
                return (None, None)
            vals = list(d.values())
            return (float(min(vals)), float(max(vals)))

        for axis in ALL_AXES:
            axis_vals = {oid: normalised[oid][axis] for oid in normalised}
            summary["ranges"][axis] = _range(axis_vals)
        summary["ranges"]["composite"] = _range(composites)

    except Exception as e:
        log.warning(f"recompute_all_orgs failed: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        summary["error"] = str(e)

    return summary


# --------------------------------------------------------------------------- #
# Celery beat entrypoint
# --------------------------------------------------------------------------- #

@celery_app.task(name="ai_org_score_engine.recompute_nightly")
def recompute_nightly() -> Dict[str, object]:
    """Nightly score recompute. Emits `ai_org_scores_recomputed` event with the
    summary so downstream dashboards / ops_sentinel can observe.

    Called by Celery beat at 03:00 UTC (see celery_config.py beat_schedule
    addition).
    """
    log.info("ai_org_score_engine.recompute_nightly: start")
    try:
        from app import app as flask_app
        with flask_app.app_context():
            summary = recompute_all_orgs()
    except Exception as e:
        log.exception(f"recompute_nightly outer exception: {e}")
        return {"error": str(e)}

    # Best-effort event emission — never block the score commit on this.
    try:
        from events import emit as emit_event
        emit_event(
            event_type="ai_org_scores_recomputed",
            user_id=None,
            payload={
                "orgs_scored": summary.get("orgs_scored"),
                "computed_at": summary.get("computed_at"),
                "ranges": summary.get("ranges"),
                "confidence_applied": (
                    summary.get("confidence_decision", {}).get("applied")
                    if isinstance(summary.get("confidence_decision"), dict)
                    else None
                ),
            },
            source="ai_org_score_engine.recompute_nightly",
        )
    except Exception as e:
        log.info(f"ai_org_scores_recomputed event emit skipped: {e}")

    log.info(f"recompute_nightly done: {summary}")
    return summary


# --------------------------------------------------------------------------- #
# ScoreDispute model — council mitigation #1 dispute API surface
# --------------------------------------------------------------------------- #

class ScoreDispute(db.Model):
    """A dispute filed against a status-score computation. Council mitigation
    #1: external orgs can challenge their score via /ai_org/<slug>/dispute.

    Lifecycle: open → under_review → (resolved | dismissed)
    The /dispute endpoint creates rows in 'open'. Strategy Council review
    moves them through subsequent statuses out-of-band (no auto-resolution
    in this engine).
    """
    __tablename__ = "score_dispute"

    id = db.Column(db.Integer, primary_key=True)
    ai_org_id = db.Column(
        db.Integer,
        db.ForeignKey("ai_org.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Loose FK to user.id — left non-FK at DB level to avoid coupling this
    # module to the auth model. Application-layer enforces via login_required.
    filed_by_user_id = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.Text, nullable=False)
    evidence_payload = db.Column(db.JSON, nullable=True)
    status = db.Column(db.String(16), nullable=False, default="open")

    filed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)
    resolution_notes = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f"<ScoreDispute {self.id} org={self.ai_org_id} {self.status}>"


def _ensure_score_dispute_table() -> None:
    """Idempotent CREATE TABLE IF NOT EXISTS for score_dispute. Same belt-and-
    braces pattern as ai_org_models._ensure_ai_org_tables.
    """
    try:
        from sqlalchemy import text as sql_text
        from app import app
        with app.app_context():
            db.session.execute(sql_text("""
                CREATE TABLE IF NOT EXISTS score_dispute (
                    id SERIAL PRIMARY KEY,
                    ai_org_id INTEGER NOT NULL REFERENCES ai_org(id) ON DELETE CASCADE,
                    filed_by_user_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    evidence_payload JSON,
                    status VARCHAR(16) NOT NULL DEFAULT 'open',
                    filed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TIMESTAMP,
                    resolution_notes TEXT
                )
            """))
            db.session.execute(sql_text(
                "CREATE INDEX IF NOT EXISTS ix_score_dispute_org "
                "ON score_dispute (ai_org_id)"
            ))
            db.session.commit()
    except Exception as e:
        log.warning(f"_ensure_score_dispute_table failed: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass


_ensure_score_dispute_table()


__all__ = [
    "AXIS_ECONOMIC",
    "AXIS_HUMAN_IMPACT",
    "AXIS_AI_RELIABILITY",
    "ALL_AXES",
    "HALF_LIFE_DAYS",
    "LOOKBACK_DAYS",
    "_apply_decay",
    "band_for_score",
    "compute_axis_raw",
    "compute_org_raw_axes",
    "z_score_normalize_across_orgs",
    "compute_composite_score",
    "apply_confidence_multiplier_if_eligible",
    "recompute_all_orgs",
    "recompute_nightly",
    "ScoreDispute",
]
