#!/usr/bin/env bash
# demo/break_upstream.sh
# ─────────────────────────────────────────────────────────────────────────────
# Demo 1 — Cascading upstream failure.
#
# Breaks stg_orders by introducing a bad column reference, runs the full
# pipeline, then shows the cascading FAIL spans in SigNoz: stg_orders FAIL
# propagates to fct_orders FAIL, which propagates to fct_customer_ltv FAIL.
#
# Restores the original file at the end.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_DIR="$REPO_ROOT/demo/dbt_project"
MODEL="$DEMO_DIR/models/staging/stg_orders.sql"
HYRAX_PORT="${HYRAX_PORT:-5050}"
SIGNOZ_UI="${SIGNOZ_UI:-http://localhost:3301}"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

banner() { echo -e "${BLUE}▶ $1${NC}"; }
ok()     { echo -e "${GREEN}✓ $1${NC}"; }
fail()   { echo -e "${RED}✗ $1${NC}"; }

# Ensure Hyrax is running
if ! curl -sf "http://localhost:$HYRAX_PORT/health" > /dev/null 2>&1; then
  echo "Hyrax is not running. Start it first: uv run python -m hyrax.listener"
  exit 1
fi

# Save original
cp "$MODEL" "$MODEL.bak"

cleanup() {
  banner "Restoring original stg_orders.sql…"
  mv "$MODEL.bak" "$MODEL"
  ok "Restored."
}
trap cleanup EXIT

# ── Inject the break ─────────────────────────────────────────────────────────
banner "Breaking stg_orders.sql (introducing non-existent column 'this_column_does_not_exist')…"
cat > "$MODEL" << 'EOF'
-- INTENTIONALLY BROKEN for demo purposes
with source as (
    select * from {{ ref('raw_orders') }}
),
staged as (
    select
        order_id,
        customer_id,
        this_column_does_not_exist  -- << BREAKS HERE
    from source
)
select * from staged
EOF
fail "stg_orders.sql broken — this will cause fct_orders and fct_customer_ltv to cascade"

# ── Run pipeline (expect failure) ────────────────────────────────────────────
banner "Running broken pipeline (OpenLineage FAIL events will flow to Hyrax)…"
cd "$DEMO_DIR"
OPENLINEAGE_URL="http://localhost:$HYRAX_PORT" \
OPENLINEAGE_NAMESPACE="hyrax-demo-break" \
  uv run --with dbt-core --with dbt-duckdb --with openlineage-dbt \
  dbt-ol run --profiles-dir . --project-dir . --target dev || true

echo ""
echo -e "${RED}Expected failure! Check SigNoz for cascading FAIL spans:${NC}"
echo "  $SIGNOZ_UI/traces"
echo ""
echo "You should see:"
echo "  stg_orders    → ERROR (column not found)"
echo "  fct_orders    → ERROR (upstream dependency failed)"
echo "  fct_customer_ltv → ERROR (upstream dependency failed)"

# cleanup runs via trap
