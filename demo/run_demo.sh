#!/usr/bin/env bash
# demo/run_demo.sh
# ─────────────────────────────────────────────────────────────────────────────
# Full end-to-end Hyrax demo.
# Prerequisites:
#   - SigNoz running locally (./install.sh from signoz/deploy)
#   - uv installed: https://docs.astral.sh/uv/getting-started/installation/
#
# Usage:
#   cd <repo-root>
#   ./demo/run_demo.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_DIR="$REPO_ROOT/demo/dbt_project"
HYRAX_PORT="${HYRAX_PORT:-5050}"
OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4317}"
SIGNOZ_UI="${SIGNOZ_UI:-http://localhost:3301}"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

banner() { echo -e "${BLUE}▶ $1${NC}"; }
ok()     { echo -e "${GREEN}✓ $1${NC}"; }
info()   { echo -e "${YELLOW}ℹ $1${NC}"; }

# ── 1. Install deps ──────────────────────────────────────────────────────────
banner "Installing Hyrax + demo dependencies via uv…"
cd "$REPO_ROOT"
uv sync
uv sync --extra demo
ok "Dependencies ready"

# ── 2. Start Hyrax listener ──────────────────────────────────────────────────
banner "Starting Hyrax listener on port $HYRAX_PORT…"
OTEL_EXPORTER_OTLP_ENDPOINT="$OTLP_ENDPOINT" \
HYRAX_PORT="$HYRAX_PORT" \
  uv run python -m hyrax.listener &
HYRAX_PID=$!
trap "kill $HYRAX_PID 2>/dev/null; exit" INT TERM EXIT

# Wait for listener to be ready
for i in $(seq 1 20); do
  if curl -sf "http://localhost:$HYRAX_PORT/health" > /dev/null 2>&1; then
    ok "Hyrax listener ready (PID $HYRAX_PID)"
    break
  fi
  sleep 0.5
done

# ── 3. Run dbt pipeline ──────────────────────────────────────────────────────
banner "Running dbt pipeline (seeds → staging → marts)…"
cd "$DEMO_DIR"

OPENLINEAGE_URL="http://localhost:$HYRAX_PORT" \
OPENLINEAGE_NAMESPACE="hyrax-demo" \
  uv run --with dbt-core --with dbt-duckdb --with openlineage-dbt \
  dbt-ol run --profiles-dir . --project-dir . --target dev

ok "dbt run complete"

# ── 4. Run dbt tests ─────────────────────────────────────────────────────────
banner "Running dbt tests (fires dataQualityAssertions events)…"
OPENLINEAGE_URL="http://localhost:$HYRAX_PORT" \
OPENLINEAGE_NAMESPACE="hyrax-demo" \
  uv run --with dbt-core --with dbt-duckdb --with openlineage-dbt \
  dbt-ol test --profiles-dir . --project-dir . --target dev || true

ok "dbt tests complete"

# ── 5. Health check ──────────────────────────────────────────────────────────
banner "Hyrax bridge stats:"
curl -sf "http://localhost:$HYRAX_PORT/health" | python3 -m json.tool

echo ""
info "Pipeline complete! Open SigNoz to view your traces:"
info "  Traces  →  $SIGNOZ_UI/traces"
info "  Metrics →  $SIGNOZ_UI/metrics"
info ""
info "Import dashboards/signoz-dashboards.json to get the pre-built panels."

wait $HYRAX_PID
