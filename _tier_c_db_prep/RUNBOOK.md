# Tier C6 — DB Migration Runbook (Neon → Fly Postgres bom)

**Goal:** Move FIESTA's Postgres from Neon (us-east-1) to Fly Postgres in
the `bom` region, eliminating the cross-continent round-trip that currently
caps DB-touching requests at ~2.5s.

**Status as of 2026-05-24 prep:**
- Fly PG cluster `fiesta-pg-bom` CREATED (region=bom, vm=shared-cpu-1x,
  vol=10GB, cluster_size=1)
- Cluster ATTACHED to fiesta-mvp as Fly secret `FLY_PG_DATABASE_URL`
  (DATABASE_URL untouched — still Neon — for safe parallel state)
- Parity verifier, cutover script, rollback runbook READY
- **G1 NOT YET FIRED** — CEO authorizes cutover when ready

---

## 1. Pre-cutover checklist (CEO + operator)

Run these in order before invoking `cutover.sh`:

- [ ] **Maintenance window scheduled** (~20min downtime; tax-bill clients
      see a maintenance page or 503 during the window)
- [ ] **NEON_DATABASE_URL exported** in the operator's shell — get it from
      `flyctl secrets list -a fiesta-mvp` (you only see the digest there;
      retrieve actual value from CEO's password vault / Neon dashboard)
- [ ] **FLY_PG_DATABASE_URL_APP exported** — value lives in
      `_tier_c_db_prep/fly_pg_connection.txt` (gitignored)
- [ ] **Connectivity confirmed** —
      `python verify_schema_parity.py --help` runs from this worktree
- [ ] **Baseline latency recaptured** (in case Neon performance has drifted
      since prep): 5 samples of `curl -w '%{time_starttransfer}'
      https://fiesta-mvp.fly.dev/health`
- [ ] **App is healthy** — `flyctl status -a fiesta-mvp` shows all
      machines passing /healthz
- [ ] **Fly PG cluster is healthy** — `flyctl status -a fiesta-pg-bom`
      shows machine state=started, primary healthy
- [ ] **CEO has fired G1** in Telegram or session log (binding consent
      for the cutover to proceed)
- [ ] **Rollback runbook is open in a second terminal** — `ROLLBACK_RUNBOOK.md`

---

## 2. Cutover sequence (what cutover.sh does)

The script automates this in ~5-10 minutes wall-clock; reproduced here
in English so CEO can follow along:

| # | Step | ETA | What happens |
|---|---|---|---|
| 0 | Pre-flight | 5s | Verify flyctl auth, both DSNs set, apps reachable |
| 1 | Schema parity verifier | 10-30s | Run `verify_schema_parity.py`; abort if non-zero (G1 gate) |
| 2 | Snapshot Neon DSN | <1s | Save current DATABASE_URL to `.rollback_database_url` |
| 3 | Maintenance mode | 30s | `flyctl scale count app=0 -a fiesta-mvp` — app group stops accepting traffic |
| 4 | Final pg_dump + restore | 3-8 min | Dump Neon (from inside fiesta-pg-bom machine — egress allowed), restore into the empty fiesta_mvp DB on the new cluster |
| 5 | Flip secret | 5s | `flyctl secrets set DATABASE_URL=... -a fiesta-mvp --stage` (queues for next deploy, no restart yet) |
| 6 | Scale back up | 60-90s | `flyctl scale count app=2 -a fiesta-mvp` — new machines spin up with the new secret; wait for `/healthz` to return 200 |
| 7 | Verify /health | 5s | Confirm `/health` returns 200 with `database: connected` against Fly bom |
| 8 | Report elapsed | <1s | Print total time + next steps |

If ANY step fails, the script halts and reports the rollback command. If
the failure is in steps 5-7 (after the secret flip), the rollback file
from Step 2 is the safety net.

---

## 3. Invoking the cutover

```bash
cd "C:/Users/mahes/fiesta_phase_a/worktrees/tier-c6-db-prep/_tier_c_db_prep"

# These two env vars are the only operator-supplied inputs.
export NEON_DATABASE_URL='postgresql://USER:PASS@HOST.neon.tech/DB?sslmode=require'
export FLY_PG_DATABASE_URL_APP='postgres://fiesta_mvp:PASS@fiesta-pg-bom.flycast:5432/fiesta_mvp?sslmode=disable'

# Optional: dry-run first to print every command without executing
DRY_RUN=1 ./cutover.sh

# Real cutover:
./cutover.sh
```

Re-running after partial failure: the script is idempotent. Specifically:
- Step 2 won't overwrite an existing `.rollback_database_url`
- Step 4 can be skipped with `SKIP_DATA_SYNC=1` if the dump already
  completed but a later step failed
- Step 5 uses `--stage` so re-running won't double-restart the app

---

## 4. Post-cutover verification

Within 5 minutes of the cutover script reporting SUCCESS:

```bash
# 10 samples of /health TTFB — should drop from ~3s to ~1s
for i in $(seq 1 10); do
  curl -s -o /dev/null -w "$i: %{time_starttransfer}s %{http_code}\n" \
    https://fiesta-mvp.fly.dev/health
  sleep 2
done

# Log inspection — last 5 minutes, look for connection errors
flyctl logs -a fiesta-mvp | tail -100 | grep -iE 'error|exception|database' | head -20

# Quick smoke: anonymous /tax-bill (should 302 to login)
curl -s -o /dev/null -w "tax-bill: %{time_starttransfer}s %{http_code}\n" \
  https://fiesta-mvp.fly.dev/tax-bill
```

**Pass criteria (must all hold):**
- `/health` TTFB median < 1.5s across 10 samples (baseline was 2-3s; aim is <1s)
- No connection errors in `flyctl logs` in the last 5 min
- `/tax-bill` returns 302 in <2s

If any fails → ROLLBACK_RUNBOOK.md.

---

## 5. Rollback trigger conditions

See `ROLLBACK_RUNBOOK.md` for the full decision tree. Trigger if:

1. `/health` 5xx for >2 consecutive minutes
2. Latency REGRESSES (worse than Neon baseline)
3. Authentication/permission errors against Fly PG
4. Data inconsistency reported by any user
5. CEO calls it

Rollback is ~90 seconds; safe up to 24h after cutover.

---

## 6. Latency before/after

Captured during prep (n=5 samples each, my-worktree-to-app):

| Route | Before (Neon, live now) | After (Fly bom, projected) | Delta |
|---|---|---|---|
| `/healthz` (no DB) | 0.90s | 0.90s | unchanged |
| `/health` (1 SELECT 1) | 3.04s | ~0.90s | **−2.14s (−70%)** |

Per-query DB latency:
- Neon (us-east-1, from bom app): 1.1-2.2s round-trip
- Fly bom (in-region, pooled conn): **0.26-0.57 ms**

Full breakdown: `latency_measurements.md`

---

## 7. Resource & cost

Fly PG cluster `fiesta-pg-bom`:
- Image: `flyio/postgres-flex:17.2` (Postgres 17, Repmgr-managed)
- VM: shared-cpu-1x / 512MB RAM
- Volume: 10GB
- Cluster size: 1 (single primary, no replica)
- Region: bom
- Org: personal

Fly pricing (as of 2026-05-24, public list):
- shared-cpu-1x VM: ~$1.94/month always-on
- 512MB RAM: ~$1.50/month
- 10GB volume: ~$1.50/month
- **Total: ~$5/month** (vs Neon's metered usage tier)

If we later scale to HA (2-3 replicas) for primary safety: ~$15-25/month.

---

## 8. What this prep does NOT do (out of scope)

- True logical replication via pglogical or `CREATE PUBLICATION` —
  Neon's replication slots are managed/restricted, and `wal_level` may
  not be `logical` on our instance. The cutover uses **dump+restore
  during the maintenance window** instead. ~3-8 min for the data tier.
- Continuous reverse sync to Neon — once cutover lands, Neon is FROZEN.
  Keep Neon paused (not deleted) for 30 days as a manual rollback
  option.
- HA / replica configuration on Fly PG. Single primary is sufficient
  for the migration; scale to N replicas as a separate Tier C work
  item if needed.
- Connection pooler (PgBouncer) on Fly. The Fly PG image has no
  built-in pooler; SQLAlchemy's pool inside the app is the only layer.
  Acceptable for v1; revisit if connection counts grow.
- Monitoring layer (Datadog, Grafana). Use `flyctl logs` + `/health`
  endpoint + existing Telegram on-error alerts.

---

## 9. Files in this prep package

```
_tier_c_db_prep/
├── RUNBOOK.md                  ← this file
├── ROLLBACK_RUNBOOK.md         ← rollback procedure + decision tree
├── cutover.sh                  ← single-command cutover orchestrator
├── verify_schema_parity.py     ← G1 gate (schema diff source ↔ target)
├── latency_measurements.md     ← captured baseline + projection
├── fly_pg_connection.txt       ← cluster credentials (GITIGNORED)
├── _pg_create_redacted.log     ← cluster creation log (redacted)
├── _pg_attach.log              ← cluster attach log (redacted)
└── .rollback_database_url      ← (written by cutover.sh at runtime, GITIGNORED)
```
