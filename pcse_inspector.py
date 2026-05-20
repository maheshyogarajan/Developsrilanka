"""
S16 — Admin PCSE Markov Inspector
=================================

Read/write layer for the FIESTA /admin/pcse page. The PCSE (Per-Client State
Engine) v1.0 lives at `G:/My Drive/CEO OS/working files/_cockpit_pcse/` and
writes to Supabase Postgres. This module is the FIESTA-side reader + the
three control-button writers (pause / resume / halt).

Connection
----------
Uses psycopg2 directly against the Supabase Postgres instance. Connection
string resolution order (first match wins):
    1. PCSE_SUPABASE_DB_URL
    2. SUPABASE_DB_URL
    3. SUPABASE_POSTGRES_URL
    4. Build from SUPABASE_DB_HOST / SUPABASE_DB_USER / SUPABASE_DB_PASSWORD
       / SUPABASE_DB_NAME / SUPABASE_DB_PORT (default 5432)

For TESTS: always patch `pcse_inspector._get_pcse_connection` — do NOT hit
live Supabase.

Spec→schema mapping
-------------------
The build spec referenced tables that don't literally exist; the real schema
(per `G:/My Drive/CEO OS/working files/_cockpit_pcse/schema/001_pcse_v1_tables.sql`)
is mapped as follows:

  spec name                  ->  actual table
  ------------------------------------------------------
  pcse_state_log             ->  pcse_state_history
  pcse_decisions             ->  pcse_decision
  pcse_runs                  ->  pcse_orchestrator_run
  pcse_runs.is_paused        ->  pcse_engine_state.state == 'paused'
  pcse_stop_loss_triggers    ->  pcse_stop_loss_log
  pcse_bucket_assignments    ->  derived from latest pcse_state_history per client
  pcse_ev_calculations       ->  pcse_proposal.ev_lkr (latest per client)
  pcse_eligibility_checks    ->  pcse_proposal.eligible_actions_considered
  pcse_staff_collisions      ->  inferred from pcse_decision.reason_category_*
  pcse_executions            ->  pcse_decision rows with decision='yes' (executed)

States S00-S14 are v1-admissible (rendered SOLID in the SVG).
States S15-S37 are v2+ (rendered DASHED).
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State catalogue (matches pcse_markov.STATE_VECTOR_MAP at v1.0 freeze)
# ---------------------------------------------------------------------------
V1_STATES: Tuple[str, ...] = tuple(f"S{n:02d}" for n in range(0, 15))   # S00-S14
V2_STATES: Tuple[str, ...] = tuple(f"S{n:02d}" for n in range(15, 38))  # S15-S37
ALL_STATES: Tuple[str, ...] = V1_STATES + V2_STATES

# Human labels for the v1-admissible states (sourced from STATE_VECTOR_MAP +
# BUCKET_STATE_DEFINITIONS in pcse_orchestrator.py). v2 states stay
# parenthetically labelled with their raw code; they are dashed and not
# operational in v1.
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

DECISION_OUTCOMES: Tuple[str, ...] = (
    "yes", "no", "edit", "defer", "explain", "alternative",
)

# Color codes consumed by the template / JSON view layer
DECISION_COLORS: Dict[str, str] = {
    "yes":         "success",       # green
    "executed":    "success",
    "dry_run_ok":  "info",          # blue
    "no":          "secondary",     # grey
    "edit":        "primary",       # blue
    "defer":       "warning",       # amber
    "deferred_staff_collision": "warning",
    "explain":     "info",
    "alternative": "primary",
    "blocked_by_stop_loss": "danger",
    "vetoed":      "danger",
    "no_action":   "light",
}

# Engine-state vocabulary as stored in pcse_engine_state.state
ENGINE_STATE_RUNNING = "running"
ENGINE_STATE_PAUSED = "paused"
ENGINE_STATE_HALTED = "halted"

# Halt confirmation text the admin must type
HALT_CONFIRM_TEXT = "HALT"


# ---------------------------------------------------------------------------
# Connection plumbing
# ---------------------------------------------------------------------------
def _build_dsn_from_parts() -> Optional[str]:
    host = os.environ.get("SUPABASE_DB_HOST")
    user = os.environ.get("SUPABASE_DB_USER")
    pw = os.environ.get("SUPABASE_DB_PASSWORD")
    db = os.environ.get("SUPABASE_DB_NAME", "postgres")
    port = os.environ.get("SUPABASE_DB_PORT", "5432")
    if host and user and pw:
        return f"postgresql://{user}:{pw}@{host}:{port}/{db}"
    return None


def _resolve_dsn() -> Optional[str]:
    """Return the first non-empty Supabase Postgres DSN from env, or None."""
    for key in ("PCSE_SUPABASE_DB_URL", "SUPABASE_DB_URL", "SUPABASE_POSTGRES_URL"):
        v = os.environ.get(key)
        if v:
            return v
    return _build_dsn_from_parts()


def _get_pcse_connection():  # pragma: no cover — real DB path, fully mocked in tests
    """Open a psycopg2 connection to Supabase Postgres.

    Patched out wholesale in tests via `unittest.mock.patch`.
    Raises RuntimeError if no DSN is resolvable so the route can render an
    explicit "PCSE DB not configured" panel instead of 500-ing.
    """
    dsn = _resolve_dsn()
    if not dsn:
        raise RuntimeError(
            "PCSE Supabase DSN not configured — set PCSE_SUPABASE_DB_URL "
            "(or SUPABASE_DB_URL) in the environment."
        )
    import psycopg2  # local import — keeps test-time import-free
    return psycopg2.connect(dsn)


def _fetchall(sql: str, params: Iterable[Any] = ()) -> List[Tuple[Any, ...]]:
    """Run a SELECT and return rows. Single-shot connection per query —
    safer than a pool for an admin page hit at low volume."""
    conn = _get_pcse_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return list(cur.fetchall())
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _execute(sql: str, params: Iterable[Any] = ()) -> int:
    """Run an INSERT/UPDATE/DELETE. Returns rowcount."""
    conn = _get_pcse_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rc = cur.rowcount
        conn.commit()
        return rc
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tab 1 — state distribution + transition matrix
# ---------------------------------------------------------------------------
def fetch_state_distribution() -> Dict[str, int]:
    """Return {state_code: client_count}. Latest state per client wins.

    Reads pcse_state_history; counts each (client_id, tax_year_id) at its
    most-recent state. Missing v1 states get a 0 entry; v2 states only
    appear if present.
    """
    sql = """
        WITH ranked AS (
            SELECT
                client_id,
                tax_year_id,
                state_code,
                ROW_NUMBER() OVER (
                    PARTITION BY client_id, tax_year_id
                    ORDER BY inferred_at DESC
                ) AS rn
            FROM pcse_state_history
        )
        SELECT state_code, COUNT(*) AS n
          FROM ranked
         WHERE rn = 1
      GROUP BY state_code;
    """
    rows = _fetchall(sql)
    out: Dict[str, int] = {s: 0 for s in V1_STATES}
    for state_code, n in rows:
        out[state_code] = int(n)
    return out


def fetch_transition_edges(
    revision_id: Optional[int] = None,
    min_probability: float = 0.0,
) -> List[Dict[str, Any]]:
    """Return transition edges from the current pcse_transition_matrix.

    Picks the LATEST revision_id if not supplied. Filters by probability
    floor so the SVG isn't drowned in 0.001 noise.
    """
    if revision_id is None:
        rev_rows = _fetchall(
            "SELECT MAX(revision_id) FROM pcse_transition_matrix"
        )
        if not rev_rows or rev_rows[0][0] is None:
            return []
        revision_id = int(rev_rows[0][0])

    sql = """
        SELECT from_state, to_state, action_code, probability, sample_size
          FROM pcse_transition_matrix
         WHERE revision_id = %s AND probability >= %s
      ORDER BY from_state, probability DESC;
    """
    rows = _fetchall(sql, (revision_id, min_probability))
    return [
        {
            "from": r[0], "to": r[1], "action_code": r[2],
            "probability": float(r[3]), "sample_size": int(r[4]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Tab 2 — active buckets
# ---------------------------------------------------------------------------
def fetch_active_buckets(
    state_filter: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Per-client current snapshot for tab 2.

    Joins:
      - pcse_state_history (latest per client)              -> current_state
      - pcse_proposal      (latest by generated_at)         -> EV + action
      - pcse_client_blackout (active row)                   -> stop_loss_flag
    """
    sql = """
        WITH latest_state AS (
            SELECT DISTINCT ON (client_id, tax_year_id)
                   client_id, tax_year_id, state_code, inferred_at
              FROM pcse_state_history
          ORDER BY client_id, tax_year_id, inferred_at DESC
        ),
        latest_prop AS (
            SELECT DISTINCT ON (customer_id, tax_year_id)
                   customer_id, tax_year_id, action_code, ev_lkr,
                   bucket_id, status AS proposal_status, generated_at
              FROM pcse_proposal
          ORDER BY customer_id, tax_year_id, generated_at DESC
        ),
        blackout AS (
            SELECT client_id, MAX(blackout_until) AS until_dt
              FROM pcse_client_blackout
             WHERE released_at IS NULL
               AND blackout_until >= CURRENT_DATE
          GROUP BY client_id
        )
        SELECT  s.client_id,
                s.tax_year_id,
                s.state_code,
                EXTRACT(EPOCH FROM (now() - s.inferred_at)) / 86400.0
                                                       AS days_in_state,
                p.ev_lkr,
                p.action_code,
                p.proposal_status,
                p.bucket_id,
                CASE WHEN b.until_dt IS NOT NULL THEN TRUE ELSE FALSE END
                                                       AS stop_loss_flag
          FROM latest_state s
     LEFT JOIN latest_prop p
            ON p.customer_id = s.client_id
           AND p.tax_year_id = s.tax_year_id
     LEFT JOIN blackout b
            ON b.client_id = s.client_id
    """
    params: List[Any] = []
    if state_filter:
        sql += " WHERE s.state_code = %s "
        params.append(state_filter)
    sql += " ORDER BY p.ev_lkr DESC NULLS LAST LIMIT %s "
    params.append(limit)

    rows = _fetchall(sql, params)
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            "client_id": r[0],
            "tax_year_id": r[1],
            "current_state": r[2],
            "days_in_state": round(float(r[3] or 0.0), 1),
            "ev_lkr": float(r[4]) if r[4] is not None else None,
            "proposed_action": r[5],
            "eligibility_status": r[6] or "n/a",
            "bucket_id": r[7],
            "stop_loss_flag": bool(r[8]),
        })
    return out


