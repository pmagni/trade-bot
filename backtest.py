"""
Swing Trading Bot - Backtest Harness (v2.15)

Reproduce la lógica de producción (strategy.py + compuertas de risk_manager/bot.py)
sobre velas históricas de Bybit para comparar variantes de configuración.

Uso:
    python3 backtest.py --data-dir /path/to/data [--variant baseline|entry|full]

Los datos se generan con un fetcher de klines de Bybit (4h y daily por símbolo):
    data/BTCUSDT_4h.json, data/BTCUSDT_daily.json, ...

Aproximaciones respecto al bot en vivo:
  - Señales evaluadas al cierre de cada vela 4h (el bot escanea cada 5-15 min).
  - Stops/TP/trailing evaluados intra-vela sobre el camino open→ext1→ext2→close.
  - Sin niveles clave (estáticos ni dinámicos) — se anulan para que ninguna
    variante dependa de niveles dibujados a mano sobre el pasado.
  - Fear & Greed anulado (sin datos históricos).
  - Fees spot 0.1% por lado.
"""

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import sell_rules
from config import config
from strategy import Strategy

logging.disable(logging.WARNING)

FEE = 0.001
START_USDT = 1000.0
WARMUP = 100  # velas 4h antes de operar (BB20, EMA50, aprox EMA200)


def load_data(data_dir: Path, symbols: list) -> dict:
    data = {}
    for sym in symbols:
        data[sym] = {
            "4h": json.loads((data_dir / f"{sym}_4h.json").read_text()),
            "daily": json.loads((data_dir / f"{sym}_daily.json").read_text()),
        }
    return data


