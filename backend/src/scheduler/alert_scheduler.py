"""alert_scheduler.py — autonomous rebalancing, threshold alerts, and arb scanner.

Three loops run concurrently:
  1. rebalance_loop  — every REBALANCE_INTERVAL_SEC (default 300s)
                       pulls portfolio, asks Claude for the best trade,
                       posts recommendation with copy-trade tiers to Discord.
  2. threshold_loop  — every THRESHOLD_POLL_SEC (default 60s)
                       checks VIX proxy and watchlist price moves.
  3. arb_loop        — every ARB_SCAN_INTERVAL_SEC (default 300s)
                       reads ArbScanner, routes opportunities through Claude
                       as quomodocunquizing invocations with copy-trade sizing.

Copy-trade tier system:
  AUTO tier  (ARB_AUTO_PCT,  default 1%)  — micro-execute immediately, no CONFIRM
  SHOW tier  (ARB_SHOW_MIN%–ARB_SHOW_MAX%, default 5%–25%) — shown to user to copy
  Both tiers are included in every arb Discord post.
"""

from __future__ import annotations

import asyncio
import pathlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from src.scheduler.quant_memory import QuantMemory, MarketSignal, get_qmem

logger = logging.getLogger(__name__)

# ── tunables ──────────────────────────────────────────────────────────────────
REBALANCE_INTERVAL_SEC  = int(os.environ.get("REBALANCE_INTERVAL_SEC", "300"))
THRESHOLD_POLL_SEC      = int(os.environ.get("THRESHOLD_POLL_SEC", "60"))
ARB_SCAN_INTERVAL_SEC   = int(os.environ.get("ARB_SCAN_INTERVAL_SEC", "60"))
VIX_ALERT_THRESHOLD     = float(os.environ.get("VIX_ALERT_THRESHOLD", "25.0"))
VIX_VETO_THRESHOLD      = float(os.environ.get("VIX_VETO_THRESHOLD", "35.0"))
PRICE_MOVE_PCT          = float(os.environ.get("PRICE_MOVE_PCT", "3.0"))
ARB_AUTO_PCT            = float(os.environ.get("ARB_AUTO_PCT", "0.01"))    # 1%  auto-execute
ARB_SHOW_MIN_PCT        = float(os.environ.get("ARB_SHOW_MIN_PCT", "0.05")) # 5%  copy-low
ARB_SHOW_MAX_PCT        = float(os.environ.get("ARB_SHOW_MAX_PCT", "0.25")) # 25% copy-high
ARB_MIN_PROFIT_PCT      = float(os.environ.get("ARB_MIN_PROFIT_PCT", "0.10")) # floor to alert
MARKET_OPEN_ET_HOUR     = 9
MARKET_OPEN_ET_MIN      = 30
MARKET_CLOSE_ET_HOUR    = 16

# ── rebalance prompt ──────────────────────────────────────────────────────────
_REBALANCE_PROMPT = """You are an autonomous portfolio optimizer. Your job is to find the single most quomodocunquizing trade available right now.

Steps you MUST follow in order:
1. Call get_portfolio() to see current holdings and P&L.
2. Call get_watchlist() to see tracked symbols.
3. For the top 3 most interesting symbols, call cross_market_context(symbol).
4. Call strategy_analyst(symbol, quantity, side, reason) for your top candidate.
5. If APPROVE: state the exact trade and copy-trade tiers (see format below).
   If VETO: explain why and state the next-best alternative.

End your response with this exact block:
---
RECOMMENDATION: [BUY/SELL/HOLD] [SYMBOL] [QTY]
CONFIDENCE: [HIGH/MEDIUM/LOW]
REASONING: [one sentence]
EST. IMPACT: [+/- $X.XX on portfolio]
COPY-TRADE:
  AUTO (1%):   [BUY/SELL] [SYMBOL] $[auto_usd]
  SHOW (5%):   [BUY/SELL] [SYMBOL] $[show_low_usd]
  SHOW (25%):  [BUY/SELL] [SYMBOL] $[show_high_usd]
---

Current time: {timestamp}
Quantized market memory (last 5 signals):
{qmem_ctx}
TRADE_ENABLED: {trade_enabled}
Portfolio capital reference: ${capital_usd:.2f}
"""

