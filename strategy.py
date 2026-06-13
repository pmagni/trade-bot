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
from database import db

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
            indicators["ema_200"] = self._calc_ema(close, min(len(close) - 1, 200))

        indicators["above_ema_200"] = close.iloc[-1] > indicators["ema_200"]

        # MACD
        macd_line = close.ewm(span=IC.macd_fast, adjust=False).mean() - close.ewm(span=IC.macd_slow, adjust=False).mean()
        signal_line = macd_line.ewm(span=IC.macd_signal, adjust=False).mean()
        indicators["macd"] = macd_line.iloc[-1]
        indicators["macd_signal"] = signal_line.iloc[-1]
        indicators["macd_histogram"] = macd_line.iloc[-1] - signal_line.iloc[-1]
        indicators["macd_bullish_cross"] = (macd_line.iloc[-1] > signal_line.iloc[-1]) and (macd_line.iloc[-2] <= signal_line.iloc[-2])
        indicators["macd_bearish_cross"] = (macd_line.iloc[-1] < signal_line.iloc[-1]) and (macd_line.iloc[-2] >= signal_line.iloc[-2])

        # RSI momentum (rising from oversold)
        rsi_series = self._calc_rsi_series(close, IC.rsi_period)
        indicators["rsi_prev"] = rsi_series.iloc[-2] if len(rsi_series) >= 2 else indicators["rsi"]
        indicators["rsi_rising_from_oversold"] = (indicators["rsi"] > 30 and indicators["rsi_prev"] <= 30)

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

        # Key manual S/R levels (per-asset)
        indicators["key_support"] = None
        indicators["key_resistance"] = None
        indicators["near_key_support"] = False
        indicators["near_key_resistance"] = False
        indicators["below_key_support"] = False

        return indicators

    # ═══════════════════════════════════════
    # SCORING
    # ═══════════════════════════════════════

    def calc_buy_score(self, indicators: dict, symbol: str = "") -> Tuple[int, dict]:
        """
        Calculate buy score (0-15+).
        Returns (score, details_dict).
        """
        score = 0
        details = {}

        # Overbought guard: don't buy into overbought territory
        # v2.13.1: RSI>70 alone is sufficient — no need to also pierce upper BB
        rsi = indicators["rsi"]
        if rsi > IC.rsi_overbought:
            details["overbought_guard"] = (
                f"BLOCKED: RSI {rsi:.1f} > {IC.rsi_overbought} & above upper BB"
            )
            return 0, details

        # RSI
        if rsi < IC.rsi_extreme_oversold:
            score += 3
            details["rsi"] = f"RSI {rsi:.1f} < {IC.rsi_extreme_oversold} (extreme oversold) +3"
        elif rsi < IC.rsi_oversold:
            score += 2
            details["rsi"] = f"RSI {rsi:.1f} < {IC.rsi_oversold} (oversold) +2"

        # Bollinger Bands
        if indicators["price"] < indicators["bb_lower"]:
            score += 2
            details["bb"] = "Price below lower BB +2"
        if indicators["bb_pct_b"] < IC.bb_low_threshold:
            score += 1
            details["bb_pctb"] = f"%B {indicators['bb_pct_b']:.3f} < {IC.bb_low_threshold} +1"

        # EMA Cross
        if indicators["ema_cross_bullish"]:
            score += 1
            details["ema_cross"] = "Bullish EMA cross +1"

        # MACD bullish crossover
        if indicators.get("macd_bullish_cross"):
            score += 2
            details["macd"] = "MACD bullish crossover +2"

        # RSI rising from oversold (reversal signal)
        if indicators.get("rsi_rising_from_oversold"):
            score += 2
            details["rsi_reversal"] = "RSI rising above 30 (reversal) +2"

        # Above EMA 200 (trend confirmation)
        if indicators["above_ema_200"]:
            score += 1
            details["ema_200"] = "Price above EMA200 (uptrend) +1"

        # Volume spike — removed from buy scoring (43% win rate = negative predictor)
        # Kept as informational only, no score contribution
        if indicators["volume_ratio"] > IC.volume_buy_multiplier:
            details["volume_info"] = f"Volume {indicators['volume_ratio']:.1f}x avg (info only, no score)"

        # Fear & Greed (reduced weight: 57% win rate — near random)
        fg = indicators["fear_greed"]
        if fg is not None and fg < IC.fg_extreme_fear:
            score += 1
            details["fear_greed"] = f"Fear and Greed {fg} (extreme fear) +1"

        # Drop from local high (reduced weight: 46% win rate — negative predictor)
        drop = indicators["drop_from_high"]
        if drop > IC.drop_strong:
            score += 2
            details["drop"] = f"Drop {drop:.1%} from 14d high (strong) +2"
        elif drop > IC.drop_moderate:
            score += 1
            details["drop"] = f"Drop {drop:.1%} from 14d high (moderate) +1"

        # Support/Resistance zone bonus
        zone_score = indicators.get("zone_score", 0)
        if zone_score > 0:
            score += zone_score
            details["zone"] = f"Price in {indicators['zone']} zone +{zone_score}"

        # Key support levels (multi-tier): find best matching support within tolerance
        # v2.13: prefer dynamic levels (auto-updated daily) over static config
        kl = db.get_effective_levels(symbol)
        supports = kl.get("supports", [])
        if supports:
            price = indicators["price"]
            tol = config.key_levels.tolerance_pct
            best_bonus = 0
            best_label = ""
            for s in supports:
                dist = (price - s["price"]) / s["price"]
                if 0 <= dist <= tol and s["score_bonus"] > best_bonus:
                    best_bonus = s["score_bonus"]
                    best_label = f"At {s['label']} ${s['price']:,.2f} ({dist:.2%}) +{s['score_bonus']}"
                elif -tol <= dist < 0:
                    fallback = max(s["score_bonus"] - 1, 1)
                    if fallback > best_bonus:
                        best_bonus = fallback
                        best_label = f"Just below {s['label']} ${s['price']:,.2f} ({dist:.2%}) +{fallback}"
            if best_bonus > 0:
                score += best_bonus
                details["key_support"] = best_label

        return score, details

    def calc_sell_score(self, indicators: dict, positions: list = None, symbol: str = "") -> Tuple[int, dict]:
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
            details["rsi"] = f"RSI {rsi:.1f} > {IC.rsi_extreme_overbought} (extreme overbought) +3"
        elif rsi > IC.rsi_overbought:
            score += 2
            details["rsi"] = f"RSI {rsi:.1f} > {IC.rsi_overbought} (overbought) +2"

        # Bollinger Bands
        if indicators["price"] > indicators["bb_upper"]:
            score += 2
            details["bb"] = "Price above upper BB +2"
        if indicators["bb_pct_b"] > IC.bb_high_threshold:
            score += 1
            details["bb_pctb"] = f"%B {indicators['bb_pct_b']:.3f} > {IC.bb_high_threshold} +1"

        # EMA Cross bearish
        if indicators["ema_cross_bearish"]:
            score += 1
            details["ema_cross"] = "Bearish EMA cross +1"

        # Volume climax
        if indicators["volume_ratio"] > IC.volume_sell_multiplier:
            score += 1
            details["volume"] = f"Volume climax {indicators['volume_ratio']:.1f}x avg +1"

        # Fear & Greed
        fg = indicators["fear_greed"]
        if fg is not None and fg > IC.fg_extreme_greed:
            score += 2
            details["fear_greed"] = f"Fear and Greed {fg} (extreme greed) +2"

        # Rise from local low
        rise = indicators["rise_from_low"]
        if rise > IC.rise_strong:
            score += 3
            details["rise"] = f"Rise {rise:.1%} from 14d low (strong) +3"
        elif rise > IC.rise_moderate:
            score += 2
            details["rise"] = f"Rise {rise:.1%} from 14d low (moderate) +2"

        # Resistance zone
        zone_score = indicators.get("zone_score", 0)
        if zone_score < 0:
            score += abs(zone_score)
            details["zone"] = f"Price in {indicators['zone']} zone +{abs(zone_score)}"

        # Profit target from open positions
        if positions:
            max_profit = max(
                (indicators["price"] - p["entry_price"]) / p["entry_price"]
                for p in positions
            )
            if max_profit > config.scoring.profit_target_pct:
                score += 2
                details["profit"] = f"Position at {max_profit:.1%} profit (> {config.scoring.profit_target_pct:.0%} target) +2"

        # Time-decay: push losing positions toward exit
        if positions:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            for p in positions:
                entry_time = datetime.fromisoformat(p["entry_time"])
                hours_open = (now - entry_time).total_seconds() / 3600
                pnl_pct = (indicators["price"] - p["entry_price"]) / p["entry_price"]

                # Softened time-decay: 1-24h holds are most profitable, don't push exits too early
                if hours_open > 96 and pnl_pct < 0:
                    score += 5  # +5 so time-decay alone can independently trigger sell (threshold=5)
                    details["time_decay"] = f"Position open {hours_open:.0f}h at {pnl_pct:.1%} (>96h loss) +5"
                    break
                elif hours_open > 72 and pnl_pct < -0.015:
                    score += 3
                    details["time_decay"] = f"Position open {hours_open:.0f}h at {pnl_pct:.1%} (> 72h, > -1.5%) +3"
                    break
                elif hours_open > 48 and pnl_pct < 0:
                    score += 2
                    details["time_decay"] = f"Position open {hours_open:.0f}h at {pnl_pct:.1%} (> 48h, in loss) +2"
                    break

        # MACD bearish crossover
        if indicators.get("macd_bearish_cross"):
            score += 1
            details["macd"] = "MACD bearish crossover +1"

        # Below EMA200 = bearish regime → easier to sell
        if not indicators.get("above_ema_200", True):
            score += 1
            details["trend"] = "Below EMA200 (downtrend) +1"

        # Key resistance levels (multi-tier): find best matching resistance
        # v2.13: prefer dynamic levels (auto-updated daily) over static config
        kl = db.get_effective_levels(symbol)
        resistances = kl.get("resistances", [])
        if resistances:
            price = indicators["price"]
            tol = config.key_levels.tolerance_pct
            best_bonus = 0
            best_label = ""
            for r in resistances:
                dist = (price - r["price"]) / r["price"]
                if -tol <= dist <= tol and r["score_bonus"] > best_bonus:
                    best_bonus = r["score_bonus"]
                    best_label = f"At {r['label']} ${r['price']:,.2f} ({dist:+.2%}) +{r['score_bonus']}"
            if best_bonus > 0:
                score += best_bonus
                details["key_resistance"] = best_label

        # Key support break: price broke below a support level (kl already loaded above)
        supports = kl.get("supports", [])
        if supports:
            price = indicators["price"]
            tol = config.key_levels.tolerance_pct
            best_bonus = 0
            best_label = ""
            for s in supports:
                dist = (price - s["price"]) / s["price"]
                if dist < -tol and s["score_bonus"] > best_bonus:
                    best_bonus = s["score_bonus"]
                    best_label = f"Broke below {s['label']} ${s['price']:,.2f} ({dist:.2%}) +{s['score_bonus']}"
            if best_bonus > 0:
                score += best_bonus
                details["key_support_break"] = best_label

        # Anti-panic-sell: suppress sells when RSI is deeply oversold
        # Oversold conditions often lead to short-term rebounds
        rsi = indicators["rsi"]
        if rsi < 30 and score > 0:
            penalty = min(score, 2)  # Reduce score by up to 2 points
            score -= penalty
            details["anti_panic"] = f"RSI {rsi:.1f} oversold, suppressing sell -{penalty}"

        return score, details

    # ═══════════════════════════════════════
    # POSITION SIZING
    # ═══════════════════════════════════════

    def calc_buy_amount(self, buy_score: int, available_usdt: float,
                        total_portfolio: float, open_positions: list = None) -> float:
        """
        Calculate how much USDT to spend based on buy score and DCA position.
        Applies escalating multipliers when adding to an existing position:
          - 1st entry: 1.0× base size
          - 2nd entry: 1.5× base size (requires score ≥6 — enforced in risk_manager)
          - 3rd+ entry: 2.0× base size (requires score ≥7 — enforced in risk_manager)
        Hard cap: no single trade > dca_max_per_trade_pct of available.
        """
        SC = config.scoring
        RC = config.risk

        # Ensure minimum reserve
        max_deployable = available_usdt - (total_portfolio * RC.min_reserve_pct)
        if max_deployable <= 0:
            return 0

        # Base size by opportunity tier
        if buy_score >= SC.buy_maximum:
            base_pct = SC.buy_maximum_pct
        elif buy_score >= SC.buy_strong:
            base_pct = SC.buy_strong_pct
        elif buy_score >= SC.buy_moderate:
            base_pct = SC.buy_moderate_pct
        else:
            base_pct = SC.buy_light_pct  # score 5-6: probe

        amount = max_deployable * base_pct

        # DCA escalation multiplier based on existing open positions in this asset
        pos_count = len(open_positions) if open_positions else 0
        if pos_count >= 2:
            amount *= SC.dca_multiplier_3rd
        elif pos_count == 1:
            amount *= SC.dca_multiplier_2nd

        # Hard cap: never more than 50% of available in one trade
        cap = available_usdt * SC.dca_max_per_trade_pct
        amount = min(amount, cap)

        # If below minimum but funds are available, floor to minimum order
        if amount < RC.min_order_usdt:
            if max_deployable >= RC.min_order_usdt:
                amount = RC.min_order_usdt
            else:
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
    def _calc_rsi_series(close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

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
            logger.warning(f"Could not fetch Fear and Greed Index: {e}")
            return self._fg_cache["value"]

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

        for s in supports:
            if abs(current_price - s) / current_price < tolerance:
                if s <= s2 or s <= fib_levels.get("fib_0.618", 0):
                    zone = "strong_buy"
                    zone_score = 2
                else:
                    zone = "buy"
                    zone_score = 1
                break

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
            "supports": supports[:3],
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
            new_max = max(trailing_max, current_price)
            new_trailing = new_max * (1 - config.risk.trailing_stop_distance)

            if trailing_stop > 0 and current_price <= trailing_stop:
                return True, f"trailing_stop (max: {trailing_max:.2f}, stop: {trailing_stop:.2f})"

            return False, f"trailing_update:{new_max}:{new_trailing}"

        return False, ""


# Global instance
strategy = Strategy()
