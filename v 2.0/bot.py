"""
Swing Trading Bot - Main Orchestrator
Runs the scanning loop, executes trades, and manages the bot lifecycle.
"""

import logging
import asyncio
import signal
import sys
import json
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application

from config import config
from database import db
from exchange import exchange
from strategy import strategy
from risk_manager import risk_manager
from portfolio import portfolio
from notifications import notifier

# ─── LOGGING SETUP ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("bot")


class SwingBot:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.telegram_app = None
        self._scan_count = 0

    # ═══════════════════════════════════════
    # MAIN SCAN CYCLE (every 15 min)
    # ═══════════════════════════════════════

    async def scan_cycle(self):
        """Main scanning loop - runs every 15 minutes."""
        try:
            logger.info("─── Scan cycle starting ───")
            db.clear_expired_cooldowns()

            status = db.get_state("bot_status", "scanning")
            if status == "paused":
                logger.info("Bot is paused, skipping scan")
                return

            all_signals = {}

            for symbol in config.pairs.symbols:
                try:
                    signals = await self._analyze_symbol(symbol)
                    all_signals[symbol] = signals

                    # Check for force sell
                    if db.get_state(f"force_sell_{symbol}") == "pending":
                        await self._force_sell(symbol)
                        db.set_state(f"force_sell_{symbol}", "")
                        continue

                    # Execute buy if score is high enough
                    if signals["buy_score"] >= config.scoring.buy_light:
                        await self._execute_buy(symbol, signals)

                    # Execute sell if score is high enough
                    if signals["sell_score"] >= config.scoring.sell_partial:
                        await self._execute_sell(symbol, signals)

                    # Check stop-losses on open positions
                    await self._check_stop_losses(symbol, signals["indicators"]["price"])

                except Exception as e:
                    logger.error(f"Error analyzing {symbol}: {e}")
                    notifier.send_sync(notifier.format_error(f"Scan {symbol}", str(e)))

            # Save state
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            db.set_state("last_scan_time", now)

            # Store signals for /signals command
            signals_text = notifier.format_scanning_report(all_signals)
            db.set_state("last_signals", signals_text)

            # Store zones for /zones command
            zones_text = self._format_zones(all_signals)
            db.set_state("last_zones", zones_text)

            # Periodic scanning report (every 6 hours = 24 scans)
            self._scan_count += 1
            if self._scan_count % (config.reporting.scanning_report_interval_hours * 4) == 0:
                notifier.send_sync(signals_text)

            logger.info("─── Scan cycle complete ───")

        except Exception as e:
            logger.error(f"Scan cycle error: {e}")
            notifier.send_sync(notifier.format_error("Scan cycle", str(e)))

    async def _analyze_symbol(self, symbol: str) -> dict:
        """Analyze a symbol and return scores + indicators."""
        # Fetch market data
        candles_4h = exchange.get_ohlcv(symbol, interval="240", limit=100)
        candles_daily = exchange.get_ohlcv(symbol, interval="D", limit=90)

        # Compute indicators
        indicators = strategy.compute_indicators(candles_4h, candles_daily)

        # Get open positions for this symbol
        open_positions = db.get_open_positions(symbol)

        # Calculate scores
        buy_score, buy_details = strategy.calc_buy_score(indicators)
        sell_score, sell_details = strategy.calc_sell_score(indicators, open_positions)

        return {
            "indicators": indicators,
            "buy_score": buy_score,
            "buy_details": buy_details,
            "sell_score": sell_score,
            "sell_details": sell_details,
        }

    # ═══════════════════════════════════════
    # TRADE EXECUTION
    # ═══════════════════════════════════════

    async def _execute_buy(self, symbol: str, signals: dict):
        """Execute a buy based on signals."""
        buy_score = signals["buy_score"]
        indicators = signals["indicators"]
        price = indicators["price"]

        # Get portfolio state
        portfolio_data = portfolio.get_total_value()
        available = portfolio_data["available_usdt"]
        total = portfolio_data["total_usdt"]

        # Calculate amount to buy
        amount = strategy.calc_buy_amount(buy_score, available, total)
        if amount <= 0:
            logger.info(f"{symbol}: Buy score {buy_score} but insufficient funds or below minimum")
            return

        # Risk check
        allowed, reason = risk_manager.can_buy(symbol, amount, available, total)
        if not allowed:
            logger.info(f"{symbol}: Buy blocked - {reason}")
            return

        # Check leverage opportunity
        use_leverage = False
        leverage = 1.0
        if buy_score >= config.risk.leverage_score_threshold:
            lev_ok, lev_reason = risk_manager.can_use_leverage(symbol, total)
            if lev_ok:
                use_leverage = True
                leverage = config.risk.leverage_max
                logger.info(f"{symbol}: Using {leverage}x leverage (score {buy_score})")

        try:
            # Execute order
            if use_leverage:
                # Futures order
                qty = amount / price
                result = exchange.place_futures_market(symbol, "Buy", qty, leverage)
                trade_type = "futures"
            else:
                # Spot order
                result = exchange.place_spot_market_buy(symbol, amount)
                trade_type = "spot"

            fill_price = result.get("price", price)
            fill_qty = result.get("qty", amount / price)
            fill_value = result.get("value_usdt", amount)
            fill_fee = result.get("fee", 0)

            # Calculate stop-loss
            sl_price = risk_manager.calc_stop_loss_price(fill_price, leverage)

            # Record trade
            db.record_trade(
                symbol=symbol, side="Buy", qty=fill_qty, price=fill_price,
                value_usdt=fill_value, fee_usdt=fill_fee, score=buy_score,
                score_details=signals["buy_details"],
                order_id=result.get("order_id", ""),
                trade_type=trade_type,
            )

            # Open position
            db.open_position(
                symbol=symbol, entry_price=fill_price, qty=fill_qty,
                value_usdt=fill_value, stop_loss=sl_price,
                trade_type=trade_type, leverage=leverage,
            )

            # Set cooldown
            risk_manager.set_buy_cooldown(symbol)

            # Notify
            alert = notifier.format_buy_alert(
                symbol, fill_price, fill_value, buy_score,
                signals["buy_details"], sl_price
            )
            notifier.send_sync(alert)

            logger.info(
                f"BUY {symbol}: ${fill_value:.2f} @ ${fill_price:,.2f} "
                f"(score={buy_score}, SL=${sl_price:,.2f})"
            )

        except Exception as e:
            logger.error(f"Buy execution error for {symbol}: {e}")
            notifier.send_sync(notifier.format_error(f"BUY {symbol}", str(e)))

    async def _execute_sell(self, symbol: str, signals: dict):
        """Execute a sell based on signals."""
        sell_score = signals["sell_score"]
        indicators = signals["indicators"]
        price = indicators["price"]

        # Check if selling is allowed
        allowed, reason = risk_manager.can_sell(symbol)
        if not allowed:
            logger.info(f"{symbol}: Sell blocked - {reason}")
            return

        open_positions = db.get_open_positions(symbol)
        if not open_positions:
            return

        for pos in open_positions:
            try:
                # Calculate how much to sell
                sell_qty = strategy.calc_sell_qty(sell_score, pos["qty"])
                if sell_qty <= 0:
                    continue

                # Check minimum order value
                sell_value = sell_qty * price
                if sell_value < config.risk.min_order_usdt:
                    # If below minimum, sell entire position
                    sell_qty = pos["qty"]
                    sell_value = sell_qty * price

                # Execute
                if pos["trade_type"] == "futures":
                    result = exchange.close_futures_position(symbol)
                    sell_qty = pos["qty"]
                else:
                    result = exchange.place_spot_market_sell(symbol, sell_qty)

                fill_price = result.get("price", price)
                fill_value = result.get("value_usdt", sell_value)

                # Calculate P&L
                pnl_usdt = (fill_price - pos["entry_price"]) * sell_qty
                pnl_pct = (fill_price - pos["entry_price"]) / pos["entry_price"]

                # If partial sell, update position; if full, close it
                if sell_qty >= pos["qty"] * 0.99:  # ~full close
                    db.close_position(pos["id"], fill_price, pnl_usdt, pnl_pct, "signal_sell")
                else:
                    # Update remaining qty
                    remaining = pos["qty"] - sell_qty
                    db.close_position(pos["id"], fill_price, pnl_usdt, pnl_pct, "partial_sell")
                    # Reopen with remaining
                    db.open_position(
                        symbol=symbol, entry_price=pos["entry_price"],
                        qty=remaining, value_usdt=remaining * pos["entry_price"],
                        stop_loss=pos["stop_loss"],
                        trade_type=pos["trade_type"], leverage=pos["leverage"],
                    )

                # Record trade
                db.record_trade(
                    symbol=symbol, side="Sell", qty=sell_qty, price=fill_price,
                    value_usdt=fill_value, score=sell_score,
                    score_details=signals["sell_details"],
                    order_id=result.get("order_id", ""),
                    trade_type=pos["trade_type"],
                    notes=f"pnl={pnl_usdt:.2f}",
                )

                # Update daily P&L
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                conn = db._get_conn()
                conn.execute("""
                    INSERT INTO daily_stats (date, pnl_usdt) VALUES (?, ?)
                    ON CONFLICT(date) DO UPDATE SET pnl_usdt = pnl_usdt + ?
                """, (today, pnl_usdt, pnl_usdt))
                conn.commit()
                conn.close()

                # Cooldown
                risk_manager.set_sell_cooldown(symbol)

                # Notify
                alert = notifier.format_sell_alert(
                    symbol, fill_price, sell_qty, fill_value,
                    sell_score, signals["sell_details"], pnl_usdt, pnl_pct
                )
                notifier.send_sync(alert)

                logger.info(
                    f"SELL {symbol}: {sell_qty:.6f} @ ${fill_price:,.2f} "
                    f"(score={sell_score}, P&L=${pnl_usdt:+.2f})"
                )

            except Exception as e:
                logger.error(f"Sell execution error for {symbol} pos {pos['id']}: {e}")
                notifier.send_sync(notifier.format_error(f"SELL {symbol}", str(e)))

    async def _check_stop_losses(self, symbol: str, current_price: float):
        """Check and execute stop-losses for open positions."""
        open_positions = db.get_open_positions(symbol)

        for pos in open_positions:
            should_close, reason = strategy.check_stop_loss(pos, current_price)

            if should_close:
                try:
                    # Execute sell
                    if pos["trade_type"] == "futures":
                        exchange.close_futures_position(symbol)
                    else:
                        exchange.place_spot_market_sell(symbol, pos["qty"])

                    pnl_usdt = (current_price - pos["entry_price"]) * pos["qty"]
                    pnl_pct = (current_price - pos["entry_price"]) / pos["entry_price"]

                    db.close_position(pos["id"], current_price, pnl_usdt, pnl_pct, reason)
                    db.record_trade(
                        symbol=symbol, side="Sell", qty=pos["qty"],
                        price=current_price,
                        value_usdt=current_price * pos["qty"],
                        notes=f"stop_loss: {reason}",
                    )

                    risk_manager.set_stop_loss_cooldown(symbol)

                    alert = notifier.format_stop_loss_alert(
                        symbol, pos["entry_price"], current_price,
                        pnl_usdt, pnl_pct, reason
                    )
                    notifier.send_sync(alert)

                    logger.warning(
                        f"STOP-LOSS {symbol}: ${pnl_usdt:+.2f} ({pnl_pct:+.1%}) - {reason}"
                    )

                except Exception as e:
                    logger.error(f"Stop-loss execution error for {symbol}: {e}")
                    notifier.send_sync(notifier.format_error(f"Stop-loss {symbol}", str(e)))

            elif reason.startswith("trailing_update:"):
                # Update trailing stop values
                _, new_max, new_trailing = reason.split(":")
                db.update_trailing_stop(pos["id"], float(new_trailing), float(new_max))

    async def _force_sell(self, symbol: str):
        """Force sell all positions for a symbol."""
        open_positions = db.get_open_positions(symbol)
        price = exchange.get_price(symbol)

        for pos in open_positions:
            try:
                if pos["trade_type"] == "futures":
                    exchange.close_futures_position(symbol)
                else:
                    exchange.place_spot_market_sell(symbol, pos["qty"])

                pnl_usdt = (price - pos["entry_price"]) * pos["qty"]
                pnl_pct = (price - pos["entry_price"]) / pos["entry_price"]
                db.close_position(pos["id"], price, pnl_usdt, pnl_pct, "force_sell")

                notifier.send_sync(
                    f"⚡ <b>VENTA FORZADA</b> {symbol}\n"
                    f"Precio: ${price:,.2f} | P&L: ${pnl_usdt:+.2f} ({pnl_pct:+.1%})"
                )
            except Exception as e:
                notifier.send_sync(notifier.format_error(f"Force sell {symbol}", str(e)))

    def _format_zones(self, all_signals: dict) -> str:
        """Format support/resistance zones for /zones command."""
        lines = ["🎯 <b>Zonas de Soporte/Resistencia</b>\n"]
        for symbol, data in all_signals.items():
            ind = data["indicators"]
            supports = ind.get("supports", [])
            resistances = ind.get("resistances", [])
            lines.append(f"━━━ <b>{symbol}</b> (${ind['price']:,.2f}) ━━━")
            lines.append(f"🟢 Soportes: {', '.join(f'${s:,.0f}' for s in supports)}")
            lines.append(f"🔴 Resistencias: {', '.join(f'${r:,.0f}' for r in resistances)}")
            lines.append(f"🏷 Zona actual: {ind.get('zone', 'neutral')}\n")
        return "\n".join(lines)

    # ═══════════════════════════════════════
    # SCHEDULED TASKS
    # ═══════════════════════════════════════

    async def daily_report(self):
        """Send daily portfolio report."""
        try:
            portfolio.save_snapshot()
            summary = portfolio.get_summary()
            report = notifier.format_daily_report(summary)
            notifier.send_sync(report)
            logger.info("Daily report sent")
        except Exception as e:
            logger.error(f"Daily report error: {e}")

    async def health_check(self):
        """Periodic health check."""
        healthy, msg = exchange.health_check()
        if not healthy:
            notifier.send_sync(f"🚨 <b>Health Check FAILED</b>\n{msg}")
            logger.error(f"Health check failed: {msg}")

    # ═══════════════════════════════════════
    # STARTUP
    # ═══════════════════════════════════════

    async def start(self):
        """Start the bot."""
        logger.info("=" * 50)
        logger.info("Swing Trading Bot starting...")
        logger.info("=" * 50)

        # Validate config
        if not config.exchange.api_key:
            logger.error("BYBIT_API_KEY not set!")
            sys.exit(1)
        if not config.telegram.token:
            logger.error("TELEGRAM_TOKEN not set!")
            sys.exit(1)

        # Health check
        healthy, msg = exchange.health_check()
        if not healthy:
            logger.error(f"Exchange health check failed: {msg}")
            sys.exit(1)

        # Get initial balance
        balance = exchange.get_balance("USDT")
        logger.info(f"USDT Balance: ${balance:.2f}")

        # Save initial snapshot
        portfolio.save_snapshot()

        # Set initial state
        db.set_state("bot_status", "scanning")
        db.set_state("start_time", datetime.now(timezone.utc).isoformat())

        # Setup Telegram bot
        self.telegram_app = Application.builder().token(config.telegram.token).build()
        notifier.setup_commands(self.telegram_app)

        # Schedule tasks
        self.scheduler.add_job(
            self.scan_cycle, "interval",
            minutes=config.scanning.interval_minutes,
            id="scan_cycle",
        )
        self.scheduler.add_job(
            self.daily_report, "cron",
            hour=config.reporting.daily_report_hour_utc,
            id="daily_report",
        )
        self.scheduler.add_job(
            self.health_check, "interval",
            minutes=30,
            id="health_check",
        )

        # Start scheduler
        self.scheduler.start()

        # Send startup message
        notifier.send_sync(
            f"🚀 <b>Swing Trading Bot iniciado</b>\n\n"
            f"💵 Balance: ${balance:.2f} USDT\n"
            f"📊 Pares: {', '.join(config.pairs.symbols)}\n"
            f"⏱ Scan cada {config.scanning.interval_minutes} min\n"
            f"🛡 Stop-Loss: {config.risk.stop_loss_pct:.0%}\n"
            f"📡 Testnet: {'Sí' if config.exchange.testnet else 'No'}\n\n"
            f"Comandos: /status /portfolio /signals /history /performance /zones /pause /resume /config"
        )

        # Run initial scan
        await self.scan_cycle()

        # Start Telegram polling
        logger.info("Starting Telegram polling...")
        await self.telegram_app.initialize()
        await self.telegram_app.start()
        await self.telegram_app.updater.start_polling()

        # Keep running
        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Shutting down...")
            self.scheduler.shutdown()
            await self.telegram_app.updater.stop()
            await self.telegram_app.stop()
            await self.telegram_app.shutdown()


def main():
    bot = SwingBot()
    asyncio.run(bot.start())


if __name__ == "__main__":
    main()