class BacktestSim:
    def __init__(self, data: dict, symbols: list):
        self.data = data
        self.symbols = symbols
        self.strategy = Strategy()
        # Anular dependencias externas: niveles clave y Fear&Greed
        import strategy as strategy_module
        strategy_module.db.get_effective_levels = lambda s: {}
        # Misma fuente de niveles que usa bot.py, ya anulada arriba: el harness
        # consulta por el mismo camino aunque la respuesta sea vacía.
        self._levels = strategy_module.db.get_effective_levels
        self.strategy._get_fear_greed = lambda: None

        self.usdt = START_USDT
        self.positions = {s: [] for s in symbols}   # dicts estilo tabla positions
        self.closed = []
        self.cooldowns = {}          # (symbol, kind) -> until_ts_ms
        self.sl_history = []         # (symbol, ts_ms)
        self.paused = False
        self.peak = START_USDT
        self.day_open_value = START_USDT
        self.current_day = None
        self.equity_curve = []
        self._pos_id = 0

    # ─── helpers ───

    def _now_positions(self, symbol):
        return self.positions[symbol]

    def _total_value(self, prices: dict) -> float:
        total = self.usdt
        for sym in self.symbols:
            for p in self.positions[sym]:
                total += p["qty"] * prices[sym]
        return total

    def _on_cooldown(self, symbol, kind, ts):
        return self.cooldowns.get((symbol, kind), 0) > ts

    def _set_cooldown(self, symbol, kind, minutes, ts):
        self.cooldowns[(symbol, kind)] = ts + minutes * 60_000

    def _close_position(self, symbol, pos, price, reason, ts, fraction=1.0):
        qty = pos["qty"] * fraction
        proceeds = qty * price * (1 - FEE)
        self.usdt += proceeds
        pnl_pct = (price - pos["entry_price"]) / pos["entry_price"]
        pnl_usdt = (price - pos["entry_price"]) * qty
        self.closed.append({
            "symbol": symbol, "entry_price": pos["entry_price"],
            "close_price": price, "qty": qty, "pnl_usdt": pnl_usdt,
            "pnl_pct": pnl_pct, "reason": reason,
            "entry_ts": pos["ts"], "close_ts": ts,
            "uptrend_entry": pos.get("uptrend_entry", False),
            "score": pos.get("score", 0),
            "entry_details": pos.get("entry_details", {}),
            "sell_details": (getattr(self, "_last_sell_details", {})
                             if reason == "signal_sell" else {}),
        })
        if fraction >= 0.999:
            self.positions[symbol].remove(pos)
        else:
            pos["qty"] -= qty

    # ─── stops intra-vela ───

    def _candle_path(self, candle):
        o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
        if c >= o:  # vela verde: asumir open→low→high→close
            return [o, l, h, c]
        return [o, h, l, c]

    def _check_stops(self, symbol, candle, ts):
        for price in self._candle_path(candle):
            for pos in list(self.positions[symbol]):
                should_close, reason = self.strategy.check_stop_loss(pos, price)
                if should_close:
                    if reason.startswith("take_profit_partial"):
                        # v2.15: TP parcial — vende la fracción, el resto queda como runner
                        frac = config.risk.take_profit_sell_pct
                        self._close_position(symbol, pos, price, "take_profit_partial", ts, frac)
                        pos["tp_taken"] = 1
                        pos["trailing_max"] = price
                        pos["trailing_stop"] = price * (1 - config.risk.trailing_stop_distance)
                        self._set_cooldown(symbol, "sell", config.risk.cooldown_after_sell, ts)
                    else:
                        self._close_position(symbol, pos, price, reason, ts)
                        if reason.startswith("take_profit"):
                            self._set_cooldown(symbol, "sell", config.risk.cooldown_after_sell, ts)
                        elif reason.startswith("stop_loss"):
                            self._set_cooldown(symbol, "buy", config.risk.cooldown_after_stop_loss, ts)
                            self.sl_history.append((symbol, ts))
                        else:  # trailing_stop
                            self._set_cooldown(symbol, "sell", config.risk.cooldown_after_sell, ts)
                elif reason.startswith("trailing_update:"):
                    _, new_max, new_trailing = reason.split(":")
                    pos["trailing_max"] = float(new_max)
                    pos["trailing_stop"] = float(new_trailing)

    # ─── compuertas de riesgo (réplica de risk_manager.can_buy) ───

    def _can_buy(self, symbol, amount, total, price, score, ts, in_uptrend):
        RC, SC = config.risk, config.scoring
        if self.paused:
            return False
        if self._on_cooldown(symbol, "buy", ts):
            return False
        open_pos = self.positions[symbol]
        if len(open_pos) >= RC.max_open_positions_per_asset:
            return False
        if open_pos:
            cheapest = min(p["entry_price"] for p in open_pos)
            spread = (cheapest - price) / cheapest
            all_in_profit = all(price > p["entry_price"] for p in open_pos)
            uptrend_add_ok = (
                config.regime.uptrend_mode_enabled
                and config.regime.uptrend_reentry
                and in_uptrend and all_in_profit
            )
            if spread < RC.min_entry_spread_pct and not uptrend_add_ok:
                return False
            min_score = (SC.dca_score_min_3rd if len(open_pos) >= 2
                         else SC.dca_score_min_2nd)
            if score < min_score:
                return False
        if amount < RC.min_order_usdt:
            return False
        if self.usdt - amount < total * RC.min_reserve_pct:
            return False
        # circuit breakers
        drawdown = (self.peak - total) / self.peak if self.peak > 0 else 0
        if drawdown >= RC.max_drawdown_pct:
            self.paused = True
            return False
        daily_change = (total - self.day_open_value) / self.day_open_value
        if daily_change <= -RC.max_daily_loss_pct:
            return False
        return True

    # ─── loop principal ───

    def run(self):
        n = min(len(self.data[s]["4h"]) for s in self.symbols)
        for i in range(WARMUP, n):
            ts = self.data[self.symbols[0]]["4h"][i]["timestamp"]
            day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            prices_close = {s: self.data[s]["4h"][i]["close"] for s in self.symbols}

            if day != self.current_day:
                self.current_day = day
                self.day_open_value = self._total_value(prices_close)

            for sym in self.symbols:
                candle = self.data[sym]["4h"][i]
                # 1) stops/TP/trailing intra-vela
                self._check_stops(sym, candle, ts)

                # 2) señales al cierre
                window_4h = self.data[sym]["4h"][max(0, i - 99):i + 1]
                daily_all = [c for c in self.data[sym]["daily"] if c["timestamp"] <= ts]
                window_daily = daily_all[-90:]
                ind = self.strategy.compute_indicators(window_4h, window_daily)

                open_pos = self.positions[sym]
                buy_score, buy_details = self.strategy.calc_buy_score(ind, sym)
                sim_now = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                sell_score, sell_details = self.strategy.calc_sell_score(
                    ind, open_pos, sym, now=sim_now)
                self._last_sell_details = sell_details

                price = ind["price"]
                total = self._total_value(prices_close)
                self.peak = max(self.peak, total)

                # circuit breaker + auto-resume
                drawdown = (self.peak - total) / self.peak if self.peak > 0 else 0
                if self.paused and config.risk.auto_resume_enabled \
                        and drawdown <= config.risk.auto_resume_drawdown_pct:
                    self.paused = False
                if drawdown >= config.risk.max_drawdown_pct:
                    self.paused = True

                in_uptrend = self.strategy.regime_is_uptrend(ind)

                # ── SELL por señal ──
                # v2.19 — mismo gate y misma decisión que bot.py, vía sell_rules.
                # min_hold NO se chequea por edad de posición: producción lo
                # implementa como cooldown de venta a nivel símbolo, seteado al
                # comprar (ver el bloque BUY más abajo y bot.py:_execute_buy).
                if sell_score >= config.scoring.sell_partial and open_pos \
                        and not self._on_cooldown(sym, "sell", ts):
                    supports = self._levels(sym).get("supports", [])
                    blocked, _ = sell_rules.near_support_block(price, supports)
                    if not blocked:
                        for pos in list(open_pos):
                            plan = sell_rules.plan_sell(
                                sell_score, sell_details, pos["qty"], price)
                            if plan is None:
                                continue
                            runner_ok = sell_rules.runner_is_viable(plan.runner_qty, price)
                            reason = plan.reason
                            if plan.runner_qty > 0 and not runner_ok:
                                # Runner bajo el mínimo de orden: el resto queda
                                # abierto pero sin trailing (igual que bot.py).
                                reason = "partial_sell"
                            frac = plan.qty / pos["qty"]
                            self._close_position(sym, pos, price, reason, ts, frac)
                            if runner_ok:
                                pos["trailing_stop"] = plan.runner_trailing
                                pos["trailing_max"] = price
                        self._set_cooldown(sym, "sell", config.risk.cooldown_after_sell, ts)

                # ── BUY por señal ──
                buy_threshold = config.scoring.buy_light
                if not ind["above_ema_200"]:
                    buy_threshold = max(buy_threshold, 6)
                if sym == "BTCUSDT":
                    buy_threshold = max(buy_threshold, config.scoring.btc_min_buy_score)

                if buy_score >= buy_threshold and not self.paused:
                    regime_ok, _ = self.strategy.regime_allows_buy(ind)
                    # waterfall: ≥2 SL en 5 días
                    window_ms = config.crash_detector.consecutive_sl_days * 86_400_000
                    recent_sl = sum(1 for (s, t) in self.sl_history
                                    if s == sym and ts - t <= window_ms)
                    waterfall = recent_sl >= config.crash_detector.consecutive_sl_limit
                    if waterfall and not self._on_cooldown(sym, "buy", ts):
                        self._set_cooldown(sym, "buy",
                                           config.crash_detector.consecutive_sl_pause_hours * 60, ts)
                    # crash velocity
                    crash = (ind["drop_from_high"] >= config.crash_detector.crash_threshold_24h
                             and ind["rise_from_low"] < 0.05)
                    if crash and not self._on_cooldown(sym, "buy", ts):
                        self._set_cooldown(sym, "buy",
                                           config.crash_detector.crash_pause_hours * 60, ts)

                    if regime_ok and not waterfall and not crash:
                        amount = self.strategy.calc_buy_amount(
                            buy_score, self.usdt, total, open_pos)
                        if amount > 0 and self._can_buy(
                                sym, amount, total, price, buy_score, ts, in_uptrend):
                            qty = (amount / price) * (1 - FEE)
                            self.usdt -= amount
                            stop = self._calc_stop(price, ind["above_ema_200"], in_uptrend)
                            self._pos_id += 1
                            self.positions[sym].append({
                                "id": self._pos_id, "symbol": sym,
                                "entry_price": price, "qty": qty,
                                "value_usdt": amount, "ts": ts,
                                "entry_time": datetime.fromtimestamp(
                                    ts / 1000, tz=timezone.utc).isoformat(),
                                "stop_loss": stop, "trailing_stop": 0,
                                "trailing_max": price, "tp_taken": 0,
                                "trade_type": "spot", "leverage": 1.0,
                                "uptrend_entry": in_uptrend,
                                "score": buy_score,
                                "entry_details": buy_details,
                            })
                            self._set_cooldown(sym, "buy", config.risk.cooldown_after_buy, ts)
                            # v2.19 — paridad con bot.py:_execute_buy: la compra
                            # bloquea ventas del símbolo por min_hold_minutes.
                            # Antes el harness chequeaba la edad de cada posición,
                            # lo que permitía vender posiciones viejas que en
                            # producción quedaban bloqueadas por la compra nueva.
                            self._set_cooldown(sym, "sell",
                                               config.risk.min_hold_minutes, ts)

            self.equity_curve.append((ts, self._total_value(prices_close)))

        # liquidar al final para comparar en USDT
        final_prices = {s: self.data[s]["4h"][n - 1]["close"] for s in self.symbols}
        final_ts = self.data[self.symbols[0]]["4h"][n - 1]["timestamp"]
        for sym in self.symbols:
            for pos in list(self.positions[sym]):
                self._close_position(sym, pos, final_prices[sym], "end_of_backtest", final_ts)
        return self.report()

    def _calc_stop(self, entry_price, above_ema200, in_uptrend):
        RC, CD = config.risk, config.crash_detector
        if (config.regime.uptrend_mode_enabled
                and config.regime.uptrend_tight_stop and in_uptrend):
            sl_pct = config.regime.uptrend_stop_loss_pct
        elif not above_ema200 and CD.enabled:
            sl_pct = RC.stop_loss_pct * CD.downtrend_stop_multiplier
        else:
            sl_pct = RC.stop_loss_pct
        return entry_price * (1 - sl_pct)

    # ─── métricas ───

    def report(self) -> dict:
        final = self.usdt
        trades = [t for t in self.closed if t["reason"] != "end_of_backtest"]
        wins = [t for t in trades if t["pnl_usdt"] > 0]
        losses = [t for t in trades if t["pnl_usdt"] <= 0]
        max_dd, peak = 0.0, 0.0
        for _, v in self.equity_curve:
            peak = max(peak, v)
            max_dd = max(max_dd, (peak - v) / peak)
        gross_win = sum(t["pnl_usdt"] for t in wins)
        gross_loss = abs(sum(t["pnl_usdt"] for t in losses))
        up_trades = [t for t in trades if t["uptrend_entry"]]
        return {
            "final_usdt": round(final, 2),
            "return_pct": round((final - START_USDT) / START_USDT * 100, 2),
            "n_closes": len(trades),
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
            "avg_win_pct": round(sum(t["pnl_pct"] for t in wins) / len(wins) * 100, 2) if wins else 0,
            "avg_loss_pct": round(sum(t["pnl_pct"] for t in losses) / len(losses) * 100, 2) if losses else 0,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
            "max_drawdown_pct": round(max_dd * 100, 1),
            "uptrend_entries": len(up_trades),
            "uptrend_pnl_usdt": round(sum(t["pnl_usdt"] for t in up_trades), 2),
            "reasons": {r: sum(1 for t in trades if t["reason"] == r)
                        for r in sorted({t["reason"] for t in trades})},
        }


