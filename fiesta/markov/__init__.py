"""
fiesta.markov — Layer 2 Markov tracker (event-driven state transition log).

Layer 1 (`/admin/fiesta-states`) derives each user's CURRENT state on demand
from existing FIESTA tables. Layer 2 adds an append-only time-series record
of every state transition so we can compute funnel dwell time, conversion
rates by cohort, and progression velocity.

Modules:
  - models      : ``UserStateHistory`` SQLAlchemy model
  - state_writer: event-name -> S-state mapping + ``record_state_transition``
  - backfill    : one-shot CLI to seed history for the existing user base
  - cli         : Flask CLI registration (``flask markov backfill``)

The writer follows the same best-effort contract as ``events.emit``:
analytics writes MUST NEVER raise or block the user-facing flow.
"""
from __future__ import annotations

__all__ = ["models", "state_writer", "backfill", "cli"]
