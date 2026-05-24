"""tests.backup.test_pg_backup — coverage for tasks.pg_backup.

Run via:
  cd C:/Users/mahes/fiesta_phase_a/worktrees/tier-d1-f1-backup
  python -m pytest tests/backup/test_pg_backup.py -v

Covers:
  - _load_config: missing-vars error, defaults applied
  - run_backup: dry-run short-circuit
  - run_backup: happy path (pg_dump + boto3 fully mocked)
  - run_backup: monthly snapshot on day-1
  - _prune: keeps newest N, deletes rest
  - key naming determinism

No live Tigris calls. No live pg_dump. boto3 client is monkey-patched.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add repo root to sys.path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tasks import pg_backup as mod  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def good_env(monkeypatch):
    """Set all required env vars to a sane test value."""
    env = {
        "BACKUP_S3_BUCKET": "fiesta-mvp-pg-backups",
        "BACKUP_S3_ENDPOINT_URL": "https://fly.storage.tigris.dev",
        "BACKUP_S3_ACCESS_KEY_ID": "test-key",
        "BACKUP_S3_SECRET_ACCESS_KEY": "test-secret",
        "FLY_PG_DATABASE_URL": "postgres://u:p@h:5432/db",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # Remove any optional that might leak from the harness.
    monkeypatch.delenv("BACKUP_DRY_RUN", raising=False)
    monkeypatch.delenv("BACKUP_RETAIN_DAILY", raising=False)
    monkeypatch.delenv("BACKUP_RETAIN_MONTHLY", raising=False)
    monkeypatch.delenv("BACKUP_S3_REGION", raising=False)
    yield env


# ---------------------------------------------------------------------------
# _load_config
# ---------------------------------------------------------------------------

def test_load_config_missing_var_raises(monkeypatch):
    """Drop one required var → BackupConfigError naming the var."""
    monkeypatch.setenv("BACKUP_S3_BUCKET", "x")
    monkeypatch.setenv("BACKUP_S3_ENDPOINT_URL", "x")
    monkeypatch.setenv("BACKUP_S3_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("BACKUP_S3_SECRET_ACCESS_KEY", "x")
    monkeypatch.delenv("FLY_PG_DATABASE_URL", raising=False)

    with pytest.raises(mod.BackupConfigError) as exc:
        mod._load_config()
    assert "FLY_PG_DATABASE_URL" in str(exc.value)


def test_load_config_applies_defaults(good_env):
    cfg = mod._load_config()
    assert cfg["region"] == "auto"
    assert cfg["retain_daily"] == mod.DEFAULT_RETAIN_DAILY
    assert cfg["retain_monthly"] == mod.DEFAULT_RETAIN_MONTHLY
    assert cfg["dry_run"] is False


def test_load_config_overrides_from_env(good_env, monkeypatch):
    monkeypatch.setenv("BACKUP_RETAIN_DAILY", "30")
    monkeypatch.setenv("BACKUP_RETAIN_MONTHLY", "6")
    monkeypatch.setenv("BACKUP_S3_REGION", "iad")
    monkeypatch.setenv("BACKUP_DRY_RUN", "1")

    cfg = mod._load_config()
    assert cfg["retain_daily"] == 30
    assert cfg["retain_monthly"] == 6
    assert cfg["region"] == "iad"
    assert cfg["dry_run"] is True


# ---------------------------------------------------------------------------
# Key naming
# ---------------------------------------------------------------------------

def test_daily_key_format():
    ts = dt.datetime(2026, 5, 24, 20, 30, 0, tzinfo=dt.timezone.utc)
    assert mod._daily_key(ts) == "daily/fiesta_pg_20260524.pgdump"


def test_monthly_key_format():
    ts = dt.datetime(2026, 5, 1, 20, 30, 0, tzinfo=dt.timezone.utc)
    assert mod._monthly_key(ts) == "monthly/fiesta_pg_202605.pgdump"


# ---------------------------------------------------------------------------
# run_backup — dry-run short-circuit
# ---------------------------------------------------------------------------

def test_run_backup_dry_run_no_calls(good_env, monkeypatch):
    monkeypatch.setenv("BACKUP_DRY_RUN", "1")

    # Spies — should NOT be called in dry-run.
    spy_dump = MagicMock(side_effect=AssertionError("pg_dump should not run in dry-run"))
    spy_s3 = MagicMock(side_effect=AssertionError("S3 client should not build in dry-run"))
    monkeypatch.setattr(mod, "_run_pg_dump", spy_dump)
    monkeypatch.setattr(mod, "_build_s3_client", spy_s3)

    fixed_now = dt.datetime(2026, 5, 24, 20, 30, 0, tzinfo=dt.timezone.utc)
    result = mod.run_backup(now=fixed_now)

    assert result.ok is True
    assert result.dry_run is True
    assert result.daily_key == "daily/fiesta_pg_20260524.pgdump"
    assert result.monthly_key is None  # day != 1
    assert result.bytes_uploaded == 0
    spy_dump.assert_not_called()
    spy_s3.assert_not_called()


def test_run_backup_dry_run_marks_monthly_on_day_1(good_env, monkeypatch):
    monkeypatch.setenv("BACKUP_DRY_RUN", "1")
    fixed_now = dt.datetime(2026, 6, 1, 20, 30, 0, tzinfo=dt.timezone.utc)
    result = mod.run_backup(now=fixed_now)
    assert result.ok is True
    assert result.daily_key == "daily/fiesta_pg_20260601.pgdump"
    assert result.monthly_key == "monthly/fiesta_pg_202606.pgdump"


# ---------------------------------------------------------------------------
# run_backup — happy path with full mocking
# ---------------------------------------------------------------------------

def _make_fake_s3():
    """Mock S3 client; list_objects_v2 paginator returns empty (no prune)."""
    s3 = MagicMock(name="s3_client")
    paginator = MagicMock(name="paginator")
    paginator.paginate.return_value = [{"Contents": []}]
    s3.get_paginator.return_value = paginator
    return s3


def test_run_backup_happy_path(good_env, monkeypatch, tmp_path):
    """pg_dump + upload + prune all happen exactly once on a normal day."""
    # Mock pg_dump → write a fake 1024-byte file at the path the caller chose.
    def fake_pg_dump(dsn, out_path):
        Path(out_path).write_bytes(b"X" * 1024)
        return 1024
    monkeypatch.setattr(mod, "_run_pg_dump", fake_pg_dump)

    fake_s3 = _make_fake_s3()
    monkeypatch.setattr(mod, "_build_s3_client", lambda cfg: fake_s3)

    fixed_now = dt.datetime(2026, 5, 24, 20, 30, 0, tzinfo=dt.timezone.utc)
    result = mod.run_backup(now=fixed_now)

    assert result.ok is True, result.error
    assert result.daily_key == "daily/fiesta_pg_20260524.pgdump"
    assert result.monthly_key is None
    assert result.bytes_uploaded == 1024
    assert result.bucket == "fiesta-mvp-pg-backups"
    assert result.endpoint == "https://fly.storage.tigris.dev"
    # Exactly one upload call (no monthly on day 24).
    assert fake_s3.upload_file.call_count == 1
    # list_objects_v2 paginator was used twice (daily prune + monthly prune).
    assert fake_s3.get_paginator.call_count == 2


def test_run_backup_monthly_snapshot_on_day_1(good_env, monkeypatch):
    """Day-1 of month → upload happens twice (daily + monthly key)."""
    def fake_pg_dump(dsn, out_path):
        Path(out_path).write_bytes(b"X" * 2048)
        return 2048
    monkeypatch.setattr(mod, "_run_pg_dump", fake_pg_dump)

    fake_s3 = _make_fake_s3()
    monkeypatch.setattr(mod, "_build_s3_client", lambda cfg: fake_s3)

    fixed_now = dt.datetime(2026, 6, 1, 20, 30, 0, tzinfo=dt.timezone.utc)
    result = mod.run_backup(now=fixed_now)

    assert result.ok is True
    assert result.daily_key == "daily/fiesta_pg_20260601.pgdump"
    assert result.monthly_key == "monthly/fiesta_pg_202606.pgdump"
    assert fake_s3.upload_file.call_count == 2


def test_run_backup_handles_pg_dump_failure(good_env, monkeypatch):
    """pg_dump raises → result.ok=False, error captured, no upload attempted."""
    import subprocess

    def fake_pg_dump_fails(dsn, out_path):
        raise subprocess.CalledProcessError(1, ["pg_dump"], stderr="connection refused")
    monkeypatch.setattr(mod, "_run_pg_dump", fake_pg_dump_fails)

    fake_s3 = _make_fake_s3()
    monkeypatch.setattr(mod, "_build_s3_client", lambda cfg: fake_s3)

    result = mod.run_backup(now=dt.datetime(2026, 5, 24, 20, 30, 0, tzinfo=dt.timezone.utc))
    assert result.ok is False
    assert result.error is not None
    assert "CalledProcessError" in result.error
    assert fake_s3.upload_file.call_count == 0


def test_run_backup_handles_missing_env(monkeypatch):
    """No env vars → ok=False, error names the missing var."""
    for k in list(os.environ):
        if k.startswith("BACKUP_S3_") or k == "FLY_PG_DATABASE_URL":
            monkeypatch.delenv(k, raising=False)

    # _load_config raises BackupConfigError which run_backup should NOT catch
    # (config errors are operator-fixable; surfacing them loudly is correct).
    with pytest.raises(mod.BackupConfigError):
        mod.run_backup(now=dt.datetime(2026, 5, 24, tzinfo=dt.timezone.utc))


# ---------------------------------------------------------------------------
# _prune
# ---------------------------------------------------------------------------

def _fake_paginator(keys):
    """Build a paginator stub that returns Contents=keys."""
    p = MagicMock()
    p.paginate.return_value = [{
        "Contents": [
            {"Key": k, "LastModified": ts} for k, ts in keys
        ],
    }]
    return p


def test_prune_keeps_newest_n_and_deletes_rest():
    s3 = MagicMock()
    now = dt.datetime(2026, 5, 24, tzinfo=dt.timezone.utc)
    keys = [
        (f"daily/fiesta_pg_2026{m:02d}{d:02d}.pgdump",
         now - dt.timedelta(days=i))
        for i, (m, d) in enumerate([(5, 24), (5, 23), (5, 22), (5, 21), (5, 20)])
    ]
    s3.get_paginator.return_value = _fake_paginator(keys)

    n = mod._prune(s3, "bucket", "daily/", retain=2)
    assert n == 3  # 5 keys total → keep 2 newest → delete 3
    # delete_objects called once with the 3 oldest keys
    s3.delete_objects.assert_called_once()
    deleted_keys = {k["Key"] for k in s3.delete_objects.call_args[1]["Delete"]["Objects"]}
    # 3 oldest = indices 2, 3, 4 = the (5,22), (5,21), (5,20) ones
    assert "daily/fiesta_pg_20260522.pgdump" in deleted_keys
    assert "daily/fiesta_pg_20260521.pgdump" in deleted_keys
    assert "daily/fiesta_pg_20260520.pgdump" in deleted_keys


def test_prune_noop_when_under_threshold():
    s3 = MagicMock()
    now = dt.datetime(2026, 5, 24, tzinfo=dt.timezone.utc)
    keys = [
        (f"daily/k{i}.pgdump", now - dt.timedelta(days=i))
        for i in range(3)
    ]
    s3.get_paginator.return_value = _fake_paginator(keys)

    n = mod._prune(s3, "bucket", "daily/", retain=10)
    assert n == 0
    s3.delete_objects.assert_not_called()


def test_prune_zero_retain_is_skipped():
    s3 = MagicMock()
    n = mod._prune(s3, "bucket", "daily/", retain=0)
    assert n == 0
    s3.get_paginator.assert_not_called()
