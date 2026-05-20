"""
S16 — PCSE Inspector test fixtures.

Re-exports the shared FIESTA fixtures (`app`, `client`, `db_session`)
from tests/remittance/conftest.py and adds a local `admin_user` so the
@admin_required gate is satisfied without colliding with the existing
ai_run admin_user (different email prefix).

All Supabase Postgres calls in `pcse_inspector` are mocked via
`mock_pcse_connection` — no live DB hits.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

# Reuse shared FIESTA fixtures (env loading, sys.path bootstrap, Flask app).
from tests.remittance.conftest import (  # noqa: F401
    app,
    client,
    db_session,
    login_as,
)


# --------------------------------------------------------------------------- #
# Admin user
# --------------------------------------------------------------------------- #
@pytest.fixture
def admin_user(db_session):
    """A user with role='admin', S16-scoped email so cleanup is unambiguous."""
    from datetime import datetime, timedelta
    from models import User
    from werkzeug.security import generate_password_hash

    email = "pytest_s16_pcse_admin@fiesta.local"
    # Defensive: purge any leftover row from a previous failed run.
    User.query.filter(User.email == email).delete()
    db_session.commit()

    u = User(
        email=email,
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name="Pytest S16 Admin",
        role="admin",
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
    )
    db_session.add(u)
    db_session.commit()
    yield u
    User.query.filter(User.id == u.id).delete()
    db_session.commit()


@pytest.fixture
def non_admin_user(db_session):
    """A regular role='user' account — the admin gate must reject them."""
    from datetime import datetime, timedelta
    from models import User
    from werkzeug.security import generate_password_hash

    email = "pytest_s16_pcse_user@fiesta.local"
    User.query.filter(User.email == email).delete()
    db_session.commit()

    u = User(
        email=email,
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name="Pytest S16 User",
        role="user",
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
    )
    db_session.add(u)
    db_session.commit()
    yield u
    User.query.filter(User.id == u.id).delete()
    db_session.commit()


# --------------------------------------------------------------------------- #
# Mock Supabase connection — every test uses this, no live DB hits
# --------------------------------------------------------------------------- #
class FakeCursor:
    """In-memory psycopg2 cursor stand-in.

    Routes queries by substring matching on the SQL string and returns the
    canned rows. `rowcount` is set to len(rows) on the last result.
    """
    def __init__(self, query_map=None):
        # query_map: list of (substring_or_callable, rows_or_callable)
        self.query_map = query_map or []
        self.last_sql = None
        self.last_params = None
        self._rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.last_sql = sql
        self.last_params = params or ()
        # Find a matching rule
        for matcher, result in self.query_map:
            if callable(matcher):
                ok = matcher(sql, params)
            else:
                ok = matcher in sql
            if ok:
                rows = result(sql, params) if callable(result) else result
                self._rows = list(rows)
                self.rowcount = len(self._rows)
                return
        # Default: empty result
        self._rows = []
        self.rowcount = 0

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """Tiny psycopg2.Connection stand-in. Records commits + closes for
    assertion-friendly tests."""
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor
        self.commits = 0
        self.closes = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def close(self):
        self.closes += 1


@pytest.fixture
def fake_cursor():
    return FakeCursor()


@pytest.fixture
def fake_conn(fake_cursor):
    return FakeConn(fake_cursor)


@pytest.fixture
def mock_pcse_connection(monkeypatch, fake_conn):
    """Patch `pcse_inspector._get_pcse_connection` to return our FakeConn.

    Yields the FakeConn so each test can:
      - install query_map entries on fake_conn._cursor.query_map
      - assert on commits / closes / last_sql
    """
    import pcse_inspector
    monkeypatch.setattr(
        pcse_inspector, "_get_pcse_connection", lambda: fake_conn
    )
    yield fake_conn


# --------------------------------------------------------------------------- #
# Sample row builders
# --------------------------------------------------------------------------- #
def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def sample_state_distribution_rows():
    """Rows shape: (state_code, count)."""
    return [
        ("S00", 12),
        ("S01", 30),
        ("S03", 45),
        ("S08", 7),
        ("S14", 3),
    ]


@pytest.fixture
def sample_transition_rows():
    """Shape: (from_state, to_state, action_code, probability, sample_size)."""
    return [
        ("S00", "S01", "F0",  0.62, 240),
        ("S01", "S02", "F1",  0.50, 110),
        ("S03", "S04", "F3",  0.40,  80),
        ("S03", "S03", "F0",  0.10, 200),   # below 0.05? above — keep
        ("S03", "S05", "F3a", 0.02,  30),   # below floor — should be filtered by SQL
    ]


@pytest.fixture
def sample_bucket_rows():
    """Shape matches fetch_active_buckets SQL.
       (client_id, tax_year_id, state_code, days_in_state, ev_lkr, action_code,
        proposal_status, bucket_id, stop_loss_flag)
    """
    return [
        ("C001", "7", "S03", 12.5, 42500.00, "F3.0", "generated", 2, False),
        ("C002", "7", "S01",  1.2, 18000.00, "F1.0", "generated", 1, False),
        ("C003", "6", "S00", 90.0,     0.00, None,    None,       1, True),
    ]


@pytest.fixture
def sample_decision_rows():
    """Shape matches fetch_recent_decisions SQL.
       (id, proposal_uuid, decision, rationale, decided_at, execution_surface,
        tenant, state_snapshot, action_code, ev_lkr, customer_id)
    """
    n = now_utc()
    return [
        (1, "PUUID-aaa", "yes",     "approved",   n,
         "C1", "lanka.tax", {"run_uuid": "RUN-001"}, "F1.0", 22500.0, "C001"),
        (2, "PUUID-bbb", "no",      "declined",   n - timedelta(minutes=1),
         "C2", "lanka.tax", {"run_uuid": "RUN-001"}, "F2.0",  9000.0, "C002"),
        (3, "PUUID-ccc", "defer",   "later",      n - timedelta(minutes=5),
         "C3", "lanka.tax", {}, "F3.0", 14000.0, "C003"),
    ]
