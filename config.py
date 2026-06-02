"""
Swing Trading Bot - Configuration
All configurable parameters in one place.
Load sensitive values from .env file.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ExchangeConfig:
    """Bybit API configuration."""
    api_key: str = os.getenv("BYBIT_API_KEY", "")
    api_secret: str = os.getenv("BYBIT_API_SECRET", "")
    testnet: bool = os.getenv("BYBIT_TESTNET", "false").lower() == "true"


@dataclass
class TelegramConfig:
    """Telegram bot configuration."""
    token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")


@dataclass
class TradingPairs:
    """Trading pair configuration."""
    symbols: list = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    base_assets: list = field(default_factory=lambda: ["BTC", "ETH"])
    quote_asset: str = "USDT"


@dataclass
class ScanningConfig:
    """Market scanning parameters."""
    interval_minutes: int = 15          # How often to scan (base rate)
    fast_interval_minutes: int = 5       # Scan every 5 min during high volatility
    ohlcv_timeframe: str = "240"        # 4h candles (Bybit format: minutes)
    ohlcv_limit: int = 100              # Number of candles to fetch
    daily_timeframe: str = "D"          # Daily candles for trend
    daily_limit: int = 90               # 90 days of daily data


@dataclass
class IndicatorConfig:
    """Technical indicator parameters."""
    # RSI
    rsi_period: int = 14
    rsi_oversold: float = 35.0
    rsi_extreme_oversold: float = 25.0
    rsi_overbought: float = 70.0
    rsi_extreme_overbought: float = 80.0

    # Bollinger Bands
    bb_period: int = 20
    bb_std: float = 2.0
    bb_low_threshold: float = 0.05      # %B below this = buy signal
    bb_high_threshold: float = 0.95     # %B above this = sell signal

    # EMA
    ema_fast: int = 9
    ema_slow: int = 21
    ema_trend: int = 200                # Long-term trend

    # MACD
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # Volume
    volume_period: int = 20
    volume_buy_multiplier: float = 2.0   # Vol > 2x avg = buy signal
    volume_sell_multiplier: float = 2.5  # Vol > 2.5x avg = sell climax

    # Fear & Greed
    fg_extreme_fear: int = 25
    fg_extreme_greed: int = 75

    # Price drop/rise from local high/low
    local_period_days: int = 14
    drop_moderate: float = 0.05          # 5% drop
    drop_strong: float = 0.10            # 10% drop
    rise_moderate: float = 0.08          # 8% rise
    rise_strong: float = 0.15            # 15% rise

    # Fibonacci levels
    fib_levels: list = field(default_factory=lambda: [0.236, 0.382, 0.5, 0.618, 0.786])

    # Pivot Points
    pivot_zone_tolerance: float = 0.01   # 1% tolerance around pivot levels


@dataclass
class ScoringConfig:
    """Score thresholds for buy/sell actions."""
    # Buy score thresholds
    buy_no_action: int = 4              # Score 0-4: don't buy
    buy_light: int = 5                  # Score 5: buy 8%
    buy_moderate: int = 6               # Score 6: buy 28%
    buy_strong: int = 7                 # Score 7-9: buy 48%
    buy_maximum: int = 10               # Score 10+: buy 60%

    # Buy capital allocation — opportunity-sized (v2.9)
    buy_light_pct: float = 0.08          # Score 5: probe (was 0.10)
    buy_moderate_pct: float = 0.28       # Score 6: confirmed setup (was 0.30)
    buy_strong_pct: float = 0.48         # Score 7-9: strong signal (was 0.45)
    buy_maximum_pct: float = 0.60        # Score 10+: max conviction (was 0.60, unchanged)

    # DCA escalation (v2.9)
    dca_multiplier_2nd: float = 1.5      # 2nd position: 1.5× base size
    dca_multiplier_3rd: float = 2.0      # 3rd+ position: 2.0× base size
    dca_score_min_2nd: int = 6           # Min score to open 2nd position in same asset
    dca_score_min_3rd: int = 7           # Min score to open 3rd+ position in same asset
    dca_max_per_trade_pct: float = 0.50  # Hard cap: no single trade > 50% of available

    # Symbol-specific minimum buy scores (v2.10)
    # BTC score-5 trades: 55% win rate, +0.3% avg — below acceptable threshold
    # BTC score-6 trades: 100% win rate, +3.5% avg — require confirmed signal
    btc_min_buy_score: int = 6           # BTC needs stronger confirmation than ETH

    # Sell score thresholds (raised: score-4 sells had 43% win rate — too early)
    sell_no_action: int = 4
    sell_partial: int = 5               # Score 5-6: sell 25%
    sell_moderate: int = 6              # Score 6-7: sell 50%
    sell_strong: int = 8               # Score 8-9: sell 75%
    sell_total: int = 10               # Score 10+: sell 100%

    # Sell position allocation (% of position)
    sell_partial_pct: float = 0.25
    sell_moderate_pct: float = 0.50
    sell_strong_pct: float = 0.75
    sell_total_pct: float = 1.00

    # Profit target for sell score bonus
    profit_target_pct: float = 0.08     # 8% profit = +2 sell score

    # Partial sell: sell portion at resistance, let rest ride with trailing
    partial_sell_at_resistance: bool = True
    partial_sell_pct: float = 0.50      # Sell 50% at resistance, keep 50% as runner


@dataclass
class RiskConfig:
    """Risk management parameters."""
    # Stop-loss
    stop_loss_pct: float = 0.05         # -5% from entry
    trailing_stop_activation: float = 0.015  # Activate trailing at +1.5% (was 3% — never triggered)
    trailing_stop_distance: float = 0.02    # 2% below max

    # Position sizing
    max_per_trade_pct: float = 0.40     # Max 40% of available per trade
    min_reserve_pct: float = 0.10       # Always keep 10% in USDT
    min_order_usdt: float = 10.0        # Bybit minimum
    max_open_positions_per_asset: int = 4   # was 10 — cap DCA at 4 levels
    min_entry_spread_pct: float = 0.02      # New entry must be ≥2% below cheapest open entry

    # Portfolio limits
    max_daily_loss_pct: float = 0.05    # -5% daily circuit breaker
    max_drawdown_pct: float = 0.15      # -15% total circuit breaker

    # Leverage (optional, disabled by default)
    leverage_enabled: bool = False
    leverage_max: float = 2.0
    leverage_score_threshold: int = 10  # Only at score 10+
    leverage_max_portfolio_pct: float = 0.15
    leverage_stop_loss_pct: float = 0.04
    leverage_max_duration_days: int = 5

    # Dust management: sell leftover crypto < this USD value
    dust_threshold_usdt: float = 5.0    # Sweep dust worth > $5

    # Cooldowns (in minutes)
    cooldown_after_buy: int = 30        # 30 minutes
    cooldown_after_sell: int = 30       # 30 minutes
    cooldown_after_stop_loss: int = 1440  # 24 hours
    min_hold_minutes: int = 60          # Don't sell within 60 min of buying
    max_trades_per_day_per_asset: int = 2


@dataclass
class KeyLevelsConfig:
    """Manual key support/resistance levels per asset (multi-tier).
    Each asset has lists of supports/resistances with price, label, and score_bonus.
    Optional stop_level overrides percentage-based stop-loss."""
    levels: dict = field(default_factory=lambda: {
        "BTCUSDT": {
            # Updated Jun 2 — BTC at ~$67,548 (sharp drop from $74,600; all May supports broken)
            # stop_level set to $56,000 so 5% pct_stop always wins for entries above $58,947
            "supports": [
                {"price": 67000, "label": "Current floor / range low",      "score_bonus": 1},
                {"price": 64000, "label": "Prior demand zone",              "score_bonus": 2},
                {"price": 60000, "label": "Major structural support",       "score_bonus": 3},
                {"price": 56000, "label": "Deep accumulation zone",         "score_bonus": 3},
            ],
            "resistances": [
                {"price": 69000, "label": "Broken support → resistance",   "score_bonus": 1},
                {"price": 71000, "label": "Prior consolidation zone",      "score_bonus": 2},
                {"price": 74000, "label": "Prior range high",              "score_bonus": 2},
            ],
            "stop_level": 56000,
        },
        "ETHUSDT": {
            # Updated Jun 2 — ETH at ~$1,905 (at $1,900 support; $2,000 and above broken)
            # stop_level set to $1,600 so 5% pct_stop always wins for entries above $1,684
            "supports": [
                {"price": 1900, "label": "Current floor / structural support", "score_bonus": 2},
                {"price": 1800, "label": "Key accumulation zone",              "score_bonus": 2},
                {"price": 1650, "label": "Deep structural support",            "score_bonus": 3},
                {"price": 1450, "label": "Major accumulation zone",            "score_bonus": 3},
            ],
            "resistances": [
                {"price": 2000, "label": "Broken support → resistance",    "score_bonus": 1},
                {"price": 2100, "label": "Prior consolidation zone",       "score_bonus": 2},
                {"price": 2250, "label": "Prior range high",               "score_bonus": 2},
            ],
            "stop_level": 1600,
        },
    })
    tolerance_pct: float = 0.008  # 0.8% proximity threshold (was 0.5%)


@dataclass
class ReportingConfig:
    """Reporting and notification settings."""
    scanning_report_interval_hours: int = 6  # Report market status every 6h
    daily_report_hour_utc: int = 23          # ~20:00 CLT
    weekly_report_day: int = 6               # Sunday (0=Monday)
    weekly_report_hour_utc: int = 23


@dataclass
class DatabaseConfig:
    """Database configuration."""
    db_path: str = "swing_bot.db"


@dataclass
class Config:
    """Master configuration."""
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    pairs: TradingPairs = field(default_factory=TradingPairs)
    scanning: ScanningConfig = field(default_factory=ScanningConfig)
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    key_levels: KeyLevelsConfig = field(default_factory=KeyLevelsConfig)
    reporting: ReportingConfig = field(default_factory=ReportingConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)


# Global config instance
config = Config()
