#!/usr/bin/env bash
# boot.sh — DeerTick cold-start script
# Usage: ./boot.sh [--model claude-opus-4-6] [--dry-run]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
BACKEND="$SCRIPT_DIR/backend"
UV="$HOME/.local/bin/uv"

# ── arg parse ──────────────────────────────────────────────────────────────────
DRY_RUN=false
for arg in "$@"; do
  case $arg in
    --model=*) MODEL="${arg#*=}"; sed -i "s/^DEERTICK_MODEL=.*/DEERTICK_MODEL=$MODEL/" "$ENV_FILE"; echo "[boot] Model override: $MODEL" ;;
    --dry-run) DRY_RUN=true ;;
  esac
done

# ── pre-flight checks ──────────────────────────────────────────────────────────
echo "[boot] DeerTick pre-flight..."

[ -f "$ENV_FILE" ] || { echo "ERROR: .env not found at $ENV_FILE"; exit 1; }

source "$ENV_FILE"

check_key() {
  local key=$1 val="${!1:-}"
  if [ -z "$val" ]; then
    echo "MISSING  $key"
    MISSING=1
  else
    echo "OK       $key"
  fi
}

MISSING=0
check_key ANTHROPIC_API_KEY
check_key DISCORD_BOT_TOKEN
check_key DISCORD_ALERT_CHANNEL_ID
check_key COINBASE_API_KEY
check_key COINBASE_API_SECRET
check_key ALPACA_API_KEY
check_key ALPACA_SECRET_KEY
check_key DEERTICK_MODEL
check_key TV_WEBHOOK_SECRET

[ $MISSING -eq 0 ] || { echo ""; echo "ERROR: Missing required keys above. Edit $ENV_FILE and retry."; exit 1; }

# ── optional keys ─────────────────────────────────────────────────────────────
[ -n "${NGROK_AUTHTOKEN:-}" ] && echo "OK       NGROK_AUTHTOKEN" || echo "WARN     NGROK_AUTHTOKEN not set (tunnel disabled — run ngrok http 8080 manually)"
[ -n "${ROBINHOOD_USERNAME:-}" ] && echo "OK       ROBINHOOD_USERNAME" || echo "WARN     ROBINHOOD_USERNAME not set (RH tools will fail)"

# ── model confirmation ─────────────────────────────────────────────────────────
echo ""
echo "[boot] Active model:    $DEERTICK_MODEL"
echo "[boot] Trade gate:      ${TRADE_ENABLED:-false}"
echo "[boot] Arb capital cap: ${ARB_MAX_TRADE_USD:-50} per leg"
echo "[boot] Rebalance cycle: ${REBALANCE_INTERVAL_SEC:-300}s"
echo "[boot] Arb scan cycle:  ${ARB_SCAN_INTERVAL_SEC:-120}s"
echo ""

$DRY_RUN && { echo "[boot] Dry run complete — not starting agent."; exit 0; }

# ── quick API smoke test ───────────────────────────────────────────────────────
echo "[boot] Testing Anthropic API..."
cd "$BACKEND"
set -a && source "$ENV_FILE" && set +a
"$UV" run python -c "
import anthropic, os
c = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])
r = c.messages.create(
    model=os.environ['DEERTICK_MODEL'],
    max_tokens=5,
    messages=[{'role':'user','content':'hi'}]
)
print('[boot] API OK —', r.model)
" || { echo "ERROR: Anthropic API check failed — check key and credits"; exit 1; }

# ── launch ─────────────────────────────────────────────────────────────────────
echo "[boot] Starting DeerTick..."
echo ""
exec "$UV" run python discord_bridge.py
