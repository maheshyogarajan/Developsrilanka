#!/usr/bin/env bash
# Tier C6 — Neon → Fly Postgres (bom) cutover script
#
# Council requirement: ONE COMMAND CEO runs after Y'ing G1.
# Idempotent: safe to re-run mid-failure.
#
# Steps:
#   0. Pre-flight (flyctl auth, target app/cluster exist, runbook acks)
#   1. Run schema parity verifier — abort if non-zero
#   2. Snapshot current DATABASE_URL (Neon) to local file for rollback
#   3. Put app in maintenance mode (scale app process to 0)
#   4. Final pg_dump from Neon + pg_restore into fiesta-pg-bom (data delta)
#   5. Flip DATABASE_URL secret to FLY_PG_DATABASE_URL value
#   6. Scale app back up (Bundle B requires min 2)
#   7. Verify /health returns 200 (5 retries)
#   8. Report total elapsed time
#
# Required env at invocation time (CEO sets these from local trusted store):
#   NEON_DATABASE_URL          — current Neon DSN (read from `flyctl secrets`
#                                or your password manager — NOT committed)
#   FLY_PG_DATABASE_URL_APP    — Fly cluster app DSN (the value `flyctl
#                                postgres attach` printed; also persisted in
#                                _tier_c_db_prep/fly_pg_connection.txt)
#
# Optional:
#   APP=fiesta-mvp             — Fly app to cut over
#   CLUSTER=fiesta-pg-bom      — Fly Postgres cluster name
#   SKIP_DATA_SYNC=1           — re-run after data sync already done; just
#                                flips the secret + restarts
#   DRY_RUN=1                  — print every flyctl/ssh command but execute
#                                nothing destructive
#
# Exit codes:
#   0 success
#   1 pre-flight or parity failed (no changes made)
#   2 partial — some step failed; check RUNBOOK rollback section

set -euo pipefail

APP=${APP:-fiesta-mvp}
CLUSTER=${CLUSTER:-fiesta-pg-bom}
SKIP_DATA_SYNC=${SKIP_DATA_SYNC:-0}
DRY_RUN=${DRY_RUN:-0}

HERE="$(cd "$(dirname "$0")" && pwd)"
ROLLBACK_FILE="${HERE}/.rollback_database_url"
T0=$(date +%s)

