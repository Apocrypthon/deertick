# 🦌 DeerTick

> Autonomous crypto trading agent — Discord-native, Claude-powered, exchange-agnostic.

Built on [DeerFlow](https://github.com/bytedance/deer-flow) + LangGraph.
Connects Robinhood, Coinbase Advanced Trade, and Alpaca paper trading to a Discord bot
that thinks, vetoes, and executes trades on your behalf.

---

## What it does

- **Rebalances every 5 minutes** — pulls live portfolio data, scans cross-market context,
  runs a risk veto layer, and posts structured recommendations with copy-trade tiers
- **Triangular arb scanner** — monitors BTC/ETH/SOL order books via WebSocket,
  routes opportunities through Claude with auto/show tier sizing
- **TradingView webhook ingestion** — RSI/MACD/VWAP alerts POST to ngrok → Claude evaluates
- **Parallel paper trading** — every signal executes on Alpaca paper alongside Coinbase live
  so you can compare P&L before flipping the live gate
- **Quantized market memory** — 8^4 = 4096-bit ring buffer persists last 16 market signals
  across restarts, feeds each rebalance with longitudinal context

---

## Architecture
```
TradingView alert
  → ngrok HTTPS (static domain)
    → FastAPI :8080 /webhook
      → MessageBus → DirectDispatcher
        → Claude claude-sonnet-4-6 (thinking=True)
          → 13 registered tools
            → Discord alert + copy-trade tiers
```

---

## 13 Tools

| Tool | Exchange | Gate |
|------|----------|------|
| `get_portfolio` | Robinhood (equity + crypto) | — |
| `get_quote` | Robinhood | — |
| `get_watchlist` | Robinhood | — |
| `place_order` | Robinhood | `TRADE_ENABLED=true` |
| `get_crypto_quote` | Coinbase Advanced Trade | — |
| `get_crypto_portfolio` | Coinbase Advanced Trade | — |
| `place_crypto_order` | Coinbase Advanced Trade | `TRADE_ENABLED=true` |
| `get_alpaca_portfolio` | Alpaca paper | — |
| `place_alpaca_order` | Alpaca paper | **none — always executes** |
| `cross_market_context` | RH + Coinbase live | — |
| `strategy_analyst` | Risk veto layer | — |
| `arb_scan` | ccxt WebSocket | — |
| `arb_execute` | Coinbase | `TRADE_ENABLED=true` |

---

## Copy-Trade Tiers

Every recommendation includes three tiers scaled to your portfolio equity:

| Tier | Size | Execution |
|------|------|-----------|
| AUTO | 1% of equity | Fires automatically when `TRADE_ENABLED=true` |
| SHOW-LOW | 5% of equity | Posted to Discord — you copy manually |
| SHOW-HIGH | 25% of equity | Posted to Discord — you copy manually |

---

## Quickstart

### 1 — Prerequisites

- Python 3.12+, [uv](https://docs.astral.sh/uv/)
- Discord bot token ([discord.com/developers](https://discord.com/developers))
- Anthropic API key ([console.anthropic.com](https://console.anthropic.com))
- Robinhood account
- Coinbase Advanced Trade API key (Ed25519)
- Alpaca paper account ([alpaca.markets](https://alpaca.markets)) — free, instant
- ngrok account ([ngrok.com](https://ngrok.com)) — free tier works

### 2 — Install
```bash
git clone https://github.com/Apocrypthon/deer-tick
cd deer-tick/backend
uv sync
```

### 3 — Configure
```bash
cp .env.example .env
vim .env  # fill in your keys
```

### 4 — Run
```bash
cd deer-tick
set -a && source .env && set +a
cd backend && uv run python discord_bridge.py
```

Expected startup output:
```
ArbScanner started (6 symbols)
Tools registered (13): [get_portfolio, ..., arb_scan, arb_execute]
ngrok tunnel: https://your-domain.ngrok-free.app
deer-tick#7045 connected to Gateway
AlertScheduler started — rebalance every 300s, arb every 120s
```

---

## TradingView Webhook

Set your alert webhook URL to:
```
https://your-domain.ngrok-free.app/webhook
```

Message body (JSON):
```json
{
  "symbol": "{{ticker}}",
  "side": "{{strategy.order.action}}",
  "price": {{close}},
  "secret": "your-TV_WEBHOOK_SECRET"
}
```

---

## Enabling Live Trading

DeerTick ships with `TRADE_ENABLED=false`. The full pipeline runs — Claude thinks,
strategy_analyst vetoes, Alpaca paper executes — but no real money moves.

When you're ready:
```bash
# Watch Alpaca paper P&L for 2+ profitable cycles first
sed -i 's/TRADE_ENABLED=false/TRADE_ENABLED=true/' .env
# Restart — AUTO tier (1%) activates on next recommendation
```

---

## Rate Limits

Anthropic free tier: **30,000 input tokens/minute** on Sonnet 4.6.

| Loop | Interval | Token cost |
|------|----------|------------|
| Rebalance | 300s | ~8k tokens (multi-tool chain) |
| Arb scan | 120s | ~1k tokens (read-only) |
| Threshold poll | 60s | no LLM call |
| User message | on demand | ~2-4k tokens |

Stay under: avoid manual queries while rebalance is running.

---

## Upgrading the Model
```bash
# Switch to Opus 4.6 for richer reasoning (higher token cost)
sed -i 's/claude-sonnet-4-6/claude-opus-4-6/g' backend/config.yaml
```

---

## Project Structure
```
deer-tick/
├── backend/
│   ├── discord_bridge.py          # single entry point
│   ├── config.yaml                # model + tool config
│   ├── src/
│   │   ├── arb/
│   │   │   ├── arb_scanner.py     # ccxt WebSocket order book monitor
│   │   │   └── arb_tools.py       # LangChain tool wrappers
│   │   ├── channels/
│   │   │   ├── discord_channel.py
│   │   │   ├── direct_dispatcher.py
│   │   │   └── message_bus.py
│   │   ├── scheduler/
│   │   │   ├── alert_scheduler.py # rebalance + threshold + arb loops
│   │   │   └── quant_memory.py    # 8^4 bit market signal ring buffer
│   │   ├── tools/
│   │   │   ├── robinhood_tools.py
│   │   │   ├── coinbase_tools.py
│   │   │   ├── alpaca_tools.py
│   │   │   └── strategy_tools.py
│   │   ├── deer_tick_client.py    # LangGraph agent client
│   │   └── webhook_server.py      # FastAPI TradingView ingestion
│   └── .deer-flow/
│       ├── checkpoints.db         # LangGraph SQLite state
│       └── memory.json            # long-term agent memory
├── .env.example
├── DEERTICK.md
└── README.md
```

---

## Built On

- [DeerFlow](https://github.com/bytedance/deer-flow) — ByteDance open-source agent framework
- [LangGraph](https://github.com/langchain-ai/langgraph) — stateful multi-agent orchestration
- [ccxt](https://github.com/ccxt/ccxt) — unified crypto exchange API
- [claude-sonnet-4-6](https://anthropic.com) — reasoning + tool use
- [discord.py](https://discordpy.readthedocs.io) — Discord gateway
- [Alpaca](https://alpaca.markets) — commission-free paper + live trading API
- [Coinbase Advanced Trade](https://docs.cdp.coinbase.com) — institutional crypto REST API

---

## License

MIT — fork it, extend it, share it.