# ---------------------------------------------------------------------------
# Tab 3 — recent decisions
# ---------------------------------------------------------------------------
def fetch_recent_decisions(limit: int = 50) -> List[Dict[str, Any]]:
    """Last N rows from pcse_decision, formatted for tab 3.

    Picks the run_uuid from the decision's state_snapshot JSON when present
    (set by the orchestrator); falls back to '-' when absent.
    """
    sql = """
        SELECT  d.id,
                d.proposal_uuid,
                d.decision,
                COALESCE(d.reason_text, d.reason_category_stated,
                         d.reason_category_inferred, '')         AS rationale,
                d.decided_at,
                d.execution_surface,
                d.tenant,
                d.state_snapshot,
                p.action_code,
                p.ev_lkr,
                p.customer_id
           FROM pcse_decision d
      LEFT JOIN pcse_proposal p ON p.proposal_uuid = d.proposal_uuid
       ORDER BY d.decided_at DESC
          LIMIT %s;
    """
    rows = _fetchall(sql, (limit,))
    out: List[Dict[str, Any]] = []
    for r in rows:
        snapshot = r[7] if isinstance(r[7], dict) else {}
        run_uuid = snapshot.get("run_uuid") if isinstance(snapshot, dict) else None
        decision = r[2]
        out.append({
            "decision_id": r[0],
            "proposal_uuid": r[1],
            "run_uuid": run_uuid or "-",
            "client_id": r[10],
            "decision": decision,
            "rationale": r[3] or "",
            "decided_at": r[4].isoformat() if r[4] else None,
            "execution_surface": r[5],
            "tenant": r[6],
            "action_code": r[8],
            "ev_lkr": float(r[9]) if r[9] is not None else None,
            "color": DECISION_COLORS.get(decision, "secondary"),
        })
    return out


