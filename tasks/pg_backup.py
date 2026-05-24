"""
tasks/pg_backup.py — Daily Fly Postgres backup to Tigris (Tier D1 / F1).

PURPOSE
-------
fiesta-pg-bom is an UNMANAGED Fly Postgres cluster — no automatic snapshots,
no point-in-time recovery, no managed backups. If the single primary's volume
is lost (data corruption, hardware failure, accidental DROP), FIESTA loses
all customer data.

This task plugs the gap with daily logical pg_dump (custom format) to a
Tigris S3-compatible bucket. Retention is 14 daily + 12 monthly (the dump
taken on the 1st of each month is also kept under a `monthly/` prefix).

SCOPE (council-binding)
-----------------------
- pg_dump (logical, --format=custom) — NOT pg_basebackup (physical).
  Custom format is parallel-restorable, selective, and self-contained.
- Retention: 14 daily + 12 monthly. No yearly.
- NO encryption-at-rest beyond Tigris default (S3-style server-side).
- NO point-in-time recovery (would require WAL streaming → out of scope).
- DR is human-driven via DR_RUNBOOK.md. No auto-restore.

ENV / SECRETS (set on fiesta-mvp via flyctl secrets set)
--------------------------------------------------------
Required:
  BACKUP_S3_BUCKET           e.g. fiesta-mvp-pg-backups
  BACKUP_S3_ENDPOINT_URL     e.g. https://fly.storage.tigris.dev
  BACKUP_S3_ACCESS_KEY_ID    Tigris access key (NOT the AWS_* receipts key)
  BACKUP_S3_SECRET_ACCESS_KEY
  BACKUP_S3_REGION           e.g. auto (Tigris uses 'auto')
  FLY_PG_DATABASE_URL        already-deployed; the unmanaged PG DSN
                             (cutover.sh exports this from the Fly secret store)

Optional:
  BACKUP_RETAIN_DAILY        default 14
  BACKUP_RETAIN_MONTHLY      default 12
  BACKUP_DRY_RUN             if '1', skip pg_dump+upload, log a planned run

We deliberately do NOT use AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY —
those already point at the receipts S3 bucket. Sharing the env vars
would (a) couple two unrelated stores' lifecycle, (b) risk an accidental
overwrite if either credentials rotate.

INVOCATION
----------
Celery beat (production):
    Beat entry in celery_config.py:
        'pg_backup-daily-2030-utc': {
            'task': 'tasks.pg_backup.daily_backup_task',
            'schedule': crontab(hour=20, minute=30),  # 02:00 IST
        }

Manual (operator-triggered, e.g. one-shot smoke test):
    flyctl ssh console -a fiesta-mvp --process worker --command \\
      'python -c "from tasks.pg_backup import run_backup; print(run_backup())"'

Flask CLI (in worker / dev shell):
    flask pg-backup
    flask pg-backup --dry-run

DESIGN
------
- Streams pg_dump stdout straight into a temp file on the worker disk,
  then uploads via boto3 multipart. A 2-3MB dump (current data size)
  finishes in seconds; no streaming-upload complexity needed for v1.
- Idempotent within the day: rerunning writes to the same daily key
  (overwrites). Monthly snapshot is taken only on day-of-month=1.
- Failure mode: logged + Telegram-alerted via existing infrastructure
  (caller wraps with try/except and posts to the worker_heartbeat path).
- Tested with mocked boto3 client (no live Tigris calls in tests).
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_RETAIN_DAILY = 14
DEFAULT_RETAIN_MONTHLY = 12

# S3 key prefixes
DAILY_PREFIX = "daily/"
MONTHLY_PREFIX = "monthly/"

# pg_dump runtime cap. Current cluster is ~2-3MB; 300s is very generous
# (covers data-tier growth up to GB-scale before this needs revisiting).
PG_DUMP_TIMEOUT_SECONDS = 300


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class BackupResult:
    """Structured outcome of a single backup run."""
    ok: bool
    started_at_utc: str
    finished_at_utc: str
    duration_seconds: float
    daily_key: Optional[str] = None
    monthly_key: Optional[str] = None
    bytes_uploaded: int = 0
    bucket: Optional[str] = None
    endpoint: Optional[str] = None
    error: Optional[str] = None
    pruned_daily: int = 0
    pruned_monthly: int = 0
    dry_run: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Config / env loading
# ---------------------------------------------------------------------------

class BackupConfigError(RuntimeError):
    """Raised when required env vars are missing."""


def _load_config() -> Dict[str, Any]:
    """Read env vars and validate. Raises BackupConfigError on missing required."""
    required = [
        "BACKUP_S3_BUCKET",
        "BACKUP_S3_ENDPOINT_URL",
        "BACKUP_S3_ACCESS_KEY_ID",
        "BACKUP_S3_SECRET_ACCESS_KEY",
        "FLY_PG_DATABASE_URL",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise BackupConfigError(
            f"pg_backup: missing required env vars: {', '.join(missing)}"
        )

    return {
        "bucket": os.environ["BACKUP_S3_BUCKET"],
        "endpoint_url": os.environ["BACKUP_S3_ENDPOINT_URL"],
        "access_key_id": os.environ["BACKUP_S3_ACCESS_KEY_ID"],
        "secret_access_key": os.environ["BACKUP_S3_SECRET_ACCESS_KEY"],
        "region": os.environ.get("BACKUP_S3_REGION", "auto"),
        "database_url": os.environ["FLY_PG_DATABASE_URL"],
        "retain_daily": int(os.environ.get("BACKUP_RETAIN_DAILY", DEFAULT_RETAIN_DAILY)),
        "retain_monthly": int(os.environ.get("BACKUP_RETAIN_MONTHLY", DEFAULT_RETAIN_MONTHLY)),
        "dry_run": os.environ.get("BACKUP_DRY_RUN") == "1",
    }


# ---------------------------------------------------------------------------
# S3 client
# ---------------------------------------------------------------------------

def _build_s3_client(cfg: Dict[str, Any]):
    """Build a boto3 S3 client pointed at the Tigris endpoint.

    Separate factory so tests can monkey-patch easily.
    """
    import boto3  # local import — boto3 is heavy, only load when needed
    from botocore.config import Config as BotoConfig

    return boto3.client(
        "s3",
        endpoint_url=cfg["endpoint_url"],
        aws_access_key_id=cfg["access_key_id"],
        aws_secret_access_key=cfg["secret_access_key"],
        region_name=cfg["region"],
        config=BotoConfig(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


# ---------------------------------------------------------------------------
# Key naming
# ---------------------------------------------------------------------------

def _daily_key(ts: datetime) -> str:
    return f"{DAILY_PREFIX}fiesta_pg_{ts.strftime('%Y%m%d')}.pgdump"


def _monthly_key(ts: datetime) -> str:
    return f"{MONTHLY_PREFIX}fiesta_pg_{ts.strftime('%Y%m')}.pgdump"


# ---------------------------------------------------------------------------
# Core: dump + upload
# ---------------------------------------------------------------------------

def _run_pg_dump(database_url: str, out_path: Path) -> int:
    """Invoke pg_dump --format=custom against *database_url*, write to *out_path*.

    Returns bytes written. Raises CalledProcessError on non-zero exit.
    """
    cmd = [
        "pg_dump",
        "--format=custom",       # custom = compressed, selective-restore, parallel-restore
        "--no-owner",            # restore should not require role names to match
        "--no-privileges",       # don't dump GRANT/REVOKE — restore target may differ
        "--compress=9",          # max compression; small data tier
        "--file", str(out_path),
        database_url,
    ]

    # Hide DSN from logs.
    log.info("pg_backup: running pg_dump → %s", out_path)
    proc = subprocess.run(
        cmd,
        timeout=PG_DUMP_TIMEOUT_SECONDS,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        # Strip the DSN from any echoed error.
        stderr = (proc.stderr or "")[:2000]
        raise subprocess.CalledProcessError(
            proc.returncode, ["pg_dump", "<DSN_REDACTED>", "..."], stderr=stderr,
        )

    size = out_path.stat().st_size
    log.info("pg_backup: pg_dump wrote %d bytes", size)
    return size


def _upload(s3_client, bucket: str, key: str, file_path: Path) -> int:
    """Upload *file_path* to s3://bucket/key. Returns bytes uploaded."""
    log.info("pg_backup: uploading → s3://%s/%s", bucket, key)
    s3_client.upload_file(
        Filename=str(file_path),
        Bucket=bucket,
        Key=key,
        ExtraArgs={
            "ContentType": "application/octet-stream",
            "Metadata": {
                "source": "fiesta-pg-bom",
                "tool": "pg_dump",
                "format": "custom",
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            },
        },
    )
    return file_path.stat().st_size


