# Tier D1 / F1 — Fly Postgres Disaster Recovery Runbook

**Cluster:** `fiesta-pg-bom` (unmanaged Fly Postgres, region=bom, single primary)
**Backup target:** Tigris bucket `fiesta-mvp-pg-backups`
**Cadence:** daily at 20:30 UTC (02:00 IST) via Celery beat
**Tool:** `pg_dump --format=custom` (logical, compressed)
**Retention:** 14 daily + 12 monthly
**Restore command of last resort:** `pg_restore` into a fresh cluster, then flip `DATABASE_URL`

Subagent #6 raised this gap on 2026-05-24 (post-cutover from Neon → Fly PG): the unmanaged cluster has no automatic snapshots, no PITR, no managed backups. Customer data is one volume-loss event away from being unrecoverable. This runbook + the daily backup task close the gap.

---

## 1. What is in the bucket

```
s3://fiesta-mvp-pg-backups/
├── daily/
│   ├── fiesta_pg_20260524.pgdump        ← yesterday
│   ├── fiesta_pg_20260523.pgdump
│   └── ... (14 most recent)
└── monthly/
    ├── fiesta_pg_202605.pgdump          ← May 2026 snapshot (taken on day 1)
    ├── fiesta_pg_202604.pgdump
    └── ... (12 most recent)
```

- **Format:** PostgreSQL custom format (compressed; supports selective + parallel restore).
- **Producer:** `tasks/pg_backup.py:daily_backup_task` (Celery beat).
- **Pruner:** built into the same task — keeps newest 14 daily + 12 monthly, deletes the rest at the end of each run.
- **Object metadata:** every key carries `source=fiesta-pg-bom`, `tool=pg_dump`, `format=custom`, `uploaded_at=<ISO>`.

---

## 2. LIST backups

From any machine with `flyctl` + Tigris credentials on PATH:

```bash
# (a) Quick check via flyctl — show bucket status and size.
flyctl storage status fiesta-mvp-pg-backups

# (b) Full key listing via aws CLI pointed at the Tigris endpoint.
# Get credentials from `flyctl secrets list -a fiesta-mvp` (BACKUP_S3_*).
export AWS_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_ACCESS_KEY"
export AWS_DEFAULT_REGION="auto"

aws s3 ls --endpoint-url https://fly.storage.tigris.dev \
  s3://fiesta-mvp-pg-backups/daily/ --human-readable
aws s3 ls --endpoint-url https://fly.storage.tigris.dev \
  s3://fiesta-mvp-pg-backups/monthly/ --human-readable
```

You should see a new `daily/fiesta_pg_YYYYMMDD.pgdump` appear every day within ~5 min of 20:30 UTC.

---

## 3. DOWNLOAD a backup

```bash
# Pick the date you want.
TARGET=20260524
aws s3 cp --endpoint-url https://fly.storage.tigris.dev \
  s3://fiesta-mvp-pg-backups/daily/fiesta_pg_${TARGET}.pgdump \
  /tmp/fiesta_pg_${TARGET}.pgdump
ls -lh /tmp/fiesta_pg_${TARGET}.pgdump
```

Expected size today: **2–4 MB** (current data tier ~2 MB uncompressed, custom format compresses ~50%).

---

## 4. RESTORE — full DR procedure

**TRIGGER:** primary volume on `fiesta-pg-bom` is lost / corrupt / DROP'd. The app group on `fiesta-mvp` is returning 5xx because every DB call fails.

**Recovery objective:** restore the most recent good backup into a NEW cluster, flip `DATABASE_URL`, scale the app back to healthy. Wall-clock target: **10–15 min** from a 2–3 MB dump.

### Step 1. Decide which dump to restore
Default: yesterday's `daily/fiesta_pg_YYYYMMDD.pgdump`. If corruption was caught mid-day and the daily ran AFTER the corruption, pick the previous day instead.

### Step 2. Stand up a fresh Fly PG cluster

```bash
# Same shape as the dead one.
flyctl postgres create \
  --name fiesta-pg-bom-restore \
  --region bom \
  --vm-size shared-cpu-1x \
  --volume-size 10 \
  --initial-cluster-size 1 \
  --org personal

# Note the connection string the create command prints — save it as RESTORE_DSN.
export RESTORE_DSN='postgres://fiesta_mvp:PASS@fiesta-pg-bom-restore.flycast:5432/fiesta_mvp?sslmode=disable'
```

