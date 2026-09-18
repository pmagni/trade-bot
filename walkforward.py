"""
Walk-forward harness — v2.21 (rama exp/alpha-walkforward)

Por qué existe: `backtest.py` corre una sola ventana in-sample y su "baseline"
es la config v2.14, no buy & hold. Con eso, la regla de la casa ("no ship sin
batir baseline") nunca midió el costo de oportunidad de no hacer nada.

Este runner agrega las dos piezas que faltaban:
  1. Buy & hold equiponderado sobre el MISMO universo y la MISMA ventana.
  2. Ventanas temporales no traslapadas, con calibración y validación separadas.

Convención de ventanas: el dataset se parte en N tramos iguales sobre las velas
4h. El warmup de indicadores usa las velas previas al tramo, que siguen
disponibles — no hay fuga hacia adelante porque ninguna decisión mira más allá
del índice en curso.

Uso:
    python3 walkforward.py --data-dir data --windows 3
    python3 walkforward.py --data-dir data --variant prod,be_ts
"""

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from backtest import BacktestSim, WARMUP, buy_and_hold, load_data
from config import config

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]

# Cada variante es un set de overrides sobre config. `prod` es exactamente lo
# que corre hoy en el servidor (v2.20 = flags COMBO).
PROD = {
    "regime.uptrend_mode_enabled": True,
    "regime.uptrend_guard_relax": True,
    "regime.uptrend_pullback_score": False,
    "regime.uptrend_reentry": True,
    "regime.uptrend_tight_stop": False,
    "regime.uptrend_sell_rise_off": True,
    "risk.take_profit_partial": False,
    # experimentales, apagados
    "risk.breakeven_trigger_pct": 0.0,
    "risk.time_stop_hours": 0.0,
    "risk.tp_arms_trailing": False,
    "scoring.global_min_buy_score": 0,
    "scoring.sell_partial": 5,
    "risk.take_profit_pct": 0.03,
    "risk.tp_arm_trailing_distance": 0.0,
    "risk.trailing_stop_activation": 0.015,
    "risk.trailing_stop_distance": 0.008,
}


def v(**over):
    d = dict(PROD)
    d.update(over)
    return d


VARIANTS = {
    "prod":        v(),
    "noreentry":   v(**{"regime.uptrend_reentry": False}),
    # piezas nuevas, una por una (atribución)
    "be":          v(**{"risk.breakeven_trigger_pct": 0.010}),
    "minscore6":   v(**{"scoring.global_min_buy_score": 6}),
    "timestop48":  v(**{"risk.time_stop_hours": 48}),
    "sellgate6":   v(**{"scoring.sell_partial": 6}),
    "sellgate7":   v(**{"scoring.sell_partial": 7}),
    "tprun":       v(**{"risk.tp_arms_trailing": True}),
    # combinaciones
    "be_ts":       v(**{"risk.breakeven_trigger_pct": 0.010,
                        "risk.time_stop_hours": 48}),
    "be_ts_s6":    v(**{"risk.breakeven_trigger_pct": 0.010,
                        "risk.time_stop_hours": 48,
                        "scoring.global_min_buy_score": 6}),
    "be_tprun":    v(**{"risk.breakeven_trigger_pct": 0.010,
                        "risk.tp_arms_trailing": True}),
    "be_ts_tprun": v(**{"risk.breakeven_trigger_pct": 0.010,
                        "risk.time_stop_hours": 48,
                        "risk.tp_arms_trailing": True}),
    # ── dejar correr al ganador: el TP de +3% es el techo estructural ──
    "notp":        v(**{"risk.take_profit_pct": 0.0}),
    "tp8":         v(**{"risk.take_profit_pct": 0.08}),
    "tprun_wide":  v(**{"risk.tp_arms_trailing": True,
                        "risk.tp_arm_trailing_distance": 0.03}),
    "be_tprun_w":  v(**{"risk.breakeven_trigger_pct": 0.010,
                        "risk.tp_arms_trailing": True,
                        "risk.tp_arm_trailing_distance": 0.03}),
    "be_ts_tpw":   v(**{"risk.breakeven_trigger_pct": 0.010,
                        "risk.time_stop_hours": 48,
                        "risk.tp_arms_trailing": True,
                        "risk.tp_arm_trailing_distance": 0.03}),
    # ── dejar correr de verdad: el techo real es el TRAILING (activa a +1.5%,
    #    trail 0.8%) — no el take-profit. Con TP=0 y trailing intacto el
    #    resultado es identico, prueba de que el TP casi nunca es el que corta.
    "trailw1":     v(**{"risk.take_profit_pct": 0.0,
                        "risk.trailing_stop_activation": 0.03,
                        "risk.trailing_stop_distance": 0.025}),
    "trailw2":     v(**{"risk.take_profit_pct": 0.0,
                        "risk.trailing_stop_activation": 0.05,
                        "risk.trailing_stop_distance": 0.04}),
    "trailw3":     v(**{"risk.take_profit_pct": 0.0,
                        "risk.trailing_stop_activation": 0.08,
                        "risk.trailing_stop_distance": 0.06}),
    # ── candidatos serios salidos de la primera pasada ──
    "ms7_sg6":     v(**{"scoring.global_min_buy_score": 7,
                        "scoring.sell_partial": 6}),
    "ms6_sg5":     v(**{"scoring.global_min_buy_score": 6}),
    "ms6_sg7":     v(**{"scoring.global_min_buy_score": 6,
                        "scoring.sell_partial": 7}),
    "ms6_sg6":     v(**{"scoring.global_min_buy_score": 6,
                        "scoring.sell_partial": 6}),
    "ms6_sg6_nr":  v(**{"scoring.global_min_buy_score": 6,
                        "scoring.sell_partial": 6,
                        "regime.uptrend_reentry": False}),
    "ms6_sg6_nr_tw": v(**{"scoring.global_min_buy_score": 6,
                          "scoring.sell_partial": 6,
                          "regime.uptrend_reentry": False,
                          "risk.take_profit_pct": 0.0,
                          "risk.trailing_stop_activation": 0.03,
                          "risk.trailing_stop_distance": 0.025}),
    "ms6_nr_tw2":  v(**{"scoring.global_min_buy_score": 6,
                        "regime.uptrend_reentry": False,
                        "risk.take_profit_pct": 0.0,
                        "risk.trailing_stop_activation": 0.05,
                        "risk.trailing_stop_distance": 0.04}),
    "be_ts_s6_tpw": v(**{"risk.breakeven_trigger_pct": 0.010,
                         "risk.time_stop_hours": 48,
                         "scoring.global_min_buy_score": 6,
                         "risk.tp_arms_trailing": True,
                         "risk.tp_arm_trailing_distance": 0.03}),
}


