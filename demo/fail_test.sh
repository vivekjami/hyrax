#!/usr/bin/env bash
# demo/fail_test.sh
# ─────────────────────────────────────────────────────────────────────────────
# Demo 2 — dbt test failure as a span event.
#
# Seeds a row with an invalid status ('cancelled' — not in accepted_values),
# runs dbt test, shows the test.failed span event in SigNoz with
# actual="cancelled", expected="[completed, shipped, placed, returned,
# return_pending]", severity="ERROR".
#
# Restores the original seed file at the end.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_DIR="$REPO_ROOT/demo/dbt_project"
SEED="$DEMO_DIR/seeds/raw_orders.csv"
HYRAX_PORT="${HYRAX_PORT:-5050}"
SIGNOZ_UI="${SIGNOZ_UI:-http://localhost:8080}"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

banner() { echo -e "${BLUE}▶ $1${NC}"; }
ok()     { echo -e "${GREEN}✓ $1${NC}"; }

if ! curl -sf "http://localhost:$HYRAX_PORT/health" > /dev/null 2>&1; then
  echo "Hyrax is not running. Start it first: uv run python -m hyrax.listener"
  exit 1
fi

cp "$SEED" "$SEED.bak"
cleanup() {
  banner "Restoring original raw_orders.csv…"
  mv "$SEED.bak" "$SEED"
  ok "Restored."
}
trap cleanup EXIT

# ── Append a bad row ─────────────────────────────────────────────────────────
banner "Injecting invalid status='cancelled' into raw_orders seed…"
echo "9999,C001,2024-05-01,cancelled,99.99,us-east" >> "$SEED"

# Re-seed so DuckDB picks up the new row
cd "$DEMO_DIR"
OPENLINEAGE_URL="http://localhost:$HYRAX_PORT" \
OPENLINEAGE_NAMESPACE="hyrax-demo-failtest" \
  uv run --with dbt-core --with dbt-duckdb --with openlineage-dbt \
  dbt-ol seed --profiles-dir . --project-dir . --target dev --full-refresh

# ── Run tests (accepted_values test will fail) ───────────────────────────────
banner "Running dbt tests — accepted_values will fire test.failed span event…"
OPENLINEAGE_URL="http://localhost:$HYRAX_PORT" \
OPENLINEAGE_NAMESPACE="hyrax-demo-failtest" \
  uv run --with dbt-core --with dbt-duckdb --with openlineage-dbt \
  dbt-ol test --profiles-dir . --project-dir . --target dev || true

echo ""
echo -e "${RED}Test failed as expected! Check SigNoz for the span event:${NC}"
echo "  $SIGNOZ_UI/traces"
echo ""
echo "Look for a span event named 'test.failed' with attributes:"
echo "  assertion.column   = status"
echo "  assertion.actual   = cancelled"
echo "  assertion.severity = ERROR"

# cleanup runs via trap
