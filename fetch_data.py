"""
Fetch de klines históricas de Bybit para el harness de backtest.

Genera los archivos que espera backtest.py:
    data/{SYMBOL}_4h.json
    data/{SYMBOL}_daily.json

Formato idéntico al de exchange.get_ohlcv() — mismas claves, mismo orden
cronológico — para que el backtest consuma velas indistinguibles de las que
ve el bot en vivo.

Uso:
    python3 fetch_data.py                      # universo actual, mar-2025 → hoy
    python3 fetch_data.py --symbols BTCUSDT,ETHUSDT --start 2025-03-01

Usa el endpoint público v5 de Bybit: no requiere credenciales (mismo motivo
por el que 'v 2.0/fetch_data.py' hacía HTTP(testnet=False)). Solo necesita
`requests`, así que corre en el venv mínimo de tests.
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://api.bybit.com/v5/market/kline"
MAX_LIMIT = 1000  # tope de Bybit por request

# Intervalos tal como los nombra Bybit → sufijo del archivo
INTERVALS = {
    "240": "4h",
    "D": "daily",
}


def _to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _parse_candles(rows: list) -> list:
    """Convierte la respuesta cruda de Bybit al formato de exchange.get_ohlcv()."""
    return [
        {
            "timestamp": int(c[0]),
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5]),
            "turnover": float(c[6]),
        }
        for c in rows
    ]


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """
    Descarga velas paginando hacia atrás desde end_ms.

    Bybit devuelve como máximo 1000 velas por request, más nuevas primero, y
    acota por el parámetro `end`. Se avanza moviendo `end` al timestamp más
    viejo recibido, hasta cubrir start_ms.
    """
    collected = {}
    cursor = end_ms

    while cursor > start_ms:
        resp = requests.get(
            BASE_URL,
            params={
                "category": "spot",
                "symbol": symbol,
                "interval": interval,
                "end": cursor,
                "limit": MAX_LIMIT,
            },
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()

        if payload.get("retCode") != 0:
            raise RuntimeError(f"Bybit error para {symbol} {interval}: {payload.get('retMsg')}")

        rows = payload["result"]["list"]
        if not rows:
            break

        candles = _parse_candles(rows)
        for c in candles:
            collected[c["timestamp"]] = c  # dedup por timestamp

        oldest = min(c["timestamp"] for c in candles)
        if oldest >= cursor:
            break  # sin avance: evita loop infinito
        cursor = oldest

        time.sleep(0.15)  # cortesía con el rate limit público

    # Orden cronológico y recorte al rango pedido
    out = [c for ts, c in sorted(collected.items()) if start_ms <= ts <= end_ms]
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,BNBUSDT")
    parser.add_argument("--start", default="2025-03-01", help="YYYY-MM-DD (UTC)")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD (UTC); default: hoy")
    parser.add_argument("--out", default="data")
    args = parser.parse_args()

    start_ms = _to_ms(args.start)
    end_ms = _to_ms(args.end) if args.end else int(time.time() * 1000)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for symbol in args.symbols.split(","):
        symbol = symbol.strip()
        for interval, suffix in INTERVALS.items():
            candles = fetch_klines(symbol, interval, start_ms, end_ms)
            path = out_dir / f"{symbol}_{suffix}.json"
            path.write_text(json.dumps(candles))

            if candles:
                first = datetime.fromtimestamp(candles[0]["timestamp"] / 1000, tz=timezone.utc)
                last = datetime.fromtimestamp(candles[-1]["timestamp"] / 1000, tz=timezone.utc)
                rango = f"{first:%Y-%m-%d} → {last:%Y-%m-%d}"
            else:
                rango = "SIN DATOS"

            print(f"{path}: {len(candles)} velas  [{rango}]")


if __name__ == "__main__":
    main()
