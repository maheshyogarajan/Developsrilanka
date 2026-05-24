# Tier C6 — Rollback Runbook (Neon → Fly bom → Neon)

## When to roll back

Roll back if any of these hit within the 24h post-cutover window:

1. `/health` returns non-200 for >2 consecutive minutes
2. /tax-bill or other DB-touching routes show **higher** latency than the
   pre-cutover Neon baseline (~3s for `/health` — see `latency_measurements.md`)
3. Errors in `flyctl logs -a fiesta-mvp` mentioning connection-refused,
   timeout, or "FATAL: password authentication failed" against Fly PG
4. Data loss reported by any user (rare — `pg_dump --clean` reseeds
   schema but data after cutover lives ONLY on Fly PG until reverse-sync)
5. CEO calls it — no questions asked

## What's at risk

**Writes made during the cutover window land on Fly PG only.** A rollback
without a reverse data sync loses those writes (typically a small handful
since the cutover window is ~20min).

Mitigation: the cutover.sh saves `.rollback_database_url` containing the
Neon DSN at cutover-time. As long as Neon is still live (Neon-side cluster
unchanged), the rollback is a single secret flip.

## Rollback procedure (90 seconds, single command)

### Step 1: Flip the secret back

```bash
cd "C:/Users/mahes/fiesta_phase_a/worktrees/tier-c6-db-prep/_tier_c_db_prep"

# Read saved Neon DSN
NEON_URL=$(cat .rollback_database_url)

# Flip the secret (this restarts the app, ~60-90s)
flyctl secrets set DATABASE_URL="$NEON_URL" -a fiesta-mvp
```

### Step 2: Verify

```bash
# Should return 200 within 90s
for i in 1 2 3 4 5 6 7 8 9; do
  curl -s -o /dev/null -w "$i: %{http_code} %{time_starttransfer}s\n" \
    https://fiesta-mvp.fly.dev/health
  sleep 10
done
```

Expected: TTFB returns to ~3s range (pre-cutover Neon baseline). HTTP 200.

### Step 3: Reverse-sync any writes from Fly PG → Neon (OPTIONAL)

Only do this if writes happened in the post-cutover window AND you need
to preserve them in Neon.

```bash
# Dump from Fly PG (run from inside fiesta-pg-bom)
flyctl ssh console -a fiesta-pg-bom -C \
  'pg_dump --no-owner --no-privileges --format=custom \
   --file=/tmp/reverse_sync.pgdump \
   "postgres://fiesta_mvp:PASSWORD@fiesta-pg-bom.flycast:5432/fiesta_mvp"'

# Restore into Neon (also from inside cluster machine, which has network egress)
flyctl ssh console -a fiesta-pg-bom -C \
  'pg_restore --no-owner --no-privileges --data-only \
   --dbname="$NEON_URL" /tmp/reverse_sync.pgdump'
```

WARNING: `--data-only` may duplicate rows if PK conflicts. For most rows
this is OK because of unique constraints, but inspect logs before
declaring done.

## Decision tree

```
/health 500 or timeouts post-cutover?
├─ YES → check flyctl logs -a fiesta-mvp last 5min
│        ├─ "FATAL: ... fiesta_mvp" → Fly PG role/perm issue → Step 1 rollback
│        ├─ "could not translate ... flycast" → Flycast routing issue
│        │   → check `flyctl status -a fiesta-pg-bom` first; if cluster healthy,
│        │     Step 1 rollback and open Fly support ticket
│        ├─ "ERROR: relation \"x\" does not exist" → schema parity false-negative
│        │   → Step 1 rollback IMMEDIATELY; re-run verify_schema_parity.py
│        │     with --verbose to find the missing object before retry
│        └─ unrelated app error → not a DB-cutover issue; investigate independently
└─ NO → /health OK but slow (>baseline)?
         ├─ YES → connection pool not warmed yet; wait 60s, recheck
         │        if still slow → Step 1 rollback, open ticket
         └─ NO → cutover succeeded; monitor for 24h
```

## After 24h of stable operation

1. Delete `.rollback_database_url` (it contains the Neon password — security
   hygiene)
2. Optionally `flyctl secrets unset FLY_PG_DATABASE_URL -a fiesta-mvp`
   (no longer needed since DATABASE_URL now points to the same value)
3. Tell CEO the Neon DB can be put on a delete countdown (recommend
   30-day grace before actually deleting Neon — gives time to discover
   any data discrepancy)
4. Update `DEPLOYMENT.md` to reflect new DATABASE_URL host

## Known failure modes & their fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| `flyctl secrets set` takes >5min | Fly platform issue, not us | Wait, retry; if stuck, contact Fly support |
| `/health` 200 but `/tax-bill` 500 | view `vw_tax_bill_context` missing | Parity verifier should have caught — re-seed schema |
| Workers (celery) can't connect | Worker process didn't pick up new secret | `flyctl restart -a fiesta-mvp --process=worker` |
| `.rollback_database_url` missing | cutover.sh interrupted before Step 2 | Read NEON_DATABASE_URL value from your password vault and re-export |
| Schema parity diff appears ONLY after cutover | Late writes added new column on Neon | Re-run cutover with fresh schema (drop+recreate fiesta_mvp DB on Fly cluster, re-seed, retry) |
