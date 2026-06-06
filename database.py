"""
Swing Trading Bot - Database Module
SQLite persistence for trades, positions, portfolio state, and bot state.
"""

import sqlite3
import json
import time
from datetime import datetime, timezone
from config import config


class Database:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.database.db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                qty REAL NOT NULL,
                price REAL NOT NULL,
                value_usdt REAL NOT NULL,
                fee_usdt REAL DEFAULT 0,
                score INTEGER DEFAULT 0,
                score_details TEXT DEFAULT '{}',
                order_id TEXT DEFAULT '',
                trade_type TEXT DEFAULT 'spot',
                notes TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                entry_price REAL NOT NULL,
                qty REAL NOT NULL,
                value_usdt REAL NOT NULL,
                entry_time TEXT NOT NULL,
                stop_loss REAL DEFAULT 0,
                trailing_stop REAL DEFAULT 0,
                trailing_max REAL DEFAULT 0,
                status TEXT DEFAULT 'open',
                close_price REAL DEFAULT 0,
                close_time TEXT DEFAULT '',
                pnl_usdt REAL DEFAULT 0,
                pnl_pct REAL DEFAULT 0,
                close_reason TEXT DEFAULT '',
                trade_type TEXT DEFAULT 'spot',
                leverage REAL DEFAULT 1.0
            );

            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                total_value_usdt REAL NOT NULL,
                available_usdt REAL NOT NULL,
                btc_value_usdt REAL DEFAULT 0,
                eth_value_usdt REAL DEFAULT 0,
                open_positions INTEGER DEFAULT 0,
                notes TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cooldowns (
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY (symbol, action)
            );

            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                trades_count INTEGER DEFAULT 0,
                buy_count INTEGER DEFAULT 0,
                sell_count INTEGER DEFAULT 0,
                pnl_usdt REAL DEFAULT 0,
                volume_usdt REAL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
            CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
            CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);
        """)
        conn.commit()
        conn.close()

    # ─── TRADES ───

    def record_trade(self, symbol: str, side: str, qty: float, price: float,
                     value_usdt: float, fee_usdt: float = 0, score: int = 0,
                     score_details: dict = None, order_id: str = "",
                     trade_type: str = "spot", notes: str = "") -> int:
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute("""
            INSERT INTO trades (timestamp, symbol, side, qty, price, value_usdt,
                                fee_usdt, score, score_details, order_id, trade_type, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (now, symbol, side, qty, price, value_usdt, fee_usdt, score,
              json.dumps(score_details or {}), order_id, trade_type, notes))
        trade_id = cursor.lastrowid

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        conn.execute("""
            INSERT INTO daily_stats (date, trades_count, buy_count, sell_count, volume_usdt)
            VALUES (?, 1, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                trades_count = trades_count + 1,
                buy_count = buy_count + ?,
                sell_count = sell_count + ?,
                volume_usdt = volume_usdt + ?
        """, (today,
              1 if side == "Buy" else 0, 1 if side == "Sell" else 0, value_usdt,
              1 if side == "Buy" else 0, 1 if side == "Sell" else 0, value_usdt))

        conn.commit()
        conn.close()
        return trade_id

    def get_recent_trades(self, limit: int = 20, symbol: str = None) -> list:
        conn = self._get_conn()
        if symbol:
            rows = conn.execute(
                "SELECT * FROM trades WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?",
                (symbol, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_trades_today(self, symbol: str = None) -> int:
        conn = self._get_conn()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if symbol:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM trades WHERE symbol = ? AND timestamp LIKE ?",
                (symbol, f"{today}%")
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM trades WHERE timestamp LIKE ?",
                (f"{today}%",)
            ).fetchone()
        conn.close()
        return row["cnt"]

    # ─── POSITIONS ───

    def open_position(self, symbol: str, entry_price: float, qty: float,
                      value_usdt: float, stop_loss: float = 0,
                      trade_type: str = "spot", leverage: float = 1.0) -> int:
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute("""
            INSERT INTO positions (symbol, entry_price, qty, value_usdt, entry_time,
                                   stop_loss, trailing_max, status, trade_type, leverage)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
        """, (symbol, entry_price, qty, value_usdt, now, stop_loss, entry_price,
              trade_type, leverage))
        pos_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return pos_id

    def close_position(self, position_id: int, close_price: float,
                       pnl_usdt: float, pnl_pct: float, reason: str = ""):
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE positions SET
                status = 'closed', close_price = ?, close_time = ?,
                pnl_usdt = ?, pnl_pct = ?, close_reason = ?
            WHERE id = ?
        """, (close_price, now, pnl_usdt, pnl_pct, reason, position_id))
        conn.commit()
        conn.close()

    def get_open_positions(self, symbol: str = None) -> list:
        conn = self._get_conn()
        if symbol:
            rows = conn.execute(
                "SELECT * FROM positions WHERE status = 'open' AND symbol = ? ORDER BY entry_time",
                (symbol,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM positions WHERE status = 'open' ORDER BY entry_time"
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_closed_positions(self, limit: int = 20) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM positions WHERE status = 'closed' ORDER BY close_time DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_trailing_stop(self, position_id: int, trailing_stop: float, trailing_max: float):
        conn = self._get_conn()
        conn.execute("""
            UPDATE positions SET trailing_stop = ?, trailing_max = ? WHERE id = ?
        """, (trailing_stop, trailing_max, position_id))
        conn.commit()
        conn.close()

    def count_open_positions(self, symbol: str) -> int:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM positions WHERE status = 'open' AND symbol = ?",
            (symbol,)
        ).fetchone()
        conn.close()
        return row["cnt"]

    # ─── PORTFOLIO SNAPSHOTS ───

    def save_snapshot(self, total_value: float, available: float,
                      btc_value: float = 0, eth_value: float = 0,
                      open_positions: int = 0, notes: str = ""):
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO portfolio_snapshots
                (timestamp, total_value_usdt, available_usdt, btc_value_usdt,
                 eth_value_usdt, open_positions, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (now, total_value, available, btc_value, eth_value, open_positions, notes))
        conn.commit()
        conn.close()

    def get_peak_value(self) -> float:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT MAX(total_value_usdt) as peak FROM portfolio_snapshots"
        ).fetchone()
        conn.close()
        return row["peak"] if row["peak"] else 0

    def get_snapshots(self, limit: int = 30) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ─── BOT STATE ───

    def set_state(self, key: str, value: str):
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO bot_state (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?
        """, (key, value, now, value, now))
        conn.commit()
        conn.close()

    def get_state(self, key: str, default: str = "") -> str:
        conn = self._get_conn()
        row = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else default

    # ─── COOLDOWNS ───

    def set_cooldown(self, symbol: str, action: str, duration_minutes: int):
        conn = self._get_conn()
        expires = datetime.fromtimestamp(
            time.time() + duration_minutes * 60, tz=timezone.utc
        ).isoformat()
        conn.execute("""
            INSERT INTO cooldowns (symbol, action, expires_at) VALUES (?, ?, ?)
            ON CONFLICT(symbol, action) DO UPDATE SET expires_at = ?
        """, (symbol, action, expires, expires))
        conn.commit()
        conn.close()

    def is_on_cooldown(self, symbol: str, action: str) -> bool:
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        row = conn.execute(
            "SELECT expires_at FROM cooldowns WHERE symbol = ? AND action = ?",
            (symbol, action)
        ).fetchone()
        conn.close()
        if not row:
            return False
        return row["expires_at"] > now

    def clear_expired_cooldowns(self):
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("DELETE FROM cooldowns WHERE expires_at < ?", (now,))
        conn.commit()
        conn.close()

    # ─── PERFORMANCE METRICS ───

    def count_recent_stop_losses(self, symbol: str, days: int) -> int:
        """
        Cuenta cuántas posiciones del símbolo fueron cerradas por stop-loss
        en los últimos `days` días. Usado por el crash detector (v2.12).
        """
        conn = self._get_conn()
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        row = conn.execute(
            """SELECT COUNT(*) as cnt FROM positions
               WHERE symbol = ? AND status = 'closed'
               AND close_reason LIKE 'stop_loss%'
               AND close_time >= ?""",
            (symbol, cutoff)
        ).fetchone()
        conn.close()
        return row["cnt"] if row else 0

    def get_price_24h_ago(self, symbol: str) -> float:
        """
        Retorna el precio de cierre de la vela de hace 24h aprox,
        usando los snapshots de portfolio como referencia.
        Retorna 0 si no hay datos suficientes.
        (v2.12 — usado por crash detector para calcular variación 24h)
        """
        conn = self._get_conn()
        from datetime import timedelta
        target = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        # Usamos el snapshot más cercano a 24h atrás
        row = conn.execute(
            """SELECT btc_value_usdt, eth_value_usdt, total_value_usdt, available_usdt
               FROM portfolio_snapshots
               WHERE timestamp <= ?
               ORDER BY timestamp DESC LIMIT 1""",
            (target,)
        ).fetchone()
        conn.close()
        return dict(row) if row else {}

    def get_performance_stats(self) -> dict:
        conn = self._get_conn()
        closed = conn.execute(
            "SELECT * FROM positions WHERE status = 'closed'"
        ).fetchall()
        conn.close()

        if not closed:
            return {
                "total_trades": 0, "wins": 0, "losses": 0,
                "win_rate": 0, "profit_factor": 0,
                "avg_win": 0, "avg_loss": 0,
                "total_pnl": 0, "max_win": 0, "max_loss": 0,
            }

        wins = [r for r in closed if r["pnl_usdt"] > 0]
        losses = [r for r in closed if r["pnl_usdt"] <= 0]

        total_wins = sum(r["pnl_usdt"] for r in wins) if wins else 0
        total_losses = abs(sum(r["pnl_usdt"] for r in losses)) if losses else 0

        return {
            "total_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(closed) * 100 if closed else 0,
            "profit_factor": total_wins / total_losses if total_losses > 0 else float("inf"),
            "avg_win": total_wins / len(wins) if wins else 0,
            "avg_loss": total_losses / len(losses) if losses else 0,
            "total_pnl": sum(r["pnl_usdt"] for r in closed),
            "max_win": max((r["pnl_usdt"] for r in closed), default=0),
            "max_loss": min((r["pnl_usdt"] for r in closed), default=0),
        }


# Global instance
db = Database()
