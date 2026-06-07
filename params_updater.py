"""
Swing Trading Bot - Dynamic Params Updater
v2.13: Daily job that refreshes key support/resistance levels from recent candle data
and computes ATR-based stop percentages.

Replaces stale static config levels automatically so the bot always operates
with levels grounded in the current market regime — not last month's prices.

Algorithm:
  1. Fetch last 30 daily candles per symbol
  2. Detect swing lows (supports) and swing highs (resistances) using local-extrema logic
  3. Cluster nearby levels within 1.8% (volume-weighted average price)
  4. Filter: supports below current price, resistances above
  5. Sort by proximity and keep the top N for each side
  6. Derive stop_level: nearest strong support ≥7% below current price
  7. Compute ATR-14d-based stop percentage, clamped [5%, 10%]
  8. Persist to DB (bot_state table as JSON) so strategy and bot can read them next scan
"""

import logging
import statistics
from datetime import datetime, timezone
from typing import List, Dict

logger = logging.getLogger("params_updater")

# ─── TUNING CONSTANTS ───────────────────────────────────────────────────────
CLUSTER_TOLERANCE   = 0.018   # 1.8%  — merge swing levels within this distance
SWING_LOOKBACK      = 2       # candles on each side to confirm a swing point
RECENT_CANDLES      = 30      # days of history used for level detection
ATR_PERIOD          = 14      # days for ATR calculation
ATR_STOP_MULTIPLIER = 2.0     # stop_pct = (ATR / price) × multiplier
MIN_STOP_PCT        = 0.05    # absolute floor for dynamic stop
MAX_STOP_PCT        = 0.10    # absolute ceiling for dynamic stop
MAX_SUPPORTS        = 4       # max support levels to keep
MAX_RESISTANCES     = 3       # max resistance levels to keep
MIN_DIST_FROM_PRICE = 0.005   # 0.5% — ignore levels too close to current price
STOP_MIN_DROP_PCT   = 0.07    # stop_level must be ≥7% below current price


# ─── SWING DETECTION ────────────────────────────────────────────────────────

def _find_swing_lows(candles: List[dict], lookback: int = SWING_LOOKBACK) -> List[dict]:
    """
    Identify local swing lows (support candidates).
    A candle is a swing low when its `low` is less than or equal to all
    candles within `lookback` positions on each side.
    """
    result = []
    for i in range(lookback, len(candles) - lookback):
        low_i = candles[i]["low"]
        neighbors = [
            candles[j]["low"]
            for j in range(i - lookback, i + lookback + 1)
            if j != i
        ]
        if all(low_i <= n for n in neighbors):
            result.append({
                "price": low_i,
                "volume": candles[i].get("volume", 0),
            })
    return result


def _find_swing_highs(candles: List[dict], lookback: int = SWING_LOOKBACK) -> List[dict]:
    """
    Identify local swing highs (resistance candidates).
    A candle is a swing high when its `high` is greater than or equal to all
    candles within `lookback` positions on each side.
    """
    result = []
    for i in range(lookback, len(candles) - lookback):
        high_i = candles[i]["high"]
        neighbors = [
            candles[j]["high"]
            for j in range(i - lookback, i + lookback + 1)
            if j != i
        ]
        if all(high_i >= n for n in neighbors):
            result.append({
                "price": high_i,
                "volume": candles[i].get("volume", 0),
            })
    return result


def _cluster_levels(levels: List[dict], tolerance: float = CLUSTER_TOLERANCE) -> List[dict]:
    """
    Merge nearby price levels into clusters.
    Each cluster becomes a single level with volume-weighted average price.
    `tests` = number of swing points that fell into this cluster (touch count).
    """
    if not levels:
        return []

    sorted_levels = sorted(levels, key=lambda x: x["price"])
    clusters: List[List[dict]] = []
    current: List[dict] = [sorted_levels[0]]

    for lvl in sorted_levels[1:]:
        ref = statistics.mean(l["price"] for l in current)
        if abs(lvl["price"] - ref) / ref <= tolerance:
            current.append(lvl)
        else:
            clusters.append(current)
            current = [lvl]
    clusters.append(current)

    result = []
    for cluster in clusters:
        total_vol = sum(l["volume"] for l in cluster)
        if total_vol > 0:
            wap = sum(l["price"] * l["volume"] for l in cluster) / total_vol
        else:
            wap = statistics.mean(l["price"] for l in cluster)
        result.append({
            "price": round(wap, 2),
            "tests": len(cluster),
            "volume": total_vol,
        })
    return result


def _score_bonus(tests: int) -> int:
    """Score bonus based on how many times a level was tested (touched)."""
    if tests >= 3:
        return 3
    elif tests >= 2:
        return 2
    return 1