# ---------------------------------------------------------------------------
# Retention pruning
# ---------------------------------------------------------------------------

def _list_keys(s3_client, bucket: str, prefix: str) -> List[Tuple[str, datetime]]:
    """List all keys under *prefix*, returning [(key, last_modified)] sorted newest-first."""
    keys: List[Tuple[str, datetime]] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            keys.append((obj["Key"], obj["LastModified"]))
    keys.sort(key=lambda kv: kv[1], reverse=True)
    return keys


def _prune(s3_client, bucket: str, prefix: str, retain: int) -> int:
    """Keep the *retain* newest objects under *prefix*; delete the rest.

    Returns count of deleted keys.
    """
    if retain <= 0:
        log.warning("pg_backup: retain=%d for prefix=%s skips pruning", retain, prefix)
        return 0

    keys = _list_keys(s3_client, bucket, prefix)
    if len(keys) <= retain:
        return 0

    to_delete = keys[retain:]
    deleted = 0
    # S3 DeleteObjects supports up to 1000/call.
    for i in range(0, len(to_delete), 1000):
        chunk = to_delete[i:i + 1000]
        s3_client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k, _ in chunk]},
        )
        deleted += len(chunk)
    log.info("pg_backup: pruned %d old %s backups", deleted, prefix.rstrip("/"))
    return deleted


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def run_backup(now: Optional[datetime] = None) -> BackupResult:
    """Take a backup and prune old ones. Returns a BackupResult.

    Safe to call from any context — does NOT touch the Flask app_context.
    """
    started = datetime.now(timezone.utc) if now is None else now
    cfg = _load_config()

    if cfg["dry_run"]:
        finished = datetime.now(timezone.utc)
        return BackupResult(
            ok=True,
            started_at_utc=started.isoformat(),
            finished_at_utc=finished.isoformat(),
            duration_seconds=(finished - started).total_seconds(),
            daily_key=_daily_key(started),
            monthly_key=_monthly_key(started) if started.day == 1 else None,
            bucket=cfg["bucket"],
            endpoint=cfg["endpoint_url"],
            dry_run=True,
        )

    s3 = _build_s3_client(cfg)

    try:
        with tempfile.TemporaryDirectory(prefix="pgbackup_") as tmpdir:
            tmp_path = Path(tmpdir) / f"fiesta_pg_{started.strftime('%Y%m%d_%H%M%S')}.pgdump"
            _run_pg_dump(cfg["database_url"], tmp_path)

            daily_key = _daily_key(started)
            bytes_uploaded = _upload(s3, cfg["bucket"], daily_key, tmp_path)

            monthly_key: Optional[str] = None
            if started.day == 1:
                monthly_key = _monthly_key(started)
                _upload(s3, cfg["bucket"], monthly_key, tmp_path)

        pruned_daily = _prune(s3, cfg["bucket"], DAILY_PREFIX, cfg["retain_daily"])
        pruned_monthly = _prune(s3, cfg["bucket"], MONTHLY_PREFIX, cfg["retain_monthly"])

        finished = datetime.now(timezone.utc)
        return BackupResult(
            ok=True,
            started_at_utc=started.isoformat(),
            finished_at_utc=finished.isoformat(),
            duration_seconds=(finished - started).total_seconds(),
            daily_key=daily_key,
            monthly_key=monthly_key,
            bytes_uploaded=bytes_uploaded,
            bucket=cfg["bucket"],
            endpoint=cfg["endpoint_url"],
            pruned_daily=pruned_daily,
            pruned_monthly=pruned_monthly,
        )
    except Exception as exc:
        finished = datetime.now(timezone.utc)
        log.exception("pg_backup: failed")
        return BackupResult(
            ok=False,
            started_at_utc=started.isoformat(),
            finished_at_utc=finished.isoformat(),
            duration_seconds=(finished - started).total_seconds(),
            bucket=cfg.get("bucket"),
            endpoint=cfg.get("endpoint_url"),
            error=f"{type(exc).__name__}: {exc}"[:500],
        )


