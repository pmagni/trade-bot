"""
Swing Trading Bot - Portfolio Module
Tracks portfolio value, positions, and P&L.
"""

import logging
from typing import Dict, List
from config import config
from database import db
from exchange import exchange

logger = logging.getLogger("portfolio")


class Portfolio:

    def get_total_value(self) -> Dict:
        """Get current total portfolio value."""
        try:
            balances = exchange.get_all_balances()

            usdt_balance = balances.get("USDT", {}).get("available", 0)
            total = usdt_balance

            positions_value = {}
            for symbol in config.pairs.symbols:
                base = symbol.replace("USDT", "")
                if base in balances:
                    qty = balances[base]["total"]
                    price = exchange.get_price(symbol)
                    value = qty * price
                    positions_value[symbol] = {
                        "qty": qty,
                        "price": price,
                        "value_usdt": value,
                    }
                    total += value

            return {
                "total_usdt": total,
                "available_usdt": usdt_balance,
                "positions": positions_value,
                "balances": balances,
            }
        except Exception as e:
            logger.error(f"Error getting portfolio value: {e}")
            raise

    def get_positions_with_pnl(self) -> List[Dict]:
        """Get open positions with current P&L."""
        open_positions = db.get_open_positions()
        result = []

        for pos in open_positions:
            try:
                current_price = exchange.get_price(pos["symbol"])
                pnl_usdt = (current_price - pos["entry_price"]) * pos["qty"]
                pnl_pct = (current_price - pos["entry_price"]) / pos["entry_price"]

                result.append({
                    **pos,
                    "current_price": current_price,
                    "current_value": current_price * pos["qty"],
                    "unrealized_pnl_usdt": pnl_usdt,
                    "unrealized_pnl_pct": pnl_pct,
                })
            except Exception as e:
                logger.error(f"Error getting P&L for position {pos['id']}: {e}")
                result.append({**pos, "current_price": 0, "unrealized_pnl_usdt": 0, "unrealized_pnl_pct": 0})

        return result

    def get_summary(self) -> Dict:
        """Get complete portfolio summary."""
        portfolio_data = self.get_total_value()
        positions = self.get_positions_with_pnl()
        stats = db.get_performance_stats()

        total_unrealized = sum(p["unrealized_pnl_usdt"] for p in positions)

        return {
            "total_value": portfolio_data["total_usdt"],
            "available_usdt": portfolio_data["available_usdt"],
            "positions_count": len(positions),
            "positions": positions,
            "total_unrealized_pnl": total_unrealized,
            "performance": stats,
        }

    def save_snapshot(self):
        """Save current portfolio snapshot to database."""
        try:
            portfolio_data = self.get_total_value()
            btc_val = portfolio_data["positions"].get("BTCUSDT", {}).get("value_usdt", 0)
            eth_val = portfolio_data["positions"].get("ETHUSDT", {}).get("value_usdt", 0)
            open_count = len(db.get_open_positions())

            db.save_snapshot(
                total_value=portfolio_data["total_usdt"],
                available=portfolio_data["available_usdt"],
                btc_value=btc_val,
                eth_value=eth_val,
                open_positions=open_count,
            )
            logger.info(f"Snapshot saved: total={portfolio_data['total_usdt']:.2f} USDT")
        except Exception as e:
            logger.error(f"Error saving snapshot: {e}")


# Global instance
portfolio = Portfolio()
