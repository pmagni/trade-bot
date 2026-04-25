"""
Fetch historical OHLCV data from Bybit for backtesting.
Downloads 4h candles for the specified period.
"""

import csv
import time
import sys
from datetime import datetime, timedelta
from pybit.unified_trading import HTTP


def fetch_historical(symbol: str = "BTCUSDT", days: int = 365,
                     interval: str = "240", output_file: str = None):
    """
    Fetch historical candle data from Bybit.

    symbol: Trading pair (e.g., BTCUSDT)
    days: Number of days of history to fetch
    interval: Candle interval in minutes (240 = 4h)
    """
    if output_file is None:
        output_file = f"{symbol}_{days}d_{interval}m.csv"

    client = HTTP(testnet=False)  # Public endpoint, no auth needed

    end_time = int(datetime.now().timestamp() * 1000)
    start_time = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)

    all_candles = []
    current_end = end_time
    batch = 0

    print(f"Fetching {symbol} {interval}min candles for {days} days...")

    while current_end > start_time:
        try:
            resp = client.get_kline(
                category="spot",
                symbol=symbol,
                interval=interval,
                limit=200,
                end=current_end,
            )

            candles = resp["result"]["list"]
            if not candles:
                break

            for c in candles:
                ts = int(c[0])
                if ts >= start_time:
                    all_candles.append({
                        "timestamp": ts,
                        "open": float(c[1]),
                        "high": float(c[2]),
                        "low": float(c[3]),
                        "close": float(c[4]),
                        "volume": float(c[5]),
                        "turnover": float(c[6]),
                    })

            # Move window back
            current_end = int(candles[-1][0]) - 1
            batch += 1

            if batch % 10 == 0:
                print(f"  Batch {batch}: {len(all_candles)} candles so far...")

            time.sleep(0.1)  # Rate limiting

        except Exception as e:
            print(f"  Error at batch {batch}: {e}")
            time.sleep(1)
            continue

    # Sort chronologically and remove duplicates
    all_candles.sort(key=lambda x: x["timestamp"])
    seen = set()
    unique = []
    for c in all_candles:
        if c["timestamp"] not in seen:
            seen.add(c["timestamp"])
            unique.append(c)

    # Write CSV
    with open(output_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
        writer.writeheader()
        writer.writerows(unique)

    print(f"\nDone! {len(unique)} candles saved to {output_file}")
    print(f"Period: {datetime.fromtimestamp(unique[0]['timestamp']/1000)} to {datetime.fromtimestamp(unique[-1]['timestamp']/1000)}")

    return output_file


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 365

    fetch_historical(symbol, days)

    # Also fetch ETH if only BTC was specified
    if symbol == "BTCUSDT":
        print("\n" + "="*50)
        fetch_historical("ETHUSDT", days)
