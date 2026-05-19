"""fiesta.compliance -- audit-defensibility helpers for FIESTA.

This package contains compliance utilities that mitigate Risk B from the
2026-05-20 council decision pack (THE_PATH_20260520.md): FIESTA must not be
characterised by IRD as a systemic evasion facilitator. Lanka.tax's operating
license depends on section 195 (Inland Revenue Act No. 24 of 2017) related-party
disclosure being DEFAULT-ON when signal evidence suggests an arrangement is
not at arm's length.

Modules
-------
gate          : Per-screen compliance gate (X6 cross-cutting). Pure functions.
events        : Gate-event persistence (SQLite/postgres + JSONL fallback).
override      : Customer-override hook + consultant-booking handoff.
related_party : Section-195 related-party signal detection (Wave 4 sibling
                branch wave4/related-party-default-on -- imported lazily so
                wave3/x6-compliance-gates can ship without blocking on it).
"""
from __future__ import annotations

# Core X6 gate -- always available on this branch.
from fiesta.compliance.gate import GateResult, gate_check
from fiesta.compliance.events import GateEvent, log_gate_check, query_recent_events
from fiesta.compliance.override import (
    ConsultantBookingHandoff,
    OverrideOutcome,
    get_override_history,
    request_override,
    route_block_to_consultant,
)

# Related-party detector -- optional (sister branch). Re-export when available.
try:  # pragma: no cover -- branch-dependent
    from fiesta.compliance.related_party import (  # noqa: F401
        RelatedPartyResult,
        RelatedPartySignal,
        detect_related_party,
    )
    _RELATED_PARTY_AVAILABLE = True
except ImportError:
    _RELATED_PARTY_AVAILABLE = False

__all__ = [
    # Gate
    "GateResult",
    "gate_check",
    # Events
    "GateEvent",
    "log_gate_check",
    "query_recent_events",
    # Override
    "ConsultantBookingHandoff",
    "OverrideOutcome",
    "get_override_history",
    "request_override",
    "route_block_to_consultant",
]

if _RELATED_PARTY_AVAILABLE:
    __all__ += [
        "RelatedPartyResult",
        "RelatedPartySignal",
        "detect_related_party",
    ]