# ---------------------------------------------------------------------------
# Tab 4 — engine state + control writes
# ---------------------------------------------------------------------------
def fetch_engine_state() -> Dict[str, Any]:
    """Return the latest pcse_engine_state row, or a synthesized default."""
    rows = _fetchall(
        "SELECT state, reason, changed_at, changed_by "
        "FROM pcse_engine_state ORDER BY changed_at DESC LIMIT 1"
    )
    if not rows:
        return {
            "state": ENGINE_STATE_RUNNING, "reason": "default",
            "changed_at": None, "changed_by": "system",
        }
    r = rows[0]
    return {
        "state": r[0],
        "reason": r[1],
        "changed_at": r[2].isoformat() if r[2] else None,
        "changed_by": r[3],
    }


def _write_engine_state(state: str, reason: str, changed_by: str) -> int:
    """Insert a new row into pcse_engine_state."""
    return _execute(
        "INSERT INTO pcse_engine_state (state, reason, changed_by) "
        "VALUES (%s, %s, %s)",
        (state, reason, changed_by),
    )


def pause_engine(reason: str = "ceo_pause_via_admin_ui",
                 changed_by: str = "ceo") -> Dict[str, Any]:
    """Sets pcse_engine_state to 'paused'."""
    _write_engine_state(ENGINE_STATE_PAUSED, reason, changed_by)
    return fetch_engine_state()