# ── arb invocation prompt ─────────────────────────────────────────────────────
_ARB_PROMPT = """QUOMODOCUNQUIZING ARB ALERT — triangular arbitrage opportunity detected.

Triangle: {name}
Estimated profit: {profit_pct:+.4f}% on ${capital:.2f} base capital
Gross profit estimate: ${gross:.4f}

Legs:
{legs}

Your task:
1. Call arb_scan() to get live order book confirmation.
2. Evaluate whether spread has held since detection ({age}s ago).
3. Call strategy_analyst with: symbol={first_sym}, side={first_side}, quantity=1, reason="triangular arb {name} at {profit_pct:.4f}% spread"
4. If APPROVE and profit still >= 0.30%: state execution plan with copy-trade tiers.
   If VETO or spread collapsed: explain and stand down.

Copy-trade tiers to include in response:
  AUTO tier  = ${auto_usd:.2f} (1% of capital — executes automatically if TRADE_ENABLED)
  SHOW 5%    = ${show_low:.2f}
  SHOW 25%   = ${show_high:.2f}

Respond with a structured block:
---
ARB VERDICT: [EXECUTE/HOLD/EXPIRED]
PROFIT_PCT: [actual % from arb_scan]
AUTO: [execute ${auto_usd:.2f} across 3 legs — Y/N]
COPY-LOW:  BUY {first_sym} ${show_low:.2f}
COPY-HIGH: BUY {first_sym} ${show_high:.2f}
---
TRADE_ENABLED: {trade_enabled}
"""


@dataclass
class ThresholdState:
    vix_last: float = 0.0
    vix_alerted_at: float = 0.0
    price_baselines: dict[str, float] = field(default_factory=dict)
    price_alerted: dict[str, float] = field(default_factory=dict)
    last_rebalance: float = 0.0
    last_arb_alert: float = 0.0