# ---------------------------------------------------------------------------
# Celery task — lazy registration so test imports don't require a broker
# ---------------------------------------------------------------------------

def _register_celery_task():
    """Lazily register the Celery task. Returns the task function or None."""
    try:
        from celery_config import app as celery  # type: ignore
    except ImportError:
        return None

    @celery.task(name="tasks.pg_backup.daily_backup_task", bind=False)
    def daily_backup_task():
        """Celery beat task: take a daily Fly PG backup → Tigris.

        Runs daily at 20:30 UTC = 02:00 IST (after the lowest-traffic window).
        Beat entry in celery_config.py.

        No Flask app_context needed — task talks to PG directly via pg_dump
        and to Tigris via boto3.
        """
        result = run_backup()
        log_msg = (
            f"pg_backup: ok={result.ok} "
            f"key={result.daily_key} "
            f"bytes={result.bytes_uploaded} "
            f"pruned_daily={result.pruned_daily} "
            f"pruned_monthly={result.pruned_monthly} "
            f"duration={result.duration_seconds:.2f}s"
        )
        if result.ok:
            log.info(log_msg)
        else:
            log.error("pg_backup FAILED: %s | %s", result.error, log_msg)
        return result.to_dict()

    return daily_backup_task


daily_backup_task = _register_celery_task()