log()  { printf '\n[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() { printf '\nFAIL: %s\n' "$*" >&2; exit 2; }
run()  {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf 'DRY_RUN > %s\n' "$*"
  else
    eval "$@"
  fi
}

# ─── Step 0: pre-flight ─────────────────────────────────────────────────────
log "Step 0: pre-flight checks"

command -v flyctl >/dev/null || fail "flyctl not in PATH"
command -v python >/dev/null || fail "python not in PATH"

flyctl auth whoami >/dev/null 2>&1 || fail "flyctl not authenticated (run 'flyctl auth login')"

if [[ -z "${NEON_DATABASE_URL:-}" ]]; then
  fail "NEON_DATABASE_URL not set. Read from \`flyctl secrets list -a $APP\` "\
"+ your password vault; export NEON_DATABASE_URL='postgresql://...' before running."
fi
if [[ -z "${FLY_PG_DATABASE_URL_APP:-}" ]]; then
  fail "FLY_PG_DATABASE_URL_APP not set. See _tier_c_db_prep/fly_pg_connection.txt"
fi

flyctl status -a "$APP" >/dev/null      || fail "app $APP not reachable"
flyctl status -a "$CLUSTER" >/dev/null  || fail "cluster $CLUSTER not reachable"

log "  OK: flyctl auth, both apps reachable, both DSNs set"

# ─── Step 1: SKIPPED (was pre-Step-4 parity check; logic moved post-restore) ──
log "Step 1: skipped — parity check moved post Step 4 (empty cluster diverges before restore)"

# ─── Step 2: snapshot current secret for rollback ───────────────────────────
log "Step 2: snapshot current DATABASE_URL for rollback"
if [[ ! -f "$ROLLBACK_FILE" ]]; then
  printf '%s\n' "$NEON_DATABASE_URL" > "$ROLLBACK_FILE"
  chmod 600 "$ROLLBACK_FILE" 2>/dev/null || true
  log "  wrote $ROLLBACK_FILE (mode 600, gitignored)"
else
  log "  $ROLLBACK_FILE already exists (re-run detected) — keeping original"
fi

# ─── Step 3: maintenance mode ───────────────────────────────────────────────
log "Step 3: put $APP into maintenance (scale app process to 0)"
run "flyctl scale count app=0 -a '$APP' --yes"
log "  app process scaled to 0; worker + beat continue"
log "  (Bundle B's min_machines_running=2 reasserts on Step 6 scale-up)"

# ─── Step 4: data sync (final pg_dump | pg_restore) ─────────────────────────
if [[ "$SKIP_DATA_SYNC" == "1" ]]; then
  log "Step 4: SKIPPED (SKIP_DATA_SYNC=1)"
else
  log "Step 4: final pg_dump Neon → pg_restore into $CLUSTER"
  log "  this runs INSIDE the $CLUSTER machine so we don't need local pg_dump"
  log "  ETA: ~3-8 minutes depending on data volume"

  # Note: pg_dump|pg_restore via ssh stdin pipe is brittle on Windows;
  # safer to do the dump TO A FILE inside the cluster machine, then load.
  DUMP_PATH="/tmp/fiesta_neon_dump_$(date +%s).pgdump"

  run "flyctl ssh console -a '$CLUSTER' -C 'sh -c \"pg_dump --no-owner --no-privileges --format=custom --file=$DUMP_PATH \\\"$NEON_DATABASE_URL\\\"\"'"
  log "  dump complete on cluster machine: $DUMP_PATH"

  run "flyctl ssh console -a '$CLUSTER' -C 'sh -c \"pg_restore --no-owner --no-privileges --clean --if-exists --dbname=\\\"$FLY_PG_DATABASE_URL_APP\\\" $DUMP_PATH\"'"
  log "  restore complete"

  run "flyctl ssh console -a '$CLUSTER' -C 'sh -c \"rm -f $DUMP_PATH\"'"
fi

# ─── Step 4.5: parity check (moved from Step 1 — now schemas should match) ──
log "Step 4.5: schema parity verifier (post-restore)"
# Run from inside fiesta-mvp app machine — has Neon egress + Flycast access.
PARITY_CMD="cd /app && PYTHONPATH=/app DATABASE_URL='$NEON_DATABASE_URL' FLY_PG_DATABASE_URL='$FLY_PG_DATABASE_URL_APP' python /app/_tier_c_db_prep/verify_schema_parity.py"
run "flyctl ssh console -a '$APP' -C \"bash -c \\\"$PARITY_CMD\\\"\"" || \
  fail "Schema parity FAILED after restore — DO NOT FLIP SECRET. Inspect divergence. ROLLBACK with: flyctl secrets set DATABASE_URL=\"\$(cat $ROLLBACK_FILE)\" -a $APP"
log "  OK: schemas match post-restore (parity gate passed)"

# ─── Step 5: flip the secret ────────────────────────────────────────────────
log "Step 5: flip DATABASE_URL on $APP → Fly bom cluster"
# `flyctl secrets set` triggers a restart, but app is at 0 machines so it's
# a no-op; the new value is picked up on the next scale-up.
run "flyctl secrets set DATABASE_URL='$FLY_PG_DATABASE_URL_APP' -a '$APP' --stage"
# --stage queues the secret without auto-deploy; we control restart in Step 6.
log "  secret staged"

# ─── Step 6: scale back up ──────────────────────────────────────────────────
log "Step 6: scale $APP back to 2 (Bundle B min)"
run "flyctl scale count app=2 -a '$APP' --yes"

log "  waiting for /healthz to return 200..."
for i in 1 2 3 4 5 6 7 8 9 10; do
  CODE=$(curl -o /dev/null -s -w '%{http_code}' "https://${APP}.fly.dev/healthz" || echo 000)
  if [[ "$CODE" == "200" ]]; then
    log "  /healthz OK on attempt $i"
    break
  fi
  log "  attempt $i: /healthz returned $CODE — waiting 6s"
  sleep 6
  if [[ "$i" == "10" ]]; then
    fail "/healthz never returned 200 after 10 attempts (~60s). Check 'flyctl logs -a $APP'. ROLLBACK with: flyctl secrets set DATABASE_URL=\"\$(cat $ROLLBACK_FILE)\" -a $APP"
  fi
done

# ─── Step 7: verify /health (DB roundtrip) ──────────────────────────────────
log "Step 7: verify /health (DB roundtrip against Fly bom)"
HEALTH_CODE=$(curl -o /tmp/_health.json -s -w '%{http_code}' "https://${APP}.fly.dev/health" || echo 000)
HEALTH_BODY=$(cat /tmp/_health.json 2>/dev/null || echo '{}')
if [[ "$HEALTH_CODE" != "200" ]]; then
  fail "/health returned $HEALTH_CODE. Body: $HEALTH_BODY. ROLLBACK now."
fi
log "  /health OK: $HEALTH_BODY"

# ─── Step 8: report ─────────────────────────────────────────────────────────
T1=$(date +%s)
ELAPSED=$((T1 - T0))
log "Cutover SUCCESS in ${ELAPSED}s"
log "Rollback file kept at: $ROLLBACK_FILE"
log "  (delete after 24h stability window; see ROLLBACK_RUNBOOK.md)"
echo
echo "Next:"
echo "  - run 5+ samples of curl -w '%{time_starttransfer}' https://${APP}.fly.dev/health"
echo "  - compare with latency_measurements.md baseline (~3.04s before)"
echo "  - if anomaly, ROLLBACK_RUNBOOK.md step 1 reverses in <90s"

exit 0