# ─── MAIN DETECTION FUNCTIONS ───────────────────────────────────────────────

def detect_key_levels(symbol: str, candles_daily: List[dict], current_price: float) -> dict:
    """
    Auto-detect support and resistance levels from daily candles.

    Returns a dict compatible with KeyLevelsConfig.levels format:
    {
        "supports": [{"price": float, "label": str, "score_bonus": int}, ...],
        "resistances": [{"price": float, "label": str, "score_bonus": int}, ...],
        "stop_level": float,
        "updated_at": str,
        "source": "auto",
    }

    Returns {} if there are not enough candles for reliable detection.
    Falls back to static config levels in the bot if this returns {}.
    """
    if len(candles_daily) < 10:
        logger.warning(
            f"[{symbol}] Not enough candles for level detection "
            f"({len(candles_daily)} candles, need ≥10)"
        )
        return {}

    # Use the most recent RECENT_CANDLES days
    recent = candles_daily[-RECENT_CANDLES:]

    # Detect swing points
    raw_lows  = _find_swing_lows(recent,  lookback=SWING_LOOKBACK)
    raw_highs = _find_swing_highs(recent, lookback=SWING_LOOKBACK)

    # Cluster nearby levels
    support_clusters    = _cluster_levels(raw_lows)
    resistance_clusters = _cluster_levels(raw_highs)

    # Filter: supports must be below current price, resistances above
    min_dist = MIN_DIST_FROM_PRICE
    supports    = [c for c in support_clusters    if c["price"] < current_price * (1 - min_dist)]
    resistances = [c for c in resistance_clusters if c["price"] > current_price * (1 + min_dist)]

    # Sort by proximity to current price (closest first)
    supports.sort(   key=lambda x: current_price - x["price"])
    resistances.sort(key=lambda x: x["price"] - current_price)

    # Cap at max counts
    supports    = supports[:MAX_SUPPORTS]
    resistances = resistances[:MAX_RESISTANCES]

    # Derive stop_level: use nearest support that is ≥ STOP_MIN_DROP_PCT below price.
    # Place the stop slightly below (0.5%) that support to avoid noise triggers.
    # If no suitable support found, fall back to 8% below current price.
    stop_level = round(current_price * (1 - 0.08))
    for s in sorted(supports, key=lambda x: x["price"], reverse=True):
        if s["price"] <= current_price * (1 - STOP_MIN_DROP_PCT):
            stop_level = round(s["price"] * 0.995)
            break

    # Build output in config-compatible format
    supports_out = [
        {
            "price": round(s["price"]),
            "label": f"Soporte auto — {s['tests']} toque{'s' if s['tests'] > 1 else ''}",
            "score_bonus": _score_bonus(s["tests"]),
        }
        for s in supports
    ]
    resistances_out = [
        {
            "price": round(r["price"]),
            "label": f"Resistencia auto — {r['tests']} toque{'s' if r['tests'] > 1 else ''}",
            "score_bonus": _score_bonus(r["tests"]),
        }
        for r in resistances
    ]

    logger.info(
        f"[{symbol}] Level detection: {len(supports_out)} soportes, "
        f"{len(resistances_out)} resistencias, stop_level={stop_level:,.0f}"
    )

    return {
        "supports":     supports_out,
        "resistances":  resistances_out,
        "stop_level":   stop_level,
        "updated_at":   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source":       "auto",
    }


def calc_atr_stop_pct(candles_daily: List[dict], current_price: float) -> float:
    """
    Compute ATR-14d-based dynamic stop percentage.

    In high-volatility regimes the stop widens to avoid noise-triggered exits.
    In low-volatility regimes the stop tightens to protect profits.
    Formula: stop_pct = (ATR_14 / price) × ATR_STOP_MULTIPLIER
    Clamped to [MIN_STOP_PCT, MAX_STOP_PCT].

    Examples at BTC $60K:
      ATR $2,400 (4%) → stop = 4% × 2.0 = 8%
      ATR $1,800 (3%) → stop = 3% × 2.0 = 6%
      ATR  $900 (1.5%) → stop = min(3%, 5%) = 5%
    """
    if len(candles_daily) < ATR_PERIOD + 1 or current_price <= 0:
        return MIN_STOP_PCT

    recent = candles_daily[-(ATR_PERIOD + 1):]
    true_ranges = []
    for i in range(1, len(recent)):
        h = recent[i]["high"]
        l = recent[i]["low"]
        pc = recent[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        true_ranges.append(tr)

    if not true_ranges:
        return MIN_STOP_PCT

    atr = statistics.mean(true_ranges)
    raw_pct = (atr / current_price) * ATR_STOP_MULTIPLIER
    clamped = max(MIN_STOP_PCT, min(MAX_STOP_PCT, raw_pct))
    return round(clamped, 3)
