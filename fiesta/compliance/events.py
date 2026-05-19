"""fiesta.compliance.events -- persistence layer for gate-check telemetry.

Every gate_check invocation flows through `log_gate_check` so the analytics
layer can detect:
  - Rule-firing frequency (which rule fires for X% of customers?)
  - False-positive complaint correlation (rule fired, customer complained)
  - Per-screen drop-off (where do customers abandon after a block?)
  - Time-to-resolve (warning fired -> customer fixed it -> elapsed minutes)

Storage strategy
----------------
Primary: postgres table `compliance_gate_events` (created lazily via
`ensure_table` on first call; idempotent CREATE IF NOT EXISTS).
Secondary: append-only JSONL fallback at `logs/compliance_gate_events.jsonl`
when the DB is unavailable (fail-soft, never blocks the gate itself).

Why both: the gate runs in the request path, so a DB hiccup must not break
the customer journey. JSONL replay is fed back into postgres by a nightly
ingestion job (see ops/replay_gate_events.py -- not in scope for X6 v1.0).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("fiesta.compliance.events")

# Fallback log file -- repo-relative; created on first failed DB write.
_FALLBACK_LOG_DIR = Path("logs")
_FALLBACK_LOG_FILE = _FALLBACK_LOG_DIR / "compliance_gate_events.jsonl"


class GateEvent(BaseModel):
    """Single gate-check event for persistence."""

    customer_id: str
    screen_id: str
    action: str
    passed: bool
    warnings_count: int = 0
    blocks_count: int = 0
    rule_ids_fired: list[str] = Field(default_factory=list)
    timestamp_unix: float = Field(default_factory=time.time)
    session_id: str | None = None
    user_agent: str | None = None
    # Trace is large (1 entry per rule); store compactly as JSON.
    reasoning_trace: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# DB layer -- SQLite for v1 (dev + test). Production swap to postgres via
# connection_factory injection; the schema is identical.
# ---------------------------------------------------------------------------
_DB_PATH_ENV = "FIESTA_COMPLIANCE_DB_PATH"
_DEFAULT_DB_PATH = "compliance_gate_events.sqlite3"
_TABLE_CREATED = False


def _get_db_path() -> str:
    return os.environ.get(_DB_PATH_ENV, _DEFAULT_DB_PATH)


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Idempotent CREATE. Safe to call on every write."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS compliance_gate_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT NOT NULL,
            screen_id TEXT NOT NULL,
            action TEXT NOT NULL,
            passed INTEGER NOT NULL,
            warnings_count INTEGER NOT NULL,
            blocks_count INTEGER NOT NULL,
            rule_ids_fired TEXT NOT NULL,
            timestamp_unix REAL NOT NULL,
            session_id TEXT,
            user_agent TEXT,
            reasoning_trace TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_compliance_gate_customer_screen
            ON compliance_gate_events (customer_id, screen_id, timestamp_unix DESC)
        """
    )
    conn.commit()


def _write_jsonl_fallback(event: GateEvent) -> None:
    """Last-resort persistence -- append to JSONL file. Never raises."""
    try:
        _FALLBACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(_FALLBACK_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.model_dump()) + "\n")
    except Exception as exc:  # noqa: BLE001
        # Truly nowhere to write -- log to stderr and move on.
        logger.error("compliance_gate_events JSONL fallback failed: %s", exc)


def log_gate_check(
    customer_id: str,
    screen_id: str,
    action: str,
    gate_result: Any,
    session_id: str | None = None,
    user_agent: str | None = None,
) -> bool:
    """Persist one gate-check event.

    Args:
        customer_id:  Stable customer identifier (fiesta user_id).
        screen_id:    Same value as passed to gate_check.
        action:       Same value as passed to gate_check.
        gate_result:  A GateResult instance (duck-typed -- we only read
                      .passed, .warnings, .blocks, .reasoning_trace).
        session_id:   Optional request/session correlation id.
        user_agent:   Optional UA string for analytics.

    Returns:
        True on persistence success (DB OR fallback). False on total failure
        (logger emits an error but the caller proceeds -- never block the
        customer for a logging issue).

    Idempotency: no dedupe. Multiple identical events are intentionally
    distinct rows; the analytics layer dedupes by (customer_id, screen_id,
    timestamp_unix) when needed.
    """
    try:
        event = GateEvent(
            customer_id=customer_id,
            screen_id=screen_id,
            action=action,
            passed=bool(getattr(gate_result, "passed", True)),
            warnings_count=len(getattr(gate_result, "warnings", []) or []),
            blocks_count=len(getattr(gate_result, "blocks", []) or []),
            rule_ids_fired=[
                w["rule_id"]
                for w in (getattr(gate_result, "warnings", []) or [])
                if isinstance(w, dict) and "rule_id" in w
            ]
            + [
                b["rule_id"]
                for b in (getattr(gate_result, "blocks", []) or [])
                if isinstance(b, dict) and "rule_id" in b
            ],
            session_id=session_id,
            user_agent=user_agent,
            reasoning_trace=list(
                getattr(gate_result, "reasoning_trace", []) or []
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("event construction failed: %s", exc)
        return False

    # Try DB first, fall back to JSONL.
    try:
        global _TABLE_CREATED
        with sqlite3.connect(_get_db_path()) as conn:
            if not _TABLE_CREATED:
                _ensure_table(conn)
                _TABLE_CREATED = True
            conn.execute(
                """
                INSERT INTO compliance_gate_events
                    (customer_id, screen_id, action, passed,
                     warnings_count, blocks_count, rule_ids_fired,
                     timestamp_unix, session_id, user_agent, reasoning_trace)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.customer_id,
                    event.screen_id,
                    event.action,
                    int(event.passed),
                    event.warnings_count,
                    event.blocks_count,
                    json.dumps(event.rule_ids_fired),
                    event.timestamp_unix,
                    event.session_id,
                    event.user_agent,
                    json.dumps(event.reasoning_trace),
                ),
            )
            conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "compliance_gate_events DB write failed (%s); writing JSONL fallback",
            exc,
        )
        _write_jsonl_fallback(event)
        return False


def query_recent_events(
    customer_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Read recent events for one customer. For admin debugging.

    Returns empty list on any error -- never raises.
    """
    try:
        with sqlite3.connect(_get_db_path()) as conn:
            _ensure_table(conn)
            cur = conn.execute(
                """
                SELECT customer_id, screen_id, action, passed,
                       warnings_count, blocks_count, rule_ids_fired,
                       timestamp_unix, session_id, reasoning_trace
                FROM compliance_gate_events
                WHERE customer_id = ?
                ORDER BY timestamp_unix DESC
                LIMIT ?
                """,
                (customer_id, limit),
            )
            cols = [d[0] for d in cur.description]
            out = []
            for row in cur.fetchall():
                rec = dict(zip(cols, row))
                rec["rule_ids_fired"] = json.loads(rec.get("rule_ids_fired") or "[]")
                rec["reasoning_trace"] = json.loads(rec.get("reasoning_trace") or "[]")
                rec["passed"] = bool(rec.get("passed"))
                out.append(rec)
            return out
    except Exception as exc:  # noqa: BLE001
        logger.error("query_recent_events failed: %s", exc)
        return []


__all__ = ["GateEvent", "log_gate_check", "query_recent_events"]
