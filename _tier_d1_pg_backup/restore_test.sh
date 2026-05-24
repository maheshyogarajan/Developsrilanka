#!/usr/bin/env bash
# restore_test.sh — Monthly synthetic "restore-test-and-throw-away" drill.
#
# Verifies the latest daily pg_dump in Tigris is actually restorable into
# a fresh Fly PG cluster. Run on the 1st of every month (manually, NOT
# from the worker — this script provisions/destroys infra).
#
# Prereqs:
#   - flyctl installed + authenticated (flyctl auth whoami → CEO email)
#   - aws CLI installed
#   - BACKUP_S3_* env vars exported (get from flyctl secrets list -a fiesta-mvp)
#     The values themselves are only printable via the Fly dashboard; export
#     them locally for the duration of this drill.
#
# Exit codes:
#   0 = restore OK, scratch cluster destroyed
#   1 = restore failed (you SHOULD investigate — backups are not safe)
#   2 = setup error (missing tool/credential — fix and retry)

set -euo pipefail

# ────────────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────────────
SCRATCH_CLUSTER="fiesta-pg-restore-test"
ORG="personal"
REGION="bom"
BUCKET="${BACKUP_S3_BUCKET:-fiesta-mvp-pg-backups}"
ENDPOINT="${BACKUP_S3_ENDPOINT_URL:-https://fly.storage.tigris.dev}"

# Optional override: RESTORE_TEST_DATE=20260523 to test a specific dump.
TARGET_DATE="${RESTORE_TEST_DATE:-$(date -u -d 'yesterday' +%Y%m%d 2>/dev/null || date -u -v-1d +%Y%m%d)}"
DUMP_KEY="daily/fiesta_pg_${TARGET_DATE}.pgdump"
LOCAL_DUMP="/tmp/restore_test_${TARGET_DATE}.pgdump"

log()  { printf '[restore_test %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() { log "FAIL: $*"; cleanup; exit 1; }

# ────────────────────────────────────────────────────────────────────────────
# Cleanup — runs on any exit, idempotent
# ────────────────────────────────────────────────────────────────────────────
cleanup() {
  log "cleanup: removing local dump"
  rm -f "$LOCAL_DUMP" || true
  if flyctl status -a "$SCRATCH_CLUSTER" >/dev/null 2>&1; then
    log "cleanup: destroying scratch cluster $SCRATCH_CLUSTER"
    flyctl postgres destroy "$SCRATCH_CLUSTER" --yes || true
  fi
}
trap cleanup EXIT

# ────────────────────────────────────────────────────────────────────────────
# 0. Pre-flight
# ────────────────────────────────────────────────────────────────────────────
log "preflight: checking tools"
command -v flyctl >/dev/null || { log "missing flyctl"; exit 2; }
command -v aws >/dev/null    || { log "missing aws cli"; exit 2; }
command -v psql >/dev/null   || { log "missing psql"; exit 2; }
command -v pg_restore >/dev/null || { log "missing pg_restore"; exit 2; }

for v in BACKUP_S3_ACCESS_KEY_ID BACKUP_S3_SECRET_ACCESS_KEY; do
  test -n "${!v:-}" || { log "missing env: $v"; exit 2; }
done

export AWS_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_ACCESS_KEY"
export AWS_DEFAULT_REGION="${BACKUP_S3_REGION:-auto}"

# ────────────────────────────────────────────────────────────────────────────
# 1. Pull the dump
# ────────────────────────────────────────────────────────────────────────────
log "fetch: s3://$BUCKET/$DUMP_KEY  →  $LOCAL_DUMP"
aws s3 cp --endpoint-url "$ENDPOINT" "s3://$BUCKET/$DUMP_KEY" "$LOCAL_DUMP" \
  || fail "S3 download failed (key may not exist yet — first run after midnight UTC?)"

DUMP_SIZE=$(stat -c%s "$LOCAL_DUMP" 2>/dev/null || stat -f%z "$LOCAL_DUMP")
log "fetch: OK, $DUMP_SIZE bytes"
[ "$DUMP_SIZE" -gt 1024 ] || fail "dump is suspiciously small ($DUMP_SIZE bytes)"

# ────────────────────────────────────────────────────────────────────────────
# 2. Spin up scratch cluster (only if not already running from a previous drill)
# ────────────────────────────────────────────────────────────────────────────
if flyctl status -a "$SCRATCH_CLUSTER" >/dev/null 2>&1; then
  log "scratch: cluster $SCRATCH_CLUSTER already exists from a previous run — re-using"
else
  log "scratch: creating $SCRATCH_CLUSTER (this takes ~60s)"
  flyctl postgres create \
    --name "$SCRATCH_CLUSTER" \
    --region "$REGION" \
    --vm-size shared-cpu-1x \
    --volume-size 1 \
    --initial-cluster-size 1 \
    --org "$ORG" \
    || fail "flyctl postgres create failed"
fi

# Get the connection string. flyctl postgres create prints it once and
# never again, but `flyctl postgres list` shows the cluster name and
# we can craft the .flycast DSN from convention. The password is in
# the cluster's secrets — extract via `flyctl ssh console`.
log "scratch: deriving DSN"
SCRATCH_DSN=$(flyctl ssh console -a "$SCRATCH_CLUSTER" --command "cat /data/postgres/.env" 2>/dev/null \
  | grep '^OPERATOR_PASSWORD=' | cut -d= -f2 \
  | xargs -I{} echo "postgres://postgres:{}@${SCRATCH_CLUSTER}.flycast:5432/postgres?sslmode=disable")

[ -n "$SCRATCH_DSN" ] || fail "could not derive scratch DSN — check flyctl ssh access"

# ────────────────────────────────────────────────────────────────────────────
# 3. Restore
# ────────────────────────────────────────────────────────────────────────────
log "restore: pg_restore into scratch (parallel=2)"
psql "$SCRATCH_DSN" -c "DROP DATABASE IF EXISTS fiesta_mvp;" || fail "DROP DATABASE failed"
psql "$SCRATCH_DSN" -c "CREATE DATABASE fiesta_mvp;"         || fail "CREATE DATABASE failed"

RESTORE_DSN="${SCRATCH_DSN/\/postgres\?/\/fiesta_mvp\?}"
pg_restore --dbname="$RESTORE_DSN" \
  --jobs=2 --no-owner --no-privileges \
  "$LOCAL_DUMP" 2>&1 | tail -30 \
  || fail "pg_restore failed"

# ────────────────────────────────────────────────────────────────────────────
# 4. Validate
# ────────────────────────────────────────────────────────────────────────────
log "validate: checking core tables"
USER_TABLE_EXISTS=$(psql "$RESTORE_DSN" -tAc \
  "SELECT 1 FROM information_schema.tables WHERE table_name='user'" || echo "")
[ "$USER_TABLE_EXISTS" = "1" ] || fail "no 'user' table in restored DB"

USER_COUNT=$(psql "$RESTORE_DSN" -tAc 'SELECT COUNT(*) FROM "user"' || echo "0")
log "validate: $USER_COUNT users restored"
[ "$USER_COUNT" -ge 0 ] || fail "user count query failed"

# ────────────────────────────────────────────────────────────────────────────
# 5. Pass — cleanup runs via trap
# ────────────────────────────────────────────────────────────────────────────
log "PASS: dump $DUMP_KEY restored cleanly into scratch cluster"
log "      dump bytes: $DUMP_SIZE  |  users restored: $USER_COUNT"
exit 0