### Step 3. Download the chosen dump (see §3 above)

### Step 4. Restore into the new cluster

`pg_restore` will run from inside a Fly machine that has network access to the cluster's `.flycast` address. The fiesta-mvp worker is perfect — it already has `pg_dump`/`pg_restore` (postgresql-client-15, added to the Dockerfile in Tier D1) and access to `BACKUP_S3_*` + the new DSN.

```bash
# SSH into a worker machine. (--process worker because beat/web shouldn't be used.)
flyctl ssh console -a fiesta-mvp --process worker

# Inside the machine:
export AWS_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_ACCESS_KEY"
export AWS_DEFAULT_REGION="auto"
export RESTORE_DSN='<from step 2>'

TARGET=20260524  # ← the dump you chose in step 1

# Pull the dump.
aws s3 cp --endpoint-url https://fly.storage.tigris.dev \
  s3://fiesta-mvp-pg-backups/daily/fiesta_pg_${TARGET}.pgdump \
  /tmp/restore.pgdump

# Drop+recreate the target DB (custom-format restore is happiest into an empty DB).
psql "$RESTORE_DSN" -c "DROP DATABASE IF EXISTS fiesta_mvp;"
psql "$RESTORE_DSN" -c "CREATE DATABASE fiesta_mvp OWNER fiesta_mvp;"

# Restore. -j 2 = parallel restore (custom format only).
pg_restore --dbname="$RESTORE_DSN" \
  --jobs=2 \
  --no-owner \
  --no-privileges \
  --verbose \
  /tmp/restore.pgdump 2>&1 | tail -50
```

Expected duration: **30–90 seconds** for the current data size.

### Step 5. Sanity check

```bash
# Row counts should be in the ballpark you expect.
psql "$RESTORE_DSN" -c "
  SELECT relname, n_live_tup
  FROM pg_stat_user_tables
  ORDER BY n_live_tup DESC LIMIT 20;
"

# Latest user, latest submission — verify recency matches the dump date.
psql "$RESTORE_DSN" -c "SELECT MAX(created_at) FROM \"user\";"
```

### Step 6. Flip `DATABASE_URL` and `FLY_PG_DATABASE_URL`

```bash
flyctl secrets set \
  DATABASE_URL="$RESTORE_DSN" \
  FLY_PG_DATABASE_URL="$RESTORE_DSN" \
  -a fiesta-mvp
```

This restarts the app group automatically. Wait for `/healthz` to return 200:

```bash
for i in $(seq 1 30); do
  curl -s -o /dev/null -w "$i: %{http_code} ttfb=%{time_starttransfer}s\n" \
    https://fiesta-mvp.fly.dev/healthz
  sleep 2
done
```

### Step 7. Smoke test

```bash
# /health should report DB connected.
curl -s https://fiesta-mvp.fly.dev/health | head -20

# /tax-bill (anonymous) should 302.
curl -s -o /dev/null -w "%{http_code} %{time_starttransfer}s\n" \
  https://fiesta-mvp.fly.dev/tax-bill
```

### Step 8. Decommission the dead cluster (after 24h cool-off)

```bash
# Only after you're confident the restore is good. KEEP for 24h in case
# something turns out to need data from the broken cluster.
flyctl postgres destroy fiesta-pg-bom --yes
```

Then optionally rename the restore cluster back to the canonical name:

```bash
flyctl apps rename fiesta-pg-bom-restore fiesta-pg-bom
```

---

## 5. Validation: monthly restore drill

To make sure backups are actually restorable (not just present), run `restore_test.sh` on the **1st of every month** against a throwaway scratch cluster. The script:

1. Spins up a temporary Fly PG cluster `fiesta-pg-restore-test`.
2. Pulls the most recent daily dump from Tigris.
3. Runs `pg_restore` into the scratch cluster.
4. Asserts `SELECT 1 FROM information_schema.tables WHERE table_name='user'` returns 1 row.
5. Destroys the scratch cluster.
6. Posts pass/fail to Telegram.

```bash
cd _tier_d1_pg_backup
./restore_test.sh
```

Schedule via cron on CEO laptop or as a one-off Fly app — NOT bolted into the main worker (it provisions/destroys infra and we want it operator-supervised for now).

---

## 6. Failure modes & alerting