# ---------------------------------------------------------------------------
# Flask CLI command (`flask pg-backup`)
# ---------------------------------------------------------------------------

def register_cli(app) -> None:
    """Register `flask pg-backup` command on *app*.

    Wire from app.py:
        from tasks.pg_backup import register_cli
        register_cli(app)
    """
    import click

    @app.cli.command("pg-backup")
    @click.option("--dry-run", is_flag=True, default=False,
                  help="Skip the actual dump+upload; just log the plan.")
    def pg_backup_cmd(dry_run):
        """Run an ad-hoc Fly PG → Tigris backup."""
        if dry_run:
            os.environ["BACKUP_DRY_RUN"] = "1"
        result = run_backup()
        if result.ok:
            click.echo(
                f"pg_backup OK in {result.duration_seconds:.1f}s: "
                f"{result.daily_key} ({result.bytes_uploaded} bytes)"
            )
            if result.monthly_key:
                click.echo(f"  monthly copy: {result.monthly_key}")
            if result.pruned_daily or result.pruned_monthly:
                click.echo(
                    f"  pruned daily={result.pruned_daily} "
                    f"monthly={result.pruned_monthly}"
                )
        else:
            click.echo(f"pg_backup FAILED: {result.error}", err=True)
            raise SystemExit(1)


# ---------------------------------------------------------------------------
# __main__ entry point — `python -m tasks.pg_backup`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    result = run_backup()
    print(json.dumps(result.to_dict(), indent=2))
    sys.exit(0 if result.ok else 1)
