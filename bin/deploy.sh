#!/usr/bin/env bash
# bin/deploy.sh — Deploy FIESTA to Fly.io (fiesta-mvp, bom region)
# Usage: ./bin/deploy.sh [--strategy immediate|rolling|bluegreen]
set -euo pipefail

STRATEGY="${1:---strategy}"
STRATEGY_VAL="${2:-immediate}"

echo "=== FIESTA Fly.io Deploy ==="
echo "App: fiesta-mvp | Region: bom | Strategy: ${STRATEGY_VAL}"

# Sanity checks
if ! command -v fly &>/dev/null && ! command -v flyctl &>/dev/null; then
  echo "ERROR: flyctl not found. Install via: curl -L https://fly.io/install.sh | sh"
  exit 1
fi

FLY=$(command -v fly 2>/dev/null || command -v flyctl)

# Auth check
if ! "$FLY" auth whoami &>/dev/null; then
  echo "ERROR: Not authenticated. Run: fly auth login"
  exit 1
fi

# Sync secrets before deploy
echo "--- Syncing secrets ---"
"$(dirname "$0")/secrets-sync.sh"

# Deploy
echo "--- Deploying ---"
"$FLY" deploy --remote-only "$STRATEGY" "$STRATEGY_VAL"

echo ""
echo "=== Deploy complete ==="
"$FLY" status
echo ""
echo "App URL: https://fiesta-mvp.fly.dev"
echo "Health:  curl https://fiesta-mvp.fly.dev/healthz"