| Failure | Detection | Recovery |
|---|---|---|
| pg_dump exits non-zero | `flyctl logs -a fiesta-mvp` grep `pg_backup FAILED`; worker_heartbeat task already alerts on missing AI-org task success but does NOT cover pg_backup specifically. **TODO:** add pg_backup to the worker_heartbeat checklist (next session). | Investigate DSN / pg_dump connectivity. Re-run manually: `flyctl ssh console -a fiesta-mvp --process worker --command 'python -c "from tasks.pg_backup import run_backup; print(run_backup())"'` |
| Tigris upload fails | Same — error appears in worker logs and result.ok=False | Check `flyctl storage status fiesta-mvp-pg-backups`. Verify `BACKUP_S3_*` env vars are still set. |
| Daily dump file is 0 bytes or wildly smaller than yesterday | Manual `aws s3 ls` shows the anomaly | Treat as a restore-needed event; investigate cluster health. |
| Backup task never ran (Celery beat down) | `aws s3 ls` shows no new key for today after 21:00 UTC | Check `flyctl status -a fiesta-mvp` — confirm beat process running. Restart: `flyctl machine restart <beat-machine-id> -a fiesta-mvp`. |

---

## 7. Cost estimate

Tigris storage list price (2026-05-24, public):
- **Storage:** $0.02 / GB / month
- **Class A operations** (PUT/COPY): $5.00 / million
- **Class B operations** (GET/LIST): $0.40 / million
- **Egress** (free between Fly apps + Tigris)

Projected monthly cost at current scale (~3 MB per dump):
- 14 daily + 12 monthly = ~26 objects × ~3 MB ≈ 78 MB stored = **~$0.002 / month** (storage)
- ~30 PUTs / month (1 daily + ~1 monthly) = **~$0.0002 / month**
- ~60 DELETEs / month (prune) = **~$0.0003 / month**
- LIST during prune (~30/month) = **~$0.000012 / month**

**Total ≈ $0.003 / month** at today's data size. Even at 10x growth (~30 MB / dump, ~800 MB stored), still **< $0.02 / month**.

---

## 8. Out of scope (do NOT attempt without a separate council)

- **WAL streaming / PITR.** Custom-format dumps are point-in-time-of-dump only. Recovery window is "yesterday's data" worst case. Acceptable for MVP.
- **Cross-region replica.** Tigris stores in multi-region by default, so the bucket survives a single Fly region failure. The cluster does not — single primary in bom. If bom goes down, you DR via this runbook into another region.
- **Encryption at rest beyond Tigris default.** No customer PII fields require GDPR-style encrypted-by-key handling today; revisit when first EU enterprise contract lands.
- **Auto-restore.** This runbook is human-driven on purpose. Restore is an irreversible action against prod state; gating it behind a human is correct for v1.

---

## 9. Setup checklist (one-time, before first cron fire)

CEO actions:

- [ ] Create the Tigris bucket: `flyctl storage create --name fiesta-mvp-pg-backups --org personal --yes` (subagent could not run this; see "Permission denied by classifier" note in the commit message). Capture the printed `AWS_*` credentials.
- [ ] Push the credentials as the BACKUP_* env vars (NOT AWS_*, which are already used for the receipts S3 bucket):
  ```bash
  flyctl secrets set \
    BACKUP_S3_BUCKET=fiesta-mvp-pg-backups \
    BACKUP_S3_ENDPOINT_URL=https://fly.storage.tigris.dev \
    BACKUP_S3_ACCESS_KEY_ID=<from create output> \
    BACKUP_S3_SECRET_ACCESS_KEY=<from create output> \
    BACKUP_S3_REGION=auto \
    -a fiesta-mvp
  ```
- [ ] Deploy this branch (`tier-d1/f1-pg-backup`) so the worker image carries `postgresql-client-15` and the new `tasks/pg_backup.py` is on PYTHONPATH:
  ```bash
  flyctl deploy -a fiesta-mvp
  ```
- [ ] Trigger the first manual run to confirm end-to-end:
  ```bash
  flyctl ssh console -a fiesta-mvp --process worker --command \
    'python -c "from tasks.pg_backup import run_backup; import json; r=run_backup(); print(json.dumps(r.to_dict(), indent=2))"'
  ```
  Expected: `"ok": true`, `daily_key` like `daily/fiesta_pg_20260524.pgdump`, `bytes_uploaded` in low millions.

Subagent has built everything above the line. CEO actions below the line are required because they (a) provision infra on the personal org, (b) handle secrets, (c) deploy to prod.
