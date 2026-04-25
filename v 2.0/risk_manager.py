"""
Swing Trading Bot - Risk Manager
Enforces all risk rules: position limits, drawdown, circuit breakers.
"""

import logging
from datetime import datetime, timezone
from config import config
from database import db

logger = logging.getLogger("risk")

RC = config.risk


class RiskManager:

    def can_buy(self, symbol: str, amount_usdt: float,
                available_usdt: float, total_portfolio: float) -> tuple[bool, str]:
        """
        Check all risk rules before placing a buy.
        Returns (allowed, reason).
        """
        # Bot paused?
        if db.get_state("bot_status") == "paused":
            return False, "Bot is paused"

        # Cooldown
        if db.is_on_cooldown(symbol, "buy"):
            return False, f"{symbol} buy on cooldown"

        # Max trades per day
        trades_today = db.get_trades_today(symbol)
        if trades_today >= RC.max_trades_per_day_per_asset:
            return False, f"{symbol} max {RC.max_trades_per_day_per_asset} trades/day reached"

        # Max open positions per asset
        open_count = db.count_open_positions(symbol)
        if open_count >= RC.max_open_positions_per_asset:
            return False, f"{symbol} max {RC.max_open_positions_per_asset} open positions reached"

        # Minimum order
        if amount_usdt < RC.min_order_usdt:
            return False, f"Amount {amount_usdt:.2f} below minimum {RC.min_order_usdt}"

        # Reserve check
        remaining = available_usdt - amount_usdt
        min_reserve = total_portfolio * RC.min_reserve_pct
        if remaining < min_reserve:
            max_allowed = available_usdt - min_reserve
            if max_allowed < RC.min_order_usdt:
                return False, f"Would breach {RC.min_reserve_pct:.0%} reserve. Available after reserve: {max_allowed:.2f}"
            return False, f"Adjusted: max {max_allowed:.2f} to maintain reserve"

        # Drawdown check
        peak = db.get_peak_value()
        if peak > 0:
            current_drawdown = (peak - total_portfolio) / peak
            if current_drawdown >= RC.max_drawdown_pct:
                db.set_state("bot_status", "paused")
                db.set_state("pause_reason", f"Max drawdown {current_drawdown:.1%} reached")
                return False, f"CIRCUIT BREAKER: Drawdown {current_drawdown:.1%} >= {RC.max_drawdown_pct:.0%}"

        # Daily loss check
        snapshots = db.get_snapshots(limit=2)
        if len(snapshots) >= 2:
            daily_change = (total_portfolio - snapshots[1]["total_value_usdt"]) / snapshots[1]["total_value_usdt"]
            if daily_change <= -RC.max_daily_loss_pct:
                return False, f"Daily loss {daily_change:.1%} exceeds limit {-RC.max_daily_loss_pct:.0%}"

        return True, "OK"

    def can_sell(self, symbol: str) -> tuple[bool, str]:
        """Check if selling is allowed."""
        if db.is_on_cooldown(symbol, "sell"):
            return False, f"{symbol} sell on cooldown"

        open_positions = db.get_open_positions(symbol)
        if not open_positions:
            return False, f"No open positions for {symbol}"

        return True, "OK"

    def can_use_leverage(self, symbol: str, total_portfolio: float) -> tuple[bool, str]:
        """Check if leverage trading is allowed."""
        if not RC.leverage_enabled:
            return False, "Leverage disabled in config"

        # Check existing leverage positions
        open_positions = db.get_open_positions(symbol)
        leverage_value = sum(
            p["value_usdt"] * p["leverage"]
            for p in open_positions
            if p["trade_type"] == "futures"
        )
        max_leverage_value = total_portfolio * RC.leverage_max_portfolio_pct
        if leverage_value >= max_leverage_value:
            return False, f"Leverage exposure {leverage_value:.2f} >= max {max_leverage_value:.2f}"

        # Check consecutive stop losses
        recent_closed = db.get_closed_positions(limit=3)
        consecutive_sl = 0
        for p in recent_closed:
            if p["close_reason"] == "stop_loss" and p["trade_type"] == "futures":
                consecutive_sl += 1
            else:
                break
        if consecutive_sl >= 3:
            return False, "3 consecutive leverage stop-losses"

        return True, "OK"

    def set_buy_cooldown(self, symbol: str):
        db.set_cooldown(symbol, "buy", RC.cooldown_after_buy)

    def set_sell_cooldown(self, symbol: str):
        db.set_cooldown(symbol, "sell", RC.cooldown_after_sell)

    def set_stop_loss_cooldown(self, symbol: str):
        db.set_cooldown(symbol, "buy", RC.cooldown_after_stop_loss)

    def calc_stop_loss_price(self, entry_price: float, leverage: float = 1.0) -> float:
        """Calculate stop-loss price."""
        if leverage > 1:
            sl_pct = RC.leverage_stop_loss_pct
        else:
            sl_pct = RC.stop_loss_pct
        return entry_price * (1 - sl_pct)


# Global instance
risk_manager = RiskManager()
