"""
Swing Trading Bot - Main Orchestrator
Runs the scanning loop, executes trades, and manages the bot lifecycle.
"""

import logging
import asyncio
import atexit
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
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
        RotatingFileHandler("bot.log", maxBytes=10_000_000, backupCount=5),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("bot")

# ─── SINGLE-INSTANCE GUARD ───
# v2.17: previene el escenario de ago-2026 — un nohup/systemd restart dejó dos
# procesos bot.py vivos simultáneamente, y ambos hicieron polling contra el
# mismo token de Telegram (getUpdates 409 Conflict). Un PID file simple basta:
# si ya hay un proceso vivo con ese PID al arrancar, se aborta en vez de
# arrancar un segundo scan loop / segundo Telegram poller.
PID_FILE = "bot.pid"


def _acquire_single_instance_lock():
    """Aborta si ya hay un proceso bot.py corriendo (mismo PID file)."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)  # no mata; solo prueba si el PID sigue vivo
        except (ValueError, ProcessLookupError, PermissionError):
            pass
        else:
            logger.error(
                f"Ya hay un proceso bot.py corriendo (PID {old_pid}, {PID_FILE}). "
                f"Abortando para evitar doble polling de Telegram / doble scan loop. "
                f"Si el PID quedó huérfano, verificalo (`ps -p {old_pid}`) y borrá {PID_FILE}."
            )
            sys.exit(1)

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    def _release():
        try:
            if os.path.exists(PID_FILE):
                with open(PID_FILE) as f:
                    if f.read().strip() == str(os.getpid()):
                        os.remove(PID_FILE)
        except OSError:
            pass

    atexit.register(_release)


class SwingBot:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.telegram_app = None
        self._scan_count = 0
        self._fast_mode = False

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
                # v2.14: auto-reanudar tras circuit breaker cuando el drawdown se recupera.
                # Evita que un drawdown deje el bot apagado indefinidamente (jun-2026: 12 días off).
                if self._maybe_auto_resume():
                    status = "scanning"
                else:
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

                    # Execute buy if score is high enough (trend gate applied)
                    buy_threshold = config.scoring.buy_light
                    if not signals["indicators"].get("above_ema_200", True):
                        buy_threshold = max(buy_threshold, 6)  # Downtrend: need score 6+
                    # Symbol-specific minimum: BTC requires score≥6 (score-5 BTC historically underperforms)
                    if symbol == "BTCUSDT":
                        buy_threshold = max(buy_threshold, config.scoring.btc_min_buy_score)
                    signals["buy_threshold"] = buy_threshold

                    # v2.14 Regime filter: no comprar reversión en downtrend confirmado
                    regime_ok, regime_reason = strategy.regime_allows_buy(signals["indicators"])

                    if signals["buy_score"] >= buy_threshold and not regime_ok:
                        signals["buy_action"] = f"Blocked: régimen — {regime_reason}"
                        logger.info(f"{symbol}: Buy bloqueado por filtro de régimen — {regime_reason}")
                    elif signals["buy_score"] >= buy_threshold:
                        # v2.12 Crash detector: consecutive stop-losses
                        cd_blocked, cd_reason = risk_manager.check_crash_detector(
                            symbol,
                            above_ema200=signals["indicators"].get("above_ema_200", True)
                        )
                        if cd_blocked:
                            signals["buy_action"] = f"Blocked: crash detector — {cd_reason}"
                            logger.warning(f"{symbol}: {signals['buy_action']}")
                        # v2.12 Crash detector: velocity (caída >10% en 24h via candles diarios)
                        elif self._is_crash_velocity(symbol, signals["indicators"]):
                            signals["buy_action"] = f"Blocked: crash velocity (>10% en 24h)"
                            logger.warning(f"{symbol}: Buy bloqueado por crash velocity")
                        else:
                            # Pre-buy resistance check: don't buy near a key resistance
                            # v2.13: use dynamic levels (auto-updated daily) if available
                            kl = db.get_effective_levels(symbol)
                            near_resistance = False
                            for r in kl.get("resistances", []):
                                dist = abs(signals["indicators"]["price"] - r["price"]) / r["price"]
                                if dist <= config.key_levels.tolerance_pct:
                                    near_resistance = True
                                    signals["buy_action"] = f"Blocked: near resistance {r['label']} ${r['price']:,.0f}"
                                    logger.info(f"{symbol}: Buy blocked — price near resistance {r['label']}")
                                    break
                            if not near_resistance:
                                bought = await self._execute_buy(symbol, signals)
                                signals["buy_action"] = "BOUGHT" if bought else "Blocked (internal check)"
                    elif signals["buy_score"] >= config.scoring.buy_light:
                        signals["buy_action"] = f"Blocked: trend gate (need {buy_threshold}+)"
                        logger.info(
                            f"{symbol}: Buy score {signals['buy_score']} blocked by trend gate "
                            f"(below EMA200, need {buy_threshold}+)"
                        )
                    else:
                        signals["buy_action"] = "Score too low"

                    # Execute sell if score is high enough
                    if signals["sell_score"] >= config.scoring.sell_partial:
                        # v2.17 — Paridad con el path de compra (propuesto, OFF por
                        # defecto — ver SellGuardConfig): no vender justo sobre un
                        # soporte clave, espejo del check de resistencia en compra.
                        near_support = False
                        if config.sell_guard.near_support_gate_enabled:
                            kl = db.get_effective_levels(symbol)
                            for s in kl.get("supports", []):
                                dist = abs(signals["indicators"]["price"] - s["price"]) / s["price"]
                                if dist <= config.key_levels.tolerance_pct:
                                    near_support = True
                                    signals["sell_action"] = f"Blocked: near support {s['label']} ${s['price']:,.0f}"
                                    logger.info(f"{symbol}: Sell blocked — price near support {s['label']}")
                                    break
                        if not near_support:
                            sold = await self._execute_sell(symbol, signals)
                            signals["sell_action"] = "SOLD" if sold else "Blocked (no position / cooldown)"
                    elif signals["sell_score"] > 0:
                        signals["sell_action"] = f"Score {signals['sell_score']} (need {config.scoring.sell_partial}+)"
                        logger.info(
                            f"{symbol}: Sell score {signals['sell_score']} "
                            f"(need {config.scoring.sell_partial}+) — {signals['sell_details']}"
                        )
                    else:
                        signals["sell_action"] = "No sell signal"

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

            # Sweep untracked dust
            await self._sweep_dust()

            # Periodic scanning report (every 6 hours = 24 scans)
            self._scan_count += 1
            if self._scan_count % (config.reporting.scanning_report_interval_hours * 4) == 0:
                notifier.send_sync(signals_text)

            # Adaptive scan speed: go fast when positions are open or volatility is high
            self._adapt_scan_speed(all_signals)

            logger.info("─── Scan cycle complete ───")

        except Exception as e:
            logger.error(f"Scan cycle error: {e}")
            notifier.send_sync(notifier.format_error("Scan cycle", str(e)))

    def _maybe_auto_resume(self) -> bool:
        """
        v2.14 — Reanuda el bot tras un circuit breaker por drawdown cuando el
        portfolio se recupera por debajo de auto_resume_drawdown_pct.
        Solo aplica si el pause fue por drawdown (no a pausas manuales del usuario).
        Devuelve True si reanudó.
        """
        RC = config.risk
        if not RC.auto_resume_enabled:
            return False

        pause_reason = db.get_state("pause_reason", "") or ""
        if "drawdown" not in pause_reason.lower():
            return False  # pausa manual u otra causa → no auto-reanudar

        try:
            total = portfolio.get_total_value()["total_usdt"]
            peak = db.get_peak_value()
            if peak <= 0:
                return False
            drawdown = (peak - total) / peak
            if drawdown <= RC.auto_resume_drawdown_pct:
                db.set_state("bot_status", "scanning")
                db.set_state("pause_reason", "")
                msg = (
                    f"▶️ <b>Bot reanudado automáticamente</b>\n"
                    f"Drawdown recuperado a {drawdown:.1%} (≤ {RC.auto_resume_drawdown_pct:.0%})"
                )
                notifier.send_sync(msg)
                logger.info(f"Auto-resume: drawdown {drawdown:.1%} <= {RC.auto_resume_drawdown_pct:.0%}")
                return True
        except Exception as e:
            logger.warning(f"Auto-resume check error: {e}")
        return False

    def _is_crash_velocity(self, symbol: str, indicators: dict) -> bool:
        """
        v2.12 — Velocity crash check: retorna True si el activo cayó > 10% en 24h.
        Usa el precio actual vs. el cierre de la vela diaria de ayer (candles_daily).
        Si la condición se cumple, activa un cooldown de buy de crash_pause_hours.
        """
        cd = config.crash_detector
        if not cd.enabled:
            return False

        try:
            # Precio actual
            price_now = indicators.get("price", 0)
            if not price_now:
                return False

            # Precio hace 24h: usamos el local_high/low de los candles diarios que ya tenemos.
            # Una forma más directa: price vs close de la vela de hace 1 día.
            # Los indicators ya tienen local_high de 14d; no tenemos el cierre de ayer directamente.
            # Aproximamos: si drop_from_high > threshold Y drop es reciente (rise_from_low bajo)
            # significa que la caída fue aguda y reciente.
            drop_from_high = indicators.get("drop_from_high", 0)
            rise_from_low = indicators.get("rise_from_low", 0)

            # Caída fuerte (>10%) desde el máximo de 14d Y rebote mínimo (< 5%) → crash activo
            if drop_from_high >= cd.crash_threshold_24h and rise_from_low < 0.05:
                # Activar cooldown de crash por símbolo
                if not db.is_on_cooldown(symbol, "buy"):
                    db.set_cooldown(symbol, "buy", cd.crash_pause_hours * 60)
                    logger.warning(
                        f"Crash velocity [{symbol}]: caída {drop_from_high:.1%} desde máximo "
                        f"con rebote mínimo ({rise_from_low:.1%}) → buy freeze {cd.crash_pause_hours}h"
                    )
                return True
        except Exception as e:
            logger.warning(f"Crash velocity check error [{symbol}]: {e}")

        return False

    async def _analyze_symbol(self, symbol: str) -> dict:
        """Analyze a symbol and return scores + indicators."""
        candles_4h = exchange.get_ohlcv(symbol, interval="240", limit=100)
        candles_daily = exchange.get_ohlcv(symbol, interval="D", limit=90)

        indicators = strategy.compute_indicators(candles_4h, candles_daily)

        open_positions = db.get_open_positions(symbol)

        buy_score, buy_details = strategy.calc_buy_score(indicators, symbol)
        sell_score, sell_details = strategy.calc_sell_score(indicators, open_positions, symbol)

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

    async def _execute_buy(self, symbol: str, signals: dict) -> bool:
        """Execute a buy based on signals. Returns True if a trade was placed."""
        buy_score = signals["buy_score"]
        indicators = signals["indicators"]
        price = indicators["price"]

        portfolio_data = portfolio.get_total_value()
        available = portfolio_data["available_usdt"]
        total = portfolio_data["total_usdt"]

        open_positions = db.get_open_positions(symbol)
        amount = strategy.calc_buy_amount(buy_score, available, total, open_positions)
        if amount <= 0:
            logger.info(f"{symbol}: Buy score {buy_score} but insufficient funds or below minimum")
            return False

        in_uptrend = strategy.regime_is_uptrend(indicators)
        allowed, reason = risk_manager.can_buy(
            symbol, amount, available, total,
            current_price=price, buy_score=buy_score,
            in_uptrend=in_uptrend,
        )
        if not allowed:
            logger.info(f"{symbol}: Buy blocked - {reason}")
            return False

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
            if use_leverage:
                qty = amount / price
                result = exchange.place_futures_market(symbol, "Buy", qty, leverage)
                trade_type = "futures"
            else:
                result = exchange.place_spot_market_buy(symbol, amount)
                trade_type = "spot"

            fill_price = result.get("price", price)
            fill_qty = result.get("qty", amount / price)
            fill_value = result.get("value_usdt", amount)
            fill_fee = result.get("fee", 0)

            # v2.13: use dynamic stop_level if available from daily params update
            key_stop = db.get_effective_levels(symbol).get("stop_level", 0)
            above_ema200 = indicators.get("above_ema_200", True)
            sl_price = risk_manager.calc_stop_loss_price(
                fill_price, leverage, key_stop, above_ema200=above_ema200,
                in_uptrend=in_uptrend,
            )

            db.record_trade(
                symbol=symbol, side="Buy", qty=fill_qty, price=fill_price,
                value_usdt=fill_value, fee_usdt=fill_fee, score=buy_score,
                score_details=signals["buy_details"],
                order_id=result.get("order_id", ""),
                trade_type=trade_type,
            )

            db.open_position(
                symbol=symbol, entry_price=fill_price, qty=fill_qty,
                value_usdt=fill_value, stop_loss=sl_price,
                trade_type=trade_type, leverage=leverage,
            )

            risk_manager.set_buy_cooldown(symbol)
            # Block sells for min_hold_minutes to prevent instant buy-sell cycles
            db.set_cooldown(symbol, "sell", config.risk.min_hold_minutes)

            alert = notifier.format_buy_alert(
                symbol, fill_price, fill_value, buy_score,
                signals["buy_details"], sl_price
            )
            notifier.send_sync(alert)

            logger.info(
                f"BUY {symbol}: ${fill_value:.2f} @ ${fill_price:,.2f} "
                f"(score={buy_score}, SL=${sl_price:,.2f})"
            )
            return True

        except Exception as e:
            logger.error(f"Buy execution error for {symbol}: {e}")
            notifier.send_sync(notifier.format_error(f"BUY {symbol}", str(e)))
            return False

    async def _execute_sell(self, symbol: str, signals: dict) -> bool:
        """Execute a sell based on signals. Returns True if a trade was placed."""
        sell_score = signals["sell_score"]
        indicators = signals["indicators"]
        price = indicators["price"]

        allowed, reason = risk_manager.can_sell(symbol)
        if not allowed:
            logger.info(f"{symbol}: Sell blocked - {reason}")
            return False

        open_positions = db.get_open_positions(symbol)
        if not open_positions:
            return False

        for pos in open_positions:
            try:
                sell_qty = strategy.calc_sell_qty(sell_score, pos["qty"])
                if sell_qty <= 0:
                    continue

                # Partial sell at resistance: sell 50%, keep runner with trailing stop
                SC = config.scoring
                at_resistance = "key_resistance" in signals["sell_details"]
                if (at_resistance and SC.partial_sell_at_resistance
                        and sell_score < SC.sell_strong):
                    sell_qty = pos["qty"] * SC.partial_sell_pct
                    close_reason = "partial_resistance"
                    # Activate trailing stop on the runner
                    runner_qty = pos["qty"] - sell_qty
                    runner_trailing = price * (1 - config.risk.trailing_stop_distance)
                else:
                    runner_qty = 0
                    close_reason = "signal_sell"

                sell_value = sell_qty * price
                if sell_value < config.risk.min_order_usdt:
                    sell_qty = pos["qty"]
                    sell_value = sell_qty * price
                    runner_qty = 0
                    close_reason = "signal_sell"

                if pos["trade_type"] == "futures":
                    result = exchange.close_futures_position(symbol)
                    sell_qty = pos["qty"]
                    runner_qty = 0
                else:
                    result = exchange.place_spot_market_sell(symbol, sell_qty)

                fill_price = result.get("price", price)
                fill_value = result.get("value_usdt", sell_value)

                pnl_usdt = (fill_price - pos["entry_price"]) * sell_qty
                pnl_pct = (fill_price - pos["entry_price"]) / pos["entry_price"]

                if runner_qty > 0 and runner_qty * fill_price >= config.risk.min_order_usdt:
                    # Close partial, keep runner with trailing stop
                    db.close_position(pos["id"], fill_price, pnl_usdt, pnl_pct, close_reason)
                    db.open_position(
                        symbol=symbol, entry_price=pos["entry_price"],
                        qty=runner_qty, value_usdt=runner_qty * pos["entry_price"],
                        stop_loss=pos["stop_loss"],
                        trade_type=pos["trade_type"], leverage=pos["leverage"],
                    )
                    # Set trailing stop on the new runner position
                    new_pos = db.get_open_positions(symbol)
                    if new_pos:
                        latest = new_pos[-1]
                        db.update_trailing_stop(latest["id"], runner_trailing, fill_price)
                    logger.info(
                        f"{symbol}: Partial sell at resistance — sold {sell_qty:.6f}, "
                        f"runner {runner_qty:.6f} with trailing @ ${runner_trailing:,.2f}"
                    )
                elif sell_qty >= pos["qty"] * 0.99:
                    db.close_position(pos["id"], fill_price, pnl_usdt, pnl_pct, "signal_sell")
                else:
                    remaining = pos["qty"] - sell_qty
                    db.close_position(pos["id"], fill_price, pnl_usdt, pnl_pct, "partial_sell")
                    db.open_position(
                        symbol=symbol, entry_price=pos["entry_price"],
                        qty=remaining, value_usdt=remaining * pos["entry_price"],
                        stop_loss=pos["stop_loss"],
                        trade_type=pos["trade_type"], leverage=pos["leverage"],
                    )

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

                risk_manager.set_sell_cooldown(symbol)

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

        return True

    async def _check_stop_losses(self, symbol: str, current_price: float):
        """Check and execute stop-losses for open positions."""
        open_positions = db.get_open_positions(symbol)

        for pos in open_positions:
            should_close, reason = strategy.check_stop_loss(pos, current_price)

            if (should_close and reason.startswith("take_profit_partial")
                    and pos["trade_type"] == "spot"):
                # v2.15 — TP parcial: vende la fracción configurada, el resto queda
                # como runner con trailing activado. Si el runner quedaría bajo el
                # mínimo de orden, se cierra todo (fallback al TP completo).
                try:
                    sell_qty = pos["qty"] * config.risk.take_profit_sell_pct
                    runner_qty = pos["qty"] - sell_qty
                    if runner_qty * current_price < config.risk.min_order_usdt:
                        reason = f"take_profit (runner < min) {reason}"
                        # cae al bloque de cierre total de abajo
                    else:
                        exchange.place_spot_market_sell(symbol, sell_qty)
                        pnl_usdt = (current_price - pos["entry_price"]) * sell_qty
                        pnl_pct = (current_price - pos["entry_price"]) / pos["entry_price"]
                        trailing = current_price * (1 - config.risk.trailing_stop_distance)
                        db.apply_partial_tp(pos["id"], runner_qty, trailing, current_price)
                        db.record_trade(
                            symbol=symbol, side="Sell", qty=sell_qty,
                            price=current_price,
                            value_usdt=current_price * sell_qty,
                            notes=reason,
                        )
                        risk_manager.set_sell_cooldown(symbol)
                        notifier.send_sync(notifier.format_sell_alert(
                            symbol, current_price, sell_qty,
                            current_price * sell_qty, 0,
                            {"take_profit_partial": reason}, pnl_usdt, pnl_pct
                        ))
                        logger.info(
                            f"TP PARCIAL {symbol}: ${pnl_usdt:+.2f} ({pnl_pct:+.1%}) — "
                            f"runner {runner_qty:.6f} con trailing @ ${trailing:,.2f}"
                        )
                        continue
                except Exception as e:
                    logger.error(f"Partial TP execution error for {symbol}: {e}")
                    notifier.send_sync(notifier.format_error(f"TP parcial {symbol}", str(e)))
                    continue

            if should_close:
                try:
                    if pos["trade_type"] == "futures":
                        exchange.close_futures_position(symbol)
                    else:
                        exchange.place_spot_market_sell(symbol, pos["qty"])

                    pnl_usdt = (current_price - pos["entry_price"]) * pos["qty"]
                    pnl_pct = (current_price - pos["entry_price"]) / pos["entry_price"]

                    db.close_position(pos["id"], current_price, pnl_usdt, pnl_pct, reason)

                    is_take_profit = reason.startswith("take_profit")
                    db.record_trade(
                        symbol=symbol, side="Sell", qty=pos["qty"],
                        price=current_price,
                        value_usdt=current_price * pos["qty"],
                        notes=reason if is_take_profit else f"stop_loss: {reason}",
                    )

                    # v2.14: un take-profit es una venta normal (cooldown corto),
                    # no debe disparar el cooldown largo de stop-loss.
                    if is_take_profit:
                        risk_manager.set_sell_cooldown(symbol)
                        notifier.send_sync(notifier.format_sell_alert(
                            symbol, current_price, pos["qty"],
                            current_price * pos["qty"], 0,
                            {"take_profit": reason}, pnl_usdt, pnl_pct
                        ))
                        logger.info(
                            f"TAKE-PROFIT {symbol}: ${pnl_usdt:+.2f} ({pnl_pct:+.1%}) - {reason}"
                        )
                    else:
                        risk_manager.set_stop_loss_cooldown(symbol)
                        notifier.send_sync(notifier.format_stop_loss_alert(
                            symbol, pos["entry_price"], current_price,
                            pnl_usdt, pnl_pct, reason
                        ))
                        logger.warning(
                            f"STOP-LOSS {symbol}: ${pnl_usdt:+.2f} ({pnl_pct:+.1%}) - {reason}"
                        )

                except Exception as e:
                    logger.error(f"Stop-loss execution error for {symbol}: {e}")
                    notifier.send_sync(notifier.format_error(f"Stop-loss {symbol}", str(e)))

            elif reason.startswith("trailing_update:"):
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
                    f"Precio: ${price:,.2f} | P&amp;L: ${pnl_usdt:+.2f} ({pnl_pct:+.1%})"
                )
            except Exception as e:
                notifier.send_sync(notifier.format_error(f"Force sell {symbol}", str(e)))

    def _adapt_scan_speed(self, all_signals: dict):
        """Switch to fast scanning when conditions warrant it."""
        has_positions = len(db.get_open_positions()) > 0

        # Check if any symbol has high buy or sell score
        high_signal = any(
            data["buy_score"] >= config.scoring.buy_light
            or data["sell_score"] >= config.scoring.sell_partial - 1
            for data in all_signals.values()
        )

        should_be_fast = has_positions or high_signal

        if should_be_fast and not self._fast_mode:
            self._fast_mode = True
            self.scheduler.reschedule_job(
                "scan_cycle", trigger="interval",
                minutes=config.scanning.fast_interval_minutes
            )
            logger.info(
                f"Scan speed → FAST ({config.scanning.fast_interval_minutes}min) "
                f"[positions={has_positions}, high_signal={high_signal}]"
            )
        elif not should_be_fast and self._fast_mode:
            self._fast_mode = False
            self.scheduler.reschedule_job(
                "scan_cycle", trigger="interval",
                minutes=config.scanning.interval_minutes
            )
            logger.info(
                f"Scan speed → NORMAL ({config.scanning.interval_minutes}min)"
            )

    async def _sweep_dust(self):
        """Sell untracked crypto dust sitting on the exchange."""
        RC = config.risk
        for symbol, base_asset in zip(config.pairs.symbols, config.pairs.base_assets):
            try:
                balance = exchange.get_balance(base_asset)
                if balance <= 0:
                    continue
                price = exchange.get_price(symbol)
                value = balance * price

                # Only sweep if above threshold and no open positions tracking it
                open_positions = db.get_open_positions(symbol)
                tracked_qty = sum(p["qty"] for p in open_positions)
                untracked = balance - tracked_qty

                if untracked > 0 and untracked * price >= RC.dust_threshold_usdt:
                    # Don't sweep during sell cooldown (e.g., right after a buy)
                    allowed, _ = risk_manager.can_sell(symbol)
                    if not allowed:
                        logger.debug(f"Dust sweep {symbol}: skipped (sell cooldown active)")
                        continue
                    result = exchange.place_spot_market_sell(symbol, untracked)
                    fill_price = result.get("price", price)
                    fill_value = result.get("value_usdt", untracked * price)

                    db.record_trade(
                        symbol=symbol, side="Sell", qty=untracked, price=fill_price,
                        value_usdt=fill_value, notes="dust_sweep",
                    )

                    notifier.send_sync(
                        f"🧹 <b>Dust sweep</b> {symbol}\n"
                        f"Sold {untracked:.8f} {base_asset} (${fill_value:.2f})"
                    )
                    logger.info(
                        f"Dust sweep {symbol}: sold {untracked:.8f} {base_asset} "
                        f"(${fill_value:.2f})"
                    )
            except Exception as e:
                logger.warning(f"Dust sweep {symbol}: {e}")

    def _format_zones(self, all_signals: dict) -> str:
        """Format support/resistance zones for /zones command."""
        lines = ["<b>Zonas de Soporte/Resistencia</b>\n"]
        for symbol, data in all_signals.items():
            ind = data["indicators"]
            supports = ind.get("supports", [])
            resistances = ind.get("resistances", [])
            lines.append(f"<b>{symbol}</b> (${ind['price']:,.2f})")
            lines.append(f"Soportes: {', '.join(f'${s:,.0f}' for s in supports) or 'ninguno'}")
            lines.append(f"Resistencias: {', '.join(f'${r:,.0f}' for r in resistances) or 'ninguna'}")
            lines.append(f"Zona actual: {ind.get('zone', 'neutral')}\n")
        return "\n".join(lines)

    # ═══════════════════════════════════════
    # SCHEDULED TASKS
    # ═══════════════════════════════════════

    async def _rapid_stop_check(self):
        """
        v2.12 — Check de stop-loss dedicado, corre cada 2 minutos.
        Reduce el gap risk: si el precio cae bruscamente entre scans de 5-15 min,
        este job detecta el breach antes y ejecuta el stop más cerca del nivel calculado.
        """
        open_positions = db.get_open_positions()
        if not open_positions:
            return

        symbols_checked = set()
        for pos in open_positions:
            symbol = pos["symbol"]
            if symbol in symbols_checked:
                continue
            symbols_checked.add(symbol)
            try:
                current_price = exchange.get_price(symbol)
                await self._check_stop_losses(symbol, current_price)
            except Exception as e:
                logger.warning(f"Rapid stop check [{symbol}]: {e}")

    # ═══════════════════════════════════════
    # DAILY PARAMS UPDATE (v2.13)
    # ═══════════════════════════════════════

    async def _daily_params_update(self):
        """
        v2.13 — Daily parameter update job (runs at 00:05 UTC).

        Refreshes key support/resistance levels for each trading pair using:
          - Swing high/low detection on the last 30 daily candles
          - Volume-weighted clustering (1.8% tolerance)
          - ATR-14d-based dynamic stop percentage

        Results are saved to the database (bot_state JSON).
        Strategy and bot read from DB first, falling back to static config if needed.
        A Telegram summary is sent so the user can see what changed.
        """
        try:
            from params_updater import detect_key_levels, calc_atr_stop_pct
        except ImportError as e:
            logger.error(f"Daily params update: cannot import params_updater — {e}")
            return

        logger.info("─── Daily params update starting ───")
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        report_lines = [f"⚙️ <b>Parámetros actualizados</b> — {now_str}\n"]

        for symbol in config.pairs.symbols:
            try:
                candles_daily = exchange.get_ohlcv(symbol, interval="D", limit=60)
                current_price = exchange.get_price(symbol)

                # Detect swing-based key levels
                new_levels = detect_key_levels(symbol, candles_daily, current_price)
                if not new_levels:
                    logger.warning(f"Params update [{symbol}]: no levels detected, keeping previous")
                    report_lines.append(f"⚠️ <b>{symbol}</b>: sin niveles detectados — se mantienen los anteriores\n")
                    continue

                # ATR-based dynamic stop percentage
                dyn_stop_pct = calc_atr_stop_pct(candles_daily, current_price)
                new_levels["dynamic_stop_pct"] = dyn_stop_pct

                # Persist to DB
                db.save_dynamic_levels(symbol, new_levels)

                # ── Build Telegram report ──
                sup_prices = [s["price"] for s in new_levels.get("supports", [])]
                res_prices = [r["price"] for r in new_levels.get("resistances", [])]
                sup_str = "  ".join(f"${p:,.0f}" for p in sup_prices) or "—"
                res_str = "  ".join(f"${p:,.0f}" for p in res_prices) or "—"

                # Compare with previous dynamic levels to flag changes
                prev = db.get_dynamic_levels(symbol)  # already updated, so compare before save
                prev_stop = prev.get("stop_level", 0) if prev else 0
                stop_changed = "🔄" if prev_stop and prev_stop != new_levels["stop_level"] else "✅"

                report_lines.append(
                    f"<b>{symbol}</b> @ ${current_price:,.2f}\n"
                    f"  📍 Soportes: {sup_str}\n"
                    f"  🚧 Resistencias: {res_str}\n"
                    f"  {stop_changed} Stop level: ${new_levels['stop_level']:,.0f} | "
                    f"Stop dinámico: {dyn_stop_pct:.1%}\n"
                )

                logger.info(
                    f"Params updated [{symbol}]: "
                    f"{len(sup_prices)} soportes {sup_prices}, "
                    f"{len(res_prices)} resistencias {res_prices}, "
                    f"stop_level={new_levels['stop_level']:,.0f}, "
                    f"dyn_stop={dyn_stop_pct:.1%}"
                )

            except Exception as e:
                logger.error(f"Params update [{symbol}]: {e}")
                report_lines.append(f"⚠️ <b>{symbol}</b>: error — {e}\n")

        report_lines.append(
            "\n<i>📊 Datos: últimos 30 candles diarios\n"
            "⚡ Algoritmo: swing highs/lows + clustering por volumen\n"
            "🔁 Próxima actualización: mañana 00:05 UTC</i>"
        )

        notifier.send_sync("\n".join(report_lines))
        logger.info("─── Daily params update complete ───")

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
    # SIZING RECALIBRATION (every 6 hours)
    # ═══════════════════════════════════════

    async def send_sizing_report(self):
        """
        Display current effective buy amounts based on portfolio balance.
        Informational only — does not modify config or behavior.
        Sends a Telegram summary every 6 hours.
        """
        try:
            data = portfolio.get_total_value()
            available = data["available_usdt"]
            total = data["total_usdt"]

            SC = config.scoring
            RC = config.risk

            max_deployable = available - (total * RC.min_reserve_pct)
            reserve = total * RC.min_reserve_pct

            def effective(pct):
                amt = max_deployable * pct
                amt = min(amt, available * RC.max_per_trade_pct)
                if amt < RC.min_order_usdt:
                    return RC.min_order_usdt if max_deployable >= RC.min_order_usdt else 0
                return round(amt, 2)

            tiers = {
                f"score_{SC.buy_light}+  (light)":    effective(SC.buy_light_pct),
                f"score_{SC.buy_moderate}+ (moderate)": effective(SC.buy_moderate_pct),
                f"score_{SC.buy_strong}+  (strong)":   effective(SC.buy_strong_pct),
                f"score_{SC.buy_maximum}+ (maximum)":  effective(SC.buy_maximum_pct),
            }

            lines = [
                f"<b>Sizing actual (informativo)</b>",
                f"Portfolio: <b>${total:.2f}</b> | USDT: ${available:.2f} | Reserva: ${reserve:.2f}\n",
            ]
            for label, amt in tiers.items():
                lines.append(f"  {label}: <b>${amt:.2f}</b>")

            summary = "\n".join(lines)
            db.set_state("last_sizing", summary)
            notifier.send_sync(summary)
            logger.info(
                f"Sizing report — portfolio=${total:.2f}, "
                f"deployable=${max_deployable:.2f}, tiers={tiers}"
            )
        except Exception as e:
            logger.error(f"Sizing report error: {e}")

    # ═══════════════════════════════════════
    # STARTUP
    # ═══════════════════════════════════════

    async def start(self):
        """Start the bot."""
        logger.info("=" * 50)
        logger.info("Swing Trading Bot v2.15 starting...")
        logger.info("=" * 50)

        if not config.exchange.api_key:
            logger.error("BYBIT_API_KEY not set!")
            sys.exit(1)
        if not config.telegram.token:
            logger.error("TELEGRAM_BOT_TOKEN not set!")
            sys.exit(1)

        # Health check
        healthy, msg = exchange.health_check()
        if not healthy:
            logger.error(f"Exchange health check failed: {msg}")
            sys.exit(1)

        balance = exchange.get_balance("USDT")
        logger.info(f"USDT Balance: ${balance:.2f}")

        portfolio.save_snapshot()

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
        self.scheduler.add_job(
            self.send_sizing_report, "interval",
            hours=6,
            id="sizing_report",
        )
        # v2.12: stop-loss check dedicado cada 2 min para reducir gap risk entre scans
        self.scheduler.add_job(
            self._rapid_stop_check, "interval",
            minutes=2,
            id="rapid_stop_check",
        )
        # v2.13: actualización diaria de parámetros (key levels + ATR stop) a las 00:05 UTC
        self.scheduler.add_job(
            self._daily_params_update, "cron",
            hour=0, minute=5,
            id="daily_params_update",
        )

        self.scheduler.start()

        notifier.send_sync(
            f"🚀 <b>Swing Trading Bot v2.15 iniciado</b>\n\n"
            f"🧭 Régimen: {'ON' if config.regime.enabled else 'OFF'} | "
            f"Uptrend: {'ON' if config.regime.uptrend_mode_enabled else 'OFF'} | "
            f"TP +{config.risk.take_profit_pct:.0%} | Stop -{config.risk.stop_loss_pct:.1%}\n"
            f"💵 Balance: ${balance:.2f} USDT\n"
            f"📊 Pares: {', '.join(config.pairs.symbols)}\n"
            f"⏱ Scan cada {config.scanning.interval_minutes} min\n"
            f"🛡 Stop-Loss: {config.risk.stop_loss_pct:.0%}\n"
            f"📡 Testnet: {'Si' if config.exchange.testnet else 'No'}\n\n"
            f"Comandos: /status /portfolio /signals /history /performance /zones /pause /resume /config /force_sell /report /sizing"
        )

        # v2.13: update key levels on startup so first scan uses fresh data
        await self._daily_params_update()
        # Run initial sizing report then scan
        await self.send_sizing_report()
        await self.scan_cycle()

        # Start Telegram polling
        logger.info("Starting Telegram polling...")
        await self.telegram_app.initialize()
        await self.telegram_app.start()
        await self.telegram_app.updater.start_polling()

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
    _acquire_single_instance_lock()
    bot = SwingBot()
    asyncio.run(bot.start())


if __name__ == "__main__":
    main()