def resume_engine(reason: str = "ceo_resume_via_admin_ui",
                  changed_by: str = "ceo") -> Dict[str, Any]:
    """Sets pcse_engine_state to 'running'."""
    _write_engine_state(ENGINE_STATE_RUNNING, reason, changed_by)
    return fetch_engine_state()


def halt_engine(reason: str = "manual_halt_via_admin_ui",
                changed_by: str = "ceo") -> Dict[str, Any]:
    """Emergency stop:
      - sets pcse_engine_state to 'halted'
      - writes a pcse_stop_loss_log row (trigger_code='R_MANUAL', reaction='halt')

    The orchestrator + executor read pcse_engine_state at the top of each
    dispatch loop; setting 'halted' is sufficient to stop new work. In-flight
    Step 14 wait/listen subscribers also poll engine state every 2s per
    pcse_orchestrator step 2 contract.
    """
    _write_engine_state(ENGINE_STATE_HALTED, reason, changed_by)
    _execute(
        "INSERT INTO pcse_stop_loss_log "
        "  (trigger_code, window_definition, metric_value, threshold, "
        "   proposals_in_window, reaction) "
        "VALUES (%s, %s, %s, %s, %s::jsonb, %s)",
        ("R_MANUAL", "admin_ui_halt", None, None, "[]", "halt"),
    )
    return fetch_engine_state()


# ---------------------------------------------------------------------------
# SVG state graph
# ---------------------------------------------------------------------------
def build_state_graph_svg(
    state_counts: Dict[str, int],
    edges: List[Dict[str, Any]],
    width: int = 1180,
    height: int = 760,
) -> str:
    """Render the 38-state Markov diagram as an inline SVG string.

    Layout: v1 states (S00-S14) laid out as a 5-row × 3-col grid in the left
    region; v2 states (S15-S37) laid out as a 5-row × ~5-col grid in the
    right region. Edges drawn for transitions whose probability >= 0.05 to
    avoid noise. Node radius = log-scaled bucket count.

    Pure string construction — no external SVG lib needed.
    """
    # Precompute node positions
    positions: Dict[str, Tuple[float, float]] = {}
    pad_left = 60
    v1_cols = 3
    v1_x_step = 130
    v1_y_step = 120
    v1_y_top = 60
    for i, s in enumerate(V1_STATES):
        col = i % v1_cols
        row = i // v1_cols
        positions[s] = (pad_left + col * v1_x_step, v1_y_top + row * v1_y_step)

    v2_x_origin = pad_left + v1_cols * v1_x_step + 80
    v2_cols = 5
    v2_x_step = 110
    v2_y_step = 110
    v2_y_top = 60
    for j, s in enumerate(V2_STATES):
        col = j % v2_cols
        row = j // v2_cols
        positions[s] = (v2_x_origin + col * v2_x_step, v2_y_top + row * v2_y_step)

    # Helper for node radius from count
    def _node_radius(count: int) -> float:
        # log-ish scale: 0 -> 18; 10 -> 22; 100 -> 28; 1000 -> 34
        if count <= 0:
            return 18.0
        import math
        return min(38.0, 18.0 + 5.0 * math.log10(max(1, count) + 1))

    parts: List[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="xMidYMid meet" '
        f'class="pcse-state-graph" role="img" '
        f'aria-label="PCSE Markov state graph">'
    )
    parts.append(
        '<defs>'
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        ' markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#777"/>'
        '</marker>'
        '</defs>'
    )
    # Region backdrops + labels
    parts.append(
        f'<rect x="20" y="20" width="{v2_x_origin - 40}" height="{height - 40}" '
        f'rx="14" ry="14" fill="#f5f9ff" stroke="#cfe1ff" stroke-width="1"/>'
    )
    parts.append(
        f'<rect x="{v2_x_origin - 20}" y="20" width="{width - v2_x_origin}" '
        f'height="{height - 40}" rx="14" ry="14" '
        f'fill="#fafafa" stroke="#e2e2e2" stroke-width="1" '
        f'stroke-dasharray="6,4"/>'
    )
    parts.append(
        f'<text x="{pad_left}" y="40" font-size="13" font-weight="700" '
        f'fill="#0d6efd">v1 admissible (S00-S14, solid)</text>'
    )
    parts.append(
        f'<text x="{v2_x_origin}" y="40" font-size="13" font-weight="700" '
        f'fill="#888" font-style="italic">v2+ deferred (S15-S37, dashed)</text>'
    )

    # Edges
    for e in edges:
        f = e.get("from"); t = e.get("to")
        if f not in positions or t not in positions:
            continue
        p = float(e.get("probability", 0.0))
        if p < 0.05:
            continue
        x1, y1 = positions[f]
        x2, y2 = positions[t]
        stroke_w = max(0.6, min(3.0, p * 3.5))
        opacity = max(0.15, min(0.9, p))
        title = (
            f"{f} → {t} (action {e.get('action_code')}, "
            f"p={p:.2f}, n={e.get('sample_size')})"
        )
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="#777" stroke-width="{stroke_w:.2f}" '
            f'opacity="{opacity:.2f}" marker-end="url(#arrow)">'
            f'<title>{title}</title></line>'
        )

    # Nodes
    for s in ALL_STATES:
        x, y = positions[s]
        count = int(state_counts.get(s, 0))
        r = _node_radius(count)
        is_v1 = s in V1_STATES
        fill = "#dbe9ff" if is_v1 else "#f0f0f0"
        stroke = "#0d6efd" if is_v1 else "#999"
        stroke_dash = "" if is_v1 else ' stroke-dasharray="5,3"'
        label = STATE_LABELS.get(s, s)
        title = f"{s} — {label} (n={count})"
        parts.append(
            f'<g class="pcse-node" data-state="{s}">'
            f'<title>{title}</title>'
            f'<circle cx="{x}" cy="{y}" r="{r:.1f}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="2"{stroke_dash}/>'
            f'<text x="{x}" y="{y + 4}" text-anchor="middle" font-size="11" '
            f'font-weight="700" fill="#222">{s}</text>'
            f'<text x="{x}" y="{y + r + 12}" text-anchor="middle" '
            f'font-size="9.5" fill="#555">n={count}</text>'
            f'</g>'
        )

    parts.append('</svg>')
    return "".join(parts)


