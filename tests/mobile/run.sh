#!/usr/bin/env bash
# Tier C-5 mobile audit — runner script.
#
# Usage:
#   ./run.sh before     # capture before-baseline snapshots
#   ./run.sh after      # capture after-fix snapshots
#   ./run.sh regression # run viewport regression test (test_viewports.spec.ts)
#
# Sets NODE_PATH so the playwright module resolves from tests/playwright/node_modules.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PW_DIR="$ROOT/tests/playwright"

cd "$PW_DIR"

export NODE_PATH="$PW_DIR/node_modules"
export BASE_URL="${BASE_URL:-https://fiesta-mvp.fly.dev}"

case "${1:-before}" in
  before|after)
    export MOBILE_AUDIT_PHASE="$1"
    npx playwright test --config "$HERE/playwright.config.ts" --grep "${1} " "${@:2}"
    ;;
  regression)
    npx playwright test --config "$HERE/playwright.config.ts" --grep "viewport regression" "${@:2}"
    ;;
  all)
    export MOBILE_AUDIT_PHASE="${1}"
    npx playwright test --config "$HERE/playwright.config.ts" "${@:2}"
    ;;
  *)
    echo "Usage: $0 {before|after|regression|all} [extra-playwright-args...]" >&2
    exit 2
    ;;
esac
