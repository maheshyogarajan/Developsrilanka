#!/usr/bin/env bash
# ============================================================
# FIESTA Smoke Test Runner
# Usage:
#   ./run-smoke.sh                          # use default BASE_URL
#   BASE_URL=https://fiesta.developsrilanka.com ./run-smoke.sh
#   TEST_EMAIL=me@example.com TEST_PASSWORD=pass ./run-smoke.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 1. Resolve BASE_URL ─────────────────────────────────────
INFRA_REPORT="../../../../../../my drive/ceo os/working files/_cockpit_fiesta/SUB_C_INFRA_REPORT.md"
if [[ -z "${BASE_URL:-}" ]]; then
  if [[ -f "$INFRA_REPORT" ]]; then
    # Parse first https:// URL from the report
    PARSED_URL=$(grep -oP 'https://[^\s"]+' "$INFRA_REPORT" | grep -v 'fly.dev/metrics\|supabase\|example' | head -1 || true)
    if [[ -n "$PARSED_URL" ]]; then
      export BASE_URL="$PARSED_URL"
      echo "[run-smoke] BASE_URL from SUB_C_INFRA_REPORT: $BASE_URL"
    else
      export BASE_URL="https://fiesta-mvp.fly.dev"
      echo "[run-smoke] BASE_URL defaulted to: $BASE_URL"
    fi
  else
    export BASE_URL="https://fiesta-mvp.fly.dev"
    echo "[run-smoke] SUB_C_INFRA_REPORT not found. BASE_URL defaulted to: $BASE_URL"
  fi
else
  echo "[run-smoke] BASE_URL from env: $BASE_URL"
fi

# ── 2. Run Playwright ────────────────────────────────────────
mkdir -p test-results

npx playwright test smoke/ \
  --reporter=list \
  --reporter=html \
  --output=test-results

echo ""
echo "[run-smoke] Done. HTML report: $SCRIPT_DIR/playwright-report/index.html"
echo "[run-smoke] JUnit XML: $SCRIPT_DIR/test-results/results.xml"