SUB_FLAGS = ["uptrend_guard_relax", "uptrend_pullback_score", "uptrend_reentry",
             "uptrend_tight_stop", "uptrend_sell_rise_off"]

VARIANTS = {
    # v2.14 tal cual
    "baseline": {"flags": [], "tp_partial": False},
    # piezas individuales (atribución)
    "guard": {"flags": ["uptrend_guard_relax"], "tp_partial": False},
    "pullback": {"flags": ["uptrend_pullback_score"], "tp_partial": False},
    "reentry": {"flags": ["uptrend_reentry"], "tp_partial": False},
    "sellfix": {"flags": ["uptrend_sell_rise_off"], "tp_partial": False},
    "tppartial": {"flags": [], "tp_partial": True},
    # combinaciones
    "entry": {"flags": SUB_FLAGS, "tp_partial": False},
    "full": {"flags": SUB_FLAGS, "tp_partial": True},
    # piezas con atribución positiva, sin pullback score
    "tightstop": {"flags": ["uptrend_tight_stop"], "tp_partial": False},
    "combo": {"flags": ["uptrend_guard_relax", "uptrend_reentry",
                        "uptrend_sell_rise_off"], "tp_partial": False},
    "combo_ts": {"flags": ["uptrend_guard_relax", "uptrend_reentry",
                           "uptrend_sell_rise_off", "uptrend_tight_stop"],
                 "tp_partial": False},
}


def apply_variant(name: str):
    v = VARIANTS[name]
    config.regime.uptrend_mode_enabled = bool(v["flags"])
    for f in SUB_FLAGS:
        setattr(config.regime, f, f in v["flags"])
    config.risk.take_profit_partial = v["tp_partial"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--variant", default=None, choices=list(VARIANTS))
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    parser.add_argument("--dump", default=None, help="directorio para volcar trades cerrados (JSON)")
    args = parser.parse_args()

    symbols = args.symbols.split(",")
    data = load_data(Path(args.data_dir), symbols)
    variants = [args.variant] if args.variant else list(VARIANTS)

    for name in variants:
        apply_variant(name)
        sim = BacktestSim(data, symbols)
        result = sim.run()
        print(f"\n═══ {name.upper()} ═══")
        for k, v in result.items():
            print(f"  {k}: {v}")
        if args.dump:
            out = Path(args.dump) / f"trades_{name}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(sim.closed, default=str))
            print(f"  [dump] {out}")


if __name__ == "__main__":
    main()
