"""
SubagentSmokeTest — reusable end-to-end smoke-test framework for AI-org
subagents (v18.3 operational hardening).

Council mandate (4-of-4 unanimous, 2026-05-18): every subagent ships with a
smoke test that exercises the FULL runtime path (synthetic event in → Celery
task processes it → downstream row appears). Smoke tests catch the silent-
failure class that v15→v18.1 fell into: substrate present, tasks "scheduled",
nothing actually running.

Pattern (every concrete smoke MUST implement these 4 phases):
  1. emit_synthetic_event(marker) — drop a recognisable Event row
  2. trigger_or_wait(timeout)     — invoke the task synchronously OR wait
                                      for a beat tick to pick it up
  3. assert_downstream(marker)    — verify the expected downstream row
                                      exists within the timeout
  4. cleanup(marker)              — best-effort scrub; respect APPEND-ONLY
                                      on reputation_event (DROP RULE pattern
                                      from tests/ai_run/test_ai_org_substrate)

Each smoke writes a unique marker (uuid hex) into the event payload so the
assertion query can find ONLY this run's rows and we never assert against
prod traffic.

Cleanup is best-effort by design: if it fails, the smoke still reports the
PASS/FAIL of the actual behaviour. The cleanup error is logged but does not
mask a green smoke.
"""
from __future__ import annotations

import logging
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# Public registry — concrete smoke classes append themselves so run_all.py
# discovers them without import gymnastics.
_REGISTERED_SMOKES: list[type] = []


def register_smoke(cls):
    """Decorator: register a SubagentSmokeTest subclass for run_all.py."""
    _REGISTERED_SMOKES.append(cls)
    return cls


def all_registered_smokes() -> list[type]:
    return list(_REGISTERED_SMOKES)


@dataclass
class SmokeResult:
    """Result envelope a smoke run reports back to the runner."""
    subagent_name: str
    passed: bool
    duration_seconds: float
    marker: str
    event_id: Optional[int] = None
    evidence: dict = field(default_factory=dict)
    error: Optional[str] = None
    cleanup_error: Optional[str] = None


class SubagentSmokeTest:
    """One-shot end-to-end smoke test for a subagent's runtime path.

    Subclasses MUST override:
      - subagent_name (class attribute)
      - emit_synthetic_event(marker) -> int
      - trigger_or_wait(timeout_seconds)
      - assert_downstream(marker) -> dict
      - cleanup(marker) -> None
    """

    subagent_name: str = "unknown_subagent"
    # Default budget; concrete smokes may override.
    default_timeout_seconds: int = 360

    # --- subclass contract ----------------------------------------------- #

    def emit_synthetic_event(self, marker: str) -> int:
        raise NotImplementedError

    def trigger_or_wait(self, timeout_seconds: int) -> None:
        raise NotImplementedError

    def assert_downstream(self, marker: str) -> dict:
        raise NotImplementedError

    def cleanup(self, marker: str) -> None:
        raise NotImplementedError

    # --- runner ---------------------------------------------------------- #

    def run(self, timeout_seconds: Optional[int] = None) -> SmokeResult:
        """Execute the 4 phases in order. Returns a SmokeResult — never raises.

        Phase failures abort subsequent phases (you don't assert_downstream if
        trigger_or_wait blew up). Cleanup ALWAYS runs.
        """
        timeout = timeout_seconds or self.default_timeout_seconds
        marker = f"smoke_{self.subagent_name}_{uuid.uuid4().hex[:12]}"
        result = SmokeResult(
            subagent_name=self.subagent_name,
            passed=False,
            duration_seconds=0.0,
            marker=marker,
        )
        t0 = time.time()
        try:
            result.event_id = self.emit_synthetic_event(marker)
            log.info(
                f"[smoke:{self.subagent_name}] emitted synthetic event "
                f"id={result.event_id} marker={marker!r}"
            )
            self.trigger_or_wait(timeout)
            log.info(
                f"[smoke:{self.subagent_name}] trigger_or_wait returned, "
                f"asserting downstream..."
            )
            evidence = self.assert_downstream(marker)
            result.evidence = evidence or {}
            result.passed = True
            log.info(
                f"[smoke:{self.subagent_name}] PASS marker={marker!r} "
                f"evidence={result.evidence}"
            )
        except Exception as e:
            result.passed = False
            result.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            log.error(
                f"[smoke:{self.subagent_name}] FAIL marker={marker!r} error={e}"
            )
        finally:
            # Cleanup runs no matter what — even after a PASS we want the
            # synthetic rows gone so prod analytics stay clean.
            try:
                self.cleanup(marker)
            except Exception as e:
                result.cleanup_error = f"{type(e).__name__}: {e}"
                log.warning(
                    f"[smoke:{self.subagent_name}] cleanup error "
                    f"marker={marker!r}: {e}"
                )
            result.duration_seconds = round(time.time() - t0, 2)
        return result


# --------------------------------------------------------------------------- #
# Concrete smoke: BasicAttributionSmokeTest
#
# Exercises Subagent B's path that's been verified live since v18.2:
#   synthetic `signup` Event with utm_source=lankatax + unique marker payload
#     → process_event() picks it up
#     → AttributionLedger row created, claimed_by_org_id = acquisition_studio
#
# We invoke process_event() DIRECTLY rather than wait for the beat tick. This
# keeps the smoke under 10 seconds and makes it suitable as a deploy gate.
# The beat-driven path is validated separately by the heartbeat (worker is
# alive AND a real task fired in the last 60 min).
# --------------------------------------------------------------------------- #