# ---------------------------------------------------------------------------
# Convenience composite for the route's GET path
# ---------------------------------------------------------------------------
def build_inspector_payload(
    state_filter: Optional[str] = None,
    decision_limit: int = 50,
    bucket_limit: int = 500,
) -> Dict[str, Any]:
    """Single-call assembly for the route. Each subquery is wrapped in
    try/except so one outage doesn't blank the whole page."""
    payload: Dict[str, Any] = {
        "generated_at": datetime.utcnow().isoformat(),
        "errors": [],
    }
    try:
        payload["state_distribution"] = fetch_state_distribution()
    except Exception as e:
        logger.exception("fetch_state_distribution failed")
        payload["state_distribution"] = {s: 0 for s in V1_STATES}
        payload["errors"].append({"section": "state_distribution", "error": str(e)})

    try:
        payload["transition_edges"] = fetch_transition_edges(min_probability=0.05)
    except Exception as e:
        logger.exception("fetch_transition_edges failed")
        payload["transition_edges"] = []
        payload["errors"].append({"section": "transition_edges", "error": str(e)})

    try:
        payload["active_buckets"] = fetch_active_buckets(
            state_filter=state_filter, limit=bucket_limit,
        )
    except Exception as e:
        logger.exception("fetch_active_buckets failed")
        payload["active_buckets"] = []
        payload["errors"].append({"section": "active_buckets", "error": str(e)})

    try:
        payload["recent_decisions"] = fetch_recent_decisions(limit=decision_limit)
    except Exception as e:
        logger.exception("fetch_recent_decisions failed")
        payload["recent_decisions"] = []
        payload["errors"].append({"section": "recent_decisions", "error": str(e)})

    try:
        payload["engine_state"] = fetch_engine_state()
    except Exception as e:
        logger.exception("fetch_engine_state failed")
        payload["engine_state"] = {
            "state": "unknown", "reason": str(e), "changed_at": None,
            "changed_by": "system",
        }
        payload["errors"].append({"section": "engine_state", "error": str(e)})

    try:
        payload["svg"] = build_state_graph_svg(
            state_counts=payload["state_distribution"],
            edges=payload["transition_edges"],
        )
    except Exception as e:
        logger.exception("build_state_graph_svg failed")
        payload["svg"] = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 80">'
            '<text x="10" y="40" font-size="14" fill="#c00">'
            'SVG render failed</text></svg>'
        )
        payload["errors"].append({"section": "svg", "error": str(e)})

    return payload