class AlertScheduler:

    def __init__(self, bus, store, target_channel_id: str, client=None) -> None:
        self.bus = bus
        self.store = store
        self.target_channel_id = target_channel_id
        self._client = client
        self._state = ThresholdState()
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._capital_usd: Optional[float] = None

    def _get_client(self):
        if self._client is None:
            from src.deer_tick_client import DeerTickClient
            # No thinking on scheduler — cuts token cost ~60%, avoids 429s
            self._client = DeerTickClient(thinking_enabled=False)
        return self._client

    def _get_rh(self):
        from src.tools.robinhood_tools import _get_session
        return _get_session()

    def _get_capital(self) -> float:
        """Best-effort portfolio equity for sizing. Falls back to ARB_MAX_TRADE_USD*20."""
        if self._capital_usd:
            return self._capital_usd
        try:
            rh = self._get_rh()
            profiles = rh.profiles.load_portfolio_profile()
            equity = float(profiles.get("equity") or profiles.get("extended_hours_equity") or 0)
            if equity > 0:
                self._capital_usd = equity
                return equity
        except Exception:
            pass
        return float(os.environ.get("ARB_MAX_TRADE_USD", "50")) * 20

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._rebalance_loop()),
            asyncio.create_task(self._threshold_loop()),
            asyncio.create_task(self._arb_loop()),
        ]
        logger.info(
            "AlertScheduler started — rebalance every %ds, threshold poll every %ds, arb every %ds",
            REBALANCE_INTERVAL_SEC, THRESHOLD_POLL_SEC, ARB_SCAN_INTERVAL_SEC,
        )

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        logger.info("AlertScheduler stopped")

    # ── market hours ──────────────────────────────────────────────────────────

    @staticmethod
    def _market_is_open() -> bool:
        import datetime
        now_utc = datetime.datetime.utcnow()
        et_hour = (now_utc.hour - 4) % 24
        et_min  = now_utc.minute
        weekday = now_utc.weekday()
        if weekday >= 5:
            return False
        after_open   = (et_hour > MARKET_OPEN_ET_HOUR) or                        (et_hour == MARKET_OPEN_ET_HOUR and et_min >= MARKET_OPEN_ET_MIN)
        before_close = et_hour < MARKET_CLOSE_ET_HOUR
        return after_open and before_close

    # ── outbound helper ───────────────────────────────────────────────────────

    async def _send(self, text: str) -> None:
        from src.channels.message_bus import OutboundMessage
        msg = OutboundMessage(
            channel_name="discord",
            chat_id=self.target_channel_id,
            thread_id="scheduler",
            text=text,
        )
        await self.bus.publish_outbound(msg)

    async def _send_as_inbound(self, text: str, thread_id: str) -> None:
        """Route text through Claude (full agent pipeline) via InboundMessage."""
        from src.channels.message_bus import InboundMessage
        msg = InboundMessage(
            channel_name="discord",
            chat_id=self.target_channel_id,
            thread_id=thread_id,
            text=text,
            msg_type="chat",
        )
        await self.bus.publish_inbound(msg)

    # ── rebalance loop ────────────────────────────────────────────────────────

    async def _rebalance_loop(self) -> None:
        await asyncio.sleep(10)
        while self._running:
            try:
                await self._run_rebalance()
            except Exception:
                logger.exception("Rebalance loop error")
            await asyncio.sleep(REBALANCE_INTERVAL_SEC)

    async def _run_rebalance(self) -> None:
        import datetime
        from concurrent.futures import ThreadPoolExecutor
        logger.info("Running rebalance analysis...")
        await self._send("🔄 **Rebalance scan started…**")
        capital = self._get_capital()
        # Inject quantized memory context into prompt
        try:
            qmem_ctx = get_qmem().summary(5)
        except Exception:
            qmem_ctx = "QMem unavailable"
        prompt = _REBALANCE_PROMPT.format(
            timestamp=datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            trade_enabled=os.environ.get("TRADE_ENABLED", "false"),
            qmem_ctx=qmem_ctx,
            capital_usd=capital,
        )
        loop = asyncio.get_event_loop()
        client = self._get_client()
        try:
            executor = ThreadPoolExecutor(max_workers=1)
            response = await loop.run_in_executor(
                executor,
                lambda: client.chat(prompt, thread_id="scheduler-rebalance"),
            )
            if response:
                await self._send(f"📊 **Rebalance Analysis**\n\n{response}")
            else:
                await self._send("Rebalance returned no response.")
        except Exception as e:
            logger.exception("Rebalance Claude call failed")
            await self._send(f"Rebalance error: {e}")
        self._state.last_rebalance = time.time()
        # Push quantized memory signal
        try:
            qm = get_qmem()
            vix = self._state.vix_last or 20.0
            capital = self._get_capital()
            sig = MarketSignal(
                vix_fear=vix,
                signal_type=0,  # rebalance
            )
            qm.push(sig)
            logger.debug("QMem: %s | %d/%d bits", sig.summary(), qm.bits_used, qm.bits_total)
        except Exception as _qe:
            logger.debug("QMem push failed: %s", _qe)

    # ── arb loop ─────────────────────────────────────────────────────────────

    async def _arb_loop(self) -> None:
        """Every ARB_SCAN_INTERVAL_SEC: check scanner, route opportunities through Claude."""
        await asyncio.sleep(30)  # let order books warm up first
        while self._running:
            try:
                await self._run_arb_scan()
            except Exception:
                logger.exception("Arb loop error")
            await asyncio.sleep(ARB_SCAN_INTERVAL_SEC)

    async def _run_arb_scan(self) -> None:
        from src.arb import arb_tools as _arb_tools
        scanner = _arb_tools._scanner
        if scanner is None:
            return
        if scanner.symbols_ready < scanner.total_symbols:
            logger.debug("Arb scan skipped — books warming (%d/%d)",
                         scanner.symbols_ready, scanner.total_symbols)
            return

        opp = scanner.get_latest()
        if opp is None or opp.profit_pct < ARB_MIN_PROFIT_PCT:
            logger.debug("Arb scan: no opportunity above %.2f%%", ARB_MIN_PROFIT_PCT)
            return

        # cooldown — don't flood Discord with same triangle
        now = time.time()
        if now - self._state.last_arb_alert < ARB_SCAN_INTERVAL_SEC * 0.9:
            return

        self._state.last_arb_alert = now
        capital = self._get_capital()
        auto_usd  = capital * ARB_AUTO_PCT
        show_low  = capital * ARB_SHOW_MIN_PCT
        show_high = capital * ARB_SHOW_MAX_PCT

        first_sym  = opp.legs[0][0] if opp.legs else "BTC/USD"
        first_side = opp.legs[0][1] if opp.legs else "buy"
        legs_str   = "\n".join(
            f"  {side.upper():4s}  {sym:<10s}  @ {price:.6g}"
            for sym, side, price in opp.legs
        )
        age = round(now - opp.detected_at, 1)

        try:
            qmem_ctx = get_qmem().summary(3)
        except Exception:
            qmem_ctx = "QMem unavailable"

        prompt = _ARB_PROMPT.format(
            name=opp.name,
            profit_pct=opp.profit_pct,
            capital=capital,
            gross=opp.gross_profit_usd,
            legs=legs_str,
            age=age,
            first_sym=first_sym,
            first_side=first_side,
            auto_usd=auto_usd,
            show_low=show_low,
            show_high=show_high,
            trade_enabled=os.environ.get("TRADE_ENABLED", "false"),
            qmem_ctx=qmem_ctx,
        )

        logger.info(
            "Arb opportunity routed to Claude: %s %+.4f%% | auto=$%.2f show=$%.2f-$%.2f",
            opp.name, opp.profit_pct, auto_usd, show_low, show_high,
        )
        try:
            qm = get_qmem()
            qm.push(MarketSignal(arb_profit_pct=opp.profit_pct, signal_type=1))
        except Exception:
            pass

        # Route through full agent pipeline — Claude gets tools, veto layer, etc.
        await self._send_as_inbound(prompt, thread_id=f"arb-{opp.name}-{int(now)}")

    # ── threshold loop ────────────────────────────────────────────────────────

    async def _threshold_loop(self) -> None:
        await asyncio.sleep(15)
        await self._seed_baselines()
        while self._running:
            try:
                await self._check_vix()
                await self._check_price_moves()
            except Exception:
                logger.exception("Threshold loop error")
            await asyncio.sleep(THRESHOLD_POLL_SEC)

    async def _seed_baselines(self) -> None:
        loop = asyncio.get_event_loop()
        try:
            rh = self._get_rh()
            watchlist = await loop.run_in_executor(
                None,
                lambda: rh.account.get_watchlist_by_name("Cryptos to Watch"),
            )
            symbols = [
                item if isinstance(item, str) else item.get("symbol", "")
                for item in (watchlist or [])
            ]
            symbols = [s for s in symbols if s and s.isupper() and s.isalpha() and 1 <= len(s) <= 6]
            if symbols:
                quotes = await loop.run_in_executor(
                    None, lambda: rh.stocks.get_quotes(symbols)
                )
                for quote in (quotes or []):
                    if not quote:
                        continue
                    symbol = quote.get("symbol")
                    # Prioritize extended hours price to match original get_latest_price behavior
                    raw_price = quote.get("last_extended_hours_trade_price") or quote.get("last_trade_price")
                    if symbol and raw_price:
                        price = float(raw_price)
                        if price > 0:
                            self._state.price_baselines[symbol] = price
            logger.info("Price baselines seeded: %s", self._state.price_baselines)
        except Exception:
            logger.exception("Failed to seed price baselines")

    async def _check_vix(self) -> None:
        loop = asyncio.get_event_loop()
        try:
            rh = self._get_rh()
            raw = await loop.run_in_executor(None, lambda: rh.stocks.get_latest_price("VIXY"))
            vix = float(raw[0]) if raw and raw[0] else 0.0
            if vix == 0.0:
                return
            now = time.time()
            cooldown = 1800
            # Load persisted alert time so cooldown survives restarts
            _vix_state_path = pathlib.Path(".deer-flow/vix_alerted_at.txt")
            try:
                self._state.vix_alerted_at = float(_vix_state_path.read_text())
            except Exception:
                pass

            if vix >= VIX_VETO_THRESHOLD:
                if now - self._state.vix_alerted_at > cooldown:
                    await self._send(
                        f"🚨 **EXTREME FEAR** — VIXY ${vix:.2f} above veto threshold ${VIX_VETO_THRESHOLD}\n"
                        f"All buy orders vetoed. Defensive positioning recommended."
                    )
                    self._state.vix_alerted_at = now
                    _vix_state_path.parent.mkdir(exist_ok=True)
                    _vix_state_path.write_text(str(now))
            elif vix >= VIX_ALERT_THRESHOLD:
                if now - self._state.vix_alerted_at > cooldown:
                    await self._send(
                        f"⚠️ **ELEVATED FEAR** — VIXY ${vix:.2f} above caution threshold ${VIX_ALERT_THRESHOLD}"
                    )
                    self._state.vix_alerted_at = now
                    _vix_state_path.parent.mkdir(exist_ok=True)
                    _vix_state_path.write_text(str(now))
            self._state.vix_last = vix
        except Exception:
            logger.exception("VIX check failed")

    async def _check_price_moves(self) -> None:
        loop = asyncio.get_event_loop()
        try:
            rh = self._get_rh()
            # Snapshot to avoid "dictionary changed size during iteration"
            baselines = list(self._state.price_baselines.items())
            symbols = [s for s, b in baselines if b > 0.0]
            if not symbols:
                return

            # get_quotes is safer than get_latest_price for batching as it returns symbol info
            quotes = await loop.run_in_executor(
                None, lambda: rh.stocks.get_quotes(symbols)
            )

            for quote in (quotes or []):
                if not quote:
                    continue
                symbol = quote.get("symbol")
                # Prioritize extended hours price to match original get_latest_price behavior
                raw_price = quote.get("last_extended_hours_trade_price") or quote.get("last_trade_price")

                baseline = self._state.price_baselines.get(symbol)
                if not baseline or not raw_price:
                    continue

                price = float(raw_price)
                if price == 0.0:
                    continue

                pct_move = ((price - baseline) / baseline) * 100
                now = time.time()
                last_alerted = self._state.price_alerted.get(symbol, 0.0)
                cooldown = 900
                if abs(pct_move) >= PRICE_MOVE_PCT and now - last_alerted > cooldown:
                    direction = "📈" if pct_move > 0 else "📉"
                    await self._send(
                        f"{direction} **Price Move: {symbol}** "
                        f"${baseline:.2f} → ${price:.2f} ({pct_move:+.1f}%)"
                    )
                    self._state.price_alerted[symbol] = now
                    self._state.price_baselines[symbol] = price
        except Exception:
            logger.exception("Price move check failed")