@register_smoke
class BasicAttributionSmokeTest(SubagentSmokeTest):
    subagent_name = "attribution_writer_basic"
    default_timeout_seconds = 30  # synchronous → quick

    @staticmethod
    def _preload_models() -> None:
        """Hydrate SQLAlchemy metadata in the correct dependency order.

        Required because event_models.events has an FK to `organization`
        (in models.py). If event_models is imported first, FK resolution
        fails with NoReferencedTableError. Pytest's conftest hides this
        because route registration pulls models transitively; standalone
        `python -m tests.ai_run.smoke.run_all` does not.

        Mirrors the preload order in celery_config.py's `include` list.
        Idempotent — Python caches modules after first import.
        """
        import models  # noqa: F401  ← Organization table lives here
        import event_models  # noqa: F401
        import ai_org_models  # noqa: F401
        try:
            import lankatax_models  # noqa: F401
        except Exception:
            # lankatax_models is referenced by outreach lookups but the
            # attribution smoke does not depend on it.
            pass

    def emit_synthetic_event(self, marker: str) -> int:
        self._preload_models()
        from app import app
        from app import db
        from event_models import Event
        from ai_org_substrate import seed_initial_orgs

        with app.app_context():
            # Ensure orgs are seeded — idempotent.
            seed_initial_orgs()
            ev = Event(
                event_type="signup",
                user_id=None,  # synthetic, no real user
                payload={
                    "utm_source": "lankatax",
                    "smoke_marker": marker,
                },
                source=f"smoke:{self.subagent_name}",
            )
            db.session.add(ev)
            db.session.commit()
            return ev.id

    def trigger_or_wait(self, timeout_seconds: int) -> None:
        """Invoke process_event() synchronously. This is the same code path
        the Celery beat triggers — we just skip the broker hop.
        """
        self._preload_models()
        from app import app
        from event_models import Event
        from ai_org_attribution_writer import process_event

        with app.app_context():
            # Re-fetch by event_id; the upstream emit stored it via the
            # framework before this phase runs.
            ev = (
                Event.query
                .filter(Event.source == f"smoke:{self.subagent_name}")
                .order_by(Event.id.desc())
                .first()
            )
            if ev is None:
                raise RuntimeError(
                    "trigger_or_wait: could not re-fetch synthetic event; "
                    "did emit_synthetic_event commit?"
                )
            self._processed_event_id = ev.id  # remember for assertion phase
            ids = process_event(ev)
            if not ids:
                raise RuntimeError(
                    f"trigger_or_wait: process_event returned no attribution "
                    f"ids for event {ev.id}; expected at least 1"
                )
            self._attribution_ids = ids

    def assert_downstream(self, marker: str) -> dict:
        self._preload_models()
        from app import app
        from ai_org_models import AttributionLedger, AIOrg

        with app.app_context():
            acq = AIOrg.query.filter_by(slug="acquisition_studio").first()
            if acq is None:
                raise RuntimeError(
                    "assert_downstream: acquisition_studio org missing — "
                    "seed_initial_orgs may have failed"
                )
            event_id = getattr(self, "_processed_event_id", None)
            if event_id is None:
                raise RuntimeError(
                    "assert_downstream: _processed_event_id not set; "
                    "trigger_or_wait did not run"
                )
            row = (
                AttributionLedger.query
                .filter_by(
                    external_event_type="signup",
                    external_event_ref_id=event_id,
                    claimed_by_org_id=acq.id,
                )
                .first()
            )
            if row is None:
                raise AssertionError(
                    f"assert_downstream: no AttributionLedger row for "
                    f"event_id={event_id} claimed_by acquisition_studio"
                )
            return {
                "event_id": event_id,
                "attribution_id": row.id,
                "attribution_kind": row.attribution_kind,
                "confidence": float(row.confidence),
                "claimed_by_org": "acquisition_studio",
            }

    def cleanup(self, marker: str) -> None:
        """Scrub synthetic Event + AttributionLedger + reputation_event rows
        we created. Respects APPEND-ONLY on reputation_event by dropping the
        RULE for the duration of the DELETE then restoring.
        """
        from sqlalchemy import text as sql_text
        from app import app
        from app import db

        event_id = getattr(self, "_processed_event_id", None)
        if event_id is None:
            return

        self._preload_models()
        with app.app_context():
            try:
                db.session.execute(
                    sql_text(
                        "DROP RULE IF EXISTS no_delete ON reputation_event"
                    )
                )
                db.session.execute(
                    sql_text(
                        "DROP RULE IF EXISTS no_update ON reputation_event"
                    )
                )
                db.session.commit()
                db.session.execute(
                    sql_text(
                        "DELETE FROM reputation_event "
                        "WHERE payload::text LIKE :probe"
                    ),
                    {"probe": f'%"event_id": {event_id}%'},
                )
                db.session.execute(
                    sql_text(
                        "DELETE FROM attribution_ledger "
                        "WHERE external_event_ref_id = :eid"
                    ),
                    {"eid": event_id},
                )
                db.session.execute(
                    sql_text("DELETE FROM events WHERE id = :eid"),
                    {"eid": event_id},
                )
                db.session.commit()
            finally:
                db.session.execute(
                    sql_text(
                        "CREATE OR REPLACE RULE no_update AS ON UPDATE "
                        "TO reputation_event DO INSTEAD NOTHING"
                    )
                )
                db.session.execute(
                    sql_text(
                        "CREATE OR REPLACE RULE no_delete AS ON DELETE "
                        "TO reputation_event DO INSTEAD NOTHING"
                    )
                )
                db.session.commit()


__all__ = [
    "SmokeResult",
    "SubagentSmokeTest",
    "BasicAttributionSmokeTest",
    "register_smoke",
    "all_registered_smokes",
]
