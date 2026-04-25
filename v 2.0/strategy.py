"""
Swing Trading Bot - Strategy Module
Calculates technical indicators, buy/sell scores, and support/resistance zones.
"""

import logging
import requests
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from config import config

logger = logging.getLogger("strategy")

IC = config.indicators  # shortcut


class Strategy:

    def __init__(self):
        self._fg_cache = {"value": None, "timestamp": 0}

    # ═══════════════════════════════════════
    # INDICATOR CALCULATIONS
    # ═══════════════════════════════════════

    def compute_indicators(self, candles_4h: list, candles_daily: list = None) -> dict:
        """
        Compute all technical indicators from OHLCV data.
        Returns dict with all indicator values.
        """
        df = pd.DataFrame(candles_4h)
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        indicators = {}

        # RSI
        indicators["rsi"] = self._calc_rsi(close, IC.rsi_period)

        # Bollinger Bands
        bb = self._calc_bollinger(close, IC.bb_period, IC.bb_std)
        indicators["bb_upper"] = bb["upper"]
        indicators["bb_lower"] = bb["lower"]
        indicators["bb_middle"] = bb["middle"]
        indicators["bb_pct_b"] = bb["pct_b"]

        # EMAs
        indicators["ema_fast"] = self._calc_ema(close, IC.ema_fast)
        indicators["ema_slow"] = self._calc_ema(close, IC.ema_slow)

        # EMA cross: positive = fast above slow (bullish)
        indicators["ema_cross"] = indicators["ema_fast"] - indicators["ema_slow"]
        # Detect fresh cross (current bar vs previous)
        ema_fast_series = close.ewm(span=IC.ema_fast, adjust=False).mean()
        ema_slow_series = close.ewm(span=IC.ema_slow, adjust=False).mean()
        diff = ema_fast_series - ema_slow_series
        indicators["ema_cross_bullish"] = (diff.iloc[-1] > 0) and (diff.iloc[-2] <= 0)
        indicators["ema_cross_bearish"] = (diff.iloc[-1] < 0) and (diff.iloc[-2] >= 0)

        # EMA 200 (from daily data if available, else approximate from 4h)
        if candles_daily and len(candles_daily) >= IC.ema_trend:
            daily_close = pd.Series([c["close"] for c in candles_daily])
            indicators["ema_200"] = self._calc_ema(daily_close, IC.ema_trend)
        else:
            # Approximate: 200 daily ~ 1200 4h candles, use what we have
            indicators["ema_200"] = self._calc_ema(close, min(len(close) - 1, 200))

        indicators["above_ema_200"] = close.iloc[-1] > indicators["ema_200"]

        # Volume analysis
        vol_avg = volume.rolling(IC.volume_period).mean().iloc[-1]
        indicators["volume_current"] = volume.iloc[-1]
        indicators["volume_avg"] = vol_avg
        indicators["volume_ratio"] = volume.iloc[-1] / vol_avg if vol_avg > 0 else 1.0

        # Current price
        indicators["price"] = close.iloc[-1]

        # Price drop/rise from local high/low
        if candles_daily and len(candles_daily) >= IC.local_period_days:
            daily_df = pd.DataFrame(candles_daily[-IC.local_period_days:])
            local_high = daily_df["high"].max()
            local_low = daily_df["low"].min()
        else:
            recent = df.tail(IC.local_period_days * 6)  # ~14 days in 4h candles
            local_high = recent["high"].max()
            local_low = recent["low"].min()

        indicators["local_high"] = local_high
        indicators["local_low"] = local_low
        indicators["drop_from_high"] = (local_high - close.iloc[-1]) / local_high if local_high > 0 else 0
        indicators["rise_from_low"] = (close.iloc[-1] - local_low) / local_low if local_low > 0 else 0

        # Fear & Greed Index
        indicators["fear_greed"] = self._get_fear_greed()

        # Support & Resistance zones
        sr = self._calc_support_resistance(candles_4h, candles_daily)
        indicators["supports"] = sr["supports"]
        indicators["resistances"] = sr["resistances"]
        indicators["zone"] = sr["zone"]
        indicators["zone_score"] = sr["zone_score"]

        return indicators

    # ═══════════════════════════════════════
    # SCORING
    # ═══════════════════════════════════════

    def calc_buy_score(self, indicators: dict) -> Tuple[int, dict]:
        """
        Calculate buy score (0-15+).
        Returns (score, details_dict).
        """
        score = 0
        details = {}

        # RSI
        rsi = indicators["rsi"]
        if rsi < IC.rsi_extreme_oversold:
            score += 3
            details["rsi"] = f"RSI {rsi:.1f} < {IC.rsi_extreme_oversold} (extreme oversold) → +3"
        elif rsi < IC.rsi_oversold:
            score += 2
            details["rsi"] = f"RSI {rsi:.1f} < {IC.rsi_oversold} (oversold) → +2"

        # Bollinger Bands
        if indicators["price"] < indicators["bb_lower"]:
            score += 2
            details["bb"] = f"Price below lower BB → +2"
        if indicators["bb_pct_b"] < IC.bb_low_threshold:
            score += 1
            details["bb_pctb"] = f"%B {indicators['bb_pct_b']:.3f} < {IC.bb_low_threshold} → +1"

        # EMA Cross
        if indicators["ema_cross_bullish"]:
            score += 1
            details["ema_cross"] = "Bullish EMA cross → +1"

        # Above EMA 200 (trend confirmation)
        if indicators["above_ema_200"]:
            score += 1
            details["ema_200"] = "Price above EMA200 (uptrend) → +1"

        # Volume spike
        if indicators["volume_ratio"] > IC.volume_buy_multiplier:
            score += 1
            details["volume"] = f"Volume {indicators['volume_ratio']:.1f}x avg → +1"

        # Fear & Greed
        fg = indicators["fear_greed"]
        if fg is not None and fg < IC.fg_extreme_fear:
            score += 2
            details["fear_greed"] = f"Fear & Greed {fg} (extreme fear) → +2"

        # Drop from local high
        drop = indicators["drop_from_high"]
        if drop > IC.drop_strong:
            score += 3
            details["drop"] = f"Drop {drop:.1%} from 14d high (strong) → +3"
        elif drop > IC.drop_moderate:
            score += 2
            details["drop"] = f"Drop {drop:.1%} from 14d high (moderate) → +2"

        # Support/Resistance zone bonus
        zone_score = indicators.get("zone_score", 0)
        if zone_score > 0:
            score += zone_score
            details["zone"] = f"Price in {indicators['zone']} zone → +{zone_score}"

        return score, details

    def calc_sell_score(self, indicators: dict, positions: list = None) -> Tuple[int, dict]:
        """
        Calculate sell score (0-15+).
        Returns (score, details_dict).
        """
        score = 0
        details = {}

        # RSI
        rsi = indicators["rsi"]
        if rsi > IC.rsi_extreme_overbought:
            score += 3
            details["rsi"] = f"RSI {rsi:.1f} > {IC.rsi_extreme_overbought} (extreme overbought) → +3"
        elif rsi > IC.rsi_overbought:
            score += 2
            details["rsi"] = f"RSI {rsi:.1f} > {IC.rsi_overbought} (overbought) → +2"

        # Bollinger Bands
        if indicators["price"] > indicators["bb_upper"]:
            score += 2
            details["bb"] = "Price above upper BB → +2"
        if indicators["bb_pct_b"] > IC.bb_high_threshold:
            score += 1
            details["bb_pctb"] = f"%B {indicators['bb_pct_b']:.3f} > {IC.bb_high_threshold} → +1"

        # EMA Cross bearish
        if indicators["ema_cross_bearish"]:
            score += 1
            details["ema_cross"] = "Bearish EMA cross → +1"

        # Volume climax
        if indicators["volume_ratio"] > IC.volume_sell_multiplier:
            score += 1
            details["volume"] = f"Volume climax {indicators['volume_ratio']:.1f}x avg → +1"

        # Fear & Greed
        fg = indicators["fear_greed"]
        if fg is not None and fg > IC.fg_extreme_greed:
            score += 2
            details["fear_greed"] = f"Fear & Greed {fg} (extreme greed) → +2"

        # Rise from local low
        rise = indicators["rise_from_low"]
        if rise > IC.rise_strong:
            score += 3
            details["rise"] = f"Rise {rise:.1%} from 14d low (strong) → +3"
        elif rise > IC.rise_moderate:
            score += 2
            details["rise"] = f"Rise {rise:.1%} from 14d low (moderate) → +2"

        # Resistance zone
        zone_score = indicators.get("zone_score", 0)
        if zone_score < 0:
            score += abs(zone_score)
            details["zone"] = f"Price in {indicators['zone']} zone → +{abs(zone_score)}"

        # Profit target from open positions
        if positions:
            max_profit = max(
                (indicators["price"] - p["entry_price"]) / p["entry_price"]
                for p in positions
            )
            if max_profit > config.scoring.profit_target_pct:
                score += 2
                details["profit"] = f"Position at {max_profit:.1%} profit (> {config.scoring.profit_target_pct:.0%} target) → +2"

        return score, details

    # ═══════════════════════════════════════
    # POSITION SIZING
    # ═══════════════════════════════════════

    def calc_buy_amount(self, buy_score: int, available_usdt: float,
                        total_portfolio: float) -> float:
        """Calculate how much USDT to spend based on buy score."""
        SC = config.scoring
        RC = config.risk

        # Ensure minimum reserve
        max_deployable = available_usdt - (total_portfolio * RC.min_reserve_pct)
        if max_deployable <= 0:
            return 0

        # Determine allocation percentage
        if buy_score >= SC.buy_maximum:
            pct = SC.buy_maximum_pct
        elif buy_score >= SC.buy_strong:
            pct = SC.buy_strong_pct
        elif buy_score >= SC.buy_moderate:
            pct = SC.buy_moderate_pct
        elif buy_score >= SC.buy_light:
            pct = SC.buy_light_pct
        else:
            return 0

        amount = max_deployable * pct

        # Apply max per trade limit
        amount = min(amount, available_usdt * RC.max_per_trade_pct)

        # Ensure above minimum order
        if amount < RC.min_order_usdt:
            return 0

        return round(amount, 2)

    def calc_sell_qty(self, sell_score: int, position_qty: float) -> float:
        """Calculate how much to sell based on sell score."""
        SC = config.scoring

        if sell_score >= SC.sell_total:
            pct = SC.sell_total_pct
        elif sell_score >= SC.sell_strong:
            pct = SC.sell_strong_pct
        elif sell_score >= SC.sell_moderate:
            pct = SC.sell_moderate_pct
        elif sell_score >= SC.sell_partial:
            pct = SC.sell_partial_pct
        else:
            return 0

        return position_qty * pct

    # ═══════════════════════════════════════
    # TECHNICAL INDICATOR HELPERS
    # ═══════════════════════════════════════

    @staticmethod
    def _calc_rsi(close: pd.Series, period: int) -> float:
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]

    @staticmethod
    def _calc_bollinger(close: pd.Series, period: int, std: float) -> dict:
        middle = close.rolling(window=period).mean()
        std_dev = close.rolling(window=period).std()
        upper = middle + (std_dev * std)
        lower = middle - (std_dev * std)
        pct_b = (close - lower) / (upper - lower)
        return {
            "upper": upper.iloc[-1],
            "lower": lower.iloc[-1],
            "middle": middle.iloc[-1],
            "pct_b": pct_b.iloc[-1],
        }

    @staticmethod
    def _calc_ema(close: pd.Series, period: int) -> float:
        return close.ewm(span=period, adjust=False).mean().iloc[-1]

    def _get_fear_greed(self) -> Optional[int]:
        """Fetch Fear & Greed Index from alternative.me API. Cache for 1 hour."""
        import time
        now = time.time()
        if self._fg_cache["value"] is not None and (now - self._fg_cache["timestamp"]) < 3600:
            return self._fg_cache["value"]

        try:
            resp = requests.get(
                "https://api.alternative.me/fng/?limit=1",
                timeout=10
            )
            data = resp.json()
            value = int(data["data"][0]["value"])
            self._fg_cache = {"value": value, "timestamp": now}
            return value
        except Exception as e:
            logger.warning(f"Could not fetch Fear & Greed Index: {e}")
            return self._fg_cache["value"]  # Return cached or None

    # ═══════════════════════════════════════
    # SUPPORT & RESISTANCE
    # ═══════════════════════════════════════

    def _calc_support_resistance(self, candles_4h: list, candles_daily: list = None) -> dict:
        """
        Calculate support and resistance zones using:
        - Pivot Points
        - Fibonacci retracement
        - Local min/max
        """
        # Use daily data if available, else approximate from 4h
        if candles_daily and len(candles_daily) >= 5:
            df = pd.DataFrame(candles_daily)
        else:
            df = pd.DataFrame(candles_4h)

        high = df["high"]
        low = df["low"]
        close = df["close"]
        current_price = close.iloc[-1]

        # Pivot Points (from last completed period)
        h = high.iloc[-2]
        l = low.iloc[-2]
        c = close.iloc[-2]
        pivot = (h + l + c) / 3
        s1 = 2 * pivot - h
        s2 = pivot - (h - l)
        s3 = l - 2 * (h - pivot)
        r1 = 2 * pivot - l
        r2 = pivot + (h - l)
        r3 = h + 2 * (pivot - l)

        # Fibonacci from swing high/low of last 30 candles
        recent = df.tail(30)
        swing_high = recent["high"].max()
        swing_low = recent["low"].min()
        fib_range = swing_high - swing_low

        fib_levels = {}
        for level in IC.fib_levels:
            fib_levels[f"fib_{level}"] = swing_high - (fib_range * level)

        # Collect all support and resistance levels
        supports = sorted([
            s1, s2, s3,
            fib_levels.get("fib_0.618", 0),
            fib_levels.get("fib_0.786", 0),
            swing_low,
        ])
        resistances = sorted([
            r1, r2, r3,
            fib_levels.get("fib_0.236", 0),
            fib_levels.get("fib_0.382", 0),
            swing_high,
        ])

        # Filter: only levels within 20% of current price
        supports = [s for s in supports if s > current_price * 0.80 and s < current_price]
        resistances = [r for r in resistances if r < current_price * 1.20 and r > current_price]

        # Determine zone
        tolerance = IC.pivot_zone_tolerance
        zone = "neutral"
        zone_score = 0

        # Check if near strong support
        for s in supports:
            if abs(current_price - s) / current_price < tolerance:
                if s <= s2 or s <= fib_levels.get("fib_0.618", 0):
                    zone = "strong_buy"
                    zone_score = 2
                else:
                    zone = "buy"
                    zone_score = 1
                break

        # Check if near resistance
        for r in resistances:
            if abs(current_price - r) / current_price < tolerance:
                if r >= r2 or r >= fib_levels.get("fib_0.236", float("inf")):
                    zone = "strong_sell"
                    zone_score = -2
                else:
                    zone = "sell"
                    zone_score = -1
                break

        return {
            "supports": supports[:3],     # Top 3 closest
            "resistances": resistances[:3],
            "pivot": pivot,
            "zone": zone,
            "zone_score": zone_score,
            "fib_levels": fib_levels,
        }

    # ═══════════════════════════════════════
    # STOP-LOSS MANAGEMENT
    # ═══════════════════════════════════════

    def check_stop_loss(self, position: dict, current_price: float) -> Tuple[bool, str]:
        """
        Check if a position should be closed due to stop-loss or trailing stop.
        Returns (should_close, reason).
        """
        entry_price = position["entry_price"]
        stop_loss = position["stop_loss"]
        trailing_stop = position.get("trailing_stop", 0)
        trailing_max = position.get("trailing_max", entry_price)

        # Fixed stop-loss
        if stop_loss > 0 and current_price <= stop_loss:
            pnl_pct = (current_price - entry_price) / entry_price
            return True, f"stop_loss ({pnl_pct:.1%})"

        # Trailing stop
        pnl_pct = (current_price - entry_price) / entry_price
        if pnl_pct >= config.risk.trailing_stop_activation:
            # Update trailing max and stop
            new_max = max(trailing_max, current_price)
            new_trailing = new_max * (1 - config.risk.trailing_stop_distance)

            if trailing_stop > 0 and current_price <= trailing_stop:
                return True, f"trailing_stop (max: {trailing_max:.2f}, stop: {trailing_stop:.2f})"

            # Return False but caller should update trailing values
            return False, f"trailing_update:{new_max}:{new_trailing}"

        return False, ""


# Global instance
strategy = Strategy()
