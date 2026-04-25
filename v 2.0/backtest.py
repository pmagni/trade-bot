"""
Swing Trading Bot - Backtesting Module
Simulates the strategy on historical data to validate performance.
"""

import logging
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Tuple
from config import config
from strategy import Strategy

logger = logging.getLogger("backtest")


class Backtest:
    def __init__(self, initial_capital: float = 1000.0, fee_pct: float = 0.001,
                 slippage_pct: float = 0.0005):
        self.initial_capital = initial_capital
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct
        self.strategy = Strategy()

    def run(self, candles_4h: list, symbol: str = "BTCUSDT") -> Dict:
        """
        Run backtest on historical 4h candle data.

        candles_4h: list of dicts with {timestamp, open, high, low, close, volume}
        Returns comprehensive results dict.
        """
        df = pd.DataFrame(candles_4h)
        if len(df) < 200:
            raise ValueError(f"Need at least 200 candles, got {len(df)}")

        # State
        capital = self.initial_capital
        positions = []  # List of open positions
        closed_trades = []
        portfolio_history = []
        signals_log = []

        # Cooldown tracking
        last_buy_idx = -100
        last_sell_idx = -50
        last_sl_idx = -500
        cooldown_buy = config.risk.cooldown_after_buy // 240  # Convert min to 4h bars
        cooldown_sell = config.risk.cooldown_after_sell // 240
        cooldown_sl = config.risk.cooldown_after_stop_loss // 240
        trades_today = {}

        peak_portfolio = self.initial_capital

        logger.info(f"Backtesting {symbol} with {len(df)} candles, capital=${initial_capital}")

        # Iterate through candles (start at 200 to have enough history)
        for i in range(200, len(df)):
            current = df.iloc[i]
            price = current["close"]
            bar_time = current["timestamp"]

            # Simulated date for daily trade limit
            sim_date = str(bar_time)[:10]
            if sim_date not in trades_today:
                trades_today[sim_date] = 0

            # Calculate portfolio value
            positions_value = sum(p["qty"] * price for p in positions)
            total_value = capital + positions_value
            portfolio_history.append({
                "timestamp": bar_time,
                "total": total_value,
                "capital": capital,
                "positions_value": positions_value,
                "price": price,
            })

            # Update peak
            peak_portfolio = max(peak_portfolio, total_value)

            # Drawdown check
            drawdown = (peak_portfolio - total_value) / peak_portfolio
            if drawdown >= config.risk.max_drawdown_pct:
                signals_log.append({
                    "timestamp": bar_time, "type": "circuit_breaker",
                    "detail": f"Drawdown {drawdown:.1%}"
                })
                continue  # Skip trading

            # Compute indicators from historical window
            window = df.iloc[max(0, i-99):i+1].to_dict("records")
            daily_approx = df.iloc[max(0, i-89*6):i+1:6].to_dict("records")

            try:
                indicators = self.strategy.compute_indicators(window, daily_approx)
            except Exception as e:
                continue

            # Calculate scores
            buy_score, buy_details = self.strategy.calc_buy_score(indicators)
            sell_score, sell_details = self.strategy.calc_sell_score(indicators, positions)

            # ─── CHECK STOP-LOSSES ───
            for pos in positions[:]:
                should_close, reason = self.strategy.check_stop_loss(pos, price)
                if should_close:
                    # Close position
                    sell_price = price * (1 - self.slippage_pct)
                    sell_value = pos["qty"] * sell_price
                    fee = sell_value * self.fee_pct
                    capital += sell_value - fee

                    pnl = sell_value - pos["cost"] - fee - pos.get("entry_fee", 0)
                    pnl_pct = (sell_price - pos["entry_price"]) / pos["entry_price"]

                    closed_trades.append({
                        "symbol": symbol,
                        "entry_price": pos["entry_price"],
                        "exit_price": sell_price,
                        "qty": pos["qty"],
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "reason": reason,
                        "entry_time": pos["entry_time"],
                        "exit_time": bar_time,
                        "bars_held": i - pos["entry_idx"],
                    })
                    positions.remove(pos)
                    last_sl_idx = i
                    signals_log.append({
                        "timestamp": bar_time, "type": "stop_loss",
                        "detail": f"P&L: ${pnl:.2f} ({pnl_pct:.1%})"
                    })
                elif reason.startswith("trailing_update:"):
                    _, new_max, new_trailing = reason.split(":")
                    pos["trailing_max"] = float(new_max)
                    pos["trailing_stop"] = float(new_trailing)

            # ─── BUY LOGIC ───
            if (buy_score >= config.scoring.buy_light
                and (i - last_buy_idx) >= cooldown_buy
                and (i - last_sl_idx) >= cooldown_sl
                and trades_today.get(sim_date, 0) < config.risk.max_trades_per_day_per_asset
                and len(positions) < config.risk.max_open_positions_per_asset):

                amount = self.strategy.calc_buy_amount(buy_score, capital, total_value)
                if amount >= config.risk.min_order_usdt:
                    buy_price = price * (1 + self.slippage_pct)
                    fee = amount * self.fee_pct
                    qty = (amount - fee) / buy_price

                    sl_price = buy_price * (1 - config.risk.stop_loss_pct)

                    positions.append({
                        "entry_price": buy_price,
                        "qty": qty,
                        "cost": amount,
                        "entry_fee": fee,
                        "stop_loss": sl_price,
                        "trailing_stop": 0,
                        "trailing_max": buy_price,
                        "entry_time": bar_time,
                        "entry_idx": i,
                    })

                    capital -= amount
                    last_buy_idx = i
                    trades_today[sim_date] = trades_today.get(sim_date, 0) + 1

                    signals_log.append({
                        "timestamp": bar_time, "type": "buy",
                        "detail": f"Score: {buy_score}, Amount: ${amount:.2f}, Price: ${buy_price:,.2f}"
                    })

            # ─── SELL LOGIC ───
            if (sell_score >= config.scoring.sell_partial
                and (i - last_sell_idx) >= cooldown_sell
                and positions):

                for pos in positions[:]:
                    sell_qty = self.strategy.calc_sell_qty(sell_score, pos["qty"])
                    if sell_qty <= 0:
                        continue

                    sell_price = price * (1 - self.slippage_pct)
                    sell_value = sell_qty * sell_price
                    fee = sell_value * self.fee_pct
                    capital += sell_value - fee

                    cost_basis = (sell_qty / pos["qty"]) * pos["cost"]
                    entry_fee_portion = (sell_qty / pos["qty"]) * pos.get("entry_fee", 0)
                    pnl = sell_value - cost_basis - fee - entry_fee_portion
                    pnl_pct = (sell_price - pos["entry_price"]) / pos["entry_price"]

                    closed_trades.append({
                        "symbol": symbol,
                        "entry_price": pos["entry_price"],
                        "exit_price": sell_price,
                        "qty": sell_qty,
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "reason": "signal_sell",
                        "entry_time": pos["entry_time"],
                        "exit_time": bar_time,
                        "bars_held": i - pos["entry_idx"],
                    })

                    remaining = pos["qty"] - sell_qty
                    if remaining < pos["qty"] * 0.01:
                        positions.remove(pos)
                    else:
                        pos["qty"] = remaining
                        pos["cost"] = (remaining / (remaining + sell_qty)) * pos["cost"]

                    last_sell_idx = i
                    signals_log.append({
                        "timestamp": bar_time, "type": "sell",
                        "detail": f"Score: {sell_score}, P&L: ${pnl:.2f} ({pnl_pct:.1%})"
                    })

        # ─── FINAL STATE ───
        final_price = df.iloc[-1]["close"]
        remaining_value = sum(p["qty"] * final_price for p in positions)
        final_portfolio = capital + remaining_value

        # ─── METRICS ───
        results = self._calc_metrics(
            closed_trades, portfolio_history,
            final_portfolio, initial_capital=self.initial_capital,
            buy_hold_start=df.iloc[200]["close"],
            buy_hold_end=final_price,
        )
        results["signals_log"] = signals_log
        results["portfolio_history"] = portfolio_history
        results["open_positions_at_end"] = len(positions)
        results["remaining_capital"] = capital
        results["remaining_positions_value"] = remaining_value

        return results

    def _calc_metrics(self, trades: list, history: list,
                      final_value: float, initial_capital: float,
                      buy_hold_start: float, buy_hold_end: float) -> Dict:
        """Calculate comprehensive performance metrics."""
        if not trades:
            return {
                "total_trades": 0, "wins": 0, "losses": 0,
                "win_rate": 0, "profit_factor": 0,
                "total_return_pct": (final_value / initial_capital - 1) * 100,
                "buy_hold_return_pct": (buy_hold_end / buy_hold_start - 1) * 100,
                "max_drawdown_pct": 0, "sharpe_ratio": 0,
                "avg_win": 0, "avg_loss": 0, "avg_win_loss_ratio": 0,
                "avg_bars_held": 0, "total_pnl": 0,
                "final_value": final_value,
                "trades": trades,
            }

        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]

        total_wins = sum(t["pnl"] for t in wins) if wins else 0
        total_losses = abs(sum(t["pnl"] for t in losses)) if losses else 0

        avg_win = total_wins / len(wins) if wins else 0
        avg_loss = total_losses / len(losses) if losses else 0

        # Max drawdown from portfolio history
        peak = initial_capital
        max_dd = 0
        for h in history:
            peak = max(peak, h["total"])
            dd = (peak - h["total"]) / peak
            max_dd = max(max_dd, dd)

        # Sharpe ratio (simplified: using trade returns)
        if len(trades) > 1:
            returns = [t["pnl_pct"] for t in trades]
            sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(252 / max(1, np.mean([t["bars_held"] for t in trades]) / 6)) if np.std(returns) > 0 else 0
        else:
            sharpe = 0

        total_return = (final_value / initial_capital - 1) * 100
        buy_hold_return = (buy_hold_end / buy_hold_start - 1) * 100

        return {
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(trades) * 100,
            "profit_factor": total_wins / total_losses if total_losses > 0 else float("inf"),
            "total_return_pct": total_return,
            "buy_hold_return_pct": buy_hold_return,
            "beats_buy_hold": total_return > buy_hold_return,
            "max_drawdown_pct": max_dd * 100,
            "sharpe_ratio": sharpe,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "avg_win_loss_ratio": avg_win / avg_loss if avg_loss > 0 else float("inf"),
            "avg_bars_held": np.mean([t["bars_held"] for t in trades]),
            "total_pnl": sum(t["pnl"] for t in trades),
            "max_win": max(t["pnl"] for t in trades),
            "max_loss": min(t["pnl"] for t in trades),
            "final_value": final_value,
            "trades": trades,
        }

    def print_results(self, results: Dict, symbol: str = ""):
        """Pretty print backtest results."""
        print(f"\n{'='*60}")
        print(f"  BACKTEST RESULTS {symbol}")
        print(f"{'='*60}\n")

        print(f"  Initial Capital:     ${self.initial_capital:,.2f}")
        print(f"  Final Value:         ${results['final_value']:,.2f}")
        print(f"  Total Return:        {results['total_return_pct']:+.2f}%")
        print(f"  Buy & Hold Return:   {results['buy_hold_return_pct']:+.2f}%")
        beat = "✅ YES" if results.get('beats_buy_hold') else "❌ NO"
        print(f"  Beats Buy & Hold:    {beat}")

        print(f"\n{'─'*40}")
        print(f"  Total Trades:        {results['total_trades']}")
        print(f"  Wins / Losses:       {results['wins']} / {results['losses']}")
        print(f"  Win Rate:            {results['win_rate']:.1f}%")
        print(f"  Profit Factor:       {results['profit_factor']:.2f}")
        print(f"  Sharpe Ratio:        {results['sharpe_ratio']:.2f}")

        print(f"\n{'─'*40}")
        print(f"  Avg Win:             ${results['avg_win']:.2f}")
        print(f"  Avg Loss:            ${results['avg_loss']:.2f}")
        print(f"  Win/Loss Ratio:      {results['avg_win_loss_ratio']:.2f}")
        print(f"  Max Drawdown:        {results['max_drawdown_pct']:.1f}%")
        print(f"  Best Trade:          ${results.get('max_win', 0):+.2f}")
        print(f"  Worst Trade:         ${results.get('max_loss', 0):+.2f}")
        print(f"  Avg Bars Held:       {results['avg_bars_held']:.0f} (4h bars)")

        print(f"\n{'─'*40}")
        print(f"  Open at End:         {results['open_positions_at_end']}")
        print(f"  Remaining Capital:   ${results['remaining_capital']:.2f}")
        print(f"  Positions Value:     ${results['remaining_positions_value']:.2f}")

        # Grade
        print(f"\n{'='*60}")
        grade = self._grade_results(results)
        print(f"  GRADE: {grade}")
        print(f"{'='*60}\n")

    def _grade_results(self, results: Dict) -> str:
        """Grade the backtest results."""
        score = 0
        notes = []

        if results["win_rate"] >= 55:
            score += 1
            notes.append("✅ Win rate > 55%")
        else:
            notes.append(f"❌ Win rate {results['win_rate']:.0f}% < 55%")

        if results["profit_factor"] >= 1.5:
            score += 1
            notes.append("✅ Profit factor > 1.5")
        else:
            notes.append(f"❌ Profit factor {results['profit_factor']:.2f} < 1.5")

        if results["max_drawdown_pct"] <= 15:
            score += 1
            notes.append("✅ Max drawdown < 15%")
        else:
            notes.append(f"❌ Drawdown {results['max_drawdown_pct']:.1f}% > 15%")

        if results["sharpe_ratio"] >= 1.0:
            score += 1
            notes.append("✅ Sharpe > 1.0")
        else:
            notes.append(f"❌ Sharpe {results['sharpe_ratio']:.2f} < 1.0")

        if results.get("beats_buy_hold"):
            score += 1
            notes.append("✅ Beats buy & hold")
        else:
            notes.append("❌ Doesn't beat buy & hold")

        for n in notes:
            print(f"  {n}")

        if score >= 4:
            return "🏆 A - Ready for live trading"
        elif score >= 3:
            return "🥈 B - Promising, minor adjustments needed"
        elif score >= 2:
            return "🥉 C - Needs parameter tuning"
        else:
            return "⚠️ D - Strategy needs significant revision"


# ─── CLI RUNNER ───

if __name__ == "__main__":
    import sys

    # This can be run standalone with historical data
    # Usage: python backtest.py <data_file.csv>
    # CSV format: timestamp,open,high,low,close,volume

    if len(sys.argv) < 2:
        print("Usage: python backtest.py <data_file.csv> [initial_capital]")
        print("\nCSV columns: timestamp,open,high,low,close,volume")
        print("Get data from Bybit API or TradingView export.")
        sys.exit(1)

    data_file = sys.argv[1]
    capital = float(sys.argv[2]) if len(sys.argv) > 2 else 1000.0

    df = pd.read_csv(data_file)
    candles = df.to_dict("records")

    bt = Backtest(initial_capital=capital)
    results = bt.run(candles, symbol=data_file.split("/")[-1].replace(".csv", ""))
    bt.print_results(results)