def apply(over: dict):
    for path, val in over.items():
        sec, attr = path.split(".")
        setattr(getattr(config, sec), attr, val)


def windows(n_candles: int, n_win: int):
    span = (n_candles - WARMUP) // n_win
    return [(WARMUP + k * span, WARMUP + (k + 1) * span) for k in range(n_win)]


def label(data, i):
    ts = data[SYMBOLS[0]]["4h"][i]["timestamp"]
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--windows", type=int, default=3)
    ap.add_argument("--variant", default=None, help="csv; default todas")
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    syms = args.symbols.split(",")
    data = load_data(Path(args.data_dir), syms)
    n = min(len(data[s]["4h"]) for s in syms)
    wins = windows(n, args.windows)
    names = args.variant.split(",") if args.variant else list(VARIANTS)

    out = {"windows": [], "results": {}}
    print(f"Universo: {','.join(syms)} | velas {n} | ventanas {args.windows}\n")

    bh = {}
    for k, (a, b) in enumerate(wins):
        r = buy_and_hold(data, syms, a, b)
        bh[k] = r
        out["windows"].append({"i": k, "from": label(data, a), "to": label(data, b - 1),
                               "buy_and_hold": r})
        print(f"W{k+1} {label(data,a)} → {label(data,b-1)}  "
              f"BUY&HOLD {r['return_pct']:+6.2f}%  maxDD {r['max_drawdown_pct']:4.1f}%  {r['per_symbol_pct']}")

    print(f"\n{'variante':<14}" + "".join(f"{'W'+str(k+1):>26}" for k in range(len(wins))))
    print(f"{'':<14}" + "".join(f"{'ret / vs B&H / DD / PF':>26}" for _ in wins))
    print("-" * (14 + 26 * len(wins)))

    for name in names:
        apply(VARIANTS[name])
        row = []
        rec = []
        for k, (a, b) in enumerate(wins):
            sim = BacktestSim(copy.copy(data), syms, i_start=a, i_end=b)
            res = sim.run()
            delta = res["return_pct"] - bh[k]["return_pct"]
            rec.append({**res, "vs_bh_pp": round(delta, 2),
                        "bh_pct": bh[k]["return_pct"]})
            row.append(f"{res['return_pct']:+6.2f}% {delta:+6.2f}pp "
                       f"{res['max_drawdown_pct']:4.1f}% {res['profit_factor']:>4}")
        out["results"][name] = rec
        print(f"{name:<14}" + "".join(f"{c:>26}" for c in row))

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2))
        print(f"\n[json] {args.json_out}")


if __name__ == "__main__":
    main()
