#!/usr/bin/env bash
# bin/secrets-sync.sh — Push secrets from fiesta.env to Fly.io app
# Reads from G:/My Drive/CEO OS/working files/_cockpit_fiesta/fiesta.env
# Does NOT echo secret values to stdout.
set -euo pipefail

ENV_FILE="G:/My Drive/CEO OS/working files/_cockpit_fiesta/fiesta.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: env file not found at: $ENV_FILE"
  exit 1
fi

FLY=$(command -v fly 2>/dev/null || command -v flyctl 2>/dev/null || "")
if [ -z "$FLY" ]; then
  echo "ERROR: flyctl not found."
  exit 1
fi

echo "=== Syncing secrets to fiesta-mvp (values hidden) ==="

# Build a list of KEY=VALUE pairs from the env file, skipping comments and blanks
PAIRS=()
while IFS= read -r line; do
  # Skip comments and empty lines
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  [[ -z "${line// }" ]] && continue
  # Must be KEY=VALUE format
  [[ "$line" =~ ^[A-Z_][A-Z0-9_]*= ]] || continue
  PAIRS+=("$line")
done < "$ENV_FILE"

echo "Found ${#PAIRS[@]} secret(s) to sync."

# Use fly secrets import for bulk-set
printf '%s\n' "${PAIRS[@]}" | "$FLY" secrets import --app fiesta-mvp

echo "Secrets synced. Count: ${#PAIRS[@]}"
