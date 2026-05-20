"""
S16 — Tests for the Admin PCSE Markov Inspector.

Coverage map (22 tests):

  UNIT — pcse_inspector module (in-memory FakeConn, no live DB):
    1.  test_v1_v2_state_partition
    2.  test_state_labels_complete
    3.  test_fetch_state_distribution_returns_dict_with_v1_zeros
    4.  test_fetch_transition_edges_uses_latest_revision
    5.  test_fetch_transition_edges_returns_empty_when_no_revisions
    6.  test_fetch_active_buckets_serialization
    7.  test_fetch_active_buckets_applies_state_filter
    8.  test_fetch_recent_decisions_pulls_run_uuid_from_snapshot
    9.  test_fetch_engine_state_default_when_empty
   10.  test_write_pause_inserts_paused_row
   11.  test_write_resume_inserts_running_row
   12.  test_halt_writes_engine_state_and_stop_loss_log
   13.  test_build_state_graph_svg_contains_all_38_states
   14.  test_build_state_graph_svg_marks_v1_solid_v2_dashed
   15.  test_build_inspector_payload_aggregates_all_sections

  ROUTE — /admin/pcse (admin-gated, JSON handlers):
   16.  test_get_admin_pcse_redirects_when_not_logged_in
   17.  test_get_admin_pcse_forbidden_for_non_admin
   18.  test_get_admin_pcse_renders_for_admin
   19.  test_state_graph_json_endpoint
   20.  test_pause_handler_writes_paused
   21.  test_resume_handler_writes_running
   22.  test_halt_without_confirm_returns_400
   23.  test_halt_with_confirm_writes_stop_loss
   24.  test_halt_handler_rejects_wrong_confirm_text
   25.  test_dsn_resolution_picks_first_env

Total: 25 tests.

Run:
    python -m pytest tests/pcse/test_pcse_inspector.py -v
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tests.pcse.conftest import FakeConn, FakeCursor, now_utc


# =========================================================================== #
#                                UNIT TESTS                                   #
# =========================================================================== #
def test_v1_v2_state_partition():
    import pcse_inspector as pi
    # 15 v1 states, 23 v2 states, 38 total
    assert len(pi.V1_STATES) == 15
    assert len(pi.V2_STATES) == 23
    assert len(pi.ALL_STATES) == 38
    # No overlap
    assert set(pi.V1_STATES).isdisjoint(set(pi.V2_STATES))
    # Boundaries
    assert pi.V1_STATES[0] == "S00"
    assert pi.V1_STATES[-1] == "S14"
    assert pi.V2_STATES[0] == "S15"
    assert pi.V2_STATES[-1] == "S37"


def test_state_labels_complete():
    import pcse_inspector as pi
    for s in pi.ALL_STATES:
        assert s in pi.STATE_LABELS
        assert pi.STATE_LABELS[s], f"empty label for {s}"


def test_fetch_state_distribution_returns_dict_with_v1_zeros(
    mock_pcse_connection, sample_state_distribution_rows,
):
    import pcse_inspector as pi
    mock_pcse_connection._cursor.query_map = [
        ("pcse_state_history", sample_state_distribution_rows),
    ]
    out = pi.fetch_state_distribution()
    # All v1 states must be keys
    for s in pi.V1_STATES:
        assert s in out
    # Values from rows
    assert out["S00"] == 12
    assert out["S03"] == 45
    # State not in rows should be 0
    assert out["S02"] == 0
    # Connection closed
    assert mock_pcse_connection.closes == 1


def test_fetch_transition_edges_uses_latest_revision(
    mock_pcse_connection, sample_transition_rows,
):
    import pcse_inspector as pi
    mock_pcse_connection._cursor.query_map = [
        ("MAX(revision_id)", [(7,)]),
        ("FROM pcse_transition_matrix",
         lambda sql, p: sample_transition_rows if "WHERE revision_id" in sql else []),
    ]
    edges = pi.fetch_transition_edges(min_probability=0.0)
    assert len(edges) == len(sample_transition_rows)
    e0 = edges[0]
    assert e0["from"] == "S00" and e0["to"] == "S01"
    assert e0["action_code"] == "F0"
    assert 0.0 <= e0["probability"] <= 1.0
    assert isinstance(e0["sample_size"], int)


def test_fetch_transition_edges_returns_empty_when_no_revisions(mock_pcse_connection):
    import pcse_inspector as pi
    mock_pcse_connection._cursor.query_map = [
        ("MAX(revision_id)", [(None,)]),
    ]
    assert pi.fetch_transition_edges() == []


def test_fetch_active_buckets_serialization(
    mock_pcse_connection, sample_bucket_rows,
):
    import pcse_inspector as pi
    mock_pcse_connection._cursor.query_map = [
        ("latest_state AS", sample_bucket_rows),
    ]
    rows = pi.fetch_active_buckets()
    assert len(rows) == 3
    r = rows[0]
    assert r["client_id"] == "C001"
    assert r["current_state"] == "S03"
    assert r["ev_lkr"] == 42500.0
    assert r["proposed_action"] == "F3.0"
    assert r["stop_loss_flag"] is False
    # Row with stop_loss True comes through correctly
    blackout = next(x for x in rows if x["client_id"] == "C003")
    assert blackout["stop_loss_flag"] is True
    assert blackout["ev_lkr"] == 0.0


def test_fetch_active_buckets_applies_state_filter(mock_pcse_connection):
    import pcse_inspector as pi
    captured = {}
    def record(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []
    mock_pcse_connection._cursor.query_map = [
        ("latest_state AS", record),
    ]
    pi.fetch_active_buckets(state_filter="S03", limit=42)
    assert "s.state_code = %s" in captured["sql"]
    # Params: (state_filter, limit)
    assert captured["params"][0] == "S03"
    assert captured["params"][-1] == 42


def test_fetch_recent_decisions_pulls_run_uuid_from_snapshot(
    mock_pcse_connection, sample_decision_rows,
):
    import pcse_inspector as pi
    mock_pcse_connection._cursor.query_map = [
        ("FROM pcse_decision", sample_decision_rows),
    ]
    rows = pi.fetch_recent_decisions(limit=5)
    assert len(rows) == 3
    by_uuid = {r["proposal_uuid"]: r for r in rows}
    # Rows 1 and 2 have a run_uuid in snapshot, row 3 doesn't
    assert by_uuid["PUUID-aaa"]["run_uuid"] == "RUN-001"
    assert by_uuid["PUUID-bbb"]["run_uuid"] == "RUN-001"
    assert by_uuid["PUUID-ccc"]["run_uuid"] == "-"
    # Decision colors mapped
    assert by_uuid["PUUID-aaa"]["color"] == "success"
    assert by_uuid["PUUID-bbb"]["color"] == "secondary"
    assert by_uuid["PUUID-ccc"]["color"] == "warning"
    # decided_at is ISO string, EV preserved
    assert by_uuid["PUUID-aaa"]["ev_lkr"] == 22500.0
    assert isinstance(by_uuid["PUUID-aaa"]["decided_at"], str)


def test_fetch_engine_state_default_when_empty(mock_pcse_connection):
    import pcse_inspector as pi
    mock_pcse_connection._cursor.query_map = [
        ("FROM pcse_engine_state", []),
    ]
    s = pi.fetch_engine_state()
    assert s["state"] == pi.ENGINE_STATE_RUNNING
    assert s["changed_by"] == "system"


def test_write_pause_inserts_paused_row(mock_pcse_connection):
    import pcse_inspector as pi
    fetch_engine_state_rows = [
        ("paused", "ceo_pause_via_admin_ui", now_utc(), "admin:42"),
    ]
    captured_inserts = []
    def record_insert(sql, params):
        captured_inserts.append((sql, params))
        return []
    mock_pcse_connection._cursor.query_map = [
        ("INSERT INTO pcse_engine_state", record_insert),
        ("FROM pcse_engine_state", fetch_engine_state_rows),
    ]
    res = pi.pause_engine(changed_by="admin:42")
    assert len(captured_inserts) == 1
    insert_sql, params = captured_inserts[0]
    assert params == ("paused", "ceo_pause_via_admin_ui", "admin:42")
    assert res["state"] == "paused"
    # The INSERT path called commit
    assert mock_pcse_connection.commits >= 1


def test_write_resume_inserts_running_row(mock_pcse_connection):
    import pcse_inspector as pi
    captured = []
    mock_pcse_connection._cursor.query_map = [
        ("INSERT INTO pcse_engine_state",
         lambda sql, p: captured.append(p) or []),
        ("FROM pcse_engine_state",
         [("running", "ceo_resume_via_admin_ui", now_utc(), "admin:42")]),
    ]
    pi.resume_engine(changed_by="admin:42")
    assert captured[0][0] == "running"


def test_halt_writes_engine_state_and_stop_loss_log(mock_pcse_connection):
    import pcse_inspector as pi
    inserts = []
    def record(sql, p):
        inserts.append(("ENGINE_STATE" if "pcse_engine_state" in sql
                         else "STOP_LOSS" if "pcse_stop_loss_log" in sql
                         else "OTHER", p))
        return []
    mock_pcse_connection._cursor.query_map = [
        ("INSERT INTO pcse_engine_state", record),
        ("INSERT INTO pcse_stop_loss_log", record),
        ("FROM pcse_engine_state",
         [("halted", "manual_halt_via_admin_ui", now_utc(), "admin:42")]),
    ]
    res = pi.halt_engine(changed_by="admin:42")
    # Both inserts fired
    kinds = [k for k, _ in inserts]
    assert "ENGINE_STATE" in kinds
    assert "STOP_LOSS" in kinds
    # Engine state row sets 'halted'
    es_row = next(p for k, p in inserts if k == "ENGINE_STATE")
    assert es_row[0] == "halted"
    # Stop-loss row carries R_MANUAL + reaction='halt'
    sl_row = next(p for k, p in inserts if k == "STOP_LOSS")
    assert sl_row[0] == "R_MANUAL"
    assert sl_row[-1] == "halt"
    assert res["state"] == "halted"


def test_build_state_graph_svg_contains_all_38_states():
    import pcse_inspector as pi
    counts = {s: i for i, s in enumerate(pi.ALL_STATES)}
    svg = pi.build_state_graph_svg(counts, edges=[])
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    for s in pi.ALL_STATES:
        assert f'data-state="{s}"' in svg


def test_build_state_graph_svg_marks_v1_solid_v2_dashed():
    import pcse_inspector as pi
    counts = {s: 1 for s in pi.ALL_STATES}
    edges = [
        {"from": "S00", "to": "S01", "action_code": "F0",
         "probability": 0.6, "sample_size": 100},
        # Below the 0.05 floor — should be dropped at draw time
        {"from": "S03", "to": "S05", "action_code": "F3a",
         "probability": 0.01, "sample_size": 10},
    ]
    svg = pi.build_state_graph_svg(counts, edges)
    # v1 region label
    assert "v1 admissible" in svg
    assert "v2+ deferred" in svg
    # v2 nodes use dashed stroke
    # We can't pin to one S?? string, but the marker should appear at least
    # 23 times (one per v2 node).
    assert svg.count('stroke-dasharray="5,3"') >= len(pi.V2_STATES)
    # Edge above threshold drawn; below threshold dropped
    assert "S00" in svg and "S01" in svg
    assert "F3a" not in svg  # dropped


def test_build_inspector_payload_aggregates_all_sections(
    mock_pcse_connection,
    sample_state_distribution_rows,
    sample_transition_rows,
    sample_bucket_rows,
    sample_decision_rows,
):
    import pcse_inspector as pi
    # Matchers are evaluated in order — must be specific enough that the bucket
    # SQL (which contains the substring "pcse_state_history" inside its CTE)
    # doesn't match the state-distribution rule.
    mock_pcse_connection._cursor.query_map = [
        # active buckets — must come BEFORE the broader pcse_state_history rule
        ("latest_state AS", sample_bucket_rows),
        # state distribution
        ("WITH ranked AS", sample_state_distribution_rows),
        # transition matrix latest revision id + edges
        ("MAX(revision_id)", [(11,)]),
        ("FROM pcse_transition_matrix", sample_transition_rows),
        # recent decisions
        ("FROM pcse_decision", sample_decision_rows),
        # engine state
        ("FROM pcse_engine_state",
         [("running", "default", now_utc(), "system")]),
    ]
    out = pi.build_inspector_payload()
    assert "state_distribution" in out
    assert "transition_edges" in out
    assert "active_buckets" in out
    assert "recent_decisions" in out
    assert "engine_state" in out
    assert "svg" in out
    assert out["engine_state"]["state"] == "running"
    assert len(out["recent_decisions"]) == 3
    assert len(out["active_buckets"]) == 3


# =========================================================================== #
#                              ROUTE TESTS                                    #
# =========================================================================== #
# All route tests patch `_get_pcse_connection` so no live Supabase is touched.

def _install_full_query_map(conn):
    """Install a generous query map so any subquery in build_inspector_payload
    returns at least an empty result without raising."""
    # Order matters — more-specific matchers come first.
    conn._cursor.query_map = [
        ("INSERT INTO pcse_engine_state", []),
        ("INSERT INTO pcse_stop_loss_log", []),
        ("latest_state AS", []),
        ("WITH ranked AS", []),
        ("MAX(revision_id)", [(None,)]),
        ("FROM pcse_transition_matrix", []),
        ("FROM pcse_decision", []),
        ("FROM pcse_engine_state",
         [("running", "default", now_utc(), "system")]),
    ]


def test_get_admin_pcse_redirects_when_not_logged_in(client):
    # No login — admin_required redirects to /login
    resp = client.get("/admin/pcse", follow_redirects=False)
    assert resp.status_code in (301, 302)
    location = resp.headers.get("Location", "")
    assert "login" in location.lower()


def test_get_admin_pcse_forbidden_for_non_admin(
    client, non_admin_user, mock_pcse_connection,
):
    from tests.remittance.conftest import login_as
    _install_full_query_map(mock_pcse_connection)
    login_as(client, non_admin_user)
    resp = client.get("/admin/pcse", follow_redirects=False)
    # The current decorator flashes + redirects to index for non-admin
    assert resp.status_code in (301, 302)
    assert "pcse" not in resp.headers.get("Location", "").lower()


def test_get_admin_pcse_renders_for_admin(
    client, admin_user, mock_pcse_connection,
):
    from tests.remittance.conftest import login_as
    _install_full_query_map(mock_pcse_connection)
    login_as(client, admin_user)
    resp = client.get("/admin/pcse")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Page chrome
    assert "PCSE Markov Inspector" in body
    # All four tab buttons present
    assert "State graph" in body
    assert "Active buckets" in body
    assert "Recent decisions" in body
    assert "Engine control" in body
    # SVG rendered
    assert "<svg" in body
    # Halt input present
    assert 'id="pcseHaltConfirm"' in body
    # State distribution table mentions S00-S14
    for s in ("S00", "S07", "S14"):
        assert s in body


def test_state_graph_json_endpoint(client, admin_user, mock_pcse_connection):
    from tests.remittance.conftest import login_as
    mock_pcse_connection._cursor.query_map = [
        ("pcse_state_history", [("S00", 5), ("S03", 9)]),
        ("MAX(revision_id)", [(3,)]),
        ("FROM pcse_transition_matrix",
         [("S00", "S01", "F0", 0.6, 100)]),
    ]
    login_as(client, admin_user)
    resp = client.get("/admin/pcse/data/state-graph")
    assert resp.status_code == 200
    body = json.loads(resp.get_data(as_text=True))
    assert body["ok"] is True
    assert body["state_distribution"]["S00"] == 5
    assert body["state_distribution"]["S03"] == 9
    assert len(body["transition_edges"]) == 1
    assert body["svg"].startswith("<svg")


def test_pause_handler_writes_paused(client, admin_user, mock_pcse_connection):
    from tests.remittance.conftest import login_as
    captured = []
    mock_pcse_connection._cursor.query_map = [
        ("INSERT INTO pcse_engine_state",
         lambda sql, p: captured.append(p) or []),
        ("FROM pcse_engine_state",
         [("paused", "ceo_pause_via_admin_ui", now_utc(), f"admin:{admin_user.id}")]),
    ]
    login_as(client, admin_user)
    resp = client.post(
        "/admin/pcse/control/pause",
        data=json.dumps({"reason": "ceo_pause_via_admin_ui"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = json.loads(resp.get_data(as_text=True))
    assert body["ok"] is True
    assert body["engine_state"]["state"] == "paused"
    # Insert row: (state, reason, changed_by)
    assert captured[0][0] == "paused"
    assert captured[0][2] == f"admin:{admin_user.id}"


def test_resume_handler_writes_running(client, admin_user, mock_pcse_connection):
    from tests.remittance.conftest import login_as
    captured = []
    mock_pcse_connection._cursor.query_map = [
        ("INSERT INTO pcse_engine_state",
         lambda sql, p: captured.append(p) or []),
        ("FROM pcse_engine_state",
         [("running", "ceo_resume_via_admin_ui", now_utc(), f"admin:{admin_user.id}")]),
    ]
    login_as(client, admin_user)
    resp = client.post(
        "/admin/pcse/control/resume",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = json.loads(resp.get_data(as_text=True))
    assert body["ok"] is True
    assert body["engine_state"]["state"] == "running"
    assert captured[0][0] == "running"


def test_halt_without_confirm_returns_400(client, admin_user, mock_pcse_connection):
    from tests.remittance.conftest import login_as
    _install_full_query_map(mock_pcse_connection)
    login_as(client, admin_user)
    resp = client.post(
        "/admin/pcse/control/halt",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    body = json.loads(resp.get_data(as_text=True))
    assert body["ok"] is False
    assert "HALT" in body["error"]


def test_halt_with_confirm_writes_stop_loss(client, admin_user, mock_pcse_connection):
    from tests.remittance.conftest import login_as
    inserts = []
    def record(sql, p):
        kind = ("ENGINE_STATE" if "pcse_engine_state" in sql
                else "STOP_LOSS" if "pcse_stop_loss_log" in sql
                else "OTHER")
        inserts.append((kind, p))
        return []
    mock_pcse_connection._cursor.query_map = [
        ("INSERT INTO pcse_engine_state", record),
        ("INSERT INTO pcse_stop_loss_log", record),
        ("FROM pcse_engine_state",
         [("halted", "manual_halt_via_admin_ui",
           now_utc(), f"admin:{admin_user.id}")]),
    ]
    login_as(client, admin_user)
    resp = client.post(
        "/admin/pcse/control/halt",
        data=json.dumps({"confirm_text": "HALT"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = json.loads(resp.get_data(as_text=True))
    assert body["ok"] is True
    assert body["engine_state"]["state"] == "halted"
    kinds = [k for k, _ in inserts]
    assert "ENGINE_STATE" in kinds
    assert "STOP_LOSS" in kinds
    # Stop-loss row uses R_MANUAL
    sl_params = next(p for k, p in inserts if k == "STOP_LOSS")
    assert sl_params[0] == "R_MANUAL"


def test_halt_handler_rejects_wrong_confirm_text(client, admin_user, mock_pcse_connection):
    from tests.remittance.conftest import login_as
    _install_full_query_map(mock_pcse_connection)
    login_as(client, admin_user)
    resp = client.post(
        "/admin/pcse/control/halt",
        data=json.dumps({"confirm_text": "halt"}),  # lowercase — rejected
        content_type="application/json",
    )
    assert resp.status_code == 400
    body = json.loads(resp.get_data(as_text=True))
    assert body["ok"] is False


def test_dsn_resolution_picks_first_env(monkeypatch):
    import pcse_inspector as pi
    # Clean slate
    for k in (
        "PCSE_SUPABASE_DB_URL", "SUPABASE_DB_URL", "SUPABASE_POSTGRES_URL",
        "SUPABASE_DB_HOST", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD",
        "SUPABASE_DB_NAME", "SUPABASE_DB_PORT",
    ):
        monkeypatch.delenv(k, raising=False)
    assert pi._resolve_dsn() is None

    # 1) Parts only
    monkeypatch.setenv("SUPABASE_DB_HOST", "h.example")
    monkeypatch.setenv("SUPABASE_DB_USER", "u")
    monkeypatch.setenv("SUPABASE_DB_PASSWORD", "p")
    dsn = pi._resolve_dsn()
    assert dsn is not None and "h.example" in dsn and "u:p@" in dsn

    # 2) SUPABASE_DB_URL trumps parts
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://from-env-url")
    assert pi._resolve_dsn() == "postgresql://from-env-url"

    # 3) PCSE_SUPABASE_DB_URL trumps all
    monkeypatch.setenv("PCSE_SUPABASE_DB_URL", "postgresql://pcse-specific")
    assert pi._resolve_dsn() == "postgresql://pcse-specific"
