"""
Swing Trading Bot - Telegram Notifications
Sends alerts and handles interactive commands.
"""

import logging
import asyncio
from datetime import datetime, timezone
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from config import config
from database import db
from portfolio import portfolio

logger = logging.getLogger("telegram")


class TelegramNotifier:
    def __init__(self):
        self.bot = Bot(token=config.telegram.token)
        self.chat_id = config.telegram.chat_id
        self.app = None

    # ═══════════════════════════════════════
    # SEND MESSAGES
    # ═══════════════════════════════════════

    async def send(self, text: str, parse_mode: str = "HTML"):
        """Send a message to the configured chat."""
        try:
            # Split long messages (Telegram limit: 4096 chars)
            if len(text) > 4000:
                parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
                for part in parts:
                    await self.bot.send_message(
                        chat_id=self.chat_id, text=part, parse_mode=parse_mode
                    )
            else:
                await self.bot.send_message(
                    chat_id=self.chat_id, text=text, parse_mode=parse_mode
                )
        except Exception as e:
            logger.error(f"Telegram send error: {e}")

    def send_sync(self, text: str):
        """Synchronous wrapper for send."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.send(text))
            else:
                loop.run_until_complete(self.send(text))
        except RuntimeError:
            asyncio.run(self.send(text))

    # ═══════════════════════════════════════
    # ALERT FORMATTERS
    # ═══════════════════════════════════════

    def format_buy_alert(self, symbol: str, price: float, amount: float,
                         score: int, details: dict, stop_loss: float) -> str:
        emoji = "🟢" if score >= 8 else "🔵"
        details_text = "\n".join(f"  • {v}" for v in details.values())
        return (
            f"{emoji} <b>COMPRA EJECUTADA</b>\n\n"
            f"📊 <b>{symbol}</b>\n"
            f"💰 Monto: <b>${amount:.2f} USDT</b>\n"
            f"📈 Precio: ${price:,.2f}\n"
            f"🎯 Buy Score: <b>{score}</b>\n"
            f"🛡 Stop-Loss: ${stop_loss:,.2f} ({((stop_loss/price)-1)*100:.1f}%)\n\n"
            f"📋 <b>Señales activas:</b>\n{details_text}"
        )

    def format_sell_alert(self, symbol: str, price: float, qty: float,
                          value: float, score: int, details: dict,
                          pnl_usdt: float, pnl_pct: float) -> str:
        emoji = "🟢" if pnl_usdt > 0 else "🔴"
        pnl_emoji = "📈" if pnl_usdt > 0 else "📉"
        details_text = "\n".join(f"  • {v}" for v in details.values())
        return (
            f"{emoji} <b>VENTA EJECUTADA</b>\n\n"
            f"📊 <b>{symbol}</b>\n"
            f"📤 Cantidad: {qty:.6f}\n"
            f"💰 Valor: ${value:.2f} USDT\n"
            f"📈 Precio: ${price:,.2f}\n"
            f"🎯 Sell Score: <b>{score}</b>\n"
            f"{pnl_emoji} P&L: <b>${pnl_usdt:+.2f} ({pnl_pct:+.1%})</b>\n\n"
            f"📋 <b>Señales activas:</b>\n{details_text}"
        )

    def format_stop_loss_alert(self, symbol: str, entry_price: float,
                                close_price: float, pnl_usdt: float,
                                pnl_pct: float, reason: str) -> str:
        return (
            f"🛑 <b>STOP-LOSS ACTIVADO</b>\n\n"
            f"📊 <b>{symbol}</b>\n"
            f"📈 Entrada: ${entry_price:,.2f}\n"
            f"📉 Cierre: ${close_price:,.2f}\n"
            f"💸 Pérdida: <b>${pnl_usdt:.2f} ({pnl_pct:.1%})</b>\n"
            f"⚙️ Tipo: {reason}\n\n"
            f"⏳ Cooldown 24h activado para {symbol}"
        )

    def format_scanning_report(self, signals: dict) -> str:
        """Format periodic scanning report."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"📡 <b>MARKET SCAN</b> - {now}\n"]

        for symbol, data in signals.items():
            price = data["indicators"]["price"]
            rsi = data["indicators"]["rsi"]
            fg = data["indicators"].get("fear_greed", "N/A")
            buy_score = data["buy_score"]
            sell_score = data["sell_score"]
            zone = data["indicators"].get("zone", "neutral")
            drop = data["indicators"]["drop_from_high"]
            rise = data["indicators"]["rise_from_low"]

            buy_bar = "🟩" * min(buy_score, 10) + "⬜" * max(0, 10 - buy_score)
            sell_bar = "🟥" * min(sell_score, 10) + "⬜" * max(0, 10 - sell_score)

            lines.append(
                f"━━━ <b>{symbol}</b> ━━━\n"
                f"💵 ${price:,.2f} | RSI: {rsi:.0f} | F&G: {fg}\n"
                f"📉 Caída 14d: {drop:.1%} | 📈 Subida 14d: {rise:.1%}\n"
                f"🏷 Zona: {zone}\n"
                f"🟢 Buy:  {buy_bar} ({buy_score})\n"
                f"🔴 Sell: {sell_bar} ({sell_score})\n"
            )

        status = db.get_state("bot_status", "scanning")
        open_count = len(db.get_open_positions())
        lines.append(f"\n⚙️ Estado: {status} | Posiciones: {open_count}")

        return "\n".join(lines)

    def format_daily_report(self, summary: dict) -> str:
        """Format daily portfolio report."""
        perf = summary["performance"]
        positions = summary["positions"]

        pos_lines = []
        for p in positions:
            emoji = "🟢" if p["unrealized_pnl_pct"] > 0 else "🔴"
            pos_lines.append(
                f"  {emoji} {p['symbol']}: ${p['current_value']:.2f} "
                f"({p['unrealized_pnl_pct']:+.1%})"
            )

        pos_text = "\n".join(pos_lines) if pos_lines else "  Sin posiciones abiertas"

        return (
            f"📊 <b>REPORTE DIARIO</b>\n"
            f"{'━' * 25}\n\n"
            f"💰 Portfolio: <b>${summary['total_value']:.2f}</b>\n"
            f"💵 Disponible: ${summary['available_usdt']:.2f}\n"
            f"📈 P&L no realizado: ${summary['total_unrealized_pnl']:+.2f}\n\n"
            f"📋 <b>Posiciones abiertas ({summary['positions_count']}):</b>\n"
            f"{pos_text}\n\n"
            f"📊 <b>Performance total:</b>\n"
            f"  Trades: {perf['total_trades']} | "
            f"Win Rate: {perf['win_rate']:.0f}%\n"
            f"  P&L total: ${perf['total_pnl']:+.2f} | "
            f"PF: {perf['profit_factor']:.2f}"
        )

    def format_error(self, context: str, error: str) -> str:
        return (
            f"⚠️ <b>Error de API</b>\n"
            f"Contexto: {context}\n"
            f"Error: {error}"
        )

    # ═══════════════════════════════════════
    # COMMAND HANDLERS
    # ═══════════════════════════════════════

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        status = db.get_state("bot_status", "scanning")
        last_scan = db.get_state("last_scan_time", "N/A")
        open_pos = len(db.get_open_positions())
        await update.message.reply_html(
            f"⚙️ <b>Estado del Bot</b>\n\n"
            f"Estado: <b>{status}</b>\n"
            f"Último scan: {last_scan}\n"
            f"Posiciones abiertas: {open_pos}"
        )

    async def cmd_portfolio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            summary = portfolio.get_summary()
            report = self.format_daily_report(summary)
            await update.message.reply_html(report)
        except Exception as e:
            await update.message.reply_html(f"❌ Error: {e}")

    async def cmd_signals(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        last_signals = db.get_state("last_signals", "No hay señales recientes")
        await update.message.reply_html(last_signals)

    async def cmd_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        trades = db.get_recent_trades(limit=10)
        if not trades:
            await update.message.reply_html("📋 Sin trades registrados")
            return

        lines = ["📋 <b>Últimos 10 trades:</b>\n"]
        for t in trades:
            emoji = "🟢" if t["side"] == "Buy" else "🔴"
            lines.append(
                f"{emoji} {t['symbol']} {t['side']} "
                f"${t['value_usdt']:.2f} @ ${t['price']:,.2f} "
                f"(Score: {t['score']})"
            )
        await update.message.reply_html("\n".join(lines))

    async def cmd_performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        stats = db.get_performance_stats()
        await update.message.reply_html(
            f"📊 <b>Performance</b>\n\n"
            f"Total trades: {stats['total_trades']}\n"
            f"Wins: {stats['wins']} | Losses: {stats['losses']}\n"
            f"Win Rate: {stats['win_rate']:.1f}%\n"
            f"Profit Factor: {stats['profit_factor']:.2f}\n"
            f"Avg Win: ${stats['avg_win']:.2f}\n"
            f"Avg Loss: ${stats['avg_loss']:.2f}\n"
            f"Total P&L: ${stats['total_pnl']:+.2f}\n"
            f"Best: ${stats['max_win']:+.2f} | Worst: ${stats['max_loss']:+.2f}"
        )

    async def cmd_zones(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        zones_text = db.get_state("last_zones", "No hay zonas calculadas aún")
        await update.message.reply_html(zones_text)

    async def cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        db.set_state("bot_status", "paused")
        db.set_state("pause_reason", "manual")
        await update.message.reply_html("⏸ Bot pausado. Usa /resume para reanudar.")

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        db.set_state("bot_status", "scanning")
        db.set_state("pause_reason", "")
        await update.message.reply_html("▶️ Bot reanudado. Escaneando mercado...")

    async def cmd_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        RC = config.risk
        SC = config.scoring
        await update.message.reply_html(
            f"⚙️ <b>Configuración</b>\n\n"
            f"Stop-Loss: {RC.stop_loss_pct:.0%}\n"
            f"Trailing Stop: activa al {RC.trailing_stop_activation:.0%}, "
            f"distancia {RC.trailing_stop_distance:.0%}\n"
            f"Reserva mínima: {RC.min_reserve_pct:.0%}\n"
            f"Max drawdown: {RC.max_drawdown_pct:.0%}\n"
            f"Leverage: {'ON' if RC.leverage_enabled else 'OFF'}\n"
            f"Max trades/día/activo: {RC.max_trades_per_day_per_asset}\n"
            f"Cooldown compra: {RC.cooldown_after_buy}min\n"
            f"Cooldown venta: {RC.cooldown_after_sell}min\n"
            f"Buy thresholds: {SC.buy_light}/{SC.buy_moderate}/{SC.buy_strong}/{SC.buy_maximum}\n"
            f"Sell thresholds: {SC.sell_partial}/{SC.sell_moderate}/{SC.sell_strong}/{SC.sell_total}"
        )

    async def cmd_force_sell(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args
        if not args:
            await update.message.reply_html("Uso: /force_sell BTCUSDT")
            return
        symbol = args[0].upper()
        db.set_state(f"force_sell_{symbol}", "pending")
        await update.message.reply_html(f"⚡ Venta forzada de {symbol} programada para el próximo ciclo.")

    async def cmd_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            summary = portfolio.get_summary()
            report = self.format_daily_report(summary)
            await update.message.reply_html(report)
        except Exception as e:
            await update.message.reply_html(f"❌ Error generando reporte: {e}")

    # ═══════════════════════════════════════
    # BOT SETUP
    # ═══════════════════════════════════════

    def setup_commands(self, app: Application):
        """Register all command handlers."""
        self.app = app
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("portfolio", self.cmd_portfolio))
        app.add_handler(CommandHandler("signals", self.cmd_signals))
        app.add_handler(CommandHandler("history", self.cmd_history))
        app.add_handler(CommandHandler("performance", self.cmd_performance))
        app.add_handler(CommandHandler("zones", self.cmd_zones))
        app.add_handler(CommandHandler("pause", self.cmd_pause))
        app.add_handler(CommandHandler("resume", self.cmd_resume))
        app.add_handler(CommandHandler("config", self.cmd_config))
        app.add_handler(CommandHandler("force_sell", self.cmd_force_sell))
        app.add_handler(CommandHandler("report", self.cmd_report))
        logger.info("Telegram commands registered")


# Global instance
notifier = TelegramNotifier()
